#!/usr/bin/env python3
"""Install a hash-pinned, resource-bounded learning sidecar; no PAPER unit changes."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

SOURCES=('data.py','gpu.py','economics.py','runner.py','evaluate.py','sandbox.py')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def installation_identity(inventory,config):
    # Relocated binary paths are determined by their bytes. All other effective
    # config values must participate, so a config update gets a new identity.
    effective=dict(config)
    for name in ('native_binary','gpu_library','accounting_module'):
        effective.pop(name,None)
    effective['require_sandbox']=True
    effective['python_sources_sha256']={n:inventory[n] for n in SOURCES}
    return hashlib.sha256(json.dumps({'files':inventory,'config':effective},sort_keys=True).encode()).hexdigest()


def check_unit_updates(definitions,update_owned):
    # Check the complete pair before stopping or rewriting either sidecar.
    for unit,text in definitions.items():
        if not unit.exists():continue
        existing=unit.read_text()
        if existing==text:continue
        if not update_owned or 'HeptaTrader' not in existing or '/neuron-learning/releases/' not in existing:
            raise RuntimeError('EXISTING_UNIT_CHANGE_REQUIRES_REVIEW:'+unit.name)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--start',action='store_true');ap.add_argument('--update-owned-sidecars',action='store_true');a=ap.parse_args()
    if os.geteuid()==0:raise SystemExit('USER_OWNED_SIDECAR_ONLY')
    os.umask(0o077);home=Path.home();source=Path(__file__).resolve().parent
    sys.path.insert(0,str(source))
    from runner import config_load
    config=config_load(a.config)
    if config['output_dir']!=str(home/'.local/state/heptatrader/neuron-learning/student/EUR.USD'):
        raise SystemExit('FIXED_EURUSD_LEARNING_OUTPUT_REQUIRED')
    binaries={'hepta-trader-neuron-native':Path(config['native_binary']),
              'libneuron_head_hip.so':Path(config['gpu_library']),
              'hepta_pnl_accounting.py':Path(config['accounting_module'])}
    inventory={n:sha(source/n) for n in SOURCES}
    inventory.update({n:sha(p) for n,p in binaries.items()})
    identity=installation_identity(inventory,config)
    release=home/'.local/share/heptatrader/neuron-learning/releases'/identity
    release.mkdir(parents=True,mode=0o700,exist_ok=True)
    for name in inventory:
        original=source/name if name in SOURCES else binaries[name]
        target=release/name
        if target.exists():
            if sha(target)!=inventory[name]:raise RuntimeError('EXISTING_RELEASE_DRIFT')
        else:
            with target.open('xb') as f:f.write(original.read_bytes());f.flush();os.fsync(f.fileno())
            target.chmod(0o500 if name=='hepta-trader-neuron-native' else 0o400)
    config.update(native_binary=str(release/'hepta-trader-neuron-native'),gpu_library=str(release/'libneuron_head_hip.so'),
                  accounting_module=str(release/'hepta_pnl_accounting.py'))
    config['require_sandbox']=True
    config['python_sources_sha256']={n:inventory[n] for n in SOURCES}
    configuration=home/'.config/heptatrader-neuron-learning';configuration.mkdir(mode=0o700,parents=True,exist_ok=True)
    target_config=configuration/(identity+'.json')
    raw=json.dumps(config,indent=2,sort_keys=True)+'\n'
    if target_config.exists() and target_config.read_text()!=raw:raise RuntimeError('IMMUTABLE_CONFIG_DRIFT')
    if not target_config.exists():target_config.write_text(raw);target_config.chmod(0o400)
    output=Path(config['output_dir']);output.mkdir(parents=True,mode=0o700,exist_ok=True)
    definitions={}
    for name,script,description in (
        ('heptatrader-neuron-learning.service','runner.py','HeptaTrader small Neuron GPU candidate learner (NO trade authority)'),
        ('heptatrader-neuron-shadow-eval.service','evaluate.py','HeptaTrader independent forward shadow evaluator (NO training or trading)')):
        text=f'''[Unit]
Description={description}

[Service]
Type=simple
ExecStart=/usr/bin/python3 -B {release}/{script} --config {target_config}
WorkingDirectory={release}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=OPENBLAS_NUM_THREADS=1
Environment=OMP_NUM_THREADS=1
Environment=MALLOC_ARENA_MAX=2
UMask=0077
Nice=15
CPUQuota=50%
CPUWeight=10
MemoryHigh=768M
MemoryMax=1G
TasksMax=64
IOSchedulingClass=idle
NoNewPrivileges=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX
Restart=on-failure
RestartSec=30
TimeoutStopSec=15

[Install]
WantedBy=default.target
'''
        definitions[home/'.config/systemd/user'/name]=text
    check_unit_updates(definitions,a.update_owned_sidecars)
    units={}
    for unit,text in definitions.items():
        name=unit.name
        if unit.exists() and unit.read_text()!=text:
            backup=configuration/(name+'.'+sha(unit)+'.backup')
            if not backup.exists():backup.write_text(unit.read_text());backup.chmod(0o600)
            subprocess.run(['systemctl','--user','stop',name],check=True)
            unit.write_text(text);unit.chmod(0o600)
        if not unit.exists():unit.write_text(text);unit.chmod(0o600)
        units[name]={'path':str(unit),'sha256':sha(unit)}
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    if a.start:
        subprocess.run(['systemctl','--user','enable','--now',*units],check=True)
    receipt={'schema':'heptatrader.neuron-sidecar-install.v1','release':str(release),'release_id':identity,
             'config':str(target_config),'config_sha256':sha(target_config),'files_sha256':inventory,
             'units':units,'start_requested':a.start,'paper_service_modified':False,
             'trade_authority':False,'automatic_cutover':False,'gpu_arch':'gfx1150',
             'native_source_sha':config['native_source_sha']}
    report=configuration/(identity+'.install-receipt.json')
    report.write_text(json.dumps(receipt,indent=2)+'\n');report.chmod(0o600)
    print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
