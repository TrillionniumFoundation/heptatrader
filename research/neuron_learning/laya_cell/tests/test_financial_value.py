"""Financial readout correctness; synthetic cases are not trading evidence."""
import copy,unittest
import torch
from laya_cell.financial_value_data import to_row,PROFILE
from laya_cell.financial_value_train import FinancialHead,train_head,load_value_head,prediction_usd
from laya_cell.financial_value_profile import CENTERED_RESIDUAL_PROFILE
from laya_cell.integrity import digest,ContractError

def fixture():
 request={'request_id':'request','instrument':'EUR.USD','model_id':'remote-model','started_at_ms':100,
          'prompt':'ORIGINAL CAUSAL QUOTES ONLY','history_snapshot':[],'position_at_request':0}
 r={'schema':'heptatrader.observed-execution-reward.v2','episode_id':'episode','instrument':'EUR.USD','currency':'USD',
  'learning_eligible':True,'exact_input_bound':True,'settlement_complete':True,'fees_complete':True,
  'learning_exclusion_reasons':[],'policy_gradient_allowed':False,'laya_on_policy':False,
  'behavior_model_id':'remote-model','input_id':digest(request),'input_attribution':{'request_id':'request'},
  'open_command_id':'open','realized_gross_usd':'-7.50000','actual_commissions_usd':'4','realized_net_usd':'-11.50000',
  'executions':[{'command_id':'open','execution_id':'e1','side':'SELL','quantity':'25000','price':'1.11886','commission':'2','commission_currency':'USD'},
   {'command_id':'close','execution_id':'e2','side':'BUY','quantity':'25000','price':'1.11916','commission':'2','commission_currency':'USD'}]}
 r['reward_id']=digest(r);return r,request

def rehash(r):
 r.pop('reward_id',None);r['reward_id']=digest(r);return r

class ValueDataTests(unittest.TestCase):
 def test_exact_negative_net_not_gross_target(self):
  r,req=fixture();row=to_row(r,request=req);self.assertEqual(row['target_scaled'],-.115);self.assertEqual(row['net_usd'],'-11.50000')
 def test_label_not_leaked_to_feature_text(self):
  r,req=fixture();s=to_row(r,request=req)['feature_text']
  for hidden in ('-11.50000','1.11916','e2','close','-7.50000'):self.assertNotIn(hidden,s)
  self.assertIn(req['prompt'],s);self.assertIn('selected action=SELL',s)
 def test_unknown_fee_not_zero(self):
  r,req=fixture();r['executions'][0]['commission']=None
  with self.assertRaises(ContractError):to_row(rehash(r),request=req)
 def test_original_input_hash_must_match(self):
  r,req=fixture();req['prompt']='changed'
  with self.assertRaisesRegex(ContractError,'INPUT_MISMATCH'):to_row(r,request=req)
 def test_unsettled_or_unbound_cannot_train(self):
  for field in ('learning_eligible','exact_input_bound','settlement_complete','fees_complete'):
   r,req=fixture();r[field]=False
   with self.assertRaisesRegex(ContractError,'BOUND_SETTLED'):to_row(rehash(r),request=req)
 def test_duplicate_execution_not_double_reward(self):
  r,req=fixture();r['executions'].append(r['executions'][0])
  with self.assertRaisesRegex(ContractError,'DUPLICATE'):to_row(rehash(r),request=req)
 def test_economic_claim_recomputed_not_trusted(self):
  r,req=fixture();r['realized_net_usd']='11.5'
  with self.assertRaisesRegex(ContractError,'ECONOMIC'):to_row(rehash(r),request=req)
 def test_open_residual_is_not_roundtrip(self):
  r,req=fixture();r['executions'][1]['quantity']='24999'
  with self.assertRaisesRegex(ContractError,'ECONOMIC'):to_row(rehash(r),request=req)
 def test_quantity_change_not_same_value_profile(self):
  r,req=fixture()
  for e in r['executions']:e['quantity']='1'
  with self.assertRaisesRegex(ContractError,'ECONOMIC'):to_row(rehash(r),request=req)
 def test_missing_model_identity_not_guessed(self):
  r,req=fixture();r['behavior_model_id']='some-other-model'
  with self.assertRaisesRegex(ContractError,'INPUT_MISMATCH'):to_row(rehash(r),request=req)
 def test_reward_hash_changes_if_manipulated(self):
  r,req=fixture();r['realized_net_usd']='0'
  with self.assertRaisesRegex(ContractError,'REWARD_HASH'):to_row(r,request=req)
 def test_unsupported_instrument_or_currency_refused(self):
  for k,v in (('instrument','BTC.USD'),('currency','EUR')):
   r,req=fixture();r[k]=v
   with self.assertRaisesRegex(ContractError,'BOUND_SETTLED'):to_row(rehash(r),request=req)
 def test_onpolicy_or_actor_permission_never_accepted(self):
  for k in ('policy_gradient_allowed','laya_on_policy'):
   r,req=fixture();r[k]=True
   with self.assertRaisesRegex(ContractError,'BOUND_SETTLED'):to_row(rehash(r),request=req)

