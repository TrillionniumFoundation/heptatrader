import tempfile,unittest,json
from pathlib import Path
from historical_training.merge_ib_incremental import merge
from historical_training.common import EvidenceError

class MergeTests(unittest.TestCase):
 def make(self,root,label,times,start,end):
  p=root/label;p.mkdir()
  bar=[{'time':t,'bid':{'open':'1','high':'1','low':'1','close':'1'},
        'ask':{'open':'2','high':'2','low':'2','close':'2'},
        'midpoint':{'open':'1.5','high':'1.5','low':'1.5','close':'1.5'}} for t in times]
  c={'requested_start_utc':start,'requested_end_utc':end,'bar_count':len(bar),
   'full_requested_coverage_proven':True,'scheduled_missing_count':0,
   'unpaired_timestamps':0,'actual_first_utc':start,'actual_last_utc':end}
  d={'schema':'heptatrader.historical-paired-bars.v1','bars':bar,'coverage':c,
     'trade_authority':False,'actual_execution_rewards':0}
  (p/'paired-bars.json').write_text(json.dumps(d));(p/'coverage.json').write_text(json.dumps(c))
  return p
 def test_exact_overlap_append(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);a=self.make(root,'a',[300,600,900],'2026-10-01T00:00:00+00:00','2026-10-01T01:00:00+00:00')
   b=self.make(root,'b',[600,900,1200],'2026-10-01T00:30:00+00:00','2026-10-01T02:00:00+00:00')
   d,p=merge(a,b)
   self.assertEqual(len(d['bars']),4);self.assertEqual(p['overlap_bars_identical'],2)
   self.assertEqual(p['new_bars_from_increment'],1)
 def test_exact_backward_prepend(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   a=self.make(root,'a',[900,1200,1500],'2026-10-01T01:00:00+00:00','2026-10-01T03:00:00+00:00')
   b=self.make(root,'b',[300,600,900,1200],'2026-10-01T00:00:00+00:00','2026-10-01T02:00:00+00:00')
   d,pr=merge(a,b)
   self.assertEqual(len(d['bars']),5)
   self.assertEqual(pr['overlap_bars_identical'],2)
   self.assertEqual(pr['merge_direction'],'BACKWARD_PREPEND')
   self.assertEqual(pr['new_bars_from_increment'],2)
 def test_revised_overlap_refused(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);a=self.make(root,'a',[300,600,900],'2026-10-01T00:00:00+00:00','2026-10-01T01:00:00+00:00')
   b=self.make(root,'b',[600,900,1200],'2026-10-01T00:30:00+00:00','2026-10-01T02:00:00+00:00')
   p=b/'paired-bars.json';d=json.loads(p.read_text());d['bars'][0]['bid']['close']='1.1';p.write_text(json.dumps(d))
   with self.assertRaisesRegex(EvidenceError,'REVISED'):merge(a,b)
if __name__=='__main__':unittest.main()
