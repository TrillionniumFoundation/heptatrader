"""Current USD100k Value scope on the existing signed prospective framework.

Never relabel this head as the old 25,000 EUR experiment. Reuse only the common
future timing, signed custody and bounded-loss comparison; no actor selection.
"""
from __future__ import annotations
import hashlib, math, time
from pathlib import Path
from .integrity import ContractError, digest, read_json, sha_file
from .resident_store import write_once

PLAN_SCHEMA='heptatrader.current100k-value-forward-plan.v1'
CANDIDATE_SCHEMA='heptatrader.current100k-value-candidate.v1'

def validate_value_candidate(directory,c=None):
 from historical_training.current100k import BEHAVIOR
 directory=Path(directory);c=read_json(directory/'candidate.json') if c is None else c
 if c.get('schema')!=CANDIDATE_SCHEMA or c.get('candidate_id')!=digest({k:v for k,v in c.items() if k!='candidate_id'}):
  raise ContractError('CURRENT100K_FORWARD_CANDIDATE_HASH')
 if (c.get('training_scope')!=BEHAVIOR or c.get('pnl_reward_used') is not True
     or c.get('reward_revalidated_before_and_after') is not True
     or c.get('pretrained_backbone_unchanged') is not True
     or any(c.get(k) is not False for k in ('scorer_or_actor_updated','on_policy_policy_gradient','automatic_selection','canonical_owner_accepted'))
     or c.get('broker_orders_sent')!=0 or c.get('scale_usd')!=100 or c.get('status')!='UNVALIDATED_BEHAVIOR_VALUE_ONLY'):
  raise ContractError('CURRENT100K_FORWARD_CANDIDATE_SCOPE')
 if (type(c.get('rows'))is not int or not 1<=c['rows']<=64 or len(c['row_ids'])!=c['rows']
     or len(set(c['row_ids']))!=c['rows'] or sha_file(directory/'value-head.safetensors')!=c['head_sha256']):
  raise ContractError('CURRENT100K_FORWARD_HEAD_OR_ROWS')
 return c

def validate_bound_plan(plan,c):
 from historical_training.current100k import BEHAVIOR,to_row
 from .financial_value_data import ExactRequestIndex
 from .financial_forward_protocol import validate_plan
 validate_plan(plan,int(time.time()*1000))
 if plan.get('schema')!=PLAN_SCHEMA:raise ContractError('CURRENT100K_FORWARD_PLAN_SCHEMA')
 scope=plan.get('behavior_scope',{})
 if (scope.get('behavior_profile')!=BEHAVIOR or scope.get('reference_notional_usd')!=100000
     or scope.get('quantity') is not None or plan.get('producer_registration',{}).get('sha256')!=scope.get('producer_manifest_sha256') or scope.get('instrument')!='EUR.USD' or scope.get('currency')!='USD'
     or plan.get('candidate_id')!=c['candidate_id'] or plan.get('candidate_created_at_ms')!=c['created_at_ms']
     or plan.get('head_tensor_sha256')!=c['head_sha256'] or plan.get('training_dataset_sha256')!=c['dataset_sha256']):
  raise ContractError('CURRENT100K_FORWARD_PLAN_SCOPE')
 path=Path(plan['training_dataset_path'])
 if sha_file(path)!=c['dataset_sha256']:raise ContractError('CURRENT100K_FORWARD_DATASET_PIN')
 data=read_json(path,32*1024**2);rows=data['rows']
 if (data['period_sha256']!=plan['period_sha256'] or data['producer_manifest_sha256']!=scope['producer_manifest_sha256']
     or [x['row_id'] for x in rows]!=c['row_ids'] or [x['reward_id'] for x in rows]!=plan['training_reward_ids']
     or [x['input_id'] for x in rows]!=plan['training_input_ids']
     or {x['behavior_scope']['model_id'] for x in rows}!={scope['behavior_model_id']}):
  raise ContractError('CURRENT100K_FORWARD_TRAINING_BINDING')
 source_index=ExactRequestIndex([x['reward'] for x in rows])
 for x in rows:
  if x!=to_row(x['reward'],x['source_request'],x['source_binding'],data['producer_manifest_sha256'],data['period_sha256'],source_index=source_index):
   raise ContractError('CURRENT100K_FORWARD_REWARD_RECOMPUTATION')
 expected=sum(float(x['net_usd']) for x in rows)/len(rows)
 if plan['training_mean_usd']!=expected:raise ContractError('CURRENT100K_FORWARD_BASELINE_CHANGED')
 return plan

def feature_rows(plan,request):
 from paired_net_100k_preflight.core import feature_text
 from historical_training.current100k import BEHAVIOR
 scope=plan.get('behavior_scope',{})
 if (plan.get('schema')!=PLAN_SCHEMA or scope.get('behavior_profile')!=BEHAVIOR
     or scope.get('reference_notional_usd')!=100000 or request.get('model_id')!=scope.get('behavior_model_id')):
  raise ContractError('CURRENT100K_FORWARD_FEATURE_SCOPE')
 return [{'feature_text':feature_text(request,side)} for side in ('BUY','SELL')]

