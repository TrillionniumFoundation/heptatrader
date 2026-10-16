"""Use real frozen rules; a scheduling release is never a trial verdict."""
import json,tempfile,unittest
from pathlib import Path
from laya_cell.integrity import ContractError,digest
from laya_cell.promotion import RULE,PROTOCOL,rule_for_trial,assess
from laya_cell.resident_queue import inspect

class ViableQueueTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.c={'evaluation_directory':str(self.root),'period_sha256':'p'}
 def trial(self,name='trial',censored=0,observed=0,legacy=False):
  d=self.root/name;d.mkdir()
  p={'candidate_id':name,'scope':'p','trial':38,'rule':dict(RULE) if legacy else rule_for_trial(38,PROTOCOL),
     'outcome_end_ms':9999999999999}
  p['plan_id']=digest(p);(d/'plan.json').write_text(json.dumps(p))
  for i in range(censored+observed):
   r={'candidate_id':name,'plan_id':p['plan_id'],'slot':i,'status':'CENSORED' if i<censored else 'OBSERVED'}
   (d/('outcome-%03d.json'%i)).write_text(json.dumps(r))
  return d,p
 def test_empty_queue(self):self.assertEqual(inspect(self.c)['viable_pending_evaluations'],0)
 def test_two_censors_still_occupy_slot(self):
  self.trial(censored=2);self.assertEqual(inspect(self.c)['viable_pending_evaluations'],1)
 def test_three_censors_release_queue_not_original_trial(self):
  d,p=self.trial(censored=3);before={f.name:f.read_bytes() for f in d.iterdir()}
  r=inspect(self.c);self.assertEqual((r['viable_pending_evaluations'],r['pending_original_trials'],r['futile_pending_trials']),(0,1,1))
  self.assertEqual(before,{f.name:f.read_bytes() for f in d.iterdir()});self.assertFalse((d/'qualification.json').exists())
  results=[json.loads(f.read_text()) for f in d.glob('outcome-*.json')]
  self.assertEqual(assess(p,results,1)['state'],'PENDING')
 def test_observed_rows_not_treated_as_censors(self):
  self.trial(observed=5);self.assertEqual(inspect(self.c)['viable_pending_evaluations'],1)
 def test_one_viable_plus_futile_keeps_bound(self):
  self.trial('a',censored=3);self.trial('b',censored=0)
  r=inspect(self.c);self.assertEqual((r['viable_pending_evaluations'],r['pending_original_trials']),(1,2))
 def test_legacy_frozen_rule_is_retained(self):
  self.trial(censored=3,legacy=True);self.assertEqual(inspect(self.c)['futile_pending_trials'],1)
 def test_mutated_plan_fails_closed(self):
  d,p=self.trial();p['trial']=39;(d/'plan.json').write_text(json.dumps(p))
  with self.assertRaisesRegex(ContractError,'PLAN_CHANGED'):inspect(self.c)
 def test_wrong_period_fails_closed(self):
  self.trial();self.c['period_sha256']='q'
  with self.assertRaisesRegex(ContractError,'PERIOD'):inspect(self.c)
 def test_duplicate_slot_fails_closed(self):
  d,p=self.trial(censored=3);(d/'outcome-extra.json').write_bytes((d/'outcome-000.json').read_bytes())
  with self.assertRaisesRegex(ContractError,'FUTILITY'):inspect(self.c)
 def test_foreign_outcome_fails_closed(self):
  d,p=self.trial(censored=3);f=d/'outcome-000.json';r=json.loads(f.read_text());r['plan_id']='other';f.write_text(json.dumps(r))
  with self.assertRaisesRegex(ContractError,'FUTILITY'):inspect(self.c)
 def test_unknown_status_fails_closed(self):
  d,p=self.trial(censored=3);f=d/'outcome-000.json';r=json.loads(f.read_text());r['status']='INVALID';f.write_text(json.dumps(r))
  with self.assertRaisesRegex(ContractError,'FUTILITY'):inspect(self.c)
 def test_closed_trial_never_reopened(self):
  d,p=self.trial();(d/'qualification.json').write_text(json.dumps({'plan_id':p['plan_id'],'candidate_id':p['candidate_id'],'state':'REJECTED'}))
  self.assertEqual(inspect(self.c)['pending_original_trials'],0)
 def test_forged_closed_identity_fails(self):
  d,p=self.trial();(d/'qualification.json').write_text(json.dumps({'plan_id':'wrong','candidate_id':p['candidate_id'],'state':'REJECTED'}))
  with self.assertRaisesRegex(ContractError,'QUALIFICATION'):inspect(self.c)

