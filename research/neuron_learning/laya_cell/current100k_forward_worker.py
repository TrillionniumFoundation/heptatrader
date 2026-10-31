"""Bounded signed Value observations; default one turn, explicit finite warmth.

A finite external timer invokes this worker. Inference holds the SAME heavy-worker
lock through tensor destruction. Missing/late requests remain missed. Evaluation
uses the original authoritative replay and independently signed role receipts.
"""
from __future__ import annotations
import argparse, fcntl, gc, json, os, sys, time, traceback
from pathlib import Path
from .integrity import ContractError, read_json, sha_file, digest
from .resident_store import atomic


def validate_runtime(c):
 if c.get('schema')!='heptatrader.current100k-forward-worker-config.v1':raise ContractError('VALUE_FORWARD_CONFIG_SCHEMA')
 if c.get('prediction_path_profile','VERIFIED_TURN_SNAPSHOT_V1')!='VERIFIED_TURN_SNAPSHOT_V1':raise ContractError('VALUE_FORWARD_PATH_PROFILE')
 if c.get('automatic_trading') is not False or c.get('actor_update') is not False:raise ContractError('VALUE_FORWARD_NO_ACTOR')
 if sha_file(Path(c['period_path']))!=c['period_sha256']:raise ContractError('VALUE_FORWARD_PERIOD_CHANGED')
 inventory=c.get('worker_runtime_sha256')
 if not inventory or digest(inventory)!=c['worker_runtime_inventory_digest']:raise ContractError('VALUE_FORWARD_RUNTIME_INVENTORY')
 root=Path(c['worker_source_root'])
 for name,want in inventory.items():
  p=Path(name)
  if p.is_absolute() or '..' in p.parts or sha_file(root/p)!=want:raise ContractError('VALUE_FORWARD_RUNTIME_CHANGED')
 if c.get('warm_flat_profile') is not None:
  from .current100k_warm_flat import check_source_pin
  check_source_pin(c)


def acquire_value_resources(gate,subject):
 previous=list(sys.path)
 try:
  return gate.acquire(subject)
 finally:
  sys.path[:]=previous


