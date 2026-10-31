import {test} from 'node:test';
import assert from 'node:assert/strict';
import {LooklookConversation} from '../interaction.js';
const url='https://v.douyin.com/example/';
test('30秒到期默认评价日常，重复到期不会重复提交',()=>{let now=0;const c=new LooklookConversation({now:()=>now});assert.match(c.handle('a',url).text,/30秒/);now=29999;assert.equal(c.expire('a'),null);now=30000;const r=c.expire('a');assert.equal(r.jobs[0].mode,'evaluate');assert.equal(r.jobs[0].detail,'normal');assert.equal(c.expire('a'),null);});
test('取消阻止超时；会话隔离；已选用途保留',()=>{let now=0;const c=new LooklookConversation({now:()=>now});c.handle('a',url);c.handle('b',url);c.handle('a','取消');c.handle('b','判断可信度');now=30000;assert.equal(c.expire('a'),null);assert.equal(c.expire('b').jobs[0].mode,'credibility');});
test('一次回复用途和详略立即提交',()=>{const c=new LooklookConversation();c.handle('a',url);const r=c.handle('a','🙊🐥');assert.equal(r.kind,'submit');assert.equal(r.jobs[0].detail,'detailed');});
test('持久化选择可在重启后按原截止时间到期',()=>{let now=0;const c=new LooklookConversation({now:()=>now});c.handle('a',url);const saved=JSON.parse(JSON.stringify([...c.states]));now=31000;const next=new LooklookConversation({now:()=>now});next.states=new Map(saved);assert.equal(next.expire('a').kind,'submit');});
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {ChoiceWorker} from '../choice-worker.js';
import {state} from '../runtime.js';
test('后台恢复、失败重试、取消和重复轮询',async()=>{
 const root=mkdtempSync(join(tmpdir(),'looklook-choice-'));const old=process.env.LOOKLOOK_STATE_ROOT;process.env.LOOKLOOK_STATE_ROOT=root;
 try{
  let now=0,submitted=[];const c=new LooklookConversation({now:()=>now});c.handle('a',url);
  const queue={submit:async(route,request)=>{submitted.push(request);}};
  const w=new ChoiceWorker({namespace:'test-choice',conversation:c,queue});w.remember('a',{},{});
  await w.tick();assert.equal(submitted.length,0);now=31000;
  const fresh=new LooklookConversation({now:()=>now});const resumed=new ChoiceWorker({namespace:'test-choice',conversation:fresh,queue});await resumed.tick();await resumed.tick();assert.equal(submitted.length,1);
  assert.equal(submitted[0].mode,'evaluate');
  c.handle('b',url);w.remember('b',{},{});c.handle('b','取消');w.remember('b',{},{});now+=31000;await w.tick();assert.equal(submitted.length,1);
  c.handle('retry',url);w.remember('retry',{},{});now+=31000;w.queue={submit:async()=>{throw Error('暂不可用');}};await assert.rejects(w.tick());assert.equal(state('get','test-choice','retry').status,'queued');w.queue=queue;await w.tick();assert.equal(submitted.length,2);
 }finally{if(old===undefined)delete process.env.LOOKLOOK_STATE_ROOT;else process.env.LOOKLOOK_STATE_ROOT=old;rmSync(root,{recursive:true,force:true});}
});
