"""Independent current-ledger monetary evaluation; no torch and no optimizer.

Permanent original-command denominator; disappearing provenance or corrected
training labels cannot remove bad cases or retain a stale qualification.
"""
from __future__ import annotations
import argparse,fcntl,hashlib,json,os,signal,time
from pathlib import Path
from .integrity import ContractError,read_json,sha_file,digest
_original_sha_file=sha_file
def sha_file(path):return _original_sha_file(Path(path))
from .resident_store import write_once,atomic
from .resident_rewards import current
from .financial_forward_source import ingress
from .financial_forward_protocol import RULE,validate_plan,score_case,assessment
from .financial_forward_witness import verify_record,sign_record


def execution_index(source):
 from hepta_decision_attribution import read_private,strict
 # The original ledger is append-only: its full size can exceed the frozen
 # prefix. Keep a hard private-file bound without the obsolete 32 MiB ceiling.
 # Do not weaken owner/mode/symlink checks or prefix SHA256 verification.
 from hepta_decision_attribution import MAX_TOTAL
 if type(source.get('bytes')) is not int or not 0 <= source['bytes'] <= MAX_TOTAL:
  raise ContractError('FINANCIAL_LEDGER_PREFIX_SIZE')
 raw=read_private(source['path'],MAX_TOTAL,prefix_bytes=source['bytes'])
 if len(raw)!=source['bytes'] or hashlib.sha256(raw).hexdigest()!=source['sha256']:raise ContractError('FINANCIAL_LEDGER_PREFIX_CHANGED')
 by_id={};by_command={};execution_commands={}
 for line in raw.splitlines():
  r=strict(line)
  if r.get('event')!='broker_execution' or not r.get('broker_execution_id') or type(r.get('ts_ms'))is not int:continue
  eid=r['broker_execution_id'];at=r['ts_ms'];by_id[eid]=min(by_id.get(eid,at),at)
  if r.get('execution_domain')=='PAPER' and r.get('instrument')=='EUR.USD' and r.get('req_id'):
   command=r['req_id'];by_command[command]=min(by_command.get(command,at),at)
   old=execution_commands.get(eid)
   if old is not None and old!=command:raise ContractError('EXECUTION_COMMAND_CHANGED')
   execution_commands[eid]=command
 return by_id,by_command,execution_commands


def first_execution_times(source):return execution_index(source)[0]


def read_prediction(root,plan,request_id,config=None):
 folder=root/'plans'/plan['plan_id']/'predictions'/request_id
 if not (folder/'commit.json').exists():return None
 commit=read_json(folder/'commit.json');payload=read_json(folder/'prediction.json')
 if config is None:raise ContractError('SIGNED_PREDICTOR_TRUST_REQUIRED')
 verify_record(config,'predictor',commit)
 if (sha_file(folder/'prediction.json')!=commit['prediction_sha256'] or commit['plan_id']!=plan['plan_id']
     or commit['request_id']!=request_id or payload['request_id']!=request_id
     or commit['candidate_id']!=plan['candidate_id'] or payload['candidate_id']!=plan['candidate_id']
     or commit['role_witness']['signed_at_ms']<commit['committed_at_ms']):raise ContractError('PREDICTION_COMMIT_WITNESS')
 # Conservative persistence witness: use the signature's later actual timestamp,
 # not a claimed earlier computed timestamp, to compare with PREPARED and fills.
 return {**payload,'committed_at_ms':commit['role_witness']['signed_at_ms'],
         'prediction_sha256':commit['prediction_sha256'],'commit_sha256':sha_file(folder/'commit.json')}


def validate_registration(config,path,plan):
 validate_plan(plan,int(time.time()*1000));seal=read_json(path.parent/'registration.json')
 verify_record(config,'registrar',seal)
 if seal['plan_file_sha256']!=sha_file(path) or seal['plan_id']!=plan['plan_id']:
  raise ContractError('FORWARD_REGISTRATION_CHANGED')
 if plan.get('witness_trust_digest')!=config['witness_trust_digest']:raise ContractError('FORWARD_TRUST_SUBSTITUTED')
 if seal['role_witness']['signed_at_ms']>=plan['input_start_ms']:raise ContractError('PLAN_SIGNED_AFTER_INPUT_START')
 for name,value in plan['runtime_sha256'].items():
  if sha_file(Path(__file__).parent/name)!=value:raise ContractError('FROZEN_EVALUATOR_RUNTIME_CHANGED')
 from .financial_forward_protocol import validate_candidate
 c=validate_candidate(Path(plan['candidate_directory']))
 if c['candidate_id']!=plan['candidate_id'] or sha_file(Path(plan['candidate_directory'])/'candidate.json')!=plan['candidate_manifest_sha256']:
  raise ContractError('CANDIDATE_SUBSTITUTION')
 if (c.get('schema')=='heptatrader.current100k-value-candidate.v1')!=(plan.get('schema')=='heptatrader.current100k-value-forward-plan.v1'):
  raise ContractError('VALUE_CANDIDATE_AND_PLAN_BEHAVIOR_MISMATCH')
 if plan.get('schema')=='heptatrader.current100k-value-forward-plan.v1':
  from .current100k_forward import validate_bound_plan
  validate_bound_plan(plan,c)
 return c


