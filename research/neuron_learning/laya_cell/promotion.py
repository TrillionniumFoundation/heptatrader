"""Independent future-price qualification; never trades or mints owner authority.

The rule is preregistered per candidate. Repeated candidates spend a bounded total
alpha budget. Missing/censored windows remain in the denominator and the record.
A statistical PASS is a proposal, not a canonical Agentd acknowledgement.
"""
from __future__ import annotations
import math
from .integrity import ContractError,digest
from .promotion_power import PROTOCOL,SCHEMA,planned_rule,checked_trial

RULE={'schema':'heptatrader.laya-promotion-rule.v1','slots':12,'slot_spacing_ms':360000,
 'horizon_ms':300000,'input_grace_ms':60000,'min_observed':10,'max_censored':2,
 'minimum_return_classes':2,'minimum_mean_brier_improvement':1e-6,
 'global_alpha_budget':.05,'alpha_spending':'0.05/(trial*(trial+1))',
 'bootstrap_hit_rate_not_worse':True,'canonical_owner_required':True,
 'output_scope':'SHADOW_QUALIFICATION_ONLY','trading_cutover_allowed':False}


def rule_for_trial(trial, protocol=None):
 if protocol is None:return dict(RULE)
 if protocol!=PROTOCOL:raise ContractError('UNKNOWN_PROMOTION_PROTOCOL')
 return planned_rule(RULE,trial)


def validate_plan_rule(plan):
 from .resident_price_baselines import validate_value_rule
 validate_value_rule(plan)
 rule=plan.get('rule')
 if rule==RULE:return rule
 if not isinstance(rule,dict) or rule.get('schema')!=SCHEMA:raise ContractError('PROMOTION_RULE_CHANGED')
 if rule!=planned_rule(RULE,plan.get('trial')):raise ContractError('PROMOTION_RULE_CHANGED')
 return rule


def assess(plan,results,now_ms):
 rule=validate_plan_rule(plan)
 if type(plan.get('trial'))is not int or plan['trial']<1:raise ContractError('PROMOTION_TRIAL')
 if len({r['slot'] for r in results})!=len(results):raise ContractError('DUPLICATE_EVALUATION_SLOT')
 if any(r.get('candidate_id')!=plan['candidate_id'] or not 0<=r['slot']<rule['slots'] for r in results):
  raise ContractError('EVALUATION_CANDIDATE_OR_SLOT')
 base={'candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],'rule_id':digest(rule),
       'automatic_execution_selection':False,'canonical_owner_admitted':False,'promotion_performed':False}
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
 improvements=[r['parent_brier']-r['candidate_brier'] for r in observed]
 if not all(math.isfinite(x) for x in improvements):raise ContractError('NONFINITE_EVALUATION_LOSS')
 mean=sum(improvements)/len(improvements) if improvements else None
 if mean is None or mean<=rule['minimum_mean_brier_improvement']:reasons.append('NO_POSITIVE_MEAN_IMPROVEMENT')
 non_ties=[x for x in improvements if abs(x)>1e-12];wins=sum(x>0 for x in non_ties);n=len(non_ties)
 p=sum(math.comb(n,k) for k in range(wins,n+1))/2**n if n else 1.0
 alpha=.05/(plan['trial']*(plan['trial']+1))
 if p>alpha:reasons.append('PAIRED_SIGN_EVIDENCE_INSUFFICIENT')
 if rule.get('schema')==SCHEMA:
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
