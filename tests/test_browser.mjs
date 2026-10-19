import test from 'node:test';
import assert from 'node:assert/strict';
import {findRecords,mediaRecord,collectPage} from '../scripts/browser_media.mjs';
const id='7693035311652126314';
function record(seconds=363.5,host='v5.zjcdn.com') { return {aweme_id:id,desc:'测试作品',video:{duration:seconds*1000,play_addr:{url_list:[`https://${host}/oXXXXXXXXXXXXXXXXXXXXXXXX/media-video-avc1/`]},audio:{url_list:[`https://${host}/oYYYYYYYYYYYYYYYYYYYYYYYY/media-audio-und-mp4a/`]}}}; }
function page(data,delay=0) { let n=0;return {on(){},async goto(){},url:()=>`https://www.douyin.com/video/${id}`,async evaluate(){return ++n>delay?data:[];},async waitForTimeout(){}}; }
test('只出现占位地址必须失败',async()=>{await assert.rejects(collectPage(page([{src:'https://lf-douyin-pc-web.douyinstatic.com/obj/uuu_265.mp4',duration:2.6}]),'分享',`https://www.douyin.com/video/${id}`,5),/目标编号/);});
test('目标延迟出现后成功，时长取目标',async()=>{
 // 前四次读取为空，按每轮 250 毫秒需等待约 1 秒；留出执行调度余量。
 const out=await collectPage(page([record()],4),'分享',`https://www.douyin.com/video/${id}`,3000);
 assert.equal(out.duration,363.5);assert.equal(out.target_id,id);
});
test('混入其他作品不接受',()=>{const other=record();other.aweme_id='123';assert.equal(findRecords([other],id).length,0);assert.equal(mediaRecord(other,id,'测试'),null);});
test('两类域名及真正两秒作品均有效',()=>{for(const host of ['v3-web.douyinvod.com','v5-hl-mly-ov.zjcdn.com'])assert.equal(mediaRecord(record(2,host),id,'测试').duration,2);});
test('网络出现无关联音频不能凭域名配对',()=>{const r=record();delete r.video.audio;assert.equal(mediaRecord(r,id,'测试',['https://v5.zjcdn.com/unknown/media-audio-und-mp4a/']),null);});
test('封面音乐占位标签不能成为媒体',()=>{const r=record();delete r.video.play_addr;delete r.video.audio;r.video.cover={url_list:['https://v5.zjcdn.com/cover.mp4']};assert.equal(mediaRecord(r,id,'测试'),null);});
test('音轨类型使用目标记录字段，不依赖URL固定文字',()=>{
 const r=record();r.video.play_addr.url_list=['https://v5.zjcdn.com/video.mp4'];r.video.audio.url_list=['https://v5.zjcdn.com/audio.m4a'];
 const out=mediaRecord(r,id,'测试');assert.equal(out.media.find(x=>x.url.endsWith('audio.m4a')).kind,'audio');
});
