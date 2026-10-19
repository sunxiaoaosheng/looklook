import test from 'node:test';import assert from 'node:assert/strict';
import {LooklookConversation,detectEntry,parseMode,parseDetail,jobRequest,MODE_PROMPT,DETAIL_PROMPT} from '../interaction.js';

const share='7.99 复制打开抖音，看看【吸金11记 #财富】 https://v.douyin.com/abc/ 01/02 x@y.com';
test('只自动识别视频平台链接，其他网页需要 look 前缀',()=>{
 assert.equal(detectEntry(share).source,'https://v.douyin.com/abc/');
 assert.equal(detectEntry('看看这篇 https://example.com/a'),null);
 assert.equal(detectEntry('look https://example.com/v').source,'https://example.com/v');
 assert.equal(detectEntry('lookup https://example.com/v'),null);
 assert.equal(detectEntry('普通聊天'),null);
 assert.match(detectEntry('look https://youtu.be/a https://youtu.be/b').error,/一个链接/);
});
test('两轮问答：先问目的，再问详略，表情数字文字都可以',()=>{
 const c=new LooklookConversation();
 assert.deepEqual(c.handle('k',share),{kind:'reply',text:MODE_PROMPT});
 assert.deepEqual(c.handle('k','🙉'),{kind:'reply',text:DETAIL_PROMPT});
 const r=c.handle('k','详细');assert.equal(r.kind,'submit');
 assert.deepEqual(r.jobs[0],{source:'https://v.douyin.com/abc/',mode:'credibility',detail:'detailed',purpose:'',model:null,debug:false});
 assert.equal(parseMode('2'),'text');assert.equal(parseDetail('1'),'brief');
});
test('仅视频、仅文本不问详略；消息里写明的选项直接跳过',()=>{
 const c=new LooklookConversation();
 c.handle('a',share);assert.equal(c.handle('a','1').jobs[0].mode,'video');
 const r=c.handle('b','look 详细评价 用 deepseek https://www.bilibili.com/video/BV1xx');
 assert.equal(r.kind,'submit');assert.deepEqual([r.jobs[0].mode,r.jobs[0].detail,r.jobs[0].model],['evaluate','detailed','deepseek']);
});
test('其他：收集具体目的；分享标题不被当作目的',()=>{
 const c=new LooklookConversation();
 c.handle('k','【标题】 https://b23.tv/x');assert.equal(c.handle('k','🐵').text,'请直接说明想怎么处理这条视频（例如“总结成三点”）。');
 assert.equal(c.handle('k','总结成三点').text,DETAIL_PROMPT);
 const r=c.handle('k','🐣');assert.deepEqual([r.jobs[0].mode,r.jobs[0].purpose,r.jobs[0].detail],['other','总结成三点','normal']);
});
test('中途发新链接：可切换或排队，不把回答套到错误视频上',()=>{
 const c=new LooklookConversation();
 c.handle('k','https://youtu.be/one');
 assert.match(c.handle('k','https://youtu.be/two').text,/切换到新链接/);
 assert.match(c.handle('k','2').text,/排在后面/);
 const first=c.handle('k','📀');assert.equal(first.jobs[0].source,'https://youtu.be/one');
 assert.match(first.text,/下一条链接：https:\/\/youtu.be\/two/);
 c.handle('k','🙊');const second=c.handle('k','极简');assert.equal(second.jobs[0].source,'https://youtu.be/two');
 c.handle('s','https://youtu.be/one');c.handle('s','https://youtu.be/two');
 assert.equal(c.handle('s','1').text,MODE_PROMPT);assert.equal(c.handle('s','📀').jobs[0].source,'https://youtu.be/two');
});
test('取消、过期后回到普通聊天，重复投递不重复处理，不同对象互不影响',()=>{
 let now=0;const c=new LooklookConversation({ttlMs:1000,now:()=>now});
 c.handle('k','https://youtu.be/a');assert.equal(c.handle('k','取消').text,'已取消。');assert.equal(c.handle('k','你好').kind,'pass');
 c.handle('k','https://youtu.be/a');now=5000;assert.equal(c.handle('k','你好').kind,'pass');
 assert.equal(c.handle('m','https://youtu.be/a','id1').text,MODE_PROMPT);assert.equal(c.handle('m','https://youtu.be/a','id1').text,'');
 assert.equal(c.handle('other','3').kind,'pass');
});
test('详略答不上来时再问一次，再不明确用日常交流',()=>{
 const c=new LooklookConversation();c.handle('k','https://youtu.be/a');c.handle('k','3');
 assert.equal(c.handle('k','嗯').text,DETAIL_PROMPT);assert.equal(c.handle('k','嗯').jobs[0].detail,'normal');
});
test('执行器请求只把链接放进 input，其余字段单独传递',()=>{
 assert.deepEqual(jobRequest({source:'https://youtu.be/a',mode:'other',detail:'brief',purpose:'$(rm -rf ~)',model:'codex',debug:true},{scope:'s'}),
  {input:'https://youtu.be/a',purpose:'$(rm -rf ~)',mode:'other',detail:'brief',scope:'s',model:'codex',debug:true});
});
