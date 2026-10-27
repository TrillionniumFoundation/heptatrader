"""Real rule functions, synthetic fixtures; no market efficacy or trading."""
import copy, json, math, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from laya_cell.promotion import RULE, PROTOCOL, rule_for_trial, assess, validate_plan_rule
from laya_cell.promotion_power import design, critical_wins
from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell.resident_evaluate import Evaluator

class ReachabilityTests(unittest.TestCase):
 def plan(self,trial=29,legacy=False):
  return {'candidate_id':'synthetic','plan_id':'synthetic-plan','trial':trial,
          'rule':dict(RULE) if legacy else rule_for_trial(trial,PROTOCOL),'outcome_end_ms':999999999}
 def rows(self,p):
  return [{'candidate_id':'synthetic','slot':i,'status':'OBSERVED','target_class':i%2,
    'anchor_ms':i*360000+100,'prediction_available_ms':i*360000+1000,'trained_until_ms':0,
    'path_gap':False,'parent_brier':.6,'candidate_brier':.4,'candidate_hit':True,'bootstrap_hit':False}
    for i in range(p['rule']['slots'])]
 def test_original_14_all_wins_remains_rejected(self):
  p=self.plan(14,True);self.assertEqual(assess(p,self.rows(p),10**10)['reasons'],['PAIRED_SIGN_EVIDENCE_INSUFFICIENT'])
 def test_original_13_remains_reachable(self):
  p=self.plan(13,True);self.assertTrue(assess(p,self.rows(p),10**10)['statistical_qualification'])
 def test_new_rule_reachable_without_reset_for_late_trials(self):
  for t in [14,28,29,100,512,10000]:
   p=self.plan(t);a=assess(p,self.rows(p),10**10)
   self.assertTrue(a['statistical_qualification'],(t,a));self.assertFalse(a['canonical_owner_admitted'])
   self.assertFalse(a['unconditional_efficacy_proven']);self.assertEqual(a['alpha'],.05/(t*(t+1)))
 def test_29_preplanned_power_not_just_all_wins(self):
  n,k,power=design(29);self.assertEqual((n,k),(84,60));self.assertGreaterEqual(power,.8)
  p=self.plan();rows=self.rows(p)
  for row in rows[k:]:row['candidate_brier']=.601
  a=assess(p,rows[:84]+[dict(candidate_id='synthetic',slot=i,status='CENSORED') for i in [84,85]],10**10)
  self.assertTrue(a['statistical_qualification'],a)
 def test_worse_candidate_is_not_rescued(self):
  p=self.plan();rows=self.rows(p)
  for x in rows:x['candidate_brier']=.8
  self.assertFalse(assess(p,rows,10**10)['statistical_qualification'])
 def test_ties_do_not_inflate_power_sample(self):
  p=self.plan();rows=self.rows(p)
  for x in rows[:3]:x['candidate_brier']=.6
  self.assertIn('INSUFFICIENT_NON_TIE_POWER_SAMPLE',assess(p,rows,10**10)['reasons'])
 def test_censor_limit_not_loosened(self):
  p=self.plan();rows=self.rows(p)
  for x in rows[:3]:x['status']='CENSORED'
  self.assertIn('EXCESS_CENSORED_WINDOWS',assess(p,rows,10**10)['reasons'])
 def test_future_window_cannot_pass_early(self):
  p=self.plan();self.assertEqual(assess(p,self.rows(p),1)['state'],'PENDING')
 def test_mutated_power_rule_refused(self):
  for key in ['slots','planned_critical_wins','max_censored','min_observed','planning_win_probability']:
   p=self.plan();p['rule'][key]+=1
   with self.assertRaises(ContractError):assess(p,self.rows(self.plan()),10**10)
 def test_invalid_or_bool_trial_and_unknown_protocol(self):
  for t in [True,0,-1,1.5,1000001]:
   with self.assertRaises(ContractError):rule_for_trial(t,PROTOCOL)
  with self.assertRaises(ContractError):rule_for_trial(29,'RESET_ALPHA')
 def test_rules_do_not_share_mutable_cached_objects(self):
  a=rule_for_trial(29,PROTOCOL);a['slots']=1
  self.assertEqual(rule_for_trial(29,PROTOCOL)['slots'],86)
 def test_spent_alpha_unchanged(self):
  spent=sum(.05/(t*(t+1)) for t in range(1,29))
  self.assertAlmostEqual(spent+.05/29,.05)

class ClosedStatusTests(unittest.TestCase):
 def test_closed_original_result_is_preserved_and_counted_after_source_upgrade(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);period=root/'period';period.write_text('{}')
   folder=root/'evaluation'/'old';folder.mkdir(parents=True)
   p={'candidate_id':'c','trial':14,'rule':dict(RULE),'scope':sha_file(period),'evaluation_runtime_sha256':{'missing.py':'x'}}
   p['plan_id']=digest(p);(folder/'plan.json').write_text(json.dumps(p))
   q={'candidate_id':'c','plan_id':p['plan_id'],'state':'REJECTED','observed':10,'censored':2}
   f=folder/'qualification.json';f.write_text(json.dumps(q));before=f.read_bytes()
   config=root/'config';config.write_text(json.dumps({'evaluation_directory':str(root/'evaluation'),'period_path':str(period),'period_sha256':sha_file(period),'runtime_directory':str(root/'new-runtime')}))
   e=Evaluator(config)
   with patch('laya_cell.resident_evaluate.read_quotes',return_value=([],[])):
    status=e.step()
   self.assertEqual((status['observed'],status['censored']),(10,2));self.assertEqual(f.read_bytes(),before)
   self.assertEqual(status['closed_coverage_diagnostics']['closed_plans_without_verifiable_deadline'],1)
   self.assertEqual(status['closed_coverage_diagnostics']['plans_with_diagnostics'],0)

class StorageStatusTests(unittest.TestCase):
 def test_actual_scheduler_publishes_storage_hold_without_consuming_rows(self):
  from laya_cell.resident import Supervisor
  from laya_cell.resident_store import database
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);s=Supervisor.__new__(Supervisor);s.c={'state_root':str(root),'state_max_bytes':100,'state_admission_reserve_bytes':90};s.root=root;s.db=database(root/'scheduler.sqlite');s.storage=None
   try:
    with patch('laya_cell.resident.config_check'),patch('laya_cell.resident.bootstrap_still_off'):
     out=s.step()
    self.assertEqual(out['state'],'HOLD_NEW_TRAINING_STORAGE');self.assertFalse(out['storage']['admission_ready'])
    self.assertEqual(s.db.execute('SELECT count(*) FROM consumed_rows').fetchone()[0],0)
    self.assertGreater(out['at_ms'],0)
   finally:s.db.close()

class TrialHistoryTests(unittest.TestCase):
 def test_reset_trial_history_cannot_start_v2(self):
  from laya_cell.resident import Supervisor
  from laya_cell.resident_store import database,put_meta
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);folder=root/'evaluation'/'old';folder.mkdir(parents=True)
   (folder/'plan.json').write_text(json.dumps({'trial':28}))
   s=Supervisor.__new__(Supervisor);s.c={'promotion_protocol':PROTOCOL,'promotion_legacy_trial_floor':28,'evaluation_directory':str(root/'evaluation')};s.db=database(root/'db')
   try:
    with self.assertRaisesRegex(ContractError,'HISTORY_RESET'):s.recover()
    put_meta(s.db,'evaluation_trial',28);s.recover()
   finally:s.db.close()
