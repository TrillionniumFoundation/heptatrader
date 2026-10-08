"""Install a newly frozen independent price trial without touching trading."""
from __future__ import annotations
import argparse,hashlib,json,os,subprocess,time
from pathlib import Path
from laya_cell.integrity import ContractError,canonical,digest,read_json,sha_file
from .common import PROFILE,RULE,check_candidate,check_plan,require,utc_ms,immutable_json

def snapshot(name):
 data=subprocess.check_output(['systemctl','--user','show',name,'-p','MainPID','-p','ActiveState','-p','NRestarts'],text=True)
 return dict(x.split('=',1) for x in data.splitlines() if '=' in x)
def install(input_config,candidate_dir,start):
 home=Path.home();src=Path(__file__).resolve().parent;repo=src.parents[1];learn=repo/'research/neuron_learning'
 c=read_json(input_config);candidate=check_candidate(candidate_dir)
 require(c.get('automatic_trading') is False and sha_file(Path(c['period_path']))==c['period_sha256'],'PERIOD_CHANGED')
 require(candidate['period_sha256']==c['period_sha256'],'CANDIDATE_DIFFERENT_PERIOD')
 for name,sha in candidate['source_sha256'].items():
  require(sha_file(src/name)==sha,'TRAINING_SOURCE_CHANGED:'+name)
 require(candidate['trained_until_ms']<=candidate['created_at_ms']<=utc_ms(),'CANDIDATE_NOT_FROZEN')
 before_names=('heptatrader-paper-driver.service','heptatrader-crypto-driver.service','heptatrader-execution.service',
  'heptatrader-gateway.service','heptatrader-temporal-fusion-predictor.service','heptatrader-temporal-fusion-evaluator.service',
  'heptatrader-laya-trainer.service','heptatrader-laya-financial-value.service')
 before={x:snapshot(x) for x in before_names}
 files={}
 for p in src.glob('*.py'):files['paired_price/'+p.name]=p
 for p in (learn/'laya_cell').glob('*.py'):files['learn/laya_cell/'+p.name]=p
 for name in ('data.py','sandbox.py'):files['learn/'+name]=learn/name
 hashes={name:sha_file(p) for name,p in files.items()}
 release_id=digest(hashes);release=home/'.local/share/heptatrader/paired-price/releases'/release_id
 release.mkdir(mode=0o700,parents=True,exist_ok=True)
 for name,p in files.items():
  target=release/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  if target.exists():require(sha_file(target)==hashes[name],'RELEASE_CHANGED')
  else:
   with target.open('xb') as f:f.write(p.read_bytes());f.flush();os.fsync(f.fileno())
   target.chmod(0o400)
 state=home/'.local/state/heptatrader/neuron-learning/paired-price'/c['period_sha256']/candidate['candidate_id']/release_id
 state.mkdir(mode=0o700,parents=True,exist_ok=True)
 registered=utc_ms();start_ms=registered+120000
 end_ms=start_ms+(RULE['slots']-1)*RULE['slot_spacing_ms']+RULE['input_grace_ms']+RULE['horizon_ms']+90000
 plan={'schema':'heptatrader.paired-price-prospective-plan.v1','registered_at_ms':registered,'start_ms':start_ms,'end_ms':end_ms,
  'candidate_created_at_ms':candidate['created_at_ms'],'candidate_id':candidate['candidate_id'],
  'period_sha256':c['period_sha256'],'rule':RULE,'runtime_sha256':hashes,
  'training_expert_ids':candidate['expert_row_ids'],'training_gate_ids':candidate['gate_row_ids'],
  'numeric_independent':True,'legacy_numeric_frozen_comparator':candidate['legacy_numeric_id'],
  'candidate_model_sha256':candidate['trained_model_sha256'],
  'effect_authority':False,'automatic_selection':False}
 plan['plan_id']=digest(plan);check_plan(plan);immutable_json(state/'plan.json',plan)
 config={**c,'shadow_root':str(state),'runtime_directory':str(release),'runtime_sha256':hashes,
  'candidate_directory':str(candidate_dir),'automatic_trading':False}
 cfgdir=home/'.config/heptatrader-paired-price';cfgdir.mkdir(mode=0o700,parents=True,exist_ok=True)
 cfg=cfgdir/(digest(config)+'.json');immutable_json(cfg,config)
 services={}
 for mode in ('predictor','evaluator'):
  unit='heptatrader-paired-price-'+mode+'.service';path=home/'.config/systemd/user'/unit
  if path.exists():raise ContractError('EXISTING_FUTURE_UNIT_REFUSE_REPLACE')
  python=c['python'] # single pinned runtime, independent evaluators never load torch
  contents=f"""[Unit]
Description=HeptaTrader isolated paired-price {mode} prospective shadow (no orders)
[Service]
Type=simple
ExecStart={python} -B -m paired_price.launch --config {cfg} --mode {mode}
WorkingDirectory={release}
Environment=PYTHONPATH={release}/learn:{release}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=OMP_NUM_THREADS=2
Environment=OPENBLAS_NUM_THREADS=1
Environment=HF_HUB_OFFLINE=1
Environment=TRANSFORMERS_OFFLINE=1
Environment=USE_TF=0
UMask=0077
Nice=15
CPUQuota={'150%' if mode=='predictor' else '50%'}
CPUWeight=10
MemoryMax={'5G' if mode=='predictor' else '768M'}
TasksMax=96
NoNewPrivileges=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_UNIX
LockPersonality=yes
Restart=on-failure
RestartSec=30
KillMode=control-group
TimeoutStopSec=20
[Install]
WantedBy=default.target
"""
  with path.open('xb') as f:f.write(contents.encode());f.flush();os.fsync(f.fileno())
  path.chmod(0o600);services[mode]=unit
 subprocess.run(['systemctl','--user','daemon-reload'],check=True)
 if start:subprocess.run(['systemctl','--user','enable','--now',*services.values()],check=True)
 after={name:snapshot(name) for name in before}
 require(all(before[name]['MainPID']==after[name]['MainPID'] for name in before),'PROTECTED_PROCESS_CHANGED')
 receipt={'schema':'heptatrader.paired-price-install.v1','source_commit':subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),
  'at_ms':utc_ms(),'release':str(release),'release_id':release_id,'runtime_files':len(hashes),
  'runtime_hashes':hashes,'config':str(cfg),'state':str(state),'plan':plan,'services':services,
  'existing_services_unchanged':True,'broker_orders_sent':0,'trading_authority':False,'automatic_selection':False}
 print(json.dumps({k:v for k,v in receipt.items() if k not in ('runtime_hashes',)},indent=2))
 return receipt

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--start',action='store_true');a=p.parse_args()
 os.umask(0o077);install(a.config,a.candidate,a.start)
if __name__=='__main__':main()
