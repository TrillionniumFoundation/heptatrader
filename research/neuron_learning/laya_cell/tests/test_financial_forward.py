"""Synthetic falsification fixtures, never real prospective performance claims."""
import copy,hashlib,json,math,os,tempfile,time,unittest
from pathlib import Path
from laya_cell.integrity import ContractError,digest,canonical
from laya_cell.financial_forward_protocol import RULE,validate_plan,score_case,assessment,feature_text,huber
from laya_cell.financial_forward_evaluate import read_prediction,execution_index

T=1791380000000

def plan():
 p={'candidate_id':'c','candidate_created_at_ms':T-20000,'registered_at_ms':T-10000,'input_start_ms':T,
  'input_end_ms':T+RULE['window_ms'],'outcome_end_ms':T+RULE['window_ms']+RULE['closure_grace_ms'],
  'behavior_scope':{'producer_manifest_sha256':'producer','behavior_model_id':'test-remote'},'training_reward_ids':['train-reward'],
  'training_input_ids':['train-input'],'training_mean_usd':.5,'trial':1,'rule':copy.deepcopy(RULE),'synthetic':False}
 p['plan_id']=digest(p);return p

def reseal(p):p.pop('plan_id',None);p['plan_id']=digest(p);return p

def fixture(p=None):
 p=p or plan();t=p['input_start_ms']+10000
 b={'command':{'command_id':'cmd','created_at_ms':t+1000,'side':'SELL'},'request_id':'r',
  'request_sha256':'input','request_started_at_ms':t}
 pred={'plan_id':p['plan_id'],'candidate_id':p['candidate_id'],'request_id':'r','request_sha256':'input',
  'producer_manifest_sha256':'producer','computed_at_ms':t+300,'committed_at_ms':t+400,'values_usd':{'BUY':.2,'SELL':-.3}}
 reward={'behavior_model_id':'test-remote','learning_eligible':True,'fees_complete':True,'settlement_complete':True,'input_id':'input',
  'open_command_id':'cmd','instrument':'EUR.USD','input_attribution':{'producer_manifest_sha256':'producer'},
  'realized_net_usd':'-11.50','actual_commissions_usd':'4','realized_gross_usd':'-7.50'}
 reward['reward_id']=digest(reward)
 return p,b,pred,reward,t+1100,t+60000

class LedgerPrefixSizeTests(unittest.TestCase):
 def test_execution_ledger_over_32_mib_keeps_private_scope(self):
  from hepta_decision_attribution import MAX_TOTAL
  self.assertGreater(MAX_TOTAL, 32*1024**2)
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'oms-journal.jsonl'
   row=(json.dumps({'event':'broker_execution','broker_execution_id':'fixture-e1',
                    'ts_ms':123456,'execution_domain':'PAPER','instrument':'EUR.USD',
                    'req_id':'fixture-c1'},sort_keys=True)+'\n').encode()
   with path.open('wb') as f:
    f.write(row)
    f.truncate(33*1024**2)
   os.chmod(path,0o600)
   prefix={'path':str(path),'bytes':len(row),'sha256':hashlib.sha256(row).hexdigest()}
   self.assertEqual(execution_index(prefix)[1]['fixture-c1'],123456)
   os.chmod(path,0o644)
   with self.assertRaisesRegex(ValueError,'ATTRIBUTION_FILE_SCOPE'):execution_index(prefix)
 def test_oversized_prefix_refused_without_read(self):
  from hepta_decision_attribution import MAX_TOTAL
  with self.assertRaisesRegex(ContractError,'FINANCIAL_LEDGER_PREFIX_SIZE'):
   execution_index({'path':'/nonexistent','bytes':MAX_TOTAL+1,'sha256':'0'*64})

