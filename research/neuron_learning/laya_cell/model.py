"""Freeze the full pretrained Laya path; adapt only explicit scorer coordinates."""
from __future__ import annotations
import hashlib,os
from pathlib import Path
from typing import Mapping
import torch
from torch import nn
from torch.nn import functional as F
from .integrity import ContractError,CODE_BLOBS,LAYA_VERSION,canonical,digest,git_blob,read_json,verify_snapshot
PROFILE={'schema':'heptatrader.laya-cell-profile.v1','max_len':1024,'dtype':'float32','precision_policy':'EXPLICIT_FP32_RUNTIME_PROFILE','organ_adapter':'FROZEN_IDENTITY','targets':{'scorer.1':{'rows':[0,1,2,3],'rank':2},'scorer.3':{'rows':[0],'rank':1}},'action_head_trainable':False,'native_max_effective_deltas':4096,'per_layer_relative_limit':.005,'global_relative_limit':.0025}
_TENSOR_HASH_CHUNK_BYTES=16*1024*1024
_PRETRAINED_FP32_STAGING_BYTES=16*1024*1024
def require_pocket4_gpu()->torch.device:
 if not torch.version.hip or not torch.cuda.is_available():raise ContractError('POCKET4_ROCM_GPU_REQUIRED_NO_CPU_TRAINING_FALLBACK')
 if not str(getattr(torch.cuda.get_device_properties(0),'gcnArchName','')).startswith('gfx1150'):raise ContractError('POCKET4_GFX1150_REQUIRED')
 return torch.device('cuda:0')
def tensor_hash(tensor):
 t=tensor.detach()
 # Keep legacy normalization outside ordinary contiguous GPU parameters,
 # including empty/unusual-stride and unresolved conjugate/negative views.
 if (t.device.type!='cuda' or not t.is_contiguous() or t.is_conj() or t.is_neg()
     or not t.numel() or not t.dim() or t.stride(-1)!=1):t=t.cpu().contiguous()
 h=hashlib.sha256(canonical({'shape':list(t.shape),'dtype':str(t.dtype)}))
 data=t.view(torch.uint8).reshape(-1)
 for start in range(0,data.numel(),_TENSOR_HASH_CHUNK_BYTES):
  chunk=data.narrow(0,start,min(_TENSOR_HASH_CHUNK_BYTES,data.numel()-start)).cpu()
  h.update(memoryview(chunk.numpy()))
  # Release the host tensor and its temporary NumPy/buffer views before the
  # next synchronous GPU-to-CPU allocation, rather than keeping two chunks.
  del chunk
 return h.hexdigest()
class RowLoRALinear(nn.Module):
 def __init__(self,base:nn.Linear,rows:list[int],rank:int,seed:int):
  super().__init__()
  if not isinstance(base,nn.Linear) or base.weight.dtype!=torch.float32:raise ContractError('EXPECTED_FP32_PRETRAINED_LINEAR')
  if type(rank) is not int or not 1<=rank<=8 or not rows or rows!=sorted(set(rows)) or any(type(i) is not int or not 0<=i<base.out_features for i in rows):raise ContractError('LORA_MASK_INVALID')
  self.base=base.requires_grad_(False);self.rows=tuple(rows);self.rank=rank
  self.register_buffer('row_index',torch.tensor(rows,device=base.weight.device),persistent=False)
  gen=torch.Generator(device='cpu').manual_seed(seed)
  a=torch.randn(rank,base.in_features,generator=gen,dtype=torch.float32)*.01
  self.A=nn.Parameter(a.to(base.weight.device));self.B=nn.Parameter(torch.zeros(len(rows),rank,device=base.weight.device))
 def forward(self,x):return self.base(x).index_add(-1,self.row_index,F.linear(F.linear(x,self.A),self.B))
 def effective_rows(self):return self.base.weight[self.row_index]+self.B@self.A
 def full_effective_weight(self):return self.base.weight.index_add(0,self.row_index,self.B@self.A)
