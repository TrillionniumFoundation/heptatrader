"""Fail-closed quality and trial-identity preflight for *future* paired-price trials.

Review only. Assesses unchanged provider-observed quote times; never interpolates,
registers a trial itself, changes IB connectivity or grants a trading permission.
"""
from __future__ import annotations
from collections import Counter
import time
from pathlib import Path
from laya_cell.integrity import ContractError,digest
from laya_cell.source_readiness import RULE as SOURCE_RULE,assess

EXPECTED_STATES=frozenset({'USABLE','INPUT_UNAVAILABLE','OUTCOME_UNAVAILABLE','ENDPOINT_GAP','PATH_GAP'})

def require_unused_candidate(home,period_sha256,candidate_id):
 """A candidate with a prior prospective plan cannot be recycled for a new draw."""
 if (type(period_sha256) is not str or len(period_sha256)!=64 or
     type(candidate_id) is not str or len(candidate_id)!=64 or
     any(x not in '0123456789abcdef' for x in period_sha256+candidate_id)):
  raise ContractError('PRETRIAL_IDENTITY_FORMAT')
 root=Path(home)/'.local/state/heptatrader/neuron-learning/paired-price'/period_sha256/candidate_id
 if root.is_symlink():raise ContractError('PRETRIAL_ROOT_SYMLINK')
 for p in root.glob('*/plan.json'):
  if p.is_symlink():raise ContractError('PRETRIAL_PLAN_SYMLINK')
  raise ContractError('CANDIDATE_ALREADY_HAS_PROSPECTIVE_PLAN_NO_REPEAT')
 return True

def verify_quality(config,quality,now_ms):
 if config.get('automatic_trading') is not False or type(now_ms) is not int or now_ms<=0:
  raise ContractError('SOURCE_PREFLIGHT_SCOPE')
 if quality.get('schema')!='heptatrader.quote-readiness-status.v1' or quality.get('rule')!=SOURCE_RULE:
  raise ContractError('SOURCE_READINESS_RULE_OR_SCHEMA_CHANGED')
 if quality.get('period_sha256')!=config.get('period_sha256'):
  raise ContractError('SOURCE_READINESS_PERIOD_CHANGED')
 if quality.get('automatic_trial_registration') is not False or quality.get('trading_authority') is not False or quality.get('orders_sent')!=0:
  raise ContractError('SOURCE_READINESS_HAS_EFFECT_AUTHORITY')
 if quality.get('state')!='READY_FOR_REVIEW_NOT_AUTHORIZATION' or quality.get('reasons')!=[]:
  raise ContractError('SOURCE_READINESS_NOT_READY')
 at=quality.get('at_ms')
 if type(at) is not int or not 0<=now_ms-at<=15000:
  raise ContractError('SOURCE_READINESS_STALE_OR_FUTURE')
 probes=quality.get('probes')
 if type(probes)is not list or len(probes)!=SOURCE_RULE['input_probes']:
  raise ContractError('SOURCE_READINESS_PROBE_COUNT')
 if not all(type(p)is dict and p.get('status') in EXPECTED_STATES and type(p.get('start_ms'))is int for p in probes):
  raise ContractError('SOURCE_READINESS_PROBE_SCHEMA')
 times=[p['start_ms'] for p in probes]
 if any(y-x!=SOURCE_RULE['spacing_ms'] for x,y in zip(times,times[1:])):
  raise ContractError('SOURCE_READINESS_WINDOW_NOT_FIXED')
 if times[-1]+SOURCE_RULE['input_grace_ms']+SOURCE_RULE['horizon_ms']+SOURCE_RULE['endpoint_tolerance_ms']>at:
  raise ContractError('SOURCE_READINESS_UNMATURED_PROBES')
 count=Counter(p['status'] for p in probes)
 if count!=quality.get('counts') or count['USABLE']!=quality.get('usable') or len(probes)-count['USABLE']!=quality.get('unusable'):
  raise ContractError('SOURCE_READINESS_COUNT_CONFLICT')
 if count['USABLE']<SOURCE_RULE['minimum_usable'] or len(probes)-count['USABLE']>SOURCE_RULE['maximum_missing']:
  raise ContractError('SOURCE_READINESS_COVERAGE_BELLOW_FIXED_GATE')
 age=quality.get('latest_quote_age_ms')
 if type(age) is not int or not 0<=age<=SOURCE_RULE['source_ttl_ms']:
  raise ContractError('SOURCE_READINESS_QUOTE_STALE')
 source=quality.get('source_prefixes')
 if type(source) is not list or not source or digest(source)!=quality.get('source_prefix_digest'):
  raise ContractError('SOURCE_READINESS_PREFIX_IDENTITY')
 if quality.get('past_probe_labels_are_not_independent_efficacy_samples') is not True or quality.get('old_failed_windows_changed') is not False:
  raise ContractError('SOURCE_READINESS_FALSE_EFFICACY_CLAIM')
 return {'schema':'heptatrader.paired-price-source-admission.v1','status':'QUALITY_SCREENED_NOT_OUTCOME_QUALIFIED',
  'source_rule_sha256':digest(SOURCE_RULE),'source_prefix_digest':quality['source_prefix_digest'],
  'assessed_at_ms':at,'registered_quality_slots':len(probes),'usable':count['USABLE'],'missing':len(probes)-count['USABLE'],
  'latest_quote_age_ms':age,'no_market_data_mutation':True,'automatic_trial_registration':False,
  'evidence_is_prior_operational_quality_not_future_efficacy':True,'trading_authority':False}

def require_live_quality(config):
 # Never trust a caller-provided health-file "READY" boolean or a stale snapshot.
 now=int(time.time()*1000)
 quality=assess(config,now)
 return verify_quality(config,quality,int(time.time()*1000))
