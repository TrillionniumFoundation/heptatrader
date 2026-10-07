"""Independent resident financial-value candidate service. No order operations.

Each changed set of current, settled, exactly attributed rewards creates at most
one bounded fit. This service never selects its readout for execution and never
changes the existing Laya scorer/action candidates or their future evaluation.
"""
from __future__ import annotations
import argparse,fcntl,json,os,signal,sqlite3,subprocess,time
from pathlib import Path
from .integrity import ContractError,read_json,sha_file,digest,canonical
from .financial_value_data import PROFILE,snapshot
from .resident_store import write_once,atomic


def database(path):
 db=sqlite3.connect(path,timeout=5);db.row_factory=sqlite3.Row;db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
 db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,fingerprint TEXT UNIQUE NOT NULL,status TEXT NOT NULL,created_ms INTEGER NOT NULL,payload TEXT NOT NULL,candidate_id TEXT)')
 db.commit();return db

def validate_config(c):
 if c.get('financial_profile')!=PROFILE or c.get('automatic_trading') is not False:raise ContractError('FINANCIAL_PROFILE_OR_AUTHORITY')
 if sha_file(Path(c['period_path']))!=c['period_sha256']:raise ContractError('FINANCIAL_PERIOD_CHANGED')
 for name,sha in c['value_runtime_sha256'].items():
  if Path(name).is_absolute() or '..' in Path(name).parts:raise ContractError('VALUE_RUNTIME_PATH')
  if sha_file(Path(c['value_runtime'])/name)!=sha:raise ContractError('VALUE_RUNTIME_CHANGED')
 return c

def behavior_datasets(data):
 groups={}
 for row in data['rows']:
  scope={'instrument':row['instrument'],'behavior_model_id':row['behavior_model_id'],
   'producer_manifest_sha256':row['reward']['input_attribution']['producer_manifest_sha256'],
   'quantity':25000,'currency':'USD'}
  key=digest(scope)
  if key not in groups:groups[key]={'scope':scope,'rows':[]}
  groups[key]['rows'].append(row)
 result=[]
 for key,g in sorted(groups.items()):
  rows=sorted(g['rows'],key=lambda r:(r['input_started_at_ms'],r['row_id']))
  result.append({**data,'rows':rows,'behavior_scope':g['scope'],'behavior_scope_id':key,
    'fingerprint':digest([r['row_id'] for r in rows])})
 return result

