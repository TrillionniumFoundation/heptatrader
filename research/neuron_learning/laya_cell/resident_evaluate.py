"""Independent resident future evaluator and conditional promotion assessment.

The evaluator has no optimizer, no write path into model tensors and no execution
client. QUALIFIED is still not a canonical-owner admission or a trading cutover.
"""
from __future__ import annotations
import argparse,fcntl,json,math,os,re,signal,subprocess,sys,time
from bisect import bisect_left,bisect_right
from pathlib import Path
from .integrity import ContractError,read_json,digest,sha_file
from .resident_store import write_once,atomic
from .resident_data import read_quotes
from .dataset import BIN_EDGES_BPS
from .promotion import RULE,assess,validate_plan_rule
from . import resident_training_v2 as training_v2
from . import resident_price_baselines as price_refs
from . import resident_price_calibration as price_calibration
from . import resident_parent_calibration as parent_calibration

PREDICTION_PREWARM_MS=90000
PUBLICATION_PROFILE='FIXED_SLOT_DURABLE_PUBLICATION_V1'

_WORKER_EXCEPTION_TITLE=re.compile(
 r'^(?:[A-Za-z_][A-Za-z_0-9]*\.)*'
 r'(?:[A-Z][A-Za-z_0-9]*(?:Error|Exception)|Exception|'
 r'SystemExit|KeyboardInterrupt|GeneratorExit|StopIteration|StopAsyncIteration|TimeoutExpired)'
 r':(?:[ \t]|$)')


def worker_failure_diagnostic(stderr_tail):
 """Prefer the final Python exception title over following runtime advice."""
 lines=stderr_tail.strip().splitlines()
 # Keep indentation while matching so quoted/source examples remain ordinary logs.
 for line in reversed(stderr_tail.splitlines()):
  if _WORKER_EXCEPTION_TITLE.match(line):return line[:240]
 return lines[-1][:240] if lines else 'NO_PROCESS_DIAGNOSTIC'


def progress_with_coverage(plan,results,assessment):
 """Annotate a copy using already assessed outcomes, with no further file reads.

 These are observational counts, never an alternate qualification rule. Missing
 outcomes remain pending here even after the deadline; assess() independently
 keeps its original denominator and rejection rules. Unknown historical reasons
 stay verbatim, and absent provenance is never inferred from other slots.
 """
 rule=plan['rule'];observed=0;censored=0
 reasons={};dispositions={};denial_slots={};details={};phases={}
 reason_missing=0;disposition_missing=0;detail_missing=0;phase_missing=0
 first_denial_slots=set()
 def count_text(counts,value):
  if not isinstance(value,str) or not value:return False
  counts[value]=counts.get(value,0)+1
  return True
 for result in results:
  first=result.get('first_admission_denial_reasons')
  if isinstance(first,list):
   for reason in first:
    if isinstance(reason,str) and reason:
     denial_slots.setdefault(reason,set()).add(result['slot'])
     first_denial_slots.add(result['slot'])
  if result['status']=='OBSERVED':
   observed+=1;continue
  if result['status']!='CENSORED':continue
  censored+=1;reason=result.get('reason')
  reason_missing+=not count_text(reasons,reason)
  if reason=='NO_PREDICTION_IN_FIXED_SLOT':
   disposition_missing+=not count_text(dispositions,result.get('no_prediction_disposition'))
  elif reason=='PREDICTION_PROCESS_FAILED':
   detail_missing+=not count_text(details,result.get('detail'))
   phase=result.get('phase')
   if isinstance(phase,dict):phase=phase.get('phase')
   phase_missing+=not count_text(phases,phase)
 coverage={'schema':'heptatrader.laya-coverage-diagnostics.v1','observational_only':True,
  'frozen_rule':{key:rule.get(key) for key in ('slots','min_observed','min_non_ties','max_censored')},
  'counts':{'OBSERVED':observed,'CENSORED':censored,'pending':rule['slots']-len(results)},
  'pending_scope':'SLOTS_WITHOUT_RECORDED_OUTCOME_NO_CAUSE_ASSIGNED',
  'censor_budget':{'scope':'RECORDED_CENSORED_OUTCOMES_ONLY',
   'remaining':max(0,rule['max_censored']-censored),
   'excess':max(0,censored-rule['max_censored']),
   'maximum_possible_observed':rule['slots']-censored},
  'censored_reasons':dict(sorted(reasons.items())),
  'censored_reason_unrecorded':reason_missing,
  'no_prediction_dispositions':dict(sorted(dispositions.items())),
  'no_prediction_disposition_unrecorded':disposition_missing,
  'first_admission_denial_reasons':{reason:len(slots) for reason,slots in sorted(denial_slots.items())},
  'first_admission_denial_slots':len(first_denial_slots),
  'first_admission_denial_scope':'FIRST_DENIAL_RECORDED_IN_OUTCOME_PER_SLOT_NOT_EVERY_ATTEMPT_OR_COMPLETE_CAUSAL_ATTRIBUTION',
  'prediction_process_failed':{'details':dict(sorted(details.items())),
   'phases':dict(sorted(phases.items())),
   'detail_unrecorded':detail_missing,'phase_unrecorded':phase_missing}}
 return {**assessment,'coverage_diagnostics':coverage}


