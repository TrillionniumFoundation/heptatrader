"""Training-input identity and fair no-label readout preparation.

This module is *not* a trained net-value estimator, prospective prediction,
actor, order interface, or financial qualification.
"""
from __future__ import annotations
import hashlib,json,math
from dataclasses import dataclass
from pathlib import Path

PROFILE={
 'schema':'heptatrader.current-100k-english-feature-preflight.v1',
 'instrument':'EUR.USD',
 'currency':'USD',
 'behavior_profile':'EURUSD_ORIGINAL_ENTRY_CONF_0.62_USD100K_REFERENCE_NOTIONAL',
 'reference_notional_usd':100000,
 'max_tokens_each_arm':2048,
 'action_conditions':['BUY','SELL'],
 'features':'FROZEN_FULL_PRETRAINED_ENCODER_MASKED_MEAN_L2',
 'equal_trainable_capacity_experiment':'FIXED_PROJECTION_TO_256_PLUS_LINEAR_256_TO_1',
 'feature_projection_seed':2026100802,
 'no_retroactive_prospective_prediction':True,
 'old_25k_not_eligible':True,
 'automatic_selection':False,
 'trade_authority':False
}
class PreflightError(ValueError):pass
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def check_request(item,manifest_sha,period):
 if type(item)is not dict or type(item.get('request'))is not dict:raise PreflightError('SOURCE_REQUEST_TYPE')
 r=item['request']
 if (r.get('period_sha256')!=period or r.get('instrument')!='EUR.USD'
     or r.get('position_at_request')!=0
     or item.get('producer_manifest_sha256')!=manifest_sha
     or not isinstance(r.get('prompt'),str) or not r['prompt']
     or not isinstance(r.get('request_id'),str) or not r['request_id']
     or not isinstance(r.get('model_id'),str) or not r['model_id']
     or digest(r)!=item.get('request_sha256')
     or hashlib.sha256(r['prompt'].encode()).hexdigest()!=r.get('prompt_sha256')):
  raise PreflightError('CURRENT100K_EXACT_PRE_OPEN_REQUEST_REQUIRED')
 for field in ('source_record_sha256','source_prefix'):
  if field not in item:raise PreflightError('SOURCE_EVIDENCE_MISSING:'+field)
 return r
def feature_text(r,side):
 if side not in PROFILE['action_conditions']:raise PreflightError('CONDITIONAL_ACTION')
 if r.get('position_at_request')!=0:raise PreflightError('NOT_FLAT_AT_REQUEST')
 return (r['prompt']+
 '\nFinancial value context: EUR.USD; requested reference notional budget=100000 USD; '+
 'selected action='+side+'; behavior model='+r['model_id']+
 '. Predict eventual roundtrip net USD after reported execution commissions.')
def paired_items(items,manifest_sha,period):
 if type(items) not in (list,tuple) or not 1<=len(items)<=128:raise PreflightError('SOURCE_COUNT')
 rids=set();out=[]
 for item in sorted(items,key=lambda x:(x['request']['started_at_ms'],x['request']['request_id'])):
  r=check_request(item,manifest_sha,period)
  if r['request_id'] in rids:raise PreflightError('DUPLICATE_ORIGINAL_REQUEST')
  rids.add(r['request_id'])
  for side in PROFILE['action_conditions']:
   text=feature_text(r,side)
   out.append({'request_id':r['request_id'],'started_at_ms':r['started_at_ms'],
     'side':side,'input_sha256':item['request_sha256'],
     'prompt_sha256':r['prompt_sha256'],'source_record_sha256':item['source_record_sha256'],
     'source_prefix':item['source_prefix'],
     'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'text':text})
 return out
def project_fixed_cpu(features,seed,destination=256):
 # Fixed random projection, NOT trained on future market samples. The same
 # target width controls readout parameter count; it does not equalize quality.
 import torch
 from torch.nn import functional as F
 if features.ndim!=2 or features.shape[1] not in (768,1024) or destination!=256:
  raise PreflightError('ENCODER_DIM_OR_PROJECTION_SCOPE')
 g=torch.Generator(device='cpu').manual_seed(seed)
 matrix=torch.randn(features.shape[1],destination,generator=g,dtype=torch.float32)
 matrix=F.normalize(matrix,dim=0)
 result=F.normalize(features.float()@matrix,dim=-1)
 if not torch.isfinite(result).all():raise PreflightError('NONFINITE_REPRESENTATION')
 return result.contiguous()
def check_prepared_economics_never_used(features):
 # Cannot infer feature origins merely from an array. Input construction
 # accepts ONLY the verified native REQUEST, never COMMAND_BINDING.
 if any('order_quantity' in f or 'reference_price' in f or 'realized_net_usd' in f for f in features):
  raise PreflightError('HINDSIGHT_FEATURE')
 return True
