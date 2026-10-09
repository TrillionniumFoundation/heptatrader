"""Install only an isolated read-only reward-consumer service and timer.
No existing trading, NAV, quote or trainer unit is touched. Reuse pinned source.
"""
from __future__ import annotations
import argparse,json,os,subprocess
from pathlib import Path
from .common import EvidenceError,canonical,digest,read,sha,write_once,atomic
from .current100k import validate_config

UNIT='heptatrader-current100k-consumer'

def install(base_config,receipt_path):
    validate_config(base_config)
    repo=Path(__file__).resolve().parents[2];home=Path.home()
    inventory={}
    for folder in ('research/neuron_learning','research/paired_net_100k_preflight','research/historical_training'):
        for p in (repo/folder).rglob('*.py'):
            if 'tests' in p.parts or '__pycache__' in p.parts:continue
            if p.is_symlink():raise EvidenceError('SOURCE_SYMLINK')
            inventory[str(p.relative_to(repo))]=sha(p)
    for name in ('hepta_pnl_accounting.py','hepta_decision_attribution.py'):
        p=repo/'scripts'/name;inventory[str(p.relative_to(repo))]=sha(p)
    release_id=digest(inventory)
    release=home/'.local/share/heptatrader/current100k-consumer/releases'/release_id
    release.mkdir(mode=0o700,parents=True,exist_ok=True)
    for name,wanted in inventory.items():
        source=repo/name;target=release/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if target.exists():
            if target.is_symlink() or sha(target)!=wanted:raise EvidenceError('RELEASE_CONFLICT')
        else:
            raw=source.read_bytes()
            if __import__('hashlib').sha256(raw).hexdigest()!=wanted:raise EvidenceError('SOURCE_CHANGED_DURING_PACKAGE')
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    write_once(release/'manifest.json',{'schema':'heptatrader.current100k-release.v1','release_id':release_id,'files':inventory})
    config={**base_config,'runtime_root':str(release),'runtime_sha256':inventory}
    path=home/'.config/heptatrader-current100k-consumer'/(digest(config)+'.json')
    write_once(path,config)
    pythonpath=':'.join(str(release/p) for p in ('research','research/neuron_learning','scripts'))
    unit='\n'.join(('[Unit]','Description=HeptaTrader isolated current100k evidence consumer; no training or orders','',
        '[Service]','Type=oneshot',f'WorkingDirectory={release}',
        f'ExecStart=/usr/bin/python3 -B -m historical_training.consumer_launch --config {path}',
        'Environment=PYTHONDONTWRITEBYTECODE=1',f'Environment=PYTHONPATH={pythonpath}',
        'UMask=0077','Nice=15','CPUQuota=25%','CPUWeight=10','MemoryMax=512M','TasksMax=16',
        'NoNewPrivileges=yes','RestrictSUIDSGID=yes','LockPersonality=yes',
        'RestrictAddressFamilies=AF_UNIX','TimeoutStartSec=45','TimeoutStopSec=10','KillMode=control-group',''))
    timer='\n'.join(('[Unit]','Description=HeptaTrader current100k read-only evidence refresh','',
        '[Timer]','OnActiveSec=10','OnUnitInactiveSec=60','AccuracySec=1',f'Unit={UNIT}.service','',
        '[Install]','WantedBy=timers.target',''))
    units=home/'.config/systemd/user';units.mkdir(parents=True,exist_ok=True)
    prior={}
    for extension,text in (('service',unit),('timer',timer)):
        target=units/(UNIT+'.'+extension)
        if target.exists():
            prior[extension]=target.read_text()
            if 'HeptaTrader' not in prior[extension] or 'current100k' not in prior[extension]:
                raise EvidenceError('REFUSE_REPLACE_UNRELATED_UNIT')
        tmp=target.with_suffix(target.suffix+'.new')
        tmp.write_text(text);tmp.chmod(0o600);os.replace(tmp,target)
    write_once(receipt_path,{'schema':'heptatrader.current100k-install.v1','release_id':release_id,
        'release_directory':str(release),'manifest_sha256':sha(release/'manifest.json'),
        'config_path':str(path),'config_sha256':sha(path),'previous_units':prior,
        'units':[str(units/(UNIT+'.'+x)) for x in ('service','timer')],
        'only_new_consumer_units_changed':True,'trading_services_modified':False,
        'activation_requested':True,'rollback':['systemctl --user disable --now '+UNIT+'.timer',
            'systemctl --user stop '+UNIT+'.service'],
        'note':'Activation and recovery proof are recorded separately after real execution.'})
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','start',UNIT+'.service'],check=True,timeout=50)
    subprocess.run(['systemctl','--user','enable','--now',UNIT+'.timer'],check=True,timeout=10)
    print(json.dumps({'release_id':release_id,'config_path':str(path),'timer':UNIT+'.timer'}))

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    a=p.parse_args();os.umask(0o077);install(read(a.config),a.receipt)
if __name__=='__main__':main()
