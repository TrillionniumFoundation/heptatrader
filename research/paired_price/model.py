"""Five-arm independent price-complementarity experiment. No trading interface."""
from __future__ import annotations
import torch
from torch import nn
from torch.nn import functional as F
from laya_cell.temporal_fusion_model import NumericTemporalNeuron
from laya_cell.integrity import ContractError,digest
from .common import PROFILE,PROJECTION_SEED

def projection(device):
 g=torch.Generator(device='cpu').manual_seed(PROJECTION_SEED)
 return F.normalize(torch.randn(768,32,generator=g,dtype=torch.float32),dim=0).to(device)

class PairModel(nn.Module):
 def __init__(self):
  super().__init__()
  self.numeric=NumericTemporalNeuron()
  self.semantic=nn.Linear(768,7)
  self.gate=nn.Sequential(nn.Linear(66,32),nn.Tanh(),nn.Linear(32,1))
  self.register_buffer('projection',projection(torch.device('cpu')))
 def separate(self,x,lengths,semantic_features):
  if semantic_features.ndim!=2 or semantic_features.shape[1]!=768:raise ContractError('FULL_FROZEN_ENCODER_REQUIRED')
  numeric_embedding,numeric_logits=self.numeric(x,lengths)
  numeric=numeric_logits.softmax(-1)
  semantic_logits=self.semantic(semantic_features)
  semantic=semantic_logits.softmax(-1)
  return numeric_embedding,numeric_logits,numeric,semantic_logits,semantic
 def predict(self,x,lengths,features,legacy):
  emb,_,numeric,_,semantic=self.separate(x,lengths,features)
  laya_embed=F.normalize(features@self.projection,p=2,dim=-1)
  def entropy(p):return -(p*p.clamp_min(1e-8).log()).sum(-1)/torch.log(torch.tensor(7.,device=p.device))
  gate_logits=self.gate(torch.cat([emb,laya_embed,entropy(numeric)[:,None],entropy(semantic)[:,None]],-1))
  weight=torch.sigmoid(gate_logits)
  mixed=weight*numeric+(1-weight)*semantic
  fixed=.5*numeric+.5*semantic
  if legacy.ndim!=2 or legacy.shape!=numeric.shape:raise ContractError('LEGACY_INDEPENDENT_BASELINE_SHAPE')
  return {'numeric':numeric,'semantic':semantic,'fixed_mix':fixed,'gate':mixed,'legacy_numeric':legacy,'numeric_weight':weight}
 def freeze_experts(self):
  self.numeric.requires_grad_(False).eval()
  self.semantic.requires_grad_(False).eval()
 def assert_parameters(self):
  if not all(torch.isfinite(p).all() for p in self.parameters()):raise ContractError('NONFINITE_TRAINED_COMPONENT')
