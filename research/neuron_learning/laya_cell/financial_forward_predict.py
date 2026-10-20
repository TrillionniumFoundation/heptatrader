"""Warm, read-only financial predictions for real requests before original OPEN.

Both action conditions are sealed regardless of the eventual choice. No model
output is sent to the trader. No optimizer, future reward or execution client is
used by this worker. Missed deadlines remain missed, never backdated.
"""
from __future__ import annotations
import argparse,copy,fcntl,hashlib,json,os,signal,time
from pathlib import Path
from .integrity import ContractError,read_json,sha_file,digest
_original_sha_file=sha_file
def sha_file(path):return _original_sha_file(Path(path))
from .resident_store import write_once,atomic
from .financial_forward_protocol import RULE,feature_text,validate_plan,validate_candidate
from .financial_forward_source import ingress,file_identity


def pin(config,plan):
 validate_plan(plan,int(time.time()*1000))
 if plan['period_sha256']!=config['period_sha256']:raise ContractError('PREDICT_PERIOD_SCOPE')
 for name,expected in plan['runtime_sha256'].items():
  if sha_file(Path(__file__).parent/name)!=expected:raise ContractError('FROZEN_FORWARD_RUNTIME_CHANGED')
 directory=Path(plan['candidate_directory'])
 c=validate_candidate(directory)
 if c['candidate_id']!=plan['candidate_id'] or sha_file(directory/'candidate.json')!=plan['candidate_manifest_sha256']:
  raise ContractError('CANDIDATE_SUBSTITUTION')
 if (c.get('schema')=='heptatrader.current100k-value-candidate.v1')!=(plan.get('schema')=='heptatrader.current100k-value-forward-plan.v1'):
  raise ContractError('VALUE_CANDIDATE_AND_PLAN_BEHAVIOR_MISMATCH')
 if plan.get('schema')=='heptatrader.current100k-value-forward-plan.v1':
  from .current100k_forward import validate_bound_plan
  validate_bound_plan(plan,c)
 return c

class RegisteredPlanSnapshot:
 """Reuse a fully verified registration only within this bounded process.

 Frozen bytes remain SHA-pinned on every use. The costly per-training-row
 journal replay repeats only when an original witness file changes.
 """
 def __init__(self,config,path,plan):
  self.config_digest=digest(config);self.plan_digest=digest(plan)
  self.path=Path(path);self.plan=copy.deepcopy(plan);self.config=copy.deepcopy(config)
  if plan['period_sha256']!=config['period_sha256']:raise ContractError('PREDICT_PERIOD_SCOPE')
  if read_json(self.path)!=plan:raise ContractError('VALUE_REGISTERED_PLAN_BYTES_MISMATCH')
  self.pins={};self.full_validations=0;self.reuses=0
  def add(path,want):
   name=str(Path(path))
   if name in self.pins and self.pins[name]!=want:raise ContractError('VALUE_PIN_COLLISION')
   self.pins[name]=want
  add(config['period_path'],config['period_sha256'])
  add(config['witness_trust_path'],config['witness_trust_sha256'])
  add(self.path,sha_file(self.path))
  add(self.path.parent/'registration.json',sha_file(self.path.parent/'registration.json'))
  directory=Path(plan['candidate_directory'])
  add(directory/'candidate.json',plan['candidate_manifest_sha256'])
  add(directory/'value-head.safetensors',plan['head_tensor_sha256'])
  add(plan['training_dataset_path'],plan['training_dataset_sha256'])
  for name,want in plan['runtime_sha256'].items():add(Path(__file__).parent/name,want)
  for name,want in config.get('worker_runtime_sha256',{}).items():add(Path(config['worker_source_root'])/name,want)
  self._verify_pins()
  self.source_paths=[]
  if plan.get('schema')=='heptatrader.current100k-value-forward-plan.v1':
   data=read_json(Path(plan['training_dataset_path']),32*1024**2)
   self.source_paths=sorted({w['path'] for row in data['rows'] for w in row['reward']['input_attribution']['source_prefixes']})
  self._validate()
 def _verify_pins(self):
  for path,want in self.pins.items():
   if sha_file(path)!=want:raise ContractError('VALUE_REGISTERED_TURN_BYTES_CHANGED:'+path)
 def _source_identity(self):return [(p,file_identity(p)) for p in self.source_paths]
 def _validate(self):
  from .financial_forward_evaluate import validate_registration
  before=self._source_identity()
  candidate=validate_registration(self.config,self.path,self.plan)
  self._verify_pins();after=self._source_identity()
  if before!=after:raise ContractError('VALUE_TRAINING_SOURCE_CHANGED_DURING_PIN')
  self._sources=after;self._candidate=candidate;self.full_validations+=1
 def candidate(self,config,plan):
  if digest(config)!=self.config_digest or digest(plan)!=self.plan_digest:
   raise ContractError('VALUE_REGISTERED_TURN_SCOPE_CHANGED')
  self._verify_pins()
  if self._source_identity()!=self._sources:self._validate()
  else:self.reuses+=1
  validate_plan(plan,int(time.time()*1000))
  return copy.deepcopy(self._candidate)