class TradingDecisionCell(nn.Module):
 def __init__(self,pretrained:nn.Module,base_id:str,profile:dict|None=None):
  super().__init__();self.profile=profile or PROFILE
  if self.profile!=PROFILE:raise ContractError('UNREVIEWED_ADAPTER_PROFILE')
  if not base_id or len(base_id)!=64:raise ContractError('BOUND_BASE_ID_REQUIRED')
  self.base_id=base_id;self.model=pretrained.requires_grad_(False).eval();self.adapters={}
  for i,(path,shape) in enumerate(self.profile['targets'].items()):
   parent_path,child=path.rsplit('.',1);parent=self.model.get_submodule(parent_path);original=self.model.get_submodule(path)
   adapter=RowLoRALinear(original,shape['rows'],shape['rank'],20261007+i);setattr(parent,child,adapter);self.adapters[path]=adapter
  if sum(len(a.rows)*a.base.in_features for a in self.adapters.values())>PROFILE['native_max_effective_deltas']:raise ContractError('NATIVE_PARAMETER_PROFILE_TOO_LARGE')
  self.parent_effective={n:a.full_effective_weight().detach().clone() for n,a in self.adapters.items()}
  self._frozen={n:tensor_hash(p) for n,p in self.model.named_parameters() if not p.requires_grad};self.train(False)
 def train(self,mode=True):
  super().train(mode);self.model.eval()
  for a in self.adapters.values():a.training=mode
  return self
 def forward(self,**upstream_batch):return self.model(**upstream_batch)
 def trainable_state(self):return {n:p.detach().clone() for n,p in self.named_parameters() if p.requires_grad}
 def restore_trainable(self,state:Mapping[str,torch.Tensor]):
  parameters={n:p for n,p in self.named_parameters() if p.requires_grad}
  if set(parameters)!=set(state):raise ContractError('ADAPTER_TENSOR_MASK_CHANGED')
  with torch.no_grad():
   for n,p in parameters.items():
    v=state[n]
    if v.shape!=p.shape or not torch.isfinite(v).all():raise ContractError('ADAPTER_TENSOR_SHAPE_OR_NUMBER')
    p.copy_(v)
 def assert_frozen(self):
  current={n:tensor_hash(p) for n,p in self.model.named_parameters() if not p.requires_grad}
  if current!=self._frozen:raise ContractError('PRETRAINED_BASE_CHANGED')
 def norm_report(self):
  num=den=0.;layers={}
  for name,a in self.adapters.items():
   reference=self.parent_effective[name].double();delta=a.full_effective_weight().double()-reference
   n,d=float(delta.square().sum().detach()),float(reference.square().sum())
   if not d>0:raise ContractError('ZERO_PRETRAINED_NORM')
   layers[name]={'delta_squared':n,'base_squared':d,'relative_l2':(n/d)**.5};num+=n;den+=d
  relative=(num/den)**.5
  return {'layers':layers,'global_relative_l2':relative,'within_profile':relative<=PROFILE['global_relative_limit'] and all(x['relative_l2']<=PROFILE['per_layer_relative_limit'] for x in layers.values()),'basis':'EFFECTIVE_PRETRAINED_WEIGHT_NOT_LORA_FACTOR_NORM'}
 def native_parameter_rows(self):
  layers=[]
  for name,a in self.adapters.items():
   reference=self.parent_effective[name];candidate=a.full_effective_weight().detach()
   qref=torch.round(reference.double()*2**32).to(torch.int64).cpu();qnew=torch.round(candidate.double()*2**32).to(torch.int64).cpu();delta=qnew-qref;indices=(delta!=0).nonzero()
   layers.append({'name':name,'shape':list(reference.shape),'baseline_squared_q64':str(sum(int(v)**2 for v in qref.flatten().tolist())),'deltas':[{'row':int(r),'col':int(c),'delta_q32':int(delta[r,c])} for r,c in indices.tolist()]})
  if sum(len(x['deltas']) for x in layers)>4096:raise ContractError('NATIVE_DELTA_COUNT_EXCEEDED')
  return layers

