"""Independent future-price qualification; never trades or mints owner authority.

The rule is preregistered per candidate. Repeated candidates spend a bounded total
alpha budget. Missing/censored windows remain in the denominator and the record.
A statistical PASS is a proposal, not a canonical Agentd acknowledgement.
"""
from __future__ import annotations
import math
from .integrity import ContractError,digest
from .promotion_power import (PROTOCOL,SCHEMA,planned_rule,checked_trial,
 MATCHED_PARENT_PROTOCOL,MATCHED_PARENT_SCHEMA,MATCHED_PARENT,POWER_PROTOCOLS)

RULE={'schema':'heptatrader.laya-promotion-rule.v1','slots':12,'slot_spacing_ms':360000,
 'horizon_ms':300000,'input_grace_ms':60000,'min_observed':10,'max_censored':2,
 'minimum_return_classes':2,'minimum_mean_brier_improvement':1e-6,
 'global_alpha_budget':.05,'alpha_spending':'0.05/(trial*(trial+1))',
 'bootstrap_hit_rate_not_worse':True,'canonical_owner_required':True,
 'output_scope':'SHADOW_QUALIFICATION_ONLY','trading_cutover_allowed':False}


def rule_for_trial(trial, protocol=None):
 if protocol is None:return dict(RULE)
 return planned_rule(RULE,trial,protocol)


def check_protocol_config(config):
 protocol=config.get('promotion_protocol')
 if protocol not in (None,*POWER_PROTOCOLS):raise ContractError('UNKNOWN_PROMOTION_PROTOCOL')
 if protocol!=MATCHED_PARENT_PROTOCOL:return
 from . import resident_parent_calibration as comparison
 if (config.get('objective',{}).get('schema')!='heptatrader.laya-resident-objective.v8'
     or not comparison.check_config(config)):
  raise ContractError('MATCHED_PARENT_SCREEN_REQUIRES_V8_COMPARATOR')
 for name in ('learn/laya_cell/promotion.py','learn/laya_cell/promotion_power.py'):
  value=config.get('runtime_sha256',{}).get(name)
  if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
   raise ContractError('MATCHED_PARENT_SCREEN_RUNTIME_PINS_REQUIRED')


def protocol_for_job(config,job):
 """A new screen never upgrades an already frozen job on configuration reload."""
 selected=config.get('promotion_protocol');original=job.get('promotion_protocol')
 if MATCHED_PARENT_PROTOCOL in (selected,original):return original
 return selected


def validate_plan_rule(plan):
 from .resident_price_baselines import validate_value_rule
 validate_value_rule(plan)
 rule=plan.get('rule')
 if rule==RULE:return rule
 schemas={SCHEMA:PROTOCOL,MATCHED_PARENT_SCHEMA:MATCHED_PARENT_PROTOCOL}
 if not isinstance(rule,dict) or rule.get('schema') not in schemas:raise ContractError('PROMOTION_RULE_CHANGED')
 if rule!=planned_rule(RULE,plan.get('trial'),schemas[rule['schema']]):raise ContractError('PROMOTION_RULE_CHANGED')
 if rule['schema']==MATCHED_PARENT_SCHEMA:
  from . import resident_parent_calibration as comparison
  from . import resident_price_input as price_input
  if not comparison.enabled(plan) or validate_value_rule(plan) is None:
   raise ContractError('MATCHED_PARENT_SCREEN_COMPARATORS_REQUIRED')
  bound=comparison.validate(plan.get(comparison.RECORD),
   price_baselines=plan.get('price_baselines'),parent_id=plan.get('parent_id'))
  inputs=price_input.check_bindings(plan)
  if (bound['parameter_update_isolated'] is not True
      or inputs['price_input_objective']!=price_input.V8_OBJECTIVE_SCHEMA
      or inputs['parent_price_input_profile']!=price_input.RAW):
   raise ContractError('MATCHED_PARENT_SCREEN_INPUTS_NOT_ISOLATED')
 return rule


