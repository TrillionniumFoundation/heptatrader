"""Synthetic CPU regressions for the prospective v3 training and value policy."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import torch
from laya_cell import resident_action_v2_research as action
from laya_cell import resident_training_v2 as v2
from laya_cell import resident_training_v3 as v3
from laya_cell import resident_data as source
from laya_cell import resident_price_baselines as refs
from laya_cell.integrity import ContractError,digest,sha_file
from laya_cell.resident_row_accounting import row_id
from laya_cell.promotion import RULE,assess
from laya_cell.tests import test_resident_price_baselines as fixtures

BASE=1800000000000


def causal_rows(correct=4):
 return [{'row_id':f'{i:064x}','anchor_ms':BASE-100000-i*1000,
          'future_outcome_available_ms':BASE-1000-i,'parent_correct':i<correct}
         for i in range(32)]


class ActionBalanceV3Tests(unittest.TestCase):
 def profile(self,correct=4):
  torch.set_num_threads(1)
  return action.decide_train_only_action(causal_rows(correct),cutoff_ms=BASE,
      parent_action_logits=torch.zeros(32,2,device='cpu'))
 def gradient(self,profile):
  logits=torch.zeros(2,2,device='cpu',requires_grad=True)
  loss=action.balanced_action_loss(logits,torch.tensor([True,False]),torch.tensor(10.),profile)
  loss.backward()
  return logits.grad.detach()
 def test_4_28_pool_balanced_once_and_legacy_gradient_is_unchanged(self):
  legacy=self.profile();before=copy.deepcopy(legacy)
  new=v3.pair_calibration(legacy)
  old_grad,new_grad=self.gradient(legacy),self.gradient(new)
  self.assertEqual(legacy,before)
  self.assertNotEqual(legacy['profile_id'],new['profile_id'])
  self.assertEqual(new['labels'],{'ACT_correct':4,'ESCALATE_wrong':28})
  self.assertAlmostEqual(float(old_grad[0].abs().sum()/old_grad[1].abs().sum()),7.)
  self.assertAlmostEqual(float(new_grad[0].abs().sum()/new_grad[1].abs().sum()),1.)
  torch.testing.assert_close(new_grad[0],-new_grad[1])
  print(json.dumps({'synthetic_cpu_only':True,'action_gradient_4_28':{
      'legacy_v2':old_grad.tolist(),'v3_single_balance':new_grad.tolist()}}))
 def test_single_class_still_abstains_and_has_zero_loss_gradient(self):
  p=v3.pair_calibration(self.profile(0))
  x=torch.zeros(2,2,device='cpu',requires_grad=True)
  loss=action.balanced_action_loss(x,torch.tensor([False,False]),torch.tensor(2.),p)
  loss.backward()
  self.assertFalse(p['action_optimizer_admitted'])
  self.assertEqual(float(loss.detach()),0.)
  self.assertEqual(float(x.grad.abs().sum()),0.)
  self.assertIsNone(v3.action_inference(x,p)['action_correct_probs'])
 def test_3_29_support_reason_is_precise_without_rewriting_frozen_profile(self):
  p=v3.pair_calibration(self.profile(3));before=copy.deepcopy(p)
  diagnostic=action.action_support_diagnostic(p)
  self.assertEqual(p,before)
  self.assertEqual(p['action_refusal_reason'],'NO_BOTH_TRUE_ACTION_LABELS')
  self.assertEqual(diagnostic['reason'],'BOTH_LABELS_PRESENT_BUT_BELOW_MINIMUM_SUPPORT')
  self.assertEqual(diagnostic['labels'],{'ACT_correct':3,'ESCALATE_wrong':29})
  self.assertEqual(diagnostic['shortfall_by_label'],{'ACT_correct':1,'ESCALATE_wrong':0})
  self.assertEqual(diagnostic['frozen_profile_id'],p['profile_id'])
  x=torch.zeros(2,2,device='cpu',requires_grad=True)
  loss=action.balanced_action_loss(x,torch.tensor([True,False]),torch.tensor(2.),p)
  loss.backward()
  self.assertEqual(float(loss.detach()),0.)
  self.assertEqual(float(x.grad.abs().sum()),0.)
  inference=v3.action_inference(x,p)
  self.assertIsNone(inference['action_correct_probs'])
  self.assertEqual(inference['action_support_diagnostic'],diagnostic)
  self.assertEqual(p,before)
 def test_support_diagnostic_distinguishes_missing_labels_and_admitted_support(self):
  for correct,reason in ((0,'MISSING_TRUE_ACTION_LABEL'),(4,None),
                         (29,'BOTH_LABELS_PRESENT_BUT_BELOW_MINIMUM_SUPPORT'),
                         (32,'MISSING_TRUE_ACTION_LABEL')):
   with self.subTest(correct=correct):
    p=v3.pair_calibration(self.profile(correct))
    self.assertEqual(action.action_support_diagnostic(p)['reason'],reason)
 def test_v3_rejects_nonpaired_two_class_batch(self):
  p=v3.pair_calibration(self.profile())
  with self.assertRaisesRegex(ContractError,'DISJOINT_PAIR'):
   action.balanced_action_loss(torch.zeros(3,2),torch.tensor([True,False,False]),torch.tensor(2.),p)
 def test_unknown_balance_policy_cannot_hide_in_valid_profile_hash(self):
  p=v3.pair_calibration(self.profile())
  p['batch_balance_strategy']='FUTURE_WEIGHT_POLICY'
  p['profile_id']=digest({k:v for k,v in p.items() if k!='profile_id'})
  with self.assertRaisesRegex(ContractError,'BATCH_BALANCE'):
   self.gradient(p)
 def test_v3_selects_only_primary_horizon_at_original_budget(self):
  rows,_=fixtures.population()
  rows=rows+[{**r,'sample_id':digest(['extra',r['sample_id']]),'anchor_ms':r['anchor_ms']+86400000,
             'outcome_receipt_digest':digest(['extra',r['outcome_receipt_digest']])} for r in rows]
  chosen=v3.select_covered_rows([(row_id(r),r) for r in rows])
  self.assertEqual(len(chosen),32)
  self.assertEqual({r['horizon_sec'] for _,r in chosen},{300})
  self.assertGreater(max(r['anchor_ms'] for _,r in chosen)-min(r['anchor_ms'] for _,r in chosen),86400000)
  self.assertEqual(v3.OBJECTIVE['max_steps'],16)
  self.assertEqual(v3.OBJECTIVE['max_rows'],32)


def quote_population():
 quotes=[]
 for session in (0,12,36):
  for i in range(360):
   at=BASE+session*3600000+i*10000
   bid=1.08+(i%23)*.000001;ask=bid+.00002
   quotes.append({'t':at,'bid':bid,'ask':ask,'mid':(bid+ask)/2,
                  'known_at_ms':at+1,'receipt':digest(['SYNTHETIC_ONLY',session,i])})
 return quotes


class MaturePoolV3Tests(unittest.TestCase):
 def collect(self,quotes=None,reserved=(),legacy=False,seed_scope=None):
  quotes=quote_population() if quotes is None else quotes
  now=quotes[-1]['t']+2
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'seed.json'
   seed={'scope':seed_scope or 'a'*64+':EUR.USD','source_kind':'REAL_PAPER_RUNTIME',
         'samples':[],'source_prefixes':[]}
   path.write_text(json.dumps(seed))
   config={'period_sha256':'a'*64,'seed_dataset':str(path),'seed_dataset_sha256':sha_file(path),
           'objective':copy.deepcopy(v2.OBJECTIVE if legacy else v3.OBJECTIVE)}
   with patch.object(source,'read_quotes',return_value=(quotes,[{'synthetic_fixture':True}])):
    return source.collect(config,now,reserved_row_ids=reserved)
 def test_cap_spans_old_middle_and_new_sessions_before_training_selection(self):
  dataset,rows,quality,_=self.collect()
  legacy,_,_,_=self.collect(legacy=True)
  self.assertEqual(len(dataset['samples']),512)
  self.assertEqual(len(rows),512)
  self.assertEqual({r['horizon_sec'] for r in rows},{300})
  self.assertGreater(max(r['anchor_ms'] for r in rows)-min(r['anchor_ms'] for r in rows),24*3600000)
  self.assertLess(min(r['anchor_ms'] for r in rows),legacy['samples'][0]['anchor_ms'])
  self.assertEqual({(r['anchor_ms']-BASE)//3600000 for r in rows},{0,12,36})
  self.assertGreater(quality['training_pool']['unreserved_mature_primary_anchors_before_cap'],512)
  self.assertNotIn('training_pool',legacy)
 def test_reservations_are_removed_before_cap_and_pool_stays_full(self):
  _,rows,_,_=self.collect()
  reserved={row_id(r) for r in rows[::16]}
  _,new,quality,_=self.collect(reserved=reserved)
  self.assertEqual(len(new),512)
  self.assertFalse(reserved.intersection(row_id(r) for r in new))
  self.assertEqual(quality['training_pool']['exclusion_counts']['ALREADY_RESERVED_PRIMARY_ROW'],len(reserved))
 def test_gap_and_unavailable_future_endpoint_never_supply_primary_label(self):
  quotes=quote_population()
  quotes=[q for q in quotes if not BASE+12*3600000+1800000<=q['t']<BASE+12*3600000+1850000]
  now=quotes[-1]['t']+2
  for q in quotes[-30:]:q['known_at_ms']=now+100000
  dataset,rows,quality,_=self.collect(quotes=quotes)
  self.assertTrue(rows)
  self.assertGreater(quality['training_pool']['exclusion_counts']['PATH_GAP'],0)
  for sample in dataset['samples']:
   self.assertEqual(sample['label_reasons'][0],'OBSERVED')
   self.assertLessEqual(sample['outcome_receipts'][0]['available_at_ms'],now)
   history=sample['input_snapshot']['quotes']
   self.assertTrue(all(q['known_at_ms']<=sample['known_at_ms'] for q in history))
   self.assertTrue(all(b['t']-a['t']<=30000 for a,b in zip(history,history[1:])))
   target=sample['outcome_receipts'][0]['observed_at_ms']
   times=[q['t'] for q in quotes if sample['anchor_ms']<=q['t']<=target]
   self.assertTrue(all(b-a<=30000 for a,b in zip(times,times[1:])))
 def test_old_period_seed_cannot_be_relabelled(self):
  with self.assertRaisesRegex(ContractError,'SEED_SCOPE'):
   self.collect(seed_scope='b'*64+':EUR.USD')
 def test_collection_is_deterministic_for_same_cutoff_and_reservations(self):
  a,rows,_,_=self.collect();b,other,_,_=self.collect()
  self.assertEqual(a,b)
  self.assertEqual([row_id(r) for r in rows],[row_id(r) for r in other])


class PriceValueV3Tests(unittest.TestCase):
 def fixture(self):
  training,cutoff=fixtures.population()
  profile=refs.freeze(v3.primary_rows(training),cutoff)
  start=cutoff+60000
  plan={'candidate_id':'c','plan_id':'p','trial':1,'rule':copy.deepcopy(RULE),
        'start_ms':start,'outcome_end_ms':start+11*360000+300001,
        'price_baselines':profile,'price_value_rule':copy.deepcopy(refs.VALUE_RULE)}
  values={'laya_candidate':.4,'frozen_parent':.6,'uniform':6/7,'train_prior':.5,'linear_softmax':.55}
  results=[]
  for i in range(12):
   anchor=start+i*360000
   results.append({'candidate_id':'c','plan_id':'p','slot':i,'status':'OBSERVED',
     'target_class':i%2,'anchor_ms':anchor,'prediction_available_ms':anchor+1,
     'trained_until_ms':cutoff,'path_gap':False,'horizon_sec':300,
     'parent_brier':.6,'candidate_brier':.4,'candidate_hit':True,'bootstrap_hit':False,
     'price_baseline_profile_id':profile['profile_id'],
     'same_window_price_scores':{name:{'brier':value} for name,value in values.items()}})
  return plan,results,plan['outcome_end_ms']+1
 def test_parent_improvement_alone_cannot_hide_loss_to_simple_baseline(self):
  plan,results,now=self.fixture()
  for row in results:row['same_window_price_scores']['linear_softmax']['brier']=.3
  decision=assess(plan,results,now)
  self.assertEqual(decision['state'],'REJECTED')
  self.assertIn('NO_POSITIVE_BRIER_IMPROVEMENT_VS_LINEAR_SOFTMAX',decision['reasons'])
  self.assertLess(decision['paired_sign_p'],decision['alpha'])
  self.assertFalse(decision['same_window_price_value']['all_comparators_beaten'])
 def test_same_observed_300s_window_must_beat_every_comparator(self):
  plan,results,now=self.fixture();decision=assess(plan,results,now)
  self.assertEqual(decision['state'],'QUALIFIED_AWAITING_CANONICAL_OWNER')
  self.assertEqual(set(decision['same_window_price_value']['comparisons']),{'uniform','train_prior','linear_softmax'})
  self.assertTrue(decision['same_window_price_value']['all_comparators_beaten'])
  self.assertFalse(decision['promotion_performed'])
  self.assertFalse(decision['same_window_price_value']['execution_profitability_proven'])
 def test_paired_diagnostic_reports_raw_count_and_conditional_uncertainty(self):
  plan,results,now=self.fixture();before=copy.deepcopy(plan)
  decision=assess(plan,results,now)
  report=decision['same_window_price_value'];pair=report['comparisons']['linear_softmax']['paired_diagnostics']
  self.assertEqual(pair['paired_observations'],12)
  self.assertEqual((pair['wins'],pair['losses'],pair['ties']),(12,0,0))
  self.assertEqual(pair['exploratory_one_sided_sign_p'],1/4096)
  self.assertAlmostEqual(pair['median_brier_improvement'],.15)
  self.assertAlmostEqual(pair['sample_sd_brier_improvement'],0.)
  self.assertIsNone(report['uncertainty']['mean_confidence_interval'])
  self.assertIsNone(report['uncertainty']['effective_independent_sample_size'])
  self.assertFalse(report['uncertainty']['used_for_promotion'])
  self.assertFalse(report['uncertainty']['serial_dependence_adjusted'])
  self.assertEqual(plan,before)
  self.assertEqual(assess(plan,results,plan['outcome_end_ms']-1)['state'],'PENDING')
 def test_diagnostic_exposes_outlier_mean_without_changing_frozen_gate(self):
  plan,results,now=self.fixture()
  for row in results[:-1]:row['same_window_price_scores']['linear_softmax']['brier']=.39
  results[-1]['same_window_price_scores']['linear_softmax']['brier']=.8
  decision=assess(plan,results,now)
  comparison=decision['same_window_price_value']['comparisons']['linear_softmax']
  self.assertTrue(comparison['value_floor_passed'])
  self.assertGreater(comparison['mean_brier_improvement'],0.)
  self.assertLess(comparison['paired_diagnostics']['median_brier_improvement'],0.)
  self.assertEqual(comparison['paired_diagnostics']['wins'],1)
  self.assertEqual(comparison['paired_diagnostics']['losses'],11)
  self.assertGreater(comparison['paired_diagnostics']['exploratory_one_sided_sign_p'],.99)
  self.assertEqual(decision['state'],'QUALIFIED_AWAITING_CANONICAL_OWNER')
 def test_paired_ties_and_missing_observations_do_not_forge_sample_size(self):
  empty=refs.paired_brier_diagnostics([])
  self.assertEqual(empty['paired_observations'],0)
  self.assertIsNone(empty['median_brier_improvement'])
  self.assertIsNone(empty['sample_sd_brier_improvement'])
  pair=refs.paired_brier_diagnostics([.5,-.5,0.])
  self.assertEqual((pair['wins'],pair['losses'],pair['ties']),(1,1,1))
  self.assertEqual(pair['non_ties'],2)
  self.assertEqual(pair['exploratory_one_sided_sign_p'],.75)
  self.assertAlmostEqual(pair['sample_sd_brier_improvement'],.5)
 def test_censored_windows_stay_in_original_denominator(self):
  plan,results,now=self.fixture();decision=assess(plan,results[:8],now)
  self.assertEqual(decision['state'],'REJECTED')
  self.assertEqual(decision['censored'],4)
  self.assertEqual(decision['same_window_price_value']['observed'],8)
  self.assertEqual(decision['same_window_price_value']['comparisons']['uniform']['paired_diagnostics']['paired_observations'],8)
 def test_mismatched_horizon_or_missing_comparator_refuses(self):
  plan,results,now=self.fixture();results[0]['horizon_sec']=900
  with self.assertRaisesRegex(ContractError,'SAME_WINDOW'):assess(plan,results,now)
  plan,results,now=self.fixture();del results[0]['same_window_price_scores']['train_prior']
  with self.assertRaisesRegex(ContractError,'SCORE_POPULATION'):assess(plan,results,now)
 def test_relabelled_candidate_brier_refuses(self):
  plan,results,now=self.fixture();results[0]['same_window_price_scores']['laya_candidate']['brier']=.1
  with self.assertRaisesRegex(ContractError,'SCORE_CHANGED'):assess(plan,results,now)
 def test_old_plan_without_value_rule_keeps_original_qualification(self):
  plan,results,now=self.fixture();del plan['price_value_rule'];del plan['price_baselines']
  for row in results:row['same_window_price_scores']['linear_softmax']['brier']=.1
  decision=assess(plan,results,now)
  self.assertEqual(decision['state'],'QUALIFIED_AWAITING_CANONICAL_OWNER')
  self.assertNotIn('same_window_price_value',decision)
 def test_value_rule_cannot_be_changed_after_declaration(self):
  plan,results,now=self.fixture();plan['price_value_rule']['minimum_mean_improvement']=0
  with self.assertRaisesRegex(ContractError,'VALUE_RULE_CHANGED'):assess(plan,results,now)


class V3ConfigurationAndPlanTests(unittest.TestCase):
 def test_configuration_keeps_ledger_trial_history_and_native_identity(self):
  with tempfile.TemporaryDirectory() as td:
   config,runtime=fixtures.ExplicitConfigurationTests().fixture(Path(td))
   for name in v3.RUNTIME_PINS:
    path=runtime/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('cpu fixture module\n')
   before=copy.deepcopy(config);result=v3.configuration_for_release(config,runtime)
   self.assertEqual(config,before)
   self.assertEqual(result['objective'],v3.OBJECTIVE)
   for key in ('control_directory','evaluation_directory','candidate_directory','initial_parent_directory',
               'promotion_protocol','promotion_legacy_trial_floor','future_plan_admission_profile','profile'):
    self.assertEqual(result[key],config[key])
   self.assertEqual(result['native_binary_sha256'],config['native_binary_sha256'])
   self.assertEqual(result['objective']['training_horizons_seconds'],[300])
   for name in v3.RUNTIME_PINS:self.assertEqual(result['runtime_sha256'][name],sha_file(runtime/name))
 def test_only_new_v3_candidate_plan_declares_value_rule(self):
  from laya_cell.resident import Supervisor
  rows,cutoff=fixtures.population();profile=refs.freeze(v3.primary_rows(rows),cutoff)
  with tempfile.TemporaryDirectory() as td:
   directory=Path(td);(directory/'candidate.json').write_text('{}')
   service=object.__new__(Supervisor)
   service.c={'period_sha256':'a'*64,'runtime_sha256':{name:'b'*64 for name in v3.RUNTIME_PINS}}
   job={'output_directory':td,'parent_directory':td,'parent_id':'parent'}
   for policy in (v2,v3):
    candidate={'candidate_id':'candidate','objective':copy.deepcopy(policy.OBJECTIVE),
               'price_baselines':profile,'action_training_profile':None,'action_optimizer_admitted':False}
    plan=service._evaluation_plan('job',candidate,job,1,cutoff+60000)
    self.assertEqual(plan['rule']['horizon_ms'],300000)
    self.assertEqual('price_value_rule' in plan,policy is v3)
    if policy is v3:
     self.assertEqual(plan['price_value_rule'],refs.VALUE_RULE)
     self.assertTrue(set(v3.RUNTIME_PINS).issubset(plan['evaluation_runtime_sha256']))




class LateAnchorMaturityTests(unittest.TestCase):
    def test_original_late_input_boundary_precedes_selection_cap(self):
        from data import make_sample, label
        from laya_cell.resident_data import contiguous_history, mature_anchor_indices
        base = 1700000000000
        scope = 'a' * 64 + ':EUR.USD'
        for delay, selected_keys, expected_reason in (
                (60000, {5}, 'OBSERVED'),
                (60001, set(), 'INPUT_AVAILABLE_TOO_LATE')):
            with self.subTest(delay=delay):
                quotes = [
                    {'t': base + i * 10000, 'known_at_ms': base + i * 10000,
                     'bid': 1.09999, 'ask': 1.10001, 'mid': 1.1,
                     'receipt': format(i + 1, '064x')}
                    for i in range(37)]
                anchor = quotes[5]
                anchor['known_at_ms'] += delay
                cutoff = quotes[-1]['t']
                selected, _ = mature_anchor_indices(
                    quotes, quotes[:6], cutoff, scope, set())
                self.assertEqual(set(selected), selected_keys)
                if delay > 60000:
                    self.assertEqual(selected, {})
                history, _ = contiguous_history(
                    quotes, anchor['known_at_ms'], end=6)
                sample = make_sample(history, anchor['known_at_ms'], scope,
                                     'CAUSAL_QUOTE_WINDOW')
                label(sample, quotes, [q['t'] for q in quotes], cutoff)
                self.assertEqual(sample['label_reasons'][0], expected_reason)


if __name__=='__main__':unittest.main()
