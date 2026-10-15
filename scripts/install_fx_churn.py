#!/usr/bin/env python3
"""Install FX new-entry hysteresis around the existing attributed PAPER runtime.

Stages immutable bytes first. Activates only when the existing driver and Broker agree on a fully accounted
position, no pending command/order exists, and native authority is ready. Never
flattens a position for deployment, never changes actual fee or native risk rules.
"""
from __future__ import annotations
import argparse,fcntl,hashlib,importlib.util,json,math,os,re,subprocess,time
from pathlib import Path
from hepta_fx_churn import PROFILE,digest,require_original_fx_sizing

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_bytes())
def show(unit):
 s=subprocess.check_output(['systemctl','--user','show',unit,'-p','MainPID','-p','ActiveState','-p','NRestarts','-p','ExecStart'],text=True)
 return dict(x.split('=',1) for x in s.splitlines() if '=' in x)
def write_once(path,value,mode=0o400):
 raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode() if not isinstance(value,bytes) else value
 path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 if path.exists():
  if path.read_bytes()!=raw:raise RuntimeError('IMMUTABLE_PATH_CONFLICT:'+str(path))
  return
 with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 path.chmod(mode)
def flat(state):
 return state.get('pending') is None and abs(float(state.get('position',0)))<=1e-9 and not any(state.get(k) for k in ('halt_new_entries','risk_halt_reason','owner_fence_recovery_required','lease_uncertain'))

def coherent_idle(state):
 if state.get('pending') is not None or any(state.get(k) for k in ('halt_new_entries','risk_halt_reason','owner_fence_recovery_required','lease_uncertain')):
  return False
 position=state.get('position')
 if type(position) not in (int,float) or not math.isfinite(position) or position not in (0,25000,-25000):return False
 if position==0:return True
 entry=state.get('entry_price')
 return type(entry) in (int,float) and math.isfinite(entry) and entry>0 and bool(state.get('entry_price_basis'))

def economic_state(state):
 return {k:state.get(k) for k in ('position','pending','entries','exits','gross_pnl','entry_price','entry_price_basis','entry_at_ms')}

def wait_for_natural_flat(read_state, owner_identity, original_pid, timeout_seconds,
                          *, monotonic=time.monotonic, sleep=time.sleep):
    """Wait for the original strategy's voluntary flat state, never place/close orders.

    The official installer still obtains a fresh Broker position/order/owner
    snapshot after stopping that exact original driver. This watch is only
    an early, bounded file-level hint, not broker authorization.
    """
    if type(timeout_seconds) is not int or not 1<=timeout_seconds<=1800:
        raise ValueError('FX_NATURAL_FLAT_WAIT_LIMIT')
    if not original_pid or original_pid=='0':
        raise RuntimeError('FX_ORIGINAL_DRIVER_MISSING')
    deadline=monotonic()+timeout_seconds
    while True:
        if owner_identity()!=original_pid:
            raise RuntimeError('FX_ORIGINAL_DRIVER_CHANGED_DURING_WAIT')
        state=read_state()
        if flat(state):
            if owner_identity()!=original_pid:
                raise RuntimeError('FX_ORIGINAL_DRIVER_CHANGED_DURING_WAIT')
            return state
        remaining=deadline-monotonic()
        if remaining<=0:
            raise RuntimeError('FX_NO_NATURAL_FLAT_WITHIN_BOUNDED_WINDOW')
        sleep(min(.35,remaining))


def release_slot_name(head, now, activate):
    # A stage-only artifact and a later flat activation cannot share a frozen
    # OMS seed: activation must consume a fresh original command window.
    if not isinstance(head,str) or not re.fullmatch(r'[0-9a-f]{40}',head):
        raise ValueError('FX_RELEASE_COMMIT_INVALID')
    if type(now) is not int or now<=0:
        raise ValueError('FX_ACTIVATION_CLOCK_INVALID')
    return head if not activate else head+'-active-'+str(now)

def collect_original_oms_open_attempts(blob, now, period_started_at):
    """Conservatively seed only actual original PAPER FX send/uncertain attempts.

    An earlier timestamp for a repeated command can age it out of the
    rolling-hour limiter before the later broker send; keep the *latest*
    credible event for each unchanged command identity, never mint new IDs.
    """
    attempts={}
    for line in blob.splitlines():
        event=json.loads(line)
        if (event.get('event') not in ('place_sent','place_outcome_uncertain') or
                event.get('instrument')!='EUR.USD' or
                event.get('execution_domain')!='PAPER'):
            continue
        at=event.get('ts_ms');cmd=event.get('req_id')
        # A future or malformed relevant send must fail the installation,
        # not disappear from cooldown history.
        if type(at) is not int or at>now or not isinstance(cmd,str) or not cmd:
            raise ValueError('UNVERIFIABLE_OR_FUTURE_FX_OPEN_ATTEMPT')
        if at>=max(now-86400000,period_started_at):
            attempts[cmd]=max(attempts.get(cmd,at),at)
    return attempts

