"""Synthetic contract tests only; no market efficacy or order generation."""
from __future__ import annotations
from dataclasses import replace,asdict
import copy,math,sys,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import torch
from neuron_graph_v2.contracts import *
from neuron_graph_v2.observers import Quote,ExecutionFact,market_observers as _market_observers,execution_observer as _execution_observer
from neuron_graph_v2.graph import GraphMetaNeuron,seal_output
from neuron_graph_v2.protocol import Candidate,PairedPlan,Prediction,Outcome,summarize

T=1791415000000
H='1'*64

def market_observers(*args,**kwargs):
 kwargs.setdefault('algorithm_frozen_at_ms',T-2000)
 return _market_observers(*args,**kwargs)
def execution_observer(*args,**kwargs):
 kwargs.setdefault('algorithm_frozen_at_ms',T-2000)
 return _execution_observer(*args,**kwargs)

def context():return Context('paper-cycle','EUR.USD',H,T,T+100,300000,'2'*64,'3'*64)
def node(name='laya',probs=None):
 p=tuple(probs or [1/7]*7)
 emb=tuple([1.]+[0.]*255)
 return NeuronOutput(SCHEMA,name,'price','4'*64,context(),emb,'5'*64,p,
   Uncertainty('normalized_class_entropy',entropy(p)),(),T+100,T-1000,T+200,T+5000,('6'*64,))
def quotes():
 return [Quote(T-(5-i)*10000,T-(5-i)*10000+20,1.1+i*.00001,1.10002+i*.00001,format(i+1,'064x')) for i in range(6)]
def graph_nodes():
 a=node('laya',[.1,.1,.1,.4,.1,.1,.1]);b=node('numeric',[.05,.05,.1,.5,.1,.1,.1])
 reg,risk=market_observers(context(),quotes(),T+200,T+5000)
 exc=execution_observer(context(),[],T+200,T+5000)
 return [a,b,reg,risk,exc]

