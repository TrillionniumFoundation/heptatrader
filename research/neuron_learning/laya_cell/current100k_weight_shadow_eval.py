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
 def __init__(self,root,*,output_root=None,strict_preopen=False):
  self.store=W.ShadowStore(root);self.plan=self.store.plan;self.source_root=Path(root)
  self.root=Path(output_root) if output_root is not None else self.source_root
  W.require(self.root.is_absolute() and not self.root.is_symlink(),'SHADOW_EVALUATOR_OUTPUT_ROOT')
  self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
  self.preopen=None;self.strict_review=bool(strict_preopen)
  if strict_preopen or (self.source_root/'activation.json').exists():
   from .current100k_preopen_evidence import PreopenEvidence
   self.preopen=PreopenEvidence(self.source_root)
   provenance=self.preopen.provenance()
   provenance.update(review_output_root=str(self.root),original_reports_modified=self.root==self.source_root)
   W.durable_once(self.root/'strict-review-provenance.json',provenance)
  self.assessment_name = 'assessment-strict.json' if output_root is not None and self.preopen is not None else 'assessment.json'
  if strict_preopen:W.require(retained_review_plan(self.source_root,self.root)==self.plan,'SHADOW_REVIEW_PLAN_CHANGED')
  p=self.plan;W.require(W.sha(p['reward_config_path'])==p['reward_config_sha256'],'SHADOW_REWARD_CONFIG_CHANGED')
  self.config=W.read(p['reward_config_path']);self.last_identity=None;self.last_result=None;self.last_read_ms=0
 def identity(self,now):
  from .financial_forward_source import ingress_identity,file_identity
  p=self.plan
  return {'ingress':ingress_identity(p['producer_registration'],p['period_sha256']),
          'oms':file_identity(self.config['oms_path']),'period':file_identity(self.config['period_path']),
          'reward_config':file_identity(p['reward_config_path']),'dataset':file_identity(p['training_dataset_path']),
          'final':now>=p['outcome_end_ms'],
          'publications':[(str(x),file_identity(x)) for x in sorted((self.source_root/'predictions').glob('*/*.json'))
                          if x.name in {'features.json','prediction.json','commit.json','publication.json','observation.json'}],
          'preopen':self.preopen.identity() if self.preopen is not None else None}
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
  times,command_fills,execution_commands=execution_index(report['source_prefix']) if report.get('source_prefix') else ({},{},{})
  return self.evaluate_snapshot(requests,bindings,report,times,command_fills,execution_commands,now,issues)
 def evaluate_snapshot(self,requests,bindings,report,times,command_fills,execution_commands,now,issues=()):
  p=self.plan;issues=list(issues);source_replay_complete=not bool(issues)
  seen=self.store.remember_commands(bindings,state_root=self.root)
  current={b['command']['command_id']:b for b in bindings}
  reward_by={r['open_command_id']:r for r in report.get('rewards',[])}
  known_by={r['open_command_id']:r for r in report.get('execution_rewards',[]) if r.get('instrument')=='EUR.USD'}
  results=[]
  for cmd,b in seen.items():
   failure=None;preopen_evidence=None
   try:
    prediction=self.store.prediction(b['request_id'])
    if prediction is not None and self.preopen is not None:preopen_evidence=self.preopen.check(b,prediction)
   except (ValueError,OSError,KeyError,TypeError) as exc:
    prediction=None;failure='PREDICTION_REVOKED:'+str(exc)[:160]
    issues.append({'command_id':cmd,'reason':failure})
   reward=reward_by.get(cmd);first=command_fills.get(cmd);closed=None
   if reward is not None and reward.get('executions'):
    full_times=[times.get(e['execution_id']) for e in reward['executions']]
    if all(t is not None for t in full_times):closed=max(full_times)
   result=W.score_case(p,b,prediction,requests.get(b['request_id']),reward,first,closed,now)
   if failure:result.update(status='CENSORED',reason=failure)
   if preopen_evidence is not None:result['preopen_terminal_evidence']=preopen_evidence
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
  # An incomplete authoritative replay cannot preserve current financial credit.
  # Retain any newly discovered denominator first, without overwriting the last complete report.
  if self.strict_review and not source_replay_complete:
   return strict_source_unavailable(self.root,p,ValueError('STRICT_SOURCE_REPLAY_INCOMPLETE'),now,source_issues=issues)
  present={r['reward_id'] for r in report.get('rewards',[])}
  training_ok=set(p['training_reward_ids'])<=present
  result=assessment(p,results,now,issues,training_ok)
  result['nontrade_requests']=sum(p['input_start_ms']<=v['request']['started_at_ms']<p['input_end_ms'] and v['request']['position_at_request']==0 and rid not in {b['request_id'] for b in seen.values()} for rid,v in requests.items())
  result['outcome_set_digest']=W.digest(results);result['source_ledger_prefix']=report.get('source_prefix')
  result['source_record_count']=len(requests)
  result['current_replay_complete']=source_replay_complete
  result['original_command_count_complete']=source_replay_complete
  result['strict_preopen_terminal_required']=self.preopen is not None
  if self.preopen is not None:result['review_provenance_sha256']=W.sha(self.root/'strict-review-provenance.json')
  for row in results:
   folder=self.root/'outcomes'/W.safe_id(row['command_id']);record_id=W.digest(row)
   W.durable_once(folder/(record_id+'.json'),row)
   pointer={'record_id':record_id}
   if not (folder/'current.json').exists() or W.read(folder/'current.json')!=pointer:W.atomic(folder/'current.json',pointer)
  W.atomic(self.root/self.assessment_name,result)
  return result
 def step(self):
  now=W.clock_ms();identity=self.identity(now)
  # Avoid repeated full-ledger replay, but periodically revalidate all evidence.
  if identity==self.last_identity and self.last_result is not None and now-self.last_read_ms<60000:
   return {**self.last_result,'heartbeat_at_ms':now,'replay_skipped':True}
  result=self.snapshot();after=self.identity(W.clock_ms())
  self.last_identity=identity if identity==after else None;self.last_result=result;self.last_read_ms=now
  return result

