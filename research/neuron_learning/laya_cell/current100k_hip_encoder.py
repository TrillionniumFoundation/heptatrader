"""Pinned HIP FP32 encoder; admission and the original lease belong to the caller."""
from __future__ import annotations
import gc,hashlib,json,os,sys,time
from pathlib import Path
from hepta_decision_attribution import read_private,strict
from .integrity import ContractError
from weight_edit.current100k_study import pinned_module
PROFILE='MASKED_MEAN_L2_NORMALIZED_FP32_768'
SINGLE='LAYA_HIP_FP32_SINGLE_ROW_MAX2048_NO_TRUNCATION_V1'
BATCH2='LAYA_HIP_FP32_BATCH2_MAX2048_NO_TRUNCATION_V1'
MEMORY_LIMIT_BYTES=4*1024**3

def need(ok,reason):
 if not ok:raise ContractError(reason)

def hip_binding(config_path,config_sha256,runtime,base,period_sha256):
 """CPU-only source/runtime verification before the worker's exec boundary."""
 raw=read_private(config_path);need(hashlib.sha256(raw).hexdigest()==config_sha256,'PREOPEN_HIP_CONFIG_PIN')
 c=strict(raw)
 need(Path(c['runtime_directory'])==Path(runtime) and Path(c['worker_source_root'])==Path(runtime)
      and Path(c['base_directory'])==Path(base) and c['period_sha256']==period_sha256,'PREOPEN_HIP_ORIGINAL_SCOPE')
 directory=Path(runtime)/'learn/laya_cell'
 pinned_module(directory,'current100k_forward_worker').validate_runtime(c)
 hip=pinned_module(directory,'resident_hip_runtime')
 need(c.get('hip_runtime')is not None,'PREOPEN_ACCEPTED_HIP_REQUIRED')
 environment=hip.worker_environment(c)
 need(environment is not None,'PREOPEN_BOUND_HIP_ENVIRONMENT_REQUIRED')
 return c,hip,environment

def environment_is_bound(config,hip,environment):
 return (sys.executable==config['python'] and os.environ.get(hip.ENV_PIN)==environment[hip.ENV_PIN]
         and os.environ.get('LD_LIBRARY_PATH','').split(os.pathsep)[0]==environment['LD_LIBRARY_PATH'].split(os.pathsep)[0])