class QueuePreflightTests(unittest.TestCase):
 def test_viable_queue_does_not_take_global_worker_lease(self):
  from unittest.mock import MagicMock
  from historical_training.resident_resource_guard import guarded_trainer_class
  gate=MagicMock()
  class Base:
   def queue_preflight(self):return {'state':'EVALUATING_BOUNDED_CANDIDATE_QUEUE'}
   def step(self):raise AssertionError('Heavy path must not run')
  result=guarded_trainer_class(Base,gate)().step()
  self.assertEqual(result['state'],'EVALUATING_BOUNDED_CANDIDATE_QUEUE');gate.acquire.assert_not_called()
 def test_futile_only_queue_still_requires_resource_lock(self):
  from unittest.mock import MagicMock
  from historical_training.resident_resource_guard import guarded_trainer_class
  gate=MagicMock();gate.acquire.return_value=True
  class Base:
   def queue_preflight(self):return None
   def step(self):return 'ACTUAL_ADMISSION_PATH'
  self.assertEqual(guarded_trainer_class(Base,gate)().step(),'ACTUAL_ADMISSION_PATH')
  gate.acquire.assert_called_once();gate.release.assert_called_once()
 def test_viable_queue_syncs_rewards_but_skips_quote_rebuild(self):
  from unittest.mock import patch
  from laya_cell.resident import Supervisor
  from laya_cell.resident_store import database
  with tempfile.TemporaryDirectory() as td:
   s=Supervisor.__new__(Supervisor);s.c={'promotion_protocol':PROTOCOL};s.root=Path(td);s.storage=None
   s.db=database(s.root/'db')
   try:
    with patch('laya_cell.resident.config_check'),patch('laya_cell.resident.bootstrap_still_off'),patch('laya_cell.resident.inspect_storage',return_value={'admission_ready':True}),patch.object(s,'reconcile_evaluation_queue',return_value={'pending_evaluations':2,'viable_pending_evaluations':1,'pending_original_trials':2,'futile_pending_trials':1,'deferred_training_candidates':0}),patch('laya_cell.resident.rewards_current',return_value={'at_ms':1,'reason':'NO_COMPLETE_EXECUTION_EPISODE','rewards':[],'execution_rewards':[]}) as rewards,patch('laya_cell.resident.collect',side_effect=AssertionError('No quote rebuild')):
     result=s.step()
    self.assertEqual(result['state'],'EVALUATING_BOUNDED_CANDIDATE_QUEUE');rewards.assert_called_once()
    self.assertEqual(s.db.execute('select count(*) from consumed_rows').fetchone()[0],0)
   finally:s.db.close()

class DeferredRegistrationTests(unittest.TestCase):
 def supervisor(self,root):
  from laya_cell.resident import Supervisor
  from laya_cell.resident_store import database,admit_job,complete_job
  s=Supervisor.__new__(Supervisor);s.root=root/'control';s.root.mkdir();s.storage=None
  s.c={'evaluation_directory':str(root/'evaluation'),'period_sha256':'p','promotion_protocol':PROTOCOL}
  (root/'evaluation').mkdir();s.db=database(s.root/'db');self.addCleanup(s.db.close)
  admit_job(s.db,'new','fp',1,{},['reserved']);complete_job(s.db,'new',{'candidate_id':'new-candidate'})
  return s
 def test_completed_candidate_waits_without_overlapping_original_trial(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as td:
   s=self.supervisor(Path(td));queue={'pending_original_trials':1,'pending_evaluations':1,'viable_pending_evaluations':0}
   with patch('laya_cell.resident.inspect_evaluation_queue',return_value=queue),patch.object(s,'candidate_done',side_effect=AssertionError('Must not register before original ends')):
    r=s.reconcile_evaluation_queue()
   self.assertEqual(r['deferred_training_candidates'],1);self.assertFalse((Path(s.c['evaluation_directory'])/'new/plan.json').exists())
 def test_waiting_candidate_blocks_another_training_job(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as td:
   s=self.supervisor(Path(td));queue={'pending_original_trials':1,'pending_evaluations':1,'viable_pending_evaluations':0,'deferred_training_candidates':1}
   with patch('laya_cell.resident.config_check'),patch('laya_cell.resident.inspect_storage',return_value={'admission_ready':True}),patch.object(s,'sync_rewards'),patch.object(s,'reconcile_evaluation_queue',return_value=queue),patch.object(s,'write_status',side_effect=lambda **k:k):
    r=s.queue_preflight()
   self.assertEqual(r['state'],'CANDIDATE_READY_AWAITING_FROZEN_TRIAL_END')
 def test_original_closure_registers_one_completed_artifact(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as td:
   s=self.supervisor(Path(td));calls=[]
   def done(row):
    calls.append(row['id']);d=Path(s.c['evaluation_directory'])/row['id'];d.mkdir();(d/'plan.json').write_text('{}');return True
   queues=[{'pending_original_trials':0,'pending_evaluations':0,'viable_pending_evaluations':0},{'pending_original_trials':1,'pending_evaluations':1,'viable_pending_evaluations':1}]
   with patch('laya_cell.resident.inspect_evaluation_queue',side_effect=queues),patch.object(s,'candidate_done',side_effect=done):r=s.reconcile_evaluation_queue()
   self.assertEqual(calls,['new']);self.assertEqual(r['deferred_training_candidates'],0)
 def test_registration_itself_refuses_overlapping_pending_plan(self):
  from unittest.mock import patch
  from laya_cell.resident_store import get_meta
  with tempfile.TemporaryDirectory() as td:
   s=self.supervisor(Path(td));candidate={'candidate_id':'new-candidate'}
   with patch('laya_cell.resident.inspect_evaluation_queue',return_value={'pending_original_trials':1}):
    s.register_evaluation('new',candidate,{})
   self.assertIsNone(get_meta(s.db,'evaluation_trial'));self.assertFalse((Path(s.c['evaluation_directory'])/'new/plan.json').exists())

if __name__=='__main__':unittest.main()
