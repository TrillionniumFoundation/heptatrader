"""Laya scorer plus original ACT/ESCALATE head adapters, never BUY/SELL logits.

The full pretrained checkpoint stays frozen. The existing scorer profile is
preserved; a rank-one update on 64 columns of the final action layer adds only
128 effective coordinates. Original action meanings follow rl_agent_config.
"""
from __future__ import annotations
import copy
import torch
from torch import nn
from torch.nn import functional as F
from .integrity import ContractError,digest
from .model import TradingDecisionCell,PROFILE as SCORER_PROFILE

PROFILE={**SCORER_PROFILE,'schema':'heptatrader.laya-resident-profile.v1',
 'action_head_trainable':True,'action_target':'FROZEN_PARENT_ANSWER_CORRECTNESS_ACT_VS_ESCALATE',
 'action_target_is_trade_permission':False,'action_cost_wrong':3.0,'action_cost_escalate':.5,
 'action_targets':{'act_head.2':{'rows':[0,1],'cols':list(range(64)),'rank':1}},
 'maximum_effective_coordinates':3968}

class MaskedActionLinear(nn.Module):
 def __init__(self,base):
  super().__init__()
  if not isinstance(base,nn.Linear) or base.in_features!=256 or base.out_features!=2 or base.weight.dtype!=torch.float32:
   raise ContractError('PINNED_LAYA_ACT_ESCALATE_SHAPE_REQUIRED')
  self.base=base.requires_grad_(False);self.rows=(0,1)
  self.register_buffer('row_index',torch.tensor([0,1],device=base.weight.device),persistent=False)
  self.register_buffer('col_index',torch.arange(64,device=base.weight.device),persistent=False)
  generator=torch.Generator(device='cpu').manual_seed(2026100801)
  self.A=nn.Parameter((torch.randn(1,64,generator=generator)*.01).to(base.weight.device))
  self.B=nn.Parameter(torch.zeros(2,1,device=base.weight.device))
 def forward(self,x):return self.base(x)+F.linear(F.linear(x.index_select(-1,self.col_index),self.A),self.B)
 def full_effective_weight(self):return self.base.weight.index_add(1,self.col_index,self.B@self.A)

class ResidentDecisionCell(TradingDecisionCell):
 def __init__(self,pretrained,base_id):
  super().__init__(pretrained,base_id)
  # The action wrapper keeps the exact frozen Parameter objects. Retain strong
  # references while their names change, so identities cannot be recycled.
  frozen={id(p):(p,self._frozen[name]) for name,p in self.model.named_parameters() if not p.requires_grad}
  adapter=MaskedActionLinear(self.model.get_submodule('act_head.2'))
  self.model.act_head[2]=adapter;self.adapters['act_head.2']=adapter
  self.profile=copy.deepcopy(PROFILE)
  self.parent_effective={name:a.full_effective_weight().detach().clone() for name,a in self.adapters.items()}
  current={name:p for name,p in self.model.named_parameters() if not p.requires_grad}
  if {id(p) for p in current.values()}!=set(frozen):raise ContractError('RESIDENT_FROZEN_PARAMETER_IDENTITY_CHANGED')
  self._frozen={name:frozen[id(p)][1] for name,p in current.items()}
  self.train(False)
 def restore_parent(self,state,allow_scorer_only=False):
  current=self.trainable_state()
  if allow_scorer_only and set(state)=={n for n in current if '.scorer.' in n}:
   current.update(state);self.restore_trainable(current)
  else:self.restore_trainable(state)
  self.parent_effective={name:a.full_effective_weight().detach().clone() for name,a in self.adapters.items()}

def answer_targets(frozen_parent_logits,true_labels):
 """Independent observed correctness, not remote confidence or financial return."""
 if frozen_parent_logits.ndim!=2 or frozen_parent_logits.shape[1]!=7 or true_labels.shape!=frozen_parent_logits.shape[:1]:
  raise ContractError('ACTION_TARGET_SHAPE')
 # ACT = 0; ESCALATE = 1. Model choice was frozen before adapter updates.
 return (frozen_parent_logits.detach().argmax(-1)!=true_labels).long()

def action_loss(action_logits,targets):
 if action_logits.ndim!=2 or action_logits.shape[1]!=2 or targets.shape!=action_logits.shape[:1]:
  raise ContractError('ACTION_LOSS_SHAPE')
 if not torch.isfinite(action_logits).all() or not ((targets==0)|(targets==1)).all():
  raise ContractError('ACTION_LOSS_INPUT')
 # Cross entropy is stable even when the upstream pretrained action head saturates.
 # A shared scale is NOT silently introduced; raw upstream logits remain the interface.
 return F.cross_entropy(action_logits,targets)

def identity():return digest(PROFILE)
