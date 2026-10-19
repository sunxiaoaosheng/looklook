import importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from state import Store,fingerprint
import settings
class StateTests(unittest.TestCase):
 def test_lease_and_fingerprint(self):
  with tempfile.TemporaryDirectory() as t:
   a=Store(t);b=Store(t)
   self.assertTrue(a.lease('heavy','a'));self.assertFalse(b.lease('heavy','b'));a.release('heavy','a');self.assertTrue(b.lease('heavy','b'))
   a.put('jobs','k',{'status':'failed','answer':'保留'});self.assertEqual(b.get('jobs','k')['answer'],'保留');self.assertEqual((Path(t)/'state.sqlite3').stat().st_mode&0o777,0o600)
   p=Path(t)/'video';p.write_bytes(b'one');one=fingerprint(p);p.write_bytes(b'two');self.assertNotEqual(one,fingerprint(p));a.close();b.close()
 def test_precedence(self):
  with patch.dict(os.environ,{'LOOKLOOK_STORAGE_ROOT':'env'}),patch.object(settings,'load',return_value={'storage_root':'file'}):
   self.assertEqual(settings.value('storage_root','default'),'env');self.assertEqual(settings.value('storage_root',profile={'storage_root':'profile'}),'profile');self.assertEqual(settings.value('storage_root',explicit='explicit',profile={'storage_root':'profile'}),'explicit')
 def test_prune_expired_results(self):
  with tempfile.TemporaryDirectory() as t:
   a=Store(t);a.put('jobs','old',{'status':'completed','answer':'正文'});a.db.execute('UPDATE records SET updated=0');a.prune();self.assertIsNone(a.get('jobs','old'));a.close()
