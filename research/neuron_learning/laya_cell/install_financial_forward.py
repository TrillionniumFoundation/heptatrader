"""Create independent read-only forward services; no trading or trainer restart."""
from __future__ import annotations
import argparse,hashlib,json,os,re,subprocess,time
from pathlib import Path
from .integrity import read_json,digest,canonical,sha_file,ContractError
_original_sha_file=sha_file
def sha_file(path):return _original_sha_file(Path(path))


def write_once(path,raw,mode=0o400):
 path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 if path.exists():
  if path.read_bytes()!=raw:raise ContractError('IMMUTABLE_INSTALL_CONFLICT:'+str(path))
  return
 with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 path.chmod(mode)


def install(config_path,native_path,start=False,update_owned=False):
 if os.geteuid()==0:raise ContractError('USER_ONLY')
 h=Path.home();src=Path(__file__).resolve().parent;learn=src.parent;repo=learn.parent.parent
 c=read_json(config_path);period=Path(c['period_path'])
 if sha_file(period)!=c['period_sha256'] or read_json(period).get('execution_domain')!='PAPER':raise ContractError('CURRENT_PAPER_PERIOD_REQUIRED')
 fx=subprocess.check_output(['systemctl','--user','show','heptatrader-paper-driver.service','-p','ExecStart','--value'],text=True)
 found=re.search(r'--manifest\s+(\S+)',fx)
 if not found:raise ContractError('EXACT_FX_PRODUCER_NOT_RUNNING')
 manifest_path=Path(found.group(1));active=sha_file(manifest_path)
 regs=c.get('decision_attribution_registrations',{}).get('EUR.USD',[])
 if not any(r['sha256']==active and Path(r['manifest'])==manifest_path for r in regs):raise ContractError('ACTIVE_BEHAVIOR_NOT_REGISTERED')
 root=h/'.local/state/heptatrader/neuron-learning/financial-forward'/c['period_sha256'];root.mkdir(mode=0o700,parents=True,exist_ok=True)
 cfgdir=h/'.config/heptatrader-financial-forward';cfgdir.mkdir(mode=0o700,parents=True,exist_ok=True)
 keys=cfgdir/'role-keys';keys.mkdir(mode=0o700,exist_ok=True)
 # Generate only independent local witness identities; no canonical issuer key.
 command='''from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import json,os,sys
root=Path(sys.argv[1]);out={}
for role in ('registrar','predictor','evaluator','selector'):
 p=root/(role+'.key')
 if not p.exists():
  key=Ed25519PrivateKey.generate()
  fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'wb') as f:f.write(key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption()));f.flush();os.fsync(f.fileno())
 else:key=Ed25519PrivateKey.from_private_bytes(p.read_bytes())
 out[role]=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex()
print(json.dumps(out))'''
 pub=json.loads(subprocess.check_output(['/usr/bin/python3','-c',command,str(keys)],text=True))
 trust={'schema':'heptatrader.financial-local-witness-trust.v1','scope':c['period_sha256'],'roles':pub,'canonical_agentd_issuer':False}
 trustfile=cfgdir/('witness-'+digest(trust)+'.json');write_once(trustfile,canonical(trust)+b'\n')
 files={}
 for p in sorted(src.glob('*.py')):files['learn/laya_cell/'+p.name]=p
 for name in ('data.py','sandbox.py'):files['learn/'+name]=learn/name
 for name in ('hepta_pnl_accounting.py','hepta_decision_attribution.py'):files['scripts/'+name]=repo/'scripts'/name
 files['bin/hepta-financial-owner-verify']=native_path
 hashes={name:sha_file(path) for name,path in files.items()};release_id=digest(hashes)
 release=h/'.local/share/heptatrader/financial-forward/releases'/release_id;release.mkdir(mode=0o700,parents=True,exist_ok=True)
 for name,path in files.items():write_once(release/name,path.read_bytes(),0o500 if name.startswith('bin/') else 0o400)
 c.update(forward_root=str(root),forward_runtime=str(release),forward_runtime_sha256=hashes,
  forward_max_bytes=512*1024**2,active_behavior_manifest_sha256=active,
  witness_trust_path=str(trustfile),witness_trust_sha256=sha_file(trustfile),witness_trust_digest=digest(trust),
  role_key_paths={role:str(keys/(role+'.key')) for role in pub},
  canonical_verifier_binary=str(release/'bin/hepta-financial-owner-verify'),canonical_verifier_sha256=hashes['bin/hepta-financial-owner-verify'],
  canonical_owner_registration=None,canonical_owner_readonly_paths=[],canonical_product_route_enrolled=False)
 services={};configs={}
 for role in pub:
  config={**c,'signing_role':role,'role_key_path':c['role_key_paths'][role]};cid=digest(config);cfg=cfgdir/(role+'-'+cid+'.json')
  write_once(cfg,canonical(config)+b'\n');configs[role]=str(cfg)
  name='heptatrader-financial-forward-'+role+'.service';unit=h/'.config/systemd/user'/name
  py=c['python'] if role=='predictor' else '/usr/bin/python3'
  text=f'''[Unit]
Description=HeptaTrader independent financial forward {role} (no order authority)

[Service]
Type=simple
ExecStart={py} -B -m laya_cell.financial_forward_launch --config {cfg}
WorkingDirectory={release}
Environment=PYTHONPATH={release}/learn:{release}/scripts
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=OMP_NUM_THREADS=2
Environment=OPENBLAS_NUM_THREADS=1
Environment=HF_HUB_OFFLINE=1
Environment=TRANSFORMERS_OFFLINE=1
Environment=USE_TF=0
UMask=0077
Nice=15
CPUQuota={'150%' if role=='predictor' else '50%'}
CPUWeight=10
MemoryMax={'4G' if role=='predictor' else '512M'}
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
'''
  if unit.exists() and unit.read_text()!=text:
   if not update_owned or 'independent financial forward' not in unit.read_text():raise ContractError('EXISTING_UNIT_NOT_OWNED')
   write_once(cfgdir/(name+'.'+sha_file(unit)+'.before'),unit.read_bytes())
   subprocess.run(['systemctl','--user','stop',name],check=True)
   temp=unit.with_suffix('.next');temp.write_text(text);temp.chmod(0o600);os.replace(temp,unit)
  elif not unit.exists():write_once(unit,text.encode(),0o600)
  services[role]=name
 subprocess.run(['systemctl','--user','daemon-reload'],check=True)
 if start:subprocess.run(['systemctl','--user','enable','--now',*services.values()],check=True)
 receipt={'schema':'heptatrader.financial-forward-install.v1','installed_at_ms':int(time.time()*1000),'release':str(release),'source_sha':subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),
  'runtime_sha256':hashes,'configs':configs,'services':services,'forward_root':str(root),'active_behavior_manifest_sha256':active,
  'native_verifier_sha256':c['canonical_verifier_sha256'],'witness_public_trust_sha256':sha_file(trustfile),
  'canonical_agentd_admitted':False,'canonical_owner_registration':None,'trading_services_touched':False,'existing_learning_services_touched':False,
  'automatically_selects_without_canonical_signature':False}
 print(json.dumps(receipt,indent=2));return receipt


def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--native-verifier',type=Path,required=True);p.add_argument('--start',action='store_true');p.add_argument('--update-owned',action='store_true');a=p.parse_args();os.umask(0o077);install(a.config,a.native_verifier,a.start,a.update_owned)
if __name__=='__main__':main()
