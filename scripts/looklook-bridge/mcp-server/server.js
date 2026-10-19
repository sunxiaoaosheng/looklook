// looklook 的本机 stdio MCP 服务：让 Claude Desktop 的 Cowork 模式调用 Mac 上的完整流程。
// 协议为逐行 JSON-RPC 2.0（手写，无第三方依赖）。stdout 只写协议消息，诊断信息写 stderr。
// Cowork 对本机 MCP 工具调用默认 180 秒超时，而下载转录可能十几分钟，所以拆成两个工具：
// looklook_analyze 启动任务并最多等 waitMs，未完成就返回 job_id；looklook_status 继续等。
// 任务只在本进程内存里，不写文件；服务重启后未完成的任务需要重新提交。
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { realpathSync } from 'node:fs';
import { invoke, state } from '../runtime.js';
import { deliveryText } from '../delivery.js';

export const SCOPE = 'cowork-owner';
export const MODES = ['video', 'text', 'evaluate', 'credibility', 'project', 'other'];
export const DETAILS = ['brief', 'normal', 'detailed'];
export const MODELS = ['claude', 'codex', 'deepseek', 'ollama'];
const PROTOCOLS = ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'];
const SERVER_INFO = { name: 'looklook', version: '0.2.0' };
const DEFAULT_WAIT_MS = 120_000;      // 低于 Cowork 的 180 秒，留出余量
const PROGRESS_MS = 15_000;
const KEEP_MS = 24 * 60 * 60 * 1000;   // 完成后保留结果，供 looklook_status 取回

const PENDING_HINT = '不要重复调用 looklook_analyze，那样不会更快。';

export const TOOLS = [
  {
    name: 'looklook_analyze',
    title: 'looklook 视频分析',
    description: '在用户的 Mac 上用 looklook 下载视频、转录并分析（抖音、B站、YouTube 及其他视频网页）。'
      + '完成时返回完整分析正文和待删除中间文件清单，请把正文完整转述给用户。'
      + '任务可能需要十几分钟：若在等待时限内未完成，会返回 job_id，之后调用 looklook_status 取结果。',
    inputSchema: {
      type: 'object', additionalProperties: false, required: ['url'],
      properties: {
        url: { type: 'string', description: '视频分享链接，可带平台分享文案原文' },
        mode: { type: 'string', enum: MODES, default: 'evaluate',
          description: 'video 仅获取视频；text 仅获取文本；evaluate 评价文案信息；credibility 判断信息可信度；project 获取相关项目信息；other 按 purpose 处理' },
        detail: { type: 'string', enum: DETAILS, default: 'normal', description: '回复详略：brief 极简、normal 日常交流、detailed 详细' },
        purpose: { type: 'string', description: '用户的分析目的原文（mode 为 other 时必填）' },
        model: { type: 'string', enum: MODELS, description: '仅在用户明确指定模型时填写；不填则按 Claude→Codex→DeepSeek→Ollama 降级' },
      },
    },
  },
  {
    name: 'looklook_status',
    title: 'looklook 任务结果',
    description: '查询 looklook_analyze 返回的 job_id；最多等待一段时间，完成则返回完整分析正文和待删除清单，否则提示稍后再查。',
    inputSchema: {
      type: 'object', additionalProperties: false, required: ['job_id'],
      properties: { job_id: { type: 'string', description: 'looklook_analyze 返回的 job_id' } },
    },
  },
  {name:'looklook_cancel',title:'取消 looklook 后台任务',description:'显式取消后台任务，保留原素材。',inputSchema:{type:'object',additionalProperties:false,required:['job_id'],properties:{job_id:{type:'string'}}}},

];

function textResult(text, isError = false) {
  return { content: [{ type: 'text', text }], isError };
}

export function buildRequest(args) {
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('参数必须是对象。');
  if(Object.keys(args).some(k=>!['url','mode','detail','purpose','model'].includes(k)))throw new Error('存在未支持参数。');
  const { url, mode = 'evaluate', detail = 'normal', purpose, model } = args;
  if (typeof url !== 'string' || !url.trim()) throw new Error('url 必填：请提供视频分享链接。');
  if (url.length > 12000) throw new Error('url 过长。');
  if (!MODES.includes(mode)) throw new Error('mode 只能是 ' + MODES.join('/') + '。');
  if (!DETAILS.includes(detail)) throw new Error('detail 只能是 ' + DETAILS.join('/') + '。');
  if (purpose !== undefined && typeof purpose !== 'string') throw new Error('purpose 必须是文本。');
  if (model !== undefined && !MODELS.includes(model)) throw new Error('model 只能是 ' + MODELS.join('/') + '。');
  if (mode === 'other' && !purpose?.trim()) throw new Error('mode 为 other 时需要在 purpose 里说明想怎么处理。');
  // 不传 storage_root：沿用 runner 默认的 ~/Documents/Codex/looklookFiles。
  const request = { input: url.trim(), mode, detail, scope: SCOPE };
  if (purpose?.trim()) request.purpose = purpose.trim();
  if (model) request.model = model;
  return request;
}

