import argparse,importlib.util,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('runner',Path(__file__).parents[1]/'runner.py')
r=importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
class RunnerTests(unittest.TestCase):
 def test_inputs_scopes_and_purposes(self):
  for body in ('口令',str(Path(__file__).resolve())):
   with self.assertRaises(r.JobError):r.parse_request({'input':body})
  self.assertNotEqual(r.parse_request({'input':'总结 https://v.douyin.com/a/','scope':'a'})[2],r.parse_request({'input':'总结 https://v.douyin.com/a/','scope':'b'})[2])
 def test_debug_requires_explicit_prefix_or_field(self):
  self.assertTrue(r.debug_requested({'input':'/looklook --debug 分析 https://v.douyin.com/a/'}))
  self.assertTrue(r.debug_requested({'input':'链接','debug':True}))
  self.assertFalse(r.debug_requested({'input':'/looklook 看看调试教程 --debug https://v.douyin.com/a/'}))
 def test_final_answer_only_last_agent_message(self):
  events=[{'type':'item.completed','item':{'type':'reasoning','text':'过程'}},{'type':'item.completed','item':{'type':'agent_message','text':'前文'}},{'type':'item.completed','item':{'type':'agent_message','text':'完整分析'}}]
  self.assertEqual(r.final_answer('\n'.join(json.dumps(e) for e in events)),'完整分析')
 def test_execute_writes_no_report_state_or_logs(self):
  with tempfile.TemporaryDirectory() as t,patch.object(r,'FILES',Path(t)):
   root=Path(t);video=root/'默认名.mp4';text=root/'默认名.txt';video.write_bytes(b'video');text.write_text('真实正文')
   material={'status':'ready','media':{'path':str(video)},'transcript':{'text':str(text)},'temporary_files':[]}
   with patch.object(r,'prepare_material',return_value=material),patch.object(r,'analyze',return_value='完整分析。'*30):result=r.execute({'input':'https://v.douyin.com/a/'})
   self.assertEqual(result['status'],'ready');self.assertEqual({p.name for p in root.iterdir()},{'默认名.mp4','默认名.txt'})
 def test_analysis_error_preserves_material_and_cleanup(self):
  with tempfile.TemporaryDirectory() as t,patch.object(r,'FILES',Path(t)):
   material={'media':{'path':t+'/v.mp4'},'transcript':{'text':t+'/v.txt'},'temporary_files':[{'path':t+'/临时.wav','purpose':'音频'}]}
   with patch.object(r,'prepare_material',return_value=material),patch.object(r,'analyze',side_effect=r.JobError('model_capacity','繁忙')):result=r.execute({'input':'https://v.douyin.com/a/'})
   self.assertEqual(result['code'],'model_capacity');self.assertEqual(result['temporary_files'],material['temporary_files']);self.assertEqual(list(Path(t).iterdir()),[])
 def test_preparation_failure_retains_cleanup(self):
  with tempfile.TemporaryDirectory() as t,patch.object(r,'FILES',Path(t)),patch.object(r,'prepare_material',side_effect=r.JobError('failed','下载失败',{'temporary_files':[{'path':t+'/未完成.part','purpose':'片段'}]})):
   result=r.execute({'input':'https://v.douyin.com/a/'});self.assertTrue(result['temporary_files'])
 def test_debug_creates_only_visible_debug_files(self):
  with tempfile.TemporaryDirectory() as t,patch.object(r,'FILES',Path(t)):
   root=Path(t);text=root/'v.txt';text.write_text('正文')
   debug_dir=root/'调试-测试';debug_dir.mkdir()
   material={'status':'ready','media':{'path':t+'/v.mp4'},'transcript':{'text':str(text)},'temporary_files':[],'debug_dir':str(debug_dir)}
   def analyze(m,p,ctx,*a,**k):ctx.record('analysis','诊断');return '完整分析。'*30
   with patch.object(r,'run_capture',return_value=argparse.Namespace(returncode=0,stdout=json.dumps(material),stderr='')) as capture,patch.object(r,'analyze',side_effect=analyze):
    result=r.execute({'input':'https://v.douyin.com/a/','debug':True})
   self.assertIn('--debug',capture.call_args.args[0]);self.assertEqual(Path(result['debug_dir']),debug_dir)
   self.assertEqual([p.name for p in root.iterdir() if p.is_dir()],['调试-测试'])
   self.assertEqual(len(list(debug_dir.iterdir())),2)
 def test_codex_command_no_output_file_and_keys_removed(self):
  with tempfile.TemporaryDirectory() as t:
   ctx=r.prepare.Context(Path(t));events=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'完整分析。'*30}})
   results=[argparse.Namespace(returncode=0,stdout='',stderr='Logged in using ChatGPT'),argparse.Namespace(returncode=0,stdout=events,stderr='')]
   with patch.object(r,'run_capture',side_effect=results) as run:r.call_codex('提示',ctx,Path(t),'normal')
   command=run.call_args.args[0];self.assertIn('--json',command);self.assertIn('--ephemeral',command);self.assertNotIn('--output-last-message',command)
   self.assertIn('gpt-6.1-sol',command);self.assertIn('model_reasoning_effort="medium"',command)
   self.assertEqual(list(Path(t).iterdir()),[])
  with patch.dict(os.environ,{'OPENAI_API_KEY':'fixture','CODEX_API_KEY':'fixture','OPENAI_BASE_URL':'fixture','ANTHROPIC_API_KEY':'fixture'}):
   env=r.model_environment();self.assertFalse(any(k in env for k in ('OPENAI_API_KEY','CODEX_API_KEY','OPENAI_BASE_URL','ANTHROPIC_API_KEY')))
 def test_chain_falls_back_only_on_provider_errors_and_labels(self):
  calls=[]
  def fake(provider,*a):
   calls.append(provider)
   if provider in ('claude','codex'):raise r.ProviderError('不可用')
   return '分析正文'*30
  with tempfile.TemporaryDirectory() as t,patch.object(r,'call_provider',side_effect=fake):
   answer=r.analyze({},'目的',r.prepare.Context(Path(t)),'credibility','normal')
  self.assertEqual(calls,['claude','codex','deepseek'])
  self.assertIn('分析模型：DeepSeek Flash',answer);self.assertIn('已自动改用 DeepSeek Flash',answer);self.assertIn('未联网核验',answer)
 def test_named_model_is_not_silently_replaced(self):
  with tempfile.TemporaryDirectory() as t,patch.object(r,'call_provider',side_effect=r.ProviderError('额度不足')) as call:
   with self.assertRaises(r.JobError) as e:r.analyze({},'目的',r.prepare.Context(Path(t)),'evaluate','normal','codex')
  self.assertEqual(call.call_count,1);self.assertEqual(e.exception.code,'model_unavailable')
  self.assertEqual(r.requested_provider({},'请用 DeepSeek 分析'),'deepseek');self.assertIsNone(r.requested_provider({},'评价一下'))
 def test_openclaw_result_must_come_from_requested_model(self):
  with tempfile.TemporaryDirectory() as t:
   ctx=r.prepare.Context(Path(t))
   other=argparse.Namespace(returncode=0,stdout='[proxy] x\n'+json.dumps({'ok':True,'provider':'deepseek','model':'deepseek-v4-pro','outputs':[{'text':'正文'*50}]}),stderr='')
   with patch.object(r,'run_capture',return_value=other),self.assertRaises(r.ProviderError):r.call_openclaw(r.DEEPSEEK_MODEL,'p',ctx,'normal','deepseek')
   good=argparse.Namespace(returncode=0,stdout=json.dumps({'ok':True,'provider':'deepseek','model':'deepseek-v4-flash','outputs':[{'text':'正文'*50}]}),stderr='')
   with patch.object(r,'run_capture',return_value=good) as run:self.assertEqual(r.call_openclaw(r.DEEPSEEK_MODEL,'p',ctx,'normal','deepseek'),'正文'*50)
   self.assertIn('--agent',run.call_args.args[0])
 def test_video_and_text_modes_skip_analysis(self):
  with tempfile.TemporaryDirectory() as t,patch.object(r,'FILES',Path(t)):
   root=Path(t);video=root/'标题.mp4';text=root/'标题.txt';video.write_bytes(b'v');text.write_text('逐字稿')
   material={'status':'ready','media':{'path':str(video),'duration':125},'transcript':{'text':str(text)},'temporary_files':[]}
   with patch.object(r,'prepare_material',return_value=material) as prep,patch.object(r,'analyze') as an:
    v=r.execute({'input':'https://v.douyin.com/a/','mode':'video'});x=r.execute({'input':'https://v.douyin.com/a/','mode':'text'})
   an.assert_not_called();self.assertTrue(prep.call_args_list[0].kwargs['video_only']);self.assertIn('2分05秒',v['answer']);self.assertIn('逐字稿',x['answer'])
 def test_prepare_reads_stdout_not_json_file(self):
  with tempfile.TemporaryDirectory() as t,patch.object(r,'FILES',Path(t)):
   root=Path(t);text=root/'v.txt';text.write_text('正文');ctx=r.prepare.Context(root)
   result={'status':'ready','media':{'path':t+'/v.mp4'},'transcript':{'text':str(text)},'temporary_files':[]}
   with patch.object(r,'run_capture',return_value=argparse.Namespace(returncode=0,stdout=json.dumps(result),stderr='')) as run:
    self.assertEqual(r.prepare_material('https://v.douyin.com/a/',ctx),result)
   self.assertNotIn('--debug',run.call_args.args[0]);self.assertEqual([p.name for p in root.iterdir()],['v.txt'])
 def test_prepare_progress_events_do_not_break_result(self):
  final={'status':'failed','code':'download_timeout','message':'超时','temporary_files':[{'path':'/x/临时.mp4','purpose':'候选'}]}
  out=json.dumps({'status':'waiting_external','event':'downie_waiting'})+'\n'+json.dumps(final)+'\n'
  self.assertEqual(r.prepare_result(out),final)
  with self.assertRaises(r.JobError):r.prepare_result(json.dumps({'status':'waiting_external'}))
if __name__=='__main__':unittest.main()

class MissingProgramTests(unittest.TestCase):
 def test_missing_claude_falls_back_but_named_model_fails(self):
  with tempfile.TemporaryDirectory() as t:
   material={'transcript':{'text':str(Path(t)/'v.txt')}};ctx=r.prepare.Context(Path(t))
   with patch.object(r,'run_capture',side_effect=FileNotFoundError('missing')),patch.object(r,'call_codex',return_value='正文'*100) as codex:
    answer=r.analyze(material,'目的',ctx,files=Path(t));self.assertIn('Codex',answer);codex.assert_called_once()
   with patch.object(r,'run_capture',side_effect=FileNotFoundError('missing')):
    with self.assertRaises(r.JobError):r.analyze(material,'目的',ctx,model='claude',files=Path(t))
