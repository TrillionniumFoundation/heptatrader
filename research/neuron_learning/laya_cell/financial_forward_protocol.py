"""Frozen prospective financial-value protocol: no refit, no effects authority.

Commit both action-conditioned predictions before the original command. Score
only actual precisely attributed, closed, fee-complete trades; retain abstentions,
late forecasts, unclosed trades and corrections. No winner-biased sample removal.
"""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
from collections import Counter
import hashlib,math
from .integrity import ContractError,read_json,sha_file,digest
from .financial_value_data import PROFILE

RULE={'schema':'heptatrader.financial-forward-rule.v1','window_ms':14*86400000,'closure_grace_ms':86400000,
 'min_observed_trades':20,'min_distinct_utc_days':3,'min_trades_each_time_half':5,
 'minimum_prediction_coverage':.95,'maximum_unresolved_at_final':0,
 'minimum_mae_improvement_usd':.10,'max_abs_prediction_usd':15.,'target_scale_usd':100.,
 'benchmark_ids':['ZERO_USD','FROZEN_TRAINING_MEAN_USD'],
 'decision_support_only':True,'actor_update':False,'trade_permission':False,
 'sequential_test':'MIXTURE_HOEFFDING_HUBER_LOSS_DIFFERENCE',
 'lambda_grid':[.125,.25,.5,1.,2.,4.,8.],
 'family_alpha':.05,'candidate_alpha_spending':'0.05/(trial*(trial+1))',
 'two_time_halves_positive':True,'fee_stresses':[1.,1.5,2.],
 'missing_fee_not_zero':True,'minimum_outcome_sign_classes':2,'stable_generalization_claim':'ONLY_OBSERVED_BEHAVIOR_SCOPE_AND_PERIOD',
 'automatic_execution_selection':False}

def feature_text(request,side):
 if (request.get('instrument')!='EUR.USD' or side not in ('BUY','SELL') or not isinstance(request.get('prompt'),str)
     or not isinstance(request.get('model_id'),str) or request.get('position_at_request')!=0):
  raise ContractError('FORWARD_INPUT_SCOPE')
 return request['prompt']+'\nFinancial value context: EUR.USD; fixed requested quantity=25000 EUR; selected action='+side+'; behavior model='+request['model_id']+'. Predict eventual roundtrip net USD after reported execution commissions.'

def validate_candidate(directory):
 directory=Path(directory);c=read_json(directory/'candidate.json')
 if c.get('schema')=='heptatrader.current100k-value-candidate.v1':
  from .current100k_forward import validate_value_candidate
  return validate_value_candidate(directory,c)
 if c.get('candidate_id')!=digest({k:v for k,v in c.items() if k!='candidate_id'}):raise ContractError('FORWARD_CANDIDATE_HASH')
 if (c.get('financial_profile')!=PROFILE or c.get('pretrained_laya_unchanged') is not True
     or c.get('scorer_or_action_adapter_updated') is not False or c.get('automatic_trading') is not False
     or c.get('policy_gradient_used') is not False or c.get('pnl_reward_used') is not True
     or c.get('training_scope')!='INDEPENDENT_BEHAVIOR_BOUND_VALUE_HEAD'):
  raise ContractError('FORWARD_CANDIDATE_ROLE')
 if sha_file(directory/'value-head.safetensors')!=c['head_tensor_sha256']:raise ContractError('FORWARD_HEAD_BYTES')
 scope=c.get('behavior_scope') or {}
 if (scope.get('instrument')!='EUR.USD' or scope.get('currency')!='USD' or scope.get('quantity')!=25000
     or len(scope.get('producer_manifest_sha256',''))!=64):raise ContractError('FORWARD_BEHAVIOR_SCOPE')
 if c.get('unique_closed_rewards')!=len(c['reward_ids']) or len(set(c['reward_ids']))!=len(c['reward_ids']):raise ContractError('FORWARD_TRAINING_IDENTITY')
 return c