class ContractTests(unittest.TestCase):
 def test_native_256_packet(self):self.assertEqual(len(node().validate(T+300).embedding),256)
 def test_32_is_not_256(self):
  with self.assertRaisesRegex(ContractError,'256'):replace(node(),embedding=(1.,)+(0.,)*31).validate(T+300)
 def test_dimensional_upgrade_preserves_norm_and_inner_product(self):
  a=[1.]+[0.]*31;b=[.6,.8]+[0.]*30
  aa=lift_legacy32(a);bb=lift_legacy32(b)
  self.assertAlmostEqual(sum(x*x for x in aa),1.)
  self.assertAlmostEqual(sum(x*y for x,y in zip(aa,bb)),.6)
  self.assertEqual(len(aa),256)
 def test_nonfinite_input_rejected(self):
  for value in (float('nan'),float('inf'),True):
   with self.subTest(value=value),self.assertRaises(ContractError):replace(node(),embedding=(value,)+(0.,)*255).validate(T+300)
 def test_bad_simplex_rejected(self):
  with self.assertRaises(ContractError):replace(node(),probabilities=(.2,)*7).validate(T+300)
 def test_descriptive_entropy_not_win_probability(self):
  with self.assertRaisesRegex(ContractError,'NO_CALIBRATION'):replace(node(),uncertainty=Uncertainty('normalized_class_entropy',1.,True)).validate(T+300)
 def test_entropy_tampering_rejected(self):
  with self.assertRaises(ContractError):replace(node(),uncertainty=Uncertainty('normalized_class_entropy',.1)).validate(T+300)
 def test_no_effect_permission_field(self):
  for value in (True,1,None):
   with self.subTest(value=value),self.assertRaises(ContractError):replace(node(),effect_authority=value).validate(T+300)
 def test_time_fields_not_bool_or_float(self):
  for value in (True,1.5):
   with self.subTest(value=value),self.assertRaises(ContractError):replace(node(),source_cutoff_ms=value).validate(T+300)
 def test_future_weights_or_future_inputs_rejected(self):
  for changed in ({'model_frozen_at_ms':T+1},{'source_cutoff_ms':T+101}):
   with self.subTest(changed=changed),self.assertRaisesRegex(ContractError,'FUTURE'):replace(node(),**changed).validate(T+300)
 def test_result_after_target_is_not_predictive(self):
  with self.assertRaises(ContractError):replace(node(),available_at_ms=T+300001,valid_until_ms=T+400000).validate(T+300001)
 def test_expired_output_rejected(self):
  with self.assertRaisesRegex(ContractError,'STALE'):node().validate(T+5001)
 def test_missing_evidence_rejected(self):
  with self.assertRaises(ContractError):replace(node(),evidence_sha256=()).validate(T+300)
 def test_digest_requires_hex(self):
  with self.assertRaises(ContractError):replace(node(),model_sha256='z'*64).validate(T+300)
 def test_align_rejects_every_incompatible_scope(self):
  c=context()
  changes=[{'period_id':'other'},{'instrument':'BTC.USD'},{'input_sha256':'a'*64},{'horizon_ms':600000},
           {'behavior_policy_sha256':'b'*64},{'quantity_policy_sha256':'c'*64}]
  for change in changes:
   with self.subTest(change=change),self.assertRaises(ContractError):align([node('a'),replace(node('b'),context=replace(c,**change))],T+300)
 def test_duplicate_model_node_names_rejected(self):
  with self.assertRaisesRegex(ContractError,'DUPLICATE'):align([node(),node()],T+300)
 def test_wire_is_plain_256_and_contains_no_secret(self):
  wire=node().wire();self.assertEqual(len(wire['embedding']),256);self.assertFalse(wire['effect_authority'])
 def test_wire_roundtrip(self):
  n=node();self.assertEqual(decode_packet(encode_packet(n,T+300),T+300),n)
 def test_wire_does_not_accept_extra_authority(self):
  v=node().wire();v['can_trade']=True
  with self.assertRaisesRegex(ContractError,'WIRE_FIELDS'):decode_packet(json.dumps(v).encode(),T+300)
 def test_wire_duplicate_key_rejected(self):
  raw=encode_packet(node(),T+300).replace(b'"effect_authority":false',b'"effect_authority":true,"effect_authority":false')
  with self.assertRaisesRegex(ContractError,'DUPLICATE'):decode_packet(raw,T+300)
 def test_wire_size_bound(self):
  with self.assertRaisesRegex(ContractError,'BOUND'):decode_packet(b' '*65537,T+300)
 def test_future_descriptor_version_is_not_backdated(self):
  with self.assertRaisesRegex(ContractError,'FUTURE'):market_observers(context(),quotes(),T+200,T+5000,algorithm_frozen_at_ms=T+1)
 def test_price_target_not_scalar_net_pnl(self):
  with self.assertRaises(ContractError):replace(context(),target_schema='USD').validate()

