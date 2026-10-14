import json,os,tempfile,unittest
from pathlib import Path
from historical_training.value_request_trigger import Watcher

class TailTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.root=Path(self.tmp.name);self.root.chmod(0o700)
  self.p=self.root/'1.jsonl';self.p.write_text('')
  self.p.chmod(0o600);self.now=100000
 def tearDown(self):self.tmp.cleanup()
 def append(self,**kw):
  r={'kind':'REQUEST','written_at_ms':self.now,'data':{'request_id':'id','started_at_ms':self.now-1000,'position_at_request':0}}
  r.update(kw)
  with self.p.open('a') as f:f.write(json.dumps(r)+'\n')
 def test_old_records_skipped_and_fresh_once(self):
  self.append();w=Watcher(self.root,lambda:self.now)
  self.assertEqual(w.poll(),[])
  self.append(data={'request_id':'new','started_at_ms':self.now-1000,'position_at_request':0})
  self.assertEqual([x['request_id'] for x in w.poll()],['new'])
  self.assertEqual(w.poll(),[])
 def test_old_or_nonflat_not_triggered(self):
  w=Watcher(self.root,lambda:self.now)
  self.append(data={'request_id':'old','started_at_ms':self.now-70000,'position_at_request':0})
  self.append(data={'request_id':'pos','started_at_ms':self.now-1,'position_at_request':10})
  self.assertEqual(w.poll(),[])
 def test_new_segment_and_file_rotation(self):
  w=Watcher(self.root,lambda:self.now)
  q=self.root/'2.jsonl';q.write_text('');q.chmod(0o600)
  with q.open('a') as f:f.write(json.dumps({'kind':'REQUEST','written_at_ms':self.now,
   'data':{'request_id':'new','started_at_ms':self.now-1,'position_at_request':0}})+'\n')
  self.assertEqual([x['request_id'] for x in w.poll()],['new'])
  q.unlink();q.write_text('');q.chmod(0o600)
  self.assertEqual(w.poll(),[])
 def test_nonprivate_rejected(self):
  self.p.chmod(0o644)
  with self.assertRaises(ValueError):Watcher(self.root,lambda:self.now)
if __name__=='__main__':unittest.main()