def _to_pretrained_gpu_fp32(model,device):
 # Reuse one ordinary CPU widening buffer for the pinned dense F16 parameters.
 # Full GPU tensors and checkpoint mappings remain; this is not an RSS bound.
 staging=None
 def convert(t):
  nonlocal staging
  if (isinstance(t,nn.Parameter) and t.device.type=='cpu' and t.dtype==torch.float16
      and t.layout==torch.strided and t.is_contiguous() and not t.is_conj() and not t.is_neg()):
   result=torch.empty_like(t,device=device,dtype=torch.float32)
   source=t.detach().view(-1);destination=result.view(-1)
   if source.numel() and staging is None:
    staging=torch.empty(_PRETRAINED_FP32_STAGING_BYTES//4,dtype=torch.float32,device='cpu')
   for start in range(0,source.numel(),_PRETRAINED_FP32_STAGING_BYTES//4):
    count=min(_PRETRAINED_FP32_STAGING_BYTES//4,source.numel()-start)
    chunk=staging.narrow(0,0,count)
    try:
     chunk.copy_(source.narrow(0,start,count))
     destination.narrow(0,start,count).copy_(chunk,non_blocking=False)
    finally:del chunk
   return result
  # Preserve the original conversion for buffers and unusual tensor layouts or
  # view flags; their conversion materials are outside the 16MiB bound.
  return t.to(device=device,dtype=torch.float32 if t.is_floating_point() or t.is_complex() else None,non_blocking=False)
 try:return model._apply(convert)
 finally:staging=None

def load_verified_laya(directory:Path,manifest:dict,*,enforce_gpu=True):
 base_id=verify_snapshot(directory,manifest);device=require_pocket4_gpu() if enforce_gpu else torch.device('cpu')
 os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',USE_TF='0',TOKENIZERS_PARALLELISM='false')
 import laya
 if laya.__version__!=LAYA_VERSION:raise ContractError('LAYA_PACKAGE_VERSION_MISMATCH')
 pkg=Path(laya.__file__).parent
 for name,expected in CODE_BLOBS.items():
  if git_blob((pkg/name).read_bytes())!=expected:raise ContractError('LAYA_CODE_BLOB_MISMATCH:'+name)
 from laya.common import build_model
 from transformers import AutoTokenizer
 from safetensors.torch import load_file
 cfg=read_json(directory/'rl_agent_config.json');encoder_cfg=read_json(directory/'encoder/config.json')
 if encoder_cfg.get('hidden_size')!=768:raise ContractError('LAYA_MULTILINGUAL_SHAPE_CHANGED')
 model=build_model(cfg,encoder_dir=str(directory/'encoder'),pretrained=False)
 checkpoint=load_file(str(directory/'model.safetensors'),device='cpu')
 if not any(k.startswith('encoder.') for k in checkpoint) or not any(k.startswith('scorer.') for k in checkpoint):raise ContractError('PRETRAINED_DECISION_HEAD_MISSING')
 gpu_load=device.type=='cuda'
 if gpu_load:
  # Assignment can split tied Parameter objects; this pinned backbone is untied.
  parameter_ids=[id(p) for _,p in model.named_parameters(remove_duplicate=False)]
  if len(parameter_ids)!=len(set(parameter_ids)):raise ContractError('UNSUPPORTED_TIED_PRETRAINED_PARAMETERS')
  model.load_state_dict(checkpoint,strict=True,assign=True)
 else:model.load_state_dict(checkpoint,strict=True)
 del checkpoint
 tokenizer=AutoTokenizer.from_pretrained(str(directory/'tokenizer'),local_files_only=True,trust_remote_code=False,use_fast=True)
 embedding_rows=model.encoder.get_input_embeddings().num_embeddings
 if len(tokenizer)>embedding_rows or max(tokenizer.get_vocab().values())>=embedding_rows:raise ContractError('TOKENIZER_EMBEDDING_MISMATCH')
 if gpu_load:
  # More blocking copies change timing, not FP32 arithmetic or the native backend.
  model=_to_pretrained_gpu_fp32(model,device).eval()
 else:model=model.float().to(device).eval()
 return model,tokenizer,base_id
