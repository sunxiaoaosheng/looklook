import test from 'node:test';import assert from 'node:assert/strict';import {PassThrough} from 'node:stream';
import {createServer,buildRequest,serve,TOOLS,SCOPE} from '../mcp-server/server.js';
import {deliveryText} from '../delivery.js';

const call=(id,name,args,meta)=>({jsonrpc:'2.0',id,method:'tools/call',params:{name,arguments:args,...(meta?{_meta:meta}:{})}});
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};}

test('initialize 协商版本并声明 tools 能力，通知不回应',async()=>{
 const s=createServer({execute:async()=>({}),send:()=>{}});
 const r=await s.handle({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-06-18',capabilities:{},clientInfo:{name:'t',version:'1'}}});
 assert.equal(r.id,1);assert.equal(r.result.protocolVersion,'2025-06-18');assert.ok(r.result.capabilities.tools);assert.equal(r.result.serverInfo.name,'looklook');
 const u=await s.handle({jsonrpc:'2.0',id:2,method:'initialize',params:{protocolVersion:'1999-01-01'}});assert.equal(u.result.protocolVersion,'2025-11-25');
 assert.equal(await s.handle({jsonrpc:'2.0',method:'notifications/initialized'}),null);
 assert.deepEqual(await s.handle({jsonrpc:'2.0',id:3,method:'ping'}),{jsonrpc:'2.0',id:3,result:{}});
 assert.equal((await s.handle({jsonrpc:'2.0',id:4,method:'resources/list'})).error.code,-32601);
 assert.equal((await s.handle({id:5,method:'ping'})).error.code,-32600);
});

test('tools/list 给出 analyze 与 status，参数枚举与默认值正确',async()=>{
 const s=createServer({execute:async()=>({}),send:()=>{}});
 const {result}=await s.handle({jsonrpc:'2.0',id:1,method:'tools/list'});
 assert.deepEqual(result.tools.map(t=>t.name),['looklook_analyze','looklook_status','looklook_cancel']);
 const p=TOOLS[0].inputSchema.properties;
 assert.deepEqual(TOOLS[0].inputSchema.required,['url']);
 assert.deepEqual(p.mode.enum,['video','text','evaluate','credibility','project','other']);assert.equal(p.mode.default,'evaluate');
 assert.deepEqual(p.detail.enum,['brief','normal','detailed']);assert.equal(p.detail.default,'normal');
 assert.deepEqual(p.model.enum,['claude','codex','deepseek','ollama']);
});

test('请求固定 cowork-owner 作用域，不传 storage_root，可选项按需带上',()=>{
 assert.deepEqual(buildRequest({url:' https://v.douyin.com/a/ '}),{input:'https://v.douyin.com/a/',mode:'evaluate',detail:'normal',scope:SCOPE});
 const r=buildRequest({url:'https://b23.tv/x',mode:'credibility',detail:'detailed',purpose:'是真的吗',model:'deepseek'});
 assert.deepEqual(r,{input:'https://b23.tv/x',mode:'credibility',detail:'detailed',scope:'cowork-owner',purpose:'是真的吗',model:'deepseek'});
 assert.equal('storage_root' in r,false);
 for(const bad of [{},{url:''},{url:'https://a',mode:'x'},{url:'https://a',detail:'x'},{url:'https://a',model:'gpt'},{url:'https://a',mode:'other'},{url:'https://a',purpose:3}])assert.throws(()=>buildRequest(bad));
});

test('快速完成时直接返回正文和待删除清单',async()=>{
 let got;const result={status:'ready',answer:'完整分析',temporary_files:[{path:'/tmp/a.wav',purpose:'音频'}]};
 const s=createServer({execute:async(r)=>{got=r;return result;},send:()=>{}});
 const r=await s.handle(call(7,'looklook_analyze',{url:'https://v.douyin.com/a/',detail:'brief'}));
 assert.equal(got.scope,'cowork-owner');assert.equal(got.detail,'brief');
 assert.equal(r.result.isError,false);assert.equal(r.result.content[0].text,deliveryText(result));assert.match(r.result.content[0].text,/a\.wav（音频）/);
});

test('失败结果标记 isError 并附消息；参数错误不启动执行器',async()=>{
 let calls=0;const s=createServer({execute:async()=>{calls++;return{status:'failed',message:'这条消息没有可处理的单个视频网址'};},send:()=>{}});
 const r=await s.handle(call(1,'looklook_analyze',{url:'not a url'}));
 assert.equal(r.result.isError,true);assert.match(r.result.content[0].text,/没有可处理/);assert.match(r.result.content[0].text,/待删除中间文件：无/);
 const bad=await s.handle(call(2,'looklook_analyze',{url:'https://a',mode:'nope'}));
 assert.equal(bad.result.isError,true);assert.equal(calls,1);
 const thrown=createServer({execute:async()=>{throw new Error('x');},send:()=>{}});
 assert.equal((await thrown.handle(call(3,'looklook_analyze',{url:'https://a'}))).result.isError,true);
 assert.equal((await s.handle(call(4,'nope',{}))).error.code,-32602);
});

test('长任务超过等待时限返回 job_id，status 取回结果，重复提交复用同一任务',async()=>{
 const gate=deferred();let calls=0;
 const s=createServer({execute:()=>{calls++;return gate.promise;},send:()=>{},waitMs:20});
 const first=await s.handle(call(1,'looklook_analyze',{url:'https://youtu.be/x'}));
 const id=/job_id: (\S+)/.exec(first.result.content[0].text)[1];
 assert.equal(first.result.isError,false);assert.match(first.result.content[0].text,/looklook_status/);
 const again=await s.handle(call(2,'looklook_analyze',{url:'https://youtu.be/x'}));
 assert.equal(/job_id: (\S+)/.exec(again.result.content[0].text)[1],id);assert.equal(calls,1);
 const still=await s.handle(call(3,'looklook_status',{job_id:id}));assert.match(still.result.content[0].text,/仍在 Mac 上运行/);
 gate.resolve({status:'ready',answer:'迟到的正文'});
 const done=await s.handle(call(4,'looklook_status',{job_id:id}));
 assert.equal(done.result.isError,false);assert.equal(done.result.content[0].text,'迟到的正文\n\n待删除中间文件：无');
 const missing=await s.handle(call(5,'looklook_status',{job_id:'nope'}));assert.equal(missing.result.isError,true);
});

test('带 progressToken 时等待期间发送进度通知',async()=>{
 const gate=deferred();const sent=[];
 const s=createServer({execute:()=>gate.promise,send:m=>sent.push(m),waitMs:1000,progressMs:10});
 const p=s.handle(call(1,'looklook_analyze',{url:'https://youtu.be/y'},{progressToken:'tok'}));
 await new Promise(r=>setTimeout(r,45));gate.resolve({status:'ready',answer:'好了'});
 const r=await p;assert.equal(r.result.content[0].text,'好了\n\n待删除中间文件：无');
 assert.ok(sent.length>=2);assert.equal(sent[0].method,'notifications/progress');assert.equal(sent[0].params.progressToken,'tok');assert.ok(sent[1].params.progress>sent[0].params.progress);
});

test('stdio 逐行收发，解析错误返回 -32700，长任务不阻塞 ping',async()=>{
 const input=new PassThrough(),output=new PassThrough();const gate=deferred();const lines=[];
 output.on('data',d=>lines.push(...String(d).split('\n').filter(Boolean).map(JSON.parse)));
 serve({input,output,execute:()=>gate.promise,waitMs:5000});
 input.write('{bad json\n');
 input.write(JSON.stringify(call(1,'looklook_analyze',{url:'https://youtu.be/z'}))+'\n');
 input.write(JSON.stringify({jsonrpc:'2.0',id:2,method:'ping'})+'\n');
 await new Promise(r=>setTimeout(r,30));
 assert.deepEqual(lines.map(l=>l.id),[null,2]);assert.equal(lines[0].error.code,-32700);
 gate.resolve({status:'ready',answer:'完成'});await new Promise(r=>setTimeout(r,30));
 assert.equal(lines[2].id,1);assert.equal(lines[2].result.content[0].text,'完成\n\n待删除中间文件：无');
});
