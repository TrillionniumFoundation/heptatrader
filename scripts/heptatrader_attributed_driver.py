#!/usr/bin/env python3
"""Observation-only launcher over immutable existing FX or BTC PAPER behavior."""
from __future__ import annotations
import argparse,hashlib,os,sys
from pathlib import Path
from hepta_verified_loader import load_verified_module
from hepta_decision_attribution import Capture,Journal,read_private,strict


def load_manifest(path):
 raw=read_private(path,65536);m=strict(raw)
 if (m.get('schema')!='heptatrader.attribution-producer.v1' or m.get('execution_domain')!='PAPER'
     or m.get('instrument') not in ('EUR.USD','BTC.USD')):raise ValueError('ATTRIBUTION_LAUNCH_SCOPE')
 # The optional FX opening limiter has been retired. Reject old manifests
 # rather than silently running a legacy restricted or mismatched profile.
 if m.get('fx_churn_profile_sha256'):raise ValueError('FX_CHURN_RETIRED')
 if type(m.get('native_read_stream',False)) is not bool:raise ValueError('NATIVE_READER_MODE_INVALID')
 if 'native_read_binary' in m and m.get('native_read_stream') is not True:raise ValueError('NATIVE_READ_BINARY_REQUIRES_STREAM')
 if 'native_clients' in m and 'hepta_native_reader.py' not in m['runtime_sha256']:raise ValueError('NATIVE_CLIENT_RUNTIME_NOT_PINNED')
 period=read_private(m['period_path'],65536)
 if hashlib.sha256(period).hexdigest()!=m['period_sha256'] or strict(period).get('execution_domain')!='PAPER':
  raise ValueError('PERIOD_CHANGED_NO_LAUNCH')
 root=Path(__file__).resolve().parent
 for name,expected in m['runtime_sha256'].items():
  if Path(name).name!=name or hashlib.sha256(read_private(root/name,1048576)).hexdigest()!=expected:
   raise ValueError('PINNED_RUNTIME_CHANGED')
 return m,hashlib.sha256(raw).hexdigest()


def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True)
 p.add_argument('--once',action='store_true');p.add_argument('--reconcile-only',action='store_true');p.add_argument('--recover-owner',action='store_true')
 p.add_argument('--native-read-stream',action='store_true');a=p.parse_args();os.umask(0o077)
 m,manifest_sha=load_manifest(a.manifest);root=Path(__file__).resolve().parent;base=root/'heptatrader_paper_driver.py'
 stream=a.native_read_stream or m.get('native_read_stream') is True
 if stream and 'hepta_native_reader.py' not in m['runtime_sha256']:raise ValueError('NATIVE_READER_RUNTIME_NOT_PINNED')
 driver=load_verified_module('hepta_attributed_pinned_base',base,m['runtime_sha256']['heptatrader_paper_driver.py'])
 if stream or 'native_clients' in m:
  reader=load_verified_module('hepta_attributed_pinned_reader',root/'hepta_native_reader.py',m['runtime_sha256']['hepta_native_reader.py'])
  if 'native_clients' in m:
   reader.validate_client_bindings(m['native_clients'])
   for binding in m['native_clients'].values():
    with reader.verified_binary(binding):pass
   if stream and m.get('native_read_binary')!=m['native_clients']['heptactl']:
    raise ValueError('NATIVE_CLIENT_READ_BINDING_MISMATCH')
   driver.NATIVE_CLIENTS={name:dict(binding) for name,binding in m['native_clients'].items()}
   driver.NATIVE_BINARY_VERIFIER=reader.verified_binary
 if stream:
  driver.NATIVE_READER_CLASS=reader.NativeReader
  if 'native_read_binary' in m:
   reader.validate_binary_binding(m['native_read_binary'])
   driver.NATIVE_READ_BINARY=dict(m['native_read_binary'])
 driver.INSTRUMENT=m['instrument'];close_economics=None
 if m['instrument']=='BTC.USD' and not (a.reconcile_only or a.recover_owner):
  import heptatrader_btc_economics_driver as btc
  if m['runtime_sha256']['heptatrader_paper_driver.py']!=btc.BASE_SHA256:raise ValueError('BTC_BASE_BINDING')
  profile=root/'profile.json'
  if hashlib.sha256(read_private(profile,65536)).hexdigest()!=m['btc_profile_sha256']:raise ValueError('BTC_PROFILE_CHANGED')
  close_economics=btc.attach(driver,btc.load_profile(profile))

 observer=None
 try:
  # Once the already-pinned trading program is loaded, optional evidence failure
  # cannot remove legacy risk exits. It only makes attribution unavailable.
  try:
   journal=Journal(m['journal_directory'],m,manifest_sha)
   observer=Capture(journal,driver,m).install()
  except Exception as exc:print('DECISION_ATTRIBUTION_UNAVAILABLE:'+type(exc).__name__,file=sys.stderr,flush=True)
  sys.argv=[str(base),'--instrument',m['instrument']]+(['--once'] if a.once else [])+(['--reconcile-only'] if a.reconcile_only else [])+(['--recover-owner'] if a.recover_owner else [])+(['--native-read-stream'] if stream else [])
  return driver.main()
 finally:
  if observer is not None:observer()
  if close_economics is not None:close_economics()
if __name__=='__main__':raise SystemExit(main())
