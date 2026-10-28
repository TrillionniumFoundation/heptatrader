"""Enter inherited unprivileged confinement before importing torch or the worker.

Only the scheduler checks the old service; children cannot contact its bus or any
trading endpoint. Landlock confines files and seccomp denies new network sockets.
"""
from __future__ import annotations
import argparse,faulthandler,json,os
from pathlib import Path
from .integrity import read_json,sha_file,ContractError
from .resident import config_check,bootstrap_still_off
from .resident_store import atomic


def owner_task(path,expected,config_path,config,descriptors):
 """A real selected-model task uses its own input/output tree, never a slot."""
 import ctypes,resource,signal
 from .integrity import digest
 from .resident_owner_consume import TASK_SCHEMA,_read_pinned,_read_bytes
 if path is None or not isinstance(expected,str) or len(expected)!=64 or len(descriptors)!=2:
  raise ContractError('OWNER_PREDICTION_TASK_REQUIRED')
 path=Path(path)
 task=_read_pinned(path,expected,65536);request=task.get('request')
 outbox=(Path(config['control_directory'])/'owner-outbox').resolve()
 folder=path.parent.parent;output=Path(task['output_directory']);payload=Path(task['payload_path'])
 if (task.get('schema')!=TASK_SCHEMA or not isinstance(request,dict)
     or task.get('request_digest')!=digest(request) or folder.name!=digest(request)
     or path.resolve()!=path or path.name!='task.json' or path.parent.name!='input'
     or folder.parent.name!='advisory' or not folder.is_relative_to(outbox)
     or output!=folder/'worker' or output.resolve()!=output
     or payload!=path.parent/'selected-model.json' or payload.is_symlink()
     or _read_pinned(config_path,task.get('config_sha256'))!=config
     or task.get('effect_authority') is not False or task.get('broker_orders_sent')!=0
     or type(task.get('controller_pid')) is not int or task['controller_pid']!=os.getppid()):
  raise ContractError('OWNER_PREDICTION_TASK_BINDING')
 # The parent owns the bounded cgroup and the shared flock. A disappearing
 # controller must kill the worker even before Python imports the GPU stack.
 libc=ctypes.CDLL(None,use_errno=True)
 if libc.prctl(1,signal.SIGKILL,0,0,0)!=0 or os.getppid()!=task['controller_pid']:
  raise ContractError('OWNER_PREDICTION_PARENT_DEATH_GUARD')
 resource.setrlimit(resource.RLIMIT_CORE,(0,0))
 import hashlib
 raw=_read_bytes(payload)
 if hashlib.sha256(raw).hexdigest()!=task.get('payload_sha256'):
  raise ContractError('OWNER_PREDICTION_SELECTED_BYTES_CHANGED')
 from .resident_owner import validate_payload
 selected,_=validate_payload(raw)
 if selected['plan']['plan_id']!=folder.parent.parent.name:
  raise ContractError('OWNER_PREDICTION_SELECTED_PLAN_BINDING')
 return task,output,payload,raw


def retain_worker_locks(descriptors):
 """Hold inherited flocks here, but never leak them into native exec helpers."""
 if len(descriptors)>2 or len(set(descriptors))!=len(descriptors):
  raise ContractError('WORKER_LOCK_DESCRIPTOR_SET')
 for fd in descriptors:
  if type(fd) is not int or fd<3:raise ContractError('WORKER_LOCK_DESCRIPTOR')
  os.set_inheritable(fd,False)


