"""Explicit gradient receipts; legacy consumed_rows mean reservations only."""
from __future__ import annotations
import re
from .integrity import ContractError,digest
SCHEMA='heptatrader.laya-gradient-row-accounting.v1'
BATCH_SIZE=2
MAX_STEPS=16
MAX_TRAINING_ROWS=BATCH_SIZE*MAX_STEPS
MIN_TRAINING_ROWS=16

def row_id(row):
 return digest([row['sample_id'],row['horizon_sec'],row['outcome_receipt_digest']])

def checked_ids(values,minimum=0,maximum=MAX_TRAINING_ROWS):
 if (not isinstance(values,list) or not minimum<=len(values)<=maximum or
     any(not isinstance(x,str) or re.fullmatch('[0-9a-f]{64}',x) is None for x in values) or
     len(set(values))!=len(values)):
  raise ContractError('ROW_ACCOUNTING_IDS_OR_BUDGET')
 return values

def select_rows(new_rows):
 return new_rows[-MAX_TRAINING_ROWS:]

def batches(rows):
 if not MIN_TRAINING_ROWS<=len(rows)<=MAX_TRAINING_ROWS:
  raise ContractError('ROW_ACCOUNTING_TRAINING_BUDGET')
 return [rows[i:i+BATCH_SIZE] for i in range(0,len(rows),BATCH_SIZE)]

def receipt(reserved,steps,measured):
 checked_ids(reserved,MIN_TRAINING_ROWS)
 checked_ids(measured,MIN_TRAINING_ROWS)
 if not isinstance(steps,list) or not 1<=len(steps)<=MAX_STEPS:
  raise ContractError('ROW_ACCOUNTING_STEP_BUDGET')
 attempted=[];applied=[];rolled_back=[]
 for step in steps:
  ids=checked_ids(step.get('training_row_ids'),1,BATCH_SIZE)
  if type(step.get('applied')) is not bool:
   raise ContractError('ROW_ACCOUNTING_APPLIED_FLAG')
  attempted.extend(ids)
  (applied if step['applied'] else rolled_back).extend(ids)
 checked_ids(attempted,MIN_TRAINING_ROWS)
 if set(attempted)!=set(reserved) or set(measured)!=set(reserved):
  raise ContractError('ROW_ACCOUNTING_RESERVED_GRADIENT_MISMATCH')
 return {'schema':SCHEMA,'reserved_row_ids':list(reserved),
         'gradient_attempted_row_ids':attempted,'applied_row_ids':applied,
         'rolled_back_row_ids':rolled_back,'measurement_row_ids':list(measured),
         'reserved_count':len(reserved),'gradient_attempted_count':len(attempted),
         'applied_count':len(applied),'rolled_back_count':len(rolled_back),
         'measurement_count':len(measured)}

def verify_candidate(job,candidate):
 # Do not retroactively strengthen or rewrite frozen historical receipts.
 version=job.get('training_row_accounting_schema')
 if version is None:return
 if version=='heptatrader.laya-gradient-row-accounting.v2':
  from . import resident_training_v5 as v5
  from . import resident_training_v6 as v6
  from . import resident_training_v7 as v7
  if v7.enabled(job.get('objective')):
   v7.verify_candidate(job,candidate);return
  if v6.enabled(job.get('objective')):
   v6.verify_candidate(job,candidate);return
  if v5.enabled(job.get('objective')):
   v5.verify_candidate(job,candidate);return
  from .resident_training_v4 import verify_candidate as verify_episode
  verify_episode(job,candidate);return
 if version!=SCHEMA:raise ContractError('ROW_ACCOUNTING_SCHEMA')
 wanted=job['training_row_ids'];got=candidate.get('row_accounting')
 if not isinstance(got,dict):raise ContractError('ROW_ACCOUNTING_RECEIPT_MISSING')
 rebuilt=receipt(wanted,candidate.get('steps'),got.get('measurement_row_ids'))
 if got!=rebuilt or candidate.get('measurement',{}).get('rows')!=len(wanted):
  raise ContractError('ROW_ACCOUNTING_RECEIPT_MISMATCH')