def validate_plan(plan,now_ms=None):
 if (plan.get('plan_id')!=digest({k:v for k,v in plan.items() if k!='plan_id'}) or plan.get('rule')!=RULE
     or type(plan.get('trial'))is not int or plan['trial']<1 or plan.get('synthetic') is not False):
  raise ContractError('FORWARD_PLAN_IDENTITY')
 if not 0<plan['candidate_created_at_ms']<=plan['registered_at_ms']<plan['input_start_ms']<plan['input_end_ms']<plan['outcome_end_ms']:
  raise ContractError('FORWARD_PLAN_NOT_PROSPECTIVE')
 if plan['input_end_ms']-plan['input_start_ms']!=RULE['window_ms'] or plan['outcome_end_ms']-plan['input_end_ms']!=RULE['closure_grace_ms']:
  raise ContractError('FORWARD_WINDOW_MODIFIED')
 if not math.isfinite(plan['training_mean_usd']) or abs(plan['training_mean_usd'])>800:raise ContractError('BASELINE_OUT_OF_SCOPE')
 if now_ms is not None and now_ms<plan['registered_at_ms']:raise ContractError('FORWARD_CLOCK_REGRESSION')
 return plan

def huber(value):
 x=abs(value);return .5*x*x if x<=1 else x-.5

def signed_receipt_payload(record):return {k:v for k,v in record.items() if k not in ('signature','record_sha256')}

def score_case(plan,binding,prediction,reward,first_fill_ms,observed_at_ms):
 """No caller-provided PASS. Structural failure is explicit and never scored."""
 validate_plan(plan)
 cmd=binding['command'];rid=binding['request_id'];opened=cmd['created_at_ms'];candidate_id=plan['candidate_id']
 result={'schema':'heptatrader.financial-forward-outcome.v1','candidate_id':candidate_id,'plan_id':plan['plan_id'],
  'command_id':cmd['command_id'],'request_id':rid,'opened_at_ms':opened,'observed_at_ms':observed_at_ms,
  'status':'CENSORED','reason':None,'actual_execution_sample':True,'policy_gradient_allowed':False}
 if (type(opened)is not int or type(binding['request_started_at_ms'])is not int or opened<binding['request_started_at_ms']):
  return {**result,'reason':'COMMAND_CLOCK_INVALID'}
 if not plan['input_start_ms']<=binding['request_started_at_ms']<plan['input_end_ms']:
  return {**result,'reason':'INPUT_OUTSIDE_REGISTERED_WINDOW'}
 if prediction is None:return {**result,'reason':'NO_PREDICTION_FOR_ORIGINAL_REQUEST'}
 if (prediction.get('plan_id')!=plan['plan_id'] or prediction.get('candidate_id')!=candidate_id
     or prediction.get('request_id')!=rid or prediction.get('request_sha256')!=binding['request_sha256']
     or prediction.get('producer_manifest_sha256')!=plan['behavior_scope']['producer_manifest_sha256']):
  return {**result,'reason':'PREDICTION_IDENTITY_MISMATCH'}
 # Prediction must exist before PREPARED AND before any broker execution, never
 # just before the eventual profitable/losing close. Late inference stays visible.
 if not binding['request_started_at_ms']<=prediction['computed_at_ms']<=prediction['committed_at_ms']<min(opened,first_fill_ms):
  return {**result,'reason':'NOT_COMMITTED_BEFORE_ORIGINAL_OPEN'}
 if reward is None:return {**result,'status':'PENDING','reason':'ORIGINAL_TRADE_NOT_YET_CLOSED_AND_SETTLED'}
 if (reward.get('reward_id')!=digest({k:v for k,v in reward.items() if k!='reward_id'})
     or reward.get('learning_eligible') is not True or reward.get('fees_complete') is not True
     or reward.get('settlement_complete') is not True or reward.get('input_id')!=binding['request_sha256']
     or reward.get('open_command_id')!=cmd['command_id'] or reward.get('instrument')!='EUR.USD'
     or reward.get('input_attribution',{}).get('producer_manifest_sha256')!=plan['behavior_scope']['producer_manifest_sha256']):
  return {**result,'reason':'REWARD_UNSETTLED_REVOKED_OR_UNBOUND'}
 if (reward.get('behavior_model_id')!=plan['behavior_scope']['behavior_model_id']
     or reward.get('forecast_id')!=binding.get('decision_sha256',reward.get('forecast_id'))
     or any(e.get('command_id')==cmd['command_id'] and e.get('side')!=cmd['side'] for e in reward.get('executions',[]))):
  return {**result,'reason':'REWARD_BEHAVIOR_OR_SIDE_CONFLICT'}
 if reward['reward_id'] in plan['training_reward_ids'] or reward['input_id'] in plan['training_input_ids']:
  return {**result,'reason':'TRAINING_VALIDATION_OVERLAP'}
 side=cmd['side'];values=prediction.get('values_usd',{})
 if set(values)!= {'BUY','SELL'} or any(type(v) not in (float,int) or not math.isfinite(v) or abs(v)>RULE['max_abs_prediction_usd'] for v in values.values()):
  return {**result,'reason':'VALUE_OUTPUT_SCOPE'}
 y=float(reward['realized_net_usd']);fee=float(reward['actual_commissions_usd']);gross=float(reward['realized_gross_usd'])
 if not all(math.isfinite(v) for v in (y,fee,gross)) or abs(gross-fee-y)>1e-7:
  return {**result,'reason':'ACTUAL_ECONOMIC_IDENTITY'}
 # Unlike the fit profile, the evaluator never discards a large loss or clips
 # its label to the training range. Such extrapolation is retained and flagged.
 pred=values[side];bench={'ZERO_USD':0.,'FROZEN_TRAINING_MEAN_USD':plan['training_mean_usd']}
 stress=[]
 for multiple in RULE['fee_stresses']:
  target=gross-multiple*fee
  stress.append({'fee_multiplier':multiple,'target_usd':target,'candidate_abs_error':abs(pred-target),
   'baseline_abs_error':{k:abs(b-target) for k,b in bench.items()}})
 result.update({'status':'OBSERVED','reason':None,'reward_id':reward['reward_id'],'input_id':reward['input_id'],
  'side':side,'prediction_usd':pred,'actual_net_usd':y,'actual_commission_usd':fee,
  'candidate_abs_error_usd':abs(pred-y),'candidate_huber_scaled':huber((pred-y)/100),
  'baselines':{k:{'prediction_usd':b,'abs_error_usd':abs(b-y),'huber_scaled':huber((b-y)/100),
     'loss_difference':huber((b-y)/100)-huber((pred-y)/100),'predictable_difference_bound':abs(b-pred)/100} for k,b in bench.items()},
  'fee_stresses':stress,'target_exceeds_training_support':abs(y)>800,
  'prediction_committed_at_ms':prediction['committed_at_ms'],'first_fill_ms':first_fill_ms})
 return result

