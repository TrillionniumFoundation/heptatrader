"""Exact, settled behavior-bound net PnL -> independent financial value targets.

No output text, realized price, close time or reward enters the feature text.
The target is a realized remote-strategy roundtrip, not an on-policy Laya action.
"""
from __future__ import annotations
from decimal import Decimal
from pathlib import Path
import hashlib,math,time
from .integrity import ContractError,digest,read_json

PROFILE={'schema':'heptatrader.financial-value-profile.v2','instrument':'EUR.USD','currency':'USD',
 'quantity':25000,'target_scale_usd':100.,'target_abs_scaled_max':8.,'max_tokens':2048,
 'representation':'FROZEN_FULL_LAYA_ENCODER_MASKED_MEAN_L2_NORMALIZED_768',
 'head':'INDEPENDENT_LINEAR_768_TO_1_WITH_BIAS','head_initialization':'ZERO_NEUTRAL_NEW_MONETARY_READOUT',
 'max_training_steps':16,'sgd_learning_rate':.05,'max_update_l2':.1,'max_parameter_l2':1.,
 'loss':'HUBER_SCALED_NET_USD','automatic_selection':False,'on_policy':False}

def exact_request(reward):
 attribution=reward['input_attribution'];rid=attribution['request_id'];found=[]
 for witness in attribution['source_prefixes']:
  p=Path(witness['path']);fd=None
  from hepta_decision_attribution import read_private,strict
  blob=read_private(p);prefix=blob[:witness['bytes']]
  if len(prefix)!=witness['bytes'] or hashlib.sha256(prefix).hexdigest()!=witness['sha256']:
   raise ContractError('VALUE_INPUT_PREFIX_CHANGED')
  for line in prefix.splitlines():
   record=strict(line)
   if record.get('kind')=='REQUEST' and record['data'].get('request_id')==rid:found.append(record['data'])
 if len(found)!=1:raise ContractError('VALUE_EXACT_SINGLE_REQUEST_REQUIRED')
 request=found[0]
 if digest(request)!=reward['input_id'] or hashlib.sha256(request['prompt'].encode()).hexdigest()!=attribution['prompt_sha256']:
  raise ContractError('VALUE_REQUEST_HASH')
 return request

def to_row(reward,*,request=None):
 if reward.get('reward_id')!=digest({k:v for k,v in reward.items() if k!='reward_id'}):raise ContractError('VALUE_REWARD_HASH')
 if (reward.get('instrument')!='EUR.USD' or reward.get('currency')!='USD' or reward.get('learning_eligible') is not True
     or reward.get('exact_input_bound') is not True or reward.get('settlement_complete') is not True
     or reward.get('fees_complete') is not True or reward.get('learning_exclusion_reasons')
     or reward.get('policy_gradient_allowed') is not False or reward.get('laya_on_policy') is not False):
  raise ContractError('VALUE_BOUND_SETTLED_REWARD_REQUIRED')
 position=Decimal(0);cash=Decimal(0);fee=Decimal(0);ids=set();opened=Decimal(0)
 side=reward['executions'][0]['side']
 for e in reward['executions']:
  if e['execution_id'] in ids or e.get('commission') is None or e.get('commission_currency')!='USD':raise ContractError('VALUE_DUPLICATE_OR_MISSING_FEE')
  ids.add(e['execution_id']);q=Decimal(e['quantity']);p=Decimal(e['price']);c=Decimal(e['commission'])
  if not all(x.is_finite() for x in (q,p,c)) or q<=0 or p<=0 or e['side'] not in ('BUY','SELL'):raise ContractError('VALUE_NUMERIC')
  signed=q if e['side']=='BUY' else -q;cash-=signed*p;position+=signed;fee+=c
  if e['command_id']==reward['open_command_id']:opened+=q
 if position!=0 or opened!=25000 or cash!=Decimal(reward['realized_gross_usd']) or fee!=Decimal(reward['actual_commissions_usd']) or cash-fee!=Decimal(reward['realized_net_usd']):
  raise ContractError('VALUE_REWARD_ECONOMIC_RECOMPUTATION')
 if request is None:request=exact_request(reward)
 if (request.get('instrument')!='EUR.USD' or request.get('model_id')!=reward.get('behavior_model_id')
     or digest(request)!=reward['input_id'] or not isinstance(request.get('prompt'),str)):
  raise ContractError('VALUE_BEHAVIOR_INPUT_MISMATCH')
 target=float(cash-fee)/100.
 if not math.isfinite(target) or abs(target)>8:raise ContractError('VALUE_TARGET_OUTSIDE_PROFILE')
 # The original prompt and chosen direction were known before OPEN. No close
 # metadata, execution price, commission, PnL or hindsight rationale is input.
 text=request['prompt']+'\nFinancial value context: EUR.USD; fixed requested quantity=25000 EUR; selected action='+side+'; behavior model='+request['model_id']+'. Predict eventual roundtrip net USD after reported execution commissions.'
 row={'reward_id':reward['reward_id'],'episode_id':reward['episode_id'],'input_id':reward['input_id'],
  'request_id':request['request_id'],'behavior_model_id':request['model_id'],'instrument':'EUR.USD','action':side,
  'input_started_at_ms':request['started_at_ms'],'feature_text':text,'feature_text_sha256':hashlib.sha256(text.encode()).hexdigest(),
  'net_usd':str(cash-fee),'commission_usd':str(fee),'target_scaled':target,
  'execution_ids':sorted(ids),'open_command_id':reward['open_command_id'],'exact_request':request,
  'reward':reward,'on_policy':False,'actor_gradient_allowed':False}
 row['row_id']=digest(row);return row

def snapshot(config,now_ms):
 from .resident_rewards import current
 report=current(config,now_ms);rows=[];excluded=[]
 for reward in report['rewards']:
  try:rows.append(to_row(reward))
  except (ContractError,ValueError,KeyError,TypeError,OSError) as exc:excluded.append({'reward_id':reward.get('reward_id'),'reason':str(exc)})
 rows.sort(key=lambda r:(r['input_started_at_ms'],r['row_id']))
 if len(rows)>64:raise ContractError('VALUE_DATASET_CAPACITY_REQUIRES_REVIEW')
 fingerprint=digest([r['row_id'] for r in rows])
 return {'schema':'heptatrader.financial-value-dataset.v1','observed_at_ms':now_ms,'period_sha256':config['period_sha256'],
  'profile':PROFILE,'rows':rows,'exclusions':excluded,'source_ledger_prefix':report.get('source_prefix'),
  'fingerprint':fingerprint,'financial_reward_reason':report['reason'],'real_settled_rewards_only':True,
  'synthetic_rows':0,'model_effect_authority':False}
