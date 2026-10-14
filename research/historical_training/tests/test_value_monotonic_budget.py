import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from laya_cell.financial_forward_predict import PublicationBudget, publish_prediction, Predictor
from laya_cell.integrity import ContractError
from historical_training.history_farm_readiness import FarmReadiness
from historical_training.common import EvidenceError

class BudgetTests(unittest.TestCase):
 def setUp(self):
  self.plan={'plan_id':'p','candidate_id':'c','input_start_ms':1,'input_end_ms':200000,'behavior_scope':{'behavior_model_id':'m','producer_manifest_sha256':'x'}}
  self.req={'request_id':'r','started_at_ms':1000,'model_id':'m','position_at_request':0}
 def test_monotonic_expiry_when_wall_stands_still(self):
  with patch('laya_cell.financial_forward_predict.time.monotonic_ns',side_effect=[0,1_000_000_000]):
   b=PublicationBudget(self.plan,self.req,60000)
   with self.assertRaisesRegex(ContractError,'MONOTONIC'):b.check(self.plan,self.req,60000)
 def test_last_nanosecond_allowed(self):
  with patch('laya_cell.financial_forward_predict.time.monotonic_ns',side_effect=[0,999_999_999]):
   b=PublicationBudget(self.plan,self.req,60000);self.assertEqual(b.check(self.plan,self.req,60000),60000)
 def test_wall_rollback_after_admission(self):
  with patch('laya_cell.financial_forward_predict.time.monotonic_ns',return_value=0):
   b=PublicationBudget(self.plan,self.req,60000)
   with self.assertRaisesRegex(ContractError,'CLOCK_REVERSED'):b.check(self.plan,self.req,59000)
 def test_clock_can_never_revive_expired_budget(self):
  with patch('laya_cell.financial_forward_predict.time.monotonic_ns',side_effect=[0,2_000_000_000,0]):
   b=PublicationBudget(self.plan,self.req,60000)
   with self.assertRaises(ContractError):b.check(self.plan,self.req,60000)
   with self.assertRaisesRegex(ContractError,'ALREADY_FAILED'):b.check(self.plan,self.req,60000)
 def test_budget_cannot_transfer_to_other_request(self):
  with patch('laya_cell.financial_forward_predict.time.monotonic_ns',return_value=0):
   b=PublicationBudget(self.plan,self.req,2000)
   with self.assertRaisesRegex(ContractError,'SCOPE_CHANGED'):b.check(self.plan,{**self.req,'request_id':'other'},2000)
 def test_fixed_window_budget_shorter_than_request_ttl(self):
  p={**self.plan,'input_end_ms':2500}
  with patch('laya_cell.financial_forward_predict.time.monotonic_ns',side_effect=[0,500_000_000]):
   b=PublicationBudget(p,self.req,2000)
   with self.assertRaises(ContractError):b.check(p,self.req,2200)
 def test_reversed_monotonic_rejected(self):
  with patch('laya_cell.financial_forward_predict.time.monotonic_ns',side_effect=[100,99]):
   b=PublicationBudget(self.plan,self.req,2000)
   with self.assertRaisesRegex(ContractError,'CLOCK_REVERSED'):b.check(self.plan,self.req,2000)
 def test_real_negative_control_rollback_no_commit(self):
  with tempfile.TemporaryDirectory() as root:
   def sign(c,r,v):return {**v,'role_witness':{'signed_at_ms':60250}}
   with patch('laya_cell.financial_forward_predict.time.time',side_effect=[60.9,60.2,60.4]),patch('laya_cell.financial_forward_predict.time.monotonic_ns',return_value=0),patch('laya_cell.financial_forward_witness.sign_record',side_effect=sign),self.assertRaisesRegex(ContractError,'CLOCK_REVERSED'):
    publish_prediction({},self.plan,self.req,Path(root),{'computed_at_ms':60000})
   self.assertFalse((Path(root)/'commit.json').exists())
 def test_budget_created_before_load_at_exact_expiry(self):
  with tempfile.TemporaryDirectory() as root:
   worker=Predictor({'forward_root':root})
   with patch('laya_cell.financial_forward_predict.time.time',return_value=61.),patch.object(worker,'load') as load,patch('laya_cell.financial_forward_predict.pin') as pin:
    result=worker.predict(self.plan,{'request':self.req,'producer_manifest_sha256':'x'})
   self.assertIsNone(result);load.assert_not_called();pin.assert_not_called()
 def test_preparation_delay_cannot_reuse_stale_entry_clock(self):
  with tempfile.TemporaryDirectory() as root:
   worker=Predictor({'forward_root':root})
   with patch('laya_cell.financial_forward_predict.time.time',side_effect=[60.,62.,62.]),patch.object(worker,'load') as load,patch('laya_cell.financial_forward_predict.pin') as pin:
    result=worker.predict(self.plan,{'request':self.req,'producer_manifest_sha256':'x'})
   self.assertIsNone(result);load.assert_not_called();pin.assert_not_called()
 def test_publication_does_not_reset_existing_budget(self):
  with tempfile.TemporaryDirectory() as root,patch('laya_cell.financial_forward_predict.time.monotonic_ns',side_effect=[0,2_000_000_000]),patch('laya_cell.financial_forward_predict.time.time',return_value=60.):
   b=PublicationBudget(self.plan,self.req,60000)
   with self.assertRaisesRegex(ContractError,'MONOTONIC'):
    publish_prediction({},self.plan,self.req,Path(root),{'computed_at_ms':60000},b)
   self.assertFalse((Path(root)/'prediction.json').exists())

class FarmDeadlineTests(unittest.TestCase):
 def run_case(self,observed):
  c=[0.];f=FarmReadiness(clock=lambda:c[0],wall=lambda:100.)
  def sleep(_):c[0]=observed;f.notice(-1,2106,'CONNECTED:cashhmds')
  return f.wait(1,sleep=sleep)
 def test_late_ready_is_not_success(self):
  with self.assertRaises(EvidenceError):self.run_case(2.)
 def test_ready_at_deadline_is_not_success(self):
  with self.assertRaises(EvidenceError):self.run_case(1.)
 def test_ready_before_deadline(self):self.assertEqual(self.run_case(.999),'CONNECTED')
 def test_zero_is_one_nonblocking_observation(self):
  f=FarmReadiness();f.notice(-1,2107,'IDLE:cashhmds');self.assertEqual(f.wait(0),'INACTIVE')
if __name__=='__main__':unittest.main()