def summarize_closed_coverage(entries,now_ms):
 """Expose existing final coverage only; never reconstruct or assess outcomes."""
 summary={'observational_only':True,'model_performance_result':False,'authorization_effect':'NONE',
  'scope':'RECORDED_FINAL_PROGRESS_AFTER_ORIGINAL_COMPLETE_DEADLINE',
  'eligible_closed_plans':0,'closed_plans_without_verifiable_deadline':0,
  'plans_with_diagnostics':0,'plans_without_diagnostics':0,
  'counts_scope':'PLANS_WITH_COMPATIBLE_FINAL_PROGRESS_ONLY_HISTORY_NOT_RECONSTRUCTED',
  'counts':{'OBSERVED':0,'CENSORED':0,'pending':0},
  'pending_count_scope':'SLOTS_WITHOUT_RECORDED_OUTCOME_NOT_ACTIVE_TRIALS',
  'censored_reasons':{},'censored_reason_unrecorded':0,
  'no_prediction_dispositions':{},'no_prediction_disposition_unrecorded':0,
  'first_admission_denial_reasons':{},'first_admission_denial_slots':0,
  'first_admission_denial_scope':'FIRST_RECORDED_DENIAL_NOT_COMPLETE_CAUSAL_ATTRIBUTION',
  'prediction_process_failed':{'details':{},'phases':{},'detail_unrecorded':0,'phase_unrecorded':0}}
 fields=('counts','censored_reasons','censored_reason_unrecorded','no_prediction_dispositions',
  'no_prediction_disposition_unrecorded','first_admission_denial_reasons',
  'first_admission_denial_slots','prediction_process_failed')
 def add_counts(target,source,maximum=None):
  for key,value in source.items():
   if not isinstance(key,str) or not 0<len(key)<=240:raise ValueError('DIAGNOSTIC_COUNTER_KEY')
   if isinstance(value,dict):add_counts(target.setdefault(key,{}),value,maximum)
   elif type(value) is int and value>=0 and (maximum is None or value<=maximum):
    target[key]=target.get(key,0)+value
   else:raise ValueError('DIAGNOSTIC_COUNTER_VALUE')
 for plan,qualification,progress in entries:
  if qualification.get('state') not in ('REJECTED','QUALIFIED_AWAITING_CANONICAL_OWNER'):continue
  end=plan.get('outcome_end_ms')
  if type(end) is not int or end<=0:
   summary['closed_plans_without_verifiable_deadline']+=1;continue
  if now_ms<end:continue
  summary['eligible_closed_plans']+=1
  try:
   if (not isinstance(progress,dict) or any(progress.get(key)!=qualification.get(key)
       for key in ('plan_id','candidate_id','state'))
       or progress.get('plan_id')!=plan['plan_id']
       or progress.get('candidate_id')!=plan['candidate_id']):raise ValueError('STALE_FINAL_PROGRESS')
   coverage=progress['coverage_diagnostics'];counts=coverage['counts'];rule=plan['rule']
   if (coverage.get('schema')!='heptatrader.laya-coverage-diagnostics.v1'
       or coverage.get('observational_only') is not True
       or coverage.get('frozen_rule')!={key:rule.get(key) for key in ('slots','min_observed','min_non_ties','max_censored')}
       or set(counts)!={'OBSERVED','CENSORED','pending'}
       or counts['OBSERVED']!=qualification.get('observed')
       or counts['CENSORED']+counts['pending']!=qualification.get('censored')
       or sum(counts.values())!=rule['slots']):raise ValueError('FINAL_COVERAGE_COUNTS_CHANGED')
   projection={};add_counts(projection,{key:coverage[key] for key in fields},rule['slots'])
   failure=projection['prediction_process_failed']
   if set(failure)!={'details','phases','detail_unrecorded','phase_unrecorded'}:
    raise ValueError('FINAL_FAILURE_COUNTERS_CHANGED')
   for counters in (projection['censored_reasons'],projection['no_prediction_dispositions'],
       projection['first_admission_denial_reasons'],failure['details'],failure['phases']):
    if not isinstance(counters,dict) or any(type(value) is not int for value in counters.values()):
     raise ValueError('FINAL_DIAGNOSTIC_COUNTER_SHAPE')
   for value in (projection['censored_reason_unrecorded'],projection['no_prediction_disposition_unrecorded'],
       projection['first_admission_denial_slots'],failure['detail_unrecorded'],failure['phase_unrecorded']):
    if type(value) is not int:raise ValueError('FINAL_DIAGNOSTIC_COUNTER_SHAPE')
   if sum(projection['censored_reasons'].values())+projection['censored_reason_unrecorded']!=counts['CENSORED']:
    raise ValueError('FINAL_CENSOR_REASONS_INCOMPLETE')
  except (KeyError,TypeError,ValueError,AttributeError,RecursionError):
   summary['plans_without_diagnostics']+=1;continue
  summary['plans_with_diagnostics']+=1;add_counts(summary,projection)
 return summary


def _slot_order(path,plan,rule,now_ms):
 """Account known misses, dispatch due work, then read mature source archives."""
 def priority(slot):
  folder=path.parent
  if (folder/f'outcome-{slot:03}.json').exists():return (4,slot)
  if (folder/f'prediction-{slot:03}.json').exists():return (2,slot)
  start=plan['start_ms']+slot*rule['slot_spacing_ms'];end=start+rule['input_grace_ms']
  if (folder/f'failure-{slot:03}.json').exists() or now_ms>end:return (0,slot)
  return (1 if start-PREDICTION_PREWARM_MS<=now_ms<=end else 3,slot)
 return sorted(range(rule['slots']),key=priority)


def _plan_dispatch_priority(path,plan,rule,now_ms,declared,cycle_records):
 """Priority is a read-only hint; ordinary validation and admission still run."""
 pins=plan.get('evaluation_runtime_sha256')
 if ((path.parent/'qualification.json').exists()
     or (declared and isinstance(pins,dict) and any(declared.get(k)!=v for k,v in pins.items()))):
  return (1,0,str(path))
 for slot in _slot_order(path,plan,rule,now_ms):
  start=plan['start_ms']+slot*rule['slot_spacing_ms'];end=start+rule['input_grace_ms']
  if (start-PREDICTION_PREWARM_MS<=now_ms<=end
      and not any((path.parent/f'{kind}-{slot:03}.json').exists()
                  for kind in ('outcome','prediction','failure'))):
   records=[read_json(p) for p in sorted(path.parent.glob('outcome-*.json'))]
   cycle_records[path]=records
   if suppress_new_work_for_frozen_failure(plan,records):return (1,0,str(path))
   return (0,end,str(path))
 return (1,0,str(path))


