"""Bounded independent registrar: immutable plans and distinct local witness key."""
from __future__ import annotations
import argparse,fcntl,json,os,signal,time
from pathlib import Path
from .integrity import read_json,sha_file,ContractError
_original_sha_file=sha_file
def sha_file(path):return _original_sha_file(Path(path))
from .resident_store import atomic
from .financial_forward_source import register_available
from .financial_forward_witness import verify_trust


def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();os.umask(0o077)
 c=read_json(a.config);root=Path(c['forward_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True);verify_trust(c)
 lock=os.open(root/'registrar.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 stopped=False
 def stop(*_):
  nonlocal stopped
  stopped=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 while not stopped:
  try:
   if sha_file(c['period_path'])!=c['period_sha256']:raise ContractError('REGISTRATION_PERIOD_CHANGED')
   if sum(x.stat().st_size for x in root.rglob('*') if x.is_file())>c['forward_max_bytes']:raise ContractError('FORWARD_STORAGE_BOUND_NO_PRUNING')
   plans=register_available(c)
   status={'schema':'heptatrader.financial-forward-registry-status.v1','at_ms':int(time.time()*1000),
           'plans':len(plans),'plan_ids':[x['plan_id'] for x in plans],'refit_allowed':False,'canonical_agentd_admitted':False,'order_authority':False}
  except Exception as exc:status={'state':'REGISTRATION_UNAVAILABLE','at_ms':int(time.time()*1000),'reason':str(exc)[:200]}
  atomic(root/'registry-status.json',status)
  if a.once:break
  for _ in range(10):
   if stopped:break
   time.sleep(1)
 os.close(lock)
if __name__=='__main__':main()
