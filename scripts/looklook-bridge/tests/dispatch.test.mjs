import test from 'node:test';import assert from 'node:assert/strict';
import {createDispatchHook,agentFromSessionKey} from '../openclaw-plugin/index.js';
import {LooklookConversation,MODE_PROMPT,DETAIL_PROMPT} from '../interaction.js';
const cfg={agents:{main:{storageRoot:'/m',ytdlpCookies:'chrome'},family:{storageRoot:'/z',ytdlpCookies:''}}};
function fixture(){const submitted=[];const queue={submit:async(route,request)=>{submitted.push({route,request});return{text:''};}};
 return{submitted,hook:createDispatchHook({queue,conversation:new LooklookConversation(),pluginConfig:cfg})};}
const ctx=(agent,msg)=>({channelId:'openclaw-weixin',accountId:'bot',senderId:'u@im.wechat',sessionKey:`agent:${agent}:openclaw-weixin:bot:direct:u@im.wechat`,messageId:msg});
test('会话键解析 agent',()=>{assert.equal(agentFromSessionKey('agent:family:openclaw-weixin:x'),'family');assert.equal(agentFromSessionKey('x'),null);});
test('微信直接发视频链接：模型之前接管，两轮后按 agent 隔离提交',async()=>{
 const f=fixture();
 assert.deepEqual(await f.hook({content:'https://v.douyin.com/a/'},ctx('family','1')),{handled:true,text:MODE_PROMPT});
 assert.deepEqual(await f.hook({content:'🙊'},ctx('family','2')),{handled:true,text:DETAIL_PROMPT});
 const r=await f.hook({content:'🐤'},ctx('family','3'));assert.match(r.text,/开始处理/);
 assert.equal(f.submitted.length,1);const s=f.submitted[0];
 assert.deepEqual(s.route,{target:'u@im.wechat',accountId:'bot'});assert.equal(s.request.storage_root,'/z');assert.equal(s.request.ytdlp_cookies,'');
 assert.deepEqual([s.request.mode,s.request.detail,s.request.input],['evaluate','brief','https://v.douyin.com/a/']);
});
test('普通聊天、群聊、其他通道、未开通的 agent 都不拦截',async()=>{
 const f=fixture();
 assert.equal(await f.hook({content:'你好'},ctx('main','1')),undefined);
 assert.equal(await f.hook({content:'https://youtu.be/a',isGroup:true},ctx('main','2')),undefined);
 assert.equal(await f.hook({content:'https://youtu.be/a'},{...ctx('main','3'),channelId:'feishu'}),undefined);
 assert.equal(await f.hook({content:'https://youtu.be/a'},ctx('stranger','4')),undefined);
 assert.equal(f.submitted.length,0);
});
test('重复投递同一条消息只回应一次',async()=>{
 const f=fixture();await f.hook({content:'https://youtu.be/a'},ctx('main','dup'));
 assert.deepEqual(await f.hook({content:'https://youtu.be/a'},ctx('main','dup')),{handled:true});
});
