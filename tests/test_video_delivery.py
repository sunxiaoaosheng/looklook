import json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import video_delivery as v
@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'需要本机媒体工具')
class VideoTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name);cls.source=cls.root/'原视频.mp4'
  subprocess.run([shutil.which('ffmpeg'),'-y','-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=25','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','6','-c:v','libx264','-preset','ultrafast','-qp','16','-c:a','aac',str(cls.source)],check=True)
  cls.original=cls.source.read_bytes()
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def test_direct_and_choice(self):
  result=v.prepare({'path':str(self.source),'limit':len(self.original)+1000});self.assertTrue(result['files'][0]['original'])
  result=v.prepare({'path':str(self.source),'limit':180000});self.assertEqual(result['status'],'waiting_choice')
 def test_compress_keeps_duration_audio_and_original(self):
  result=v.prepare({'path':str(self.source),'limit':180000,'strategy':'compress'});self.assertEqual(result['status'],'ready',result)
  for item in result['files']:v.validate(item['path'],180000,6,True)
  self.assertEqual(self.source.read_bytes(),self.original)
 def test_split_covers_full_timeline(self):
  result=v.prepare({'path':str(self.source),'limit':180000,'strategy':'split'});self.assertEqual(result['status'],'ready',result)
  self.assertGreater(len(result['files']),1);end=0
  for item in result['files']:
   self.assertAlmostEqual(item['start'],end);v.validate(item['path'],180000,item['end']-item['start'],True);end=item['end']
  self.assertAlmostEqual(end,6);self.assertEqual(self.source.read_bytes(),self.original)
 def test_cli_reports_owned_temporaries_before_final_result(self):
  script=Path(v.__file__)
  p=subprocess.run([sys.executable,str(script)],input=json.dumps({'path':str(self.source),'limit':180000,'strategy':'compress'}),text=True,capture_output=True,check=True)
  events=[json.loads(line) for line in p.stdout.splitlines()]
  self.assertEqual(events[-1]['status'],'ready');self.assertTrue(any(e.get('event')=='temporary_file' for e in events[:-1]))
