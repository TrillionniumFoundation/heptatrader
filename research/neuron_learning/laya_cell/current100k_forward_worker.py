"""One bounded signed Value observation turn; no trading API or warm GPU daemon.

A finite external timer invokes this worker. Inference holds the SAME heavy-worker
lock through tensor destruction. Missing/late requests remain missed. Evaluation
uses the original authoritative replay and independently signed role receipts.
"""
from __future__ import annotations
import argparse, fcntl, gc, json, os, sys, time, traceback
from pathlib import Path
from .integrity import ContractError, read_json, sha_file, digest
from .resident_store import atomic, write_once


def request_record(c,root,plan,rid,item,now,state,reason,final=False):
 """Keep actual failed attempts; only expiration permanently misses a request."""
 folder=root/'plans'/plan['plan_id']/'predictions'/rid
 record={'schema':'heptatrader.current100k-forward-request-attempt.v1','plan_id':plan['plan_id'],
  'candidate_id':plan['candidate_id'],'request_id':rid,'request_sha256':item['request_sha256'],
  'request_started_at_ms':item['request']['started_at_ms'],'request_source_prefix':item['source_prefix'],
  'observed_at_ms':now,'state':state,'reason':reason,'worker_runtime_inventory_digest':c['worker_runtime_inventory_digest'],
  'prediction_backdated':False,'broker_orders_sent':0}
 if final:
  record['schema']='heptatrader.current100k-forward-request-failure.v1'
  destination=folder/'failure.json'
 else:
  record['attempt_id']=digest(record);destination=folder/'attempts'/(record['attempt_id']+'.json')
 if not destination.exists():write_once(destination,record)


def prediction_evidence(root):
 failures=list(root.glob('plans/*/predictions/*/failure.json'))
 attempts=list(root.glob('plans/*/predictions/*/attempts/*.json'))
 records=[read_json(p) for p in failures+attempts]
 last=max(records,key=lambda x:x['observed_at_ms'],default=None)
 return {'sealed_predictions':len(list(root.glob('plans/*/predictions/*/commit.json'))),
  'failed_or_missed_requests':len(failures),'retained_failed_or_held_attempts':len(attempts),
  'last_retained_request_issue':None if last is None else {k:last[k] for k in ('request_id','observed_at_ms','reason')}}


def clear_exception_frames(exc):
 # An inference traceback retains its frame's self/tensors after del worker.
 # Clear completed frames before releasing the shared GPU lease, including causes.
 pending=[exc];seen=set()
 while pending:
  current=pending.pop()
  if id(current) in seen:continue
  seen.add(id(current));traceback.clear_frames(current.__traceback__)
  pending.extend(x for x in (current.__cause__,current.__context__) if x is not None)


def validate_runtime(c):
 if c.get('schema')!='heptatrader.current100k-forward-worker-config.v1':raise ContractError('VALUE_FORWARD_CONFIG_SCHEMA')
 if c.get('automatic_trading') is not False or c.get('actor_update') is not False:raise ContractError('VALUE_FORWARD_NO_ACTOR')
 if sha_file(Path(c['period_path']))!=c['period_sha256']:raise ContractError('VALUE_FORWARD_PERIOD_CHANGED')
 inventory=c.get('worker_runtime_sha256')
 if not inventory or digest(inventory)!=c['worker_runtime_inventory_digest']:raise ContractError('VALUE_FORWARD_RUNTIME_INVENTORY')
 root=Path(c['worker_source_root'])
 for name,want in inventory.items():
  p=Path(name)
  if p.is_absolute() or '..' in p.parts or sha_file(root/p)!=want:raise ContractError('VALUE_FORWARD_RUNTIME_CHANGED')