class WarmHipEncoder:
 """One immutable FP32 model; load only within the caller's original guard lease."""
 def __init__(self,runtime,base,hip_config,*,guard_original,threads=2,batch_size=1):
  need(threads in (1,2,4) and batch_size in (1,2),'PREOPEN_HIP_RESOURCE_PROFILE')
  self.raw=self.tokenizer=self.torch=None;self.context=None;self.proven=False;self.closed=False
  self.runtime=Path(runtime);self.base=Path(base);self.config=hip_config
  self.threads=threads;self.batch_size=batch_size;self.backend=SINGLE if batch_size==1 else BATCH2
  self.hip=pinned_module(self.runtime/'learn/laya_cell','resident_hip_runtime')
  self.hip.check_guard_selection(hip_config,guard_original)
  need(hip_config.get('hip_runtime')is not None,'PREOPEN_ACCEPTED_HIP_REQUIRED')
  need(sys.executable==hip_config['python'],'PREOPEN_HIP_PYTHON_BINDING')
  self.context=self.hip.prepare_worker(hip_config,'predict')
  need(self.context is not None,'PREOPEN_HIP_CONTEXT_REQUIRED')
  self.hip.activate(self.context)
  import torch
  self.torch=torch
  self.hip.prove(self.context,'AFTER_TORCH_BEFORE_MODEL');self.proven=True
  torch.set_num_threads(threads);torch.set_num_interop_threads(1)
  torch.set_float32_matmul_precision('highest')
  torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
  need(torch.version.hip is not None and torch.cuda.is_available(),'PREOPEN_HIP_REQUIRED')
  total=torch.cuda.get_device_properties(0).total_memory
  need(total>=MEMORY_LIMIT_BYTES,'PREOPEN_HIP_MEMORY_SCOPE')
  torch.cuda.set_per_process_memory_fraction(MEMORY_LIMIT_BYTES/total)
  self.allocation_before=torch.cuda.memory_allocated()
  torch.cuda.reset_peak_memory_stats()
  model=pinned_module(self.runtime/'learn/laya_cell','model')
  self.feature_text=pinned_module(self.runtime/'research/paired_net_100k_preflight','core').feature_text
  manifest=json.loads((self.base/'base-manifest.json').read_text())
  self.raw,self.tokenizer,self.base_id=model.load_verified_laya(self.base,manifest,enforce_gpu=True)
  self.raw.requires_grad_(False).eval()
  need(all(p.device.type=='cuda' and p.dtype==torch.float32 and not p.requires_grad for p in self.raw.parameters()),'PREOPEN_FROZEN_HIP_FP32_REQUIRED')
  torch.cuda.synchronize()
  need(torch.cuda.max_memory_allocated()<=MEMORY_LIMIT_BYTES,'PREOPEN_HIP_MEMORY_LIMIT')
 def _encode(self,texts):
  need(not self.closed and self.raw is not None,'PREOPEN_HIP_ENCODER_CLOSED')
  torch=self.torch;started=time.monotonic()
  batch=self.tokenizer(texts,padding=True,truncation=False,return_tensors='pt')
  need(batch['input_ids'].shape[1]<=2048,'PREOPEN_CONTEXT_EXCEEDS_2048_NO_TRUNCATION')
  counts=batch['attention_mask'].sum(1).tolist()
  batch={k:v.to('cuda') for k,v in batch.items() if k in ('input_ids','attention_mask','token_type_ids')}
  with torch.inference_mode():
   hidden=self.raw.encoder(**batch).last_hidden_state
   mask=batch['attention_mask'].unsqueeze(-1).to(hidden.dtype)
   mean=(hidden*mask).sum(1)/mask.sum(1).clamp_min(1)
   x=torch.nn.functional.normalize(mean.float(),p=2,dim=1).detach().cpu()
  torch.cuda.synchronize()
  need(x.shape==(len(texts),768) and bool(torch.isfinite(x).all()),'PREOPEN_HIP_FEATURE_SHAPE')
  need(torch.cuda.max_memory_allocated()<=MEMORY_LIMIT_BYTES,'PREOPEN_HIP_MEMORY_LIMIT')
  return x.tolist(),{'token_counts':counts,'seconds':time.monotonic()-started,
   'dtype':'float32','device':'cuda','feature_bytes_sha256':hashlib.sha256(x.numpy().tobytes()).hexdigest()}
 def encode_request(self,request):
  texts={s:self.feature_text(request,s) for s in ('BUY','SELL')};started=time.monotonic()
  if self.batch_size==2:
   values,receipt=self._encode([texts[s] for s in ('BUY','SELL')])
   features=dict(zip(('BUY','SELL'),values));receipts={'paired':receipt}
  else:
   features={};receipts={}
   for side in ('BUY','SELL'):
    values,receipt=self._encode([texts[side]]);features[side]=values[0];receipts[side]=receipt
  proof=self.hip.prove(self.context,'BEFORE_PUBLISH')
  return {'features':features,'feature_text_sha256':{s:hashlib.sha256(t.encode()).hexdigest() for s,t in texts.items()},
   'feature_profile':PROFILE,'backend':self.backend,'base_id':self.base_id,'encoder_threads':self.threads,
   'encoder_batch_size':self.batch_size,'receipts':receipts,'request_encode_seconds':time.monotonic()-started,
   'hip_runtime_manifest_sha256':self.config['hip_runtime']['sha256'],'hip_runtime_proof':proof,
   'peak_allocated_bytes':self.torch.cuda.max_memory_allocated(),'computed_at_ms':time.time_ns()//1000000}
 def close(self):
  if self.closed:return self.cleanup_receipt
  need(self.proven and self.torch is not None,'PREOPEN_UNPROVEN_HIP_REQUIRES_PROCESS_EXIT')
  cuda=self.torch.cuda
  self.cleanup_receipt={'device':'hip','allocation_before_model':self.allocation_before,
   'before_model_drop':cuda.memory_allocated(),'peak_allocated_bytes':cuda.max_memory_allocated()}
  self.raw=None;self.tokenizer=None;gc.collect()
  self.cleanup_receipt['after_model_gc']=cuda.memory_allocated()
  cuda.synchronize();self.cleanup_receipt['after_synchronize']=cuda.memory_allocated()
  # BLAS owns an allocator-visible per-handle workspace beyond model lifetime.
  # Clear only this process's unused workspaces after all its kernels complete.
  clear=getattr(self.torch._C,'_cuda_clearCublasWorkspaces',None)
  self.cleanup_receipt['workspace_clear_available']=callable(clear)
  if callable(clear):clear()
  self.cleanup_receipt['after_workspace_clear']=cuda.memory_allocated()
  cuda.empty_cache();cuda.synchronize()
  self.cleanup_receipt['after_empty_cache']=cuda.memory_allocated()
  need(self.cleanup_receipt['after_empty_cache']<=self.allocation_before,
       'PREOPEN_GPU_TENSORS_STILL_LIVE:'+str(self.cleanup_receipt['after_empty_cache']))
  self.closed=True;self.cleanup_receipt['all_model_allocations_released']=True
  return self.cleanup_receipt
