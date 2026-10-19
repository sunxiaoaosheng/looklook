import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile,mkdir,readFile} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {DurableQueue} from '../durable-queue.js';import {createHandler} from '../dsh-adapter/adapter.js';import {createServer} from '../mcp-server/server.js';import {detectEntry} from '../interaction.js';import {PersistentConversation} from '../conversation-store.js';
const route={channel:'feishu',sender:'owner',chatId:'c',chatType:'p2p'};
const request={input:'https://youtu.be/a',mode:'text',scope:'owner'};
async function fixture(fn){const root=await mkdtemp(join(tmpdir(),'looklook-reliable-'));try{await fn(root);}finally{await rm(root,{recursive:true,force:true});}}
test('失败结果持久化，重启后不重置发送预算，手动重发复用结果',async()=>fixture(async root=>{
 let executions=0,sends=0;
 const options={namespace:'fixture',stateRoot:root,persistent:true,autoResume:false,retryMs:1,execute:async()=>{executions++;return{status:'ready',answer:'正文'};},sendText:async(r,t)=>{if(t.startsWith('任务未能'))return;sends++;throw Error('网络故障');}};
 const a=new DurableQueue(options);const {id}=await a.submit(route,request);await a.drain();a.stop();assert.equal(sends,2);assert.equal(a.states.get(id).status,'delivery_failed');
 const b=new DurableQueue({...options,sendText:async()=>{sends++;return{messageId:'ok'};}});await b.drain();assert.equal(sends,2);assert.equal(b.states.get(id).attempts.text0,2);
 await b.control(route,'look 重发');await b.drain();b.stop();assert.equal(executions,1);assert.equal(sends,3);assert.equal(b.states.get(id).status,'sent');
}));
test('状态无法落盘时不能确认或执行',async()=>fixture(async root=>{
 const f=join(root,'file');await writeFile(f,'x');assert.throws(()=>new DurableQueue({namespace:'x',stateRoot:f,persistent:true,execute:()=>assert.fail()}));
}));
test('两个队列实例恢复同一任务只执行一次',async()=>fixture(async root=>{
 let calls=0;const o={namespace:'same',stateRoot:root,persistent:true,autoResume:false,execute:async()=>{calls++;return{status:'ready',answer:'正文'};},sendText:async()=>({messageId:'m'})};
 const a=new DurableQueue(o);await a.submit(route,request);const b=new DurableQueue(o);await a.drain();await b.drain();a.stop();b.stop();assert.equal(calls,1);
}));
test('视频超限先询问，选择后才生成副本，每段只重试一次',async()=>fixture(async root=>{
 const file=join(root,'v.mp4');await writeFile(file,'video');let strategies=[],sends=0;const texts=[];
 const q=new DurableQueue({namespace:'video',persistent:false,autoResume:false,retryMs:1,execute:async()=>({status:'ready',media:{path:file},answer:'已保存'}),sendText:async(r,t)=>texts.push(t),sendMedia:async()=>{sends++;throw Error('断网');},prepareVideo:async r=>{strategies.push(r.strategy);return r.strategy?{status:'ready',files:[{path:file,original:true,start:0,end:1}]}:{status:'waiting_choice',size:30000000};}});
 const {id}=await q.submit(route,{...request,mode:'video'});await q.drain();assert.equal(sends,0);assert.equal(q.states.get(id).status,'waiting_choice');assert.match(texts[0],/1 分段/);
 await q.control(route,'2');await q.drain();q.stop();assert.deepEqual(strategies,[undefined,'compress']);assert.equal(sends,2);
}));
test('选择过期不上传，取消只终止当前会话任务',async()=>{
 const q=new DurableQueue({namespace:'x',persistent:false,autoResume:false,execute:()=>assert.fail()});const {id}=await q.submit(route,request);const s=q.states.get(id);s.status='waiting_choice';s.choiceAt=Date.now()-901000;
 assert.match(await q.control(route,'1'),/过期/);await q.control(route,'look 取消');assert.equal(s.status,'cancelled');q.stop();
});
test('飞书确认消息失败后仍执行并回传；群聊不启动',async()=>{
 let calls=0,sends=0;const handler=createHandler({allowedSenders:['owner'],execute:async()=>{calls++;return{status:'ready',answer:'正文'};}});const router={client:{sendMessage:async()=>{if(++sends===1)throw Error('确认失败');}},logErr:()=>{}};
 const event={text:'详细评价 https://youtu.be/a',senderOpenId:'owner',chatId:'c',chatType:'p2p',messageId:'m'};
 await handler(router,event);await handler(router,event);assert.equal(calls,1);assert.equal(sends,2);
 await handler(router,{...event,chatType:'group',messageId:'g'});assert.equal(calls,1);handler.queue.stop();
});
test('第三方分享标题不改变调试或模型；明确目的保留模型选择',()=>{
 const a=detectEntry('复制打开抖音 看看【 用 deepseek --debug 】 https://v.douyin.com/a/');assert.equal(a.debug,false);assert.equal(a.model,null);
 assert.equal(detectEntry('look --debug 详细评价 https://youtu.be/a').debug,true);
 assert.equal(detectEntry('look 详细评价 用 deepseek https://youtu.be/a').model,'deepseek');
});
test('MCP 协议取消只结束等待，显式取消才终止后台',async()=>{
 let resolve;let aborted=false;const gate=new Promise(r=>resolve=r);
 const s=createServer({send:()=>{},waitMs:10000,execute:(r,l,{signal})=>{signal.addEventListener('abort',()=>{aborted=true;resolve({status:'failed',message:'取消'});});return gate;}});
 const pending=s.handle({jsonrpc:'2.0',id:7,method:'tools/call',params:{name:'looklook_analyze',arguments:{url:'https://youtu.be/a'}}});await new Promise(r=>setTimeout(r,5));
 await s.handle({jsonrpc:'2.0',method:'notifications/cancelled',params:{requestId:7}});assert.equal(await pending,null);assert.equal(aborted,false);
 const id=[...s.store.jobs.keys()][0];await s.handle({jsonrpc:'2.0',id:8,method:'tools/call',params:{name:'looklook_cancel',arguments:{job_id:id}}});assert.equal(aborted,true);assert.equal(s.store.get(id).status,'cancelled');s.close();
});
test('微信恢复时重新核对账号白名单，授权撤销后拒绝回传',async()=>fixture(async root=>{
 const {authorizeStoredWechat}=await import('../openclaw-plugin/index.js');const previous=process.env.OPENCLAW_STATE_DIR;process.env.OPENCLAW_STATE_DIR=root;
 try{
  await mkdir(join(root,'credentials'));const file=join(root,'credentials','openclaw-weixin-bot-allowFrom.json');await writeFile(file,JSON.stringify({allowFrom:['owner@im.wechat']}));
  const api={pluginConfig:{agents:{main:{}}},config:{}};const state={request:{_agentId:'main'},route:{accountId:'bot',target:'owner@im.wechat'}};
  assert.equal(authorizeStoredWechat(api,state),true);await writeFile(file,JSON.stringify({allowFrom:['someone-else']}));assert.equal(authorizeStoredWechat(api,state),false);
 }finally{if(previous===undefined)delete process.env.OPENCLAW_STATE_DIR;else process.env.OPENCLAW_STATE_DIR=previous;}
}));
test('MCP 关闭后重新启动能找回同一个任务编号',async()=>fixture(async root=>{
 const {JobStore}=await import('../mcp-server/server.js');const previous=process.env.LOOKLOOK_STATE_ROOT;process.env.LOOKLOOK_STATE_ROOT=root;
 try{
  const a=new JobStore({persistent:true,execute:(r,l,{signal})=>new Promise(resolve=>signal.addEventListener('abort',()=>resolve({status:'failed',message:'关闭'})))});
  const job=a.start(request);await new Promise(r=>setTimeout(r,10));a.close();await job.done;
  let calls=0;const b=new JobStore({persistent:true,execute:async()=>{calls++;return {status:'ready',answer:'恢复正文'};}});const restored=b.get(job.id);assert.ok(restored);await restored.done;assert.equal(calls,1);assert.equal(restored.result.answer,'恢复正文');b.close();
 }finally{if(previous===undefined)delete process.env.LOOKLOOK_STATE_ROOT;else process.env.LOOKLOOK_STATE_ROOT=previous;}
}));