class HeadTests(unittest.TestCase):
 def features(self):
  torch.manual_seed(9);x=torch.randn(1,768);return torch.nn.functional.normalize(x,dim=1)
 def test_new_monetary_head_neutral_zero_not_pretrained_claim(self):
  h=FinancialHead('cpu');self.assertEqual(sum(p.numel() for p in h.parameters()),769);self.assertEqual(float(h(self.features())[0]),0.)
 def test_negative_actual_reward_drives_negative_prediction(self):
  h,r=train_head(self.features(),torch.tensor([-.115]));self.assertLess(r['after_scaled'][0],0)
  self.assertLess(r['huber_after'],r['huber_before']);self.assertGreater(r['changed_parameters'],0)
 def test_positive_reward_drives_positive_prediction(self):
  h,r=train_head(self.features(),torch.tensor([.2]));self.assertGreater(r['after_scaled'][0],0)
 def test_no_gradient_enters_frozen_features(self):
  x=self.features().requires_grad_(True);train_head(x,torch.tensor([-.115]));self.assertIsNone(x.grad)
 def test_absolute_zero_baseline_norm_bound(self):
  _,r=train_head(self.features(),torch.tensor([7.]));self.assertLessEqual(r['effective_update_l2'],.100001)
 def test_nonfinite_target_refused(self):
  with self.assertRaisesRegex(ContractError,'NONFINITE'):train_head(self.features(),torch.tensor([float('nan')]))
 def test_out_of_range_target_not_clipped_into_fake_reward(self):
  with self.assertRaisesRegex(ContractError,'TARGET_BOUND'):train_head(self.features(),torch.tensor([9.]))
 def test_incompatible_tensor_shape_rejected(self):
  with self.assertRaisesRegex(ContractError,'TENSOR_SHAPE'):train_head(torch.zeros(1,16),torch.zeros(1))
 def test_repeat_initialization_deterministic(self):
  _,a=train_head(self.features(),torch.tensor([-.115]));_,b=train_head(self.features(),torch.tensor([-.115]));self.assertEqual(a['after_scaled'],b['after_scaled'])
 def test_zero_reward_not_manufactured_negative(self):
  _,r=train_head(self.features(),torch.tensor([0.]));self.assertEqual(r['changed_parameters'],0)