class Service:
 def __init__(self,path):
  self.path=Path(path);self.c=validate_config(read_json(path));self.root=Path(self.c['value_output_root']);self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
  self.db=database(self.root/'jobs.sqlite');self.stop=False;self.child=None
 def signal(self,*_):
  self.stop=True
  if self.child and self.child.poll() is None:self.child.terminate()
 def status(self,state,**extra):
  r={'schema':'heptatrader.financial-value-status.v1','at_ms':int(time.time()*1000),'state':state,
   'jobs':{row[0]:row[1] for row in self.db.execute('select status,count(*) from jobs group by status')},
   'automatic_trading':False,'actor_or_scorer_updated':False,'canonical_owner_selected':False,
   'value_candidate_selected':False,'broker_orders_sent':0,**extra}
  atomic(self.root/'status.json',r);print(json.dumps(r),flush=True);return r
 def recover(self):
  for row in self.db.execute("SELECT * FROM jobs WHERE status='RUNNING'").fetchall():
   c=json.loads(row['payload']);p=Path(c['job_output'])/'candidate.json'
   if p.exists():self.complete(row['id'],p)
   else:
    with self.db:self.db.execute("UPDATE jobs SET status='INTERRUPTED' WHERE id=?",(row['id'],))
 def complete(self,job_id,p):
  r=read_json(p)
  row=self.db.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
  if row is None:raise ContractError('UNADMITTED_VALUE_JOB')
  job=json.loads(row['payload'])
  if r.get('dataset_sha256')!=job['dataset_sha256'] or r.get('financial_profile')!=PROFILE:
   raise ContractError('VALUE_JOB_DATASET_OR_PROFILE_CHANGED')
  if r.get('status')!='UNVALIDATED_FINANCIAL_VALUE_CANDIDATE':raise ContractError('VALUE_RESULT_STATUS')
  if r['candidate_id']!=digest({k:v for k,v in r.items() if k!='candidate_id'}) or sha_file(p.parent/'value-head.safetensors')!=r['head_tensor_sha256']:
   raise ContractError('FINANCIAL_CANDIDATE_IDENTITY')
  if r.get('pnl_reward_used') is not True or r.get('pretrained_laya_unchanged') is not True or r.get('policy_gradient_used') is not False:
   raise ContractError('FINANCIAL_CANDIDATE_ROLE')
  with self.db:self.db.execute("UPDATE jobs SET status='COMPLETED',candidate_id=? WHERE id=?",(r['candidate_id'],job_id))
  atomic(self.root/'latest-candidate.json',{'candidate_id':r['candidate_id'],'candidate_directory':str(p.parent),
   'selection':False,'requires_independent_future_evaluation':True})
  return r
 def step(self):
  validate_config(self.c);now=int(time.time()*1000);data=snapshot(self.c,now);count=len(data['rows'])
  if not count:return self.status('WAITING_FOR_EXACT_SETTLED_FINANCIAL_REWARDS',reason=data['financial_reward_reason'],exclusions=data['exclusions'])
  if sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())>512*1024**2:raise ContractError('VALUE_STORAGE_BUDGET_NO_PRUNING')
  groups=behavior_datasets(data)
  unprocessed=[g for g in groups if not self.db.execute('SELECT 1 FROM jobs WHERE fingerprint=?',(g['fingerprint'],)).fetchone()]
  if not unprocessed:return self.status('CURRENT_REWARD_SET_ALREADY_PROCESSED',reward_count=count,behavior_scopes=len(groups))
  data=unprocessed[0];count=len(data['rows'])
  last=self.db.execute('SELECT MAX(created_ms) FROM jobs').fetchone()[0]
  if last is not None and now<last:raise ContractError('VALUE_CLOCK_REGRESSION')
  if last is not None and now-last<900000:return self.status('COLLECTING_NEXT_REWARD_VERSION',reward_count=count,minimum_fit_interval_ms=900000)
  job_id=digest({'fingerprint':data['fingerprint'],'profile':PROFILE,'base_directory':self.c['base_directory']})
  directory=self.root/'jobs'/job_id;directory.mkdir(mode=0o700,parents=True,exist_ok=True)
  dataset=directory/'dataset.json';write_once(dataset,data)
  job={**self.c,'behavior_scope':data['behavior_scope'],'dataset_path':str(dataset),'dataset_sha256':sha_file(dataset),'job_output':str(directory/'artifact')}
  config=directory/'job.json';write_once(config,job)
  with self.db:self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',(job_id,data['fingerprint'],'RUNNING',now,canonical(job).decode(),None))
  self.status('TRAINING_FINANCIAL_READOUT_ON_GFX1150',job_id=job_id,reward_count=count,targets_net_usd=[r['net_usd'] for r in data['rows']])
  with (directory/'worker.log').open('x') as log:
   self.child=subprocess.Popen([self.c['python'],'-B','-m','laya_cell.financial_value_launch','--config',str(config)],stdout=log,stderr=log)
   try:rc=self.child.wait(timeout=300)
   except subprocess.TimeoutExpired:
    self.child.terminate()
    try:self.child.wait(timeout=10)
    except subprocess.TimeoutExpired:self.child.kill();self.child.wait(timeout=5)
    rc=-1
   self.child=None
  path=Path(job['job_output'])/'candidate.json'
  if not path.exists():
   with self.db:self.db.execute("UPDATE jobs SET status='FAILED' WHERE id=?",(job_id,))
   return self.status('FINANCIAL_JOB_FAILED_NO_CANDIDATE',job_id=job_id,exit_code=rc)
  result=self.complete(job_id,path)
  return self.status('FINANCIAL_CANDIDATE_TRAINED_NOT_SELECTED',candidate_id=result['candidate_id'],reward_count=count,
    changed_parameters=result['diagnostic']['changed_parameters'],huber_before=result['diagnostic']['huber_before'],huber_after=result['diagnostic']['huber_after'])

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();os.umask(0o077)
 c=read_json(a.config);root=Path(c['value_output_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
 lock=os.open(root/'service.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 s=Service(a.config);signal.signal(signal.SIGTERM,s.signal);signal.signal(signal.SIGINT,s.signal);s.recover()
 while not s.stop:
  try:s.step()
  except Exception as exc:s.status('FINANCIAL_VALUE_UNAVAILABLE_NO_TRADE_EFFECT',reason=str(exc))
  if a.once:break
  for _ in range(60):
   if s.stop:break
   time.sleep(1)
 s.db.close();os.close(lock)
if __name__=='__main__':main()
