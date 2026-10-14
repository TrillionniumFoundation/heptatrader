import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from laya_cell.financial_forward_predict import publication_deadline, publish_prediction
from laya_cell.integrity import ContractError
from laya_cell.resident_store import write_once

class DeadlineTest(unittest.TestCase):
 def setUp(self):self.plan={'plan_id':'p','candidate_id':'c','input_end_ms':200000};self.request={'request_id':'r','started_at_ms':1000}
 def test_last_millisecond_admitted(self):self.assertEqual(publication_deadline(self.plan,self.request,60999),60999)
 def test_equality_expired(self):
  with self.assertRaises(ContractError):publication_deadline(self.plan,self.request,61000)
 def test_original_observed_67_seconds_refused(self):
  with self.assertRaises(ContractError):publication_deadline(self.plan,self.request,68206)
 def test_fixed_window_can_expire_earlier(self):
  with self.assertRaises(ContractError):publication_deadline({**self.plan,'input_end_ms':3000},self.request,3000)
 def test_clock_reversal(self):
  with self.assertRaises(ContractError):publication_deadline(self.plan,self.request,3000,3001)
 def test_invalid_boolean_time(self):
  with self.assertRaises(ContractError):publication_deadline(self.plan,self.request,True)
 def publish(self,times,sign_at):
  def sign(c,r,v):return {**v,'role_witness':{'signed_at_ms':sign_at},'signature':'SYNTHETIC_NO_REAL_KEY'}
  return patch('laya_cell.financial_forward_predict.time.time',side_effect=times),patch('laya_cell.financial_forward_witness.sign_record',side_effect=sign)
 def test_valid_publication(self):
  with tempfile.TemporaryDirectory() as d:
   a,b=self.publish([2.,2.1,2.3],2200)
   with a,b:publish_prediction({},self.plan,self.request,Path(d),{'computed_at_ms':1900})
   self.assertTrue((Path(d)/'commit.json').exists())
 def test_expired_before_payload_no_output(self):
  with tempfile.TemporaryDirectory() as d:
   a,b=self.publish([68.206],68206)
   with a,b,self.assertRaises(ContractError):publish_prediction({},self.plan,self.request,Path(d),{'computed_at_ms':68000})
   self.assertFalse((Path(d)/'prediction.json').exists());self.assertFalse((Path(d)/'commit.json').exists())
 def test_payload_fsync_delay_no_commit(self):
  with tempfile.TemporaryDirectory() as d:
   a,b=self.publish([60.999,61.001],61001)
   with a,b,self.assertRaises(ContractError):publish_prediction({},self.plan,self.request,Path(d),{'computed_at_ms':60000})
   self.assertTrue((Path(d)/'prediction.json').exists());self.assertFalse((Path(d)/'commit.json').exists())
 def test_signing_delay_no_commit(self):
  with tempfile.TemporaryDirectory() as d:
   a,b=self.publish([60.,60.1],62000)
   with a,b,self.assertRaises(ContractError):publish_prediction({},self.plan,self.request,Path(d),{'computed_at_ms':59000})
   self.assertFalse((Path(d)/'commit.json').exists())
 def test_post_sign_clock_delay_no_commit(self):
  with tempfile.TemporaryDirectory() as d:
   a,b=self.publish([60.,60.1,62.],60200)
   with a,b,self.assertRaises(ContractError):publish_prediction({},self.plan,self.request,Path(d),{'computed_at_ms':59000})
   self.assertFalse((Path(d)/'commit.json').exists())
if __name__=='__main__':unittest.main()