class CenteredHeadTests(unittest.TestCase):
 def near_common_features(self):
  x=torch.zeros(8,768);x[:,0]=1.;x[:,1]=torch.tensor([-1e-4,1e-4]*4)
  return torch.nn.functional.normalize(x,dim=1)
 def fit(self,x,y):return train_head(x,y,readout_profile=CENTERED_RESIDUAL_PROFILE)
 def test_legacy_constant_offset_matches_finite_step_mechanism(self):
  x=torch.zeros(8,768);x[:,0]=1.;y=torch.full((8,),-.1252655)
  _,r=train_head(x,y)
  expected=float(y.mean())*(1.-.9**16)
  self.assertAlmostEqual(r['after_scaled'][0],expected,places=7)
  self.assertNotIn('readout_profile',r)
 def test_centering_exposes_input_differences_with_same_sixteen_steps(self):
  x=self.near_common_features();y=torch.tensor([-.08,.08]*4)
  _,legacy=train_head(x,y);head,result=self.fit(x,y)
  self.assertEqual(len(result['steps']),16)
  self.assertEqual(result['trainable_parameters'],769)
  self.assertLess(result['effective_update_l2'],.100001)
  self.assertLess(result['training_mae_usd'],result['training_mean_baseline_mae_usd'])
  self.assertGreater(result['prediction_std_usd'],1.)
  self.assertLess(float(torch.tensor(legacy['after_scaled']).std(unbiased=False)*100),.001)
  self.assertTrue(result['training_fit_is_not_efficacy'])
 def test_constant_inputs_do_not_invent_discrimination(self):
  x=torch.zeros(8,768);x[:,0]=1.;y=torch.tensor([-.08,.08]*4)-.02
  head,result=self.fit(x,y)
  self.assertAlmostEqual(result['prediction_std_usd'],0.,places=6)
  self.assertTrue(torch.allclose(head(x),y.mean().expand_as(y),atol=1e-7))
  self.assertAlmostEqual(result['training_mae_usd'],result['training_mean_baseline_mae_usd'],places=5)
 def test_serialized_train_geometry_is_frozen_for_future_batches(self):
  x=self.near_common_features();head,result=self.fit(x,torch.tensor([-.08,.08]*4))
  state={k:v.detach().clone() for k,v in head.state_dict().items()}
  restored=load_value_head(state,'cpu',readout_profile=CENTERED_RESIDUAL_PROFILE)
  future=torch.roll(x,1,0)
  self.assertTrue(torch.equal(prediction_usd(head,future,readout_profile=CENTERED_RESIDUAL_PROFILE),
                              prediction_usd(restored,future,readout_profile=CENTERED_RESIDUAL_PROFILE)))
  self.assertTrue(torch.equal(restored(future[:1]),restored(future)[:1]))
  for key,value in state.items():self.assertTrue(torch.equal(value,restored.state_dict()[key]))
  self.assertFalse(any(p.requires_grad for p in restored.parameters()))
 def test_unchanged_absolute_dollar_bound_and_unclipped_labels(self):
  x=self.near_common_features();head,result=self.fit(x,torch.full((8,),7.))
  self.assertTrue((prediction_usd(head,x,readout_profile=CENTERED_RESIDUAL_PROFILE).abs()<=15.).all())
  self.assertAlmostEqual(result['training_mean_baseline_mae_usd'],0.,places=6)
  self.assertAlmostEqual(result['zero_baseline_mae_usd'],700.,places=4)
  self.assertGreater(result['training_mae_usd'],600.)
  self.assertGreater(result['huber_before'],1.)
 def test_future_loader_rejects_profile_or_frozen_geometry_substitution(self):
  head,_=self.fit(self.near_common_features(),torch.tensor([-.08,.08]*4))
  saved={k:v.detach().clone() for k,v in head.state_dict().items()}
  with self.assertRaisesRegex(ContractError,'TENSOR_SCOPE'):load_value_head(saved,'cpu')
  with self.assertRaisesRegex(ContractError,'PROFILE_CHANGED'):
   load_value_head(saved,'cpu',readout_profile={**CENTERED_RESIDUAL_PROFILE,'steps':17})
  for key,value in (('feature_rms',torch.tensor(0.)),('baseline_scaled',torch.tensor(.2)),
                    ('feature_center',torch.ones(768))):
   with self.subTest(key=key):
    changed={**saved,key:value}
    with self.assertRaisesRegex(ContractError,'GEOMETRY_BOUND'):
     load_value_head(changed,'cpu',readout_profile=CENTERED_RESIDUAL_PROFILE)
 def test_training_does_not_backpropagate_into_inputs_or_targets(self):
  x=self.near_common_features().requires_grad_(True);y=torch.tensor([-.08,.08]*4,requires_grad=True)
  self.fit(x,y);self.assertIsNone(x.grad);self.assertIsNone(y.grad)


if __name__=='__main__':torch.set_num_threads(1);unittest.main(verbosity=2)
