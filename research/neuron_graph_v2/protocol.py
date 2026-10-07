"""Immutable paired future experiments: missing arms/outcomes stay in denominator.

MID classification and original-command NET_USD are different target protocols.
This validator reports diagnostics; it never supplies owner acceptance/selection.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict
import math
from .contracts import ContractError,digest,identity,integer,number

@dataclass(frozen=True)
class Candidate:
 name:str
 model_sha256:str
 frozen_at_ms:int
 train_outcome_end_ms:int
 def validate(self):
  if type(self.name)is not str or not 1<=len(self.name)<=100:raise ContractError('ARM_NAME')
  identity(self.model_sha256,'candidate');integer(self.frozen_at_ms,'freeze',1);integer(self.train_outcome_end_ms,'train_outcome',1)
  if self.train_outcome_end_ms>self.frozen_at_ms:raise ContractError('TRAINING_NOT_MATURE_AT_FREEZE')

@dataclass(frozen=True)
class PairedPlan:
 period_id:str
 behavior_policy_sha256:str
 quantity_policy_sha256:str
 target:str
 arms:tuple[Candidate,...]
 registered_ms:int
 input_start_ms:int
 input_end_ms:int
 outcome_end_ms:int
 planned_cases:int
 def validate(self):
  if not self.period_id or self.target not in ('MID_RETURN_7_BINS','ACTUAL_CLOSED_NET_USD'):raise ContractError('TARGET_SCOPE')
  identity(self.behavior_policy_sha256,'behavior');identity(self.quantity_policy_sha256,'quantity')
  if type(self.arms)is not tuple or not 2<=len(self.arms)<=8 or len({x.name for x in self.arms})!=len(self.arms):raise ContractError('ARMS')
  for a in self.arms:a.validate()
  for n in ('registered_ms','input_start_ms','input_end_ms','outcome_end_ms','planned_cases'):integer(getattr(self,n),n,1)
  if not max(a.frozen_at_ms for a in self.arms)<=self.registered_ms<self.input_start_ms<self.input_end_ms<=self.outcome_end_ms:raise ContractError('WINDOW_CHRONOLOGY')
 def identity(self):self.validate();return digest(asdict(self))

@dataclass(frozen=True)
class Prediction:
 case_id:str
 input_sha256:str
 plan_sha256:str
 observed_input_ms:int
 committed_ms:int
 expected_outcome_ms:int
 candidate_hashes:tuple[tuple[str,str],...]
 values:tuple[tuple[str,tuple[float,...]],...]
 def validate(self,plan):
  identity(self.input_sha256,'input');identity(self.plan_sha256,'plan')
  if self.plan_sha256!=plan.identity():raise ContractError('PLAN_CHANGED')
  if type(self.case_id)is not str or not self.case_id:raise ContractError('CASE_ID')
  for k in ('observed_input_ms','committed_ms','expected_outcome_ms'):integer(getattr(self,k),k,1)
  if not plan.input_start_ms<=self.observed_input_ms<plan.input_end_ms:raise ContractError('INPUT_OUTSIDE_WINDOW')
  if not self.observed_input_ms<=self.committed_ms<self.expected_outcome_ms<=plan.outcome_end_ms:raise ContractError('LATE_OR_IMPOSSIBLE_COMMIT')
  expected=tuple(sorted((c.name,c.model_sha256) for c in plan.arms))
  if tuple(sorted(self.candidate_hashes))!=expected:raise ContractError('CANDIDATE_SUBSTITUTION')
  names=[n for n,_ in self.values]
  if len(names)!=len(set(names)) or set(names)!=set(n for n,_ in expected):raise ContractError('MISSING_PAIRED_ARM')
  for name,v in self.values:
   if type(v)is not tuple or len(v)!=(7 if plan.target=='MID_RETURN_7_BINS' else 1):raise ContractError('TARGET_DIMENSION')
   for x in v:number(x,'prediction')
   if len(v)==7 and (any(x<0 or x>1 for x in v) or abs(sum(v)-1)>1e-6):raise ContractError('PROBABILITY_VECTOR')

@dataclass(frozen=True)
class Outcome:
 case_id:str
 outcome_ms:int
 value:float|int|None
 evidence_sha256:str
 path_complete:bool
 actual_fees_complete:bool
 original_command_id:str|None=None
 actual_executed:bool=False

def summarize(plan,predictions,outcomes,now_ms):
 plan.validate();integer(now_ms,'now',1)
 if len(predictions)>plan.planned_cases:raise ContractError('UNREGISTERED_EXTRA_CASES')
 if len({p.case_id for p in predictions})!=len(predictions) or len({o.case_id for o in outcomes})!=len(outcomes):raise ContractError('DUPLICATE_CASE')
 if len({p.input_sha256 for p in predictions})!=len(predictions):raise ContractError('REPEATED_INPUT_NOT_NEW_SAMPLE')
 known={p.case_id for p in predictions}
 if any(o.case_id not in known for o in outcomes):raise ContractError('UNPAIRED_OUTCOME')
 if plan.target=='ACTUAL_CLOSED_NET_USD':
  commands=[o.original_command_id for o in outcomes if o.original_command_id]
  if len(commands)!=len(set(commands)):raise ContractError('DUPLICATE_ORIGINAL_COMMAND')
 by_case={o.case_id:o for o in outcomes};losses={a.name:[] for a in plan.arms};censored=[];pending=0
 for p in predictions:
  p.validate(plan);out=by_case.get(p.case_id)
  if now_ms<p.expected_outcome_ms:pending+=1;continue
  if out is None:
   censored.append({'case_id':p.case_id,'reason':'OUTCOME_NOT_OBSERVED'});continue
  integer(out.outcome_ms,'observed_outcome',1);identity(out.evidence_sha256,'outcome')
  if type(out.path_complete)is not bool or type(out.actual_fees_complete)is not bool or type(out.actual_executed)is not bool:raise ContractError('OUTCOME_FLAGS')
  if out.outcome_ms>now_ms:pending+=1;continue
  if not p.expected_outcome_ms<=out.outcome_ms<=plan.outcome_end_ms or not out.path_complete or out.value is None:
   censored.append({'case_id':p.case_id,'reason':'INCOMPLETE_PATH_OR_OUTCOME'});continue
  if plan.target=='ACTUAL_CLOSED_NET_USD':
   if not out.actual_executed or not out.original_command_id or not out.actual_fees_complete:
    censored.append({'case_id':p.case_id,'reason':'NO_ACTUAL_FEE_COMPLETE_ORIGINAL_COMMAND'});continue
   value=number(out.value,'net_usd')
   for name,v in p.values:
    error=abs(v[0]-value)
    if not math.isfinite(error):raise ContractError('NONFINITE_USD_ERROR')
    losses[name].append(error)
  else:
   if type(out.value)is not int or not 0<=out.value<7:raise ContractError('TARGET_CLASS')
   if out.outcome_ms>p.expected_outcome_ms+5000:
    censored.append({'case_id':p.case_id,'reason':'ENDPOINT_GAP'});continue
   for name,v in p.values:losses[name].append(sum((x-(i==out.value))**2 for i,x in enumerate(v)))
 observed=len(next(iter(losses.values())))
 return {'schema':'heptatrader.paired-diagnostic.v2','plan_id':plan.identity(),
  'state':'PENDING_FIXED_WINDOW' if now_ms<plan.outcome_end_ms else 'DESCRIPTIVE_ONLY_REQUIRES_INDEPENDENT_ACCEPTANCE',
  'planned':plan.planned_cases,'predicted':len(predictions),'unpredicted':plan.planned_cases-len(predictions),
  'observed':observed,'censored':len(censored),'pending':pending,'censor_records':censored,
  'loss':'BRIER' if plan.target=='MID_RETURN_7_BINS' else 'ABSOLUTE_NET_USD_ERROR',
  'mean_losses':{name:sum(v)/len(v) if v else None for name,v in losses.items()},
  'generalization_proven':False,'canonical_owner_accepted':False,'model_selected':False,'effect_authority':False}