export class JobStore {
  constructor({execute=invoke,keepMs=KEEP_MS,persistent=execute===invoke}={}) {
    Object.assign(this,{execute,keepMs,persistent});this.jobs=new Map();this.owner=randomUUID();this.closed=false;
    if(persistent)for(const [id,s] of Object.entries(state('all','mcp'))){
      const job={...s,id};this.jobs.set(id,job);
      if(!job.result)this.launch(job);
    }
  }
  save(job){if(this.persistent)state('put','mcp',job.id,{id:job.id,key:job.key,request:job.request,startedAt:job.startedAt,result:job.result,status:job.status});}
  start(request){
    const key=JSON.stringify(request);for(const j of this.jobs.values())if(j.key===key&&!j.result)return j;
    const job={id:randomUUID(),key,request,startedAt:Date.now(),result:null,status:'queued'};
    this.save(job);this.jobs.set(job.id,job);this.launch(job);return job;
  }
  launch(job){
    const controller=new AbortController();job.controller=controller;
    job.done=(async()=>{
      let heartbeat,deadline,leased=false;const leaseOwner=this.owner+':'+job.id;
      try{
        if(this.persistent){
          while(!this.closed&&!controller.signal.aborted){if(state('lease','', 'global-heavy',null,{owner:leaseOwner})){leased=true;break;}await new Promise(r=>setTimeout(r,1000));}
          if(this.closed||controller.signal.aborted)return;
          const fresh=state('get','mcp',job.id);if(fresh?.result){Object.assign(job,fresh);return job.result;}
          heartbeat=setInterval(()=>{try{state('lease','', 'global-heavy',null,{owner:leaseOwner});}catch{controller.abort();}},20000);
        }
        job.status='processing';this.save(job);deadline=setTimeout(()=>controller.abort(),3600000);
        const result=await this.execute(job.request,undefined,{signal:controller.signal,leaseHeld:true});
        if(this.closed||job.status==='cancelled')return;
        job.result=result&&typeof result==='object'?result:{status:'failed',message:'执行器没有返回有效结果。'};
        job.status=job.result.status==='ready'?'completed':'failed';this.save(job);
        setTimeout(()=>this.jobs.delete(job.id),this.keepMs).unref?.();
      }catch(e){if(!this.closed&&job.status!=='cancelled'){job.result={status:'failed',message:'本机执行器异常退出。'};job.status='failed';this.save(job);}}
      finally{clearInterval(heartbeat);clearTimeout(deadline);if(leased)state('release','', 'global-heavy',null,{owner:leaseOwner});}
      return job.result;
    })();
  }
  get(id){return this.jobs.get(id)||null;}
  cancel(id){const j=this.get(id);if(!j)return null;if(!j.result){j.status='cancelled';j.result={status:'failed',code:'cancelled',message:'任务已取消，原素材保留。'};this.save(j);j.controller?.abort();}return j;}
  close(){this.closed=true;for(const j of this.jobs.values())if(!j.result){j.status='queued';this.save(j);j.controller?.abort();}}
}

function wait(job, ms, signal) {
  if (job.result) return Promise.resolve(true);
  let timer,abort;
  return Promise.race([
    new Promise(r=>{abort=()=>r(false);signal?.addEventListener("abort",abort,{once:true});if(signal?.aborted)abort();}),
    job.done.then(() => true),
    new Promise((r) => { timer = setTimeout(() => r(false), ms); }),
  ]).finally(() => {clearTimeout(timer);signal?.removeEventListener("abort",abort);});
}

function elapsed(job) {
  const s = Math.round((Date.now() - job.startedAt) / 1000);
  return s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
}

function jobOutcome(job) {
  if (!job.result) {
    return textResult(`任务仍在 Mac 上运行（已 ${elapsed(job)}）。\njob_id: ${job.id}\n`
      + `状态：${job.status||'processing'}\n请稍后调用 looklook_status 并传入这个 job_id 获取结果；${PENDING_HINT}`);
  }
  return {...textResult(deliveryText(job.result), job.result.status !== 'ready'),structuredContent:{job_id:job.id,status:job.status||job.result.status}};
}