def main():
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);p.add_argument('--activate',action='store_true')
 p.add_argument('--wait-flat-seconds',type=int,default=0,
                help='bounded local-only wait for natural flat before original broker-verified activation')
 a=p.parse_args();os.umask(0o077)
 if a.wait_flat_seconds<0 or a.wait_flat_seconds>1800 or (a.wait_flat_seconds and not a.activate):
  raise RuntimeError('FX_NATURAL_FLAT_WAIT_SCOPE')
 h=Path.home();repo=Path(__file__).resolve().parents[1];scripts=repo/'scripts'
 if os.geteuid()==0:raise RuntimeError('USER_SERVICE_ONLY')
 if subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True).strip():raise RuntimeError('CLEAN_COMMITTED_SOURCE_REQUIRED')
 head=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
 unit='heptatrader-paper-driver.service';before_unit=show(unit)
 if before_unit['ActiveState']!='active':raise RuntimeError('RUNNING_PAPER_DRIVER_REQUIRED')
 match=re.search(r'--manifest\s+(\S+)',before_unit['ExecStart'])
 if not match:raise RuntimeError('EXACT_ATTRIBUTION_RUNTIME_REQUIRED')
 old_manifest_path=Path(match[1]);old=read(old_manifest_path);old_root=old_manifest_path.parent
 if old['instrument']!='EUR.USD' or old['execution_domain']!='PAPER':raise RuntimeError('FX_PAPER_ONLY')
 if old.get('fx_churn_profile_sha256'):raise RuntimeError('FX_PROFILE_ALREADY_ACTIVE')
 period=Path(old['period_path']);pr=period.read_bytes()
 if sha(period)!=old['period_sha256'] or read(period)['execution_domain']!='PAPER':raise RuntimeError('PERIOD_CHANGED')
 # Preserve exactly the running attributed runtime. Repo attribution code
 # may have advanced, but must not silently switch during an FX risk edit.
 installed_names=('heptatrader_paper_driver.py','hepta_decision_attribution.py',
                  'heptatrader_attributed_driver.py')
 for name in installed_names:
  if name not in old['runtime_sha256'] or sha(old_root/name)!=old['runtime_sha256'][name]:
   raise RuntimeError('PINNED_INSTALLED_RUNTIME_CHANGED:'+name)
 if sha(old_root/'heptatrader_paper_driver.py')!=sha(scripts/'heptatrader_paper_driver.py'):
  raise RuntimeError('ORIGINAL_PAPER_DRIVER_SOURCE_CHANGED')
 base_path=old_root/'heptatrader_paper_driver.py';spec=importlib.util.spec_from_file_location('fx_churn_deploy_base',base_path);base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
 if base.CONF!=.62 or base.QTY!=25000 or base.MAX_ADVERSE!=.002:raise RuntimeError('OLD_STRATEGY_CONSTANTS_CHANGED')
 # Legacy QTY=25000 is not the EUR/USD OPEN size: the installed driver
 # computes whole-EUR size from its pinned $100k reference-notional budget.
 require_original_fx_sizing(base)
 state_path=h/'.local/state/heptatrader/paper-driver/state.json';state=read(state_path)
 # New-entry-only activation is performed exclusively at a real zero-exposure
 # boundary. Never stop an existing 100K FX position merely for installation.
 if a.activate and not flat(state):
  if not a.wait_flat_seconds:
   raise RuntimeError('FX_ACTIVATION_REQUIRES_FLAT_AND_SETTLED_STATE')
  # State files are a hint only; lock, economic identity and native Broker
  # checks after stopping the exact owner remain authoritative and unchanged.
  def same_original_owner():
   current=show(unit)
   return (current.get('MainPID') if current.get('ActiveState')=='active'
           else None)
  state=wait_for_natural_flat(
      lambda:read(state_path),same_original_owner,
      before_unit['MainPID'],a.wait_flat_seconds)
 if a.activate and (show(unit)['MainPID'] != before_unit['MainPID'] or
                    not flat(state)):
  raise RuntimeError('FX_DRIVER_OR_STATE_CHANGED_AFTER_WAIT')
 now=int(time.time()*1000);journal=h/'.local/state/heptatrader/execution/oms-journal.jsonl';blob=journal.read_bytes();blob=blob[:blob.rfind(b'\n')+1]
 attempts=collect_original_oms_open_attempts(blob,now,read(period)['started_at_ms'])
 seed={'period_sha256':old['period_sha256'],'last_exit_ms':now,'activation_ms':now,
       'last_exit_basis':'CONSERVATIVE_ACTIVATION_COOLDOWN_NOT_FABRICATED_EXECUTION',
       'open_attempts':[{'command_id':k,'at_ms':v} for k,v in sorted(attempts.items(),key=lambda x:x[1])],
       'seed_source_sha256':hashlib.sha256(blob).hexdigest()}
 profile={'profile':PROFILE,'period_path':str(period),'period_sha256':old['period_sha256'],'seed':seed}
 release=h/'.local/share/heptatrader/fx-churn/releases'/release_slot_name(head,now,a.activate)
 if release.exists():raise RuntimeError('IMMUTABLE_ACTIVATION_SLOT_ALREADY_EXISTS')
 release.mkdir(mode=0o700,parents=True)
 hashes={}
 for name in installed_names+('hepta_fx_churn.py',):
  src=old_root/name if name in installed_names else scripts/name
  write_once(release/name,src.read_bytes());hashes[name]=sha(release/name)
 write_once(release/'fx-profile.json',profile)
 manifest={**old,'source_sha':head,'created_at_ms':now,'runtime_sha256':hashes,
  'journal_directory':str(Path(old['journal_directory'])/('fx-control-'+head)),
  'fx_churn_profile_sha256':sha(release/'fx-profile.json'),'trading_behavior_changed':True,
  'behavior_change_scope':'OPEN_CONFIRMATION_COOLDOWN_ONLY_ORIGINAL_CLOSE_UNCHANGED'}
 write_once(release/'manifest.json',manifest);a.evidence.mkdir(mode=0o700,parents=True,exist_ok=True)
 receipt={'schema':'heptatrader.fx-churn-install.v1','source_sha':head,'release':str(release),
  'manifest':str(release/'manifest.json'),'manifest_sha256':sha(release/'manifest.json'),
  'old_manifest':str(old_manifest_path),'old_manifest_sha256':sha(old_manifest_path),
  'profile':PROFILE,'journal_seed_attempts':len(attempts),'state_before':{k:state.get(k) for k in ('position','pending','entries','exits','gross_pnl')},
  'activated':False,'forced_orders':0,'native_base_sha256':sha(base_path),'state_unchanged_by_staging':True}
 write_once(a.evidence/'fx-staged.json',receipt)
 if not a.activate:
  receipt['activation_blocker']='STAGE_ONLY'
  print(json.dumps(receipt,indent=2));return
 drop=h/'.config/systemd/user/heptatrader-paper-driver.service.d/80-fx-churn.conf'
 if drop.exists():raise RuntimeError('EXISTING_DROPIN_NO_OVERWRITE')
 peers={n:show(n) for n in ('heptatrader-crypto-driver.service','heptatrader-execution.service','heptatrader-gateway.service')}
 health=base.native(str(h/'.local/bin/heptatrader'),'system.get_health')
 if health.get('execution_mode')!='PAPER':raise RuntimeError('NATIVE_NOT_PAPER')
 subprocess.run(['systemctl','--user','stop',unit],check=True);lock=None;installed=False
 try:
  lock=os.open(state_path.parent/'driver.lock',os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  stopped_state=read(state_path)
  if not flat(stopped_state) or economic_state(stopped_state)!=economic_state(state) or period.read_bytes()!=pr:
   raise RuntimeError('EXPOSURE_OR_PERIOD_CHANGED')
  snap=base.snapshot(str(h/'.local/bin/heptatrader'),require_quote=False);owner=snap['owner_health']
  if (snap['active'] or abs(float(snap['position'])-float(state['position']))>1e-9 or owner.get('mutation_ready') is not True
      or owner.get('owner_fenced') is True or owner.get('owner_recovery_only') is True):
   raise RuntimeError('NATIVE_SNAPSHOT_NOT_SAME_POSITION_READY')
  text='[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B '+str(release/'heptatrader_attributed_driver.py')+' --manifest '+str(release/'manifest.json')+'\n'
  write_once(drop,text.encode(),0o600);installed=True;subprocess.run(['systemctl','--user','daemon-reload'],check=True)
  os.close(lock);lock=None;subprocess.run(['systemctl','--user','start',unit],check=True);time.sleep(2)
  if show(unit)['ActiveState']!='active' or show(unit)['MainPID']=='0':raise RuntimeError('FX_RESTART_FAILED')
 except Exception:
  if lock is not None:os.close(lock)
  if installed:subprocess.run(['systemctl','--user','stop',unit]);drop.unlink();subprocess.run(['systemctl','--user','daemon-reload'])
  subprocess.run(['systemctl','--user','start',unit]);raise
 after_state=read(state_path)
 receipt.update(activated=True,service=show(unit),position_identity_preserved=economic_state(after_state)==economic_state(state),
  economic_state_before=economic_state(state),economic_state_after=economic_state(after_state),peer_pids_unchanged=all(show(n)['MainPID']==v['MainPID'] for n,v in peers.items()))
 write_once(a.evidence/'fx-deployed.json',receipt);print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