class ObserverTests(unittest.TestCase):
 def test_three_diagnostic_nodes_not_untrained_price_forecasts(self):
  nodes=graph_nodes()[2:]
  self.assertEqual({n.kind for n in nodes},{'regime','risk','execution'})
  for n in nodes:self.assertIsNone(n.probabilities);self.assertIsNone(n.uncertainty.value);n.validate(T+300)
 def test_missing_fee_is_not_zero(self):
  f=ExecutionFact('exec1',T-1,'100000',None,None,'paper-cycle','2'*64,'7'*64)
  n=execution_observer(context(),[f],T+200,T+5000)
  self.assertIsNone(next(m.value for m in n.metrics if m.name=='reported_commission_rate'))
  self.assertEqual(next(m.value for m in n.metrics if m.name=='missing_fee_count'),1)
 def test_real_fee_rate_observable_only(self):
  f=ExecutionFact('exec1',T-1,'100000','2',15,'paper-cycle','2'*64,'7'*64)
  n=execution_observer(context(),[f],T+200,T+5000)
  self.assertAlmostEqual(next(m.value for m in n.metrics if m.name=='reported_commission_rate'),.2)
 def test_duplicate_execution_not_double_counted(self):
  f=ExecutionFact('exec1',T-1,'100','2',15,'paper-cycle','2'*64,'7'*64)
  with self.assertRaisesRegex(ContractError,'DUPLICATE'):execution_observer(context(),[f,f],T+200,T+5000)
 def test_future_execution_never_predictive_input(self):
  f=ExecutionFact('exec1',T+101,'100','2',15,'paper-cycle','2'*64,'7'*64)
  with self.assertRaisesRegex(ContractError,'FUTURE'):execution_observer(context(),[f],T+200,T+5000)
 def test_negative_fee_rebate_preserved(self):
  f=ExecutionFact('exec1',T-1,'100000','-1',15,'paper-cycle','2'*64,'7'*64)
  n=execution_observer(context(),[f],T+200,T+5000);self.assertLess(n.metrics[0].value,0)
 def test_zero_fee_is_distinct_from_missing(self):
  f=ExecutionFact('exec1',T-1,'100000','0',15,'paper-cycle','2'*64,'7'*64)
  self.assertEqual(execution_observer(context(),[f],T+200,T+5000).metrics[0].value,0)
 def test_fee_cannot_be_binary_float(self):
  f=ExecutionFact('exec1',T-1,'100000',2.,15,'paper-cycle','2'*64,'7'*64)
  with self.assertRaises(ContractError):execution_observer(context(),[f],T+200,T+5000)
 def test_new_strategy_not_mixed_with_old_execution(self):
  f=ExecutionFact('exec1',T-1,'100000','2',15,'paper-cycle','9'*64,'7'*64)
  with self.assertRaisesRegex(ContractError,'SCOPE'):execution_observer(context(),[f],T+200,T+5000)
 def test_quote_gap_not_interpolated(self):
  q=quotes();q[1]=replace(q[1],observed_ms=T-90000,known_ms=T-89990)
  with self.assertRaisesRegex(ContractError,'GAP'):market_observers(context(),q,T+200,T+5000)
 def test_quote_unreliable_or_future(self):
  for changes in ({'source':'SYNTHETIC'},{'authoritative':False},{'known_ms':T+101},{'bid':-1.}):
   q=quotes();q[-1]=replace(q[-1],**changes)
   with self.subTest(changes=changes),self.assertRaises(ContractError):market_observers(context(),q,T+200,T+5000)
 def test_no_history_mutation(self):
  q=quotes();before=copy.deepcopy(q);market_observers(context(),q,T+200,T+5000);self.assertEqual(q,before)
 def test_all_constant_prices_produce_valid_descriptor(self):
  q=[replace(v,bid=1.,ask=1.) for v in quotes()]
  for n in market_observers(context(),q,T+200,T+5000):n.validate(T+300)