STRICT_FAILURE_SCHEMA='heptatrader.weight-shadow-strict-source-failure.v1'

def retained_review_plan(source_root,output_root):
 """Retain an already registered plan for source-loss restart; never register."""
 source_root=Path(source_root);output_root=Path(output_root)
 W.require(source_root.is_absolute() and output_root.is_absolute() and source_root!=output_root,'SHADOW_STRICT_REVIEW_SEPARATE_OUTPUT')
 W.require(not output_root.is_symlink(),'SHADOW_EVALUATOR_OUTPUT_ROOT')
 output_root.mkdir(mode=0o700,parents=True,exist_ok=True);path=output_root/'review-plan-snapshot.json'
 if path.exists():saved=W.read(path)
 else:
  store=W.ShadowStore(source_root)
  saved={'schema':'heptatrader.weight-shadow-review-plan-snapshot.v1','source_root':str(source_root),
         'plan':store.plan,'original_plan_sha256':W.sha(source_root/'plan.json'),
         'registration':W.read(source_root/'registration.json'),'window_registered_by_review':False}
  saved['snapshot_id']=W.digest(saved);W.durable_once(path,saved)
 W.require(saved.get('schema')=='heptatrader.weight-shadow-review-plan-snapshot.v1' and saved.get('source_root')==str(source_root)
           and saved.get('window_registered_by_review')is False
           and saved.get('snapshot_id')==W.digest({k:v for k,v in saved.items() if k!='snapshot_id'}),'SHADOW_REVIEW_PLAN_SNAPSHOT')
 p=saved['plan'];W.validate_plan(p);registration=saved['registration']
 W.require(registration['plan_id']==p['plan_id'] and registration['plan_sha256']==saved['original_plan_sha256']
           and p['registered_at_ms']<=registration['durable_at_ms']<p['input_start_ms'],'SHADOW_REVIEW_REGISTRATION_SNAPSHOT')
 return p

