"""Resident candidate scheduler. No trading methods and no self-issued promotion.

New mature-row fingerprints, durable job admission, bounded child processes and
immutable candidate outputs survive restart. The bootstrap trainer stays off.
"""
from __future__ import annotations
import argparse,fcntl,json,os,signal,subprocess,sys,time
from pathlib import Path
from .integrity import ContractError,read_json,digest,sha_file,canonical
from .resident_data import collect
from .resident_rewards import current as rewards_current
from .resident_store import database,write_once,atomic,get_meta,put_meta,admit_job,complete_job
from .promotion import RULE
from .resident_budget import policy as budget_policy,inspect as inspect_storage

# No torch import in the resident scheduler; GPU memory belongs to bounded children.
MIN_CADENCE_MS=900000
MAX_PENDING_EVALUATIONS=2


def config_check(config):
 if (config.get('schema')!='heptatrader.laya-resident-config.v1' or config.get('period_sha256') is None
     or config.get('training_cadence_ms')!=MIN_CADENCE_MS or config.get('automatic_trading') is not False):
  raise ContractError('UNREVIEWED_RESIDENT_CONFIGURATION')
 if sha_file(Path(config['period_path']))!=config['period_sha256']:raise ContractError('ACCOUNTING_PERIOD_CHANGED')
 if sha_file(Path(config['native_binary']))!=config['native_binary_sha256']:raise ContractError('NATIVE_BINARY_CHANGED')
 for rel,expected in config.get('runtime_sha256',{}).items():
  if Path(rel).is_absolute() or '..' in Path(rel).parts:raise ContractError('RUNTIME_PATH_ESCAPE')
  if sha_file(Path(config['runtime_directory'])/rel)!=expected:raise ContractError('RUNTIME_CHANGED:'+rel)
 budget_policy(config)
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
 def write_status(self,**extra):
  stats={s:self.db.execute('SELECT count(*) FROM jobs WHERE status=?',(s,)).fetchone()[0] for s in ('ADMITTED','RUNNING','COMPLETED','FAILED')}
  state={'schema':'heptatrader.laya-resident-status.v1','at_ms':int(time.time()*1000),'job_counts':stats,
    'last_candidate_id':get_meta(self.db,'last_completed_candidate'),'historical_confirmed_pnl_rewards':self.db.execute('SELECT count(*) FROM rewards').fetchone()[0],
    'active_pnl_reward_count':len(get_meta(self.db,'active_reward_ids',[])),
    'active_execution_reward_count':len(get_meta(self.db,'active_execution_reward_ids',[])),
    'execution_reward_is_not_model_credit':True,
    'training_cadence_seconds':900,'old_mlp_training_enabled':False,'automatic_trading':False,
    'canonical_agentd_owner_connected':False,'automatic_promotion':'EVALUATION_AND_HANDOFF_ONLY_UNTIL_CANONICAL_OWNER',
    'broker_orders_sent':0,'storage':self.storage,**extra}
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
 def register_evaluation(self,job_id,candidate,job):
  folder=Path(self.c['evaluation_directory'])/job_id;folder.mkdir(parents=True,mode=0o700,exist_ok=True)
  path=folder/'plan.json'
  if path.exists():
   old=read_json(path)
   if old['candidate_id']!=candidate['candidate_id']:raise ContractError('EVALUATION_CANDIDATE_CONFLICT')
   return
  with self.db:
   trial=get_meta(self.db,'evaluation_trial',0)+1;put_meta(self.db,'evaluation_trial',trial)
  now=int(time.time()*1000);start=now+120000
  plan={'schema':'heptatrader.laya-resident-future-plan.v1','job_id':job_id,'candidate_id':candidate['candidate_id'],
   'candidate_directory':job['output_directory'],'parent_directory':job['parent_directory'],'parent_id':job['parent_id'],
   'created_at_ms':now,'start_ms':start,'outcome_end_ms':start+(RULE['slots']-1)*RULE['slot_spacing_ms']+RULE['input_grace_ms']+RULE['horizon_ms']+5000,
   'trial':trial,'rule':RULE,'scope':self.c['period_sha256'],'no_refit':True,'broker_orders_sent':0,
   'evaluation_runtime_sha256':{n:self.c.get('runtime_sha256',{}).get(n) for n in ('learn/laya_cell/resident_evaluate.py','learn/laya_cell/resident_predict.py','learn/laya_cell/promotion.py','learn/laya_cell/resident_data.py','learn/laya_cell/shadow_source.py')},
   'candidate_manifest_sha256':sha_file(Path(job['output_directory'])/'candidate.json')}
  plan['plan_id']=digest(plan);write_once(path,plan)
  # This is an unadmitted handoff, not a canonical fact or owner-issued authority.
  request={'schema':'heptatrader.unadmitted-laya-owner-handoff.v1','candidate_id':candidate['candidate_id'],
   'candidate_manifest_sha256':sha_file(Path(job['output_directory'])/'candidate.json'),'dataset_id':candidate['dataset_id'],
   'parent_id':candidate['parent_id'],'native_proposal_digest':candidate['plasticity']['proposal_digest'],
   'required_owners':['learning.ledger','learning.operator','learning.artifacts','qualification','runtime.agentd'],
   'state':'UNADMITTED_CANONICAL_OWNER_NOT_ENROLLED','canonical_fact':False,'effect_authority':False}
  write_once(self.root/'owner-outbox'/(candidate['candidate_id']+'.json'),request)
 def recover(self):
  for row in self.db.execute("SELECT * FROM jobs WHERE status IN ('ADMITTED','RUNNING','COMPLETED')").fetchall():
   if self.candidate_done(row):continue
   if row['status']=='COMPLETED':raise ContractError('COMMITTED_CANDIDATE_MISSING')
   # Lost children are not blindly restarted over partially written candidates.
   with self.db:self.db.execute('UPDATE jobs SET status=? WHERE id=?',('FAILED',row['id']))
   write_once(self.root/'failures'/(row['id']+'.json'),{'reason':'INTERRUPTED_JOB_NO_COMMITTED_CANDIDATE','job_id':row['id'],'rows_remain_consumed':True})
 def step(self):
  now=int(time.time()*1000);config_check(self.c);bootstrap_still_off()
  self.storage=inspect_storage(self.c)
  if not self.storage['admission_ready']:raise ContractError(self.storage['blocked_reason'])
  rewards=rewards_current(self.c,now);atomic(self.root/'rewards-status.json',rewards)
  with self.db:
   for r in rewards['rewards']:
    stored=self.db.execute('SELECT payload FROM rewards WHERE id=?',(r['reward_id'],)).fetchone()
    if stored and stored[0]!=canonical(r).decode():raise ContractError('REWARD_ID_CONFLICT')
    self.db.execute('INSERT OR IGNORE INTO rewards VALUES (?,?)',(r['reward_id'],canonical(r).decode()))
  with self.db:put_meta(self.db,'active_reward_ids',[r['reward_id'] for r in rewards['rewards']])
  with self.db:
   for r in rewards.get('execution_rewards',[]):
    previous=self.db.execute('SELECT payload FROM execution_rewards WHERE id=?',(r['reward_id'],)).fetchone()
    if previous and previous[0]!=canonical(r).decode():raise ContractError('EXECUTION_REWARD_ID_CONFLICT')
    write_once(self.root/'execution-rewards'/(r['reward_id']+'.json'),r)
    self.db.execute('INSERT OR IGNORE INTO execution_rewards VALUES (?,?)',(r['reward_id'],canonical(r).decode()))
   put_meta(self.db,'active_execution_reward_ids',[r['reward_id'] for r in rewards.get('execution_rewards',[])])
  pending=sum(not (p.parent/'qualification.json').exists() for p in Path(self.c['evaluation_directory']).glob('*/plan.json'))
  dataset,rows,quality,_=collect(self.c,now)
  new=[]
  for r in rows:
   key=digest([r['sample_id'],r['horizon_sec'],r['outcome_receipt_digest']])
   if not self.db.execute('SELECT 1 FROM consumed_rows WHERE id=?',(key,)).fetchone():new.append((key,r))
  state={'mature_rows':len(rows),'new_mature_rows':len(new),'feed':quality,'pending_evaluations':pending,'reward_reason':rewards['reason']}
  if pending>=MAX_PENDING_EVALUATIONS:return self.write_status(state='EVALUATING_BOUNDED_CANDIDATE_QUEUE',**state)
  last=get_meta(self.db,'last_job_created_ms',0)
  if now<last:raise ContractError('TRAINING_CLOCK_REGRESSION')
  if now-last<MIN_CADENCE_MS:return self.write_status(state='COLLECTING_BETWEEN_TRAINING_JOBS',**state)
  if len(new)<16:return self.write_status(state='WAITING_FOR_NEW_MATURE_LABELS',**state)
  chosen=new[-64:];row_ids=[k for k,_ in chosen];fingerprint=digest(row_ids)
  if self.db.execute('SELECT 1 FROM jobs WHERE fingerprint=?',(fingerprint,)).fetchone():return self.write_status(state='EXACT_DATASET_ALREADY_PROCESSED',**state)
  parent=read_json(Path(self.c['initial_parent_directory'])/'candidate.json')
  parent_id=parent['candidate_id'];generation=int(parent.get('generation',2))
  data_id=digest(dataset);dataset_path=self.root/'datasets'/(data_id+'.json');write_once(dataset_path,dataset)
  job_id=digest({'fingerprint':fingerprint,'parent':parent_id,'profile':self.c['profile'],'objective':self.c['objective']})
  job={**self.c,'schema':'heptatrader.laya-resident-job.v1','dataset':str(dataset_path),'dataset_sha256':sha_file(dataset_path),
    'data_fingerprint':fingerprint,'training_row_ids':row_ids,'parent_directory':self.c['initial_parent_directory'],
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
