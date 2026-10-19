import test from 'node:test';import assert from 'node:assert/strict';import {EventEmitter} from 'node:events';
import {mkdtemp,rm,readdir} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {numberedParts,deliveryText} from '../delivery.js';import plugin,{createCommand,invoke,requestFor} from '../openclaw-plugin/index.js';
import {createHandler} from '../dsh-adapter/adapter.js';
test('长文分段保留所有字符和表情',()=>{const text='正文😀\n\n'.repeat(3000);assert.equal(numberedParts(text).map(s=>s.replace(/^（\d+\/\d+）\n/,'')).join(''),text);});
test('清单成功失败都明确，debug 目录仅按返回值展示',()=>{assert.match(deliveryText({status:'ready',answer:'正文'}),/待删除中间文件：无/);assert.match(deliveryText({status:'failed',message:'失败',temporary_files:[{path:'/tmp/a.wav',purpose:'音频'}]}),/a.wav/);});
test('仅显式前缀或参数开启 debug，用户文本不经 shell',async()=>{
 const ctx={channelId:'fixture',senderId:'a'};assert.equal(requestFor(ctx,'--debug https://v.douyin.com/a/').debug,true);assert.equal(requestFor(ctx,'分享debug教程 https://v.douyin.com/a/').debug,false);
 let command,options,input;const child=new EventEmitter();child.stdout=new EventEmitter();child.stderr=new EventEmitter();child.stdin={end:s=>{input=JSON.parse(s);child.stdout.emit('data',JSON.stringify({status:'ready',answer:'正文'}));child.emit('close',0);}};
 await invoke({input:'$(touch SHOULD_NOT_EXIST)'},(c,a,o)=>{command=c;options=o;return child;});assert.equal(options.shell,false);assert.equal(input.input,'$(touch SHOULD_NOT_EXIST)');assert(command.includes('python'));
});
test('注册原命令与工具，无新增通道，debug 默认不启用',()=>{let command,tool;plugin.register({registerCommand:c=>command=c,registerTool:f=>tool=f({}),runtime:{}});assert.equal(command.name,'looklook');assert.equal(tool.name,'looklook_analyze');assert.equal(tool.parameters.properties.debug.type,'boolean');});
test('直接入口返回正文和清单',async()=>{const c=createCommand({},async()=>({status:'ready',answer:'完整正文'}));const r=await c.handler({isAuthorizedSender:true,args:'https://v.douyin.com/a/',channelId:'fixture'});assert.equal(r.text,'完整正文\n\n待删除中间文件：无');});
async function fixture(execute,http){
 const root=await mkdtemp(join(tmpdir(),'looklook-feishu-memory-'));const sent=[];
 const router={client:{token:async()=>'fixture-token',sendMessage:async(to,text)=>sent.push({to,text})},log:()=>{},logErr:()=>{}};
 const handler=createHandler({allowedSenders:['owner'],execute,httpRequest:http,stateRoot:root});
 return{root,sent,router,handler,cleanup:()=>rm(root,{recursive:true,force:true})};
}
const event={text:'/looklook 详细评价 https://v.douyin.com/a/',senderOpenId:'owner',chatType:'p2p',chatId:'origin',messageId:'event-1'};
const ack={to:'origin',text:'收到，开始处理：🙊 评价文案信息（🐥 详细）。完成后回复结果。'};
test('飞书短文原会话回复、重复事件不重复执行，无本地文件',async()=>{
 let calls=0;const f=await fixture(async()=>{calls++;return{status:'ready',answer:'完整正文'};});
 try{await f.handler(f.router,event);await f.handler(f.router,event);assert.equal(calls,1);assert.deepEqual(f.sent,[ack,{to:'origin',text:'完整正文\n\n待删除中间文件：无'}]);assert.deepEqual(await readdir(f.root),[]);}finally{await f.cleanup();}
});
test('飞书长报告以内存文档上传，包含全文和待删除清单',async()=>{
 const result={status:'ready',answer:'正文😀'.repeat(2500),temporary_files:[{path:'/临时.wav',purpose:'音频'}]};const requests=[];
 const f=await fixture(async()=>result,async(url,method,headers,body)=>{requests.push({url,body});return{json:{code:0,data:url.endsWith('/files')?{file_key:'fixture-file'}:{message_id:'sent'}}};});
 try{await f.handler(f.router,event);assert.equal(requests.length,2);assert(requests[0].body.toString().includes(deliveryText(result)));assert.equal(JSON.parse(requests[1].body).receive_id,'origin');assert.deepEqual(await readdir(f.root),[]);}finally{await f.cleanup();}
});
test('飞书文件上传失败回退完整分段，不写报告或状态',async()=>{
 const result={status:'ready',answer:'正文😀'.repeat(2500)};const f=await fixture(async()=>result,async()=>{throw Error('no permission');});
 try{await f.handler(f.router,event);assert.deepEqual(f.sent[0],ack);assert.equal(f.sent.slice(1).map(p=>p.text.replace(/^（\d+\/\d+）\n/,'')).join(''),deliveryText(result));assert.deepEqual(await readdir(f.root),[]);}finally{await f.cleanup();}
});
test('飞书直接发视频链接：两轮问答后执行，请求带目的与详略；普通聊天和陌生人照常',async()=>{
 const requests=[];const f=await fixture(async(r)=>{requests.push(r);return{status:'ready',answer:'正文'};});
 const msg=(text,id)=>({text,senderOpenId:'owner',chatType:'p2p',chatId:'origin',messageId:id});
 try{
  assert.equal(await f.handler(f.router,msg('你好','m0')),false);
  assert.equal(await f.handler(f.router,msg('https://www.bilibili.com/video/BV1x','m1')),true);assert.match(f.sent.at(-1).text,/想怎么处理/);
  await f.handler(f.router,msg('4','m2'));await f.handler(f.router,msg('🐤','m3'));
  assert.deepEqual([requests[0].mode,requests[0].detail,requests[0].input],['credibility','brief','https://www.bilibili.com/video/BV1x']);
  assert.equal(await f.handler(f.router,{text:'你好',senderOpenId:'stranger',chatId:'x',messageId:'m4'}),false);
  assert.equal(await f.handler(f.router,{text:'https://youtu.be/a',senderOpenId:'stranger',chatId:'x',messageId:'m5'}),true);assert.equal(requests.length,1);
 }finally{await f.cleanup();}
});