def assessment(plan,outcomes,now_ms):
 validate_plan(plan,now_ms)
 ids=[r['command_id'] for r in outcomes]
 if len(set(ids))!=len(ids):raise ContractError('DUPLICATE_ORIGINAL_TRADE_IN_ASSESSMENT')
 if any(r['plan_id']!=plan['plan_id'] or r['candidate_id']!=plan['candidate_id'] for r in outcomes):raise ContractError('ASSESSMENT_SCOPE')
 obs=sorted((r for r in outcomes if r['status']=='OBSERVED'),key=lambda r:(r['opened_at_ms'],r['command_id']))
 reasons=[];days={datetime.fromtimestamp(r['opened_at_ms']/1000,timezone.utc).date().isoformat() for r in obs}
 signs={r['actual_net_usd']>0 for r in obs if r['actual_net_usd']!=0};mid=(plan['input_start_ms']+plan['input_end_ms'])//2
 half=[sum(r['opened_at_ms']<mid for r in obs),sum(r['opened_at_ms']>=mid for r in obs)]
 unresolved=sum(r['status']!='OBSERVED' for r in outcomes)
 if len(obs)<RULE['min_observed_trades']:reasons.append('INSUFFICIENT_PROSPECTIVE_TRADES')
 if len(days)<RULE['min_distinct_utc_days']:reasons.append('INSUFFICIENT_TEMPORAL_SUPPORT')
 if min(half,default=0)<RULE['min_trades_each_time_half']:reasons.append('INDEPENDENT_TIME_HALVES_INCOMPLETE')
 if len(signs)<RULE['minimum_outcome_sign_classes']:reasons.append('INSUFFICIENT_SIGN_COVERAGE')
 if unresolved:reasons.append('INCOMPLETE_OR_LATE_TRADE_COVERAGE')
 if any(r['target_exceeds_training_support'] for r in obs):reasons.append('TRAINING_SUPPORT_EXCEEDED_RETAINED_NOT_DROPPED')
 alpha=RULE['family_alpha']/(plan['trial']*(plan['trial']+1));benchmarks={}
 for name in RULE['benchmark_ids']:
  diffs=[r['baselines'][name]['loss_difference'] for r in obs];bounds=[r['baselines'][name]['predictable_difference_bound'] for r in obs]
  if any(abs(d)>b+1e-9 for d,b in zip(diffs,bounds)):raise ContractError('HUBER_LIPSCHITZ_BOUND_VIOLATION')
  terms=[lam*sum(diffs)-.5*lam*lam*sum(b*b for b in bounds) for lam in RULE['lambda_grid']]
  maximum=max(terms,default=0);loge=maximum+math.log(sum(math.exp(x-maximum) for x in terms)/len(terms))
  mae=sum(r['baselines'][name]['abs_error_usd']-r['candidate_abs_error_usd'] for r in obs)/len(obs) if obs else None
  half_gains=[]
  for first in (True,False):
   rs=[r for r in obs if (r['opened_at_ms']<mid)==first]
   half_gains.append(sum(r['baselines'][name]['abs_error_usd']-r['candidate_abs_error_usd'] for r in rs)/len(rs) if rs else None)
  if mae is None or mae<=RULE['minimum_mae_improvement_usd']:reasons.append('NO_MATERIAL_IMPROVEMENT:'+name)
  if loge<math.log(2/alpha):reasons.append('TIME_UNIFORM_EVIDENCE_INSUFFICIENT:'+name)
  if any(v is None or v<=0 for v in half_gains):reasons.append('TEMPORAL_IMPROVEMENT_NOT_REPLICATED:'+name)
  benchmarks[name]={'mae_improvement_usd':mae,'log_e':loge,'required_log_e':math.log(2/alpha),'half_mae_improvement_usd':half_gains}
  for multiple in RULE['fee_stresses']:
   gains=[]
   for r in obs:
    s=next(x for x in r['fee_stresses'] if x['fee_multiplier']==multiple);gains.append(s['baseline_abs_error'][name]-s['candidate_abs_error'])
   if gains and sum(gains)<0:reasons.append('WORSE_UNDER_FEE_STRESS:'+name+':'+str(multiple))
 final=now_ms>=plan['outcome_end_ms']
 if not final:reasons.append('FIXED_FUTURE_WINDOW_NOT_FINISHED')
 return {'schema':'heptatrader.financial-forward-assessment.v1','plan_id':plan['plan_id'],'candidate_id':plan['candidate_id'],
  'as_of_ms':now_ms,'state':('STATISTICALLY_QUALIFIED_REQUIRES_NATIVE_OWNER' if not reasons else ('REJECTED' if final else 'PENDING')),
  'observed_trades':len(obs),'total_original_trades':len(outcomes),'unresolved_or_censored':unresolved,
  'distinct_utc_days':sorted(days),'time_half_counts':half,'benchmarks':benchmarks,'reasons':sorted(set(reasons)),
  'selection_performed':False,'generalization_proven_outside_observed_scope':False,'trading_authority':False,
  'assumption':'CONDITIONAL_NO_ADVANTAGE_NULL_FOR_ADAPTED_PAIRED_LOSS_DIFFERENCES; NOT_IID_ASSUMED',
  'arbitrary_distribution_shift_guarantee':False,
  'alpha_budget':alpha,'temporal_dependence_handled_by_predictable_bound':True}
