import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { numberedParts, deliveryText } from '../delivery.js';
import { PersistentConversation } from '../conversation-store.js';
import { WechatQueue } from '../wechat-queue.js';
import { LooklookConversation, jobRequest } from '../interaction.js';
import { invoke } from '../runtime.js';
export { invoke } from '../runtime.js';
// 按 agent 隔离：plugins.entries.looklook-mobile.config.agents.<agentId> = { storageRoot, ytdlpCookies }。
// 未配置 agents 时沿用 runner 默认（兼容旧行为）；已配置时，名单外的 agent 不提供工具、命令直接拒绝。
export function profileFor(pluginConfig, agentId) {
  const agents = pluginConfig?.agents;
  if (!agents || typeof agents !== 'object' || !Object.keys(agents).length) return {};
  const profile = agents[agentId || pluginConfig.defaultAgent || 'main'];
  if (!profile || typeof profile !== 'object') return null;
  const out = {};
  if (typeof profile.storageRoot === 'string' && profile.storageRoot) out.storage_root = profile.storageRoot;
  if (typeof profile.ytdlpCookies === 'string') out.ytdlp_cookies = profile.ytdlpCookies;
  return out;
}
// 会话键形如 agent:<agentId>:<channel>:<account>:direct:<peer>。
export function agentFromSessionKey(sessionKey) {
  return /^agent:([^:]+):/.exec(String(sessionKey || ''))?.[1] || null;
}
function scopeOf(parts) {
  return createHash('sha256').update(JSON.stringify(parts)).digest('hex');
}
export function requestFor(context, input, profile = {}) {
  const scope = scopeOf([
    context.agentId || '', context.channelId || context.channel || context.messageChannel || '',
    context.accountId || context.agentAccountId || '',
    context.senderId || context.from || '', context.sessionKey || context.to || ''
  ]);
  const debug=/^\s*(?:[/@$]?looklook\s+)?--debug(?:\s|$)/i.test(input);
  return { input, scope, debug, ...profile };
}
// 对话结果 → 回复文本；需要执行的任务交给微信队列，完成后由队列回传。
async function conversationReply(outcome, queue, route, base) {
  if (outcome.kind === 'pass') return null;
  if (outcome.kind === 'reply') return outcome.text;
  for (const job of outcome.jobs) {
    const r = await queue.submit(route, jobRequest(job, base), { quiet: true });
    if (r?.text) return r.text;
  }
  return outcome.text;
}
export function createDispatchHook({ queue, conversation, pluginConfig = {} }) {
  return async (event, ctx = {}) => {
    const channel = ctx.channelId || event.channel;
    if (channel !== 'openclaw-weixin' || event.isGroup) return undefined;
    const sender = ctx.senderId || event.senderId;
    if (!sender?.endsWith('@im.wechat')) return undefined;
    const sessionKey = ctx.sessionKey || event.sessionKey;
    const agentId = agentFromSessionKey(sessionKey);
    const profile = profileFor(pluginConfig, agentId);
    if (!profile) return undefined;   // 未开通 looklook 的 agent 照常走模型
    const key = scopeOf([agentId || '', channel, ctx.accountId || '', sender]);
    const control=queue.control?await queue.control({target:sender,accountId:ctx.accountId},event.content??event.body??''):null;
    if(control!==null)return {handled:true,text:control};
    const outcome = conversation.handle(key, event.content ?? event.body ?? '', ctx.messageId || event.messageId || null);
    const base = { scope: scopeOf([agentId || '', channel, ctx.accountId || '', sender, sessionKey || '']), _agentId:agentId, ...profile };
    const text = await conversationReply(outcome, queue, { target: sender, accountId: ctx.accountId }, base);
    if (text === null) return undefined;
    return text ? { handled: true, text } : { handled: true };
  };
}
export function createCommand(api, execute = invoke, queue = null, pluginConfig = {}, conversation = null) {
  return {
    name: 'looklook', description: '获取视频内容，按选择的方式返回视频、文本或分析',
    acceptsArgs: true, requireAuth: true,
    agentPromptGuidance: ['微信里的视频链接由 looklook 入口自动接管并询问处理方式；其他渠道用户发送 looklook 或 @looklook 加分享网址时，调用 looklook_analyze，将 answer 完整发送，并根据 temporary_files 附简短待删除清单；仅用户明确要求时开启 debug。不保存报告文件。'],
    handler: async (context) => {
      if (!context.isAuthorizedSender) return { text: '此入口仅供已授权用户使用。' };
      if (!context.args?.trim()) return { text: '发送视频分享链接即可；也可以在链接前加 look。' };
      const profile = profileFor(pluginConfig, context.agentId);
      if (!profile) return { text: '当前助手未开通 looklook。' };
      const channel = context.channelId || context.channel;
      if (queue && conversation && channel === 'openclaw-weixin') {
        const target = context.senderId;
        if (!target?.endsWith('@im.wechat') || context.to !== target) return { text: '回复会话信息不一致，任务未提交。' };
        const key = scopeOf([context.agentId || '', channel, context.accountId || '', target]);
        const control=await queue.control({target,accountId:context.accountId},'look '+context.args);if(control!==null)return {text:control};
        const base = { scope: requestFor(context, '').scope, _agentId:context.agentId, ...profile };
        const text = await conversationReply(conversation.handle(key, 'looklook ' + context.args), queue, { target, accountId: context.accountId }, base);
        return { text: text ?? '请发送一个视频分享链接。' };
      }
      const request = requestFor(context, context.args, profile);
      if (queue && channel === 'openclaw-weixin') return queue.enqueue(context, request);
      const result = await execute(request);
      const text = deliveryText(result);
      if (channel === 'openclaw-weixin' && text.length > 3500) {
        // 原生命令的最终回复不走模型流式分段，使用现有通道适配器完整发送。
        const target = context.senderId;
        if (!target?.endsWith('@im.wechat') || context.to !== target) return { text: '回复会话信息不一致，分析尚未回传；重启前可重新提交。' };
        const adapter = await api.runtime.channel.outbound.loadAdapter(channel);
        if (!adapter?.sendText) return { text: '微信回传接口暂不可用，分析尚未回传；重启前可重新提交。' };
        for (const part of numberedParts(text)) await adapter.sendText({cfg:context.config,to:target,text:part,accountId:context.accountId});
        return { suppressReply: true };
      }
      return { text };
    },
  };
}
export function authorizeStoredWechat(api,state){
  const id=state.route.accountId;if(!id||!/^[A-Za-z0-9_.@-]+$/.test(id))return false;
  if(profileFor(api.pluginConfig||{},state.request._agentId)===null)return false;
  if(api.config?.channels?.['openclaw-weixin']?.accounts?.[id]?.enabled===false)return false;
  const root=process.env.OPENCLAW_STATE_DIR||process.env.CLAWDBOT_STATE_DIR||join(homedir(),'.openclaw');
  const safe=id.trim().toLowerCase().replace(/[\\/:*?"<>|]/g,'_').replace(/\.\./g,'_');
  const credentials=process.env.OPENCLAW_OAUTH_DIR||join(root,'credentials');
  try{
    const allow=JSON.parse(readFileSync(join(credentials,'openclaw-weixin-'+safe+'-allowFrom.json'),'utf8')).allowFrom;
    if(Array.isArray(allow)&&allow.length)return allow.includes(state.route.target);
  }catch{}
  try{return JSON.parse(readFileSync(join(root,'openclaw-weixin','accounts',id+'.json'),'utf8')).userId===state.route.target;}catch{return false;}
}
function queueStorePath(api) {
  try {
    const dir = api.runtime?.state?.resolveStateDir?.();
    return dir ? join(dir, 'plugin-state', 'looklook-mobile', 'wechat-queue.json') : null;
  } catch { return null; }
}
export default {
  id: 'looklook-mobile', name: 'looklook 手机入口',
  register(api) {
    const mode = api.registrationMode ?? 'full';
    const pluginConfig = api.pluginConfig || {};
    if (['cli-metadata', 'setup-only', 'setup-runtime'].includes(mode)) return;
    const queue = mode === 'full' ? new WechatQueue({api, execute:invoke, autoResume:false, persistent:true, storePath:queueStorePath(api), authorize:s=>authorizeStoredWechat(api,s)}) : null;
    const conversation = new PersistentConversation('wechat-conversation');
    if (queue && api.registerService) api.registerService({
      id: 'looklook-wechat-worker',
      start: () => { queue.schedule(0); },
      stop: () => { queue.stop(); },
    });
    // 各运行模式都登记钩子（便于 inspect 核验）；只有完整运行时有队列，才真正接管消息。
    if (api.on) {
      const hook = queue ? createDispatchHook({ queue, conversation, pluginConfig }) : async () => undefined;
      api.on('before_dispatch', hook);
    }
    api.registerCommand(createCommand(api, invoke, queue, pluginConfig, conversation));
    api.registerTool((context) => {
      const profile = profileFor(pluginConfig, context.agentId);
      if (!profile) return null;   // 未开通的 agent 看不到这个工具
      return {
      name: 'looklook_analyze', label: 'looklook 视频分析',
      description: '用于用户明确的 looklook 视频请求。返回完整分析及必要的待删除清单；默认只保存原名视频和同名文本。debug 仅在用户明确要求调试时开启。',
      parameters: { type: 'object', additionalProperties: false, required: ['input'],
        properties: { input: { type: 'string', description: '用户的分享网址与分析目的原文' }, debug:{type:'boolean',description:'仅用户明确要求调试时为 true'} } },
      execute: async (_id, parameters) => {
        const request=requestFor(context, parameters.input, profile);
        if(parameters.debug===true)request.debug=true;
        const result = await invoke(request);
        return { content: [{ type: 'text', text: JSON.stringify(result) }] };
      },
      };
    });
  },
};