def baseline_missing_case(plan,command,opened,now,reason):
 return {'schema':'heptatrader.financial-forward-outcome.v1','candidate_id':plan['candidate_id'],'plan_id':plan['plan_id'],
  'command_id':command,'request_id':None,'opened_at_ms':opened,'observed_at_ms':now,'status':'CENSORED',
  'reason':reason,'actual_execution_sample':True,'policy_gradient_allowed':False}


class Evaluator:
 def __init__(self,config):self.c=config;self.root=Path(config['forward_root']);self.stop=False;self.log_at=0
 def step(self):
  if sha_file(Path(self.c['period_path']))!=self.c['period_sha256']:raise ContractError('FINANCIAL_EVAL_PERIOD_CHANGED')
  now=int(time.time()*1000);report=current(self.c,now)
  reward_by_cmd={r['open_command_id']:r for r in report.get('rewards',[])}
  known_by_cmd={r['open_command_id']:r for r in report.get('execution_rewards',[]) if r.get('instrument')=='EUR.USD'}
  fill_times,command_fills,execution_commands=execution_index(report['source_prefix']) if report.get('source_prefix') else ({},{},{})
  summaries=[]
  for path in sorted((self.root/'plans').glob('*/plan.json')):
   plan=read_json(path);issues=[]
   if plan['period_sha256']!=self.c['period_sha256']:raise ContractError('PLAN_PERIOD_SCOPE')
   validate_registration(self.c,path,plan)
   try:_,requests,bindings,source_issues=ingress(plan['producer_registration'],self.c['period_sha256']);issues+=source_issues
   except Exception as exc:requests={};bindings=[];issues.append({'reason':'PROVENANCE_UNAVAILABLE:'+str(exc)[:160]})
   eligible={b['command']['command_id']:b for b in bindings if plan['input_start_ms']<=b['request_started_at_ms']<plan['input_end_ms']}
   seen_root=path.parent/'seen-commands';seen_root.mkdir(mode=0o700,parents=True,exist_ok=True)
   for cmd,b in eligible.items():
    if Path(cmd).name!=cmd:raise ContractError('COMMAND_PATH')
    write_once(seen_root/(cmd+'.json'),b)
   seen={p.stem:read_json(p) for p in seen_root.glob('*.json')}
   results=[]
   for cmd,b in seen.items():
    known=known_by_cmd.get(cmd);reward=reward_by_cmd.get(cmd);prediction=None
    try:prediction=read_prediction(self.root,plan,b['request_id'],self.c)
    except Exception as exc:issues.append({'command_id':cmd,'reason':'PREDICTION_REVOKED:'+str(exc)[:160]})
    times=[fill_times[x['execution_id']] for x in (known or {}).get('executions',[]) if x['command_id']==cmd and x['execution_id'] in fill_times]
    first=min(times) if times else command_fills.get(cmd)
    if plan.get('schema')=='heptatrader.current100k-value-forward-plan.v1':
     from .current100k_forward import verify_scored_row
     verify_scored_row(plan,b,prediction,reward,requests.get(b['request_id']))
    result=score_case(plan,b,prediction,reward,first if first is not None else 2**62,now)
    if cmd not in eligible:result.update(status='CENSORED',reason='PREVIOUSLY_SEEN_COMMAND_BINDING_WITHDRAWN')
    elif known is not None and reward is None:result.update(status='CENSORED',reason='CURRENT_REPLAY_REVOKED_OR_UNSETTLED_EXACT_REWARD')
    elif reward is not None and first is None:result.update(status='CENSORED',reason='ACTUAL_FIRST_FILL_WITNESS_MISSING')
    elif reward is not None and max((fill_times.get(x['execution_id'],2**62) for x in reward['executions']),default=2**62)>plan['outcome_end_ms']:
     result.update(status='CENSORED',reason='CLOSE_AFTER_FIXED_EVALUATION_GRACE')
    if now>=plan['outcome_end_ms'] and result['status']=='PENDING':result.update(status='CENSORED',reason='NOT_CLOSED_WITHIN_FIXED_EVALUATION_GRACE')
    if prediction is not None:result.update(prediction_sha256=prediction['prediction_sha256'],commit_sha256=prediction['commit_sha256'])
    results.append(result)
   # A known actual closed FX episode with lost input cannot silently disappear
   # from the denominator. It gets NO score, just a coverage failure.
   for cmd,known in known_by_cmd.items():
    start=command_fills.get(cmd)
    if cmd not in seen and start is not None and plan['input_start_ms']<=start<plan['input_end_ms']:
     results.append(baseline_missing_case(plan,cmd,start,now,'ACTUAL_OPEN_WITHOUT_EXACT_PLAN_BINDING'))
   # Keep genuine unclosed exposure with lost attribution in coverage.
   for blocked in report.get('blocked',[]):
    if blocked.get('instrument')!='EUR.USD' or blocked.get('reason')!='OPEN_RESIDUAL_POSITION':continue
    for eid in blocked.get('execution_ids',[]):
     start=fill_times.get(eid)
     if start is None or not plan['input_start_ms']<=start<plan['input_end_ms']:continue
     original=execution_commands.get(eid)
     if original is not None and original in seen:continue
     marker='unattributed-execution-'+digest(eid)
     if not any(x['command_id']==marker for x in results):
      results.append(baseline_missing_case(plan,marker,start,now,'ACTUAL_OPEN_UNCLOSED_WITHOUT_EXACT_INPUT'))
   outcome_record_ids=[]
   for result in results:
    semantic=digest({k:v for k,v in result.items() if k!='observed_at_ms'});cmd=result['command_id']
    destination=path.parent/'outcomes'/cmd/(semantic+'.json')
    if destination.exists():
     old=read_json(destination)
     if old.get('semantic_id')!=semantic:raise ContractError('OUTCOME_REVISION_CONFLICT')
    else:
     record={**result,'source_ledger_prefix':report.get('source_prefix'),'semantic_id':semantic}
     record['record_id']=digest(record);write_once(destination,record)
    record=read_json(destination);atomic(path.parent/'outcomes'/cmd/'current.json',{'record_id':record['record_id'],'path':str(destination)})
    outcome_record_ids.append(record['record_id'])
   evaluation=assessment(plan,results,now)
   if issues:
    evaluation['source_issues']=issues;evaluation['state']='REJECTED' if now>=plan['outcome_end_ms'] else 'PENDING'
    evaluation['reasons'].append('SOURCE_LOSS_OR_ATTRIBUTION_UNAVAILABLE')
   missing=set(plan['training_reward_ids'])-set(r['reward_id'] for r in report.get('rewards',[]))
   if missing:
    evaluation['state']='REVOKED';evaluation['reasons'].append('TRAINING_REWARD_CORRECTED_OR_WITHDRAWN')
    evaluation['invalidated_training_reward_ids']=sorted(missing)
   observed_request_ids={b['request_id'] for b in eligible.values()}
   evaluation['nontrade_requests']=sum(plan['input_start_ms']<=x['request']['started_at_ms']<plan['input_end_ms'] and x['request']['position_at_request']==0 and rid not in observed_request_ids for rid,x in requests.items())
   evaluation['financial_candidate_selected']=False;evaluation['canonical_agentd_accepted']=False
   evaluation['current_ledger_prefix']=report.get('source_prefix');evaluation['outcome_set_digest']=digest([{k:v for k,v in x.items() if k!='observed_at_ms'} for x in results])
   evaluation['outcome_record_ids']=outcome_record_ids
   evaluation['actual_owner_acceptance_required']=True
   signed=sign_record(self.c,'evaluator',evaluation)
   atomic(path.parent/'assessment.json',evaluation);atomic(path.parent/'assessment-signed.json',signed);summaries.append(evaluation)
  status={'schema':'heptatrader.financial-forward-evaluator-status.v2','at_ms':now,'plans':len(summaries),
   'observed_trades':sum(x['observed_trades'] for x in summaries),
   'states':{state:sum(x['state']==state for x in summaries) for state in sorted({x['state'] for x in summaries})},
   'original_command_count':sum(x['total_original_trades'] for x in summaries),
   'canonical_agentd_accepted':False,'financial_candidate_selected':False,'actor_updated':False,'broker_orders_sent':0}
  atomic(self.root/'evaluator-status.json',status)
  if now-self.log_at>30000:print(json.dumps(status),flush=True);self.log_at=now
  return status


def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();os.umask(0o077)
 c=read_json(a.config);root=Path(c['forward_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
 fd=os.open(root/'evaluator.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 worker=Evaluator(c)
 def stop(*_):worker.stop=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 while not worker.stop:
  try:worker.step()
  except Exception as exc:atomic(root/'evaluator-status.json',{'state':'UNAVAILABLE_NO_SELECTION','at_ms':int(time.time()*1000),'reason':str(exc)[:220]})
  if a.once:break
  for _ in range(10):
   if worker.stop:break
   time.sleep(1)
 os.close(fd)
if __name__=='__main__':main()