class GraphTests(unittest.TestCase):
 def setUp(self):torch.manual_seed(11);torch.set_num_threads(1)
 def test_soft_default_and_real_probability_mixture(self):
  model=GraphMetaNeuron();x=model(graph_nodes(),T+300)
  self.assertEqual(x['mode'],'soft');self.assertEqual(len(x['selected_node_ids']),5)
  torch.testing.assert_close(x['probabilities'],torch.tensor([.075,.075,.1,.45,.1,.1,.1]))
  self.assertEqual(x['embedding'].shape,(256,));self.assertFalse(x['effect_authority'])
 def test_permutation_invariant(self):
  model=GraphMetaNeuron();a=model(graph_nodes(),T+300);b=model(list(reversed(graph_nodes())),T+300)
  torch.testing.assert_close(a['probabilities'],b['probabilities']);torch.testing.assert_close(a['embedding'],b['embedding'])
 def test_topk_is_explicit_and_bounded(self):
  x=GraphMetaNeuron('top_k',top_k=2)(graph_nodes(),T+300)
  self.assertEqual(len(x['selected_node_ids']),2);self.assertGreaterEqual(len(x['forecast_node_ids']),1)
 def test_topk_one_never_invents_forecast_from_execution(self):
  x=GraphMetaNeuron('top_k',top_k=1)(graph_nodes(),T+300)
  self.assertEqual(len(x['selected_node_ids']),1);self.assertEqual(x['selected_node_ids'],x['forecast_node_ids'])
 def test_descriptors_alone_do_not_predict_uniform_distribution(self):
  with self.assertRaisesRegex(ContractError,'NO_FORECAST'):GraphMetaNeuron()(graph_nodes()[2:],T+300)
 def test_graph_attention_is_input_conditioned(self):
  model=GraphMetaNeuron();a=model(graph_nodes(),T+300)
  changed=graph_nodes();changed[0]=replace(changed[0],embedding=(0.,1.)+(0.,)*254)
  b=model(changed,T+300)
  self.assertFalse(torch.allclose(a['graph_attention'][0],b['graph_attention'][0]))
 def test_attention_rows_are_normalized(self):
  x=GraphMetaNeuron()(graph_nodes(),T+300)
  for attention in x['graph_attention']:torch.testing.assert_close(attention.sum(-1),torch.ones(5))
 def test_finite_backprop_on_synthetic_fixture(self):
  m=GraphMetaNeuron();r=m(graph_nodes(),T+300);loss=-r['probabilities'][3].log()+.01*r['embedding'].square().mean();loss.backward()
  self.assertTrue(any(p.grad is not None and p.grad.abs().sum()>0 for p in m.parameters()))
  self.assertTrue(all(p.grad is None or torch.isfinite(p.grad).all() for p in m.parameters()))
 def test_forbidden_shapes_modes(self):
  for args in ({'mode':'actor'},{'top_k':True},{'top_k':5},{'message_rounds':-1},{'latent_dim':256}):
   with self.subTest(args=args),self.assertRaises(ContractError):GraphMetaNeuron(**args)
 def test_no_gat_ablation_valid(self):
  result=GraphMetaNeuron(message_rounds=0)(graph_nodes(),T+300);self.assertEqual(result['graph_attention'],[])
 def test_nonfinite_weights_fail(self):
  m=GraphMetaNeuron()
  with torch.no_grad():m.router.weight.fill_(float('nan'))
  with self.assertRaises(ContractError):m(graph_nodes(),T+300)
 def test_meta_result_seals_into_same_256_wire(self):
  r=GraphMetaNeuron()(graph_nodes(),T+300)
  n=seal_output(r,model_sha256='a'*64,model_frozen_at_ms=T-10,available_at_ms=T+400,valid_until_ms=T+5000)
  self.assertIsInstance(n,NeuronOutput);self.assertEqual(len(n.embedding),256);self.assertEqual(n.available_at_ms,T+400)
  self.assertFalse(n.effect_authority)
 def test_meta_result_cannot_backdate_latency_or_new_weights(self):
  r=GraphMetaNeuron()(graph_nodes(),T+300)
  with self.assertRaises(ContractError):seal_output(r,model_sha256='a'*64,model_frozen_at_ms=T+200,available_at_ms=T+400,valid_until_ms=T+5000)
 def test_sparse_does_not_claim_upstream_compute_savings(self):
  r=GraphMetaNeuron('top_k')(graph_nodes(),T+300);self.assertFalse(r['upstream_expert_compute_saved']);self.assertFalse(r['selected_for_runtime'])

