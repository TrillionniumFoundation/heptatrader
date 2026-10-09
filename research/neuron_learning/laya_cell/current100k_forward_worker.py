"""One bounded signed Value observation turn; no trading API or warm GPU daemon.

A finite external timer invokes this worker. Inference holds the SAME heavy-worker
lock through tensor destruction. Missing/late requests remain missed. Evaluation
uses the original authoritative replay and independently signed role receipts.
"""
from __future__ import annotations
import argparse, fcntl, gc, json, os, sys, time
from pathlib import Path
from .integrity import ContractError, read_json, sha_file, digest
from .resident_store import atomic


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
 if mode=='predict' and now>=plan['input_end_ms']:
  status={'at_ms':now,'state':'FIXED_INPUT_WINDOW_CLOSED_NO_MODEL_LOAD','broker_orders_sent':0}
  atomic(status_path,status);return status
 gate=None;worker=None
 try:
  if mode=='predict':
   from .financial_forward_source import ingress
   _,requests,_,issues=ingress(plan['producer_registration'],c['period_sha256'])
   if issues:raise ContractError('CURRENT100K_SOURCE_PROVENANCE_UNAVAILABLE')
   fresh=[]
   for rid,item in requests.items():
    if Path(rid).name!=rid:raise ContractError('REQUEST_PATH_ESCAPE')
    request=item['request'];folder=root/'plans'/plan['plan_id']/'predictions'/rid
    if (plan['input_start_ms']<=request['started_at_ms']<=now and now-request['started_at_ms']<=60000
        and request['position_at_request']==0 and not (folder/'commit.json').exists() and not (folder/'failure.json').exists()):fresh.append(rid)
   if not fresh:
    status={'at_ms':now,'state':'WAIT_FRESH_ORIGINAL_REQUEST','sealed_predictions':len(list((root/'plans'/plan['plan_id']/'predictions').glob('*/commit.json'))),'broker_orders_sent':0}
    atomic(status_path,status);return status
   from historical_training.resident_resource_guard import Gate,verify_config
   guard=read_json(Path(c['guard_config']));verify_config(guard);gate=Gate(guard,'value-predictor')
   if not gate.acquire({'plan_id':plan['plan_id'],'requests':fresh}):
    status={'at_ms':now,'state':'HOLD_SHARED_RESOURCE_OR_SOURCE','broker_orders_sent':0}
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
  return worker.step()
 finally:
  try:
   if mode=='predict' and worker is not None:
    del worker;gc.collect()
    if 'torch' in sys.modules:
     import torch
     torch.cuda.synchronize();torch.cuda.empty_cache()
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
   atomic(root/('predictor-status.json' if a.mode=='predict' else 'evaluator-status.json'),status)
   print(json.dumps(status),flush=True);raise
 finally:os.close(fd)
if __name__=='__main__':main()