def _main(failure_state):
 p=argparse.ArgumentParser();p.add_argument('target',choices=['train','predict','owner-predict']);p.add_argument('--config',type=Path,required=True)
 p.add_argument('--plan',type=Path);p.add_argument('--slot',type=int)
 p.add_argument('--owner-task',type=Path);p.add_argument('--owner-task-sha256')
 p.add_argument('--held-lock-fd',type=int,action='append',default=[])
 a=p.parse_args();failure_state.update(target=a.target,phase='VALIDATE_CONFIG')
 os.umask(0o077);retain_worker_locks(a.held_lock_fd)
 # Capture the Python call stack even when HIP fails before a JSON result.
 faulthandler.enable(all_threads=True)
 c=read_json(a.config);base=dict(c);base['schema']='heptatrader.laya-resident-config.v1';config_check(base);bootstrap_still_off()
 if c.get('require_worker_sandbox') is not True:raise ContractError('SANDBOX_REQUIRED')
 from . import resident_hip_runtime as hip_runtime
 failure_state['phase']='PREPARE_HIP_RUNTIME'
 hip_context=hip_runtime.prepare_worker(c,a.target)
 failure_state['hip_context']=hip_context
 if a.target=='train':
  output=Path(c['output_directory']).parent
 elif a.target=='owner-predict':
  if a.plan is not None or a.slot is not None:raise ContractError('OWNER_PREDICTION_IS_NOT_A_TRIAL')
  task,output,payload,selected_raw=owner_task(a.owner_task,a.owner_task_sha256,a.config,c,a.held_lock_fd)
 else:
  if a.plan is None or a.slot is None:raise ContractError('PREDICT_SLOT_REQUIRED')
  output=a.plan.parent
 output.mkdir(mode=0o700,parents=True,exist_ok=True)
 readonly=[c['base_directory'],str(Path(c['python']).parent.parent),c['baseline_directory'],c['feed_directory'],
  c['initial_parent_directory'],c['native_binary'],c['seed_dataset'],c['runtime_directory'],str(a.config),
  str(Path.home()/'.local/state/heptatrader/paper-driver/cost-observations.jsonl')]
 readonly.extend(c.get('completed_history_sources',[]))
 if hip_context is not None:readonly.extend(hip_context['readonly'])
 if a.target=='train':
  readonly.extend([c['dataset'],c['parent_directory']])
  from .resident_parent_action_cache import readonly_input_path as parent_action_cache_input_path
  cache_input=parent_action_cache_input_path(c)
  if cache_input is not None:readonly.append(str(cache_input))
 elif a.target=='owner-predict':readonly.extend([str(a.owner_task),str(payload)])
 else:
  plan=read_json(a.plan);readonly.extend([str(a.plan),plan['candidate_directory'],plan['parent_directory']])
 from . import resident_training_v5 as training_v5
 from . import resident_training_v6 as training_v6
 from . import resident_training_v7 as training_v7
 from . import resident_training_v8 as training_v8
 if a.target=='train' and (training_v5.enabled(c.get('objective')) or training_v6.enabled(c.get('objective')) or training_v7.enabled(c.get('objective')) or training_v8.enabled(c.get('objective'))):
  # Original closed evidence is read-only. Broker sockets/network and every
  # write path retain the same confinement as previous worker versions.
  readonly.extend([c['closed_action_source'],c['evaluation_directory']])
 sandbox_config={'output_dir':str(output),'period_path':c['period_path'],'legacy_capture':str(Path.home()/'.local/state/heptatrader/paper-driver/cost-observations.jsonl'),
  'oms_path':c['oms_path'],'export_dir':c['export_directory'],'read_only_paths':readonly}
 from sandbox import enter
 failure_state['phase']='ENTER_SANDBOX'
 receipt=enter(sandbox_config);receipt['target']=a.target
 receipt_name=('sandbox-train.json' if a.target=='train' else 'sandbox-owner-predict.json'
               if a.target=='owner-predict' else f'sandbox-predict-{a.slot:03}.json')
 atomic(output/receipt_name,receipt)
 failure_state['phase']='IMPORT_TORCH'
 hip_runtime.activate(hip_context)
 import torch
 hip_runtime.prove(hip_context,'AFTER_TORCH_BEFORE_MODEL')
 failure_state['after_torch_proved']=True
 torch.set_num_threads(2)
 failure_state['phase']='RUN_'+a.target.upper().replace('-','_')
 if a.target=='train':
  from .resident_train import run_job
  print(json.dumps(run_job(c,old_learner_verified=True),indent=2))
 elif a.target=='owner-predict':
  from .resident_owner_predict import predict_payload
  from .resident_owner_consume import RESULT_SCHEMA
  from .resident_store import write_once
  result=predict_payload(c,selected_raw,task['request'])
  if sha_file(a.owner_task)!=a.owner_task_sha256 or sha_file(payload)!=task['payload_sha256']:
   raise ContractError('OWNER_PREDICTION_INPUT_CHANGED_AFTER_MODEL')
  hip_proof=hip_runtime.prove_before_publish(c)
  receipt={'schema':RESULT_SCHEMA,'task_sha256':a.owner_task_sha256,
   'request_digest':task['request_digest'],'payload_sha256':task['payload_sha256'],'output':result}
  if hip_proof is not None:receipt['hip_runtime_proof']=hip_proof
  write_once(output/'result.json',receipt)
 else:
  from .resident_predict import predict
  predict(c,a.plan,a.slot)


def main():
 failure_state={}
 try:
  return _main(failure_state)
 except Exception as error:
  # Only a failed worker collects CPU observations. A diagnostic import or I/O
  # failure must never replace the original exception or trigger a retry.
  try:
   from .resident_worker_failure_context import emit
   emit(failure_state,error)
  except BaseException:
   pass
  raise


if __name__=='__main__':main()
