"""Soft routing first; optional sparse top-k and dynamic graph attention.

Outputs are research predictions only. Routing runs AFTER experts, so it does not
claim upstream encoder savings. Descriptor nodes never invent price forecasts.
No native authority, actor, broker or automatic-selection API is present.
"""
from __future__ import annotations
import math
import torch
from torch import nn
from torch.nn import functional as F
from .contracts import align,ContractError,DIM,KINDS,NeuronOutput,Uncertainty,SCHEMA,digest,entropy

class GraphMetaNeuron(nn.Module):
 def __init__(self,mode='soft',top_k=4,message_rounds=2,latent_dim=32):
  super().__init__()
  if mode not in ('soft','top_k'):raise ContractError('ROUTER_MODE')
  if type(top_k)is not int or not 1<=top_k<=4:raise ContractError('TOP_K')
  if type(message_rounds)is not int or not 0<=message_rounds<=3:raise ContractError('MESSAGE_ROUNDS')
  if latent_dim!=32:raise ContractError('FIXED_INTERNAL_LATENT_DIM')
  self.mode=mode;self.top_k=top_k;self.message_rounds=message_rounds
  self.input=nn.Linear(DIM,latent_dim,bias=False)
  self.kinds=nn.Embedding(len(KINDS),latent_dim)
  self.router=nn.Linear(latent_dim+2,1)
  self.query=nn.Linear(latent_dim,latent_dim,bias=False)
  self.key=nn.Linear(latent_dim,latent_dim,bias=False)
  self.value=nn.Linear(latent_dim,latent_dim,bias=False)
  self.norms=nn.ModuleList(nn.LayerNorm(latent_dim) for _ in range(message_rounds))
  self.weight_head=nn.Linear(latent_dim,1)
  self.to_output=nn.Linear(latent_dim,DIM,bias=False)
  # Neutral gates initially preserve an equal mixture of actual forecasts.
  nn.init.zeros_(self.router.weight);nn.init.zeros_(self.router.bias)
  nn.init.zeros_(self.weight_head.weight);nn.init.zeros_(self.weight_head.bias)
 def forward(self,nodes,now_ms):
  nodes=align(nodes,now_ms);device=next(self.parameters()).device
  e=torch.tensor([n.embedding for n in nodes],dtype=torch.float32,device=device)
  kind_names=sorted(KINDS);kind=torch.tensor([kind_names.index(n.kind) for n in nodes],device=device)
  forecast=torch.tensor([n.probabilities is not None for n in nodes],dtype=torch.bool,device=device)
  if not forecast.any():raise ContractError('NO_FORECAST_NEURON')
  uncertainty=torch.tensor([[n.uncertainty.value if n.uncertainty.value is not None else 0.,float(n.uncertainty.value is not None)] for n in nodes],device=device)
  h=self.input(e)+self.kinds(kind)
  scores=self.router(torch.cat([h,uncertainty],dim=-1)).squeeze(-1)
  if not torch.isfinite(scores).all():raise ContractError('NONFINITE_ROUTER')
  rank=torch.argsort(scores,descending=True,stable=True)
  if self.mode=='soft':chosen=rank
  else:
   chosen=rank[:min(self.top_k,len(nodes))]
   if not forecast[chosen].any():
    # Explicit rule: preserve at least one actual forecast, not a synthetic
    # uniform distribution from a risk/execution descriptor.
    first=rank[forecast[rank]][:1]
    chosen=torch.cat([chosen[:-1],first]);chosen=chosen[torch.argsort(scores[chosen],descending=True,stable=True)]
  z=h[chosen];attentions=[]
  for norm in self.norms:
   logits=self.query(z)@self.key(z).T/math.sqrt(z.shape[-1])
   if not torch.isfinite(logits).all():raise ContractError('NONFINITE_GRAPH_ATTENTION')
   attention=logits.softmax(-1);attentions.append(attention);z=norm(z+attention@self.value(z))
  weights=scores[chosen].softmax(0);latent=(weights[:,None]*z).sum(0)
  forecast_chosen=forecast[chosen]
  expert_ids=chosen[forecast_chosen]
  price_logits=scores[expert_ids]+self.weight_head(z[forecast_chosen]).squeeze(-1)
  price_weights=price_logits.softmax(0)
  probabilities=torch.tensor([nodes[int(i)].probabilities for i in expert_ids],device=device)
  result=(price_weights[:,None]*probabilities).sum(0)
  embedding=F.normalize(self.to_output(latent),dim=-1)
  if not torch.isfinite(result).all() or not torch.isfinite(embedding).all() or torch.linalg.vector_norm(embedding)<.999:
   raise ContractError('INVALID_META_OUTPUT')
  return {'schema':'heptatrader.graph-meta-research.v2.256','mode':self.mode,
    'selected_node_ids':tuple(nodes[int(i)].node_id for i in chosen),
    'forecast_node_ids':tuple(nodes[int(i)].node_id for i in expert_ids),
    'router_weights':weights,'forecast_weights':price_weights,'probabilities':result,
    'embedding':embedding,'graph_attention':attentions,'context':nodes[0].context,
    'source_cutoff_ms':max(n.source_cutoff_ms for n in nodes),
    'input_evidence_sha256':tuple(digest(n.wire()) for n in nodes),
    'effect_authority':False,'actor_updated':False,'selected_for_runtime':False,
    'uncertainty_is_calibrated':False,'upstream_expert_compute_saved':False}


def seal_output(result,*,model_sha256,model_frozen_at_ms,available_at_ms,valid_until_ms):
 """Explicitly timestamp an inference result into the SAME 256-D wire contract.

 The caller supplies an externally verified immutable model identity; this does
 not assert it was selected by an owner. Never reuse input availability as output
 availability to hide inference latency.
 """
 if result.get('effect_authority') is not False or result.get('selected_for_runtime') is not False:
  raise ContractError('UNEXPECTED_META_AUTHORITY')
 embedding=tuple(float(x) for x in result['embedding'].detach().cpu())
 probs=tuple(float(x) for x in result['probabilities'].detach().cpu())
 packet=NeuronOutput(SCHEMA,'meta-research-'+model_sha256[:12],'price',model_sha256,result['context'],
  embedding,digest({'model':model_sha256,'basis':'LEARNED_META_OUTPUT_256_V1'}),probs,
  Uncertainty('normalized_class_entropy',entropy(probs)),(),result['source_cutoff_ms'],
  model_frozen_at_ms,available_at_ms,valid_until_ms,result['input_evidence_sha256'],False)
 return packet.validate(available_at_ms)
