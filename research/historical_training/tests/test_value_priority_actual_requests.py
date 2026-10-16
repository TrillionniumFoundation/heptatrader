"""Actual requests reserve time; an estimated future cadence is not a job."""
import unittest
from unittest.mock import patch
from historical_training import value_priority_admission as v

class ActualRequestPriority(unittest.TestCase):
 def policy(self):return {'schema':'heptatrader.original-value-priority.v2','plan_path':'/test/plan.json','plan_sha256':'a'*64}
 def check(self,rows,role='trainer',now=1000000):
  plan={'plan_id':'b'*64,'input_start_ms':0,'input_end_ms':2000000}
  with patch.object(v,'_load_pinned',return_value=(plan,None)),patch.object(v,'_recent_requests',return_value=rows):
   return v.decision(self.policy(),role,now)
 def test_periodic_nonflat_requests_never_reserve_phantom_future_job(self):
  rows=[(500000+i*120000,False) for i in range(5)]
  for role in ('trainer','evaluator'):
   for now in range(980000,1100000,10000):
    self.assertFalse(self.check(rows,role,now)[0])
 def test_fresh_flat_request_reserves_both_roles(self):
  for role in ('trainer','evaluator'):
   held,reason,_=self.check([(990000,True)],role)
   self.assertTrue(held);self.assertEqual(reason,'VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST')
 def test_deadline_expiry_releases_reservation_without_backdating(self):
  self.assertTrue(self.check([(940000,True)])[0])
  self.assertFalse(self.check([(939999,True)])[0])
 def test_earlier_fresh_flat_is_not_hidden_by_newer_nonflat_request(self):
  self.assertTrue(self.check([(970000,True),(990000,False)])[0])
 def test_stale_source_does_not_forge_a_future_reservation(self):
  self.assertFalse(self.check([(700000,True)])[0])
 def test_future_source_is_rejected(self):
  with self.assertRaisesRegex(ValueError,'FUTURE'):self.check([(1000001,True)])
 def test_no_requests_has_no_priority_reservation(self):self.assertFalse(self.check([])[0])
 def test_frozen_v1_policy_is_still_accepted(self):
  p=self.policy();p['schema']='heptatrader.original-value-priority.v1';self.assertEqual(v.verify_policy(p),p)

if __name__=='__main__':unittest.main()