def assess(plan,results,now_ms):
 rule=validate_plan_rule(plan)
 if type(plan.get('trial'))is not int or plan['trial']<1:raise ContractError('PROMOTION_TRIAL')
 if len({r['slot'] for r in results})!=len(results):raise ContractError('DUPLICATE_EVALUATION_SLOT')
 if any(r.get('candidate_id')!=plan['candidate_id'] or not 0<=r['slot']<rule['slots'] for r in results):
  raise ContractError('EVALUATION_CANDIDATE_OR_SLOT')
 if plan.get('evaluation_attempt') is not None and any(r.get('plan_id')!=plan['plan_id'] for r in results):
  raise ContractError('EVALUATION_ATTEMPT_OUTCOME_PLAN_IDENTITY')
 base={'candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],'rule_id':digest(rule),
       'automatic_execution_selection':False,'canonical_owner_admitted':False,'promotion_performed':False}
 from . import resident_parent_calibration
 parent_comparison=resident_parent_calibration.assessment(plan,[r for r in results if r['status']=='OBSERVED'])
 if parent_comparison is not None:base['same_budget_calibrated_parent']=parent_comparison
 if now_ms<plan['outcome_end_ms']:
  return {**base,'state':'PENDING','results':len(results),'reason':'FIXED_FUTURE_WINDOW_NOT_COMPLETE'}
 reasons=[];observed=[r for r in results if r['status']=='OBSERVED'];censored=rule['slots']-len(observed)
 if len(results)!=rule['slots']:reasons.append('MISSING_EVALUATION_SLOTS')
 if len(observed)<rule['min_observed']:reasons.append('INSUFFICIENT_OBSERVABLE_FUTURE_WINDOWS')
 if censored>rule['max_censored']:reasons.append('EXCESS_CENSORED_WINDOWS')
 classes={r['target_class'] for r in observed}
 if len(classes)<rule['minimum_return_classes']:reasons.append('INSUFFICIENT_OUTCOME_VARIATION')
 previous=-1
 for r in sorted(observed,key=lambda r:r['anchor_ms']):
  if r['anchor_ms']<previous:reasons.append('OVERLAPPING_OUTCOME_WINDOWS')
  previous=r['anchor_ms']+rule['horizon_ms']
  if r['prediction_available_ms']>=previous or r['trained_until_ms']>r['anchor_ms']:
   reasons.append('LOOKAHEAD')
  if r.get('path_gap') is not False:reasons.append('OBSERVED_PATH_NOT_COMPLETE')
 score='calibrated_parent_brier' if rule.get('schema')==MATCHED_PARENT_SCHEMA else 'parent_brier'
 improvements=[r[score]-r['candidate_brier'] for r in observed]
 if rule.get('schema')==MATCHED_PARENT_SCHEMA:
  raw=[r['parent_brier']-r['candidate_brier'] for r in observed]
  base.update(primary_comparator=MATCHED_PARENT,parameter_update_isolated=True,
   raw_parent_mean_brier_improvement=sum(raw)/len(raw) if raw else None)
 if not all(math.isfinite(x) for x in improvements):raise ContractError('NONFINITE_EVALUATION_LOSS')
 mean=sum(improvements)/len(improvements) if improvements else None
 if mean is None or mean<=rule['minimum_mean_brier_improvement']:reasons.append('NO_POSITIVE_MEAN_IMPROVEMENT')
 non_ties=[x for x in improvements if abs(x)>1e-12];wins=sum(x>0 for x in non_ties);n=len(non_ties)
 p=sum(math.comb(n,k) for k in range(wins,n+1))/2**n if n else 1.0
 alpha=.05/(plan['trial']*(plan['trial']+1))
 if p>alpha:reasons.append('PAIRED_SIGN_EVIDENCE_INSUFFICIENT')
 if rule.get('schema') in (SCHEMA,MATCHED_PARENT_SCHEMA):
  if n<rule['min_non_ties']:reasons.append('INSUFFICIENT_NON_TIE_POWER_SAMPLE')
  base.update(evidence_scope=rule['output_scope'],sign_null_assumption=rule['sign_null_assumption'],
    unconditional_efficacy_proven=False,power_is_design_assumption=True)
 if sum(r['candidate_hit'] for r in observed)<sum(r['bootstrap_hit'] for r in observed):
  reasons.append('BELOW_BOOTSTRAP_INTERVAL_ACCURACY')
 if plan.get('price_value_rule') is not None:
  from .resident_price_baselines import value_assessment
  value_report,value_reasons=value_assessment(plan,observed)
  base['same_window_price_value']=value_report;reasons.extend(value_reasons)
 return {**base,'state':'QUALIFIED_AWAITING_CANONICAL_OWNER' if not reasons else 'REJECTED',
  'statistical_qualification':not reasons,'observed':len(observed),'censored':censored,
  'mean_brier_improvement':mean,'paired_sign_p':p,'alpha':alpha,'wins':wins,'non_ties':n,
  'reasons':sorted(set(reasons)),'owner_blocker':'CANONICAL_AGENTD_LAYA_ACCEPTANCE_ROUTE_NOT_ENROLLED'}
