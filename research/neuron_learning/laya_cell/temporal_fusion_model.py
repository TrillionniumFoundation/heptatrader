"""Causal numeric temporal neuron + frozen-Laya gated fusion."""
from __future__ import annotations
import math
from typing import Iterable
import torch
from torch import nn
from torch.nn import functional as F
from .integrity import ContractError,digest

PROFILE={
 "schema":"heptatrader.temporal-laya-fusion-profile.v1","instrument":"EUR.USD","horizon_sec":300,
 "feature_version":"causal-fixed-scale-features16-v1","feature_width":16,"max_sequence":24,
 "embedding_dim":32,"classes":7,"laya_projection_seed":2026100802,"temporal_hidden":32,
 "temporal_layers":1,"dropout":0.0,"objective":"FUSION_CE_PLUS_0.25_NUMERIC_CE",
 "optimizer":"AdamW","learning_rate":0.001,"weight_decay":0.0001,"epochs":48,
 "batch_size":32,"gradient_norm_cap":1.0,"max_training_rows":192,"internal_holdout_rows":32,
 "automatic_trading":False,"actor_update":False,"financial_reward_used":False,
}
def profile_id():return digest(PROFILE)
def entropy(probs):
 if probs.ndim!=2 or probs.shape[-1]!=PROFILE["classes"]:raise ContractError("FUSION_PROBABILITY_SHAPE")
 p=probs.clamp_min(1e-8);return -(p*p.log()).sum(-1)/math.log(PROFILE["classes"])
def fixed_laya_projection(device):
 g=torch.Generator(device="cpu").manual_seed(PROFILE["laya_projection_seed"])
 p=torch.randn(PROFILE["classes"],PROFILE["embedding_dim"],generator=g,dtype=torch.float32)
 return F.normalize(p,dim=0).to(device)
def padded_sequences(samples:Iterable[dict],device=None):
 values=[];lengths=[]
 for sample in samples:
  rows=(sample.get("native_episode") or {}).get("rows")
  if not isinstance(rows,list) or not 2<=len(rows)<=PROFILE["max_sequence"]:raise ContractError("TEMPORAL_SEQUENCE_BOUND")
  if any(not isinstance(row,list) or len(row)!=PROFILE["feature_width"] for row in rows):raise ContractError("TEMPORAL_FEATURE_WIDTH")
  tensor=torch.tensor(rows,dtype=torch.float32)
  if not torch.isfinite(tensor).all() or tensor.abs().max()>8.00001:raise ContractError("TEMPORAL_FEATURE_NUMERIC")
  values.append(tensor);lengths.append(len(rows))
 width=max(lengths);batch=torch.zeros(len(values),width,PROFILE["feature_width"],dtype=torch.float32)
 for i,value in enumerate(values):batch[i,:len(value)]=value
 lengths=torch.tensor(lengths,dtype=torch.long)
 if device is not None:batch=batch.to(device);lengths=lengths.to(device)
 return batch,lengths

class NumericTemporalNeuron(nn.Module):
 def __init__(self):
  super().__init__();self.gru=nn.GRU(16,32,batch_first=True);self.norm=nn.LayerNorm(32);self.classifier=nn.Linear(32,7)
 def forward(self,sequence,lengths):
  packed=nn.utils.rnn.pack_padded_sequence(sequence,lengths.detach().cpu(),batch_first=True,enforce_sorted=False)
  _,hidden=self.gru(packed);emb=F.normalize(self.norm(hidden[-1]),p=2,dim=-1);return emb,self.classifier(emb)

class TemporalLayaFusion(nn.Module):
 def __init__(self):
  super().__init__();self.numeric=NumericTemporalNeuron()
  self.register_buffer("laya_projection",fixed_laya_projection(torch.device("cpu")))
  self.gate=nn.Sequential(nn.Linear(66,32),nn.Tanh(),nn.Linear(32,1))
 def forward(self,sequence,lengths,laya_probs):
  if laya_probs.ndim!=2 or laya_probs.shape[-1]!=7:raise ContractError("LAYA_PROBABILITY_SHAPE")
  if not torch.isfinite(laya_probs).all() or (laya_probs<0).any():raise ContractError("LAYA_PROBABILITY_NUMERIC")
  if not torch.allclose(laya_probs.sum(-1),torch.ones(len(laya_probs),device=laya_probs.device),rtol=1e-4,atol=1e-4):raise ContractError("LAYA_PROBABILITY_NORMALIZATION")
  numeric_emb,numeric_logits=self.numeric(sequence,lengths);numeric_probs=numeric_logits.softmax(-1)
  laya_emb=F.normalize(laya_probs@self.laya_projection,p=2,dim=-1);nu=entropy(numeric_probs);lu=entropy(laya_probs)
  gate=torch.sigmoid(self.gate(torch.cat([numeric_emb,laya_emb,nu[:,None],lu[:,None]],-1))).squeeze(-1)
  probs=gate[:,None]*numeric_probs+(1-gate[:,None])*laya_probs
  meta=F.normalize(gate[:,None]*numeric_emb+(1-gate[:,None])*laya_emb,p=2,dim=-1)
  return {"numeric_embedding":numeric_emb,"numeric_logits":numeric_logits,"numeric_probs":numeric_probs,
   "numeric_uncertainty":nu,"laya_embedding":laya_emb,"laya_uncertainty":lu,"gate_numeric_weight":gate,
   "fusion_embedding":meta,"fusion_probs":probs,"fusion_uncertainty":entropy(probs)}
def training_loss(output,targets):
 fusion=F.nll_loss(output["fusion_probs"].clamp_min(1e-8).log(),targets);numeric=F.cross_entropy(output["numeric_logits"],targets)
 return fusion+.25*numeric,{"fusion_ce":fusion,"numeric_ce":numeric}
def brier(probs,targets):
 return (probs-F.one_hot(targets,7).to(probs.dtype)).square().sum(-1)
def output_contract(output,index):
 row=lambda name:[float(x) for x in output[name][index].detach().cpu()]
 return {"numeric":{"embedding":row("numeric_embedding"),"probs":row("numeric_probs"),"uncertainty":float(output["numeric_uncertainty"][index]),"effect_authority":False},
  "laya":{"embedding":row("laya_embedding"),"uncertainty":float(output["laya_uncertainty"][index]),"effect_authority":False},
  "fusion":{"embedding":row("fusion_embedding"),"probs":row("fusion_probs"),"uncertainty":float(output["fusion_uncertainty"][index]),
            "gate_numeric_weight":float(output["gate_numeric_weight"][index]),"effect_authority":False}}
