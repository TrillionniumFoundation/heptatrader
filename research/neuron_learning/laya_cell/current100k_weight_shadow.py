"""Frozen Current100k heads on shared features; prospective research, no effects.

The caller owns the one resident encoder. No original trial, actor, driver,
selection rule, or production witness is modified by these local receipts.
"""
from __future__ import annotations
import copy, hashlib, math, os, re, time
from pathlib import Path
from historical_training.common import EvidenceError, atomic, digest, read, sha, write_once
from .financial_forward_protocol import RULE

PLAN_SCHEMA='heptatrader.current100k-weight-shadow-plan.v1'
FEATURE_SCHEMA='heptatrader.current100k-shadow-features.v1'
FEATURE_PROFILE='MASKED_MEAN_L2_NORMALIZED_FP32_768'
SIDES=('BUY','SELL')
DAY_MS=86400000

def clock_ms():return time.time_ns()//1000000
def require(value,reason):
 if not value:raise EvidenceError(reason)
def hash_value(value):
 require(isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value),'SHADOW_DIGEST');return value
def safe_id(value):
 require(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,160}',value),'SHADOW_ID');return value
def integer(value):
 require(type(value)is int and 0<=value<2**63,'SHADOW_INTEGER');return value
def finite(value):
 require(type(value)in(int,float) and math.isfinite(value),'SHADOW_NONFINITE');return float(value)
def numeric_profile(encoder_backend,head_backend='TORCH_CPU_FP32'):
 """Name both execution devices; frozen HIP features may feed cheap CPU heads."""
 require(isinstance(encoder_backend,str),'SHADOW_ENCODER_EXECUTION_PROFILE')
 backend=encoder_backend.upper()
 cpu=backend=='CPU_FP32' or backend.startswith('LAYA_CPU_FP32_')
 hip=backend=='HIP_FP32' or backend.startswith('LAYA_HIP_FP32_')
 require(cpu or hip,'SHADOW_ENCODER_EXECUTION_PROFILE')
 require(head_backend in ('TORCH_CPU_FP32','TORCH_HIP_FP32'),'SHADOW_HEAD_EXECUTION_PROFILE')
 if head_backend=='TORCH_HIP_FP32':
  require(hip,'SHADOW_ENCODER_HEAD_DEVICE_MISMATCH')
  return 'TORCH_HIP_FP32_SHARED_LINEAR_USD100_CLIP15'
 return ('LAYA_HIP_FP32_ENCODER_' if hip else '')+'TORCH_CPU_FP32_SHARED_LINEAR_USD100_CLIP15'
