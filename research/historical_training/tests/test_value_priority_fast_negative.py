import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from historical_training.resident_resource_guard import Gate

class FastNegativeTests(unittest.TestCase):
 def config(self,root):
  return {'state_root':str(root),'value_priority':{'schema':'heptatrader.original-value-priority.v1','plan_path':str(root/'p.json'),'plan_sha256':'a'*64}}
 def test_denial_skips_original_expensive_source_probe_and_flock(self):
  with tempfile.TemporaryDirectory() as t:
   g=Gate(self.config(Path(t)),'trainer')
   g.check=lambda:self.fail('expensive_refresh')
   g.lease.acquire=lambda:self.fail('unexpected_lock')
   with patch('historical_training.value_priority_admission.decision',return_value=(True,'VALUE_PRIORITY_DEFER_NEW_LONG_TRAINING',{'source':'PINNED_ORIGINAL'})) as f:
    self.assertFalse(g.acquire({'role':'trainer'}))
    self.assertEqual(f.call_count,1)
   self.assertTrue(g.last_admission['original_resource_and_quote_refresh_skipped_for_denial'])
   self.assertFalse(g.last_admission['admit_new_worker'])
   self.assertEqual(g.last_admission['broker_orders_sent'],0)
 def test_permitted_work_still_requires_original_checks_twice(self):
  with tempfile.TemporaryDirectory() as t:
   g=Gate(self.config(Path(t)),'evaluator')
   called=[]
   def check():
    called.append('original')
    return {'admit_new_worker':True,'reasons':[]}
   g.check=check
   with patch('historical_training.value_priority_admission.decision',return_value=(False,'NO_PRIORITY',{})) as f:
    try:
     self.assertTrue(g.acquire({'plan':'test'}))
     self.assertEqual(len(called),2)
     self.assertEqual(f.call_count,3)
    finally:g.release()
 def test_manual_hold_not_overridden(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);h=root/'hold';h.write_text('')
   cfg=self.config(root);cfg['manual_hold_path']=str(h)
   g=Gate(cfg,'trainer');seen=[]
   def checked():
    seen.append('original')
    return {'admit_new_worker':False,'reasons':['OPERATOR_RESOURCE_HOLD']}
   g.check=checked
   with patch('historical_training.value_priority_admission.decision') as f:
    self.assertFalse(g.acquire({'role':'trainer'}))
    f.assert_not_called()
   self.assertEqual(seen,['original'])
   self.assertEqual(g.last_admission['reasons'],['OPERATOR_RESOURCE_HOLD'])
 def test_priority_pin_error_refuses_without_full_probe(self):
  with tempfile.TemporaryDirectory() as t:
   g=Gate(self.config(Path(t)),'evaluator')
   g.check=lambda:self.fail('unverified_source_went_to_live_refresh')
   with patch('historical_training.value_priority_admission.decision',side_effect=ValueError('PIN_CHANGED')):
    self.assertFalse(g.acquire({'plan':'test'}))
   self.assertEqual(g.last_admission['reasons'],['VALUE_PRIORITY_UNVERIFIED_SOURCE'])
