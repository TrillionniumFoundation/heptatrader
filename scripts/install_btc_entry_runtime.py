#!/usr/bin/env python3
"""Explicit BTC PAPER-only deployment; no FX, Execution or Gateway restart.

Requires the BTC driver already stopped, local+authoritative flat/no-pending,
current period and current owner readiness. Never clears risk/lease/reset flags.
"""
from pathlib import Path
import argparse,fcntl,hashlib,json,os,subprocess,time
from hepta_btc_entry_policy import PROFILE,validate_control,canonical,digest
from heptatrader_btc_economics_driver import BASE_SHA256,load_base


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def show(name):
 s=subprocess.check_output(['systemctl','--user','show',name,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True)
 return dict(line.split('=',1) for line in s.splitlines() if '=' in line)
def once(path,value,mode=0o600):
 raw=(canonical(value)+'\n').encode();fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,mode)
 with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())

def main():
 p=argparse.ArgumentParser();p.add_argument('--evidence-dir',type=Path,required=True);p.add_argument('--start',action='store_true');a=p.parse_args()
 if os.geteuid()==0:raise SystemExit('USER_ONLY_PAPER_PROFILE')
 os.umask(0o077);h=Path.home();src=Path(__file__).resolve().parent;repo=src.parent
 head=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
 if subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True).strip():raise SystemExit('CLEAN_COMMITTED_SOURCE_REQUIRED')
 remote=subprocess.check_output(['git','-C',str(repo),'ls-remote','origin','refs/heads/main'],text=True).split()[0]
 if remote!=head:raise SystemExit('REVIEWED_MAIN_NOT_MATCHED')
 if show('heptatrader-crypto-driver.service')['MainPID']!='0':raise SystemExit('STOP_FLAT_BTC_DRIVER_BEFORE_DEPLOY')
 # Same per-instrument lock as the resident base through precheck/installation.
 lock_path=h/'.local/state/heptatrader/crypto-driver/driver.lock'
 lock_fd=os.open(lock_path,os.O_RDWR|os.O_CREAT|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
 fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 state_root=h/'.local/state/heptatrader';state_path=state_root/'crypto-driver/state.json';period_path=h/'.config/heptatrader/pnl-period.json'
 if (state_root/'RESET-STOPPED.json').exists():raise SystemExit('RESET_HOLD')
 state_raw=state_path.read_bytes();s=json.loads(state_raw);period_raw=period_path.read_bytes();period=json.loads(period_raw)
 if period.get('execution_domain')!='PAPER':raise SystemExit('PAPER_PERIOD_REQUIRED')
 if (float(s.get('position',0))!=0 or s.get('pending') is not None or s.get('halt_new_entries') is not False or
     s.get('owner_fence_recovery_required') or s.get('lease_uncertain') or s.get('risk_halt_reason') or s.get('settlement_hold_reason')):
  raise SystemExit('BTC_STATE_NOT_READY_NO_FLAG_BYPASS')
 env={}
 for line in (h/'.config/heptatrader/execution.env').read_text().splitlines():
  if '=' in line and not line.lstrip().startswith('#'):
   k,v=line.split('=',1);env[k]=v.strip()
 if env.get('HEPTA_IB_EXECUTION_MODE')!='PAPER' or env.get('HEPTA_IB_PAPER_PORT')!='4002':raise SystemExit('PAPER_ONLY_TRANSPORT_REQUIRED')
 base=h/'.local/share/heptatrader/current/libexec/heptatrader/heptatrader_paper_driver.py'
 if sha(base)!=BASE_SHA256 or sha(src/'heptatrader_paper_driver.py')!=BASE_SHA256:raise SystemExit('BASE_DRIVER_HASH_CHANGED')
 m=load_base(base);m.INSTRUMENT='BTC.USD';m.QTY=1.;m.QUANTITY_TOLERANCE=1e-10
 m.NATIVE_ENDPOINT=[str(h/'.local/share/heptatrader/current/bin/heptactl'),'--socket',f'/run/user/{os.geteuid()}/heptatrader/tools.sock','--token-file',str(h/'.config/heptatrader/crypto.token')]
 if not m.crypto_roundtrip_qualified(s):raise SystemExit('EXISTING_CRYPTO_QUALIFICATION_REQUIRED_NO_NEW_CANARY')
 snap=m.snapshot(str(h/'.local/bin/heptatrader'),s,state_path,require_quote=False)
 if snap.get('active') or float(snap['position'])!=0 or snap['owner_health'].get('mutation_ready') is not True:
  raise SystemExit('AUTHORITATIVE_FLAT_READY_REQUIRED')
 if state_path.read_bytes()!=state_raw:raise SystemExit('STATE_CHANGED_DURING_PRECHECK')
 watched=('heptatrader-paper-driver.service','heptatrader-execution.service','heptatrader-gateway.service')
 before={name:show(name) for name in watched}
 # Seed opening reservations from actual current-owner durable intents. Count
 # attempted sends conservatively; do not use reused numeric broker order IDs.
 now=int(time.time()*1000);owner=period['instruments']['BTC.USD'];reservations={}
 journal=state_root/'execution/oms-journal.jsonl';raw=journal.read_bytes()
 if len(raw)>64*1024*1024 or raw and not raw.endswith(b'\n'):raise SystemExit('COMPLETE_BOUNDED_OMS_PREFIX_REQUIRED')
 for line in raw.splitlines():
  e=json.loads(line)
  if (e.get('event') not in ('order_intent','place_sent') or e.get('instrument')!='BTC.USD' or
      e.get('execution_domain')!='PAPER' or e.get('account')!=period['account'] or
      e.get('trace_id')!=owner['session_id'] or e.get('side') not in ('BUY','Buy','buy')):continue
  command=e.get('req_id');t=e.get('ts_ms')
  if not isinstance(command,str) or type(t)is not int or t>now:raise SystemExit('OMS_ENTRY_IDENTITY_OR_CLOCK')
  if t>=max(period['started_at_ms'],now-PROFILE['rolling_entry_window_ms']):reservations[command]=max(t,reservations.get(command,0))
 last=s.get('last_reconciliation') or {}
 if s.get('entries',0) and (last.get('kind')!='CLOSE' or float(last.get('position_after',1))!=0 or type(last.get('reconciled_at_ms'))is not int):
  raise SystemExit('PAST_ENTRIES_REQUIRE_EXACT_FLAT_RECONCILIATION')
 control={'schema':'heptatrader.btc-entry-control.v1','open_reservations':[{'command_id':k,'prepared_at_ms':v} for k,v in sorted(reservations.items(),key=lambda kv:kv[1])],
          'last_flat_at_ms':last.get('reconciled_at_ms',0)}
 validate_control(control,now)
 release=h/'.local/share/heptatrader/btc-entry-runtime/releases'/head;release.mkdir(mode=0o700,parents=True,exist_ok=False)
 names=('hepta_btc_entry_policy.py','heptatrader_btc_economics_driver.py','heptatrader_paper_driver.py','hepta_verified_loader.py','hepta_native_reader.py');files={}
 for name in names:
  dst=release/name;dst.write_bytes((src/name).read_bytes());dst.chmod(0o400);files[name]=sha(dst)
 config={'schema':'heptatrader.btc-entry-runtime.v1','source_sha':head,'profile':PROFILE,'period_sha256':hashlib.sha256(period_raw).hexdigest(),
   'initial_control':control,'seed_oms_prefix_sha256':hashlib.sha256(raw).hexdigest(),'seed_oms_prefix_bytes':len(raw),
   'seeded_at_ms':now,'fee_source':'https://www.interactivebrokers.com/en/pricing/commissions-cryptocurrencies.php',
   'fee_kind':'CONSERVATIVE_UPFRONT_NOT_REBATE_QUALIFIED','automatic_live_trading':False}
 profile=release/'profile.json';once(profile,config,0o400)
 dropdir=h/'.config/systemd/user/heptatrader-crypto-driver.service.d';dropdir.mkdir(mode=0o700,parents=True,exist_ok=True)
 drop=dropdir/'30-btc-entry-economics.conf'
 if drop.exists():raise SystemExit('EXISTING_DROPIN_REQUIRES_SEPARATE_UPDATE')
 text='[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B '+str(release/'heptatrader_btc_economics_driver.py')+' --base-driver '+str(release/'heptatrader_paper_driver.py')+' --profile '+str(profile)+'\n'
 with drop.open('x') as out:out.write(text)
 drop.chmod(0o600)
 subprocess.run(['systemctl','--user','daemon-reload'],check=True)
 if state_path.read_bytes()!=state_raw or period_path.read_bytes()!=period_raw:
  drop.unlink();subprocess.run(['systemctl','--user','daemon-reload'],check=True)
  raise SystemExit('CONCURRENT_PERIOD_OR_STATE_CHANGE_NO_START')
 os.close(lock_fd)
 try:
  if a.start:
   subprocess.run(['systemctl','--user','start','heptatrader-crypto-driver.service'],check=True)
   time.sleep(2)
   ready=show('heptatrader-crypto-driver.service')
   if ready.get('ActiveState')!='active' or ready.get('MainPID') in (None,'0'):
    raise RuntimeError('BTC_WRAPPER_STARTUP_FAILED')
 except Exception:
  subprocess.run(['systemctl','--user','stop','heptatrader-crypto-driver.service'])
  drop.unlink();subprocess.run(['systemctl','--user','daemon-reload'])
  raise
 result={'schema':'heptatrader.btc-entry-deploy.v1','source_sha':head,'at_ms':int(time.time()*1000),
  'release':str(release),'runtime_sha256':files,'profile_sha256':sha(profile),'dropin_sha256':sha(drop),
  'startup_requested':a.start,'predeployment_authoritative_position':snap['position'],
  'predeployment_active_orders':len(snap['active']),'initial_control':control,
  'other_services_before':before,'other_services_after':{name:show(name) for name in watched},
  'btc_service':show('heptatrader-crypto-driver.service'),'state_flags_cleared':False,'new_canary_sent':False,
  'base_driver_bytes_changed':False,'fx_strategy_changed':False,'broker_orders_sent_by_installer':0,
  'actual_commission_or_pnl_accounting_changed':False}
 a.evidence_dir.mkdir(mode=0o700,parents=True,exist_ok=True);once(a.evidence_dir/'deployment.json',result)
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
