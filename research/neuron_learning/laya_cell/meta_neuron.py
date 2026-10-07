"""Typed Hepta-neuron output contract and dormant sparse graph meta-fusion.

No order API is present. Training/deployment is gated on independent prospective
qualification of the current numeric+Laya two-node experiment.
"""
from __future__ import annotations
import math
import torch
from torch import nn
from torch.nn import functional as F
from .integrity import ContractError,digest

PROFILE={
 "schema":"heptatrader.meta-neuron-profile.v1","embedding_dim":32,"probability_dim":7,
 "max_nodes":32,"top_k_max":4,"message_rounds":2,
 "router":"LEARNED_TOP_K","graph":"ATTENTION_AMONG_SELECTED_NODES",
 "effect_authority":False,"automatic_trading":False,"policy_gradient":False,
 "activation_requires":"TEMPORAL_LAYA_FUSION_INDEPENDENT_PROSPECTIVE_QUALIFICATION",
 "canonical_owner_required":True,
}

def entropy(p):
 p=p.clamp_min(1e-8)
 return -(p*p.log()).sum(-1)/math.log(p.shape[-1])

def _number(v,label):
 if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(float(v)):
  raise ContractError("META_"+label)
 return float(v)

def validate_output(node):
 if not isinstance(node,dict):raise ContractError("META_NODE_OBJECT")
 node_id=node.get("node_id")
 if not isinstance(node_id,str) or not node_id or len(node_id)>128:raise ContractError("META_NODE_ID")
 if node.get("effect_authority") is not False:raise ContractError("META_EFFECT_AUTHORITY")
 emb=node.get("embedding");probs=node.get("probs")
 if not isinstance(emb,list) or len(emb)!=32:raise ContractError("META_EMBEDDING_DIM")
 if not isinstance(probs,list) or len(probs)!=7:raise ContractError("META_PROBABILITY_DIM")
 emb=[_number(x,"EMBEDDING") for x in emb];probs=[_number(x,"PROBABILITY") for x in probs]
 if any(x<0 or x>1 for x in probs) or abs(sum(probs)-1)>1e-5:raise ContractError("META_PROBABILITY_SIMPLEX")
 norm=math.sqrt(sum(x*x for x in emb))
 if not .999<=norm<=1.001:raise ContractError("META_EMBEDDING_NORM")
 uncertainty=_number(node.get("uncertainty"),"UNCERTAINTY")
 if not 0<=uncertainty<=1:raise ContractError("META_UNCERTAINTY_RANGE")
 evidence=node.get("evidence")
 if not isinstance(evidence,dict) or evidence.get("effect_authority") is not False:raise ContractError("META_EVIDENCE_AUTHORITY")
 for key in ("input_id","sample_id"):
  if not isinstance(evidence.get(key),str) or len(evidence[key])!=64:raise ContractError("META_EVIDENCE_ID")
 if not isinstance(evidence.get("source_prefixes"),list):raise ContractError("META_EVIDENCE_PREFIX")
 return {**node,"embedding":emb,"probs":probs,"uncertainty":uncertainty}

def canonical_nodes(nodes):
 if not isinstance(nodes,list) or not 1<=len(nodes)<=PROFILE["max_nodes"]:raise ContractError("META_NODE_COUNT")
 checked=[validate_output(x) for x in nodes]
 ids=[x["node_id"] for x in checked]
 if len(ids)!=len(set(ids)):raise ContractError("META_DUPLICATE_NODE")
 if len({x["evidence"]["input_id"] for x in checked})!=1 or len({x["evidence"]["sample_id"] for x in checked})!=1:
  raise ContractError("META_CAUSAL_INPUT_MISMATCH")
 return sorted(checked,key=lambda x:x["node_id"])

def tensorize(nodes,device):
 nodes=canonical_nodes(nodes)
 emb=torch.tensor([x["embedding"] for x in nodes],device=device,dtype=torch.float32)
 probs=torch.tensor([x["probs"] for x in nodes],device=device,dtype=torch.float32)
 unc=torch.tensor([[x["uncertainty"]] for x in nodes],device=device,dtype=torch.float32)
 return nodes,emb,probs,unc

class SparseGraphMetaNeuron(nn.Module):
 def __init__(self,top_k=4):
  super().__init__()
  if type(top_k)is not int or not 1<=top_k<=PROFILE["top_k_max"]:raise ContractError("META_TOP_K")
  self.top_k=top_k;d=32
  self.router=nn.Linear(d+1,1)
  self.q=nn.Linear(d,d,bias=False);self.k=nn.Linear(d,d,bias=False);self.v=nn.Linear(d,d,bias=False)
  self.norms=nn.ModuleList([nn.LayerNorm(d) for _ in range(PROFILE["message_rounds"])])
  self.head=nn.Linear(d,7);self.blend=nn.Linear(d,1)
 def forward(self,embedding,expert_probs,uncertainty):
  if embedding.ndim!=2 or embedding.shape[1]!=32 or expert_probs.shape!=(embedding.shape[0],7) or uncertainty.shape!=(embedding.shape[0],1):
   raise ContractError("META_FORWARD_SHAPE")
  n=embedding.shape[0];count=min(self.top_k,n)
  scores=self.router(torch.cat([embedding,uncertainty],-1)).squeeze(-1)
  tie=torch.arange(n,device=embedding.device,dtype=embedding.dtype)*1e-7
  chosen=torch.topk(scores-tie,k=count,largest=True,sorted=True).indices
  h=embedding.index_select(0,chosen);selected_scores=scores.index_select(0,chosen)
  for norm in self.norms:
   attn=(self.q(h)@self.k(h).T)/math.sqrt(h.shape[-1])
   h=norm(h+attn.softmax(-1)@self.v(h))
  weights=selected_scores.softmax(0);meta=(weights[:,None]*h).sum(0)
  learned=self.head(meta).softmax(-1)
  baseline=(weights[:,None]*expert_probs.index_select(0,chosen)).sum(0)
  alpha=torch.sigmoid(self.blend(meta)).squeeze()
  probs=alpha*learned+(1-alpha)*baseline;probs=probs/probs.sum()
  return {"selected_indices":chosen,"router_weights":weights,"embedding":F.normalize(meta,dim=0),
   "probs":probs,"uncertainty":entropy(probs[None])[0],"learned_blend":alpha}

def contract_id():return digest(PROFILE)
