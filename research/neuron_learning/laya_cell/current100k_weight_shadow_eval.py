"""Current-ledger future evaluation of frozen scalar heads, independent of training.

Paired errors use identical original commands. UTC-day blocks connected by
overlapping episodes stay together. Bootstrap intervals are conditional block
estimates, not an independence guarantee or a production selection receipt.
"""
from __future__ import annotations
import argparse, fcntl, json, math, os, random, signal, time
from collections import Counter
from pathlib import Path
from . import current100k_weight_shadow as W

def clusters(rows):
 intervals=sorted(((r['opened_at_ms']//W.DAY_MS,r['closed_at_ms']//W.DAY_MS,r) for r in rows),key=lambda v:(v[0],v[1],v[2]['command_id']))
 groups=[];end=-1
 for first,last,row in intervals:
  if first>end:groups.append([])
  groups[-1].append(row);end=max(end,last)
 return groups

def metrics(errors):
 return {'mae_usd':math.fsum(abs(v) for v in errors)/len(errors),
         'rmse_usd':math.hypot(*errors)/math.sqrt(len(errors))} if errors else {'mae_usd':None,'rmse_usd':None}

def percentile(values,q):
 values=sorted(values);at=(len(values)-1)*q;low=int(at);high=min(low+1,len(values)-1)
 return values[low]+(values[high]-values[low])*(at-low)

def paired_interval(groups,candidate,reference,alpha,repetitions,seed):
 if len(groups)<3:return None
 # Sufficient statistics per whole block; never resample dependent individual trades.
 stat=[]
 for group in groups:
  a=[r['predictions'][candidate]-r['target_usd'] for r in group]
  b=[r['predictions'][reference]-r['target_usd'] for r in group]
  stat.append((len(group),sum(map(abs,a)),sum(map(abs,b)),sum(x*x for x in a),sum(x*x for x in b)))
 rng=random.Random(seed);maes=[];rmses=[]
 for _ in range(repetitions):
  draw=[stat[i] for i in rng.choices(range(len(stat)),k=len(stat))];n=sum(x[0] for x in draw)
  maes.append((sum(x[2] for x in draw)-sum(x[1] for x in draw))/n)
  rmses.append(math.sqrt(sum(x[4] for x in draw)/n)-math.sqrt(sum(x[3] for x in draw)/n))
 return {'mae_gain_usd':[percentile(maes,alpha/2),percentile(maes,1-alpha/2)],
         'rmse_gain_usd':[percentile(rmses,alpha/2),percentile(rmses,1-alpha/2)],
         'interval_level':1-alpha,'method':'PAIRED_WHOLE_TIME_BLOCK_PERCENTILE_BOOTSTRAP','exploratory_only':True,'multiple_comparison_corrected':False,'used_for_acceptance':False,
         'assumption':'BLOCKS_APPROXIMATELY_EXCHANGEABLE; SERIAL_OR_REGIME_DEPENDENCE_CAN_REMAIN'}

def mixture_evidence(rows,candidate,reference,rule,alpha):
 from .financial_forward_protocol import huber
 differences=[];bounds=[]
 for row in rows:
  y=row['target_usd'];a=row['predictions'][candidate];b=row['predictions'][reference]
  difference=huber((b-y)/100)-huber((a-y)/100);bound=abs(b-a)/100
  W.require(abs(difference)<=bound+1e-9,'SHADOW_HUBER_LIPSCHITZ_BOUND')
  differences.append(difference);bounds.append(bound)
 terms=[lam*sum(differences)-.5*lam*lam*sum(b*b for b in bounds) for lam in rule['lambda_grid']]
 maximum=max(terms);loge=maximum+math.log(sum(math.exp(x-maximum) for x in terms)/len(terms))
 required=math.log(2/alpha)
 return {'log_e':loge,'required_log_e':required,'threshold_met':loge>=required,'pair_alpha':alpha,
         'method':rule['sequential_test'],'assumption':'CONDITIONAL_NO_ADVANTAGE_NULL_FOR_ADAPTED_PAIRED_LOSS_DIFFERENCES; NOT_IID_ASSUMED',
         'arbitrary_distribution_shift_guarantee':False,'production_trial_alpha_spending_applied':False,
         'alpha_scope':'THIS_FROZEN_RESEARCH_FAMILY_ONLY; NOT_A_PRODUCTION_TRIAL_REGISTRATION'}

def assessment(p,results,now,source_issues=(),training_labels_current=True):
 W.validate_plan(p);ids=[r['command_id'] for r in results];W.require(len(ids)==len(set(ids)),'SHADOW_DUPLICATE_COMMAND')
 obs=[dict(r,predictions={**r['predictions'],'BENCHMARK_ZERO':0.,'BENCHMARK_TRAIN_MEAN':p['training_mean_usd']}) for r in results if r['status']=='OBSERVED']
 for r in obs:W.require(set(r['predictions'])==set(p['heads'])|{'BENCHMARK_ZERO','BENCHMARK_TRAIN_MEAN'},'SHADOW_UNPAIRED_POPULATION')
 obs.sort(key=lambda r:(r['opened_at_ms'],r['command_id']));groups=clusters(obs)
 days={r['opened_at_ms']//W.DAY_MS for r in obs};mid=(p['input_start_ms']+p['input_end_ms'])//2
 halves=[sum(r['opened_at_ms']<mid for r in obs),sum(r['opened_at_ms']>=mid for r in obs)]
 unresolved=len(results)-len(obs);coverage=len(obs)/len(results) if results else 0.;reasons=[]
 rule=p['rule'];final=now>=p['outcome_end_ms']
 if len(obs)<rule['min_observed_trades']:reasons.append('INSUFFICIENT_PROSPECTIVE_TRADES')
 if len(days)<rule['min_distinct_utc_days'] or len(groups)<3:reasons.append('INSUFFICIENT_TIME_BLOCK_SUPPORT')
 if min(halves)<rule['min_trades_each_time_half']:reasons.append('INDEPENDENT_TIME_HALVES_INCOMPLETE')
 if coverage<rule['minimum_prediction_coverage']:reasons.append('PREDICTION_COVERAGE_INCOMPLETE')
 if unresolved>rule['maximum_unresolved_at_final']:reasons.append('UNRESOLVED_OR_CENSORED_ORIGINAL_COMMANDS')
 if len({r['target_usd']>0 for r in obs if r['target_usd']!=0})<rule['minimum_outcome_sign_classes']:reasons.append('INSUFFICIENT_SIGN_COVERAGE')
 if any(r['target_exceeds_training_support'] for r in obs):reasons.append('TRAINING_SUPPORT_EXCEEDED_RETAINED')
 if source_issues:reasons.append('SOURCE_UNAVAILABLE_OR_CHANGED')
 if not training_labels_current:reasons.append('TRAINING_LABEL_REVOKED_OR_CHANGED')
 if not final:reasons.append('FIXED_FUTURE_WINDOW_NOT_FINISHED')
 arms=sorted(p['heads']);references=sorted(set(arms)|{'BENCHMARK_ZERO','BENCHMARK_TRAIN_MEAN'})
 pairs=[(a,b) for a in arms for b in references if a!=b];alpha=rule['family_alpha']/max(1,len(pairs))
 summaries={a:metrics([r['predictions'][a]-r['target_usd'] for r in obs]) for a in references};comparisons={}
 for arm,reference in pairs:
  gains=[abs(r['predictions'][reference]-r['target_usd'])-abs(r['predictions'][arm]-r['target_usd']) for r in obs]
  half_gain=[sum(v for r,v in zip(obs,gains) if (r['opened_at_ms']<mid)==first)/n if n else None for first,n in zip((True,False),halves)]
  stresses={}
  for mult in rule['fee_stresses']:
   ys=[r['target_usd']-(mult-1)*r['fee_usd'] for r in obs]
   stress=[abs(r['predictions'][reference]-y)-abs(r['predictions'][arm]-y) for r,y in zip(obs,ys)]
   stresses[str(mult)]=sum(stress)/len(stress) if stress else None
  interval=paired_interval(groups,arm,reference,.05,p['bootstrap_repetitions'],p['bootstrap_seed']) if final else None
  evidence=mixture_evidence(obs,arm,reference,rule,alpha)
  gain=sum(gains)/len(gains) if gains else None
  comparisons[arm+' vs '+reference]={'mae_gain_usd':gain,'rmse_gain_usd':summaries[reference]['rmse_usd']-summaries[arm]['rmse_usd'] if obs else None,'half_mae_gain_usd':half_gain,'fee_stress_mae_gain_usd':stresses,'final_cluster_interval':interval,'huber_mixture_evidence':evidence,'comparative_conditions_met':bool(final and not reasons and gain>rule['minimum_mae_improvement_usd'] and all(v is not None and v>0 for v in half_gain) and all(v is not None and v>=0 for v in stresses.values()) and evidence['threshold_met'])}
 return {'schema':'heptatrader.current100k-weight-shadow-assessment.v1','plan_id':p['plan_id'],'as_of_ms':now,'state':'PENDING' if not final else ('FINAL_INCOMPLETE' if reasons else 'FINAL_RESEARCH_COMPARISON'),'reasons':sorted(set(reasons)),'original_command_count':len(results),'observed_trades':len(obs),'status_counts':dict(Counter(r['status'] for r in results)),'prediction_coverage':coverage,'distinct_utc_days':len(days),'time_blocks':len(groups),'half_counts':halves,'arm_metrics':summaries,'paired_comparisons':comparisons,'family_alpha':rule['family_alpha'],'pair_alpha':alpha,'multiple_comparison_count':len(pairs),'intervals_only_at_fixed_final':True,'cross_producer_transfer':p['cross_producer_transfer'],'source_issues':list(source_issues),'financial_candidate_selected':False,'canonical_agentd_accepted':False,'trade_authority':False,'broker_orders_sent':0,'historical_trials_modified':False,'independence_or_generalization_proven':False}

class Evaluator:
 """Separate process: reads sealed predictions and current authoritative replay."""
 def __init__(self,root):
  self.store=W.ShadowStore(root);self.plan=self.store.plan;self.root=Path(root)
  p=self.plan;W.require(W.sha(p['reward_config_path'])==p['reward_config_sha256'],'SHADOW_REWARD_CONFIG_CHANGED')
  self.config=W.read(p['reward_config_path']);self.last_identity=None;self.last_result=None;self.last_read_ms=0
 def identity(self,now):
  from .financial_forward_source import ingress_identity,file_identity
  p=self.plan
  return {'ingress':ingress_identity(p['producer_registration'],p['period_sha256']),
          'oms':file_identity(self.config['oms_path']),'period':file_identity(self.config['period_path']),
          'reward_config':file_identity(p['reward_config_path']),'dataset':file_identity(p['training_dataset_path']),
          'final':now>=p['outcome_end_ms'],
          'publications':[(str(x),file_identity(x)) for x in sorted((self.root/'predictions').glob('*/publication.json'))]}
 def snapshot(self):
  from .financial_forward_source import ingress
  from .resident_rewards import current
  from .financial_forward_evaluate import execution_index
  p=self.plan;now=W.clock_ms()
  W.require(W.sha(p['reward_config_path'])==p['reward_config_sha256'],'SHADOW_REWARD_CONFIG_CHANGED')
  W.require(W.sha(p['training_dataset_path'])==p['training_dataset_sha256'],'SHADOW_TRAINING_DATA_CHANGED')
  manifest,requests,bindings,issues=ingress(p['producer_registration'],p['period_sha256'])
  report=current(self.config,now)
  if not report.get('components',{}).get('EUR.USD',{}).get('ledger_complete'):
   issues.append({'reason':'CURRENT_LEDGER_REPLAY_UNAVAILABLE'})
  # A partial final replay cannot establish the population denominator. Keep
  # the last completed assessment intact; main publishes a separate failure.
  if now>=p['outcome_end_ms'] and issues:
   raise W.EvidenceError('FINAL_SOURCE_UNAVAILABLE:'+json.dumps(issues,sort_keys=True)[:2000])
  times,command_fills,execution_commands=execution_index(report['source_prefix']) if report.get('source_prefix') else ({},{},{})
  return self.evaluate_snapshot(requests,bindings,report,times,command_fills,execution_commands,now,issues)
 def evaluate_snapshot(self,requests,bindings,report,times,command_fills,execution_commands,now,issues=()):
  p=self.plan;issues=list(issues);seen=self.store.remember_commands(bindings)
  current={b['command']['command_id']:b for b in bindings}
  reward_by={r['open_command_id']:r for r in report.get('rewards',[])}
  known_by={r['open_command_id']:r for r in report.get('execution_rewards',[]) if r.get('instrument')=='EUR.USD'}
  results=[]
  for cmd,b in seen.items():
   try:prediction=self.store.prediction(b['request_id'])
   except (ValueError,OSError,KeyError) as exc:
    prediction=None;issues.append({'command_id':cmd,'reason':'PREDICTION_REVOKED:'+str(exc)[:160]})
   reward=reward_by.get(cmd);first=command_fills.get(cmd);closed=None
   if reward is not None and reward.get('executions'):
    full_times=[times.get(e['execution_id']) for e in reward['executions']]
    if all(t is not None for t in full_times):closed=max(full_times)
   result=W.score_case(p,b,prediction,requests.get(b['request_id']),reward,first,closed,now)
   if cmd not in current:result.update(status='CENSORED',reason='PREVIOUSLY_SEEN_BINDING_WITHDRAWN')
   elif cmd in known_by and reward is None:result.update(status='CENSORED',reason='CURRENT_REWARD_UNSETTLED_OR_REVOKED')
   results.append(result)
  def opening_in_window(cmd,first):
   # An exact request determines membership, even when its first fill is later.
   bound=current.get(cmd)
   if bound is not None:return p['input_start_ms']<=bound['request_started_at_ms']<p['input_end_ms']
   return first is not None and p['input_start_ms']<=first<p['input_end_ms']
  for cmd,known in known_by.items():
   first=command_fills.get(cmd)
   if cmd not in seen and first is not None and opening_in_window(cmd,first):
    results.append({'command_id':cmd,'request_id':None,'opened_at_ms':first,'status':'CENSORED','reason':'ACTUAL_OPEN_WITHOUT_EXACT_PLAN_BINDING'})
  for blocked in report.get('blocked',[]):
   if blocked.get('instrument')!='EUR.USD' or blocked.get('reason')!='OPEN_RESIDUAL_POSITION':continue
   # Native replay retains each residual cycle in execution order. Subsequent
   # fills may belong to partial closes; they are not new original openings.
   for eid in blocked.get('execution_ids',[])[:1]:
    first=times.get(eid);cmd=execution_commands.get(eid)
    if first is None or cmd in seen or not opening_in_window(cmd,first):continue
    marker=cmd or 'unattributed-execution-'+W.digest(eid)
    if not any(r['command_id']==marker for r in results):
     results.append({'command_id':marker,'request_id':None,'opened_at_ms':first,'status':'CENSORED','reason':'UNCLOSED_OPEN_WITHOUT_EXACT_INPUT'})
  # An actual execution without attribution also stays in the denominator.
  unbound=self.root/'seen-unbound-commands';unbound.mkdir(mode=0o700,parents=True,exist_ok=True)
  for row in results:
   if row['request_id'] is None:
    record={'command_id':row['command_id'],'opened_at_ms':row['opened_at_ms']}
    path=unbound/(W.safe_id(row['command_id'])+'.json')
    if not path.exists():W.durable_once(path,record)
  found={r['command_id'] for r in results}
  for path in sorted(unbound.glob('*.json')):
   record=W.read(path)
   if record['command_id'] not in found:
    results.append({**record,'request_id':None,'status':'CENSORED','reason':'PREVIOUS_ACTUAL_COMMAND_NO_LONGER_OBSERVABLE'})
  present={r['reward_id'] for r in report.get('rewards',[])}
  training_ok=set(p['training_reward_ids'])<=present
  result=assessment(p,results,now,issues,training_ok)
  result['nontrade_requests']=sum(p['input_start_ms']<=v['request']['started_at_ms']<p['input_end_ms'] and v['request']['position_at_request']==0 and rid not in {b['request_id'] for b in seen.values()} for rid,v in requests.items())
  result['outcome_set_digest']=W.digest(results);result['source_ledger_prefix']=report.get('source_prefix')
  result['source_record_count']=len(requests)
  for row in results:
   folder=self.root/'outcomes'/W.safe_id(row['command_id']);record_id=W.digest(row)
   W.durable_once(folder/(record_id+'.json'),row)
   pointer={'record_id':record_id}
   if not (folder/'current.json').exists() or W.read(folder/'current.json')!=pointer:W.atomic(folder/'current.json',pointer)
  W.atomic(self.root/'assessment.json',result)
  return result
 def step(self):
  now=W.clock_ms();identity=self.identity(now)
  # Avoid repeated full-ledger replay, but periodically revalidate all evidence.
  if identity==self.last_identity and self.last_result is not None and now-self.last_read_ms<60000:
   return {**self.last_result,'heartbeat_at_ms':now,'replay_skipped':True}
  result=self.snapshot();after=self.identity(W.clock_ms())
  self.last_identity=identity if identity==after else None;self.last_result=result;self.last_read_ms=now
  return result

TERMINAL_UNAVAILABLE_SCHEMA='heptatrader.current100k-weight-shadow-terminal-unavailable.v1'

def read_terminal_unavailable(root,plan):
 path=Path(root)/'terminal-unavailable.json'
 if not path.exists():return None
 value=W.read(path)
 W.require(value.get('schema')==TERMINAL_UNAVAILABLE_SCHEMA and value.get('plan_id')==plan['plan_id'],'SHADOW_TERMINAL_IDENTITY')
 W.require(value.get('state')=='FINAL_UNAVAILABLE_NO_SELECTION' and value.get('current_replay_complete')is False,'SHADOW_TERMINAL_STATE')
 W.require(value.get('outcome_end_ms')==plan['outcome_end_ms'] and W.integer(value['checked_at_ms'])>=plan['outcome_end_ms'],'SHADOW_TERMINAL_BEFORE_FIXED_END')
 W.require(all(value.get(k)is False for k in ('financial_candidate_selected','canonical_agentd_accepted','trade_authority')),'SHADOW_TERMINAL_NO_AUTHORITY')
 W.require(all(value.get(k)is None for k in ('observed_trades','original_command_count','prediction_coverage')),'SHADOW_TERMINAL_UNKNOWN_COVERAGE')
 W.require(isinstance(value.get('error'),dict) and isinstance(value['error'].get('type'),str) and isinstance(value['error'].get('message'),str),'SHADOW_TERMINAL_ERROR')
 return value

def publish_final_unavailable(root,plan,error,*,checked_at_ms=None,diagnostics=None):
 """A separate immutable failure receipt; never re-date or overwrite an assessment."""
 root=Path(root);W.validate_plan(plan)
 checked=W.clock_ms() if checked_at_ms is None else W.integer(checked_at_ms)
 W.require(checked>=plan['outcome_end_ms'],'SHADOW_TERMINAL_BEFORE_FIXED_END')
 existing=read_terminal_unavailable(root,plan)
 if existing is not None:return existing
 prior=None;prior_error=None;path=root/'assessment.json'
 if path.exists():
  try:
   raw=path.read_bytes();value=json.loads(raw)
   W.require(value.get('schema')=='heptatrader.current100k-weight-shadow-assessment.v1' and value.get('plan_id')==plan['plan_id'],'SHADOW_PRIOR_ASSESSMENT_SCOPE')
   W.require(type(value.get('as_of_ms'))is int and plan['registered_at_ms']<=value['as_of_ms']<=checked,'SHADOW_PRIOR_ASSESSMENT_CLOCK')
   prior={'path':str(path),'sha256':W.hashlib.sha256(raw).hexdigest(),
          **{k:value.get(k) for k in ('as_of_ms','state','observed_trades','original_command_count','prediction_coverage')}}
   # Also pin the complete old report independently of its mutable live pointer.
   saved=root/'finalization-assessments'/(W.digest(value)+'.json')
   W.durable_once(saved,value)
   prior.update(snapshot_path=str(saved),snapshot_sha256=W.sha(saved))
  except (OSError,ValueError,KeyError,TypeError) as exc:
   prior_error={'type':type(exc).__name__,'message':str(exc)[:2000]}
 receipt={'schema':TERMINAL_UNAVAILABLE_SCHEMA,'plan_id':plan['plan_id'],'state':'FINAL_UNAVAILABLE_NO_SELECTION',
          'checked_at_ms':checked,'outcome_end_ms':plan['outcome_end_ms'],'current_replay_complete':False,
          'error':{'type':type(error).__name__,'message':str(error)[:2000]},'diagnostics':diagnostics,
          'last_completed_assessment':prior,'last_assessment_read_error':prior_error,
          'observed_trades':None,'original_command_count':None,'prediction_coverage':None,
          'reasons':['FINAL_SOURCE_OR_EVALUATOR_UNAVAILABLE'],'financial_candidate_selected':False,
          'canonical_agentd_accepted':False,'trade_authority':False,'broker_orders_sent':0}
 W.durable_once(root/'terminal-unavailable.json',receipt)
 return read_terminal_unavailable(root,plan)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
 parser.add_argument('--register',type=Path);parser.add_argument('--once',action='store_true')
 args=parser.parse_args();os.umask(0o077)
 if args.register:
  print(json.dumps(W.freeze(W.read(args.register),args.root)));return
 plan=W.ShadowStore(args.root).plan;worker=None
 fd=os.open(args.root/'evaluator.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);stopping=False
 def stop(*_):
  nonlocal stopping
  stopping=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 try:
  while not stopping:
   terminal=None;finished=False
   try:
    terminal=read_terminal_unavailable(args.root,plan)
    if terminal is None:
     if worker is None:worker=Evaluator(args.root)
     result=worker.step();status={k:result[k] for k in ('plan_id','as_of_ms','state','observed_trades','original_command_count','reasons')}
     status.update(at_ms=W.clock_ms(),financial_candidate_selected=False,broker_orders_sent=0,replay_skipped=result.get('replay_skipped',False))
     finished=result['as_of_ms']>=plan['outcome_end_ms'] and result['state'].startswith('FINAL_')
   except Exception as exc:
    checked=W.clock_ms()
    status={'state':'UNAVAILABLE_NO_SELECTION','at_ms':checked,'reason':str(exc)[:220],'financial_candidate_selected':False,'broker_orders_sent':0}
    if checked>=plan['outcome_end_ms']:
     terminal=publish_final_unavailable(args.root,plan,exc,checked_at_ms=checked)
   if terminal is not None:
    status={k:terminal[k] for k in ('plan_id','state','checked_at_ms','observed_trades','original_command_count','prediction_coverage','error','last_completed_assessment','reasons','financial_candidate_selected','broker_orders_sent')}
    status.update(at_ms=W.clock_ms(),terminal_report_path=str(args.root/'terminal-unavailable.json'));finished=True
   W.atomic(args.root/'evaluator-status.json',status);print(json.dumps(status),flush=True)
   if args.once or finished:break
   for _ in range(10):
    if stopping:break
    time.sleep(1)
 finally:os.close(fd)

if __name__=='__main__':main()