class ProtocolTests(unittest.TestCase):
 def plan(self,target='MID_RETURN_7_BINS'):
  return PairedPlan('paper-cycle','2'*64,'3'*64,target,(Candidate('numeric','4'*64,T-200,T-300),Candidate('fusion','5'*64,T-200,T-300)),T-100,T,T+400000,T+800000,3)
 def prediction(self,plan):
  v=tuple((a.name,(.1,.1,.1,.4,.1,.1,.1) if plan.target=='MID_RETURN_7_BINS' else (-1.,)) for a in plan.arms)
  return Prediction('case1','6'*64,plan.identity(),T+100,T+200,T+300000,tuple((a.name,a.model_sha256) for a in plan.arms),v)
 def test_window_remains_pending_despite_good_data(self):
  p=self.plan();d=self.prediction(p);o=Outcome('case1',T+300001,3,'7'*64,True,False)
  r=summarize(p,[d],[o],T+350000);self.assertEqual(r['state'],'PENDING_FIXED_WINDOW');self.assertFalse(r['model_selected'])
 def test_unpredicted_slots_remain_in_denominator(self):
  p=self.plan();r=summarize(p,[self.prediction(p)],[],T+900000);self.assertEqual(r['unpredicted'],2);self.assertEqual(r['censored'],1)
 def test_duplicate_input_cannot_inflate_sample_count(self):
  p=self.plan();d=self.prediction(p)
  with self.assertRaisesRegex(ContractError,'REPEATED_INPUT'):summarize(p,[d,replace(d,case_id='case2')],[],T+900000)
 def test_candidate_replacement_rejected(self):
  p=self.plan();d=replace(self.prediction(p),candidate_hashes=(('numeric','8'*64),('fusion','5'*64)))
  with self.assertRaisesRegex(ContractError,'SUBSTITUTION'):summarize(p,[d],[],T+900000)
 def test_missing_arm_not_treated_as_valid_pair(self):
  p=self.plan();d=self.prediction(p);d=replace(d,values=d.values[:1])
  with self.assertRaisesRegex(ContractError,'MISSING'):summarize(p,[d],[],T+900000)
 def test_future_training_leakage(self):
  p=self.plan();p=replace(p,arms=(replace(p.arms[0],train_outcome_end_ms=T+1),p.arms[1]))
  with self.assertRaises(ContractError):p.validate()
 def test_late_commit_not_prediction(self):
  p=self.plan();d=replace(self.prediction(p),committed_ms=T+300001)
  with self.assertRaisesRegex(ContractError,'LATE'):summarize(p,[d],[],T+900000)
 def test_endpoint_gap_is_not_zero_return(self):
  p=self.plan();r=summarize(p,[self.prediction(p)],[Outcome('case1',T+305001,3,'7'*64,True,False)],T+900000)
  self.assertEqual(r['observed'],0);self.assertEqual(r['mean_losses']['numeric'],None)
 def test_actual_net_requires_fees_and_original_command(self):
  p=self.plan('ACTUAL_CLOSED_NET_USD');d=self.prediction(p)
  for out in (Outcome('case1',T+300001,-11.5,'7'*64,True,False,'cmd',True),Outcome('case1',T+300001,-11.5,'7'*64,True,True,None,True),Outcome('case1',T+300001,-11.5,'7'*64,True,True,'cmd',False)):
   r=summarize(p,[d],[out],T+900000);self.assertEqual(r['observed'],0)
 def test_net_loss_not_filtered(self):
  p=self.plan('ACTUAL_CLOSED_NET_USD');o=Outcome('case1',T+300001,-11.5,'7'*64,True,True,'cmd',True)
  r=summarize(p,[self.prediction(p)],[o],T+900000);self.assertEqual(r['observed'],1);self.assertEqual(r['mean_losses']['numeric'],10.5)
 def test_one_actual_trade_is_not_two_independent_samples(self):
  p=self.plan('ACTUAL_CLOSED_NET_USD');d=self.prediction(p);e=replace(d,case_id='case2',input_sha256='9'*64)
  outcomes=[Outcome(n,T+300001,-11.5,'7'*64,True,True,'same-cmd',True) for n in ('case1','case2')]
  with self.assertRaisesRegex(ContractError,'DUPLICATE_ORIGINAL'):summarize(p,[d,e],outcomes,T+900000)
 def test_no_infinite_money_error(self):
  p=self.plan('ACTUAL_CLOSED_NET_USD');d=self.prediction(p)
  d=replace(d,values=tuple((a.name,(1e308,)) for a in p.arms));o=Outcome('case1',T+300001,-1e308,'7'*64,True,True,'cmd',True)
  with self.assertRaisesRegex(ContractError,'NONFINITE'):summarize(p,[d],[o],T+900000)
 def test_even_complete_pair_is_not_owner_acceptance(self):
  p=replace(self.plan(),planned_cases=1);o=Outcome('case1',T+300001,3,'7'*64,True,False)
  r=summarize(p,[self.prediction(p)],[o],T+900000);self.assertFalse(r['generalization_proven']);self.assertFalse(r['canonical_owner_accepted'])

if __name__=='__main__':unittest.main(verbosity=2)
