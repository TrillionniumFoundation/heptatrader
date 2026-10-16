#!/usr/bin/env python3
"""Inspect or install an exact PAPER decision-attribution producer.

Inspect and byte-identical re-install are read-only, work while non-flat, and
never restart a service. A first installation still requires an authoritative
flat lane; changed trading code requires a separately reviewed owner-safe upgrade.
"""
from __future__ import annotations
import argparse,fcntl,hashlib,importlib.util,json,os,re,subprocess,time
from pathlib import Path
from hepta_decision_attribution import canonical,digest,read_private,strict


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def show(name):
 text=subprocess.check_output(['systemctl','--user','show',name,'-p','MainPID','-p','ActiveState','-p','NRestarts','-p','ExecStart'],text=True)
 return dict(x.split('=',1) for x in text.splitlines() if '=' in x)
def private_write(path,raw,mode=0o400):
 path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 path.chmod(mode)
def state_ok(s):
 return (abs(float(s.get('position',0)))<=1e-9 and s.get('pending') is None and not s.get('halt_new_entries')
   and not s.get('owner_fence_recovery_required') and not s.get('lease_uncertain') and not s.get('risk_halt_reason'))

def runtime_filenames(instrument):
 names={'hepta_decision_attribution.py','heptatrader_attributed_driver.py',
        'heptatrader_paper_driver.py'}
 if instrument=='BTC.USD':
  names.update({'heptatrader_btc_economics_driver.py','hepta_btc_entry_policy.py'})
 return names


def inspect_resident(command,instrument):
 """Inspect exact installed code without opening broker/state or restarting units."""
 if 'heptatrader_attributed_driver.py' not in command:
  return {'schema':'heptatrader.attribution-inspection.v1','status':'NOT_INSTRUMENTED',
          'instrument':instrument,'trade_authority':False,'service_changed':False}
 matches=re.findall(r'--manifest\s+(/[^\s;]+)',command)
 if len(matches)!=1:raise RuntimeError('ATTRIBUTION_MANIFEST_IDENTITY_AMBIGUOUS')
 path=Path(matches[0]);root=path.parent
 if path.name!='manifest.json' or path.is_symlink():
  raise RuntimeError('ATTRIBUTION_MANIFEST_PATH_INVALID')
 # A script name mentioned somewhere in a systemd command is not proof that
 # it is the real executable. Bind its argv path to the manifest's directory.
 expected=(r'argv\[\]=/usr/bin/python3\s+-B\s+'+
           re.escape(str(root/'heptatrader_attributed_driver.py'))+
           r'\s+--manifest\s+'+re.escape(str(path))+r'(?=\s|;)')
 if re.search(expected,command) is None:
  raise RuntimeError('ATTRIBUTION_EXECSTART_IDENTITY_MISMATCH')
 raw=read_private(path,65536);m=strict(raw)
 if (m.get('schema')!='heptatrader.attribution-producer.v1' or
     m.get('instrument')!=instrument or m.get('execution_domain')!='PAPER' or
     m.get('actor_scope')!='EXISTING_LOCAL_DRIVER' or
     m.get('fx_churn_profile_sha256')):
  raise RuntimeError('ATTRIBUTION_INSTALLED_SCOPE_MISMATCH')
 period_path=Path(m.get('period_path',''))
 if not period_path.is_absolute():raise RuntimeError('ATTRIBUTION_INSTALLED_PERIOD_CHANGED')
 period_raw=read_private(period_path,65536)
 if (hashlib.sha256(period_raw).hexdigest()!=m.get('period_sha256') or
     strict(period_raw).get('execution_domain')!='PAPER'):
  raise RuntimeError('ATTRIBUTION_INSTALLED_PERIOD_CHANGED')
 hashes=m.get('runtime_sha256')
 if not isinstance(hashes,dict) or set(hashes)!=runtime_filenames(instrument):
  raise RuntimeError('ATTRIBUTION_INSTALLED_RUNTIME_INCOMPLETE')
 for name,expected in hashes.items():
  if (type(expected) is not str or len(expected)!=64 or
      hashlib.sha256(read_private(root/name,1048576)).hexdigest()!=expected):
   raise RuntimeError('ATTRIBUTION_INSTALLED_RUNTIME_CHANGED:'+name)
 if instrument=='BTC.USD' and hashlib.sha256(read_private(root/'profile.json',65536)).hexdigest()!=m.get('btc_profile_sha256'):
  raise RuntimeError('ATTRIBUTION_INSTALLED_BTC_PROFILE_CHANGED')
 return {'schema':'heptatrader.attribution-inspection.v1','status':'INSTALLED_VERIFIED',
         'instrument':instrument,'manifest_sha256':hashlib.sha256(raw).hexdigest(),
         'source_sha':m.get('source_sha'),'release':str(root),
         'runtime_sha256':hashes,'trade_authority':False,'service_changed':False}