class PhaseClock:
 """Local monotonic durations; never substitutes for signed wall timestamps."""
 def __init__(self):self.phases={};self.last=time.perf_counter()
 def mark(self,name):
  now=time.perf_counter();self.phases[name]=(now-self.last)*1000.;self.last=now


def publication_deadline(plan, request, now_ms, computed_at_ms=None):
 """Candidate-only guard; no reinterpretation of an older frozen experiment.

 Checks the latest local clock before I/O, not a hard real-time guarantee that
 a future filesystem write completes before the deadline.
 """
 start=request.get('started_at_ms'); end=plan.get('input_end_ms')
 if any(type(v) is not int or v<=0 for v in (start,end,now_ms)):
  raise ContractError('VALUE_PUBLICATION_TIME_TYPE')
 if now_ms<start or (computed_at_ms is not None and (type(computed_at_ms) is not int or now_ms<computed_at_ms)):
  raise ContractError('VALUE_PUBLICATION_CLOCK_REVERSED')
 if now_ms>=min(start+60000,end):
  raise ContractError('VALUE_EXPIRED_BEFORE_PUBLICATION_NO_BACKDATING')
 return now_ms


class PublicationBudget:
 """One non-renewable local budget, anchored before pin/load/inference.

 Wall time retains the frozen request/window cutoff; monotonic elapsed time
 prevents a clock correction from granting extra computation time. This does
 not attest remote time or guarantee completion of the next filesystem call.
 """
 def __init__(self, plan, request, now_ms):
  self.binding=(plan['plan_id'],plan['candidate_id'],request['request_id'],
                request['started_at_ms'],plan['input_end_ms'])
  self.last_wall=publication_deadline(plan,request,now_ms)
  self.limit_ns=(min(request['started_at_ms']+60000,plan['input_end_ms'])-now_ms)*1000000
  self.started_ns=time.monotonic_ns();self.last_ns=self.started_ns;self.failed=False
 def check(self, plan, request, now_ms, lower_ms=None):
  if self.failed:raise ContractError('VALUE_PUBLICATION_BUDGET_ALREADY_FAILED')
  try:
   if self.binding!=(plan['plan_id'],plan['candidate_id'],request['request_id'],
                     request['started_at_ms'],plan['input_end_ms']):
    raise ContractError('VALUE_PUBLICATION_BUDGET_SCOPE_CHANGED')
   tick=time.monotonic_ns()
   if type(tick) is not int or tick<self.last_ns or now_ms<self.last_wall:
    raise ContractError('VALUE_PUBLICATION_CLOCK_REVERSED')
   if tick-self.started_ns>=self.limit_ns:
    raise ContractError('VALUE_EXPIRED_MONOTONIC_BUDGET_NO_BACKDATING')
   publication_deadline(plan,request,now_ms,lower_ms)
   self.last_ns=tick;self.last_wall=now_ms
   return now_ms
  except Exception:
   self.failed=True
   raise


