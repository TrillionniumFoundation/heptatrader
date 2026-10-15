"""Synthetic original producer; no model access or Broker request."""
import hashlib,json,os,tempfile,unittest
from pathlib import Path
from historical_training.value_priority_admission import decision,verify_policy,VALUE_DEADLINE_MS
from historical_training.common import EvidenceError

class PriorityTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.root=Path(self.tmp.name);self.root.chmod(0o700)
  self.journal=self.root/'journal';self.journal.mkdir(mode=0o700)
  self.manifest=self.root/'manifest.json'
  self.plan=self.root/'plan.json'
  self.now=1792037000000
  self.records=[]
  self.journal_file=self.journal/('a'*32+'-0000.jsonl')
  self.journal_file.write_text('');self.journal_file.chmod(0o600)
  self.update_manifest();self.update_plan()
 def tearDown(self):self.tmp.cleanup()
 def update_manifest(self,**updates):
  m={'schema':'heptatrader.attribution-producer.v1','instrument':'EUR.USD',
     'period_sha256':'fixedperiod','trading_behavior_changed':True,
     'journal_directory':str(self.journal)}
  m.update(updates);self.manifest.write_text(json.dumps(m));self.manifest.chmod(0o600)
 def update_plan(self,**updates):
  p={'schema':'heptatrader.current100k-value-forward-plan.v1',
    'plan_id':'trial-test','period_sha256':'fixedperiod',
    'worker_runtime_inventory_digest':'fixeddigest',
    'automatic_effect_authority':False,
    'input_start_ms':self.now-86400000,
    'input_end_ms':self.now+86400000,
    'producer_registration':{
     'manifest':str(self.manifest),'sha256':hashlib.sha256(self.manifest.read_bytes()).hexdigest()}}
  p.update(updates);self.plan.write_text(json.dumps(p));self.plan.chmod(0o600)
  self.policy={'schema':'heptatrader.original-value-priority.v1',
   'plan_path':str(self.plan),'plan_sha256':hashlib.sha256(self.plan.read_bytes()).hexdigest()}
 def rows(self,last_age_ms=10000,flat=True,interval_ms=120000,count=6):
  first=self.now-last_age_ms-interval_ms*(count-1)
  events=[]
  for n in range(count):
   t=first+n*interval_ms
   e={'kind':'REQUEST','written_at_ms':t+1,'data':{
    'request_id':format(n+1,'032x'),'started_at_ms':t,
    'position_at_request':0.0 if flat else 1.0}}
   events.append(e)
  self.journal_file.write_text(''.join(json.dumps(x)+'\n' for x in events))
 def test_original_flat_request_blocks_new_evaluator(self):
  self.rows(last_age_ms=9000,flat=True)
  b,r,p=decision(self.policy,'evaluator',self.now)
  self.assertTrue(b);self.assertEqual(r,'VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST')
  self.assertEqual(p['verified_periodic_intervals'],4)
  self.assertFalse(p['no_future_request_forged'] is False)
 def test_120s_producer_cannot_fit_nonpreemptible_240s_evaluator(self):
  self.rows(last_age_ms=10000,flat=False)
  deny,reason,e=decision(self.policy,'evaluator',self.now)
  self.assertTrue(deny);self.assertEqual(reason,'VALUE_PRIORITY_DEFER_NEW_LONG_EVALUATION')
  self.assertEqual(e['background_max_nonpreemptible_ms'],250000)
  self.assertTrue(e['next_original_request_is_only_estimated_not_evidence'])
 def test_verified_next_tick_within_60sec_blocks_evaluator(self):
  self.rows(last_age_ms=79000,flat=False)
  deny,reason,_=decision(self.policy,'evaluator',self.now)
  self.assertTrue(deny);self.assertEqual(reason,'VALUE_PRIORITY_DEFER_NEW_LONG_EVALUATION')
 def test_original_240sec_budget_has_bounded_shutdown_reserve(self):
  from historical_training.value_priority_admission import EVALUATION_BUDGET_MS
  self.assertGreaterEqual(EVALUATION_BUDGET_MS,240000+5000+2000)
 def test_next_scheduled_slot_after_trial_end_does_not_starve_evaluator(self):
  self.rows(last_age_ms=10000,flat=False)
  self.update_plan(input_end_ms=self.now+50000)
  deny,reason,_=decision(self.policy,'evaluator',self.now)
  self.assertFalse(deny);self.assertEqual(reason,'VALUE_PRIORITY_NO_ACTIVE_RESERVATION')
 def test_irregular_nonflat_requests_allow_independent_evaluator(self):
  self.rows(last_age_ms=10000,flat=False,interval_ms=117000)
  deny,reason,proof=decision(self.policy,'evaluator',self.now)
  self.assertFalse(deny);self.assertEqual(reason,'VALUE_PRIORITY_NO_ACTIVE_RESERVATION')
  self.assertIsNone(proof['next_expected_from_historical_cadence_ms'])
 def test_unverified_next_slot_is_not_invented(self):
  self.rows(last_age_ms=110000,flat=False,interval_ms=70000)
  deny,reason,e=decision(self.policy,'evaluator',self.now)
  self.assertFalse(deny);self.assertEqual(e['verified_periodic_intervals'],0)
 def test_trainer_defers_long_job_but_never_claims_to_stop_running_job(self):
  self.rows(last_age_ms=10000,flat=False)
  b,r,e=decision(self.policy,'trainer',self.now)
  self.assertTrue(b);self.assertEqual(r,'VALUE_PRIORITY_DEFER_NEW_LONG_TRAINING')
  self.assertFalse('signal' in e)
 def test_source_outage_finite_release(self):
  self.rows(last_age_ms=300000,flat=False)
  deny,reason,_=decision(self.policy,'trainer',self.now)
  self.assertFalse(deny);self.assertEqual(reason,'VALUE_PRODUCER_STALE_NO_RESERVATION')
 def test_expired_trial_has_no_active_priority(self):
  self.rows()
  self.update_plan(input_end_ms=self.now)
  deny,reason,_=decision(self.policy,'trainer',self.now)
  self.assertFalse(deny);self.assertEqual(reason,'VALUE_TRIAL_OUTSIDE_WINDOW')
 def test_no_pin_change(self):
  self.rows()
  self.plan.write_text(self.plan.read_text()+' ')
  with self.assertRaisesRegex(EvidenceError,'PIN_CHANGED'):decision(self.policy,'trainer',self.now)
 def test_source_manifest_must_be_pinned(self):
  self.rows()
  self.update_manifest(instrument='BTC.USD')
  with self.assertRaisesRegex(EvidenceError,'PIN_CHANGED'):decision(self.policy,'trainer',self.now)
 def test_unsafe_permissions_failclosed(self):
  self.rows();self.journal_file.chmod(0o644)
  with self.assertRaisesRegex(EvidenceError,'JOURNAL_FILE_SCOPE'):decision(self.policy,'trainer',self.now)
 def test_duplicate_journal_slot_failsclosed(self):
  self.rows()
  lines=self.journal_file.read_text().splitlines()
  x=json.loads(lines[-1]);x['data']['request_id']='f'*32
  self.journal_file.write_text(self.journal_file.read_text()+json.dumps(x)+'\n')
  with self.assertRaisesRegex(EvidenceError,'DUPLICATE_SLOT'):decision(self.policy,'trainer',self.now)
 def test_no_model_or_trade_authority_flags(self):
  self.rows()
  decision_state=decision(self.policy,'trainer',self.now)
  self.assertFalse('paper_authorized' in decision_state[2])
  self.assertEqual(decision_state[2]['broker_orders_sent'],0)
 def test_policy_shape_rejected(self):
  x={**self.policy,'override':True}
  with self.assertRaises(EvidenceError):verify_policy(x)
 def test_partial_tail_record_not_accepted(self):
  self.rows(last_age_ms=220000,flat=False)
  self.journal_file.write_text(self.journal_file.read_text()+'{"kind":"REQUEST"')
  no_hold,_,_=decision(self.policy,'evaluator',self.now)
  self.assertFalse(no_hold)

