"""Actual-read-only recorded source with disposable copies to test tampering.

Never writes to the running shadow window or touches execution.
"""
from __future__ import annotations
import json,shutil,tempfile,unittest,time
from pathlib import Path
from paired_price.independent_audit import inspect
from laya_cell.integrity import ContractError,read_json

class IndependentReviewTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  h=Path.home();e=h/'.local/state/heptatrader/operator-evidence/paired-gain-netvalue-20261008'
  installed=read_json(e/'paired-price-install.json')
  cls.original=Path(installed['state'])
  cls.config=read_json(Path(installed['config']))
  if not (cls.original/'outcome-000.json').exists():
   raise RuntimeError('FIRST_REAL_OUTCOME_NOT_YET_MATURE_CANNOT_RUN_REAL_REPLAY_TEST')
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.config={**self.config,'shadow_root':str(self.root)}
  for name in ('plan.json','prediction-000.json','outcome-000.json'):
   shutil.copyfile(self.original/name,self.root/name)
 def mutate(self,name,changes):
  p=self.root/name;v=json.loads(p.read_bytes());v.update(changes);p.write_text(json.dumps(v)+'\n')
 def test_actual_first_slot_replays_without_forging_later_outcomes(self):
  report=inspect(self.config)
  self.assertGreaterEqual(report['observed'],1)
  self.assertEqual(report['state'],'WAIT_FIXED_OUTCOMES_NO_ADVANTAGE_CLAIM')
  self.assertEqual(report['slot_records'][0]['status'],'OBSERVED')
  self.assertFalse(report['canonical_owner_admitted'])
 def test_changed_stored_gate_brier_refused(self):
  p=self.root/'outcome-000.json';v=json.loads(p.read_bytes());v['brier']['gate']+=.1;p.write_text(json.dumps(v))
  with self.assertRaisesRegex(ContractError,'BRIER_DISAGREES'):inspect(self.config)
 def test_future_plan_changed_refused(self):
  p=self.root/'plan.json';v=json.loads(p.read_bytes());v['start_ms']+=10;p.write_text(json.dumps(v))
  with self.assertRaisesRegex(ContractError,'TRIAL_PLAN_ID'):inspect(self.config)
 def test_candidate_model_mismatch_refused(self):
  p=self.root/'plan.json';v=json.loads(p.read_bytes());v['candidate_model_sha256']='0'*64
  from laya_cell.integrity import digest
  v['plan_id']=digest({k:x for k,x in v.items() if k!='plan_id'})
  p.write_text(json.dumps(v))
  with self.assertRaisesRegex(ContractError,'AUDIT_MODEL_BYTES'):inspect(self.config)
 def test_prediction_commit_changed_refused(self):
  p=self.root/'prediction-000.json';v=json.loads(p.read_bytes());v['arms']['gate']=[1./7]*7
  p.write_text(json.dumps(v))
  with self.assertRaisesRegex(ContractError,'OBSERVATION_IDENTITY'):inspect(self.config)
 def test_missing_result_remains_unscored(self):
  (self.root/'outcome-000.json').unlink()
  r=inspect(self.config)
  self.assertEqual(r['observed'],0)
  self.assertEqual(r['slot_records'][0]['status'],'EVALUATOR_RECORD_PENDING')
 def test_removed_forecast_is_not_a_successful_sample(self):
  (self.root/'prediction-000.json').unlink()
  r=inspect(self.config)
  self.assertEqual(r['observed'],0)
  self.assertNotEqual(r['slot_records'][0]['status'],'OBSERVED')
 def test_prefix_substitution_refused(self):
  p=self.root/'prediction-000.json';v=json.loads(p.read_bytes());v['source_prefixes'][0]['sha256']='0'*64
  p.write_text(json.dumps(v))
  with self.assertRaisesRegex(ContractError,'SOURCE_PREFIX_REWRITTEN'):inspect(self.config)

if __name__=='__main__':unittest.main(verbosity=2)