class PlanTests(unittest.TestCase):
 def test_plan_is_prospective(self):self.assertEqual(validate_plan(plan())['trial'],1)
 def test_hash_detects_changed_rule(self):
  p=plan();p['rule']['min_observed_trades']=1
  with self.assertRaisesRegex(ContractError,'IDENTITY'):validate_plan(p)
 def test_rehashed_changed_rule_still_rejected(self):
  p=plan();p['rule']['min_observed_trades']=1;reseal(p)
  with self.assertRaisesRegex(ContractError,'IDENTITY'):validate_plan(p)
 def test_backdated_registration_rejected(self):
  p=plan();p['registered_at_ms']=p['candidate_created_at_ms']-1;reseal(p)
  with self.assertRaisesRegex(ContractError,'PROSPECTIVE'):validate_plan(p)
 def test_window_cannot_be_shortened_after_success(self):
  p=plan();p['input_end_ms']-=1000;reseal(p)
  with self.assertRaisesRegex(ContractError,'WINDOW_MODIFIED'):validate_plan(p)
 def test_synthetic_evidence_cannot_register_as_real(self):
  p=plan();p['synthetic']=True;reseal(p)
  with self.assertRaises(ContractError):validate_plan(p)
 def test_effect_authority_false(self):self.assertFalse(RULE['trade_permission']);self.assertFalse(RULE['actor_update'])
 def test_single_reward_never_proves_generalization(self):
  f=fixture();out=score_case(*f);a=assessment(f[0],[out],f[0]['outcome_end_ms'])
  self.assertEqual(a['state'],'REJECTED');self.assertIn('INSUFFICIENT_PROSPECTIVE_TRADES',a['reasons'])
 def test_open_position_prompt_cannot_use_flat_trade_value(self):
  with self.assertRaisesRegex(ContractError,'INPUT_SCOPE'):feature_text({'instrument':'EUR.USD','model_id':'m','prompt':'x','position_at_request':1},'BUY')
 def test_btc_not_silently_routed_to_fx_value_head(self):
  with self.assertRaisesRegex(ContractError,'INPUT_SCOPE'):feature_text({'instrument':'BTC.USD','model_id':'m','prompt':'x','position_at_request':0},'BUY')
 def test_full_prompt_preserved(self):
  r={'instrument':'EUR.USD','model_id':'m','prompt':'unicode 原输入 '+('A'*10000),'position_at_request':0}
  self.assertTrue(feature_text(r,'BUY').startswith(r['prompt']));self.assertNotIn('-11.50',feature_text(r,'BUY'))

class OutcomeTests(unittest.TestCase):
 def check_change(self,which,key,value,reason=None):
  args=list(fixture());args[which][key]=value;out=score_case(*args)
  self.assertEqual(out['status'],'CENSORED')
  if reason:self.assertEqual(out['reason'],reason)
 def test_real_identity_and_commission_give_observed(self):
  out=score_case(*fixture());self.assertEqual(out['status'],'OBSERVED');self.assertEqual(out['actual_net_usd'],-11.5)
 def test_missing_prediction_is_censored(self):
  f=list(fixture());f[2]=None;self.assertEqual(score_case(*f)['reason'],'NO_PREDICTION_FOR_ORIGINAL_REQUEST')
 def test_pending_position_not_zero_reward(self):
  f=list(fixture());f[3]=None;self.assertEqual(score_case(*f)['status'],'PENDING')
 def test_prediction_after_open_is_never_out_of_sample(self):
  f=list(fixture());f[2]['committed_at_ms']=f[1]['command']['created_at_ms'];self.assertEqual(score_case(*f)['reason'],'NOT_COMMITTED_BEFORE_ORIGINAL_OPEN')
 def test_prediction_before_close_but_after_first_fill_rejected(self):
  f=list(fixture());f[4]=f[2]['committed_at_ms'];self.assertEqual(score_case(*f)['reason'],'NOT_COMMITTED_BEFORE_ORIGINAL_OPEN')
 def test_other_candidate_rejected(self):self.check_change(2,'candidate_id','substitute')
 def test_other_plan_rejected(self):self.check_change(2,'plan_id','other')
 def test_wrong_exact_request_hash_rejected(self):self.check_change(2,'request_sha256','other')
 def test_reused_request_id_no_matching_identity(self):self.check_change(2,'request_id','other')
 def test_other_producer_policy_rejected(self):self.check_change(2,'producer_manifest_sha256','old-policy')
 def test_missing_fee_cannot_be_zero(self):
  f=list(fixture());f[3]['fees_complete']=False;f[3].pop('reward_id');f[3]['reward_id']=digest(f[3]);self.assertEqual(score_case(*f)['status'],'CENSORED')
 def test_historical_training_reward_never_validation(self):
  f=list(fixture());f[0]['training_reward_ids']=[f[3]['reward_id']];reseal(f[0]);f[2]['plan_id']=f[0]['plan_id']
  self.assertEqual(score_case(*f)['reason'],'TRAINING_VALIDATION_OVERLAP')
 def test_training_same_input_different_reward_id_rejected(self):
  f=list(fixture());f[0]['training_input_ids']=['input'];reseal(f[0]);f[2]['plan_id']=f[0]['plan_id'];self.assertEqual(score_case(*f)['reason'],'TRAINING_VALIDATION_OVERLAP')
 def test_corrected_reward_bytes_not_ignored(self):
  f=list(fixture());f[3]['realized_net_usd']='100';self.assertEqual(score_case(*f)['status'],'CENSORED')
 def test_large_loss_is_retained_not_clipped(self):
  f=list(fixture());r=f[3];r['realized_net_usd']='-10000';r['realized_gross_usd']='-9996';r.pop('reward_id');r['reward_id']=digest(r)
  out=score_case(*f);self.assertEqual(out['status'],'OBSERVED');self.assertEqual(out['actual_net_usd'],-10000);self.assertTrue(out['target_exceeds_training_support'])
 def test_counterfactual_unchosen_side_not_counted_as_second_trade(self):
  out=score_case(*fixture());self.assertEqual(out['side'],'SELL');self.assertEqual(out['prediction_usd'],-.3)
 def test_both_side_predictions_required(self):self.check_change(2,'values_usd',{'SELL':-.3},'VALUE_OUTPUT_SCOPE')
 def test_nonfinite_value_rejected(self):self.check_change(2,'values_usd',{'BUY':float('nan'),'SELL':0.},'VALUE_OUTPUT_SCOPE')
 def test_stress_uses_real_fee_not_model_fee(self):
  out=score_case(*fixture());self.assertEqual(out['fee_stresses'][-1]['target_usd'],-15.5)