from unittest.mock import patch
from historical_training.resident_resource_guard import Gate

class PriorityGateTests(unittest.TestCase):
 def config(self,folder):
  return {'state_root':str(folder),
          'value_priority':{'schema':'heptatrader.original-value-priority.v1',
           'plan_path':str(folder/'plan.json'),'plan_sha256':'a'*64}}
 def test_evaluator_denial_never_claims_shared_heavy_lock(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);root.chmod(0o700)
   gate=Gate(self.config(root),'evaluator')
   gate.check=lambda:{'at_ms':10,'admit_new_worker':True,'reasons':[]}
   with patch('historical_training.value_priority_admission.decision',
              return_value=(True,'VALUE_PRIORITY_NEXT_ORIGINAL_REQUEST_WINDOW',{'source':'TEST'})):
    self.assertFalse(gate.acquire({'plan':'test','slot':2}))
   self.assertIsNone(gate.lease.fd)
   self.assertIn('VALUE_PRIORITY_NEXT_ORIGINAL_REQUEST_WINDOW',gate.last_admission['reasons'])
   self.assertFalse(gate.last_admission['admit_new_worker'])
 def test_admission_race_checked_after_flock(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);root.chmod(0o700)
   gate=Gate(self.config(root),'evaluator')
   gate.check=lambda:{'at_ms':10,'admit_new_worker':True,'reasons':[]}
   with patch('historical_training.value_priority_admission.decision',
              side_effect=[(False,'NO_PRIORITY',{'test':0}),
                           (False,'NO_PRIORITY',{'test':1}),
                           (True,'VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST',{'test':2})]):
    self.assertFalse(gate.acquire({'plan':'test','slot':1}))
   self.assertIsNone(gate.lease.fd)
   self.assertFalse(gate.last_admission['admit_new_worker'])
 def test_trainer_priority_does_not_modify_original_or_force_stop(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);root.chmod(0o700)
   gate=Gate(self.config(root),'trainer')
   gate.check=lambda:{'at_ms':10,'admit_new_worker':True,'reasons':[]}
   with patch('historical_training.value_priority_admission.decision',
              return_value=(True,'VALUE_PRIORITY_DEFER_NEW_LONG_TRAINING',{'no_future_request_forged':True})):
    self.assertFalse(gate.acquire({'role':'trainer'}))
   self.assertFalse(gate.draining)
   self.assertIsNone(gate.lease.fd)
 def test_invalid_priority_source_fails_closed_for_background_only(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);root.chmod(0o700)
   gate=Gate(self.config(root),'evaluator')
   gate.check=lambda:{'at_ms':10,'admit_new_worker':True,'reasons':[]}
   with patch('historical_training.value_priority_admission.decision',
              side_effect=EvidenceError('TEST_PIN_CHANGED')):
    self.assertFalse(gate.acquire({'plan':'test'}))
   self.assertEqual(gate.last_admission['reasons'],['VALUE_PRIORITY_UNVERIFIED_SOURCE'])
if __name__=='__main__':unittest.main()
