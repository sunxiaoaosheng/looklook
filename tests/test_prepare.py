import importlib.util
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('prepare',Path(__file__).parents[1]/'scripts/prepare.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class Files(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.ctx=p.Context(self.root)
 def tearDown(self):self.tmp.cleanup()
 def test_clean_only_owned_and_consumed(self):
  old=self.root/'历史.mp4';old.write_bytes(b'old')
  outside=self.root/'其他任务.wav';outside.write_bytes(b'other')
  tmp=self.ctx.temp('.m4a','音轨');tmp.write_bytes(b'a');self.ctx.seal(tmp)
  frame=self.ctx.temp('.jpg','画面',True);frame.write_bytes(b'f');self.ctx.seal(frame)
  self.ctx.cleanup();self.assertFalse(tmp.exists());self.assertTrue(frame.exists());self.assertTrue(old.exists());self.assertTrue(outside.exists())
  self.ctx.cleanup(True);self.assertFalse(frame.exists())
 def test_changed_file_is_retained(self):
  tmp=self.ctx.temp('.wav','音轨');tmp.write_bytes(b'changed');self.ctx.cleanup();self.assertTrue(tmp.exists());self.assertTrue(self.ctx.warnings)
 def test_replaced_and_symlink_not_deleted(self):
  tmp=self.ctx.temp('.wav','音轨');other=self.root/'历史';other.write_bytes(b'protected');tmp.unlink();tmp.symlink_to(other);self.ctx.cleanup();self.assertEqual(other.read_bytes(),b'protected');self.assertTrue(tmp.is_symlink())
 def test_publish_collision_never_overwrites(self):
  target=self.root/'作品.mp4';target.write_bytes(b'old');candidate=self.ctx.temp('.mp4','候选');candidate.write_bytes(b'new');self.ctx.seal(candidate)
  with self.assertRaises(p.MaterialError):p.publish_video(candidate,target,self.ctx)
  self.assertEqual(target.read_bytes(),b'old')
 def test_preflight_missing_model_does_not_invent_asr_call(self):
  with patch.object(p,'run',return_value='没有模型'):
   with self.assertRaises(p.MaterialError):p.transcribe('video',self.ctx,'macwhisper','auto',10)
  self.assertFalse(self.ctx.attempts)
 def test_true_backend_fallback_uses_two_calls(self):
  self.ctx.asr_model={'id':'existing:model','name':'模型'}
  with patch.object(p,'run',side_effect=p.MaterialError('backend_failed','识别失败')):
   with self.assertRaises(p.MaterialError):p.transcribe('video',self.ctx,'macwhisper','auto',10)
  with patch.object(Path,'is_file',return_value=True),patch.object(p.os,'access',return_value=True),patch.object(p,'find_tool',side_effect=lambda x:x),patch.object(p,'run',side_effect=['','0.0 -- 9.5: 成功']):
   p.transcribe('video',self.ctx,'sensevoice','zh',10)
  self.assertEqual([a['backend'] for a in self.ctx.attempts],['macwhisper','sensevoice'])
 def test_empty_and_structural_check_is_not_listening(self):
  self.assertIn('replacement_character',p.structure_check('文字�',60)['flags']);self.assertFalse(p.structure_check('文字',60)['listening_verified'])
 def test_cancel_child_does_not_kill_unrelated(self):
  unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
  try:
   with self.assertRaises(p.MaterialError):p.run([sys.executable,'-c','import time;time.sleep(20)'],self.ctx,'test',.1)
   self.assertIsNone(unrelated.poll());self.assertFalse(self.ctx.children)
  finally:unrelated.terminate();unrelated.wait()
 def test_transcription_attempts_limit_and_review_selection(self):
  self.ctx.asr_model={'id':'existing:model','name':'已有模型'}
  with patch.object(p,'run',return_value='1\n00:00:00,000 --> 00:00:10,000\n第一遍\n'):
   first=p.transcribe('video',self.ctx,'macwhisper','auto',10)
  with patch.object(p,'run',return_value='1\n00:00:00,000 --> 00:00:10,000\n第二遍\n'):
   p.transcribe('video',self.ctx,'macwhisper','zh',10)
   with self.assertRaises(p.MaterialError):p.transcribe('video',self.ctx,'macwhisper','zh',10)
  with patch.object(p,'emit'),patch.object(p,'review_action',return_value={'action':'accept','attempt':1}):
   chosen=p.review_transcript('video',self.ctx,first,SimpleNamespace(asr_language='auto',timeout=10),{'duration':10})
  self.assertEqual(chosen['text'],'第一遍');self.assertEqual([a['language'] for a in self.ctx.attempts],['auto','zh'])
 def test_failed_call_counts_and_can_fallback_once(self):
  self.ctx.asr_model={'id':'existing:model','name':'模型'}
  with patch.object(p,'run',side_effect=p.MaterialError('backend_failed','失败')):
   with self.assertRaises(p.MaterialError):p.transcribe('video',self.ctx,'macwhisper','auto',10)
  with patch.object(p,'run',return_value='1\n00:00:00,000 --> 00:00:10,000\n成功\n'):
   p.transcribe('video',self.ctx,'macwhisper','zh',10)
  self.assertEqual(len(self.ctx.attempts),2);self.assertEqual(self.ctx.attempts[0]['status'],'failed')
 def test_subtitles_and_bilibili_regression(self):
  cues=p.parse_subtitles('{"body":[{"from":0,"to":60,"content":"字幕"}]}');self.assertTrue(p.subtitle_complete(cues,60))
  self.assertEqual(p.normalize_input('https://www.bilibili.com/video/BV12345/?p=2&junk=1')[0],'https://www.bilibili.com/video/BV12345/?p=2')
  self.assertEqual(p.download_routes('bilibili',{},'auto'),['yt-dlp'])
 def test_invalid_review_language_is_controlled_failure(self):
  first={'attempt':1,'backend':'macwhisper','text':'候选'};self.ctx.candidates.append(first)
  with patch.object(p,'emit'),patch.object(p,'review_action',return_value={'action':'retry','language':None,'reason':'wrong_language'}):
   with self.assertRaises(p.MaterialError) as e:p.review_transcript('video',self.ctx,first,SimpleNamespace(asr_language='auto',asr_backend='auto',timeout=10),{'duration':10})
  self.assertEqual(e.exception.code,'invalid_language')
 def test_review_lines_and_partial_input_timeout(self):
  import os
  read,write=os.pipe();p._REVIEW_BUFFER.clear()
  try:
   os.write(write,b'{"action":"accept","attempt":1}\n{"action":"finish"}\n')
   fake=SimpleNamespace(fileno=lambda:read)
   with patch.object(p.sys,'stdin',fake):
    self.assertEqual(p.review_action()['action'],'accept');self.assertEqual(p.review_action()['action'],'finish')
   os.write(write,b'{"action":')
   with patch.object(p.sys,'stdin',fake),patch.object(p.select,'select',side_effect=[([read],[],[]),([],[],[])]):
    with self.assertRaises(p.MaterialError) as e:p.review_action()
   self.assertEqual(e.exception.code,'review_timeout')
  finally:os.close(read);os.close(write);p._REVIEW_BUFFER.clear()
 def test_bad_subtitle_entries_are_not_accepted(self):
  self.assertEqual(p.parse_subtitles('{"body":[null]}'),[])
  self.assertEqual(p.parse_subtitles('{"body":[{"from":0,"to":10,"content":123}]}'),[])
  self.assertEqual(p.parse_subtitles(123),[])
 def test_subtitle_read_has_total_size_bound(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,n):return b'x'*n
  with patch.object(p,'choose_subtitle',return_value=('subtitles','zh',{'url':'https://example.test'})),patch.object(p,'request',return_value=Response()):
   with self.assertRaises(p.MaterialError) as e:p.subtitle_text({},self.ctx)
  self.assertEqual(e.exception.code,'subtitle_too_large')
 def test_pending_downie_blocks_fallback(self):
  self.ctx.download_deadline=time.monotonic()+.03
  with patch.object(p,'run'),patch.object(p,'find_tool',return_value='open'):
   with self.assertRaises(p.MaterialError) as e:p.downie_download('url',self.ctx,.03)
  self.assertEqual(e.exception.code,'download_timeout')
 def test_title_cleaning_strips_platform_tail_only(self):
  self.assertEqual(p.clean_title('吸金11记 #思维 - 抖音'),'吸金11记 #思维')
  self.assertEqual(p.clean_title('标题_哔哩哔哩_bilibili'),'标题')
  self.assertEqual(p.clean_title('如何玩抖音'),'如何玩抖音')
  self.assertEqual(p.clean_title('A/B: t? - YouTube'),'A B t')
 def test_same_video_reused_different_video_suffixed(self):
  self.root=self.root.resolve();old=self.root/'标题.mp4';old.write_bytes(b'v');(self.root/'标题.txt').write_text('旧文本')
  ctx=p.Context(self.root)
  with patch.object(p,'probe',return_value={'duration':100.4}):
   self.assertEqual(p.resolve_target(ctx,'标题',100),(self.root/'标题 (2).mp4',False))
   self.assertEqual(p.resolve_target(ctx,'标题',300),(self.root/'标题 (2).mp4',False))
   self.assertEqual(p.resolve_target(ctx,'标题',100,reuse=False),(self.root/'标题 (2).mp4',False))
  (self.root/'标题 (2).txt').write_text('孤立文本')
  with patch.object(p,'probe',return_value={'duration':1}):self.assertEqual(p.resolve_target(ctx,'标题',300)[0],self.root/'标题 (3).mp4')
 def test_downie_file_renamed_to_title(self):
  media=self.root/'标题 - 抖音.mp4';media.write_bytes(b'v');self.ctx.adopt(media,'Downie')
  (self.root/'标题.mp4').write_bytes(b'old')
  renamed=p.rename_to_title(media,p.clean_title(media.stem),self.ctx)
  self.assertEqual(renamed.name,'标题 (2).mp4');self.assertFalse(media.exists());self.assertIn(str(renamed),self.ctx.owned)

class RangeServer(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.ctx=p.Context(Path(self.tmp.name));self.ctx.download_deadline=time.monotonic()+10
  self.mode='valid';self.data=b'01234567'*(400000);self.active=0;self.maximum=0;outer=self
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    if outer.mode=='expired':self.send_response(403);self.end_headers();return
    if outer.mode=='drip':
     self.send_response(200);self.send_header('Transfer-Encoding','chunked');self.end_headers()
     try:
      for x in b'000000000000001\r\nX\r\n':self.wfile.write(bytes([x]));self.wfile.flush();time.sleep(.04)
     except (BrokenPipeError,ConnectionResetError):pass
     return
    hdr=self.headers.get('Range')
    if not hdr or outer.mode=='ignore':
     self.send_response(200);self.send_header('Content-Length',str(len(outer.data)));self.end_headers();
     try:self.wfile.write(outer.data)
     except (BrokenPipeError,ConnectionResetError):pass
     return
    a,b=map(int,hdr.removeprefix('bytes=').split('-'));body=outer.data[a:b+1]
    self.send_response(206);self.send_header('Content-Range',f'bytes {a}-{b}/{len(outer.data)+(1 if outer.mode=="wrong_total" and b else 0)}')
    self.send_header('ETag','"changed"' if outer.mode=='version' and b else '"stable"')
    if outer.mode=='short' and b:body=body[:-1]
    self.send_header('Content-Length',str(len(body)));self.end_headers()
    outer.active+=1;outer.maximum=max(outer.maximum,outer.active)
    try:time.sleep(.01);self.wfile.write(body)
    except (BrokenPipeError,ConnectionResetError):pass
    finally:outer.active-=1
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url='http://127.0.0.1:'+str(self.server.server_port)
 def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
 def test_valid_parallel_and_bound(self):
  path=self.ctx.temp('.m4a','测试');stats=p.parallel_download(self.url,path,self.ctx,2)
  self.assertEqual(path.read_bytes(),self.data);self.assertEqual(stats['segments_total'],4);self.assertLessEqual(stats['observed_max_concurrency'],2)
 def test_bad_range_never_passes(self):
  for mode in ['ignore','wrong_total','version','short']:
   self.mode=mode;path=self.ctx.temp('.m4a','测试')
   with self.assertRaises(p.MaterialError):p.parallel_download(self.url,path,self.ctx,2)
 def test_dripping_chunk_header_cannot_escape_total_deadline(self):
  self.mode='drip';started=time.monotonic()
  with self.assertRaises(p.MaterialError) as e:
   with p.request(self.url,{},self.ctx,budget=.25) as response:response.read(1024)
  self.assertEqual(e.exception.code,'download_timeout');self.assertLess(time.monotonic()-started,2);self.assertFalse(self.ctx.children)
 def test_expiry_is_returned_for_single_refresh(self):
  self.mode='expired';path=self.ctx.temp('.m4a','测试')
  with patch.object(p,'check_media_url'):
   with self.assertRaises(p.MaterialError) as e:p.download_resource(self.url,path,self.ctx)
  self.assertEqual(e.exception.code,'address_expired')
 def test_range_failure_falls_back_once(self):
  self.mode='wrong_total';path=self.ctx.temp('.m4a','测试')
  with patch.object(p,'check_media_url'),patch.object(p,'sequential_download',side_effect=[p.MaterialError('download_slow','慢'),{'mode':'sequential'}]) as seq:
   stats=p.download_resource(self.url,path,self.ctx)
  self.assertEqual(seq.call_count,2);self.assertEqual(stats['parallel_rejected'],'range_invalid')


class Media(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.ctx=p.Context(self.root)
  self.full=self.root/'原件.mp4';self.silent=self.root/'无声.mp4';self.voice=self.root/'音频.m4a'
  ff=p.find_tool('ffmpeg')
  subprocess.run([ff,'-hide_banner','-v','error','-f','lavfi','-i','color=c=blue:s=64x64:r=10:d=8','-f','lavfi','-i','sine=frequency=440:duration=8','-c:v','libx264','-c:a','aac','-shortest',str(self.full)],check=True)
  subprocess.run([ff,'-hide_banner','-v','error','-i',str(self.full),'-map','0:v:0','-c','copy',str(self.silent)],check=True)
  subprocess.run([ff,'-hide_banner','-v','error','-i',str(self.full),'-map','0:a:0','-c','copy',str(self.voice)],check=True)
 def tearDown(self):self.tmp.cleanup()
 def test_real_tracks_and_unknown_source(self):
  self.assertFalse(p.probe(self.silent,self.ctx)['has_audio'])
  self.assertEqual(p.validate_video(self.full,self.ctx)['source_completeness'],'unknown')
  self.assertEqual(p.validate_video(self.full,self.ctx,8)['source_completeness'],'verified')
  with self.assertRaises(p.MaterialError):p.validate_video(self.silent,self.ctx,8)
  with self.assertRaises(p.MaterialError):p.validate_video(self.full,self.ctx,20)
 def test_short_video_cannot_use_three_second_slack(self):
  props={'duration':2,'has_video':True,'has_audio':True,'video_index':0,'audio_index':1}
  with patch.object(p,'probe',return_value=props),patch.object(p,'decoded_end',side_effect=[2,.1]):
   with self.assertRaises(p.MaterialError):p.validate_video('short.mp4',self.ctx,2)
  with patch.object(p,'probe',return_value=props),patch.object(p,'decoded_end',side_effect=[2,1.99]):
   self.assertEqual(p.validate_video('short.mp4',self.ctx,2)['source_completeness'],'verified')
 def test_corruption_is_rejected(self):
  broken=self.root/'坏文件.mp4';broken.write_bytes(self.full.read_bytes()[:200])
  with self.assertRaises(p.MaterialError):p.validate_video(broken,self.ctx,8)
 def test_silent_repair_reuses_picture_and_cleans_only_intermediates(self):
  own=self.ctx.create(self.root/'本轮.mp4','无声候选');own.write_bytes(self.silent.read_bytes());self.ctx.seal(own)
  data={'title':'本轮','target_id':'1','duration':8,'media':[{'kind':'audio','url':'https://v.zjcdn.com/audio'}]}
  def fetch(url,path,ctx):path.write_bytes(self.voice.read_bytes());ctx.seal(path)
  with patch.object(p,'browser_source',return_value=data),patch.object(p,'download_resource',side_effect=fetch) as d:
   final,props=p.browser_download('url',self.ctx,30,silent=own)
  self.assertEqual(d.call_count,1);self.assertEqual(final,own);self.assertTrue(props['has_audio'])
  self.ctx.media=final;txt=self.ctx.create(final.with_suffix('.txt'),'文本');txt.write_text('转录');self.ctx.seal(txt);self.ctx.text=txt
  self.ctx.cleanup(True)
  self.assertEqual(final.stem,'本轮');self.assertTrue(self.silent.exists());self.assertFalse(self.ctx.leftovers())
 def test_address_refresh_cannot_change_target(self):
  data={'title':'测试','target_id':'1','duration':8,'media':[{'kind':'complete','url':'https://v.zjcdn.com/media'}]}
  changed={**data,'target_id':'2'}
  with patch.object(p,'browser_source',side_effect=[data,changed]),patch.object(p,'download_resource',side_effect=p.MaterialError('address_expired','失效')):
   with self.assertRaises(p.MaterialError) as e:p.browser_download('url',self.ctx,30)
  self.assertEqual(e.exception.code,'target_mismatch')
 def test_bilibili_separate_formats_are_registered_and_muxed(self):
  info={'title':'B站单视频','id':'test','duration':8,'formats':[{'vcodec':'h264','acodec':'none','url':'https://video.test'},{'vcodec':'none','acodec':'aac','url':'https://audio.test'}]}
  original=p.run
  def run(command,ctx,label,timeout=120):
   if label=='ytdlp_media':
    dst=Path(command[-1]);dst.write_bytes((self.voice if dst.suffix=='.m4a' else self.silent).read_bytes());return ''
   return original(command,ctx,label,timeout)
  with patch.object(p,'run',side_effect=run):final,props=p.ytdlp_download('https://www.bilibili.com/video/BVtest',self.ctx,30,info)
  self.assertTrue(props['has_audio']);self.assertTrue(final.exists());self.assertTrue(all(str(x).startswith(str(self.ctx.root)) for x in self.ctx.owned))
 def test_mismatched_audio_cannot_be_published(self):
  short=self.root/'短音频.m4a';subprocess.run([p.find_tool('ffmpeg'),'-hide_banner','-v','error','-i',str(self.voice),'-t','1','-c','copy',str(short)],check=True)
  joined=self.ctx.temp('.mp4','错配候选');p.mux(self.silent,short,joined,self.ctx)
  with self.assertRaises(p.MaterialError):p.validate_video(joined,self.ctx,8)
 def test_review_saves_selected_candidate_only_after_accept(self):
  out=self.root/'结果';out.mkdir();calls=[]
  args=SimpleNamespace(source=str(self.full),storage_root=str(out),debug=False,download_concurrency=4,timeout=30,no_subs=True,asr_backend='macwhisper',asr_language='auto',review_transcript=True,visual=False)
  def recognize(path,ctx,backend,language,duration,timeout):
   n=len(ctx.attempts)+1;ctx.attempts.append({'number':n,'language':language,'backend':backend});c={'attempt':n,'text':'第一遍' if n==1 else '第二遍','backend':backend,'structure':p.structure_check('文字',duration)};ctx.candidates.append(c);return c
  actions=iter([{'action':'retry','language':'zh','reason':'wrong_language'},{'action':'accept','attempt':1},{'action':'finish'}])
  def action():
   x=next(actions)
   if x['action'] in ('retry','accept'):self.assertFalse((out/'原件.txt').exists())
   calls.append(x);return x
  with patch.object(p,'transcribe',side_effect=recognize),patch.object(p,'review_action',side_effect=action),patch.object(p,'emit'):
   result=p.prepare(args)
  self.assertEqual(result['status'],'ready');self.assertEqual((out/'原件.txt').read_text(),'第一遍\n');self.assertEqual(len(result['transcription_attempts']),2)
  self.assertEqual(sorted(x.suffix for x in out.iterdir()),['.mp4','.txt'])
 def test_cancel_keeps_no_text_and_preserves_original(self):
  out=self.root/'取消';out.mkdir();args=SimpleNamespace(source=str(self.full),storage_root=str(out),debug=False,download_concurrency=4,timeout=30,no_subs=True,asr_backend='macwhisper',asr_language='zh',review_transcript=True,visual=False)
  def recognize(path,ctx,*unused):
   c={'attempt':1,'text':'候选','backend':'macwhisper','structure':{}};ctx.candidates.append(c);return c
  with patch.object(p,'transcribe',side_effect=recognize),patch.object(p,'review_action',return_value={'action':'cancel'}),patch.object(p,'emit'):
   result=p.prepare(args)
  self.assertEqual(result['code'],'cancelled');self.assertFalse((out/'原件.txt').exists());self.assertTrue(self.full.exists())

if __name__=='__main__':unittest.main()