def run(c,mode):
 if mode=='predict' and c.get('warm_flat_profile') is not None:
  raise ContractError('VALUE_WARM_PARENT_ENTRY_REQUIRED')
 from .financial_forward_predict import PhaseClock,RegisteredPlanSnapshot
 clock=PhaseClock();started_ms=int(time.time()*1000)
 validate_runtime(c);clock.mark('worker_runtime_validation')
 from .financial_forward_evaluate import validate_registration
 from .financial_forward_witness import verify_trust
 from .current100k_forward import PLAN_SCHEMA
 from . import resident_hip_runtime as hip_runtime
 verify_trust(c);root=Path(c['forward_root']);plans=list((root/'plans').glob('*/plan.json'))
 if len(plans)!=1:raise ContractError('EXACTLY_ONE_CURRENT100K_FORWARD_PLAN')
 plan=read_json(plans[0]);clock.mark('trust_and_plan_read')
 hip_runtime.check_plan(c,plan)
 prepared=RegisteredPlanSnapshot(c,plans[0],plan) if mode=='predict' else None
 if prepared is None:validate_registration(c,plans[0],plan)
 clock.mark('registration_validation')
 if plan.get('schema')!=PLAN_SCHEMA:raise ContractError('CURRENT100K_PLAN_REQUIRED')
 if plan.get('worker_runtime_inventory_digest')!=c['worker_runtime_inventory_digest']:raise ContractError('PLAN_WORKER_RUNTIME_CHANGED')
 now=int(time.time()*1000);snapshot=None
 status_path=root/('predictor-status.json' if mode=='predict' else 'evaluator-status.json')
 front={'path_profile':'VERIFIED_TURN_SNAPSHOT_V1','worker_started_at_ms':started_ms,
        'plan_id':plan['plan_id'],'input_start_ms':plan['input_start_ms'],'input_end_ms':plan['input_end_ms'],
        'worker_phases_ms':clock.phases}
 def wait_status(state,**extra):
  timing={**front,'worker_phases_ms':dict(clock.phases),
   'ingress_full_scans':snapshot.full_scans if snapshot else 0,
   'ingress_snapshot_reuses':snapshot.reuses if snapshot else 0,
   'registration_full_validations':prepared.full_validations if prepared else 1,
   'registration_snapshot_reuses':prepared.reuses if prepared else 0}
  status={'at_ms':int(time.time()*1000),'state':state,'plan_id':plan['plan_id'],
   'input_start_ms':plan['input_start_ms'],'input_end_ms':plan['input_end_ms'],'front_timing':timing,
   'current_issues':[],'broker_orders_sent':0,'actor_updated':False,**extra}
  atomic(status_path,status);return status
 if now<plan['input_start_ms']:return wait_status('WAIT_FIXED_FUTURE_START')
 if mode=='predict' and now>=plan['input_end_ms']:return wait_status('FIXED_INPUT_WINDOW_CLOSED_NO_MODEL_LOAD')
 gate=None;worker=None;hip_context=None;torch=None
 gpu_import_attempted=False;gpu_runtime_ready=False
 try:
  if mode=='predict':
   from .financial_forward_source import VerifiedIngressSnapshot
   snapshot=VerifiedIngressSnapshot(plan['producer_registration'],c['period_sha256'])
   _,requests,_,issues=snapshot.read(plan['producer_registration'],c['period_sha256'])
   clock.mark('ingress_validation')
   if issues:raise ContractError('CURRENT100K_SOURCE_PROVENANCE_UNAVAILABLE')
   fresh=[];now=int(time.time()*1000)
   for rid,item in requests.items():
    if not isinstance(rid,str) or rid in ('','.','..') or Path(rid).name!=rid:raise ContractError('REQUEST_PATH_ESCAPE')
    request=item['request'];folder=root/'plans'/plan['plan_id']/'predictions'/rid
    if (plan['input_start_ms']<=request['started_at_ms']<=now<plan['input_end_ms'] and now-request['started_at_ms']<60000
        and request['position_at_request']==0 and not (folder/'commit.json').exists() and not (folder/'failure.json').exists()):fresh.append(rid)
   if not fresh:
    return wait_status('WAIT_FRESH_ORIGINAL_REQUEST',sealed_predictions=len(list((root/'plans'/plan['plan_id']/'predictions').glob('*/commit.json'))))
   from historical_training.resident_resource_guard import Gate,verify_config
   guard=read_json(Path(c['guard_config']));original=verify_config(guard)
   hip_runtime.check_guard_selection(c,original);gate=Gate(guard,'value-predictor')
   admitted=acquire_value_resources(gate,{'plan_id':plan['plan_id'],'requests':fresh})
   clock.mark('resource_admission')
   if not admitted:return wait_status('HOLD_SHARED_RESOURCE_OR_SOURCE')
   hip_context=hip_runtime.prepare_worker(c,'predict')
  from sandbox import enter
  enter({'output_dir':str(root),'period_path':c['period_path'],'legacy_capture':plan['training_dataset_path'],
   'oms_path':c['oms_path'],'export_dir':c['attribution_directory'],
   'read_only_paths':c['worker_read_only_paths']+[c['worker_source_root'],sys.prefix,c['role_key_path'],c['witness_trust_path']]
                     +(hip_context['readonly'] if hip_context is not None else [])})
  if mode=='predict':
   hip_runtime.activate(hip_context)
   gpu_import_attempted=True
   import torch
   hip_runtime.prove(hip_context,'AFTER_TORCH_BEFORE_MODEL')
   gpu_runtime_ready=True
   torch.set_num_threads(2)
   torch.cuda.set_per_process_memory_fraction(min(1.,4*1024**3/torch.cuda.get_device_properties(0).total_memory))
   from .financial_forward_predict import Predictor
   clock.mark('sandbox_and_torch_setup')
   worker=Predictor(c,ingress_snapshots={plan['plan_id']:snapshot},registered_plans={plan['plan_id']:prepared},front_timing=front)
  else:
   from .financial_forward_evaluate import Evaluator
   worker=Evaluator(c)
  return worker.step()
 except BaseException as exc:
  # A status/publication failure can keep step.self and tensors alive through
  # chained errors even after dropping this frame's worker reference.
  pending=[exc];seen=set()
  while pending:
   error=pending.pop()
   if id(error) in seen:continue
   seen.add(id(error));traceback.clear_frames(error.__traceback__)
   pending.extend(e for e in (error.__cause__,error.__context__) if e is not None)
  raise
 finally:
  if mode=='predict':
   worker=None
   if gpu_import_attempted:
    gc.collect()
    if gpu_runtime_ready and torch.cuda.is_initialized():
     torch.cuda.synchronize();torch.cuda.empty_cache()
  # Failed cleanup or an imported but unproven runtime retains the original
  # lease FD until this finite worker exits. Cleanup never initializes CUDA.
  if gate is not None and (not gpu_import_attempted or gpu_runtime_ready):gate.release()


def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--mode',choices=['predict','evaluate'],required=True);a=p.parse_args();os.umask(0o077)
 c=read_json(a.config);root=Path(c['forward_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
 fd=os.open(root/(a.mode+'-worker.lock'),os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:
  try:
   if a.mode=='predict' and c.get('warm_flat_profile') is not None:
    from .current100k_warm_flat import run_parent
    result=run_parent(c,a.config,fd)
   else:result=run(c,a.mode)
   print(json.dumps(result),flush=True)
  except Exception as exc:
   status={'at_ms':int(time.time()*1000),'state':'UNAVAILABLE_NO_SELECTION','reason':str(exc)[:220],'broker_orders_sent':0,'actor_updated':False}
   atomic(root/('predictor-status.json' if a.mode=='predict' else 'evaluator-status.json'),status)
   print(json.dumps(status),flush=True);raise
 finally:os.close(fd)
if __name__=='__main__':main()
