"""Pin the first real admission refusal without changing original slot verdict.

No broker, GPU, Value-work modification, or installation in live Laya.
"""
import json,tempfile,time,unittest
from unittest.mock import patch
from pathlib import Path
from historical_training.resident_resource_guard import guarded_evaluator_class
from laya_cell.integrity import ContractError,digest,sha_file
from laya_cell.promotion import rule_for_trial
from laya_cell.promotion_power import PROTOCOL
from laya_cell.resident_evaluate import Evaluator


class Gate:
 def __init__(self,allow=False):
  self.allow=allow;self.last_admission={'reasons':['VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST']}
  self.attempts=[];self.released=0
 def acquire(self,subject):self.attempts.append(subject);return self.allow
 def release(self):self.released+=1


class AdmissionReceiptTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
  self.root=Path(self.t.name);self.folder=self.root/'evaluation'/'candidate';self.folder.mkdir(parents=True)
  self.period=self.root/'period.json';self.period.write_text('{"execution_domain":"PAPER"}')
  runtime=self.root/'runtime';runtime.mkdir()
  self.pinned=runtime/'pinned.py';self.pinned.write_text('# frozen source')
  self.runtime=runtime
  now=int(time.time()*1000);self.now=now
  rule=rule_for_trial(39,PROTOCOL)
  self.plan={'schema':'heptatrader.laya-resident-future-plan.v1','candidate_id':'frozen',
    'trial':39,'rule':rule,'start_ms':now-121000,'created_at_ms':now-241000,
    'outcome_end_ms':now+rule['slots']*rule['slot_spacing_ms'],
    'scope':sha_file(self.period),'no_refit':True,
    'evaluation_runtime_sha256':{'pinned.py':sha_file(self.pinned)}}
  self.plan['plan_id']=digest(self.plan)
  self.path=self.folder/'plan.json'
  self.path.write_text(json.dumps(self.plan))
 def record(self,slot=0):
  return self.folder/('admission-denial-%03d.json'%slot)
 def guarded(self,gate):
  class FakeBase(Evaluator):
   def launch(self,path,slot):self.launched.append((str(path),slot))
  cls=guarded_evaluator_class(FakeBase,gate)
  obj=cls.__new__(cls);obj.tasks={};obj.launched=[]
  return obj
 def active_evaluator(self):
  obj=Evaluator.__new__(Evaluator)
  config={'evaluation_directory':str(self.folder.parent),'period_path':str(self.period),
          'period_sha256':sha_file(self.period),
          'runtime_directory':str(self.runtime),'promotion_protocol':PROTOCOL}
  obj.path=self.root/'config.json';obj.path.write_text(json.dumps(config))
  obj.root=self.folder.parent;obj.c=config;obj.tasks={};obj.last_log_ms=int(time.time()*1000)
  return obj
 def test_real_priority_denial_is_not_a_prediction_failure(self):
  gate=Gate();obj=self.guarded(gate);obj.launch(self.path,0)
  receipt=json.loads(self.record().read_text())
  self.assertEqual(receipt['plan_id'],self.plan['plan_id'])
  self.assertEqual(receipt['reason_codes'],['VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST'])
  self.assertTrue(receipt['first_denial_only'])
  self.assertTrue(receipt['possible_retry_until_frozen_input_end'])
  self.assertEqual(len(gate.attempts),1);self.assertFalse(obj.launched)
  self.assertFalse((self.folder/'failure-000.json').exists())
  self.assertFalse((self.folder/'outcome-000.json').exists())
 def test_first_reason_is_stable_on_subsequent_denial(self):
  gate=Gate();obj=self.guarded(gate);obj.launch(self.path,0)
  original=self.record().read_bytes()
  gate.last_admission={'reasons':['HOST_FULL_MEMORY_PSI_PRESSURE']}
  obj.launch(self.path,0)
  self.assertEqual(original,self.record().read_bytes());self.assertEqual(len(gate.attempts),2)
 def test_denied_guard_can_later_admit_with_original_deadline_unchanged(self):
  gate=Gate();obj=self.guarded(gate);obj.launch(self.path,0)
  original_plan=self.path.read_bytes();receipt=self.record().read_bytes()
  gate.allow=True;obj.launch(self.path,0)
  self.assertEqual(len(obj.launched),1)
  self.assertEqual(original_plan,self.path.read_bytes())
  self.assertEqual(receipt,self.record().read_bytes())
 def test_new_admitted_worker_does_not_create_denial_receipt(self):
  obj=self.guarded(Gate(allow=True));obj.launch(self.path,0)
  self.assertFalse(self.record().exists());self.assertEqual(len(obj.launched),1)
 def test_existing_child_busy_is_recorded_without_attempting_new_guard(self):
  gate=Gate();obj=self.guarded(gate);obj.tasks={'prior':'RUNNING'}
  obj.launch(self.path,0)
  self.assertFalse(gate.attempts)
  self.assertEqual(json.loads(self.record().read_text())['reason_codes'],
                   ['PRIOR_EVALUATOR_CHILD_ACTIVE'])
 def test_unattributable_denial_still_fails_closed_not_silent(self):
  gate=Gate();gate.last_admission=None
  self.guarded(gate).launch(self.path,0)
  self.assertEqual(json.loads(self.record().read_text())['reason_codes'],
                   ['RESOURCE_ADMISSION_UNCLASSIFIED'])
 def test_reject_invalid_reason_or_tampered_frozen_plan(self):
  obj=self.guarded(Gate())
  for reason in ([],[''],[None],['valid']*33):
   with self.assertRaisesRegex(ContractError,'PREWARM_DENIAL_IDENTITY_OR_REASONS'):
    obj.record_admission_deferral(self.path,0,reason)
  invalid={**self.plan,'candidate_id':'changed'}
  self.path.write_text(json.dumps(invalid))
  with self.assertRaisesRegex(ContractError,'PREWARM_DENIAL_IDENTITY_OR_REASONS'):
   obj.record_admission_deferral(self.path,0,['SOURCE_INVALID'])
 def test_long_real_os_error_is_bounded_not_a_supervisor_crash(self):
  reason="RESOURCE_OR_SOURCE_UNAVAILABLE:"+("long_original_path_"*32)
  gate=Gate();gate.last_admission={'reasons':[reason]}
  self.guarded(gate).launch(self.path,0)
  record=json.loads(self.record().read_text())
  self.assertTrue(record['reason_truncated'])
  self.assertEqual(record['reason_codes'][0],reason[:160])
  self.assertFalse((self.folder/'failure-000.json').exists())
 def test_immutable_first_denial_tampering_rejected(self):
  obj=self.guarded(Gate());obj.launch(self.path,0)
  r=json.loads(self.record().read_text());r['plan_id']='corrupted'
  self.record().write_text(json.dumps(r))
  with self.assertRaisesRegex(ContractError,'PREWARM_DENIAL_RECEIPT_CHANGED'):
   obj.launch(self.path,0)
 def test_expired_slot_retains_frozen_censor_with_denial_provenance(self):
  obj=self.guarded(Gate());obj.launch(self.path,0)
  result=self.active_evaluator().step()
  out=json.loads((self.folder/'outcome-000.json').read_text())
  self.assertEqual(out['status'],'CENSORED')
  self.assertEqual(out['reason'],'NO_PREDICTION_IN_FIXED_SLOT')
  self.assertEqual(out['first_admission_denial_reasons'],
                   ['VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST'])
  self.assertEqual(out['first_admission_denial_sha256'],sha_file(self.record()))
  self.assertTrue(out['not_proof_of_every_denial_attempt'])
  self.assertFalse(result['promotion_performed'])
  self.assertEqual(result['counts'],{'PENDING':1})
  self.assertFalse((self.folder/'prediction-000.json').exists())
 def test_missing_forecast_without_guard_receipt_remains_original_reason(self):
  self.active_evaluator().step()
  out=json.loads((self.folder/'outcome-000.json').read_text())
  self.assertEqual(out['status'],'CENSORED')
  self.assertEqual(out['reason'],'NO_PREDICTION_IN_FIXED_SLOT')
  self.assertNotIn('first_admission_denial_sha256',out)

 def test_terminal_assessment_updates_qualification_and_progress(self):
     evaluator = self.active_evaluator()
     terminal = {'candidate_id': self.plan['candidate_id'], 'plan_id': self.plan['plan_id'], 'state': 'REJECTED', 'observed': 0, 'censored': 1}
     with patch('laya_cell.resident_evaluate.assess', return_value=terminal) as assess:
         evaluator.step()
     assess.assert_called_once()
     qualification = json.loads((self.folder / 'qualification.json').read_text())
     progress = json.loads((self.folder / 'progress.json').read_text())
     self.assertEqual(qualification, terminal)
     self.assertEqual(progress, qualification)

 def test_closed_qualification_preserves_bytes_and_skips_source_work(self):
     evaluator = self.active_evaluator()
     terminal = {'candidate_id': self.plan['candidate_id'], 'plan_id': self.plan['plan_id'], 'state': 'REJECTED', 'observed': 0, 'censored': 1}
     stale_progress = {'candidate_id': self.plan['candidate_id'], 'plan_id': self.plan['plan_id'], 'state': 'PENDING', 'observed': 0, 'censored': 0}
     (self.folder / 'qualification.json').write_text(json.dumps(terminal, indent=2) + '\n', encoding='utf-8')
     (self.folder / 'progress.json').write_text(json.dumps(stale_progress, separators=(',', ':')) + '\n', encoding='utf-8')

     def contents():
         return {str(path.relative_to(self.folder)): path.read_bytes() for path in self.folder.rglob('*') if path.is_file()}
     before = contents()
     with patch('laya_cell.resident_evaluate.assess', side_effect=AssertionError('closed plan must not be reassessed')) as assess, patch('laya_cell.resident_evaluate.read_quotes', side_effect=AssertionError('closed plan must not read quotes')) as read_quotes, patch('laya_cell.resident_source_policy.read_for_pending_outcomes', side_effect=AssertionError('closed plan must not read bounded sources')) as read_pending:
         evaluator.step()
     assess.assert_not_called()
     read_quotes.assert_not_called()
     read_pending.assert_not_called()
     self.assertEqual(contents(), before)

if __name__=='__main__':unittest.main()