export function createServer({ execute = invoke, send, waitMs = DEFAULT_WAIT_MS, progressMs = PROGRESS_MS, log = () => {} } = {}) {
  const store = new JobStore({ execute });
  const waits=new Map();

  async function withProgress(job, token, fn) {
    if (token === undefined || token === null) return fn();
    let n = 0;
    const timer = setInterval(() => {
      send({ jsonrpc: '2.0', method: 'notifications/progress',
        params: { progressToken: token, progress: ++n, message: `looklook 仍在 Mac 上处理（已 ${elapsed(job)}）` } });
    }, progressMs);
    try { return await fn(); } finally { clearInterval(timer); }
  }

  async function callTool(params, signal) {
    const name = params?.name, args = params?.arguments ?? {};
    const token = params?._meta?.progressToken;
    if(['looklook_status','looklook_cancel'].includes(name)&&(!args||typeof args!=='object'||Array.isArray(args)||typeof args.job_id!=='string'||args.job_id.length>128||Object.keys(args).some(k=>k!=='job_id')))return textResult('需要有效的 job_id，且不能附加其他参数。',true);
    if (name === 'looklook_analyze') {
      let request;
      try { request = buildRequest(args); } catch (e) { return textResult(e.message, true); }
      const job = store.start(request);
      log(`job ${job.id} started`);
      await withProgress(job, token, () => wait(job, waitMs, signal));
      return jobOutcome(job);
    }
    if(name==='looklook_cancel'){const job=store.cancel(args?.job_id);return job?jobOutcome(job):textResult('没有找到这个 job_id。',true);}
    if (name === 'looklook_status') {
      const id = args?.job_id;
      const job = typeof id === 'string' ? store.get(id) : null;
      if (!job) return textResult('没有找到这个 job_id。looklook MCP 服务可能已重启，或结果已过期；请重新调用 looklook_analyze。', true);
      await withProgress(job, token, () => wait(job, waitMs, signal));
      return jobOutcome(job);
    }
    return null;
  }

  // 返回响应对象；通知返回 null。
  async function handle(message) {
    if (!message || typeof message !== 'object' || Array.isArray(message) || message.jsonrpc !== '2.0' || typeof message.method !== 'string') {
      return { jsonrpc: '2.0', id: message?.id ?? null, error: { code: -32600, message: 'Invalid Request' } };
    }
    const { id, method, params } = message;
    const isNotification = !('id' in message);
    const ok = (result) => ({ jsonrpc: '2.0', id, result });
    const fail = (code, msg) => ({ jsonrpc: '2.0', id, error: { code, message: msg } });
    if(isNotification&&method==='notifications/cancelled'){waits.get(params?.requestId)?.abort();return null;}
    if (isNotification) return null;   // initialized、cancelled 等无需回应
    switch (method) {
      case 'initialize': {
        const asked = params?.protocolVersion;
        return ok({
          protocolVersion: PROTOCOLS.includes(asked) ? asked : PROTOCOLS[0],
          capabilities: { tools: { listChanged: false } },
          serverInfo: SERVER_INFO,
          instructions: '用户要求用 looklook 处理视频链接时调用 looklook_analyze；返回 job_id 时用 looklook_status 取结果。把分析正文完整交给用户。',
        });
      }
      case 'ping': return ok({});
      case 'tools/list': return ok({ tools: TOOLS });
      case 'tools/call': {
        const c=new AbortController();waits.set(id,c);
        try{const result=await callTool(params,c.signal);if(c.signal.aborted)return null;return result?ok(result):fail(-32602,`Unknown tool: ${params?.name}`);}
        finally{waits.delete(id);}
      }
      default: return fail(-32601, `Method not found: ${method}`);
    }
  }

  return { handle, store, close:()=>{for(const c of waits.values())c.abort();store.close();} };
}

export function serve({ input = process.stdin, output = process.stdout, execute = invoke, waitMs } = {}) {
  const send = (msg) => output.write(JSON.stringify(msg) + '\n');
  const log = (s) => process.stderr.write(`[looklook-mcp] ${s}\n`);
  const server = createServer({ execute, send, waitMs, log });
  const lines = createInterface({ input, crlfDelay: Infinity });
  const pending = new Set();
  lines.on('line', (line) => {
    if (!line.trim()) return;
    let message;
    try { message = JSON.parse(line); }
    catch { send({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } }); return; }
    // 请求并发处理：等待长任务时仍能响应 ping 和 tools/list。
    const p = server.handle(message)
      .then((response) => { if (response) send(response); })
      .catch((e) => { log(String(e?.stack || e)); if (message?.id !== undefined) send({ jsonrpc: '2.0', id: message.id, error: { code: -32603, message: 'Internal error' } }); })
      .finally(() => pending.delete(p));
    pending.add(p);
  });
  // stdin 关闭（客户端断开）后等进行中的请求答完再退出；已启动的 runner 会自行跑完。
  lines.on('close', () => { server.close(); });
  return server;
}

function isMain() {
  try { return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); } catch { return false; }
}
if (isMain()) {
  const waitMs = Number(process.env.LOOKLOOK_MCP_WAIT_MS) || undefined;
  const server=serve({ waitMs });
  process.on('SIGTERM',()=>{server.close();setTimeout(()=>process.exit(0),500).unref();});
}