def publish_prediction(config, plan, request, folder, payload, budget=None):
 now=int(time.time()*1000)
 if budget is None:budget=PublicationBudget(plan,request,now)
 budget.check(plan,request,now,payload['computed_at_ms'])
 write_once(folder/'prediction.json',payload)
 commit_at=budget.check(plan,request,int(time.time()*1000),payload['computed_at_ms'])
 commit={'schema':'heptatrader.financial-forward-commit.v1','prediction_sha256':sha_file(folder/'prediction.json'),
  'committed_at_ms':commit_at,'plan_id':plan['plan_id'],'request_id':request['request_id'],'candidate_id':plan['candidate_id']}
 from .financial_forward_witness import sign_record
 commit=sign_record(config,'predictor',commit)
 budget.check(plan,request,commit['role_witness']['signed_at_ms'],commit_at)
 budget.check(plan,request,int(time.time()*1000),commit['role_witness']['signed_at_ms'])
 write_once(folder/'commit.json',commit)
 return commit


class Predictor:
 def __init__(self,config,*,ingress_snapshots=None,registered_plans=None,front_timing=None):
  self.c=config;self.root=Path(config['forward_root']);self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
  self.stop=False;self.raw=None;self.tokenizer=None;self.heads={};self.started=int(time.time()*1000);self.log_ms=0
  self.ingress_snapshots=ingress_snapshots or {};self.registered_plans=registered_plans or {}
  self.front_timing=copy.deepcopy(front_timing or {})
  self.current_prediction_failures=[];self.publication_timings_ms=[]
 def _front_receipt(self):
  return {**copy.deepcopy(self.front_timing),
   'schema':'heptatrader.value-prediction-front-timing.v1',
   'clock_scope':'LOCAL_MONOTONIC_PHASES_NOT_REMOTE_ATTESTATION',
   'ingress_full_scans':sum(x.full_scans for x in self.ingress_snapshots.values()),
   'ingress_snapshot_reuses':sum(x.reuses for x in self.ingress_snapshots.values()),
   'registration_full_validations':sum(x.full_validations for x in self.registered_plans.values()),
   'registration_snapshot_reuses':sum(x.reuses for x in self.registered_plans.values())}
 def load(self):
  if self.raw is not None:return
  import torch
  from .model import load_verified_laya
  torch.set_num_threads(2)
  self.raw,self.tokenizer,self.base_id=load_verified_laya(Path(self.c['base_directory']),read_json(Path(self.c['base_directory'])/'base-manifest.json'))
  self.raw.requires_grad_(False).eval()
  atomic(self.root/'predictor-start.json',{'at_ms':int(time.time()*1000),'base_id':self.base_id,
   'gpu_arch':str(torch.cuda.get_device_properties(0).gcnArchName),'source_scope':'ACTUAL_CLI_REQUESTS_ONLY','optimizer_used':False,'broker_orders_sent':0})
 def predict(self,plan,item):
  r=item['request'];key=plan['candidate_id'];now=int(time.time()*1000);rid=r['request_id']
  if not isinstance(rid,str) or rid in ('','.','..') or Path(rid).name!=rid:raise ContractError('REQUEST_PATH_ESCAPE')
  if r['model_id']!=plan['behavior_scope']['behavior_model_id'] or item['producer_manifest_sha256']!=plan['behavior_scope']['producer_manifest_sha256']:
   raise ContractError('BEHAVIOR_SCOPE_CHANGED')
  if not plan['input_start_ms']<=r['started_at_ms']<plan['input_end_ms'] or r['position_at_request']!=0:return None
  folder=self.root/'plans'/plan['plan_id']/'predictions'/rid
  if (folder/'commit.json').exists() or (folder/'failure.json').exists():return None
  folder.mkdir(mode=0o700,parents=True,exist_ok=True)
  if now-r['started_at_ms']>60000:
   write_once(folder/'failure.json',{'reason':'REQUEST_ALREADY_OLD_NO_BACKDATING','observed_at_ms':now,'request_id':rid});return None
  phases=PhaseClock()
  try:
   budget=PublicationBudget(plan,r,int(time.time()*1000))
   prepared=self.registered_plans.get(plan['plan_id'])
   c=prepared.candidate(self.c,plan) if prepared is not None else pin(self.c,plan)
   phases.mark('budget_and_candidate_validation')
   self.load();phases.mark('model_load')
   import torch
   from safetensors.torch import load_file
   from .financial_value_train import FinancialHead,features_from_laya
   if c['base_id']!=self.base_id:raise ContractError('FINANCIAL_BASE_MISMATCH')
   if key not in self.heads:
    head=FinancialHead(torch.device('cuda'));tensors=load_file(str(Path(plan['candidate_directory'])/'value-head.safetensors'))
    if set(tensors)!=set(head.state_dict()) or any(not torch.isfinite(t).all() for t in tensors.values()):raise ContractError('FINANCIAL_TENSOR_SCOPE')
    head.load_state_dict(tensors,strict=True);head.requires_grad_(False).eval()
    if float(torch.linalg.vector_norm(torch.cat([t.float().flatten() for t in tensors.values()])))>.100001:raise ContractError('READOUT_NORM_BOUND')
    self.heads[key]=head
   head=self.heads[key]
   if plan.get('schema')=='heptatrader.current100k-value-forward-plan.v1':
    from .current100k_forward import feature_rows
    rows=feature_rows(plan,r)
   else:rows=[{'feature_text':feature_text(r,side)} for side in ('BUY','SELL')]
   phases.mark('head_and_feature_preparation');begin=time.monotonic()
   if plan.get('schema')=='heptatrader.current100k-value-forward-plan.v1':
    encoded=[features_from_laya(self.raw,self.tokenizer,[row]) for row in rows]
    features=torch.cat([x[0] for x in encoded]);info={'serial_action_features':[x[1] for x in encoded]}
   else:features,info=features_from_laya(self.raw,self.tokenizer,rows)
   with torch.no_grad():values=(head(features)*100).cpu().tolist()
   if any(not torch.isfinite(t).all() for t in head.parameters()):raise ContractError('VALUE_NONFINITE')
   inference_seconds=time.monotonic()-begin;phases.mark('inference');computed=int(time.time()*1000)
   payload={'schema':'heptatrader.financial-forward-prediction.v1','plan_id':plan['plan_id'],'candidate_id':key,
    'request_id':rid,'request_sha256':item['request_sha256'],'producer_manifest_sha256':item['producer_manifest_sha256'],
    'prompt_sha256':r['prompt_sha256'],'request_started_at_ms':r['started_at_ms'],
    'request_source_written_at_ms':item['source_written_at_ms'],'request_prefix':item['source_prefix'],
    'values_usd':dict(zip(('BUY','SELL'),values)),'computed_at_ms':computed,
    'feature_text_sha256':{side:hashlib.sha256(row['feature_text'].encode()).hexdigest() for side,row in zip(('BUY','SELL'),rows)},
    'feature_receipt':info,'inference_seconds':inference_seconds,'candidate_refit':False,
    'front_timing':{**self._front_receipt(),'prediction_entered_at_ms':now,'prediction_phases_ms':dict(phases.phases)},
    'side_choice_provided_to_trader':False,'output_scope':'CONDITIONAL_ROUNDTRIP_NET_USD_NOT_TRADE_SIGNAL',
    'both_action_conditions_committed_without_response_input':True,'broker_orders_sent':0}
   started=time.perf_counter()
   try:return publish_prediction(self.c,plan,r,folder,payload,budget)
   finally:self.publication_timings_ms.append((time.perf_counter()-started)*1000.)
  except Exception as exc:
   failure={'reason':type(exc).__name__+':'+str(exc)[:220],'observed_at_ms':int(time.time()*1000),'request_id':rid,
            'prediction_phases_ms':dict(phases.phases)}
   self.current_prediction_failures.append(failure);write_once(folder/'failure.json',failure)
   return None
 def step(self):
  if sha_file(Path(self.c['period_path']))!=self.c['period_sha256']:raise ContractError('FINANCIAL_PREDICT_PERIOD_CHANGED')
  plans=[read_json(p) for p in sorted((self.root/'plans').glob('*/plan.json'))];now=int(time.time()*1000);new=0;issues=[]
  self.current_prediction_failures=[];self.publication_timings_ms=[]
  if len(plans)>32:raise ContractError('MAX_FORWARD_PLAN_BUDGET')
  if self.registered_plans and set(self.registered_plans)!={p['plan_id'] for p in plans}:
   raise ContractError('VALUE_TURN_PLAN_SET_CHANGED')
  for plan in plans:
   if now>=plan['input_end_ms']:continue
   try:
    started=time.perf_counter();snapshot=self.ingress_snapshots.get(plan['plan_id'])
    if snapshot is None:_,requests,_,problems=ingress(plan['producer_registration'],self.c['period_sha256'])
    else:_,requests,_,problems=snapshot.read(plan['producer_registration'],self.c['period_sha256'])
    self.front_timing.setdefault('worker_phases_ms',{})['predictor_ingress_check']=(time.perf_counter()-started)*1000.
    issues+=problems
    if problems:continue
    items=sorted(requests.values(),key=lambda x:(now-x['request']['started_at_ms']>=60000,x['request']['started_at_ms']))
    for item in items:
     if self.predict(plan,item) is not None:new+=1
   except Exception as exc:issues.append({'plan_id':plan['plan_id'],'reason':str(exc)[:220]})
  historical_failures=len(list((self.root/'plans').glob('*/predictions/*/failure.json')))
  state=('HOLD_SOURCE_PROVENANCE' if issues else 'HOLD_CURRENT_PREDICTION_FAILURE' if self.current_prediction_failures
         else 'READY_ORIGINAL_REQUEST_PREDICTIONS' if new else 'NO_PREDICTION_COMMITTED_THIS_TURN')
  status={'schema':'heptatrader.financial-forward-predictor-status.v1','at_ms':int(time.time()*1000),'state':state,
   'plans':len(plans),'new_predictions':new,'sealed_predictions':len(list((self.root/'plans').glob('*/predictions/*/commit.json'))),
   'failed_or_missed_requests':historical_failures,'historical_failed_or_missed_requests':historical_failures,
   'issues':issues,'current_issues':issues,'current_prediction_failures':self.current_prediction_failures,
   'front_timing':self._front_receipt(),'publication_timings_ms':self.publication_timings_ms,
   'loaded_heads':len(self.heads),'actor_updated':False,'broker_orders_sent':0}
  atomic(self.root/'predictor-status.json',status)
  if new or now-self.log_ms>30000:print(json.dumps(status),flush=True);self.log_ms=now
  return status


def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();os.umask(0o077)
 c=read_json(a.config);root=Path(c['forward_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
 fd=os.open(root/'predictor.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 worker=Predictor(c)
 def stop(*_):worker.stop=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 worker.load()
 while not worker.stop:
  try:worker.step()
  except Exception as exc:atomic(root/'predictor-status.json',{'at_ms':int(time.time()*1000),'state':'UNAVAILABLE_NO_TRADE_EFFECT','reason':str(exc)[:220]})
  if a.once:break
  time.sleep(.25)
 os.close(fd)
if __name__=='__main__':main()
