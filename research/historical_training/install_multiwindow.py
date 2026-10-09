"""Install one finite read-only campaign, never replacing existing units."""
from __future__ import annotations
import argparse,json,math,os,shutil,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once
from .multiwindow import anchors,validate_plan,CANDIDATE,CANDIDATE_SHA


def install(c):
    os.umask(0o077);root=Path(c['root']);repo=Path(c['source_root'])
    if root.exists():raise EvidenceError('CAMPAIGN_EXISTS_NO_DUPLICATE_INSTALL')
    unitdir=Path.home()/'.config/systemd/user';unit='heptatrader-frozen-price-campaign'
    for suffix in ('.service','.timer'):
        if (unitdir/(unit+suffix)).exists():raise EvidenceError('EXISTING_UNIT_NO_OVERWRITE')
    period=read(c['period_path'])
    if period.get('execution_domain')!='PAPER' or not str(period.get('account','')).startswith('DU'):raise EvidenceError('EXACT_PAPER_PERIOD_REQUIRED')
    sourcecandidate=Path(c['candidate'])
    if sha(sourcecandidate)!=CANDIDATE_SHA:raise EvidenceError('FROZEN_MODEL_CHANGED')
    candidate=read(sourcecandidate)
    if candidate['candidate_id']!=CANDIDATE or candidate['action_policy']!='ABSTAIN':raise EvidenceError('NO_TRADE_FROZEN_CANDIDATE')
    frozen_module=repo/'research/historical_training'
    for name,wanted in candidate['source_sha256'].items():
        if sha(frozen_module/name)!=wanted:raise EvidenceError('FROZEN_TRAINING_CODE_CHANGED:'+name)
    root.mkdir(mode=0o700,parents=True);runtime=root/'runtime';runtime.mkdir()
    for p in frozen_module.glob('*.py'):
        dest=runtime/'historical_training'/p.name;dest.parent.mkdir(mode=0o700,exist_ok=True)
        shutil.copyfile(p,dest);dest.chmod(0o400)
    shutil.copyfile(sourcecandidate,root/'candidate.json');(root/'candidate.json').chmod(0o400)
    history=read(c['history_config']);history['expected_paper_account']=period['account']
    write_once(root/'history.json',history);sdk=Path(history['sdk_root'])
    first=math.ceil((time.time()+600)/300)*300
    stamps=anchors(first)
    plan={'schema':'heptatrader.frozen-multiwindow-plan.v1','registered_at_ms':int(time.time()*1000),
        'candidate_id':CANDIDATE,'candidate_sha256':CANDIDATE_SHA,'anchors':stamps,
        'full_span':'[anchor-3600,anchor+1500]','minimum_spacing_seconds':7200,
        'period_sha256':sha(c['period_path']),'history_config_sha256':sha(root/'history.json'),
        'sdk_sha256':{str(p.relative_to(sdk)):sha(p) for p in (sdk/'ibapi').rglob('*.py')},
        'price_rule':{'min_observed':20,'max_censored':4,'min_utc_days':5,'min_classes':2,'min_mean_brier_gain':.001,
            'comparisons':['train_prior','uniform'],'alpha_per_comparison':.025,'analyse_only_after_all_24_finalized':True},
        'source_rule':{'min_usable':20,'max_unusable':4,'max_status_age_ms':120000},
        'trade_authority':False,'action_policy':'ABSTAIN','automatic_promotion':False,'reuse_previous_smoke':False,
        'statistical_independence_not_guaranteed_by_nonoverlap':True,'missed_slots_replaced':False,
        'maximum_history_queries':96,'new_account_or_quote_subscriptions':0}
    plan['campaign_id']=digest(plan);validate_plan(plan);write_once(root/'plan.json',plan)
    config={'root':str(root),'plan_sha256':sha(root/'plan.json'),'runtime':str(runtime),
        'runtime_sha256':{str(p.relative_to(runtime)):sha(p) for p in runtime.rglob('*.py')},
        'period_path':c['period_path'],'health_path':c['health_path']}
    write_once(root/'config.json',config)
    python=Path(c['python'])
    if not python.is_file():raise EvidenceError('PINNED_PYTHON_NOT_FOUND')
    service=f'''[Unit]
Description=HeptaTrader finite frozen price campaign; historical reads only; no orders
ConditionPathExists=!{root}/complete.json

[Service]
Type=oneshot
WorkingDirectory={runtime}
ExecStart={python} -B -m historical_training.multiwindow --config {root}/config.json
Environment=PYTHONPATH={runtime}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=OPENBLAS_NUM_THREADS=2
Environment=OMP_NUM_THREADS=2
UMask=0077
Nice=15
CPUQuota=50%
CPUWeight=10
MemoryMax=1G
TasksMax=24
NoNewPrivileges=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX AF_INET
TimeoutStartSec=2100
TimeoutStopSec=10
KillMode=control-group
Restart=no
'''
    times=stamps+[stamps[-1]+1680]
    lines=['[Unit]','Description=HeptaTrader 24 predeclared nonoverlapping windows, then final sweep','','[Timer]']
    for i,anchor in enumerate(times):
        when=anchor-60 if i<len(stamps) else anchor
        lines.append('OnCalendar='+datetime.fromtimestamp(when,timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))
    lines+=['OnBootSec=120','AccuracySec=1s','RandomizedDelaySec=0','Persistent=true','Unit='+unit+'.service','','[Install]','WantedBy=timers.target','']
    unitdir.mkdir(parents=True,exist_ok=True)
    for suffix,text in (('.service',service),('.timer','\n'.join(lines))):
        with (unitdir/(unit+suffix)).open('x') as f:f.write(text)
    verification=subprocess.run(['systemd-analyze','--user','verify',str(unitdir/(unit+'.service')),str(unitdir/(unit+'.timer'))],capture_output=True,text=True)
    if verification.returncode:raise EvidenceError('UNIT_VERIFY:'+verification.stderr[-2000:])
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','enable','--now',unit+'.timer'],check=True)
    subprocess.run(['systemctl','--user','start',unit+'.service'],check=True,timeout=30)
    result={'schema':'heptatrader.frozen-campaign-install.v1','root':str(root),'campaign_id':plan['campaign_id'],
        'plan_sha256':sha(root/'plan.json'),'candidate_sha256':sha(root/'candidate.json'),
        'unit_files_sha256':{suffix:sha(unitdir/(unit+suffix)) for suffix in ('.service','.timer')},
        'slots_utc':[datetime.fromtimestamp(a,timezone.utc).isoformat() for a in stamps],
        'first_outcome_ready_utc':datetime.fromtimestamp(stamps[0]+1500,timezone.utc).isoformat(),
        'last_outcome_ready_utc':datetime.fromtimestamp(stamps[-1]+1500,timezone.utc).isoformat(),
        'activated':True,'existing_units_modified':False,'production_services_restarted':False,
        'rollback':['systemctl --user disable --now '+unit+'.timer','systemctl --user stop '+unit+'.service'],
        'stop_warning':'Stopping an in-flight read censors that slot; it is not replayed.',
        'finite_effect_budget':'24 slots, at most 96 history queries, zero trading authority'}
    write_once(Path(c['receipt']),result);print(json.dumps(result,indent=2))


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();install(read(a.config))
if __name__=='__main__':main()