def suppress_new_work_for_frozen_failure(plan, results):
 """Scheduling only: immutable failed slots can never be made successful.

 Keep the original trial open until its registered deadline and continue
 recording real outcomes; never relax thresholds, promote, or rewrite history.
 """
 rule=validate_plan_rule(plan)
 seen=set();censored=0
 for result in results:
  slot=result.get('slot')
  if (type(slot) is not int or not 0<=slot<rule['slots'] or slot in seen
      or result.get('candidate_id')!=plan['candidate_id']
      or result.get('plan_id')!=plan['plan_id']
      or result.get('status') not in ('OBSERVED','CENSORED')):
   raise ContractError('FUTILITY_OUTCOME_IDENTITY_OR_STATUS')
  seen.add(slot);censored+=result['status']=='CENSORED'
 return censored>rule['max_censored'] or rule['slots']-censored<rule['min_observed']


def _futility_suppression_receipt(path,plan,slot,*,at_ms=None):
 """Bind a real in-window suppression to already immutable futile outcomes.

 Read mode never invents a missing event. An earlier real admission denial
 remains separate evidence: no admission was attempted at this suppression.
 """
 path=Path(path);rule=validate_plan_rule(plan)
 if (plan.get('plan_id')!=digest({k:v for k,v in plan.items() if k!='plan_id'})
     or type(slot) is not int or not 0<=slot<rule['slots']):
  raise ContractError('FUTILITY_SUPPRESSION_IDENTITY')
 receipt=path.parent/f'futility-suppression-{slot:03}.json'
 create=not receipt.exists()
 if create and at_ms is None:return None
 if create:
  files=sorted(path.parent.glob('outcome-*.json'))
  records=[read_json(p) for p in files]
  if not suppress_new_work_for_frozen_failure(plan,records):
   raise ContractError('FUTILITY_SUPPRESSION_NOT_IRREVERSIBLE')
  basis=[]
  needed_censors=min(rule['max_censored']+1,rule['slots']-rule['min_observed']+1)
  for source,record in zip(files,records):
   if source.name!=f"outcome-{record['slot']:03}.json":
    raise ContractError('FUTILITY_SUPPRESSION_BASIS_PATH')
   if (record['status']=='CENSORED' and len(basis)<needed_censors
       and record.get('no_prediction_disposition')!='FUTILITY_SUPPRESSED'):
    basis.append({'slot':record['slot'],'sha256':sha_file(source)})
  value={'schema':'heptatrader.laya-futility-suppression.v1',
   'plan_id':plan['plan_id'],'candidate_id':plan['candidate_id'],'slot':slot,
   'at_ms':at_ms,'basis_outcomes':basis,
   'resource_admission_attempted_at_suppression':False}
  value['receipt_id']=digest(value)
 else:value=read_json(receipt)
 start=plan['start_ms']+slot*rule['slot_spacing_ms']
 if (not isinstance(value,dict)
     or value.get('schema')!='heptatrader.laya-futility-suppression.v1'
     or value.get('plan_id')!=plan['plan_id'] or value.get('candidate_id')!=plan['candidate_id']
     or value.get('slot')!=slot or type(value.get('slot')) is not int
     or type(value.get('at_ms')) is not int
     or not start-90000<=value['at_ms']<=start+rule['input_grace_ms']
     or value.get('resource_admission_attempted_at_suppression') is not False
     or value.get('receipt_id')!=digest({k:v for k,v in value.items() if k!='receipt_id'})):
  raise ContractError('FUTILITY_SUPPRESSION_RECEIPT_CHANGED')
 basis=value.get('basis_outcomes')
 if not isinstance(basis,list) or not 0<len(basis)<=rule['slots']:
  raise ContractError('FUTILITY_SUPPRESSION_BASIS_CHANGED')
 records=[]
 for entry in basis:
  if (not isinstance(entry,dict) or set(entry)!={'slot','sha256'}
      or type(entry['slot']) is not int or not 0<=entry['slot']<slot):
   raise ContractError('FUTILITY_SUPPRESSION_BASIS_CHANGED')
  source=path.parent/f"outcome-{entry['slot']:03}.json"
  if not source.exists() or sha_file(source)!=entry['sha256']:
   raise ContractError('FUTILITY_SUPPRESSION_BASIS_CHANGED')
  record=read_json(source)
  if (record.get('slot')!=entry['slot'] or record.get('status')!='CENSORED'
      or record.get('no_prediction_disposition')=='FUTILITY_SUPPRESSED'
      or type(record.get('evaluated_at_ms')) is not int
      or record['evaluated_at_ms']>value['at_ms']):
   raise ContractError('FUTILITY_SUPPRESSION_BASIS_CHANGED')
  records.append(record)
 if not suppress_new_work_for_frozen_failure(plan,records):
  raise ContractError('FUTILITY_SUPPRESSION_NOT_IRREVERSIBLE')
 if create:write_once(receipt,value)
 return {'sha256':sha_file(receipt),'at_ms':value['at_ms']}