def run(c,mode):
 validate_runtime(c)
 from .financial_forward_evaluate import validate_registration
 from .financial_forward_witness import verify_trust
 from .current100k_forward import PLAN_SCHEMA
 verify_trust(c);root=Path(c['forward_root']);plans=list((root/'plans').glob('*/plan.json'))
 if len(plans)!=1:raise ContractError('EXACTLY_ONE_CURRENT100K_FORWARD_PLAN')
 plan=read_json(plans[0]);validate_registration(c,plans[0],plan)
 if plan.get('schema')!=PLAN_SCHEMA:raise ContractError('CURRENT100K_PLAN_REQUIRED')
 if plan.get('worker_runtime_inventory_digest')!=c['worker_runtime_inventory_digest']:raise ContractError('PLAN_WORKER_RUNTIME_CHANGED')
 now=int(time.time()*1000)
 status_path=root/('predictor-status.json' if mode=='predict' else 'evaluator-status.json')
 if now<plan['input_start_ms']:
  status={'at_ms':now,'state':'WAIT_FIXED_FUTURE_START','input_start_ms':plan['input_start_ms'],'broker_orders_sent':0}
  atomic(status_path,status);return status
 gate=None;worker=None;fresh=[];requests={}
 try:
  if mode=='predict':
   from .financial_forward_source import ingress
   _,requests,_,issues=ingress(plan['producer_registration'],c['period_sha256'])
   if issues:raise ContractError('CURRENT100K_SOURCE_PROVENANCE_UNAVAILABLE')
   for rid,item in requests.items():
    if Path(rid).name!=rid:raise ContractError('REQUEST_PATH_ESCAPE')
    request=item['request'];folder=root/'plans'/plan['plan_id']/'predictions'/rid
    if (plan['input_start_ms']<=request['started_at_ms']<plan['input_end_ms'] and request['started_at_ms']<=now
        and request['position_at_request']==0 and not (folder/'commit.json').exists() and not (folder/'failure.json').exists()):
     if now-request['started_at_ms']>60000 or now>=plan['input_end_ms']:
      request_record(c,root,plan,rid,item,now,'MISSED_ORIGINAL_REQUEST','REQUEST_ALREADY_OLD_NO_BACKDATING',final=True)
     else:fresh.append(rid)
   if now>=plan['input_end_ms']:
    status={'at_ms':now,'state':'FIXED_INPUT_WINDOW_CLOSED_NO_MODEL_LOAD',**prediction_evidence(root),'broker_orders_sent':0}
    atomic(status_path,status);return status
   if not fresh:
    status={'at_ms':now,'state':'WAIT_FRESH_ORIGINAL_REQUEST',**prediction_evidence(root),'broker_orders_sent':0}
    atomic(status_path,status);return status
   from historical_training.resident_resource_guard import Gate,verify_config
   guard=read_json(Path(c['guard_config']));verify_config(guard);gate=Gate(guard,'value-predictor')
   if not gate.acquire({'plan_id':plan['plan_id'],'requests':fresh}):
    for rid in fresh:request_record(c,root,plan,rid,requests[rid],int(time.time()*1000),'HOLD_SHARED_RESOURCE_OR_SOURCE','HOLD_SHARED_RESOURCE_OR_SOURCE')
    status={'at_ms':now,'state':'HOLD_SHARED_RESOURCE_OR_SOURCE',**prediction_evidence(root),'broker_orders_sent':0}
    atomic(status_path,status);return status
  from sandbox import enter
  enter({'output_dir':str(root),'period_path':c['period_path'],'legacy_capture':plan['training_dataset_path'],
   'oms_path':c['oms_path'],'export_dir':c['attribution_directory'],
   'read_only_paths':c['worker_read_only_paths']+[c['worker_source_root'],sys.prefix,c['role_key_path'],c['witness_trust_path']]})
  if mode=='predict':
   import torch
   torch.set_num_threads(2)
   torch.cuda.set_per_process_memory_fraction(min(1.,4*1024**3/torch.cuda.get_device_properties(0).total_memory))
   from .financial_forward_predict import Predictor
   worker=Predictor(c)
  else:
   from .financial_forward_evaluate import Evaluator
   worker=Evaluator(c)
  result=worker.step()
  if mode=='predict':
   result={**result,**prediction_evidence(root)};atomic(status_path,result)
  return result
 except BaseException as exc:
  try:
   if mode=='predict':
    for rid in fresh:request_record(c,root,plan,rid,requests[rid],int(time.time()*1000),'UNAVAILABLE_NO_SELECTION',str(exc)[:220] or type(exc).__name__)
  finally:clear_exception_frames(exc)
  raise
 finally:
  try:
   if mode=='predict' and worker is not None:
    del worker;gc.collect()
    if 'torch' in sys.modules:
     import torch
     try:torch.cuda.synchronize()
     finally:torch.cuda.empty_cache()
  finally:
   if gate is not None:gate.release()



def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--mode',choices=['predict','evaluate'],required=True);a=p.parse_args();os.umask(0o077)
 c=read_json(a.config);root=Path(c['forward_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
 fd=os.open(root/(a.mode+'-worker.lock'),os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:
  try:print(json.dumps(run(c,a.mode)),flush=True)
  except Exception as exc:
   status={'at_ms':int(time.time()*1000),'state':'UNAVAILABLE_NO_SELECTION','reason':str(exc)[:220],'broker_orders_sent':0,'actor_updated':False}
   if a.mode=='predict':status.update(prediction_evidence(root))
   atomic(root/('predictor-status.json' if a.mode=='predict' else 'evaluator-status.json'),status)
   print(json.dumps(status),flush=True);raise
 finally:os.close(fd)
if __name__=='__main__':main()
