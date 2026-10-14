import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from historical_training.value_successor_evaluator import ensure_successor,allowed_output
from laya_cell.integrity import ContractError

class SuccessorBoundaryTests(unittest.TestCase):
 def test_cannot_target_original_root_or_arbitrary_output(self):
  with self.assertRaises(ValueError):allowed_output('/tmp/value-evaluator-successor-foo')
  with self.assertRaises(ValueError):allowed_output('/home/alex/.local/state/heptatrader/execution')
 def setup_fixture(self,temp):
  src=Path(temp)/'original';p=src/'plans'/'p';p.mkdir(parents=True)
  (p/'plan.json').write_text(json.dumps({'plan_id':'p','input_end_ms':1000}))
  (p/'registration.json').write_text('{}')
  (p/'predictions').mkdir()
  c={'forward_root':str(src)}
  checksum=hashlib.sha256((p/'plan.json').read_bytes()).hexdigest()
  return src,p,c,checksum
 def test_read_only_prediction_link_and_origin_integrity(self):
  with tempfile.TemporaryDirectory() as temp:
   src,p,c,checksum=self.setup_fixture(temp)
   target=Path(temp)/'successor'
   with patch('historical_training.value_successor_evaluator.ff.validate_registration',return_value=None):
    plan,origin=ensure_successor(c,target,checksum)
    self.assertEqual(plan['plan_id'],'p')
    self.assertEqual(origin,p/'plan.json')
    self.assertTrue((target/'plans/p/predictions').is_symlink())
    self.assertTrue((p/'predictions').exists())
    ensure_successor(c,target,checksum)
    (target/'plans/p/plan.json').write_text('{"changed":true}')
    with self.assertRaisesRegex(ContractError,'SUCCESSOR_PLAN_MISMATCH'):ensure_successor(c,target,checksum)
 def test_changed_original_frozen_plan_denied(self):
  with tempfile.TemporaryDirectory() as temp:
   src,p,c,checksum=self.setup_fixture(temp)
   (p/'plan.json').write_text('{"plan_id":"different"}')
   with self.assertRaisesRegex(ContractError,'ORIGINAL_PLAN_HASH_CHANGED'):
    ensure_successor(c,Path(temp)/'next',checksum)
if __name__=='__main__':unittest.main()
