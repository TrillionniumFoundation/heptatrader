"""Synthetic structure/recovery/qualification tests plus actual native bridge calls.

No simulated gain is operator trading evidence. No test submits any broker order.
"""
import copy,json,os,tempfile,time,unittest
from pathlib import Path
import torch
from torch import nn
from laya_cell.integrity import ContractError,digest,read_json
from laya_cell.resident_model import ResidentDecisionCell,MaskedActionLinear,answer_targets,action_loss
from laya_cell.resident_rewards import assemble
from laya_cell.resident_store import database,admit_job,complete_job,get_meta,put_meta,write_once
from laya_cell.promotion import RULE,assess
from laya_cell.resident_train import ResidentNative
from laya_cell.resident_evaluate import measure

class TinyPretrained(nn.Module):
 def __init__(self):
  super().__init__();torch.manual_seed(41)
  self.encoder=nn.Linear(3,768)
  self.scorer=nn.Sequential(nn.LayerNorm(768),nn.Linear(768,768),nn.GELU(),nn.Linear(768,1))
  self.act_head=nn.Sequential(nn.Linear(772,256),nn.GELU(),nn.Linear(256,2))
 def forward(self,x):
  h=self.encoder(x);features=h[:,None,:]+torch.arange(7)[None,:,None]*.01
  p=self.scorer(features).squeeze(-1);a=self.act_head(torch.cat([h,torch.ones(len(h),4)],-1))
  return p,a

class ActionHeadTests(unittest.TestCase):
 def setUp(self):self.base=TinyPretrained();self.x=torch.randn(4,3)
 def test_zero_delta_keeps_both_original_outputs(self):
  with torch.no_grad():p,a=self.base(self.x)
  cell=ResidentDecisionCell(self.base,'a'*64)
  with torch.no_grad():q,b=cell(x=self.x)
  torch.testing.assert_close(p,q);torch.testing.assert_close(a,b)
 def test_exact_trainable_mask_and_count(self):
  c=ResidentDecisionCell(self.base,'a'*64)
  names=[n for n,p in c.named_parameters() if p.requires_grad]
  self.assertEqual(sum(p.numel() for p in c.parameters() if p.requires_grad),2379)
  self.assertEqual(len(names),6);self.assertTrue(all(n.endswith(('.A','.B')) for n in names))
 def test_action_gradients_do_not_update_base(self):
  c=ResidentDecisionCell(self.base,'a'*64);opt=torch.optim.SGD([p for p in c.parameters() if p.requires_grad],lr=.01)
  before=c.trainable_state();p,a=c(x=self.x);loss=action_loss(a,torch.tensor([0,1,1,0]));loss.backward();opt.step()
  c.assert_frozen();self.assertFalse(torch.equal(before['model.act_head.2.B'],c.trainable_state()['model.act_head.2.B']))
 def test_action_update_is_confined_to_64_columns(self):
  a=MaskedActionLinear(nn.Linear(256,2))
  with torch.no_grad():a.B.fill_(.1)
  delta=a.full_effective_weight()-a.base.weight
  self.assertEqual(int(torch.count_nonzero(delta[:,64:])),0);self.assertGreater(int(torch.count_nonzero(delta[:,:64])),0)
 def test_upstream_output_shape_is_not_redefined(self):
  with self.assertRaisesRegex(ContractError,'ACT_ESCALATE'):MaskedActionLinear(nn.Linear(256,3))
 def test_ground_truth_correctness_not_confidence_or_buy_sell(self):
  p=torch.tensor([[9.,0,0,0,0,0,0],[0,0,0,0,0,0,9.]])
  self.assertEqual(answer_targets(p,torch.tensor([0,5])).tolist(),[0,1])
 def test_bad_action_labels_fail(self):
  with self.assertRaisesRegex(ContractError,'LOSS_INPUT'):action_loss(torch.zeros(1,2),torch.tensor([2]))
 def test_nonfinite_logits_fail(self):
  with self.assertRaisesRegex(ContractError,'LOSS_INPUT'):action_loss(torch.tensor([[float('nan'),1.]]),torch.tensor([0]))
 def test_extreme_upstream_logits_have_finite_training_loss(self):
  a=torch.tensor([[3000.,-3000.]],requires_grad=True);loss=action_loss(a,torch.tensor([1]));loss.backward()
  self.assertTrue(torch.isfinite(loss));self.assertTrue(torch.isfinite(a.grad).all())
 def test_scorer_only_parent_is_restorable_without_random_backbone(self):
  from laya_cell.model import TradingDecisionCell
  old=TradingDecisionCell(TinyPretrained(),'a'*64);state=old.trainable_state()
  c=ResidentDecisionCell(self.base,'a'*64);c.restore_parent(state,allow_scorer_only=True)
  self.assertTrue(torch.equal(c.trainable_state()['model.scorer.1.A'],state['model.scorer.1.A']))
 def test_missing_parent_tensor_fails(self):
  c=ResidentDecisionCell(self.base,'a'*64);s=c.trainable_state();s.pop('model.scorer.1.A')
  with self.assertRaises(ContractError):c.restore_parent(s)
 def test_no_tensor_outside_declared_mask_gets_gradients(self):
  c=ResidentDecisionCell(self.base,'a'*64);p,a=c(x=self.x);(p.sum()+a.sum()).backward()
  self.assertTrue(all(v.grad is None for _,v in c.named_parameters() if not v.requires_grad))

