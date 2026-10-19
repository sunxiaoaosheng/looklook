import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm,readdir,writeFile,readFile} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {WechatQueue} from '../wechat-queue.js';import {createCommand,requestFor} from '../openclaw-plugin/index.js';
import {deliveryText} from '../delivery.js';
const context={isAuthorizedSender:true,senderId:'owner@im.wechat',to:'owner@im.wechat',accountId:'bot',channelId:'openclaw-weixin',args:'分析 https://v.douyin.com/first/'};
async function fixture(execute,send){
 const root=await mkdtemp(join(tmpdir(),'looklook-memory-'));const sent=[];
 const api={config:{secret:'MUST_NOT_PERSIST'},logger:{error:()=>{}},runtime:{channel:{outbound:{loadAdapter:async()=>({sendText:async(p)=>{if(send)await send(p);sent.push(p);}})}}}};
 const queue=new WechatQueue({api,execute,root,retryMs:1,autoResume:false});
 return{root,sent,api,queue,cleanup:async()=>{queue.stop();await rm(root,{recursive:true,force:true});}};
}
test('即时确认、串行后台排队，目录没有状态文件',async()=>{
 let release,started;const begun=new Promise(r=>started=r);const gate=new Promise(r=>release=r);let calls=0;
 const f=await fixture(async()=>{calls++;started();await gate;return{status:'ready',answer:'正文'};});
 try{
  const command=createCommand(f.api,()=>{throw Error('不应同步分析');},f.queue);assert.match((await command.handler(context)).text,/已收到/);
  const working=f.queue.drain();await begun;assert.match((await command.handler({...context,args:'分析 https://v.douyin.com/second/'})).text,/已收到/);
  release();await working;assert.equal(calls,2);assert.equal(f.sent.length,2);assert.deepEqual(await readdir(f.root),[]);assert.match(f.sent[0].text,/待删除中间文件：无/);
 }finally{await f.cleanup();}
});
test('同进程重复请求只执行一次，不保存账号或请求文件',async()=>{
 let calls=0;const f=await fixture(async()=>{calls++;return{status:'ready',answer:'正文'};});
 try{const request=requestFor(context,context.args);await Promise.all([f.queue.enqueue(context,request),f.queue.enqueue(context,request)]);await f.queue.drain();assert.equal(calls,1);assert.equal(f.sent.length,1);assert.deepEqual(await readdir(f.root),[]);}finally{await f.cleanup();}
});
test('重启不恢复内存任务，也不读取历史投递文件',async()=>{
 const f=await fixture(async()=>({status:'ready',answer:'旧任务'}));let calls=0,next;
 try{
  await writeFile(join(f.root,'legacy.json'),'{"status":"queued"}');await f.queue.enqueue(context,requestFor(context,context.args));f.queue.stop();
  next=new WechatQueue({api:f.api,execute:async()=>{calls++;return{status:'ready',answer:'错误恢复'};},root:f.root,autoResume:false});
  await next.resume();assert.equal(calls,0);assert.equal(f.sent.length,0);assert.equal(await readFile(join(f.root,'legacy.json'),'utf8'),'{"status":"queued"}');
 }finally{next?.stop();await f.cleanup();}
});
test('失败后内存续传未确认段落，正文和清单无丢失',async()=>{
 let failed=false,calls=0;const result={status:'ready',answer:'正文😀'.repeat(2500),temporary_files:[{path:'/临时.wav',purpose:'必要音频'}]};
 const f=await fixture(async()=>{calls++;return result;},async p=>{if(p.text.startsWith('（2/')&&!failed){failed=true;throw Error('network');}});
 try{await f.queue.enqueue(context,requestFor(context,context.args));await f.queue.drain();await new Promise(r=>setTimeout(r,10));await f.queue.resume();assert.equal(calls,1);assert.equal(f.sent.map(p=>p.text.replace(/^（\d+\/\d+）\n/,'')).join(''),'对应链接：https://v.douyin.com/first/\n\n'+deliveryText(result));assert.deepEqual(await readdir(f.root),[]);}finally{await f.cleanup();}
});
test('未授权与无关网址不启动工具',async()=>{
 const f=await fixture(()=>{throw Error('不能调用');});
 try{assert.match((await f.queue.enqueue({...context,isAuthorizedSender:false},requestFor(context,context.args))).text,/未提交/);assert.match((await f.queue.enqueue(context,requestFor(context,'没有网址'))).text,/视频分享链接/);assert.equal(f.queue.pending.size,0);}finally{await f.cleanup();}
});
test('SQLite 保存请求、结果与进度，重启后续做且不保存宿主凭据',async()=>{
 const f=await fixture(async()=>({status:'ready',answer:'正文'}));const store=join(f.root,'state','queue.json');let next;
 try{
  const a=new WechatQueue({api:f.api,execute:async()=>{throw Error('不应在此执行');},storePath:store,stateRoot:join(f.root,'state'),autoResume:false});
  await a.submit({target:'owner@im.wechat',accountId:'bot'},{input:'https://youtu.be/a',mode:'text',detail:'normal',scope:'s'});a.stop();
  const saved=await readFile(join(f.root,'state','state.sqlite3'));assert(!saved.includes(Buffer.from('MUST_NOT_PERSIST')));
  let calls=0;next=new WechatQueue({api:f.api,execute:async()=>{calls++;return{status:'ready',answer:'续做结果'};},storePath:store,stateRoot:join(f.root,'state'),autoResume:false});
  await next.resume();assert.equal(calls,1);assert.match(f.sent[0].text,/续做结果/);assert.equal([...next.states.values()][0].status,'sent');
 }finally{next?.stop();await f.cleanup();}
});
