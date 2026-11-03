"""Bounded learning-only storage admission. Never prunes existing evidence.

This is an application admission check, not a filesystem quota. A bounded job
reserve plus filesystem reserve is checked before new work; old bytes remain.
"""
from __future__ import annotations
import os
from collections import OrderedDict
from pathlib import Path
from .integrity import ContractError
from .tree_usage_cache import TreeUsageCache

GIB=1024**3
MAX_EXPLICIT_STATE_BUDGET=16*GIB
_TREE_USAGE=OrderedDict()

def _usage(root):
 key=str(root)
 cache=_TREE_USAGE.get(key)
 if cache is None:
  cache=TreeUsageCache(root);_TREE_USAGE[key]=cache
  while len(_TREE_USAGE)>4:
   _,old=_TREE_USAGE.popitem(last=False);old.close()
 else:_TREE_USAGE.move_to_end(key)
 return cache.usage()

def policy(config):
 limit=config.get('state_max_bytes');headroom=config.get('state_admission_reserve_bytes',0)
 free=config.get('filesystem_reserve_bytes',0)
 if type(limit)is not int or not 0<limit<=MAX_EXPLICIT_STATE_BUDGET:raise ContractError('STATE_BUDGET_NOT_FINITE_OR_REVIEWED')
 if type(headroom)is not int or not 0<=headroom<limit:raise ContractError('STATE_HEADROOM_INVALID')
 if type(free)is not int or not 0<=free<=1024*GIB:raise ContractError('FILESYSTEM_RESERVE_INVALID')
 return limit,headroom,free

def inspect(config):
 limit,reserve,minimum_free=policy(config);root=Path(config['state_root'])
 if not root.is_absolute() or root.is_symlink() or not root.is_dir():raise ContractError('STATE_ROOT_SCOPE')
 # Change notifications account for mutable children, not just root metadata.
 # Topology changes/lost notifications force a full scan; disk free is always
 # queried afresh and the existing admission thresholds remain unchanged.
 used,files=_usage(root)
 disk=os.statvfs(root);available=disk.f_bavail*disk.f_frsize
 result={'state_used_bytes':used,'state_max_bytes':limit,'admission_reserve_bytes':reserve,
  'state_remaining_bytes':limit-used,'filesystem_available_bytes':available,'filesystem_reserve_bytes':minimum_free,
  'counted_regular_files':files,'evidence_pruning_permitted':False,'automatic_budget_increase':False}
 if used>limit:reason='STATE_BUDGET_FULL_NO_EVIDENCE_PRUNING'
 elif available<minimum_free+reserve:reason='FILESYSTEM_RESERVE_NO_NEW_LEARNING'
 elif used+reserve>limit:reason='STATE_HEADROOM_NO_NEW_LEARNING'
 else:reason=None
 result['admission_ready']=reason is None;result['blocked_reason']=reason
 return result
