"""Synthetic invariants; no fabricated market qualification or orders."""
from __future__ import annotations
import copy,json,tempfile,time,unittest
from pathlib import Path
import torch
from torch.nn import functional as F
from laya_cell.integrity import digest,ContractError
from paired_price.common import RULE,PROFILE,ARMS,check_candidate,check_plan,require
from paired_price.model import PairModel
from paired_price.evaluate import score,summarize,sign_p

def plan():
 p={'schema':'heptatrader.paired-price-prospective-plan.v1','registered_at_ms':10000,'start_ms':10010,'end_ms':10010+RULE['slots']*RULE['slot_spacing_ms']+350000,
  'candidate_created_at_ms':9000,'candidate_id':'c'*64,'period_sha256':'1'*64,'rule':RULE,
  'runtime_sha256':{},'training_expert_ids':[],'training_gate_ids':[],
  'numeric_independent':True,'legacy_numeric_frozen_comparator':'d'*64,'candidate_model_sha256':'e'*64,
  'effect_authority':False,'automatic_selection':False}
 p['plan_id']=digest(p);return p

class IndependenceTests(unittest.TestCase):
 def setUp(self):
  torch.set_num_threads(1);torch.manual_seed(2)
 def test_separate_experts_freeze_before_gate(self):
  model=PairModel();model.freeze_experts();baseline={k:v.detach().clone() for k,v in model.state_dict().items() if k.startswith(('numeric.','semantic.'))}
  opt=torch.optim.SGD(model.gate.parameters(),lr=.001)
  seq=torch.randn(4,8,16);length=torch.tensor([8,7,6,5]);features=F.normalize(torch.randn(4,768),dim=-1)
  pred=model.predict(seq,length,features,torch.full((4,7),1/7));target=torch.tensor([1,2,3,4])
  opt.zero_grad(set_to_none=True);loss=F.nll_loss(pred['gate'].log(),target);loss.backward();opt.step()
  for k,v in baseline.items():self.assertTrue(torch.equal(v,model.state_dict()[k]))
 def test_probs_are_convex_mixtures(self):
  model=PairModel();x=model.predict(torch.randn(3,8,16),torch.tensor([8,7,5]),F.normalize(torch.randn(3,768),dim=-1),torch.full((3,7),1/7))
  torch.testing.assert_close(x['fixed_mix'],(x['numeric']+x['semantic'])/2)
  torch.testing.assert_close(x['gate'].sum(-1),torch.ones(3))
  self.assertEqual(set(ARMS),{'numeric','semantic','fixed_mix','gate','legacy_numeric'})
 def test_gate_uses_full_768_representation_not_only_seven_probs(self):
  model=PairModel();self.assertEqual(model.semantic.in_features,768)
  self.assertEqual(model.projection.shape,(768,32))
 def test_no_automatic_trade_or_selection_api(self):
  model=PairModel();self.assertFalse(hasattr(model,'submit'));self.assertFalse(hasattr(model,'select_candidate'))
 def test_gapped_or_lookahead_window_fails_at_plan(self):
  p=plan()
  with self.assertRaises(ContractError):check_plan({**p,'start_ms':1})
 def test_candidate_tamper_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'pair.safetensors').write_text('fake')
   obj={'profile':PROFILE,'artifacts':{'pair.safetensors':'0'*64},'effect_authority':False}
   obj['candidate_id']=digest(obj);(root/'candidate.json').write_text(json.dumps(obj))
   with self.assertRaisesRegex(ContractError,'PINNED_BYTES'):check_candidate(root)

class FutureEvaluatorTests(unittest.TestCase):
 def test_new_window_pending_not_early_pass(self):
  p=plan();x=summarize(p,[],p['start_ms']+1000)
  self.assertEqual(x['state'],'PENDING');self.assertFalse(x['automatic_selection'])
 def test_unobserved_denominator_never_passes(self):
  p=plan();x=summarize(p,[],p['end_ms']+1)
  self.assertEqual(x['state'],'REJECTED');self.assertEqual(x['registered_slots'],24)
 def test_invalid_probability_rejected(self):
  for v in ([float('nan')]+[0]*6,[.2]*7,[1.1]+[0]*6):
   with self.assertRaises(ContractError):score(v,1)
 def test_one_sided_alpha_bound_requires_many_samples(self):
  self.assertGreater(sign_p(8,10),RULE['alpha_each'])
  self.assertLess(sign_p(19,20),RULE['alpha_each'])
 def test_previous_rejection_not_claimed_repaired(self):
  self.assertEqual(RULE['min_observed'],20)
  self.assertEqual(set(RULE['comparators']),{'numeric','semantic','fixed_mix','legacy_numeric'})
 def test_holdout_gate_results_are_not_prospective_evidence(self):
  self.assertTrue(PROFILE['gate_fits_after_expert_freeze'])
  self.assertGreater(PROFILE['purge_ms'],PROFILE['horizon_sec']*1000)
 def test_missing_forecasts_cannot_inflate_coverage(self):
  p=plan()
  value=summarize(p,[{'status':'CENSORED','slot':0,'reason':'INVALID_PREDICTION:missing'}],p['end_ms']+1)
  self.assertEqual(value['state'],'REJECTED')
  self.assertEqual(value['censored'],1)
 def test_runtime_rule_has_no_selection(self):
  self.assertIs(RULE['automatic_selection'],False)
  self.assertIs(RULE['effects_authorized'],False)
 def test_zero_sample_does_not_invent_gain(self):
  for baseline in RULE['comparators']:
   x=summarize(plan(),[],plan()['end_ms']+1)
   self.assertIsNone(x['comparisons'][baseline]['mean_brier_gain'])
 def test_future_comparator_all_good_is_still_only_screen(self):
  p=plan();observed=[]
  for i in range(RULE['slots']):
   observed.append({'status':'OBSERVED','anchor_ms':p['start_ms']+i*RULE['slot_spacing_ms'],
    'target_class':i%3,'brier':{'numeric':.7,'semantic':.9,'fixed_mix':.8,'legacy_numeric':.85,'gate':.5}})
  v=summarize(p,observed,p['end_ms']+1)
  self.assertEqual(v['state'],'EXPERIMENTAL_GAIN_SCREENED_NO_SELECTION')
  self.assertFalse(v['canonical_owner_accepted'])
if __name__=='__main__':unittest.main(verbosity=2)
