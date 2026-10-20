"""Resident candidate scheduler. No trading methods and no self-issued promotion.

New mature-row fingerprints, durable job admission, bounded child processes and
immutable candidate outputs survive restart. The bootstrap trainer stays off.
"""
from __future__ import annotations
import sqlite3
import argparse,fcntl,json,os,signal,subprocess,sys,time
from pathlib import Path
from .integrity import ContractError,read_json,digest,sha_file,canonical
from .resident_data import collect
from .resident_rewards import current as rewards_current
from .resident_reward_cache import RewardRefreshGuard, FULL_REVERIFY_SECONDS
from .resident_store import database,write_once,atomic,get_meta,put_meta,admit_job,complete_job
from .promotion import RULE,rule_for_trial
from .promotion_power import PROTOCOL
from .resident_budget import policy as budget_policy,inspect as inspect_storage
from .resident_row_accounting import SCHEMA as ROW_ACCOUNTING_SCHEMA,select_rows
from . import resident_training_v2 as training_v2
from . import resident_training_v3 as training_v3
from . import resident_price_baselines as price_refs
from .resident_queue import inspect as inspect_evaluation_queue

# No torch import in the resident scheduler; GPU memory belongs to bounded children.
MIN_CADENCE_MS=900000
MAX_PENDING_EVALUATIONS=2
REWARD_SYNC_INTERVAL_SECONDS=60


def config_check(config):
 if (config.get('schema')!='heptatrader.laya-resident-config.v1' or config.get('period_sha256') is None
     or config.get('training_cadence_ms')!=MIN_CADENCE_MS or config.get('automatic_trading') is not False):
  raise ContractError('UNREVIEWED_RESIDENT_CONFIGURATION')
 if sha_file(Path(config['period_path']))!=config['period_sha256']:raise ContractError('ACCOUNTING_PERIOD_CHANGED')
 if sha_file(Path(config['native_binary']))!=config['native_binary_sha256']:raise ContractError('NATIVE_BINARY_CHANGED')
 for rel,expected in config.get('runtime_sha256',{}).items():
  if Path(rel).is_absolute() or '..' in Path(rel).parts:raise ContractError('RUNTIME_PATH_ESCAPE')
  if sha_file(Path(config['runtime_directory'])/rel)!=expected:raise ContractError('RUNTIME_CHANGED:'+rel)
 if training_v2.enabled(config.get('objective')):
  if config['objective']!=training_v2.OBJECTIVE:raise ContractError('RESIDENT_V2_OBJECTIVE_CHANGED')
  if any(not config.get('runtime_sha256',{}).get(p) for p in training_v2.RUNTIME_PINS):
   raise ContractError('RESIDENT_V2_RUNTIME_PINS_REQUIRED')
 if training_v3.enabled(config.get('objective')):
  if config['objective']!=training_v3.OBJECTIVE:raise ContractError('RESIDENT_V3_OBJECTIVE_CHANGED')
  if any(not config.get('runtime_sha256',{}).get(p) for p in training_v3.RUNTIME_PINS):
   raise ContractError('RESIDENT_V3_RUNTIME_PINS_REQUIRED')
 budget_policy(config)
 if config.get('promotion_protocol') not in (None,PROTOCOL):raise ContractError('UNKNOWN_PROMOTION_PROTOCOL')
 if config.get('future_source_read_profile') is not None:
  from .resident_source_policy import RECENT,RUNTIME_PIN
  if config['future_source_read_profile']!=RECENT:raise ContractError('UNREVIEWED_FUTURE_SOURCE_PROFILE')
  if any(config.get('runtime_sha256',{}).get(rel) is None for rel in RUNTIME_PIN):
   raise ContractError('FUTURE_SOURCE_RELEASE_HASHES_REQUIRED')
 if config.get('future_plan_admission_profile') is not None:
  from .resident_plan_admission import check_config as check_forward_plan_config
  check_forward_plan_config(config)
 return config


def bootstrap_still_off():
 p=subprocess.run(['systemctl','--user','show','heptatrader-neuron-learning.service','-p','ActiveState','-p','MainPID','-p','UnitFileState'],capture_output=True,text=True,timeout=5)
 fields=dict(x.split('=',1) for x in p.stdout.splitlines() if '=' in x)
 if p.returncode or fields.get('ActiveState')!='inactive' or fields.get('MainPID')!='0' or fields.get('UnitFileState') not in ('disabled','masked'):
  raise ContractError('BOOTSTRAP_NOT_FROZEN')