def verify_scored_row(plan,binding,prediction,reward,request_item):
 from historical_training.current100k import to_row
 if reward is None:return
 if request_item is None:raise ContractError('CURRENT100K_FORWARD_ORIGINAL_REQUEST_MISSING')
 row=to_row(reward,request_item,binding,plan['behavior_scope']['producer_manifest_sha256'],plan['period_sha256'])
 if row['input_id'] in plan['training_input_ids'] or row['reward_id'] in plan['training_reward_ids']:
  raise ContractError('CURRENT100K_FORWARD_TRAIN_TEST_OVERLAP')
 if prediction is not None:
  text=feature_rows(plan,request_item['request'])
  hashes={side:hashlib.sha256(x['feature_text'].encode()).hexdigest() for side,x in zip(('BUY','SELL'),text)}
  if prediction.get('feature_text_sha256')!=hashes:raise ContractError('CURRENT100K_FORWARD_FEATURE_IDENTITY')

def register(config,candidate_directory,dataset_path,prior_candidate_path,legacy_plans_root):
 from .financial_forward_protocol import RULE
 from .financial_forward_witness import sign_record
 from historical_training.current100k_value_trial import selected_rows
 c=validate_value_candidate(candidate_directory);data=read_json(Path(dataset_path),32*1024**2);selected_rows(data,data)
 prior=validate_value_candidate(Path(prior_candidate_path).parent)
 if not set(prior['row_ids'])<=set(c['row_ids']):raise ContractError('PRIOR_ELEVEN_ROWS_NOT_PRESERVED')
 legacy=[read_json(x) for x in Path(legacy_plans_root).glob('*/plan.json')]
 existing=list((Path(config['forward_root'])/'plans').glob('*/plan.json'))
 if existing:raise ContractError('CURRENT100K_FORWARD_ALREADY_REGISTERED_NO_REPLAY')
 trial=max([x['trial'] for x in legacy]+[0])+1
 now=int(time.time()*1000);start=now+300000;rows=data['rows'];models={x['behavior_scope']['model_id'] for x in rows}
 if len(models)!=1:raise ContractError('MIXED_BEHAVIOR_MODELS')
 source=Path(__file__).parent
 plan={'schema':PLAN_SCHEMA,'candidate_id':c['candidate_id'],'candidate_directory':str(candidate_directory),
  'candidate_manifest_sha256':sha_file(Path(candidate_directory)/'candidate.json'),'head_tensor_sha256':c['head_sha256'],'base_id':c['base_id'],
  'candidate_created_at_ms':c['created_at_ms'],'registered_at_ms':now,'input_start_ms':start,
  'input_end_ms':start+RULE['window_ms'],'outcome_end_ms':start+RULE['window_ms']+RULE['closure_grace_ms'],
  'training_dataset_sha256':sha_file(Path(dataset_path)),'training_dataset_path':str(dataset_path),
  'training_reward_ids':[x['reward_id'] for x in rows],'training_input_ids':[x['input_id'] for x in rows],
  'training_mean_usd':sum(float(x['net_usd']) for x in rows)/len(rows),'prior_candidate_id':prior['candidate_id'],
  'prior_fit_rows':prior['rows'],'current_fit_rows':len(rows),'previous_rows_reused_in_refit':len(prior['row_ids']),
  'fit_relation':'FRESH_HEAD_REFIT_NOT_INCREMENTAL_ACTOR_UPDATES',
  'behavior_scope':{'behavior_profile':data['behavior_profile'],'reference_notional_usd':100000,'instrument':'EUR.USD','currency':'USD',
                    'behavior_model_id':next(iter(models)),'producer_manifest_sha256':data['producer_manifest_sha256']},
  'producer_registration':config['producer_registration'],'period_sha256':config['period_sha256'],'rule':dict(RULE),'trial':trial,'synthetic':False,
  'runtime_sha256':{name:sha_file(source/name) for name in ('current100k_forward.py','financial_forward_protocol.py','financial_forward_source.py',
                   'financial_forward_predict.py','financial_forward_evaluate.py','financial_forward_witness.py')},
  'witness_trust_digest':config['witness_trust_digest'],'registry_root':config['forward_root'],
  'worker_runtime_inventory_digest':config['worker_runtime_inventory_digest'],
  'automatic_effect_authority':False,'candidate_refit_allowed':False,'old_trial_ids_retained':[x['plan_id'] for x in legacy]}
 plan['plan_id']=digest(plan);validate_bound_plan(plan,c)
 folder=Path(config['forward_root'])/'plans'/plan['plan_id'];write_once(folder/'plan.json',plan)
 seal=sign_record(config,'registrar',{'plan_id':plan['plan_id'],'plan_file_sha256':sha_file(folder/'plan.json'),'at_ms':int(time.time()*1000)})
 write_once(folder/'registration.json',seal)
 return plan
