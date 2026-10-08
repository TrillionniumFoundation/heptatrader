"""Pinned chronology, source identity and policy for five-arm future MID trials."""
from __future__ import annotations
import hashlib,json,math,time
from pathlib import Path
from laya_cell.integrity import ContractError,digest,read_json,sha_file,canonical
from laya_cell.resident_store import write_once,atomic

PROFILE={
 'schema':'heptatrader.price-complementarity-training.v1','instrument':'EUR.USD',
 'horizon_sec':300,'classes':7,'seed':20261008,'max_quote_samples':4096,
 'expert_train_rows':256,'gate_train_rows':64,'purge_ms':305000,
 'num_epochs':36,'semantic_epochs':28,'gate_epochs':24,'batch_size':32,
 'learning_rate':0.001,'weight_decay':0.001,'encoder_max_tokens':2048,
 'original_laya_frozen':True,'numeric_train_independent':True,'semantic_train_independent':True,
 'gate_fits_after_expert_freeze':True,'effect_authority':False
}
RULE={
 'schema':'heptatrader.price-complementarity-prospective.v1','slots':24,'slot_spacing_ms':360000,
 'horizon_ms':300000,'input_grace_ms':60000,'endpoint_tolerance_ms':5000,
 'path_gap_max_ms':30000,'min_observed':20,'max_censored':4,'min_class_count':2,
 'min_half_observed':8,'min_mean_brier_gain':0.00001,
 'comparators':['numeric','semantic','fixed_mix','legacy_numeric'],
 'alpha_each':0.0125,'effects_authorized':False,'automatic_selection':False
}
PROJECTION_SEED=202610081
ARMS=('numeric','semantic','fixed_mix','gate','legacy_numeric')
def require(condition,reason):
 if not condition:raise ContractError(reason)
def utc_ms():return int(time.time()*1000)
def sha_file_ensure(path,expected):
 require(sha_file(Path(path))==expected,'PINNED_BYTES_CHANGED:'+str(path))
def immutable_json(path,value):
 path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 if path.exists():
  require(read_json(path)==value,'IMMUTABLE_FILE_CHANGED:'+str(path));return
 with path.open('xb') as f:
  f.write(canonical(value)+b'\n');f.flush()
 path.chmod(0o400)
def check_candidate(folder):
 folder=Path(folder);value=read_json(folder/'candidate.json')
 require(value['candidate_id']==digest({k:v for k,v in value.items() if k!='candidate_id'}),'CANDIDATE_ID_CHANGED')
 require(value['profile']==PROFILE and value['effect_authority'] is False,'CANDIDATE_PROFILE')
 for name,sha in value['artifacts'].items():sha_file_ensure(folder/name,sha)
 return value
def check_plan(plan,now=None):
 require(plan['plan_id']==digest({k:v for k,v in plan.items() if k!='plan_id'}),'TRIAL_PLAN_ID')
 require(plan['rule']==RULE and plan['effect_authority'] is False,'TRIAL_RULE_CHANGED')
 require(plan['registered_at_ms']<plan['start_ms']<plan['end_ms'],'TRIAL_CLOCK')
 if now is not None:require(now>=plan['registered_at_ms'],'CLOCK_REGRESSION')
 return plan