def durable_once(path,value):
 write_once(path,value)
 fd=os.open(Path(path).parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
 try:os.fsync(fd)
 finally:os.close(fd)

def validate_plan(p):
 require(p.get('schema')==PLAN_SCHEMA and p.get('plan_id')==digest({k:v for k,v in p.items() if k!='plan_id'}),'SHADOW_PLAN_CHANGED')
 require(p.get('rule')==RULE,'SHADOW_ORIGINAL_SUPPORT_RULE_CHANGED')
 require(p.get('claim_scope')=='PROSPECTIVE_VALUE_RESEARCH_ONLY' and all(p.get(k)is False for k in ('trade_authority','automatic_selection','actor_update')),'SHADOW_NO_EFFECT_AUTHORITY')
 require(p.get('feature_profile')==FEATURE_PROFILE,'SHADOW_FEATURE_PROFILE')
 for k in ('base_id','period_sha256','training_dataset_sha256','reward_config_sha256','training_producer_manifest_sha256'):hash_value(p[k])
 hash_value(p['producer_registration']['sha256'])
 require(abs(finite(p['training_mean_usd']))<=800,'SHADOW_BASELINE_RANGE')
 for k in ('training_input_ids','training_reward_ids','training_row_ids'):
  values=p[k];require(isinstance(values,list) and 1<=len(values)<=100000 and len(values)==len(set(values)),'SHADOW_EXCLUSIONS')
  for v in values:hash_value(v)
 t=[integer(p[k]) for k in ('training_cutoff_ms','labels_observed_at_ms','registered_at_ms','input_start_ms','input_end_ms','outcome_end_ms')]
 require(t[0]<=t[1]<=t[2]<t[3]<t[4]<t[5],'SHADOW_NOT_PROSPECTIVE')
 require(t[4]-t[3]==RULE['window_ms'] and t[5]-t[4]==RULE['closure_grace_ms'],'SHADOW_WINDOW_CHANGED')
 heads=p['heads'];require(isinstance(heads,dict) and 2<=len(heads)<=16 and p['parent_arm'] in heads,'SHADOW_ARMS')
 for arm,h in heads.items():
  safe_id(arm);hash_value(h['sha256']);require(Path(h['path']).is_absolute() and isinstance(h.get('construction_kind'),str),'SHADOW_HEAD_BINDING')
 require(p.get('cluster_policy')=='UTC_DAYS_MERGED_BY_OVERLAPPING_EPISODES' and p.get('bootstrap_repetitions')==2000 and p.get('bootstrap_seed')==20261103,'SHADOW_INFERENCE_RULE_CHANGED')
 require(isinstance(p.get('encoder_backend'),str) and 0<len(p['encoder_backend'])<=160 and type(p.get('encoder_threads'))is int and 1<=p['encoder_threads']<=64 and p.get('encoder_batch_size') in (1,2),'SHADOW_ENCODER_EXECUTION_PROFILE')
 require(p.get('numeric_profile')==numeric_profile(p['encoder_backend'],p.get('head_execution_backend','TORCH_CPU_FP32')),'SHADOW_NUMERIC_PROFILE')
 if 'HIP' in p['numeric_profile']:hash_value(p.get('hip_runtime_manifest_sha256'))
 else:require(p.get('hip_runtime_manifest_sha256')is None,'SHADOW_CPU_HAS_HIP_RUNTIME')
 require(p['cross_producer_transfer']==(p['training_producer_manifest_sha256']!=p['producer_registration']['sha256']),'SHADOW_TRANSFER_LINEAGE')

def freeze(c,root):
 """Seal identities now, then start a new 14-day input window in five minutes."""
 root=Path(root);require(root.is_absolute() and not root.is_symlink(),'SHADOW_PRIVATE_ROOT')
 require(not (root/'plan.json').exists(),'SHADOW_ALREADY_REGISTERED')
 require(c.get('schema')=='heptatrader.current100k-weight-shadow-config.v1','SHADOW_CONFIG')
 require(all(c.get(k)is False for k in ('trade_authority','automatic_selection','actor_update')),'SHADOW_NO_EFFECT_AUTHORITY')
 for arm,h in c['heads'].items():require(sha(h['path'])==h['sha256'],'SHADOW_HEAD_CHANGED:'+arm)
 require(sha(c['reward_config_path'])==c['reward_config_sha256'],'SHADOW_REWARD_CONFIG_CHANGED')
 rc=read(c['reward_config_path'])
 require(rc['period_sha256']==c['period_sha256'] and sha(rc['period_path'])==c['period_sha256'],'SHADOW_PERIOD_CHANGED')
 from historical_training.current100k import validate_config
 validate_config({'automatic_trading':False,'automatic_training':False,'period_path':rc['period_path'],'period_sha256':c['period_sha256'],'registration':c['producer_registration']})
 require(sha(c['training_dataset_path'])==c['training_dataset_sha256'],'SHADOW_TRAINING_DATA_CHANGED')
 data=read(c['training_dataset_path']);rows=data['rows']
 require(set(c['training_input_ids'])=={r['input_id'] for r in rows} and set(c['training_reward_ids'])=={r['reward_id'] for r in rows} and set(c['training_row_ids'])=={r['row_id'] for r in rows},'SHADOW_TRAINING_EXCLUSIONS_CHANGED')
 require(c['training_producer_manifest_sha256']==data['producer_manifest_sha256'],'SHADOW_TRAINING_PRODUCER_CHANGED')
 expected_mean=sum(float(r['net_usd']) for r in rows)/len(rows)
 require(c['training_mean_usd']==expected_mean,'SHADOW_TRAINING_BASELINE_CHANGED')
 require(c['training_cutoff_ms']>=max(r['input_started_at_ms'] for r in rows) and c['labels_observed_at_ms']>=data.get('observed_at_ms',0),'SHADOW_TRAINING_TIME_CHANGED')
 keys=('base_id','behavior_model_id','period_sha256','producer_registration','heads','parent_arm','training_dataset_sha256','training_dataset_path','training_mean_usd','training_input_ids','training_reward_ids','training_row_ids','training_cutoff_ms','labels_observed_at_ms','reward_config_path','reward_config_sha256','training_producer_manifest_sha256','encoder_backend','encoder_threads','encoder_batch_size')
 p={k:copy.deepcopy(c[k]) for k in keys};now=clock_ms();start=now+300000
 head_backend=c.get('head_execution_backend','TORCH_CPU_FP32')
 p.update(schema=PLAN_SCHEMA,registered_at_ms=now,input_start_ms=start,input_end_ms=start+RULE['window_ms'],outcome_end_ms=start+RULE['window_ms']+RULE['closure_grace_ms'],rule=copy.deepcopy(RULE),feature_profile=FEATURE_PROFILE,numeric_profile=numeric_profile(p['encoder_backend'],head_backend),head_execution_backend=head_backend,hip_runtime_manifest_sha256=c.get('hip_runtime_manifest_sha256'),claim_scope='PROSPECTIVE_VALUE_RESEARCH_ONLY',trade_authority=False,automatic_selection=False,actor_update=False,cluster_policy='UTC_DAYS_MERGED_BY_OVERLAPPING_EPISODES',bootstrap_repetitions=2000,bootstrap_seed=20261103,historical_trials_modified=False,labels_viewed_rows_are_development=True,cross_producer_transfer=c['training_producer_manifest_sha256']!=c['producer_registration']['sha256'],official_registration_rule='NEW_PRODUCER_SCOPE_REQUIRED_FOR_FORMAL_ADOPTION')
 p['plan_id']=digest(p);validate_plan(p);root.mkdir(mode=0o700,parents=True,exist_ok=True);root.chmod(0o700)
 durable_once(root/'plan.json',p)
 receipt={'plan_id':p['plan_id'],'plan_sha256':sha(root/'plan.json'),'durable_at_ms':clock_ms(),'local_receipt_only':True}
 require(receipt['durable_at_ms']<start,'SHADOW_REGISTRATION_TOO_LATE');durable_once(root/'registration.json',receipt);return p

def validate_features(features):
 require(isinstance(features,dict) and set(features)==set(SIDES),'SHADOW_FEATURE_SIDES')
 for values in features.values():
  require(isinstance(values,list) and len(values)==768,'SHADOW_FEATURE_SHAPE');values=[finite(v) for v in values]
  require(abs(math.fsum(v*v for v in values)-1.)<=.001,'SHADOW_FEATURE_NOT_L2_NORMALIZED')

def validate_runtime_proof(plan,features):
 from .resident_hip_runtime import check_publication_proof
 manifest=plan.get('hip_runtime_manifest_sha256')
 require(features.get('hip_runtime_manifest_sha256')==manifest,'SHADOW_HIP_RUNTIME_MISMATCH')
 check_publication_proof(features.get('hip_runtime_proof'),manifest,'predict')

def validate_predictions(p,predictions):
 require(isinstance(predictions,dict) and set(predictions)==set(p['heads']),'SHADOW_ARM_COVERAGE')
 for arm,v in predictions.items():
  require(v['head_sha256']==p['heads'][arm]['sha256'],'SHADOW_PREDICTION_HEAD')
  for k in ('raw_scaled','raw_usd','values_usd'):
   require(set(v[k])==set(SIDES),'SHADOW_PREDICTION_SIDES')
   for x in v[k].values():finite(x)
  for side in SIDES:
   raw=v['raw_scaled'][side]*RULE['target_scale_usd']
   require(v['raw_usd'][side]==raw and v['values_usd'][side]==max(-15.,min(15.,raw)),'SHADOW_PREDICTION_SCALE_OR_CLIP')

class HeadEnsemble:
 """Load only the small heads once; all arms use the exact same FP32 features."""
 def __init__(self,plan):
  validate_plan(plan)
  import torch
  from safetensors.torch import load_file
  self.torch=torch;self.plan=plan;self.arms=sorted(plan['heads']);weights=[];biases=[]
  self.device='cuda' if plan.get('head_execution_backend')=='TORCH_HIP_FP32' else 'cpu'
  if self.device=='cuda':require(torch.version.hip is not None and torch.cuda.is_available(),'SHADOW_HIP_UNAVAILABLE')
  for arm in self.arms:
   h=plan['heads'][arm];require(sha(h['path'])==h['sha256'],'SHADOW_HEAD_CHANGED:'+arm)
   ts=load_file(h['path'],device='cpu');require(set(ts)=={'value.weight','value.bias'},'SHADOW_HEAD_KEYS')
   w,b=ts['value.weight'],ts['value.bias'];require(w.shape==(1,768) and b.shape==(1,) and w.dtype==b.dtype==torch.float32,'SHADOW_HEAD_SHAPE_DTYPE')
   require(bool(torch.isfinite(w).all() and torch.isfinite(b).all()),'SHADOW_HEAD_NONFINITE');weights.append(w.detach());biases.append(b.detach())
  self.weights=torch.cat(weights).contiguous().to(self.device);self.biases=torch.cat(biases).contiguous().to(self.device)
 def predict(self,features_by_side):
  torch=self.torch;validate_features(features_by_side)
  x=torch.tensor([features_by_side[s] for s in SIDES],dtype=torch.float32,device=self.device)
  with torch.inference_mode():values=torch.nn.functional.linear(x,self.weights,self.biases).tolist()
  result={}
  for i,arm in enumerate(self.arms):
   scaled={s:finite(values[j][i]) for j,s in enumerate(SIDES)};raw={s:v*100 for s,v in scaled.items()}
   result[arm]={'head_sha256':self.plan['heads'][arm]['sha256'],'raw_scaled':scaled,'raw_usd':raw,'values_usd':{s:max(-15.,min(15.,v)) for s,v in raw.items()}}
  return result

class ShadowStore:
 def __init__(self,root):
  self.root=Path(root);require(self.root.is_absolute() and not self.root.is_symlink(),'SHADOW_PRIVATE_ROOT')
  self.plan=read(self.root/'plan.json');validate_plan(self.plan);r=read(self.root/'registration.json')
  require(r['plan_id']==self.plan['plan_id'] and r['plan_sha256']==sha(self.root/'plan.json') and self.plan['registered_at_ms']<=r['durable_at_ms']<self.plan['input_start_ms'],'SHADOW_PLAN_REGISTRATION')
 def seal(self,request_item,feature_document,predictions):
  """One durable publication for all arms. Interrupted attempts are not backfilled."""
  from paired_net_100k_preflight.core import check_request,feature_text
  p=self.plan;r=check_request(request_item,p['producer_registration']['sha256'],p['period_sha256'])
  rid=safe_id(r['request_id']);folder=self.root/'predictions'/rid
  if (folder/'publication.json').exists() and (folder/'observation.json').exists():
   previous=self.prediction(rid);require(previous['request_sha256']==request_item['request_sha256'],'SHADOW_REQUEST_CHANGED');return previous
  require(not (folder/'prediction.json').exists() and not (folder/'commit.json').exists(),'SHADOW_INTERRUPTED_SEAL_NO_BACKFILL')
  now=clock_ms();require(p['input_start_ms']<=r['started_at_ms']<=now<p['input_end_ms'],'SHADOW_INPUT_OUTSIDE_FUTURE_WINDOW')
  require(request_item['request_sha256'] not in p['training_input_ids'],'SHADOW_DEVELOPMENT_OVERLAP')
  require(r['model_id']==p['behavior_model_id'] and r['position_at_request']==0,'SHADOW_BEHAVIOR_SCOPE')
  f=feature_document;require(f.get('schema')==FEATURE_SCHEMA and f.get('feature_profile')==FEATURE_PROFILE,'SHADOW_FEATURE_SCHEMA')
  require(f.get('backend')==p['encoder_backend'] and f.get('encoder_threads')==p['encoder_threads'] and f.get('encoder_batch_size')==p['encoder_batch_size'],'SHADOW_ENCODER_PROFILE_MISMATCH')
  validate_runtime_proof(p,f)
  for k,want in (('request_id',rid),('request_sha256',request_item['request_sha256']),('producer_manifest_sha256',p['producer_registration']['sha256']),('period_sha256',p['period_sha256']),('base_id',p['base_id']),('input_started_at_ms',r['started_at_ms'])):
   require(f.get(k)==want,'SHADOW_FEATURE_IDENTITY:'+k)
  require(r['started_at_ms']<=integer(f['computed_at_ms'])<=now,'SHADOW_FEATURE_CLOCK')
  expected={s:hashlib.sha256(feature_text(r,s).encode()).hexdigest() for s in SIDES}
  require(f.get('feature_text_sha256')==expected,'SHADOW_FEATURE_TEXT');validate_features(f['features']);validate_predictions(p,predictions)
  payload={'schema':'heptatrader.current100k-weight-shadow-prediction.v1','plan_id':p['plan_id'],'request_id':rid,'request_sha256':request_item['request_sha256'],'input_started_at_ms':r['started_at_ms'],'computed_at_ms':now,'producer_manifest_sha256':p['producer_registration']['sha256'],'source_record_sha256':request_item['source_record_sha256'],'feature_digest':digest(f),'feature_text_sha256':expected,'predictions':predictions}
  durable_once(folder/'features.json',f);durable_once(folder/'prediction.json',payload)
  commit={'plan_id':p['plan_id'],'request_id':rid,'prediction_sha256':sha(folder/'prediction.json'),'features_sha256':sha(folder/'features.json'),'local_receipt_only':True}
  durable_once(folder/'commit.json',commit)
  # Timestamp after BOTH files and their containing directory have been fsynced.
  completed=clock_ms();require(now<=completed<p['input_end_ms'],'SHADOW_SEAL_CLOCK_OR_DEADLINE')
  publication={**commit,'request_sha256':request_item['request_sha256'],'commit_sha256':sha(folder/'commit.json'),'seal_durable_completed_at_ms':completed}
  durable_once(folder/'publication.json',publication)
  observed=clock_ms();require(completed<=observed<p['input_end_ms'],'SHADOW_PUBLICATION_CLOCK_OR_DEADLINE')
  durable_once(folder/'observation.json',{'publication_sha256':sha(folder/'publication.json'),'publication_durability_observed_at_ms':observed,'local_receipt_only':True})
  return self.prediction(rid)
 def prediction(self,request_id):
  folder=self.root/'predictions'/safe_id(request_id)
  if not (folder/'publication.json').exists() or not (folder/'observation.json').exists():return None
  publication=read(folder/'publication.json');commit=read(folder/'commit.json');value=read(folder/'prediction.json');observed=read(folder/'observation.json')
  require(publication['plan_id']==commit['plan_id']==value['plan_id']==self.plan['plan_id'] and publication['request_id']==commit['request_id']==value['request_id']==request_id,'SHADOW_COMMIT_IDENTITY')
  require(publication['commit_sha256']==sha(folder/'commit.json') and publication['prediction_sha256']==commit['prediction_sha256']==sha(folder/'prediction.json') and publication['features_sha256']==commit['features_sha256']==sha(folder/'features.json'),'SHADOW_COMMIT_CHANGED')
  require(publication['request_sha256']==value['request_sha256'] and self.plan['input_start_ms']<=value['input_started_at_ms']<=value['computed_at_ms']<=publication['seal_durable_completed_at_ms']<self.plan['input_end_ms'],'SHADOW_SEAL_TIME_ORDER')
  require(observed['publication_sha256']==sha(folder/'publication.json') and publication['seal_durable_completed_at_ms']<=observed['publication_durability_observed_at_ms']<self.plan['input_end_ms'],'SHADOW_PUBLICATION_OBSERVATION')
  features=read(folder/'features.json');require(value['feature_digest']==digest(features),'SHADOW_FEATURE_DIGEST_CHANGED');validate_runtime_proof(self.plan,features)
  validate_predictions(self.plan,value['predictions'])
  return {**value,'committed_at_ms':observed['publication_durability_observed_at_ms'],'seal_durable_completed_at_ms':observed['publication_durability_observed_at_ms'],'publication_durability_observed_at_ms':observed['publication_durability_observed_at_ms'],'prediction_sha256':publication['prediction_sha256'],'publication_path':str(folder/'publication.json'),'publication_sha256':sha(folder/'publication.json'),'observation_path':str(folder/'observation.json'),'observation_sha256':sha(folder/'observation.json')}
 def remember_commands(self,bindings):
  folder=self.root/'seen-commands';folder.mkdir(mode=0o700,parents=True,exist_ok=True)
  for b in bindings:
   if self.plan['input_start_ms']<=b['request_started_at_ms']<self.plan['input_end_ms']:
    durable_once(folder/(safe_id(b['command']['command_id'])+'.json'),b)
  return {p.stem:read(p) for p in sorted(folder.glob('*.json'))}

def score_case(p,b,prediction,request_item,reward,first_fill_ms,closed_at_ms,now_ms):
 """A current, exact, fee-complete reward is the only observed scalar target."""
 cmd=b['command'];result={'command_id':cmd['command_id'],'request_id':b['request_id'],'opened_at_ms':cmd['created_at_ms'],'status':'CENSORED','reason':None}
 def reject(reason):return {**result,'reason':reason}
 if not p['input_start_ms']<=b['request_started_at_ms']<p['input_end_ms']:return reject('INPUT_OUTSIDE_WINDOW')
 if b['request_sha256'] in p['training_input_ids']:return reject('DEVELOPMENT_INPUT_OVERLAP')
 if prediction is None:return reject('NO_DURABLE_PREDICTION_FOR_ORIGINAL_REQUEST')
 if prediction['request_sha256']!=b['request_sha256'] or prediction['request_id']!=b['request_id'] or prediction['producer_manifest_sha256']!=p['producer_registration']['sha256']:return reject('PREDICTION_OR_PRODUCER_IDENTITY')
 if first_fill_ms is not None and prediction['committed_at_ms']>=first_fill_ms:return reject('PREDICTION_AFTER_FIRST_FILL')
 if not b['request_started_at_ms']<=prediction['computed_at_ms']<=prediction['committed_at_ms']<cmd['created_at_ms']:return reject('NOT_COMMITTED_BEFORE_ORIGINAL_PREPARED')
 if reward is None:return {**result,'status':'PENDING' if now_ms<p['outcome_end_ms'] else 'CENSORED','reason':'AWAITING_SETTLED_EXACT_OUTCOME' if now_ms<p['outcome_end_ms'] else 'FIXED_OUTCOME_GRACE_EXPIRED'}
 if first_fill_ms is None or closed_at_ms is None:return reject('ACTUAL_EXECUTION_TIME_WITNESS_MISSING')
 if not cmd['created_at_ms']<=first_fill_ms<=closed_at_ms<=now_ms:return reject('ACTUAL_EXECUTION_CLOCK_INVALID')
 if closed_at_ms>p['outcome_end_ms']:return reject('OUTCOME_AFTER_FIXED_GRACE')
 if request_item is None:return reject('ORIGINAL_REQUEST_MISSING')
 if reward['reward_id'] in p['training_reward_ids']:return reject('DEVELOPMENT_REWARD_OVERLAP')
 from historical_training.current100k import to_row
 try:row=to_row(reward,request_item,b,p['producer_registration']['sha256'],p['period_sha256'])
 except (ValueError,KeyError,TypeError) as exc:return reject('EXACT_REWARD_REJECTED:'+str(exc)[:160])
 if row['row_id'] in p['training_row_ids'] or row['behavior_scope']['model_id']!=p['behavior_model_id']:return reject('DEVELOPMENT_ROW_OR_BEHAVIOR_MISMATCH')
 side=row['action']
 if prediction['feature_text_sha256'][side]!=row['feature_text_sha256']:return reject('PREDICTION_FEATURE_TEXT_MISMATCH')
 target=finite(float(row['net_usd']));validate_predictions(p,prediction['predictions'])
 return {**result,'status':'OBSERVED','reason':None,'closed_at_ms':closed_at_ms,'first_fill_ms':first_fill_ms,'prediction_committed_at_ms':prediction['committed_at_ms'],'side':side,'target_usd':target,'fee_usd':finite(float(row['actual_commissions_usd'])),'reward_id':row['reward_id'],'target_exceeds_training_support':abs(target)>800,'predictions':{a:v['values_usd'][side] for a,v in prediction['predictions'].items()}}