def measure(plan,slot,prediction,quotes,now_ms,*,committed_input=None,publication_record=None):
 if prediction.get('candidate_id')!=plan['candidate_id'] or prediction.get('plan_id')!=plan['plan_id'] or prediction.get('slot')!=slot:
  raise ContractError('PREDICTION_IDENTITY')
 if prediction.get('input_id')!=digest(prediction.get('input')):raise ContractError('PREDICTION_INPUT_HASH')
 from .resident_price_input import verify_prediction as verify_price_input
 verify_price_input(plan,prediction)
 if plan.get('price_input_objective') is not None:
  from .resident_price_input import verify_committed_input
  if committed_input is None:raise ContractError('V7_COMMITTED_PRICE_INPUT_REQUIRED')
  verify_committed_input(plan,committed_input,prediction)
 calibrated=price_calibration.verify_prediction(plan,prediction)
 parent_calibration.verify_prediction(plan,prediction)
 anchor=prediction['anchor_ms'];due=anchor+RULE['horizon_ms'];times=[q['t'] for q in quotes]
 available=prediction['created_at_ms'];publication_missing=False
 if plan.get('prediction_publication_profile') is not None:
  if (plan['prediction_publication_profile']!=PUBLICATION_PROFILE
      or prediction.get('prediction_publication_profile')!=PUBLICATION_PROFILE):
   raise ContractError('PREDICTION_PUBLICATION_PROFILE_CHANGED')
  slot_start=plan['start_ms']+slot*plan['rule']['slot_spacing_ms']
  if not slot_start<=anchor<=slot_start+plan['rule']['input_grace_ms']:
   raise ContractError('PREDICTION_FIXED_INPUT_WINDOW_CHANGED')
  if publication_record is None:
   if now_ms<=due+15000:return None
   publication_missing=True
  else:
   if (not isinstance(publication_record,dict) or publication_record.get('profile')!=PUBLICATION_PROFILE
       or publication_record.get('plan_id')!=plan['plan_id'] or publication_record.get('slot')!=slot
       or publication_record.get('prediction_digest')!=digest(prediction)
       or type(publication_record.get('durably_published_at_ms')) is not int
       or publication_record['durably_published_at_ms']<available):
    raise ContractError('PREDICTION_PUBLICATION_RECEIPT_CHANGED')
   available=publication_record['durably_published_at_ms']
 a=bisect_left(times,anchor);b=bisect_left(times,due)
 if b==len(times) and now_ms<=due+15000:return None
 reason=None
 if publication_missing:reason='PREDICTION_PUBLICATION_TIME_UNAVAILABLE'
 elif available>=due or prediction['trained_until_ms']>anchor:reason='LOOKAHEAD'
 elif a>=len(times) or times[a]!=anchor or quotes[a]['mid']!=prediction['reference_mid']:reason='ANCHOR_MISSING_OR_CHANGED'
 elif b>=len(times):reason='OUTCOME_MISSING'
 elif times[b]>due+5000:reason='ENDPOINT_GAP'
 elif any(y-x>30000 for x,y in zip(times[a:b],times[a+1:b+1])):reason='PATH_GAP'
 base={'candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],'slot':slot,'anchor_ms':anchor,
       'prediction_available_ms':available,'trained_until_ms':prediction['trained_until_ms'],
       'evaluated_at_ms':now_ms,'broker_orders_sent':0,'metric_scope':'PREDICTION_NOT_EXECUTION_PNL'}
 if reason:return {**base,'status':'CENSORED','reason':reason}
 value=(quotes[b]['mid']/quotes[a]['mid']-1)*10000;target=bisect_right(BIN_EDGES_BPS,value)
 def score(probs):
  if len(probs)!=7 or any(type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=1 for x in probs) or abs(sum(probs)-1)>1e-5:
   raise ContractError('PROBABILITY_VECTOR')
  return sum((p-(i==target))**2 for i,p in enumerate(probs))
 p=prediction['candidate_probs'];parent=prediction['parent_probs'];act=prediction['candidate_act_escalate_probs']
 parent_correct=max(range(7),key=lambda i:parent[i])==target
 action_target=0 if parent_correct else 1
 action_profile=plan.get('action_output_profile')
 if action_profile is None:
  if len(act)!=2 or any(not math.isfinite(x) for x in act):raise ContractError('ACT_ESCALATE_VECTOR')
  action_brier=sum((v-(i==action_target))**2 for i,v in enumerate(act))
 elif action_profile==training_v2.ACTION_OUTPUT:
  action_brier=training_v2.action_observation(plan,prediction,parent_correct=parent_correct)['action_brier']
 else:raise ContractError('ACTION_OUTPUT_PROFILE_CHANGED')
 comparison=parent_calibration.measure(plan,prediction,target)
 if calibrated is not None:
  comparison.update(price_calibration_profile_id=calibrated['profile_id'],
   raw_child_price_scores=price_refs.probability_metrics(prediction['candidate_raw_probs'],target))
 if plan.get('price_value_rule') is not None:
  price_refs.validate_value_rule(plan)
  if prediction.get('horizon_sec')!=300 or prediction.get('input',{}).get('horizon_sec')!=300:raise ContractError('PRICE_VALUE_PRIMARY_HORIZON')
  comparison['horizon_sec']=300
 if plan.get('price_baselines') is not None:
  comparison.update(price_baseline_profile_id=plan['price_baselines']['profile_id'],
   same_window_price_scores=price_refs.measure(plan['price_baselines'],prediction,target))
 return {**base,**comparison,'status':'OBSERVED','path_gap':False,'reason':None,'return_bps':value,'target_class':target,
  'candidate_brier':score(p),'parent_brier':score(parent),'candidate_hit':max(range(7),key=lambda i:p[i])==target,
  'bootstrap_hit':bisect_right(BIN_EDGES_BPS,prediction['bootstrap_point_bps'])==target,
  'action_head_brier':action_brier,
  'outcome_receipt':quotes[b]['receipt'],'outcome_ms':times[b]}