def unit_for(instrument):
 return 'heptatrader-paper-driver.service' if instrument=='EUR.USD' else 'heptatrader-crypto-driver.service'


def install(instrument,evidence):
 h=Path.home();repo=Path(__file__).resolve().parents[1];source=repo/'scripts'
 if os.geteuid()==0:raise RuntimeError('USER_SERVICE_ONLY')
 unit=unit_for(instrument);service=show(unit)
 if 'heptatrader_attributed_driver.py' in service['ExecStart']:
  # A byte-identical observational installation is already complete. There
  # must be no flat/owner requirement, restart or new release directory.
  observed=inspect_resident(service['ExecStart'],instrument)
  if (service.get('ActiveState')!='active' or service.get('MainPID') in (None,'0')):
   raise RuntimeError('ATTRIBUTION_RESIDENT_NOT_ACTIVE')
  if all(sha(source/name)==digest for name,digest in observed['runtime_sha256'].items()):
   observed['status']='ALREADY_CURRENT_NOOP'
   print(json.dumps(observed,sort_keys=True),flush=True)
   return observed
  raise RuntimeError('ATTRIBUTION_DIFFERENT_RELEASE_REQUIRES_OWNER_SAFE_UPGRADE')
 if subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True).strip():raise RuntimeError('CLEAN_SOURCE_REQUIRED')
 source_sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
 env={}
 for line in (h/'.config/heptatrader/execution.env').read_text().splitlines():
  if '=' in line:
   key,value=line.split('=',1)
   if key in ('HEPTA_IB_EXECUTION_MODE','HEPTA_IB_PAPER_PORT'):env[key]=value
 if env!={'HEPTA_IB_EXECUTION_MODE':'PAPER','HEPTA_IB_PAPER_PORT':'4002'}:raise RuntimeError('PAPER_4002_REQUIRED')
 if (h/'.local/state/heptatrader/RESET-STOPPED.json').exists():raise RuntimeError('RESET_HOLD')
 unit=unit_for(instrument)
 lane='paper-driver' if instrument=='EUR.USD' else 'crypto-driver';state_path=h/'.local/state/heptatrader'/lane/'state.json'
 before_raw=read_private(state_path,1048576);before=strict(before_raw)
 if not state_ok(before):raise RuntimeError('DEFER_UNTIL_FLAT_CLEAR:'+instrument)
 peers={n:show(n) for n in ('heptatrader-execution.service','heptatrader-gateway.service')}
 if any(v['ActiveState']!='active' or v['MainPID']=='0' for v in peers.values()):raise RuntimeError('EXECUTION_GATEWAY_NOT_READY')
 cmd=service['ExecStart']
 base_path=(h/'.local/share/heptatrader/current/libexec/heptatrader/heptatrader_paper_driver.py').resolve()
 if sha(base_path)!=sha(source/'heptatrader_paper_driver.py'):raise RuntimeError('BASE_SOURCE_DIFFERS_FROM_RUNNING')
 btc_profile=None
 if instrument=='BTC.USD':
  import re
  match=re.search(r'--base-driver\s+(\S+)',cmd);profile=re.search(r'--profile\s+(\S+)',cmd)
  if not match or not profile:raise RuntimeError('CURRENT_BTC_ECONOMICS_REQUIRED')
  base_path=Path(match[1]);btc_profile=Path(profile[1])
  if sha(base_path)!=sha(source/'heptatrader_paper_driver.py'):raise RuntimeError('BTC_BASE_CHANGED')
  for name in ('heptatrader_btc_economics_driver.py','hepta_btc_entry_policy.py'):
   if sha(base_path.parent/name)!=sha(source/name):raise RuntimeError('BTC_ECONOMICS_CHANGED')
 period_path=h/'.config/heptatrader/pnl-period.json';period_raw=read_private(period_path,65536);period_hash=hashlib.sha256(period_raw).hexdigest()
 if strict(period_raw).get('execution_domain')!='PAPER':raise RuntimeError('PERIOD_NOT_PAPER')
 release=h/'.local/share/heptatrader/decision-attribution/releases'/source_sha/instrument
 release.mkdir(mode=0o700,parents=True,exist_ok=False)
 names=['hepta_decision_attribution.py','heptatrader_attributed_driver.py','heptatrader_paper_driver.py']
 if instrument=='BTC.USD':names+=['heptatrader_btc_economics_driver.py','hepta_btc_entry_policy.py']
 hashes={}
 for name in names:
  private_write(release/name,(source/name).read_bytes());hashes[name]=sha(release/name)
 if btc_profile:private_write(release/'profile.json',read_private(btc_profile,65536))
 manifest={'schema':'heptatrader.attribution-producer.v1','source_sha':source_sha,'instrument':instrument,
  'execution_domain':'PAPER','period_path':str(period_path),'period_sha256':period_hash,'runtime_sha256':hashes,
  'btc_profile_sha256':sha(release/'profile.json') if btc_profile else None,
  'journal_directory':str(h/'.local/state/heptatrader/neuron-learning/attribution'/period_hash/instrument),
  'actor_scope':'EXISTING_LOCAL_DRIVER','created_at_ms':int(time.time()*1000),
  'trading_behavior_changed': instrument=='EUR.USD',
  'behavior_profile':('EURUSD_ORIGINAL_ENTRY_CONF_0.62_USD100K_REFERENCE_NOTIONAL' if instrument=='EUR.USD' else 'BTC_EXISTING_POLICY_UNCHANGED')}
 manifest_path=release/'manifest.json';private_write(manifest_path,(canonical(manifest)+'\n').encode())
 drop=h/'.config/systemd/user'/(unit+'.d')/'70-exact-attribution.conf'
 if drop.exists():raise RuntimeError('EXISTING_DROPIN_NO_OVERWRITE')
 text='[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B '+str(release/'heptatrader_attributed_driver.py')+' --manifest '+str(manifest_path)+'\n'
 evidence.mkdir(mode=0o700,parents=True,exist_ok=True)
 private_write(evidence/(instrument+'-state-before.json'),before_raw)
 before_ids={k:before.get(k) for k in ('position','pending','entries','exits','gross_pnl','btc_entry_control')}
 subprocess.run(['systemctl','--user','stop',unit],check=True)
 installed=False;lock=None
 try:
  current_raw=read_private(state_path,1048576);current=strict(current_raw)
  if not state_ok(current):raise RuntimeError('EXPOSURE_CHANGED_DURING_STOP')
  lock=os.open(state_path.parent/'driver.lock',os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  spec=importlib.util.spec_from_file_location('attribution_deploy_snapshot',base_path);base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
  base.INSTRUMENT=instrument
  live_health=base.native(str(h/'.local/bin/heptatrader'),'system.get_health')
  if live_health.get('execution_mode')!='PAPER':raise RuntimeError('RUNTIME_MODE_NOT_PAPER')
  snap=base.snapshot(str(h/'.local/bin/heptatrader'),require_quote=False);owner=snap.get('owner_health') or {}
  if (snap.get('active') or abs(float(snap.get('position',0)))>1e-9 or owner.get('mutation_ready') is not True
      or owner.get('owner_fenced') is True or owner.get('owner_recovery_only') is True):raise RuntimeError('AUTHORITATIVE_NOT_FLAT_READY')
  if read_private(period_path,65536)!=period_raw or read_private(state_path,1048576)!=current_raw:raise RuntimeError('CONCURRENT_STATE_OR_PERIOD_CHANGE')
  private_write(drop,text.encode(),0o600);installed=True
  subprocess.run(['systemctl','--user','daemon-reload'],check=True);os.close(lock);lock=None
  subprocess.run(['systemctl','--user','start',unit],check=True);time.sleep(2)
  after=show(unit)
  if after['ActiveState']!='active' or after['MainPID']=='0':raise RuntimeError('OBSERVATIONAL_LAUNCH_FAILED')
 except Exception:
  if lock is not None:os.close(lock)
  if installed:
   subprocess.run(['systemctl','--user','stop',unit]);drop.unlink();subprocess.run(['systemctl','--user','daemon-reload'],check=True)
  subprocess.run(['systemctl','--user','start',unit]);raise
 if any(show(n)['MainPID']!=v['MainPID'] for n,v in peers.items()):raise RuntimeError('PEER_PROCESS_CHANGED')
 after_state=strict(read_private(state_path,1048576));after_ids={k:after_state.get(k) for k in before_ids}
 receipt={'schema':'heptatrader.exact-attribution-install.v1','source_sha':source_sha,'instrument':instrument,
  'manifest':str(manifest_path),'manifest_sha256':sha(manifest_path),'release':str(release),'dropin':str(drop),
  'state_before':before_ids,'state_after':after_ids,'state_invariants_unchanged':before_ids==after_ids,
  'service':after,'old_pid':service['MainPID'],'base_driver_sha256':hashes['heptatrader_paper_driver.py'],
  'native_execution_gateway_pids_preserved':True,'journal_authorization_effect':False,'forced_orders':0}
 private_write(evidence/(instrument+'-deploy.json'),(canonical(receipt)+'\n').encode())
 print(json.dumps(receipt,indent=2),flush=True);return receipt

def main():
 p=argparse.ArgumentParser();p.add_argument('--instrument',choices=['EUR.USD','BTC.USD'],required=True)
 p.add_argument('--evidence-directory',type=Path);p.add_argument('--inspect-only',action='store_true');a=p.parse_args();os.umask(0o077)
 if a.inspect_only:
  print(json.dumps(inspect_resident(show(unit_for(a.instrument))['ExecStart'],a.instrument),sort_keys=True),flush=True)
 else:
  if a.evidence_directory is None:p.error('--evidence-directory required for installation')
  install(a.instrument,a.evidence_directory)
if __name__=='__main__':main()