def read_strict_terminal(output_root,plan):
 path=Path(output_root)/'strict-terminal-unavailable.json'
 if not path.exists():return None
 receipt=W.read(path)
 W.require(receipt.get('schema')==STRICT_FAILURE_SCHEMA and receipt.get('plan_id')==plan['plan_id']
           and receipt.get('outcome_end_ms')==plan['outcome_end_ms']
           and W.integer(receipt['as_of_ms'])>=plan['outcome_end_ms'],'SHADOW_STRICT_FINAL_SCOPE')
 result=receipt['assessment']
 W.require(result.get('plan_id')==plan['plan_id'] and result.get('state')=='FINAL_INCOMPLETE'
           and result.get('as_of_ms')==receipt['as_of_ms'] and result.get('observed_trades')==0
           and result.get('current_replay_complete')is False
           and all(result.get(k)is False for k in ('financial_candidate_selected','canonical_agentd_accepted','trade_authority')),
           'SHADOW_STRICT_FINAL_NO_CURRENT_CREDIT')
 return receipt

def strict_source_unavailable(output_root,plan,error,now,*,source_issues=()):
 """Revoke current credit, retaining the known denominator and past reports."""
 root=Path(output_root);W.validate_plan(plan);now=W.integer(now)
 existing=read_strict_terminal(root,plan)
 if existing is not None:
  W.atomic(root/'assessment-strict.json',existing['assessment']);return existing['assessment']
 issue={'reason':'STRICT_SOURCE_REPLAY_UNAVAILABLE','error_type':type(error).__name__,'message':str(error)[:2000]}
 path=root/'assessment-strict.json';prior=None;prior_count=0
 if path.exists():
  try:
   old=W.read(path);W.require(old['plan_id']==plan['plan_id'],'SHADOW_STRICT_PRIOR_SCOPE')
   count=old.get('original_command_count');W.require(type(count)is int and count>=0,'SHADOW_STRICT_PRIOR_COUNT');prior_count=count
   prior_path=root/'assessment-history'/(W.digest(old)+'.json');W.durable_once(prior_path,old)
   prior=old.get('last_completed_assessment') if old.get('current_replay_complete')is False else {
          'path':str(prior_path),'sha256':W.sha(prior_path),'as_of_ms':old.get('as_of_ms'),
          'state':old.get('state'),'original_command_count':count,'observed_trades':old.get('observed_trades')}
  except (OSError,ValueError,KeyError,TypeError) as exc:issue['prior_assessment_error']=type(exc).__name__+':'+str(exc)[:300]
 ids=set();state_errors=[]
 for directory,kind in ((root/'seen-commands','file'),(root/'seen-unbound-commands','file'),(root/'outcomes','directory')):
  if not directory.exists():continue
  for item in sorted(directory.iterdir()):
   if (kind=='file' and item.suffix!='.json') or (kind=='directory' and not item.is_dir()):continue
   try:ids.add(W.safe_id(item.stem if kind=='file' else item.name))
   except ValueError as exc:state_errors.append({'path':str(item),'reason':str(exc)})
 rows=[{'command_id':cid,'request_id':None,'status':'CENSORED','reason':'CURRENT_SOURCE_UNAVAILABLE_NO_CREDIT'} for cid in sorted(ids)]
 result=assessment(plan,rows,now,[*source_issues,issue,*state_errors],training_labels_current=False);denominator=max(prior_count,len(rows))
 if denominator>plan['rule']['maximum_unresolved_at_final']:
  result['reasons']=sorted(set(result['reasons'])|{'UNRESOLVED_OR_CENSORED_ORIGINAL_COMMANDS'})
 result.update(original_command_count=denominator,observed_trades=0,status_counts={'CENSORED':denominator} if denominator else {},
               prediction_coverage=0.,current_replay_complete=False,original_command_count_complete=False,
               denominator_scope='RETAINED_KNOWN_COMMANDS_AND_PRIOR_COUNT_LOWER_BOUND',
               retained_known_command_count=len(rows),retained_unenumerated_command_count=denominator-len(rows),
               strict_preopen_terminal_required=True,last_completed_assessment=prior,outcome_set_digest=W.digest(rows),
               source_record_count=None,source_ledger_prefix=None,nontrade_requests=None,
               original_window_modified=False,source_failure={'type':type(error).__name__,'message':str(error)[:2000]})
 for row in rows:
  folder=root/'outcomes'/row['command_id'];record_id=W.digest(row)
  W.durable_once(folder/(record_id+'.json'),row);W.atomic(folder/'current.json',{'record_id':record_id})
 if now>=plan['outcome_end_ms']:
  receipt={'schema':STRICT_FAILURE_SCHEMA,'plan_id':plan['plan_id'],'outcome_end_ms':plan['outcome_end_ms'],
           'as_of_ms':now,'assessment':result,'original_window_modified':False,'broker_orders_sent':0}
  W.durable_once(root/'strict-terminal-unavailable.json',receipt)
 W.atomic(path,result);return result

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
 parser.add_argument('--register',type=Path);parser.add_argument('--once',action='store_true')
 parser.add_argument('--strict-preopen',action='store_true');parser.add_argument('--output-root',type=Path)
 args=parser.parse_args();os.umask(0o077)
 if args.register:
  W.require(not args.strict_preopen and args.output_root is None,'SHADOW_REVIEW_CANNOT_REGISTER')
  print(json.dumps(W.freeze(W.read(args.register),args.root)));return
 worker=None
 if args.strict_preopen:
  W.require(args.output_root is not None and args.output_root.resolve()!=args.root.resolve(),'SHADOW_STRICT_REVIEW_SEPARATE_OUTPUT')
  output_root=args.output_root;plan=retained_review_plan(args.root,output_root);assessment_name='assessment-strict.json'
 else:
  worker=Evaluator(args.root,output_root=args.output_root);output_root=worker.root;plan=worker.plan;assessment_name=worker.assessment_name
 fd=os.open(output_root/'evaluator.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);stopping=False
 def stop(*_):
  nonlocal stopping
  stopping=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 try:
  while not stopping:
   final_review=False
   try:
    terminal=read_strict_terminal(output_root,plan) if args.strict_preopen else None
    if terminal is not None:
     result=terminal['assessment'];W.atomic(output_root/assessment_name,result)
    else:
     if worker is None:worker=Evaluator(args.root,output_root=output_root,strict_preopen=True)
     result=worker.step()
    final_review=bool(args.strict_preopen and result.get('as_of_ms',0)>=plan['outcome_end_ms'] and result.get('state') in ('FINAL_INCOMPLETE','FINAL_RESEARCH_COMPARISON'))
    status={k:result[k] for k in ('plan_id','as_of_ms','state','observed_trades','original_command_count','reasons')}
    status.update(at_ms=W.clock_ms(),financial_candidate_selected=False,broker_orders_sent=0,replay_skipped=result.get('replay_skipped',False))
   except Exception as exc:
    now=W.clock_ms();status={'state':'UNAVAILABLE_NO_SELECTION','at_ms':now,'reason':str(exc)[:220],'financial_candidate_selected':False,'broker_orders_sent':0}
    if args.strict_preopen:
     if worker is not None:worker.last_identity=None;worker.last_result=None
     result=strict_source_unavailable(output_root,plan,exc,now)
     status.update({k:result[k] for k in ('plan_id','as_of_ms','observed_trades','original_command_count','reasons','current_replay_complete','original_command_count_complete')})
     if now>=plan['outcome_end_ms']:status['state']=result['state'];final_review=True
   status['assessment_path']=str(output_root/assessment_name)
   W.atomic(output_root/'evaluator-status.json',status);print(json.dumps(status),flush=True)
   if args.once or final_review:break
   for _ in range(60 if args.strict_preopen else 10):
    if stopping:break
    time.sleep(1)
 finally:os.close(fd)

if __name__=='__main__':main()