class Supervisor:
 def __init__(self,config_path):
  self.path=Path(config_path);self.c=config_check(read_json(self.path));self.root=Path(self.c['control_directory'])
  self.root.mkdir(parents=True,mode=0o700,exist_ok=True);self.db=database(self.root/'scheduler.sqlite')
  self.stop=False;self.child=None;self.storage=None
 def stop_signal(self,*_):
  self.stop=True
  if self.child and self.child.poll() is None:self.child.terminate()
 def _latest_candidate_registration(self,candidate_id):
  """Describe the latest completed artifact without collecting data or allocating work."""
  if not candidate_id or self.c.get('future_plan_admission_profile') is None:return None
  result={'candidate_id':candidate_id,'registration_is_performance_result':False}
  try:
   rows=self.db.execute("SELECT id,payload FROM jobs WHERE status='COMPLETED' AND candidate_id=? LIMIT 2",(candidate_id,)).fetchall()
   if len(rows)!=1:raise ContractError('LATEST_CANDIDATE_JOB_BINDING')
   job_id=rows[0]['id'];job=json.loads(rows[0]['payload'])
   if not isinstance(job,dict):raise ContractError('LATEST_CANDIDATE_JOB_SHAPE')
   result.update(job_id=job_id,parent_id=job.get('parent_id'),training_state='COMPLETED')
   from .resident_plan_admission import KEY_PREFIX,_identity,_check_record
   record=get_meta(self.db,KEY_PREFIX+job_id)
   folder=Path(self.c['evaluation_directory'])/job_id
   if record is None:
    return {**result,'state':'REGISTRATION_RECORD_NOT_AVAILABLE','plan_file_exists':(folder/'plan.json').exists()}
   candidate=read_json(Path(job['output_directory'])/'candidate.json')
   if not isinstance(candidate,dict):raise ContractError('LATEST_CANDIDATE_MANIFEST_SHAPE')
   _check_record(record,_identity(self,job_id,candidate,job))
   result.update(state=record['state'],reason=record.get('reason'),
     registration_id=record['registration_id'],checked_at_ms=record['checked_at_ms'],
     candidate_window=record['candidate_window'],trial_allocated=record['trial_allocated'])
   if record['state']=='NO_FORWARD_TRIAL':
    if (folder/'plan.json').exists():raise ContractError('FORWARD_TERMINAL_PLAN_CONFLICT')
    receipt=folder/'no-forward-trial.json'
    if not receipt.exists():
     return {**result,'state':'TERMINAL_RECEIPT_PUBLICATION_PENDING','recorded_state':record['state']}
    if read_json(receipt)!=record:raise ContractError('FORWARD_TERMINAL_RECEIPT_CHANGED')
    result.update(receipt_path=str(receipt),receipt_publication_verified=True,
      future_observations=0,registration_retryable=False,next_action='NEW_CANDIDATE_AFTER_TRAINING_ADMISSION')
   elif record['trial_allocated']:
    result.update(plan_id=record['plan']['plan_id'],trial=record['plan']['trial'],
      plan_start_ms=record['plan']['start_ms'],plan_outcome_end_ms=record['plan']['outcome_end_ms'])
   calendar=record.get('calendar')
   if isinstance(calendar,dict):
    result['calendar']={k:calendar[k] for k in ('reason','earliest_feasible_start_ms','latest_feasible_start_ms','plan_interval_ms') if k in calendar}
   return result
  except (ContractError,OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
   return {'candidate_id':candidate_id,'state':'REGISTRATION_EVIDENCE_UNAVAILABLE',
     'reason':str(exc),'registration_is_performance_result':False}

 def write_status(self,*,storage=None,**extra):
  self.storage=inspect_storage(self.c) if storage is None else storage
  stats={s:self.db.execute('SELECT count(*) FROM jobs WHERE status=?',(s,)).fetchone()[0] for s in ('ADMITTED','RUNNING','COMPLETED','FAILED')}
  candidate_id=extra.get('last_candidate_id',get_meta(self.db,'last_completed_candidate'))
  latest_registration=self._latest_candidate_registration(candidate_id)
  state={'schema':'heptatrader.laya-resident-status.v1','at_ms':int(time.time()*1000),'job_counts':stats,
    'last_candidate_id':candidate_id,'historical_confirmed_pnl_rewards':self.db.execute('SELECT count(*) FROM rewards').fetchone()[0],
    'active_pnl_reward_count':len(get_meta(self.db,'active_reward_ids',[])),
    'active_execution_reward_count':len(get_meta(self.db,'active_execution_reward_ids',[])),
    'execution_reward_is_not_model_credit':True,
    'reward_sync_at_ms':get_meta(self.db,'last_reward_sync_ms'),
    'reward_sync_interval_seconds':REWARD_SYNC_INTERVAL_SECONDS,
     'reward_full_reverify_interval_seconds':FULL_REVERIFY_SECONDS,
     'reward_refresh_guard':getattr(self,'_reward_refresh_guard',None).status() if getattr(self,'_reward_refresh_guard',None) else None,
    'consumed_rows_semantics':'RESERVED_NOT_PROOF_OF_GRADIENT','training_lineage':{'mode':'INDEPENDENT_CANDIDATES_FROM_CONFIGURED_PARENT','configured_parent_directory':self.c.get('initial_parent_directory'),'optimizer_reset_each_job':True,'completed_jobs_are_cumulative_updates':False},
    'training_cadence_seconds':900,'old_mlp_training_enabled':False,'automatic_trading':False,
    'canonical_agentd_owner_connected':False,'automatic_promotion':'EVALUATION_AND_HANDOFF_ONLY_UNTIL_CANONICAL_OWNER',
    'broker_orders_sent':0,'storage':self.storage,**getattr(self,'_evaluation_queue_status',{}),**extra}
  if latest_registration is not None:state['latest_candidate_registration']=latest_registration
  atomic(self.root/'status.json',state);print(json.dumps(state),flush=True);return state
 def candidate_done(self,row):
  job=json.loads(row['payload']);path=Path(job['output_directory'])/'candidate.json'
  if not path.exists():return False
  candidate=read_json(path)
  if candidate['candidate_id']!=digest({k:v for k,v in candidate.items() if k!='candidate_id'}):raise ContractError('CHILD_CANDIDATE_HASH')
  if (sha_file(path.parent/'adapter.safetensors')!=candidate['adapter_sha256'] or candidate['data_fingerprint']!=row['fingerprint'] or candidate['parent_id']!=job['parent_id']
      or candidate.get('dataset_file_sha256')!=job['dataset_sha256'] or candidate.get('native_binary_sha256')!=job['native_binary_sha256']):
   raise ContractError('CHILD_RESULT_IDENTITY')
  complete_job(self.db,row['id'],candidate)
  self.register_evaluation(row['id'],candidate,job)
  return True
 def _evaluation_plan(self,job_id,candidate,job,trial,now):
  rule=rule_for_trial(trial,self.c.get('promotion_protocol'));start=now+120000
  plan={'schema':'heptatrader.laya-resident-future-plan.v1','job_id':job_id,'candidate_id':candidate['candidate_id'],
   'candidate_directory':job['output_directory'],'parent_directory':job['parent_directory'],'parent_id':job['parent_id'],
   'created_at_ms':now,'start_ms':start,'outcome_end_ms':start+(rule['slots']-1)*rule['slot_spacing_ms']+rule['input_grace_ms']+rule['horizon_ms']+5000,
   'trial':trial,'rule':rule,'scope':self.c['period_sha256'],'no_refit':True,'broker_orders_sent':0,
   'evaluation_runtime_sha256':{n:self.c.get('runtime_sha256',{}).get(n) for n in ('learn/laya_cell/resident_evaluate.py','learn/laya_cell/resident_predict.py','learn/laya_cell/promotion.py','learn/laya_cell/promotion_power.py','learn/laya_cell/resident_data.py','learn/laya_cell/shadow_source.py')},
   'candidate_manifest_sha256':sha_file(Path(job['output_directory'])/'candidate.json')}
  if self.c.get('future_source_read_profile') is not None:
   from .resident_source_policy import RECENT,RUNTIME_PIN
   if self.c['future_source_read_profile']!=RECENT:raise ContractError('UNREVIEWED_FUTURE_SOURCE_PROFILE')
   plan['future_source_read_profile']=RECENT
   plan['future_outcome_read_profile']='ARCHIVE_VERIFIED_PENDING_OUTCOME_V1'
   for name in RUNTIME_PIN:
    pinned=self.c.get('runtime_sha256',{}).get(name)
    if pinned is None:raise ContractError('FUTURE_SOURCE_RELEASE_HASHES_REQUIRED')
    plan['evaluation_runtime_sha256'][name]=pinned
  training_policy=training_v3 if training_v3.enabled(candidate.get('objective')) else training_v2
  if training_policy.enabled(candidate.get('objective')):
   plan['price_baselines']=price_refs.validate(candidate.get('price_baselines'))
   plan['action_output_profile']=training_v2.ACTION_OUTPUT
   calibration=candidate.get('action_training_profile')
   plan['action_training_profile_id']=calibration['profile_id'] if calibration else None
   plan['action_optimizer_admitted']=bool(candidate.get('action_optimizer_admitted'))
   for name in training_policy.RUNTIME_PINS:
    expected=self.c.get('runtime_sha256',{}).get(name)
    if not expected:raise ContractError('RESIDENT_V2_RUNTIME_PINS_REQUIRED')
    plan['evaluation_runtime_sha256'][name]=expected
  if training_v3.enabled(candidate.get('objective')):
   plan['price_value_rule']={**price_refs.VALUE_RULE,'comparators':list(price_refs.VALUE_RULE['comparators'])}
  return plan
 def _write_evaluation_handoff(self,candidate,job,writer=write_once):
  # This is an unadmitted handoff, not a canonical fact or owner-issued authority.
  request={'schema':'heptatrader.unadmitted-laya-owner-handoff.v1','candidate_id':candidate['candidate_id'],
   'candidate_manifest_sha256':sha_file(Path(job['output_directory'])/'candidate.json'),'dataset_id':candidate['dataset_id'],
   'parent_id':candidate['parent_id'],'native_proposal_digest':candidate['plasticity']['proposal_digest'],
   'required_owners':['learning.ledger','learning.operator','learning.artifacts','qualification','runtime.agentd'],
   'state':'UNADMITTED_CANONICAL_OWNER_NOT_ENROLLED','canonical_fact':False,'effect_authority':False}
  writer(self.root/'owner-outbox'/(candidate['candidate_id']+'.json'),request)
 def register_evaluation(self,job_id,candidate,job):
  folder=Path(self.c['evaluation_directory'])/job_id;folder.mkdir(parents=True,mode=0o700,exist_ok=True)
  path=folder/'plan.json'
  if self.c.get('future_plan_admission_profile') is not None:
   # The policy handles existing plans/reservations before checking NEW admission.
   from .resident_plan_admission import register
   return register(self,job_id,candidate,job)
  if path.exists():
   old=read_json(path)
   if old['candidate_id']!=candidate['candidate_id']:raise ContractError('EVALUATION_CANDIDATE_CONFLICT')
   return
  if inspect_evaluation_queue(self.c)['pending_original_trials']:
   return
  with self.db:
   trial=get_meta(self.db,'evaluation_trial',0)+1;put_meta(self.db,'evaluation_trial',trial)
  plan=self._evaluation_plan(job_id,candidate,job,trial,int(time.time()*1000))
  plan['plan_id']=digest(plan);write_once(path,plan)
  self._write_evaluation_handoff(candidate,job)
 def recover(self):
  if self.c.get('promotion_protocol')==PROTOCOL:
   floor=self.c.get('promotion_legacy_trial_floor')
   if type(floor)is not int or floor<1:raise ContractError('PROMOTION_MIGRATION_FLOOR_REQUIRED')
   current=get_meta(self.db,'evaluation_trial',0)
   trials=[read_json(p)['trial'] for p in Path(self.c['evaluation_directory']).glob('*/plan.json')]
   if current<floor or not trials or max(trials)>current or max(trials)<floor or len(set(trials))!=len(trials):
    raise ContractError('PROMOTION_TRIAL_HISTORY_RESET_OR_CONFLICT')
  for row in self.db.execute("SELECT * FROM jobs WHERE status IN ('ADMITTED','RUNNING','COMPLETED')").fetchall():
   if self.candidate_done(row):continue
   if row['status']=='COMPLETED':raise ContractError('COMMITTED_CANDIDATE_MISSING')
   # Lost children are not blindly restarted over partially written candidates.
   with self.db:self.db.execute('UPDATE jobs SET status=? WHERE id=?',('FAILED',row['id']))
   write_once(self.root/'failures'/(row['id']+'.json'),{'reason':'INTERRUPTED_JOB_NO_COMMITTED_CANDIDATE','job_id':row['id'],'rows_remain_consumed':True})
 def _waiting_evaluations(self):
  rows=[row for row in self.db.execute("SELECT * FROM jobs WHERE status='COMPLETED' ORDER BY created_ms").fetchall()
        if not (Path(self.c['evaluation_directory'])/row['id']/'plan.json').exists()]
  if self.c.get('future_plan_admission_profile') is None:return rows,{}
  from .resident_plan_admission import waiting_candidates
  return waiting_candidates(self,rows)
 def _finish_impossible_forward_registration(self,row):
  """Finish an impossible unallocated calendar window without starting a trial."""
  from .resident_plan_admission import (
    KEY_PREFIX,_identity,_check_record,_unreserved_history,_sealed,_publish)
  from .resident_plan_calendar import calendar_admission
  job=json.loads(row['payload']);key=KEY_PREFIX+row['id']
  candidate=read_json(Path(job['output_directory'])/'candidate.json')
  folder=Path(self.c['evaluation_directory'])/row['id']
  if self.db.in_transaction:raise ContractError('FORWARD_CALENDAR_TRANSACTION_SCOPE')
  record=None
  self.db.execute('BEGIN IMMEDIATE')
  try:
   identity=_identity(self,row['id'],candidate,job)
   previous=get_meta(self.db,key)
   if previous is not None:
    _check_record(previous,identity)
    if not (folder/'plan.json').exists():
     if previous['state']=='NO_FORWARD_TRIAL':
      # Complete a DB-first publication with its original identity and time.
      record=previous
     elif previous['state']=='DEFERRED':
      checked=int(time.time()*1000)
      if checked<self.c['future_plan_admission_effective_at_ms']:
       raise ContractError('FORWARD_CALENDAR_BEFORE_POLICY_EFFECTIVE')
      if checked<previous['checked_at_ms']:
       raise ContractError('FORWARD_CALENDAR_CLOCK_REGRESSION')
      current=_unreserved_history(self)
      proposal=self._evaluation_plan(row['id'],candidate,job,current+1,checked)
      decision=calendar_admission(candidate,proposal,checked)
      if decision['state']=='NO_FORWARD_TRIAL':
       record=_sealed(identity,'NO_FORWARD_TRIAL',checked,
         reason=decision['reason'],calendar=decision)
       put_meta(self.db,key,record)
   self.db.commit()
  except BaseException:
   self.db.rollback();raise
  if record is None:return False
  folder.mkdir(parents=True,mode=0o700,exist_ok=True)
  _publish(self,key,record,candidate,job,folder)
  return True
 def _closed_registration_unchanged(self,row):
  """Negative-only wait: recheck today's calendar, preserve the old policy receipt."""
  from .resident_plan_admission import KEY_PREFIX,_identity,_check_record,_unreserved_history
  from .resident_plan_calendar import calendar_admission
  record=get_meta(self.db,KEY_PREFIX+row['id'])
  if not isinstance(record,dict) or record.get('state')!='DEFERRED' or record.get('reason')!='MARKET_SESSION_CLOSED':return False
  job=json.loads(row['payload']);candidate=read_json(Path(job['output_directory'])/'candidate.json')
  _check_record(record,_identity(self,row['id'],candidate,job))
  checked=int(time.time()*1000)
  if checked<max(record['checked_at_ms'],self.c['future_plan_admission_effective_at_ms']):
   raise ContractError('FORWARD_CALENDAR_CLOCK_REGRESSION')
  current=_unreserved_history(self)
  proposal=self._evaluation_plan(row['id'],candidate,job,current+1,checked)
  decision=calendar_admission(candidate,proposal,checked)
  if decision.get('state')!='DEFERRED' or decision.get('reason')!='MARKET_SESSION_CLOSED' or decision.get('ready') is not False:return False
  previous=record.get('calendar')
  if not isinstance(previous,dict):return False
  def business(value):return {k:v for k,v in value.items() if k not in ('now_ms','plan_interval_ms')}
  if business(previous)!=business(decision):return False
  path=Path(self.c['evaluation_directory'])/row['id']/'registration-status.json'
  try:return path.exists() and read_json(path)==record
  except (ContractError,ValueError,OSError):return False
 def reconcile_evaluation_queue(self):
  if self.c.get('future_plan_admission_profile') is not None:
   from .resident_plan_admission import resume_reservations
   resume_reservations(self)
  queue=inspect_evaluation_queue(self.c)
  waiting,registration=self._waiting_evaluations()
  if (self.c.get('future_plan_admission_profile') is not None and waiting
      and queue['pending_original_trials']
      and self._finish_impossible_forward_registration(waiting[0])):
   waiting,registration=self._waiting_evaluations()
  if (waiting and queue['pending_original_trials']==0
      and not (self.c.get('future_plan_admission_profile') is not None and self._closed_registration_unchanged(waiting[0]))):
   if not self.candidate_done(waiting[0]):raise ContractError('COMMITTED_CANDIDATE_MISSING')
   queue=inspect_evaluation_queue(self.c)
   waiting,registration=self._waiting_evaluations()
  queue.update(registration)
  queue['deferred_training_candidates']=len(waiting)
  return queue
 def sync_rewards(self,now=None):
  """Bookkeeping under the scheduler lock, never the heavy-worker lease.

  A watched, unchanged source snapshot keeps its original as-of timestamp;
  every input mutation or five-minute bound forces complete replay. Without
  usable watches the original one-minute refresh remains. No cache survives
  process restart. Reward rows and current IDs commit together.
  """
  if now is None:now=int(time.time()*1000)
  tick=time.monotonic();previous=getattr(self,'_reward_sync_tick',None)
  guard=getattr(self,'_reward_refresh_guard',None)
  if guard is None:self._reward_refresh_guard=guard=RewardRefreshGuard()
  unchanged=guard.state(self.c,now,tick)
  if previous is not None and now>=self._reward_snapshot['at_ms']:
   if unchanged=='UNCHANGED':return self._reward_snapshot
   if unchanged!='CHANGED' and 0<=tick-previous<REWARD_SYNC_INTERVAL_SECONDS:return self._reward_snapshot
  self._reward_sync_tick=None
  watched=guard.begin(self.c,now,tick)
  rewards=(rewards_current(self.c,now,source_observer=guard.observe) if watched
           else rewards_current(self.c,now))
  with self.db:
   for r in rewards['rewards']:
    stored=self.db.execute('SELECT payload FROM rewards WHERE id=?',(r['reward_id'],)).fetchone()
    if stored and stored[0]!=canonical(r).decode():raise ContractError('REWARD_ID_CONFLICT')
    self.db.execute('INSERT OR IGNORE INTO rewards VALUES (?,?)',(r['reward_id'],canonical(r).decode()))
   for r in rewards.get('execution_rewards',[]):
    stored=self.db.execute('SELECT payload FROM execution_rewards WHERE id=?',(r['reward_id'],)).fetchone()
    if stored and stored[0]!=canonical(r).decode():raise ContractError('EXECUTION_REWARD_ID_CONFLICT')
    write_once(self.root/'execution-rewards'/(r['reward_id']+'.json'),r)
    self.db.execute('INSERT OR IGNORE INTO execution_rewards VALUES (?,?)',(r['reward_id'],canonical(r).decode()))
   put_meta(self.db,'active_reward_ids',[r['reward_id'] for r in rewards['rewards']])
   put_meta(self.db,'active_execution_reward_ids',[r['reward_id'] for r in rewards.get('execution_rewards',[])])
   put_meta(self.db,'last_reward_sync_ms',rewards['at_ms'])
  atomic(self.root/'rewards-status.json',rewards)
  self._reward_snapshot=rewards;self._reward_sync_tick=tick
  guard.finish(self.c,rewards,now)
  return rewards
 def _training_cadence_wait(self,now,queue):
  last=get_meta(self.db,'last_job_created_ms',0)
  if type(last) is not int or last<0:raise ContractError('TRAINING_CADENCE_TIMESTAMP_INVALID')
  if now<last:raise ContractError('TRAINING_CLOCK_REGRESSION')
  if now-last>=MIN_CADENCE_MS:return None
  return self.write_status(storage=self.storage,state='COLLECTING_BETWEEN_TRAINING_JOBS',
    reason='TRAINING_CADENCE_NOT_DUE',next_training_due_ms=last+MIN_CADENCE_MS,
    training_data_refresh_performed=False,**queue)
 def _training_wait_preflight(self,now,queue):
  cadence=self._training_cadence_wait(now,queue)
  if cadence is not None:return cadence
  waiting=getattr(self,'_training_input_wait',None)
  if waiting is None:return None
  reserved={row[0] for row in self.db.execute('SELECT id FROM consumed_rows')}
  held=waiting.lookup(self.c,reserved,now)
  if held is None:return None
  payload={**queue,**held,'state':'WAITING_FOR_NEW_MATURE_LABELS','reason_codes':['UNCHANGED_TRAINING_INPUTS']}
  return self.write_status(**payload)
 def queue_preflight(self):
  # Negative-only admission optimization; completing an already-trained
  # artifact's deferred registration never takes GPU or training authority.
  config_check(self.c)
  self.storage=inspect_storage(self.c)
  if not self.storage['admission_ready']:
   return self.write_status(storage=self.storage,state='HOLD_NEW_TRAINING_STORAGE',reason=self.storage['blocked_reason'])
  self.sync_rewards()
  queue=self.reconcile_evaluation_queue()
  self._evaluation_queue_status={**queue,'evaluation_queue_checked_at_ms':int(time.time()*1000)}
  limit=1 if self.c.get('promotion_protocol')==PROTOCOL else MAX_PENDING_EVALUATIONS
  if queue['deferred_training_candidates']:
   return self.write_status(storage=self.storage,state=('CANDIDATE_READY_AWAITING_FORWARD_ADMISSION' if self.c.get('future_plan_admission_profile') else 'CANDIDATE_READY_AWAITING_FROZEN_TRIAL_END'),**queue)
  if queue['viable_pending_evaluations']>=limit:
   return self.write_status(storage=self.storage,state='EVALUATING_BOUNDED_CANDIDATE_QUEUE',**queue)
  return self._training_wait_preflight(int(time.time()*1000),queue)
 def step(self):
  now=int(time.time()*1000);config_check(self.c);bootstrap_still_off()
  self.storage=inspect_storage(self.c)
  if not self.storage['admission_ready']:
   return self.write_status(state='HOLD_NEW_TRAINING_STORAGE',reason=self.storage['blocked_reason'])
  rewards=self.sync_rewards(now)
  queue=self.reconcile_evaluation_queue()
  self._evaluation_queue_status={**queue,'evaluation_queue_checked_at_ms':int(time.time()*1000)}
  if queue['deferred_training_candidates']:return self.write_status(state=('CANDIDATE_READY_AWAITING_FORWARD_ADMISSION' if self.c.get('future_plan_admission_profile') else 'CANDIDATE_READY_AWAITING_FROZEN_TRIAL_END'),**queue)
  pending=queue['viable_pending_evaluations']
  pending_limit=1 if self.c.get('promotion_protocol')==PROTOCOL else MAX_PENDING_EVALUATIONS
  if pending>=pending_limit:return self.write_status(state='EVALUATING_BOUNDED_CANDIDATE_QUEUE',**queue)
  held=self._training_wait_preflight(now,queue)
  if held is not None:return held
  from .resident_input_wait import InputWait,input_signature
  reserved={row[0] for row in self.db.execute('SELECT id FROM consumed_rows')}
  _input_wait=getattr(self,'_training_input_wait',None)
  if _input_wait is None:
   _input_wait=InputWait(input_signature)
   self._training_input_wait=_input_wait
  _input_before=_input_wait.begin(self.c,reserved,now) if training_v3.enabled(self.c.get('objective')) else None
  if training_v3.enabled(self.c.get('objective')):
   dataset,rows,quality,_=collect(self.c,now,reserved_row_ids=reserved)
  else:dataset,rows,quality,_=collect(self.c,now)
  new=[]
  for r in rows:
   key=digest([r['sample_id'],r['horizon_sec'],r['outcome_receipt_digest']])
   if not self.db.execute('SELECT 1 FROM consumed_rows WHERE id=?',(key,)).fetchone():new.append((key,r))
  state={'mature_rows':len(rows),'new_mature_rows':len(new),'feed':quality,**queue,'reward_reason':rewards['reason']}
  if len(new)<16:
   _input_wait.remember(self.c,reserved,now,_input_before,quality=quality,mature_rows=len(rows),new_mature_rows=len(new),clock_complete=quality.get('training_input_clock_complete') is True,next_input_ms=quality.get('next_training_input_ms'))
   return self.write_status(state='WAITING_FOR_NEW_MATURE_LABELS',**state)
  rule_for_trial(get_meta(self.db,'evaluation_trial',0)+1,self.c.get('promotion_protocol'))
  training_policy=training_v3 if training_v3.enabled(self.c.get('objective')) else training_v2
  chosen=(training_policy.select_covered_rows(new) if training_policy.enabled(self.c.get('objective')) else select_rows(new));row_ids=[k for k,_ in chosen];fingerprint=digest(row_ids)
  if self.db.execute('SELECT 1 FROM jobs WHERE fingerprint=?',(fingerprint,)).fetchone():return self.write_status(state='EXACT_DATASET_ALREADY_PROCESSED',**state)
  parent=read_json(Path(self.c['initial_parent_directory'])/'candidate.json')
  parent_id=parent['candidate_id'];generation=int(parent.get('generation',2))
  data_id=digest(dataset);dataset_path=self.root/'datasets'/(data_id+'.json');write_once(dataset_path,dataset)
  job_id=digest({'fingerprint':fingerprint,'parent':parent_id,'profile':self.c['profile'],'objective':self.c['objective']})
  job={**self.c,'schema':'heptatrader.laya-resident-job.v1','dataset':str(dataset_path),'dataset_sha256':sha_file(dataset_path),
    'data_fingerprint':fingerprint,'training_row_ids':row_ids,'training_row_accounting_schema':ROW_ACCOUNTING_SCHEMA,
    'parent_directory':self.c['initial_parent_directory'],'parent_manifest_sha256':sha_file(Path(self.c['initial_parent_directory'])/'candidate.json'),
    'parent_id':parent_id,'generation':generation,'window':quality['window'],
    'output_directory':str(Path(self.c['candidate_directory'])/job_id/'artifact')}
  job_path=self.root/'jobs'/(job_id+'.json');write_once(job_path,job)
  if not admit_job(self.db,job_id,fingerprint,now,job,row_ids):return self.write_status(state='JOB_ALREADY_ADMITTED',**state)
  self.write_status(state='TRAINING_ON_GFX1150',active_job=job_id,**state)
  with self.db:self.db.execute('UPDATE jobs SET status=? WHERE id=?',('RUNNING',job_id))
  log=self.root/'logs'/(job_id+'.log');log.parent.mkdir(mode=0o700,exist_ok=True)
  try:
   with log.open('x') as stream:
    self.child=subprocess.Popen([self.c['python'],'-B','-m','laya_cell.resident_launch','train','--config',str(job_path)],stdout=stream,stderr=stream)
    rc=self.child.wait(timeout=600)
   self.child=None
  except (subprocess.TimeoutExpired,Exception) as exc:
   if self.child and self.child.poll() is None:self.child.terminate();self.child.wait(timeout=15)
   self.child=None;rc=-1
  row=self.db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
  if not self.candidate_done(row):
   with self.db:self.db.execute('UPDATE jobs SET status=? WHERE id=?',('FAILED',job_id))
   write_once(self.root/'failures'/(job_id+'.json'),{'reason':'CHILD_FAILED_NO_COMMITTED_CANDIDATE','exit_code':rc,'job_id':job_id})
   return self.write_status(state='TRAINING_FAILED_EVIDENCE_RETAINED',active_job=job_id,**state)
  return self.write_status(state='CANDIDATE_TRAINED_AWAITING_FUTURE_EVALUATION',active_job=job_id,**state)

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();os.umask(0o077)
 config=read_json(a.config);root=Path(config['control_directory']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
 lock=os.open(root/'scheduler.lock',os.O_RDWR|os.O_CREAT|os.O_CLOEXEC,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 service=Supervisor(a.config);signal.signal(signal.SIGTERM,service.stop_signal);signal.signal(signal.SIGINT,service.stop_signal);service.recover()
 while not service.stop:
  try:service.step()
  except Exception as exc:service.write_status(state='LEARNING_UNAVAILABLE_NO_TRADE_EFFECT',reason=str(exc))
  if a.once:break
  for _ in range(30):
   if service.stop:break
   time.sleep(1)
 service.db.close();os.close(lock)
if __name__=='__main__':main()
