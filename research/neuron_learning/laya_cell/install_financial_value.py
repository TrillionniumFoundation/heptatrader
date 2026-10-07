"""Install the independent financial value service without changing trading units."""
from __future__ import annotations
import argparse,json,os,subprocess
from pathlib import Path
from .integrity import ContractError,read_json,sha_file,digest,canonical
from .financial_value_data import PROFILE


def put(path,raw,mode=0o400):
 path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 if path.exists():
  if path.read_bytes()!=raw:raise ContractError('IMMUTABLE_FINANCIAL_RELEASE_CONFLICT')
  return
 with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 path.chmod(mode)

def install(config_path,start):
 if os.geteuid()==0:raise ContractError('USER_ONLY_VALUE_SERVICE')
 h=Path.home();cell=Path(__file__).resolve().parent;learn=cell.parent;repo=learn.parent.parent
 c=read_json(config_path)
 if c.get('automatic_trading') is not False or sha_file(Path(c['period_path']))!=c['period_sha256']:raise ContractError('FINANCIAL_AUTHORITY_OR_PERIOD')
 files={}
 for p in sorted(cell.glob('*.py')):files['learn/laya_cell/'+p.name]=p
 for n in ('data.py','sandbox.py'):files['learn/'+n]=learn/n
 for n in ('hepta_pnl_accounting.py','hepta_decision_attribution.py'):files['scripts/'+n]=repo/'scripts'/n
 files['bin/native']=Path(c['native_binary']);inventory={n:sha_file(p) for n,p in files.items()};identity=digest(inventory)
 root=h/'.local/share/heptatrader/financial-value/releases'/identity
 for name,path in files.items():put(root/name,path.read_bytes(),0o500 if name=='bin/native' else 0o400)
 c.update(value_runtime=str(root),value_runtime_sha256=inventory,financial_profile=PROFILE,financial_worker_sandbox=True,
   native_binary=str(root/'bin/native'),value_output_root=str(h/'.local/state/heptatrader/neuron-learning/financial-value-resident'/c['period_sha256']))
 config_id=digest(c);cfg=h/'.config/heptatrader-financial-value'/(config_id+'.json');put(cfg,canonical(c)+b'\n')
 out=Path(c['value_output_root']);out.mkdir(mode=0o700,parents=True,exist_ok=True)
 unit=h/'.config/systemd/user/heptatrader-laya-financial-value.service'
 text=f'''[Unit]
Description=HeptaTrader independent financial value candidates (no trading authority)

[Service]
Type=simple
ExecStart={c['python']} -B -m laya_cell.financial_value_service --config {cfg}
WorkingDirectory={root}
Environment=PYTHONPATH={root}/learn:{root}/scripts
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=OPENBLAS_NUM_THREADS=1
Environment=OMP_NUM_THREADS=2
Environment=HF_HUB_OFFLINE=1
Environment=TRANSFORMERS_OFFLINE=1
Environment=USE_TF=0
UMask=0077
Nice=15
CPUQuota=100%
CPUWeight=10
MemoryHigh=3G
MemoryMax=4G
TasksMax=96
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
 put(unit,text.encode(),0o600);subprocess.run(['systemctl','--user','daemon-reload'],check=True)
 if start:subprocess.run(['systemctl','--user','enable','--now',unit.name],check=True)
 receipt={'schema':'heptatrader.financial-value-install.v1','runtime':str(root),'runtime_sha256':inventory,
  'configuration':str(cfg),'configuration_sha256':sha_file(cfg),'output_root':str(out),'unit':str(unit),
  'start_requested':start,'trading_units_changed':False,'scorer_or_action_units_changed':False,
  'worker_sandbox_required':True,'financial_target':'EXACT_SETTLED_NET_USD','future_generalization_proven':False}
 path=cfg.with_suffix('.install.json');put(path,canonical(receipt)+b'\n');print(json.dumps(receipt,indent=2));return receipt

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--start',action='store_true');a=p.parse_args();os.umask(0o077);install(a.config,a.start)
if __name__=='__main__':main()