class Evaluator:
 def __init__(self,config_path):
  self.path=Path(config_path);self.c=read_json(self.path);self.root=Path(self.c['evaluation_directory'])
  from .resident import check_owner_handoff_runtime
  check_owner_handoff_runtime(self.c)
  self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
  self.stop=False;self.tasks={};self.last_log_ms=0;self._retained_closeout_cache={}
 def closed_progress(self,path,plan,now_ms):
  end=plan.get('outcome_end_ms')
  if type(end) is not int or end<=0 or now_ms<end:return None
  if not hasattr(self,'_closed_progress_cache'):self._closed_progress_cache={}
  progress=path.parent/'progress.json'
  try:
   metadata=progress.stat();token=(metadata.st_dev,metadata.st_ino,metadata.st_size,metadata.st_mtime_ns,metadata.st_ctime_ns)
  except OSError:return None
  cached=self._closed_progress_cache.get(progress)
  if cached is not None and cached[0]==token:return cached[1]
  try:value=read_json(progress,65536)
  except (OSError,ValueError,RecursionError):value=None
  self._closed_progress_cache[progress]=(token,value)
  return value
 def operator_stop_binding(self,path,plan,now):
  from .resident_trial_stop import read_stop
  return read_stop(self.c,path,plan,now_ms=now)
 def signal(self,*_):
  self.stop=True
  for p,stream,started in self.tasks.values():
   if p.poll() is None:
    try:p.terminate()
    except ProcessLookupError:pass
 def stop_workers(self):
  from .resident import terminate_worker
  self.stop=True
  for p,stream,started in list(self.tasks.values()):
   terminate_worker(p)
  self.reap()
  return not self.tasks
 def record_admission_deferral(self,path,slot,reasons):
  """Preserve first actual admission refusal, NOT a terminal prediction failure.

  Later attempts may still succeed within the frozen input window.
  """
  path=Path(path);plan=read_json(path)
  if (type(slot) is not int or not 0<=slot<validate_plan_rule(plan)['slots']
      or plan.get('plan_id')!=digest({k:v for k,v in plan.items() if k!='plan_id'})
      or type(reasons) is not list or not reasons or len(reasons)>32
      or any(type(reason) is not str or not reason for reason in reasons)):
   raise ContractError('PREWARM_DENIAL_IDENTITY_OR_REASONS')
  # A genuine OS/source error can contain a long path. Its occurrence must
  # not crash the evaluator just because the human diagnostic was verbose.
  # Bound the private evidence without changing the guard's refusal.
  reason_codes=[reason[:160] for reason in reasons]
  receipt=path.parent/f'admission-denial-{slot:03}.json'
  if receipt.exists():
   old=read_json(receipt)
   if (old.get('schema')!='heptatrader.laya-evaluator-first-admission-denial.v1'
       or old.get('plan_id')!=plan['plan_id']
       or old.get('candidate_id')!=plan['candidate_id']
       or old.get('slot')!=slot or old.get('automatic_trading') is not False
       or old.get('first_denial_only') is not True):
    raise ContractError('PREWARM_DENIAL_RECEIPT_CHANGED')
   return
  write_once(receipt,{
   'schema':'heptatrader.laya-evaluator-first-admission-denial.v1',
   'plan_id':plan['plan_id'],'candidate_id':plan['candidate_id'],
   'slot':slot,'at_ms':int(time.time()*1000),
   'reason_codes':reason_codes,'reason_truncated':any(len(reason)>160 for reason in reasons),'first_denial_only':True,
   'possible_retry_until_frozen_input_end':True,
   'not_proof_of_every_failed_attempt':True,
   'automatic_trading':False,'broker_orders_sent':0,'model_promoted':False})
 def launch(self,path,slot):
  from .resident import inherited_worker_locks
  plan=read_json(path);rule=validate_plan_rule(plan)
  if plan.get('plan_id')!=digest({k:v for k,v in plan.items() if k!='plan_id'}):
   raise ContractError('REGISTERED_PLAN_CHANGED')
  if type(slot) is not int or not 0<=slot<rule['slots']:raise ContractError('SLOT_BOUND')
  start=plan['start_ms']+slot*rule['slot_spacing_ms'];now=int(time.time()*1000)
  if now<start-PREDICTION_PREWARM_MS:return False
  if now>start+rule['input_grace_ms']:
   self.record_admission_deferral(path,slot,['INPUT_SLOT_EXPIRED_BEFORE_WORKER_DISPATCH'])
   return False
  key=(str(path),slot)
  stream=(path.parent/f'prediction-{slot:03}.log').open('a')
  inherited=inherited_worker_locks(self)
  lock_args=[part for fd in inherited for part in ('--held-lock-fd',str(fd))]
  from .resident_hip_runtime import worker_environment
  try:p=subprocess.Popen([self.c['python'],'-B','-m','laya_cell.resident_launch','predict','--config',str(self.path),'--plan',str(path),'--slot',str(slot),*lock_args],stdout=stream,stderr=stream,pass_fds=inherited,env=worker_environment(self.c))
  except BaseException:stream.close();raise
  self.tasks[key]=(p,stream,time.monotonic())
  return True
 def reap(self):
  for key,(p,stream,started) in list(self.tasks.items()):
   expired=time.monotonic()-started>240
   if p.poll() is None and not expired:continue
   if p.poll() is None:
    try:p.terminate()
    except ProcessLookupError:pass
   try:rc=p.wait(timeout=5)
   except subprocess.TimeoutExpired:
    try:p.kill()
    except ProcessLookupError:pass
    rc=p.wait(timeout=5)
   stream.close();del self.tasks[key]
   path=Path(key[0]);slot=key[1]
   if (path.parent/f'prediction-{slot:03}.json').exists():continue
   phase_path=path.parent/f'phase-{slot:03}.json'
   phase=read_json(phase_path) if phase_path.exists() else {'phase':'STARTUP'}
   with (path.parent/f'prediction-{slot:03}.log').open('rb') as diagnostic_stream:
    diagnostic_stream.seek(0,os.SEEK_END)
    diagnostic_stream.seek(max(0,diagnostic_stream.tell()-1500))
    stderr_tail=diagnostic_stream.read(1500).decode('utf-8',errors='replace')
   reason=worker_failure_diagnostic(stderr_tail)
   if expired:reason='WORKER_BUDGET_EXCEEDED'
   elif rc<0:
    try:signal_name=signal.Signals(-rc).name
    except ValueError:signal_name=str(-rc)
    reason='WORKER_SIGNAL_'+signal_name
   failure={'schema':'heptatrader.laya-worker-failure.v1','slot':slot,'at_ms':int(time.time()*1000),
    'exit_code':rc,'reason':reason,'phase':phase,'predicted':False,'stderr_tail':stderr_tail}
   write_once(path.parent/f'failure-{slot:03}.json',failure)
 def step(self):
  self.reap();now=int(time.time()*1000)
  if not hasattr(self,'_retained_closeout_cache'):self._retained_closeout_cache={}
  if sha_file(Path(self.c['period_path']))!=self.c['period_sha256']:raise ContractError('EVAL_PERIOD_CHANGED')
  # A source scan is only needed for a genuinely mature prediction.
  # Never spend a full archive decode while every active trial is already lost.
  quotes=None;prefixes=None;quote_clock=None
  _outcome_source_snapshots={}
  plans=sorted(self.root.glob('*/plan.json'))
  if len(plans)>512:raise ContractError('EVALUATION_CAPACITY_NO_PRUNING')
  prepared=[];cycle_records={}
  for path in plans:
   plan=read_json(path)
   if plan.get('plan_id')!=digest({k:v for k,v in plan.items() if k!='plan_id'}):
    raise ContractError('REGISTERED_PLAN_CHANGED')
   rule=validate_plan_rule(plan)
   if plan.get('scope')!=self.c['period_sha256']:raise ContractError('EVALUATION_PERIOD_SCOPE')
   prepared.append((path,plan,rule))
  prepared.sort(key=lambda entry:_plan_dispatch_priority(*entry,now,self.c.get('runtime_sha256'),cycle_records))
  counts={};observed=0;censored=0;worker_errors={};futile_pending=0;launches_suppressed=0;retained_plans=[];operator_stops=[];source_route_holds=[];qualified_owner_plans=[];closed_coverage=[]
  for path,plan,rule in prepared:
   # A closed v1 plan remains bound to its original result, never re-evaluated
   # against replacement source bytes or re-registered as a new experiment.
   if (path.parent/'qualification.json').exists():
    q=read_json(path.parent/'qualification.json')
    if (not isinstance(q,dict) or q.get('candidate_id')!=plan['candidate_id']
        or q.get('plan_id')!=plan['plan_id']
        or q.get('state') not in ('REJECTED','QUALIFIED_AWAITING_CANONICAL_OWNER')):
     raise ContractError('CLOSED_QUALIFICATION_IDENTITY')
    counts[q['state']]=counts.get(q['state'],0)+1
    observed+=q.get('observed',0);censored+=q.get('censored',0)
    closed_coverage.append((plan,q,self.closed_progress(path,plan,now)))
    stopped=self.operator_stop_binding(path,plan,now)
    if stopped:operator_stops.append({'plan_id':plan['plan_id'],'receipt_sha256':stopped['sha256'],
     'performance_assessment':False,'new_heavy_workers_allowed':False})
    if q['state']=='QUALIFIED_AWAITING_CANONICAL_OWNER' and not stopped:qualified_owner_plans.append((path,plan['plan_id']))
    continue
   stopped=self.operator_stop_binding(path,plan,now)
   if stopped:operator_stops.append({'plan_id':plan['plan_id'],'receipt_sha256':stopped['sha256'],
    'performance_assessment':False,'new_heavy_workers_allowed':False})
   from .resident_retained_closeout import run_retained
   retained=run_retained(self.c,path,plan,self._retained_closeout_cache,now,operator_stop=stopped)
   if retained is not None:
    for state,count in retained['counts'].items():counts[state]=counts.get(state,0)+count
    observed+=retained['observed'];censored+=retained['censored']
    futile_pending+=retained.get('futile_pending_plans',0)
    launches_suppressed+=retained.get('new_prediction_launches_suppressed',0)
    retained_plans.append({'plan_id':plan['plan_id'],'original_runtime_executed':True,
     'new_heavy_workers_allowed':False,'last_closeout_at_ms':retained['at_ms']})
    continue
   pins=plan.get('evaluation_runtime_sha256');runtime=Path(self.c.get('runtime_directory',''))
   declared=self.c.get('runtime_sha256')
   if (self.c.get('retained_futile_plan_closeouts') and isinstance(pins,dict) and pins
       and isinstance(declared,dict) and declared
       and all(isinstance(k,str) and isinstance(v,str) and len(v)==64 for k,v in pins.items())
       and any(declared.get(k)!=v for k,v in pins.items())):
    # An omitted old-version route must not turn an upgrade into rejection.
    # Keep other current plans schedulable while retaining the original ledger.
    records=[read_json(p) for p in sorted(path.parent.glob('outcome-*.json'))]
    if suppress_new_work_for_frozen_failure(plan,records):futile_pending+=1
    observed+=sum(r['status']=='OBSERVED' for r in records)
    censored+=sum(r['status']=='CENSORED' for r in records)
    counts['PENDING']=counts.get('PENDING',0)+1
    source_route_holds.append({'plan_id':plan['plan_id'],'candidate_id':plan['candidate_id'],
     'reason':'UNROUTED_RETAINED_EVALUATOR_SOURCE'})
    continue
   source_ok=isinstance(pins,dict) and bool(pins) and all(v and sha_file(runtime/k)==v for k,v in pins.items())
   if not source_ok:
    decision={'state':'REJECTED','candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],
      'reasons':['EVALUATOR_SOURCE_UNPINNED_OR_CHANGED'],'promotion_performed':False,
      'canonical_owner_admitted':False,'automatic_execution_selection':False}
    if not (path.parent/'qualification.json').exists():write_once(path.parent/'qualification.json',decision)
    counts['REJECTED']=counts.get('REJECTED',0)+1;continue
   recorded=cycle_records.get(path)
   if recorded is None:recorded=[read_json(p) for p in sorted(path.parent.glob('outcome-*.json'))]
   futile=suppress_new_work_for_frozen_failure(plan,recorded)
   if futile:futile_pending+=1
   for slot in _slot_order(path,plan,rule,now):
    output=path.parent/f'outcome-{slot:03}.json';prediction=path.parent/f'prediction-{slot:03}.json'
    if output.exists():continue
    start=plan['start_ms']+slot*rule['slot_spacing_ms'];end=start+rule['input_grace_ms']
    if not prediction.exists():
     key=(str(path),slot);failure=path.parent/f'failure-{slot:03}.json'
     if key in self.tasks:continue
     if failure.exists():
      failed=read_json(failure);reason=failed['reason'];worker_errors[reason]=worker_errors.get(reason,0)+1
      record={'candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],'slot':slot,'status':'CENSORED',
       'reason':'PREDICTION_PROCESS_FAILED','detail':reason,'phase':failed['phase'],'evaluated_at_ms':now}
      write_once(output,record);recorded.append(record);continue
     if now>end:
      first=path.parent/f'admission-denial-{slot:03}.json'
      provenance={}
      if first.exists():
       denial=read_json(first)
       if (denial.get('schema')!='heptatrader.laya-evaluator-first-admission-denial.v1'
           or denial.get('plan_id')!=plan['plan_id']
           or denial.get('candidate_id')!=plan['candidate_id']
           or denial.get('slot')!=slot
           or denial.get('automatic_trading') is not False):
        raise ContractError('PREWARM_DENIAL_OUTCOME_IDENTITY')
       provenance={'first_admission_denial_sha256':sha_file(first),
        'first_admission_denial_reasons':denial['reason_codes'],
        'not_proof_of_every_denial_attempt':True}
      suppression=_futility_suppression_receipt(path,plan,slot)
      if suppression is not None:
       provenance.update(no_prediction_disposition='FUTILITY_SUPPRESSED',
        futility_suppression_sha256=suppression['sha256'],
        futility_suppressed_at_ms=suppression['at_ms'])
      else:
       provenance['no_prediction_disposition']=('ADMISSION_DENIAL_OBSERVED' if first.exists()
        else 'NO_LOCAL_ADMISSION_OR_FUTILITY_RECEIPT')
      record={'candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],'slot':slot,'status':'CENSORED',
       'reason':'NO_PREDICTION_IN_FIXED_SLOT','evaluated_at_ms':now,**provenance}
      write_once(output,record);recorded.append(record);continue
     # Cold model preparation occurs before, not inside, the fixed input slot.
     if now<start-PREDICTION_PREWARM_MS or len(self.tasks)>=2:continue
     # An expired or failed earlier slot can become irrevocably censored
     # during THIS step. Recompute immediately before GPU admission.
     if not futile:
      futile=suppress_new_work_for_frozen_failure(plan,recorded)
      if futile:futile_pending+=1
     if futile:
      suppressed_at_ms=int(time.time()*1000)
      if start-PREDICTION_PREWARM_MS<=suppressed_at_ms<=end:
       _futility_suppression_receipt(path,plan,slot,at_ms=suppressed_at_ms)
       launches_suppressed+=1
      continue
     dispatch_now=int(time.time()*1000)
     if start-PREDICTION_PREWARM_MS<=dispatch_now<=end:self.launch(path,slot)
     continue
    pred=read_json(prediction)
    if now<pred['anchor_ms']+rule['horizon_ms']:continue
    # A snapshot collected before a long GPU call must never decide maturity.
    from .resident_source_policy import PENDING_OUTCOMES,read_for_pending_outcomes,validate_pending_prediction
    _read_profile=plan.get('future_outcome_read_profile')
    if _read_profile is None:
     _source_key=('full',)
    elif _read_profile==PENDING_OUTCOMES:
     validate_pending_prediction(plan,pred,slot)
     _source_key=('plan',plan['plan_id'])
    else:
     raise ContractError('UNKNOWN_PENDING_OUTCOME_SOURCE_PROFILE')
    _cached_source=_outcome_source_snapshots.get(_source_key)
    if (_cached_source is None or (_read_profile is not None and _cached_source[3].get(slot)!=pred['anchor_ms'])):
     quote_clock=int(time.time()*1000)
     if _read_profile is None:
      quotes,prefixes=read_quotes(self.c,quote_clock)
      _pending_anchors={}
     else:
      _pending_predictions=[pred]
      _pending_anchors={slot:pred['anchor_ms']} if pred['anchor_ms']+rule['horizon_ms']<=quote_clock else {}
      for _pending_slot in range(rule['slots']):
       if _pending_slot==slot:continue
       _pending_prediction_path=path.parent / f'prediction-{_pending_slot:03}.json'
       _pending_outcome_path=path.parent / f'outcome-{_pending_slot:03}.json'
       if _pending_outcome_path.exists() or not _pending_prediction_path.exists():continue
       _pending_prediction=read_json(_pending_prediction_path)
       validate_pending_prediction(plan,_pending_prediction,_pending_slot)
       _pending_predictions.append(_pending_prediction)
       _pending_anchor=_pending_prediction.get('anchor_ms')
       if type(_pending_anchor) is int and _pending_anchor+rule['horizon_ms']<=quote_clock:
        _pending_anchors[_pending_slot]=_pending_anchor
      quotes,prefixes,_actual_source_profile=read_for_pending_outcomes(self.c,plan,quote_clock,_pending_predictions)
     _outcome_source_snapshots[_source_key]=(quotes,prefixes,quote_clock,_pending_anchors)
    quotes,prefixes,quote_clock,_cached_pending_anchors=_outcome_source_snapshots[_source_key]
    measurement_options={}
    if plan.get('prediction_publication_profile') is not None:
     phase_path=path.parent/f'phase-{slot:03}.json'
     phase=read_json(phase_path) if phase_path.exists() else {}
     measurement_options['publication_record']=phase.get('publication')
    if plan.get('price_input_objective') is not None:
     committed_path=path.parent/f'input-{slot:03}.json'
     if (not committed_path.exists() or sha_file(committed_path)!=pred.get('input_receipt_sha256')):
      raise ContractError('V7_COMMITTED_PRICE_INPUT_RECEIPT_HASH')
     measurement_options['committed_input']=read_json(committed_path)
    result=measure(plan,slot,pred,quotes,quote_clock,**measurement_options)
    if result is not None:
     result['source_prefixes']=prefixes;result['prediction_file_sha256']=sha_file(prediction)
     write_once(output,result);recorded.append(result)
   assessed_at=int(time.time()*1000)
   # The controller is the sole outcome writer. Reuse its same-round records
   # for pending progress, but reread at the terminal qualification boundary.
   results=(sorted(recorded,key=lambda r:r['slot']) if assessed_at<plan['outcome_end_ms'] else
     [read_json(p) for p in sorted(path.parent.glob('outcome-*.json'))])
   observed+=sum(r['status']=='OBSERVED' for r in results);censored+=sum(r['status']=='CENSORED' for r in results)
   assessment=assess(plan,results,assessed_at)
   if plan.get('price_baselines') is not None:assessment['same_window_price_baselines']=price_refs.summarize(results)
   if assessment['state'] != 'PENDING':
    write_once(path.parent / 'qualification.json', assessment)
    if assessment['state']=='QUALIFIED_AWAITING_CANONICAL_OWNER' and not stopped:qualified_owner_plans.append((path,plan['plan_id']))
   progress=progress_with_coverage(plan,results,assessment)
   atomic(path.parent / 'progress.json', progress)
   if assessment['state']!='PENDING':closed_coverage.append((plan,assessment,progress))
   counts[assessment['state']]=counts.get(assessment['state'],0)+1
  # Ordinary prediction dispatch takes priority. Publication is CPU/native I/O,
  # never a GPU worker, and its outbox deduplicates each plan/enrollment attempt.
  owner_handoffs=[];allow_new_handoff=True
  if qualified_owner_plans:
   from .resident_owner import handoff
   for qualified_path,qualified_plan_id in qualified_owner_plans:
    try:
     handoff_result=handoff(self.c,qualified_path,allow_new=allow_new_handoff)
     owner_handoffs.append(handoff_result)
     if handoff_result.get('attempt_started'):allow_new_handoff=False
    except (ContractError,OSError,KeyError,TypeError,ValueError) as exc:
     allow_new_handoff=False
     owner_handoffs.append({'plan_id':qualified_plan_id,'state':'CANONICAL_OWNER_HANDOFF_BLOCKED',
      'reason':str(exc)[:240],'canonical_agentd_accepted':False,'product_consumed':False,'effect_authority':False})
  status={'schema':'heptatrader.laya-resident-evaluation-status.v2','at_ms':int(time.time()*1000),
   'plans':len(plans),'counts':counts,'observed':observed,'censored':censored,'active_prediction_workers':len(self.tasks),
   'prediction_prewarm_ms':PREDICTION_PREWARM_MS,'worker_failure_details':worker_errors,
   'latest_quote_ms':quotes[-1]['t'] if quotes else None,
   'latest_quote_scope':'OUTCOME_SOURCE_SNAPSHOT_NOT_GLOBAL_SOURCE_HEALTH',
   'source_scan_performed':quotes is not None,'futile_pending_plans':futile_pending,
   'retained_futile_plan_closeouts':retained_plans,
   'unrouted_retained_plan_holds':source_route_holds,
   'operator_stopped_trials':len(operator_stops),'operator_stopped_plans':operator_stops,
   'new_prediction_launches_suppressed':launches_suppressed,'statistical_rule':RULE if not self.c.get('promotion_protocol') else None,
   'promotion_protocol':self.c.get('promotion_protocol','LEGACY_V1'),'counts_include_closed_plans':True,
   'closed_coverage_diagnostics':summarize_closed_coverage(closed_coverage,int(time.time()*1000)),
   'canonical_owner_handoffs':owner_handoffs,
   'canonical_owner_connection_scope':'SUCCESSFUL_PUBLICATION_RECEIPT_NOT_SERVICE_LIVENESS',
   'automatic_trading':False,'canonical_owner_connected':any(
    row['state']=='CANONICAL_MODEL_PUBLISHED_AWAITING_INDEPENDENT_SELECTION' for row in owner_handoffs),
   'promotion_performed':False,'broker_orders_sent':0}
  atomic(self.root/'status.json',status)
  if now-self.last_log_ms>=30000:print(json.dumps(status),flush=True);self.last_log_ms=now
  return status

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();os.umask(0o077)
 service=Evaluator(a.config);lock=os.open(service.root/'evaluator.lock',os.O_WRONLY|os.O_CREAT|os.O_CLOEXEC,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 service._controller_lock_fd=lock
 signal.signal(signal.SIGTERM,service.signal);signal.signal(signal.SIGINT,service.signal)
 try:
  while not service.stop:
   try:service.step()
   except Exception as exc:atomic(service.root/'status.json',{'state':'INDEPENDENT_EVALUATION_UNAVAILABLE','reason':str(exc),'promotion_performed':False});print(type(exc).__name__,str(exc),flush=True)
   if a.once:break
   for _ in range(2):
    if service.stop:break
    time.sleep(1)
 finally:
  try:service.stop_workers()
  finally:os.close(lock)
if __name__=='__main__':main()