class StatisticalTests(unittest.TestCase):
 def rows(self,p,n=200):
  rs=[]
  for i in range(n):
   f=list(fixture(p));t=T+10000+i*int(RULE['window_ms']/(n+1));f[1]['command']['command_id']='cmd'+str(i)
   f[1]['command']['created_at_ms']=t+1000;f[1]['request_started_at_ms']=t
   f[2]['computed_at_ms']=t+100;f[2]['committed_at_ms']=t+200
   y=100 if i%2 else -100;pred=14 if i%2 else -14;f[2]['values_usd']={'BUY':pred,'SELL':pred}
   r=f[3];r['open_command_id']=f[1]['command']['command_id'];r['realized_net_usd']=str(y);r['realized_gross_usd']=str(y+4);r.pop('reward_id');r['reward_id']=digest(r)
   f[4]=t+1100;f[5]=t+50000;rs.append(score_case(*f))
  return rs
 def test_large_synthetic_positive_dataset_only_exercises_rule_no_selection(self):
  p=plan();a=assessment(p,self.rows(p),p['outcome_end_ms']);self.assertEqual(a['state'],'STATISTICALLY_QUALIFIED_REQUIRES_NATIVE_OWNER');self.assertFalse(a['selection_performed'])
 def test_endless_peeking_cannot_finish_fixed_window_early(self):
  p=plan();a=assessment(p,self.rows(p),p['outcome_end_ms']-1);self.assertEqual(a['state'],'PENDING')
 def test_same_trade_twice_fails(self):
  p=plan();rs=self.rows(p,2)
  with self.assertRaisesRegex(ContractError,'DUPLICATE'):assessment(p,rs+rs,p['outcome_end_ms'])
 def test_missing_predictions_prevent_qualification(self):
  p=plan();rs=self.rows(p);rs[0].update(status='CENSORED');a=assessment(p,rs,p['outcome_end_ms']);self.assertEqual(a['state'],'REJECTED')
 def test_no_temporal_replication_no_pass(self):
  p=plan();rs=self.rows(p)
  for r in rs:r['opened_at_ms']=T+10000
  a=assessment(p,rs,p['outcome_end_ms']);self.assertIn('INSUFFICIENT_TEMPORAL_SUPPORT',a['reasons'])
 def test_huber_difference_is_bounded_without_clipping_outcome(self):
  for y in (-1e10,-1000,-1,0,1,1000,1e10):self.assertLessEqual(abs(huber(.1-y)-huber(-.1-y)),.200001)
 def test_family_alpha_sum_is_bounded(self):self.assertLess(sum(.05/(i*(i+1)) for i in range(1,10000)),.05)
 def test_training_set_mae_is_not_future_evidence(self):self.assertEqual(assessment(plan(),[],plan()['outcome_end_ms'])['state'],'REJECTED')
 def test_scope_conflicts_cannot_pool(self):
  p=plan();rs=self.rows(p,1);rs[0]['candidate_id']='other'
  with self.assertRaisesRegex(ContractError,'SCOPE'):assessment(p,rs,p['outcome_end_ms'])
 def test_loss_difference_bound_not_trusted_from_invalid_data(self):
  p=plan();rs=self.rows(p,1);rs[0]['baselines']['ZERO_USD']['loss_difference']=1e9
  with self.assertRaisesRegex(ContractError,'BOUND'):assessment(p,rs,p['outcome_end_ms'])

class CommitTests(unittest.TestCase):
 def test_payload_change_after_commit_fails(self):
  with tempfile.TemporaryDirectory() as td:
   p=plan();folder=Path(td)/'plans'/p['plan_id']/'predictions/r';folder.mkdir(parents=True)
   (folder/'prediction.json').write_bytes(canonical({'request_id':'r'}))
   commit={'plan_id':p['plan_id'],'request_id':'r','committed_at_ms':T,'prediction_sha256':'0'*64}
   (folder/'commit.json').write_bytes(canonical(commit))
   with self.assertRaisesRegex(ContractError,'TRUST_REQUIRED'):read_prediction(Path(td),p,'r')
 def test_missing_commit_does_not_mean_prediction_ready(self):
  with tempfile.TemporaryDirectory() as td:self.assertIsNone(read_prediction(Path(td),plan(),'r'))

if __name__=='__main__':unittest.main(verbosity=2)
