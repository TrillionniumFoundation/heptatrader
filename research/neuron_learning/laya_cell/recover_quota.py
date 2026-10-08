"""Audited bounded quota recovery; immutable old artifacts and FX/BTC untouched.

Only idle Laya trainer/evaluator units are replaced; all prior future plans must
already be terminal. The quote collector and completed Fusion units stay pinned.
"""
from __future__ import annotations
import argparse,io,json,os,sqlite3,subprocess,time
from contextlib import redirect_stdout
from pathlib import Path
from .integrity import ContractError,read_json,sha_file,canonical
from .resident_budget import inspect as inspect_storage,GIB
from .install_resident import install


def unit(name):
 raw=subprocess.check_output(['systemctl','--user','show',name,'-p','MainPID','-p','ActiveState','-p','NRestarts','-p','ExecStart'],text=True)
 return dict(x.split('=',1) for x in raw.splitlines() if '=' in x)

def save(path,value):
 raw=canonical(value)+b'\n'
 with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 path.chmod(0o400)

def validate_transition(before,after):
 allowed={'state_max_bytes','state_admission_reserve_bytes','filesystem_reserve_bytes'}
 changed={k for k in set(before)|set(after) if before.get(k)!=after.get(k)}
 if not changed<=allowed or after['state_max_bytes']!=8*GIB or before['state_max_bytes']!=2*GIB:
  raise ContractError('ONLY_REVIEWED_2_TO_8_GIB_RECOVERY_ALLOWED')
 if after.get('state_admission_reserve_bytes')!=GIB//2 or after.get('filesystem_reserve_bytes')!=10*GIB:
  raise ContractError('RECOVERY_RESERVES_REQUIRED')
 if after.get('automatic_trading') is not False:raise ContractError('RECOVERY_CANNOT_GRANT_TRADING')
 return changed

def apply(config_path,evidence):
 if os.geteuid()==0:raise ContractError('USER_SERVICE_ONLY')
 h=Path.home();c=read_json(config_path);root=Path(c['state_root']);control=Path(c['control_directory'])
 evidence.mkdir(mode=0o700,parents=True,exist_ok=True)
 for p in Path(c['evaluation_directory']).glob('*/plan.json'):
  q=p.parent/'qualification.json'
  if not q.exists() or read_json(q).get('state') not in ('REJECTED','QUALIFIED_AWAITING_CANONICAL_OWNER'):
   raise ContractError('DEFER_CONFIG_SWITCH_UNTIL_ORIGINAL_LAYA_PLANS_TERMINAL')
 db=sqlite3.connect('file:'+str(control/'scheduler.sqlite')+'?mode=ro',uri=True)
 jobs=db.execute('select id,status,candidate_id from jobs order by id').fetchall();db.close()
 if any(x[1] in ('ADMITTED','RUNNING') for x in jobs):raise ContractError('DEFER_ACTIVE_TRAINING')
 units_dir=h/'.config/systemd/user';targets=('heptatrader-laya-evaluator.service','heptatrader-laya-trainer.service')
 before_units={n:unit(n) for n in targets}
 if str(config_path) not in before_units['heptatrader-laya-trainer.service']['ExecStart']:
  raise ContractError('RUNNING_TRAINER_CONFIG_NOT_THE_AUDITED_SOURCE')
 protected=('heptatrader-paper-driver.service','heptatrader-crypto-driver.service','heptatrader-execution.service',
  'heptatrader-gateway.service','heptatrader-laya-feed.service','heptatrader-paired-price-predictor.service','heptatrader-paired-price-evaluator.service',
  'heptatrader-temporal-fusion-predictor.service','heptatrader-temporal-fusion-evaluator.service')
 peers={n:unit(n) for n in protected}
 after={**c,'state_max_bytes':8*GIB,'state_admission_reserve_bytes':GIB//2,'filesystem_reserve_bytes':10*GIB}
 changed=validate_transition(c,after);capacity=inspect_storage(after)
 if not capacity['admission_ready']:raise ContractError(capacity['blocked_reason'])
 original_files={n:(units_dir/n).read_bytes() for n in targets}
 for name,raw in original_files.items():
  p=evidence/(name+'.before')
  with p.open('xb') as f:f.write(raw)
  p.chmod(0o400)
 requested=evidence/'requested-config-8gib.json';save(requested,after)
 save(evidence/'quota-recovery-preflight.json',{'at_ms':int(time.time()*1000),'only_config_keys_changed':sorted(changed),'storage':capacity,
  'old_jobs':jobs,'protected_services':peers,'previous_trainer_config_sha256':sha_file(config_path),'trading_authority':False})
 installed={}
 try:
  # Stop the blocked scheduler first so it cannot race the evaluator version.
  subprocess.run(['systemctl','--user','stop','heptatrader-laya-trainer.service'],check=True)
  for role in ('evaluator','trainer'):
   with redirect_stdout(io.StringIO()):receipt=install(requested,True,True,role)
   installed[role]=receipt;save(evidence/('installed-'+role+'.json'),receipt)
   current=unit('heptatrader-laya-'+role+'.service')
   if current['ActiveState']!='active' or current['MainPID']=='0':raise ContractError('RECOVERY_START_FAILED:'+role)
 except Exception:
  # Prior config remains valid and budget-blocked. No pending/job is cleared.
  for name in targets:subprocess.run(['systemctl','--user','stop',name],check=False)
  for name,raw in original_files.items():
   temp=units_dir/(name+'.rollback-next');temp.write_bytes(raw);temp.chmod(0o600);os.replace(temp,units_dir/name)
  subprocess.run(['systemctl','--user','daemon-reload'],check=True)
  subprocess.run(['systemctl','--user','start',*targets],check=False)
  raise
 for name,previous in peers.items():
  if unit(name)['MainPID']!=previous['MainPID']:raise ContractError('PROTECTED_SERVICE_CHANGED:'+name)
 result={'schema':'heptatrader.laya-quota-recovery.v1','at_ms':int(time.time()*1000),'old_limit_bytes':c['state_max_bytes'],
  'new_limit_bytes':after['state_max_bytes'],'admission_reserve_bytes':after['state_admission_reserve_bytes'],
  'filesystem_reserve_bytes':after['filesystem_reserve_bytes'],'configuration':installed['trainer']['configuration'],
  'release':installed['trainer']['release'],'old_configuration_unchanged':sha_file(config_path)==read_json(evidence/'quota-recovery-preflight.json')['previous_trainer_config_sha256'],
  'protected_pids_unchanged':True,'old_evidence_deleted':False,'trading_authority':False,'broker_orders_sent':0,
  'learning_units':{n:unit(n) for n in targets}}
 save(evidence/'quota-recovery-receipt.json',result);print(json.dumps(result,indent=2));return result

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();os.umask(0o077);apply(a.config,a.evidence)
if __name__=='__main__':main()
