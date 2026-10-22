"""Independent resident future evaluator and conditional promotion assessment.

The evaluator has no optimizer, no write path into model tensors and no execution
client. QUALIFIED is still not a canonical-owner admission or a trading cutover.
"""
from __future__ import annotations
import argparse,fcntl,json,math,os,signal,subprocess,sys,time
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


def measure(plan,slot,prediction,quotes,now_ms):
 if prediction.get('candidate_id')!=plan['candidate_id'] or prediction.get('plan_id')!=plan['plan_id'] or prediction.get('slot')!=slot:
  raise ContractError('PREDICTION_IDENTITY')
 if prediction.get('input_id')!=digest(prediction.get('input')):raise ContractError('PREDICTION_INPUT_HASH')
 calibrated=price_calibration.verify_prediction(plan,prediction)
 anchor=prediction['anchor_ms'];due=anchor+RULE['horizon_ms'];times=[q['t'] for q in quotes]
 a=bisect_left(times,anchor);b=bisect_left(times,due)
 if b==len(times) and now_ms<=due+15000:return None
 reason=None
 if prediction['created_at_ms']>=due or prediction['trained_until_ms']>anchor:reason='LOOKAHEAD'
 elif a>=len(times) or times[a]!=anchor or quotes[a]['mid']!=prediction['reference_mid']:reason='ANCHOR_MISSING_OR_CHANGED'
 elif b>=len(times):reason='OUTCOME_MISSING'
 elif times[b]>due+5000:reason='ENDPOINT_GAP'
 elif any(y-x>30000 for x,y in zip(times[a:b],times[a+1:b+1])):reason='PATH_GAP'
 base={'candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],'slot':slot,'anchor_ms':anchor,
       'prediction_available_ms':prediction['created_at_ms'],'trained_until_ms':prediction['trained_until_ms'],
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
 comparison={}
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
  self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
  self.stop=False;self.tasks={};self.last_log_ms=0;self._retained_closeout_cache={}
 def operator_stop_binding(self,path,plan,now):
  from .resident_trial_stop import read_stop
  return read_stop(self.c,path,plan,now_ms=now)
 def signal(self,*_):
  self.stop=True
  for p,stream,started in self.tasks.values():
   if p.poll() is None:p.terminate()
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
  key=(str(path),slot)
  stream=(path.parent/f'prediction-{slot:03}.log').open('a')
  p=subprocess.Popen([self.c['python'],'-B','-m','laya_cell.resident_launch','predict','--config',str(self.path),'--plan',str(path),'--slot',str(slot)],stdout=stream,stderr=stream)
  self.tasks[key]=(p,stream,time.monotonic())
 def reap(self):
  for key,(p,stream,started) in list(self.tasks.items()):
   expired=time.monotonic()-started>240
   if p.poll() is None and not expired:continue
   if p.poll() is None:p.terminate()
   try:rc=p.wait(timeout=5)
   except subprocess.TimeoutExpired:p.kill();rc=p.wait(timeout=5)
   stream.close();del self.tasks[key]
   path=Path(key[0]);slot=key[1]
   if (path.parent/f'prediction-{slot:03}.json').exists():continue
   phase_path=path.parent/f'phase-{slot:03}.json'
   phase=read_json(phase_path) if phase_path.exists() else {'phase':'STARTUP'}
   log=(path.parent/f'prediction-{slot:03}.log').read_text()[-1500:].strip().splitlines()
   reason=log[-1][:240] if log else 'NO_PROCESS_DIAGNOSTIC'
   if expired:reason='WORKER_BUDGET_EXCEEDED'
   failure={'schema':'heptatrader.laya-worker-failure.v1','slot':slot,'at_ms':int(time.time()*1000),
    'exit_code':rc,'reason':reason,'phase':phase,'predicted':False}
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
  counts={};observed=0;censored=0;worker_errors={};futile_pending=0;launches_suppressed=0;retained_plans=[];operator_stops=[]
  for path in plans:
   plan=read_json(path)
   if plan['plan_id']!=digest({k:v for k,v in plan.items() if k!='plan_id'}) :
    raise ContractError('REGISTERED_PLAN_CHANGED')
   rule=validate_plan_rule(plan)
   if plan.get('scope')!=self.c['period_sha256']:raise ContractError('EVALUATION_PERIOD_SCOPE')
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
    stopped=self.operator_stop_binding(path,plan,now)
    if stopped:operator_stops.append({'plan_id':plan['plan_id'],'receipt_sha256':stopped['sha256'],
     'performance_assessment':False,'new_heavy_workers_allowed':False})
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
   source_ok=isinstance(pins,dict) and bool(pins) and all(v and sha_file(runtime/k)==v for k,v in pins.items())
   if not source_ok:
    decision={'state':'REJECTED','candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],
      'reasons':['EVALUATOR_SOURCE_UNPINNED_OR_CHANGED'],'promotion_performed':False,
      'canonical_owner_admitted':False,'automatic_execution_selection':False}
    if not (path.parent/'qualification.json').exists():write_once(path.parent/'qualification.json',decision)
    counts['REJECTED']=counts.get('REJECTED',0)+1;continue
   recorded=[read_json(p) for p in sorted(path.parent.glob('outcome-*.json'))]
   futile=suppress_new_work_for_frozen_failure(plan,recorded)
   if futile:futile_pending+=1
   for slot in range(rule['slots']):
    output=path.parent/f'outcome-{slot:03}.json';prediction=path.parent/f'prediction-{slot:03}.json'
    if output.exists():continue
    start=plan['start_ms']+slot*rule['slot_spacing_ms'];end=start+rule['input_grace_ms']
    if not prediction.exists():
     key=(str(path),slot);failure=path.parent/f'failure-{slot:03}.json'
     if key in self.tasks:continue
     if failure.exists():
      failed=read_json(failure);reason=failed['reason'];worker_errors[reason]=worker_errors.get(reason,0)+1
      write_once(output,{'candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],'slot':slot,'status':'CENSORED',
       'reason':'PREDICTION_PROCESS_FAILED','detail':reason,'phase':failed['phase'],'evaluated_at_ms':now});continue
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
      write_once(output,{'candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],'slot':slot,'status':'CENSORED',
       'reason':'NO_PREDICTION_IN_FIXED_SLOT','evaluated_at_ms':now,**provenance});continue
     # Cold model preparation occurs before, not inside, the fixed input slot.
     if now<start-90000 or len(self.tasks)>=2:continue
     # An expired or failed earlier slot can become irrevocably censored
     # during THIS step. Recompute immediately before GPU admission.
     if not futile:
      current=[read_json(p) for p in sorted(path.parent.glob('outcome-*.json'))]
      futile=suppress_new_work_for_frozen_failure(plan,current)
      if futile:futile_pending+=1
     if futile:
      launches_suppressed+=1
      continue
     self.launch(path,slot);continue
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
    result=measure(plan,slot,pred,quotes,quote_clock)
    if result is not None:
     result['source_prefixes']=prefixes;result['prediction_file_sha256']=sha_file(prediction)
     write_once(output,result)
   results=[read_json(p) for p in sorted(path.parent.glob('outcome-*.json'))]
   observed+=sum(r['status']=='OBSERVED' for r in results);censored+=sum(r['status']=='CENSORED' for r in results)
   assessment=assess(plan,results,int(time.time()*1000))
   if plan.get('price_baselines') is not None:assessment['same_window_price_baselines']=price_refs.summarize(results)
   if assessment['state'] != 'PENDING':
    write_once(path.parent / 'qualification.json', assessment)
    atomic(path.parent / 'progress.json', assessment)
   else:
    atomic(path.parent / 'progress.json', assessment)
   counts[assessment['state']]=counts.get(assessment['state'],0)+1
  status={'schema':'heptatrader.laya-resident-evaluation-status.v2','at_ms':int(time.time()*1000),
   'plans':len(plans),'counts':counts,'observed':observed,'censored':censored,'active_prediction_workers':len(self.tasks),
   'prediction_prewarm_ms':90000,'worker_failure_details':worker_errors,
   'latest_quote_ms':quotes[-1]['t'] if quotes else None,
   'latest_quote_scope':'OUTCOME_SOURCE_SNAPSHOT_NOT_GLOBAL_SOURCE_HEALTH',
   'source_scan_performed':quotes is not None,'futile_pending_plans':futile_pending,
   'retained_futile_plan_closeouts':retained_plans,
   'operator_stopped_trials':len(operator_stops),'operator_stopped_plans':operator_stops,
   'new_prediction_launches_suppressed':launches_suppressed,'statistical_rule':RULE if not self.c.get('promotion_protocol') else None,
   'promotion_protocol':self.c.get('promotion_protocol','LEGACY_V1'),'counts_include_closed_plans':True,
   'automatic_trading':False,'canonical_owner_connected':False,'promotion_performed':False,'broker_orders_sent':0}
  atomic(self.root/'status.json',status)
  if now-self.last_log_ms>=30000:print(json.dumps(status),flush=True);self.last_log_ms=now
  return status

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();os.umask(0o077)
 service=Evaluator(a.config);lock=os.open(service.root/'evaluator.lock',os.O_WRONLY|os.O_CREAT|os.O_CLOEXEC,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 signal.signal(signal.SIGTERM,service.signal);signal.signal(signal.SIGINT,service.signal)
 while not service.stop:
  try:service.step()
  except Exception as exc:atomic(service.root/'status.json',{'state':'INDEPENDENT_EVALUATION_UNAVAILABLE','reason':str(exc),'promotion_performed':False});print(type(exc).__name__,str(exc),flush=True)
  if a.once:break
  for _ in range(2):
   if service.stop:break
   time.sleep(1)
 os.close(lock)
if __name__=='__main__':main()
