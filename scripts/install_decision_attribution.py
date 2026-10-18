#!/usr/bin/env python3
"""Inspect, prepare or install an exact PAPER decision-attribution producer.

Inspect and byte-identical re-install are read-only, work while non-flat, and
never restart a service. Upgrade preparation also stages code independently of
positions and opening holds. A first installation still requires an authoritative
flat lane; activation of changed trading code requires owner-controlled continuity.
"""
from __future__ import annotations
import argparse,ast,errno,fcntl,hashlib,json,os,re,shutil,subprocess,tempfile,time
from pathlib import Path
from hepta_verified_loader import load_verified_module
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

def runtime_filenames(instrument,*,legacy=False):
 names={'hepta_decision_attribution.py','heptatrader_attributed_driver.py',
        'heptatrader_paper_driver.py'}
 if instrument=='BTC.USD':
  names.update({'heptatrader_btc_economics_driver.py','hepta_btc_entry_policy.py'})
 if not legacy:names.add('hepta_verified_loader.py')
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
 loader=m.get('runtime_loader')
 allowed=[runtime_filenames(instrument)]
 if loader is None:allowed.append(runtime_filenames(instrument,legacy=True))
 if (loader not in (None,'verified-bytes.v1') or not isinstance(hashes,dict)
     or set(hashes) not in allowed):
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


def inspect_running_runtime(unit,instrument):
 """Report configured and actual process identities independently, read-only."""
 if instrument not in ('EUR.USD','BTC.USD') or unit!=unit_for(instrument):
  raise ValueError('ATTRIBUTION_INSPECTION_UNIT_SCOPE')
 result={'schema':'heptatrader.attribution-runtime-inspection.v1','unit':unit,
         'instrument':instrument,'captured_at_ms':int(time.time()*1000),
         'trade_authority':False,'service_changed':False}
 try:
  result['configured']=inspect_resident(show(unit)['ExecStart'],instrument)
 except Exception as exc:
  result['configured']={'status':'UNVERIFIED','error':type(exc).__name__+':'+str(exc)}
 try:
  # Lazy import avoids a module-import cycle. Activation retains strict matching
  # by default; this branch only inspects the argv of the observed process.
  from activate_prepared_driver import running_driver
  running=dict(running_driver(unit,instrument,require_configured_match=False))
  running['service']=running.pop('status')
  running['status']='RUNNING_VERIFIED'
  result['running']=running
 except Exception as exc:
  result['running']={'status':'UNVERIFIED','error':type(exc).__name__+':'+str(exc)}
 verified=(result['configured'].get('status')=='INSTALLED_VERIFIED'
           and result['running'].get('status')=='RUNNING_VERIFIED')
 matches=(result['configured']['manifest_sha256']==result['running']['manifest_sha256']) if verified else None
 result['configured_matches_running']=matches
 result['status']=('CONFIGURED_AND_RUNNING_VERIFIED' if matches else
                   'CONFIGURED_DIFFERS_FROM_RUNNING' if verified else 'INSPECTION_INCOMPLETE')
 return result


def _source_literal(raw,name):
 values=[]
 for node in ast.parse(raw).body:
  if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets):
   values.append(ast.literal_eval(node.value))
 if len(values)!=1:raise RuntimeError('ATTRIBUTION_SOURCE_BINDING_AMBIGUOUS:'+name)
 return values[0]

def _verify_prepared(release,expected,files):
 if release.is_symlink():raise RuntimeError('ATTRIBUTION_PREPARED_PATH_INVALID')
 if {p.name for p in release.iterdir()}!=set(files)|{'manifest.json'}:
  raise RuntimeError('ATTRIBUTION_PREPARED_FILE_SET_CHANGED')
 raw=read_private(release/'manifest.json',65536);actual=strict(raw)
 comparable=dict(actual);comparable.pop('created_at_ms',None)
 if comparable!=expected:raise RuntimeError('ATTRIBUTION_PREPARED_MANIFEST_CHANGED')
 for name,payload in files.items():
  if read_private(release/name,1048576)!=payload:
   raise RuntimeError('ATTRIBUTION_PREPARED_RUNTIME_CHANGED:'+name)
 return hashlib.sha256(raw).hexdigest()

