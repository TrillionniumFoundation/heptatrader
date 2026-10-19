"""Install only Laya read-only-feed, candidate scheduler and independent evaluator.

The trainer remains a user service with no authority to start PAPER or select a
production model. Runtime bytes/config are content-addressed and never edited.
"""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,subprocess
from pathlib import Path
from .integrity import ContractError,canonical,digest,read_json,sha_file


def install(config_path,start,update_owned=False,only=None):
 h=Path.home();source=Path(__file__).resolve().parent;learn=source.parent;repo=learn.parent.parent
 c=read_json(config_path)
 if os.geteuid()==0 or c.get('automatic_trading') is not False:raise ContractError('USER_ONLY_NO_TRADE_AUTHORITY')
 files={}
 for p in sorted(source.glob('*.py')):files['learn/laya_cell/'+p.name]=p
 for name in ('data.py','sandbox.py'):files['learn/'+name]=learn/name
 # Existing authoritative accounting replay is pinned with this release.
 for name in ('hepta_pnl_accounting.py','hepta_decision_attribution.py','hepta_pnl_watch.py','hepta_change_watch.py'):
  files['scripts/'+name]=repo/'scripts'/name
 files['bin/hepta-trader-neuron-native']=Path(c['native_binary'])
 inventory={name:sha_file(path) for name,path in files.items()};release_id=digest(inventory)
 release=h/'.local/share/heptatrader/laya-resident/releases'/release_id;release.mkdir(mode=0o700,parents=True,exist_ok=True)
 for name,src in files.items():
  dst=release/name;dst.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  if dst.exists():
   if sha_file(dst)!=inventory[name]:raise ContractError('IMMUTABLE_RESIDENT_RELEASE_CHANGED')
  else:
   with dst.open('xb') as out:out.write(src.read_bytes());out.flush();os.fsync(out.fileno())
   dst.chmod(0o500 if name.startswith('bin/') else 0o400)
 c.update(runtime_directory=str(release),runtime_sha256=inventory,require_worker_sandbox=True,
          native_binary=str(release/'bin/hepta-trader-neuron-native'))
 cfgdir=h/'.config/heptatrader-laya-resident';cfgdir.mkdir(mode=0o700,parents=True,exist_ok=True)
 config_id=digest({'release_id':release_id,'configuration':c})
 cfg=cfgdir/(config_id+'.json');raw=canonical(c)+b'\n'
 if cfg.exists() and cfg.read_bytes()!=raw:raise ContractError('IMMUTABLE_CONFIGURATION_CHANGED')
 if not cfg.exists():cfg.write_bytes(raw);cfg.chmod(0o400)
 for k in ('control_directory','feed_directory','evaluation_directory','candidate_directory'):
  Path(c[k]).mkdir(mode=0o700,parents=True,exist_ok=True)
 units={}
 for name,module,python,memory,cpu,description in (
  ('heptatrader-laya-feed.service','resident_feed','/usr/bin/python3','256M','20%','Read-only quote source for Laya candidates'),
  ('heptatrader-laya-trainer.service','resident',c['python'],'6G','150%','Bounded resident Laya candidate training on Radeon 890M'),
  ('heptatrader-laya-evaluator.service','resident_evaluate',c['python'],'6G','150%','Independent Laya future evaluation; no self-promotion')):
  if only is not None and name!='heptatrader-laya-'+only+'.service':continue
  unit=h/'.config/systemd/user'/name
  text=f'''[Unit]
Description=HeptaTrader {description}

[Service]
Type=simple
ExecStart={python} -B -m laya_cell.{module} --config {cfg}
WorkingDirectory={release}
Environment=PYTHONPATH={release}/learn:{release}/scripts
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=OPENBLAS_NUM_THREADS=1
Environment=OMP_NUM_THREADS=2
Environment=HF_HUB_OFFLINE=1
Environment=TRANSFORMERS_OFFLINE=1
Environment=USE_TF=0
UMask=0077
Nice=15
CPUQuota={cpu}
CPUWeight=10
MemoryHigh={memory}
MemoryMax={memory}
TasksMax=96
IOSchedulingClass=idle
NoNewPrivileges=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX
Restart=on-failure
RestartSec=30
KillMode=control-group
TimeoutStopSec=20

[Install]
WantedBy=default.target
'''
  if unit.exists() and unit.read_text()!=text:
   previous=unit.read_text()
   if not update_owned or '/laya-resident/releases/' not in previous or 'Description=HeptaTrader ' not in previous:
    raise ContractError('EXISTING_SERVICE_REQUIRES_EXPLICIT_UPDATE:'+name)
   backup=cfgdir/(name+'.'+sha_file(unit)+'.before')
   if not backup.exists():backup.write_text(previous);backup.chmod(0o400)
   subprocess.run(['systemctl','--user','stop',name],check=True)
   tmp=unit.with_name(unit.name+'.next');tmp.write_text(text);tmp.chmod(0o600);os.replace(tmp,unit)
  if not unit.exists():unit.write_text(text);unit.chmod(0o600)
  units[name]={'path':str(unit),'sha256':sha_file(unit)}
 subprocess.run(['systemctl','--user','daemon-reload'],check=True)
 if start:subprocess.run(['systemctl','--user','enable','--now',*units],check=True)
 receipt={'schema':'heptatrader.laya-resident-install.v1','release_id':release_id,'release':str(release),
  'runtime_sha256':inventory,'configuration':str(cfg),'configuration_sha256':sha_file(cfg),'units':units,
  'start_requested':start,'paper_services_changed':False,'automatic_trading':False,'worker_sandbox_required':True,
  'canonical_agentd_owner_connected':False,'promotion_mode':'STATISTICAL_QUALIFICATION_THEN_WAIT_FOR_CANONICAL_OWNER'}
 out=cfgdir/(config_id+('.'+only if only else '')+'.install.json')
 if out.exists() and out.read_bytes()!=canonical(receipt)+b'\n':raise ContractError('INSTALL_RECEIPT_CHANGED')
 if not out.exists():out.write_bytes(canonical(receipt)+b'\n');out.chmod(0o400)
 print(json.dumps(receipt,indent=2));return receipt

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--start',action='store_true');p.add_argument('--update-owned',action='store_true');p.add_argument('--only',choices=['trainer','feed','evaluator']);a=p.parse_args();os.umask(0o077);install(a.config,a.start,a.update_owned,a.only)
if __name__=='__main__':main()
