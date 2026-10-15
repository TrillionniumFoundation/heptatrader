import tempfile,unittest
from pathlib import Path
from historical_training.value_prospective_coverage import classify,summarize

class CoverageTests(unittest.TestCase):
 def test_no_backdating_and_matured_guard(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t);(r/'commit.json').write_text('{}')
   req={'started_at_ms':100}
   self.assertEqual(classify(req,r,59990,lambda:{'committed_at_ms':500})[0],'IMMATURE')
   self.assertEqual(classify(req,r,60100,lambda:{'committed_at_ms':60100})[0],'LATE_OR_INVALID_COMMIT')
   self.assertEqual(classify(req,r,60100,lambda:{'committed_at_ms':3500})[0],'VERIFIED_PREDEADLINE')
 def test_missed_immutable(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t)
   (r/'failure.json').write_text('{"reason":"REQUEST_ALREADY_OLD_NO_BACKDATING"}')
   self.assertEqual(classify({'started_at_ms':100},r,61000)[0],'IMMUTABLE_DEADLINE_MISS')
 def test_complete_denominator_includes_misses(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);plan={'plan_id':'p','input_start_ms':1,'input_end_ms':300000}
   xs={'a'*32:{'request':{'started_at_ms':100,'position_at_request':0}},
       'b'*32:{'request':{'started_at_ms':1000,'position_at_request':0}},
       'c'*32:{'request':{'started_at_ms':1000,'position_at_request':9}}}
   d=root/'plans/p/predictions';(d/('a'*32)).mkdir(parents=True)
   (d/('b'*32)).mkdir(parents=True)
   (d/('a'*32)/'commit.json').write_text('{}')
   (d/('b'*32)/'failure.json').write_text('{"reason":"REQUEST_ALREADY_OLD_NO_BACKDATING"}')
   v=summarize(xs,root,plan,62000,0,lambda rid:{'committed_at_ms':500})
   self.assertEqual(v['matured_eligible_flat_original_requests'],2)
   self.assertEqual(v['verified_predeadline_commits'],1)
   self.assertEqual(v['coverage_fraction'],.5)
   self.assertTrue(v['posthoc_not_efficacy'])
if __name__=='__main__':unittest.main()