class RewardTests(unittest.TestCase):
 def report(self):
  return {'fees_complete':True,'settlement_pending_command_ids':[],
   'executions':[{'execution_id':'e1','command_id':'open1','quantity':'2','price':'100','side':'BUY','commission':'1','commission_currency':'USD'},
                 {'execution_id':'e2','command_id':'close1','quantity':'2','price':'110','side':'SELL','commission':'1','commission_currency':'USD'}]}
 def bindings(self):return {'open1':{'input_id':'input','forecast_id':'forecast','model_id':'remote-not-laya'}}
 def test_actual_commission_deducted_exactly_once(self):
  r,b=assemble(self.report(),self.bindings(),'period');self.assertEqual(r[0]['realized_net_usd'],'18');self.assertEqual(b,[])
 def test_negative_net_reward_retained(self):
  p=self.report();p['executions'][1]['price']='90';r,_=assemble(p,self.bindings(),'period');self.assertEqual(r[0]['realized_net_usd'],'-22')
 def test_missing_fee_not_zero(self):
  p=self.report();p['executions'][0]['commission']=None
  with self.assertRaisesRegex(ContractError,'MISSING_ACTUAL_FEE'):assemble(p,self.bindings(),'period')
 def test_incomplete_fee_stream_never_reward(self):
  p=self.report();p['fees_complete']=False;r,b=assemble(p,self.bindings(),'period');self.assertEqual(r,[]);self.assertTrue(b)
 def test_unsettled_original_command_never_reward(self):
  p=self.report();p['settlement_pending_command_ids']=['close1'];self.assertEqual(assemble(p,self.bindings(),'period')[0],[])
 def test_unbound_input_not_invented(self):self.assertEqual(assemble(self.report(),{},'period')[0],[])
 def test_open_position_not_closed_reward(self):
  p=self.report();p['executions']=p['executions'][:1];r,b=assemble(p,self.bindings(),'period');self.assertEqual(r,[]);self.assertEqual(b[0]['reason'],'OPEN_RESIDUAL_POSITION')
 def test_duplicate_execution_never_double_credited(self):
  p=self.report();p['executions'].append(p['executions'][0])
  with self.assertRaisesRegex(ContractError,'DUPLICATE'):assemble(p,self.bindings(),'period')
 def test_offpolicy_remote_profit_not_laya_policy_gradient(self):
  r,_=assemble(self.report(),self.bindings(),'period');self.assertFalse(r[0]['laya_on_policy']);self.assertFalse(r[0]['policy_gradient_allowed'])
 def test_cross_zero_needs_exact_lot_allocation(self):
  p=self.report();p['executions'][1]['quantity']='3'
  with self.assertRaisesRegex(ContractError,'LOT_SPLIT'):assemble(p,self.bindings(),'period')
 def test_fee_currency_must_be_known(self):
  p=self.report();p['executions'][1]['commission_currency']='EUR'
  with self.assertRaises(ContractError):assemble(p,self.bindings(),'period')

class DurableSchedulerTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'state.sqlite';self.db=database(self.path);self.addCleanup(self.db.close)
 def test_repeat_fingerprint_is_idempotent(self):
  self.assertTrue(admit_job(self.db,'j','fp',100,{'x':1},['r1']))
  self.assertFalse(admit_job(self.db,'j','fp',101,{'x':1},['r1']))
  self.assertEqual(self.db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)
 def test_consumed_rows_cannot_be_renamed_and_retrained(self):
  admit_job(self.db,'j','fp',100,{},['r1'])
  with self.assertRaisesRegex(ContractError,'ALREADY_CONSUMED'):admit_job(self.db,'j2','fp2',200,{},['r1'])
 def test_cadence_survives_reopen(self):
  admit_job(self.db,'j','fp',100,{},['r1']);other=database(self.path)
  self.assertEqual(get_meta(other,'last_job_created_ms'),100);other.close()
 def test_candidate_completion_is_exactly_once(self):
  admit_job(self.db,'j','fp',100,{},['r1']);c={'candidate_id':'c1'}
  self.assertTrue(complete_job(self.db,'j',c));self.assertFalse(complete_job(self.db,'j',c))
  self.assertEqual(get_meta(self.db,'last_completed_candidate'),'c1')
 def test_same_job_cannot_change_candidate(self):
  admit_job(self.db,'j','fp',100,{},['r1']);complete_job(self.db,'j',{'candidate_id':'c1'})
  with self.assertRaisesRegex(ContractError,'CANDIDATE_CHANGED'):complete_job(self.db,'j',{'candidate_id':'c2'})
 def test_unadmitted_candidate_cannot_complete(self):
  with self.assertRaisesRegex(ContractError,'NO_ADMITTED'):complete_job(self.db,'none',{'candidate_id':'c'})
 def test_artifact_never_overwritten(self):
  p=self.path.with_name('artifact.json');write_once(p,{'x':1});write_once(p,{'x':1})
  with self.assertRaisesRegex(ContractError,'IMMUTABLE'):write_once(p,{'x':2})