def prepare_upgrade(instrument):
 """Stage exact candidate bytes; never read broker/state or change a unit."""
 if os.geteuid()==0:raise RuntimeError('USER_SERVICE_ONLY')
 h=Path.home();repo=Path(__file__).resolve().parents[1];source=repo/'scripts'
 service=show(unit_for(instrument))
 observed=inspect_resident(service['ExecStart'],instrument)
 if observed['status']!='INSTALLED_VERIFIED':
  raise RuntimeError('ATTRIBUTION_PREPARE_REQUIRES_EXISTING_INSTALLATION')
 resident=Path(observed['release'])
 installed_raw=read_private(resident/'manifest.json',65536)
 if hashlib.sha256(installed_raw).hexdigest()!=observed['manifest_sha256']:
  raise RuntimeError('ATTRIBUTION_INSTALLED_MANIFEST_CHANGED')
 installed=strict(installed_raw)
 files={name:(source/name).read_bytes() for name in sorted(runtime_filenames(instrument))}
 for name,raw in files.items():
  if len(raw)>1048576:raise RuntimeError('ATTRIBUTION_SOURCE_TOO_LARGE:'+name)
  compile(raw,name,'exec',dont_inherit=True)  # Validate without executing candidate code.
 hashes={name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}
 if instrument=='BTC.USD':
  profile_raw=read_private(resident/'profile.json',65536)
  if hashlib.sha256(profile_raw).hexdigest()!=installed.get('btc_profile_sha256'):
   raise RuntimeError('ATTRIBUTION_INSTALLED_BTC_PROFILE_CHANGED')
  profile=strict(profile_raw)
  if (profile.get('schema')!='heptatrader.btc-entry-runtime.v1' or
      profile.get('period_sha256')!=installed['period_sha256'] or
      profile.get('profile')!=_source_literal(files['hepta_btc_entry_policy.py'],'PROFILE')):
   raise RuntimeError('ATTRIBUTION_PREPARED_BTC_PROFILE_INCOMPATIBLE')
  if _source_literal(files['heptatrader_btc_economics_driver.py'],'BASE_SHA256')!=hashes['heptatrader_paper_driver.py']:
   raise RuntimeError('ATTRIBUTION_PREPARED_BTC_BASE_HASH_MISMATCH')
  files['profile.json']=profile_raw
 # Git records provenance; the manifest binds the exact copied runtime bytes.
 # Unrelated checkout changes are not a candidate-preparation requirement.
 source_sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
 if re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})',source_sha) is None:
  raise RuntimeError('ATTRIBUTION_SOURCE_IDENTITY_INVALID')
 manifest=dict(installed)
 manifest.pop('created_at_ms',None)
 runtime_bytes_changed=hashes!=installed['runtime_sha256']
 manifest.update(source_sha=source_sha,runtime_sha256=hashes,runtime_loader='verified-bytes.v1',
                 prepared_from_manifest_sha256=observed['manifest_sha256'],
                 runtime_bytes_changed=runtime_bytes_changed,
                 trading_behavior_changed=(installed.get('trading_behavior_changed') is not False or runtime_bytes_changed),
                 behavior_equivalence_assessed=False)
 identity=digest(manifest)
 release=h/'.local/share/heptatrader/decision-attribution/releases'/(source_sha+'-prepared-'+identity)/instrument
 release.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 if os.path.lexists(release):
  status='ALREADY_PREPARED_NOT_ACTIVATED'
  manifest_hash=_verify_prepared(release,manifest,files)
 else:
  staging=Path(tempfile.mkdtemp(prefix='.'+instrument+'-preparing-',dir=release.parent))
  try:
   for name,raw in files.items():private_write(staging/name,raw)
   complete=dict(manifest,created_at_ms=int(time.time()*1000))
   private_write(staging/'manifest.json',(canonical(complete)+'\n').encode())
   manifest_hash=_verify_prepared(staging,manifest,files)
   staging.chmod(0o500)
   try:
    os.rename(staging,release)
   except OSError as error:
    if error.errno not in (errno.EEXIST,errno.ENOTEMPTY):raise
    manifest_hash=_verify_prepared(release,manifest,files)
    status='ALREADY_PREPARED_NOT_ACTIVATED'
   else:
    status='PREPARED_NOT_ACTIVATED'
  finally:
   if staging.exists():
    staging.chmod(0o700)
    shutil.rmtree(staging)
 receipt={'schema':'heptatrader.attribution-preparation.v1','status':status,
  'instrument':instrument,'source_sha':source_sha,'release':str(release),
  'manifest':str(release/'manifest.json'),'manifest_sha256':manifest_hash,
  'prepared_from_manifest_sha256':observed['manifest_sha256'],'runtime_sha256':hashes,
  'trade_authority':False,'service_changed':False,'activated':False,'forced_orders':0}
 print(json.dumps(receipt,sort_keys=True),flush=True)
 return receipt

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
 names=sorted(runtime_filenames(instrument))
 hashes={}
 for name in names:
  private_write(release/name,(source/name).read_bytes());hashes[name]=sha(release/name)
 if btc_profile:private_write(release/'profile.json',read_private(btc_profile,65536))
 manifest={'schema':'heptatrader.attribution-producer.v1','runtime_loader':'verified-bytes.v1','source_sha':source_sha,'instrument':instrument,
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
  base=load_verified_module('attribution_deploy_snapshot',base_path,hashes['heptatrader_paper_driver.py'])
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
 p.add_argument('--evidence-directory',type=Path)
 mode=p.add_mutually_exclusive_group()
 mode.add_argument('--inspect-only',action='store_true')
 mode.add_argument('--prepare-upgrade',action='store_true')
 a=p.parse_args();os.umask(0o077)
 if a.inspect_only:
  print(json.dumps(inspect_running_runtime(unit_for(a.instrument),a.instrument),sort_keys=True),flush=True)
 elif a.prepare_upgrade:
  prepare_upgrade(a.instrument)
 else:
  if a.evidence_directory is None:p.error('--evidence-directory required for installation')
  install(a.instrument,a.evidence_directory)
if __name__=='__main__':main()
