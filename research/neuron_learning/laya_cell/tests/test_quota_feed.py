"""Synthetic regression fixtures; no source/fee rewrites or trading operations."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from laya_cell.integrity import ContractError,digest
from laya_cell.resident_budget import policy,inspect,GIB
from laya_cell.resident_data import contiguous_history,forecast_input
from laya_cell.source_readiness import probe,assess,RULE
T=1791435000000

def quotes(times):return [{'t':T+t,'known_at_ms':T+t,'bid':1.1,'ask':1.10002,'mid':1.10001,'receipt':str(t)} for t in times]

class BudgetTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  (self.root/'evidence').write_bytes(b'x'*100)
  self.c={'state_root':str(self.root),'state_max_bytes':200,'state_admission_reserve_bytes':50,'filesystem_reserve_bytes':0}
 def test_regular_evidence_count_without_pruning(self):
  result=inspect(self.c);self.assertEqual(result['state_used_bytes'],100);self.assertTrue(result['admission_ready'])
  self.assertEqual((self.root/'evidence').read_bytes(),b'x'*100);self.assertFalse(result['evidence_pruning_permitted'])
 def test_state_budget_full_still_denies(self):
  r=inspect({**self.c,'state_max_bytes':99});self.assertEqual(r['blocked_reason'],'STATE_BUDGET_FULL_NO_EVIDENCE_PRUNING')
 def test_reserve_prevents_admission_before_limit(self):
  r=inspect({**self.c,'state_max_bytes':149});self.assertEqual(r['blocked_reason'],'STATE_HEADROOM_NO_NEW_LEARNING')
 def test_exact_reserve_boundary(self):self.assertTrue(inspect({**self.c,'state_max_bytes':150})['admission_ready'])
 def test_disk_low_reserve_denies_without_state_changes(self):
  with patch('laya_cell.resident_budget.os.statvfs',return_value=SimpleNamespace(f_bavail=10,f_frsize=1)):
   r=inspect(self.c)
  self.assertEqual(r['blocked_reason'],'FILESYSTEM_RESERVE_NO_NEW_LEARNING');self.assertEqual((self.root/'evidence').stat().st_size,100)
 def test_explicit_8gib_with_512m_and_10gib_reserve_valid(self):
  self.assertEqual(policy({**self.c,'state_max_bytes':8*GIB,'state_admission_reserve_bytes':GIB//2,'filesystem_reserve_bytes':10*GIB}),(8*GIB,GIB//2,10*GIB))
 def test_boolean_nan_negative_or_unbounded_limits_refused(self):
  for val in (True,0,-1,float('nan'),float('inf'),17*GIB):
   with self.subTest(val=val),self.assertRaises(ContractError):policy({**self.c,'state_max_bytes':val})
 def test_invalid_headroom(self):
  for val in (-1,True,201):
   with self.subTest(val=val),self.assertRaises(ContractError):policy({**self.c,'state_admission_reserve_bytes':val})
 def test_symlink_cannot_hide_external_evidence_size(self):
  (self.root/'link').symlink_to(self.root/'evidence')
  with self.assertRaisesRegex(ContractError,'SYMLINK'):inspect(self.c)
 def test_negative_free_reserve(self):
  with self.assertRaises(ContractError):policy({**self.c,'filesystem_reserve_bytes':-1})
 def test_root_is_not_relative(self):
  with self.assertRaises(ContractError):inspect({**self.c,'state_root':'.'})

class HistoryRegressionTests(unittest.TestCase):
 def test_thinning_can_no_longer_create_a_36second_selected_gap(self):
  # Raw timestamps all within 30s. Backwards 10s thinning formerly discarded
  # t=27s, then selected t=0s from t=36s and rejected the whole input.
  q=quotes([0,27000,36000,46000,56000,66000,76000,86000,96000])
  h,reset=contiguous_history(q,T+96001)
  self.assertTrue(reset);self.assertEqual(h[0]['t'],T+36000)
  self.assertTrue(all(10000<=b['t']-a['t']<=30000 for a,b in zip(h,h[1:])))
  sample,_=forecast_input({'period_sha256':'a'*64},q,T+96001)
  self.assertFalse(sample['input_gap']);self.assertEqual(sample['history_selection_version'],'CONTIGUOUS_SELECTED_SUFFIX_V2')
 def test_actual_31s_raw_gap_still_cuts_history(self):
  q=quotes([0,10000,20000,30000,40000,71000]);h,reset=contiguous_history(q,T+71001)
  self.assertTrue(reset);self.assertEqual(len(h),1)
  with self.assertRaisesRegex(ContractError,'INSUFFICIENT'):forecast_input({'period_sha256':'a'*64},q,T+71001)
 def test_unchanged_dense_history_and_boundary(self):
  q=quotes(list(range(0,240000,10000)));h,reset=contiguous_history(q,T+230001)
  self.assertEqual(h,q);self.assertFalse(reset)
 def test_future_arrival_cannot_repair_old_input(self):
  q=quotes([0,10000,20000,30000,40000,50000]);q[-1]['known_at_ms']=T+51000
  with self.assertRaisesRegex(ContractError,'INSUFFICIENT'):forecast_input({'period_sha256':'a'*64},q,T+50001)
 def test_quote_ttl_5seconds_not_relaxed(self):
  q=quotes(list(range(0,60000,10000)))
  with self.assertRaisesRegex(ContractError,'NOT_FRESH'):forecast_input({'period_sha256':'a'*64},q,T+55001)
 def test_no_original_times_or_receipts_mutated(self):
  q=quotes([0,27000,36000,46000,56000,66000,76000,86000,96000]);saved=copy.deepcopy(q)
  forecast_input({'period_sha256':'a'*64},q,T+96001);self.assertEqual(q,saved)

class SourceReadinessTests(unittest.TestCase):
 def setUp(self):self.c={'period_sha256':'a'*64}
 def fixture(self):return quotes(list(range(-240000,370000,1000)))
 def test_complete_causal_probe(self):self.assertEqual(probe(self.fixture(),T,self.c)['status'],'USABLE')
 def test_future_endpoint_missing_is_not_interpolated(self):
  q=[x for x in self.fixture() if not T+300000<=x['t']<=T+305000]
  self.assertEqual(probe(q,T,self.c)['status'],'ENDPOINT_GAP')
 def test_path_gap_not_zero_filled(self):
  q=[x for x in self.fixture() if not T+120000<=x['t']<=T+160000]
  self.assertEqual(probe(q,T,self.c)['status'],'PATH_GAP')
 def test_first_outcome_gap_is_distinct_from_healthy_input_history(self):
  q=[x for x in self.fixture() if not (T+120000<=x['t']<=T+160000 or T+220000<=x['t']<=T+260000)]
  original=copy.deepcopy(q);config=copy.deepcopy(self.c);sample,task=forecast_input(self.c,q,T)
  r=probe(q,T,self.c)
  self.assertEqual(r['status'],'PATH_GAP');self.assertEqual(r['history_max_gap_ms'],10000)
  self.assertEqual(r['outcome_path_first_gap'],{'from_ms':T+119000,'to_ms':T+161000,'gap_ms':42000})
  self.assertEqual(q,original);self.assertEqual(self.c,config)
  self.assertEqual(forecast_input(self.c,q,T),(sample,task))
  self.assertEqual(sample['sample_id'],digest(sample['input_snapshot']))
  self.assertNotIn('outcome_path_first_gap',sample)
  self.assertNotIn('outcome_path_first_gap',sample['input_snapshot'])
 def test_outcome_gap_boundary_and_endpoint_priority_are_unchanged(self):
  for gap,status in ((30000,'USABLE'),(30001,'PATH_GAP')):
   with self.subTest(gap_ms=gap):
    q=[x for x in self.fixture() if not T+120000<x['t']<T+120000+gap]
    if gap==30001:q=sorted(q+quotes([120000+gap]),key=lambda x:x['t'])
    r=probe(q,T,self.c);self.assertEqual(r['status'],status)
    if status=='USABLE':self.assertNotIn('outcome_path_first_gap',r)
    else:self.assertEqual(r['outcome_path_first_gap'],{'from_ms':T+120000,'to_ms':T+150001,'gap_ms':30001})
  late=[x for x in q if not T+300000<=x['t']<=T+305000]
  r=probe(late,T,self.c);self.assertEqual(r['status'],'ENDPOINT_GAP')
  self.assertNotIn('outcome_path_first_gap',r)
  r=probe([x for x in q if x['t']<T+300000],T,self.c)
  self.assertEqual(r['status'],'OUTCOME_UNAVAILABLE');self.assertNotIn('outcome_path_first_gap',r)
  endpoint=[x for x in self.fixture() if not T+270000<x['t']<T+300001]
  endpoint=sorted(endpoint+quotes([300001]),key=lambda x:x['t'])
  r=probe(endpoint,T,self.c);self.assertEqual(r['status'],'PATH_GAP')
  self.assertEqual(r['outcome_path_first_gap'],{'from_ms':T+270000,'to_ms':T+300001,'gap_ms':30001})
 def test_outcome_gap_diagnostic_retains_original_source_prefix_binding(self):
  now=T+1800000
  q=quotes([x for x in range(-240000,1800001,1000) if not 120000<=x<=160000])
  prefixes=[{'path':'/fixture/quotes.jsonl','bytes':123,'sha256':'b'*64}]
  original=copy.deepcopy(prefixes)
  r=assess(self.c,now,quote_loader=lambda config,at:(q,prefixes))
  gaps=[p for p in r['probes'] if p['status']=='PATH_GAP']
  self.assertTrue(gaps)
  self.assertEqual(gaps[0]['outcome_path_first_gap'],{'from_ms':T+119000,'to_ms':T+161000,'gap_ms':42000})
  self.assertEqual(prefixes,original);self.assertEqual(r['source_prefixes'],original)
  self.assertEqual(r['source_prefix_digest'],digest(original))
  self.assertEqual(r['rule'],RULE);self.assertFalse(r['old_failed_windows_changed'])
  self.assertFalse(r['automatic_trial_registration']);self.assertFalse(r['trading_authority'])
 def test_no_new_trial_authority_even_full_coverage(self):
  now=T+3600000;q=quotes(list(range(0,3600001,1000)))
  with patch('laya_cell.source_readiness.read_quotes',return_value=(q,[])):r=assess(self.c,now)
  self.assertEqual(r['usable'],24);self.assertFalse(r['automatic_trial_registration']);self.assertFalse(r['trading_authority'])
 def test_stale_source_prevents_new_window(self):
  now=T+3600000;q=quotes(list(range(0,3590001,1000)))
  with patch('laya_cell.source_readiness.read_quotes',return_value=(q,[])):r=assess(self.c,now)
  self.assertEqual(r['state'],'HOLD_NEW_FUSION_WINDOW');self.assertIn('LATEST_AUTHORITATIVE_QUOTE_NOT_FRESH',r['reasons'])
 def test_no_data_remains_no_data(self):
  with patch('laya_cell.source_readiness.read_quotes',return_value=([],[])):r=assess(self.c,T+3600000)
  self.assertEqual(r['usable'],0);self.assertEqual(r['unusable'],24)
 def test_quality_grid_not_independent_model_observations(self):
  self.assertEqual(RULE['statistics_scope'],'OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE')
 def test_scope_is_not_net_pnl(self):self.assertNotIn('commission',RULE);self.assertEqual(RULE['horizon_ms'],300000)

class QuotaTransitionTests(unittest.TestCase):
 def configs(self):
  old={'state_max_bytes':2*GIB,'automatic_trading':False,'profile':{'learning_rate':.001},'period_sha256':'a'*64}
  new={**old,'state_max_bytes':8*GIB,'state_admission_reserve_bytes':GIB//2,'filesystem_reserve_bytes':10*GIB}
  return old,new
 def test_only_quota_keys_may_change(self):
  from laya_cell.recover_quota import validate_transition
  old,new=self.configs();self.assertEqual(len(validate_transition(old,new)),3)
 def test_cannot_change_learning_profile_or_period(self):
  from laya_cell.recover_quota import validate_transition
  old,new=self.configs()
  for change in ({'profile':{'learning_rate':.1}},{'period_sha256':'b'*64},{'automatic_trading':True}):
   with self.subTest(change=change),self.assertRaises(ContractError):validate_transition(old,{**new,**change})
 def test_not_an_unbounded_quota_override(self):
  from laya_cell.recover_quota import validate_transition
  old,new=self.configs()
  for limit in (0,16*GIB,100*GIB):
   with self.subTest(limit=limit),self.assertRaises(ContractError):validate_transition(old,{**new,'state_max_bytes':limit})
 def test_disk_and_job_reserves_cannot_be_disabled(self):
  from laya_cell.recover_quota import validate_transition
  old,new=self.configs()
  for key in ('state_admission_reserve_bytes','filesystem_reserve_bytes'):
   with self.subTest(key=key),self.assertRaises(ContractError):validate_transition(old,{**new,key:0})

if __name__=='__main__':unittest.main(verbosity=2)