class QualificationTests(unittest.TestCase):
 def plan(self):return {'candidate_id':'c','plan_id':'p','trial':1,'rule':copy.deepcopy(RULE),'outcome_end_ms':99999999}
 def results(self):
  return [{'candidate_id':'c','slot':i,'status':'OBSERVED','target_class':i%2,'anchor_ms':i*360000+100,
    'prediction_available_ms':i*360000+1000,'trained_until_ms':0,'path_gap':False,
    'parent_brier':.6,'candidate_brier':.4,'candidate_hit':True,'bootstrap_hit':False} for i in range(12)]
 def test_positive_future_evidence_is_not_canonical_acceptance(self):
  r=assess(self.plan(),self.results(),100000000)
  self.assertEqual(r['state'],'QUALIFIED_AWAITING_CANONICAL_OWNER');self.assertFalse(r['promotion_performed']);self.assertFalse(r['canonical_owner_admitted'])
 def test_window_not_finished_cannot_qualify(self):self.assertEqual(assess(self.plan(),self.results(),10)['state'],'PENDING')
 def test_missing_outcomes_not_deleted_from_denominator(self):
  r=assess(self.plan(),self.results()[:8],100000000);self.assertEqual(r['state'],'REJECTED');self.assertEqual(r['censored'],4)
 def test_worse_candidate_fails(self):
  rs=self.results()
  for r in rs:r['candidate_brier']=.7
  self.assertEqual(assess(self.plan(),rs,100000000)['state'],'REJECTED')
 def test_uniform_outcomes_do_not_prove_general_improvement(self):
  rs=self.results()
  for r in rs:r['target_class']=3
  self.assertIn('INSUFFICIENT_OUTCOME_VARIATION',assess(self.plan(),rs,100000000)['reasons'])
 def test_lookahead_rejected(self):
  rs=self.results();rs[0]['trained_until_ms']=101
  self.assertIn('LOOKAHEAD',assess(self.plan(),rs,100000000)['reasons'])
 def test_overlapping_outcomes_rejected(self):
  rs=self.results();rs[1]['anchor_ms']=100
  self.assertIn('OVERLAPPING_OUTCOME_WINDOWS',assess(self.plan(),rs,100000000)['reasons'])
 def test_changed_policy_rejected(self):
  p=self.plan();p['rule']['min_observed']=1
  with self.assertRaisesRegex(ContractError,'RULE_CHANGED'):assess(p,self.results(),100000000)
 def test_wrong_candidate_rejected(self):
  rs=self.results();rs[0]['candidate_id']='other'
  with self.assertRaisesRegex(ContractError,'CANDIDATE'):assess(self.plan(),rs,100000000)
 def test_duplicate_slot_rejected(self):
  rs=self.results();rs[1]['slot']=0
  with self.assertRaisesRegex(ContractError,'DUPLICATE'):assess(self.plan(),rs,100000000)
 def test_repeated_trials_spend_finite_alpha(self):
  p=self.plan();a=assess(p,self.results(),100000000);p['trial']=2;b=assess(p,self.results(),100000000)
  self.assertLess(b['alpha'],a['alpha']);self.assertLess(sum(.05/(n*(n+1)) for n in range(1,10000)),.05)
 def test_good_mean_without_paired_evidence_fails(self):
  rs=self.results()
  for r in rs[:6]:r['candidate_brier']=.61
  self.assertIn('PAIRED_SIGN_EVIDENCE_INSUFFICIENT',assess(self.plan(),rs,100000000)['reasons'])

class NativeResidentTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  c=read_json(Path(os.environ['LAYA_RESIDENT_TEST_CONFIG']));cls.native=ResidentNative(Path(c['native_binary']),c['native_binary_sha256'])
 def request(self):
  return {'op':'laya_resident_proposal','generation':1,
   **{k:'1'*64 for k in ('base_id','selected_artifact_digest','dataset_digest','update_rule_digest','modulator_digest','eligibility_digest','evaluation_digest')},
   'layers':[{'name':n,'shape':shape,'baseline_squared_q64':str(2**85),'deltas':[]} for n,shape in (('scorer.1',[768,768]),('scorer.3',[1,768]),('act_head.2',[2,256]))]}
 def test_real_native_action_mask_accepts_bounded_delta(self):
  r=self.request();r['layers'][2]['deltas']=[{'row':1,'col':63,'delta_q32':100}]
  x=self.native.call(r);self.assertEqual(x['candidate_count'],2);self.assertFalse(x['selection_performed'])
 def test_action_column_outside_mask_rejected(self):
  r=self.request();r['layers'][2]['deltas']=[{'row':1,'col':64,'delta_q32':100}]
  with self.assertRaisesRegex(ContractError,'DELTA_MASK'):self.native.call(r)
 def test_unregistered_action_shape_rejected(self):
  r=self.request();r['layers'][2]['shape']=[3,256]
  with self.assertRaisesRegex(ContractError,'SHAPE'):self.native.call(r)
 def test_same_coordinate_across_layers_is_not_duplicate_identity(self):
  r=self.request()
  for layer in r['layers']:layer['deltas']=[{'row':0,'col':0,'delta_q32':100}]
  self.assertEqual(self.native.call(r)['candidate_count'],2)
 def test_duplicate_coordinate_same_layer_rejected(self):
  r=self.request();r['layers'][2]['deltas']=[{'row':0,'col':0,'delta_q32':100}]*2
  with self.assertRaisesRegex(ContractError,'DELTA_MASK'):self.native.call(r)

if __name__=='__main__':torch.set_num_threads(1);unittest.main(verbosity=2)
