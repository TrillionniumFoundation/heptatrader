"""One-shot export from existing local evidence to a separate research dataset.
No broker connection, trading configuration changes, model fit, or promotion.
"""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
from .common import EvidenceError,read,sha,digest,atomic,write_once,canonical
from .current100k import BEHAVIOR,validate_config,to_row

def export(config):
    validate_config(config)
    from laya_cell.resident_rewards import current
    from laya_cell.financial_forward_source import ingress
    registration=config['registration']
    _,requests,bindings,issues=ingress(registration,config['period_sha256'])
    if issues:
        raise EvidenceError('ATTRIBUTION_ISSUES:'+str(issues))
    bound={item['command']['command_id']:item for item in bindings}
    source={**config['reward_config'],
        'decision_attribution_registrations':{'EUR.USD':[registration]},
        'period_path':config['period_path'],'period_sha256':config['period_sha256'],
        'reward_instruments':['EUR.USD'],'export_directories':{},'export_directory':''}
    report=current(source,int(time.time()*1000))
    component=report['components'].get('EUR.USD',{})
    if component.get('ledger_complete') is not True:
        raise EvidenceError('ACCOUNTING_EVIDENCE_UNAVAILABLE')
    rows=[]
    for reward in report['rewards']:
        binding=bound[reward['open_command_id']]
        item=requests[binding['request_id']]
        rows.append(to_row(reward,item,binding,registration['sha256'],config['period_sha256']))
    rows.sort(key=lambda row:(row['input_started_at_ms'],row['row_id']))
    if len(rows)>4096 or len({r['episode_id'] for r in rows})!=len(rows):
        raise EvidenceError('DUPLICATE_OR_EXCESSIVE_ROWS')
    payload={'schema':'heptatrader.current100k-value-dataset.v1',
        'period_sha256':config['period_sha256'],'producer_manifest_sha256':registration['sha256'],
        'behavior_profile':BEHAVIOR,'rows':rows,'synthetic_rewards':0,'old25k_rewards':0,
        'automatic_training':False,'automatic_selection':False,'trade_authority':False}
    payload['fingerprint']=digest(payload)
    root=Path(config['output_root'])
    dataset=root/'datasets'/(payload['fingerprint']+'.json')
    used=sum(p.stat().st_size for p in root.rglob('*') if p.is_file()) if root.exists() else 0
    extra=0 if dataset.exists() else len(canonical(payload))+1
    if used+extra>256*1024*1024:
        raise EvidenceError('CONSUMER_STORAGE_BUDGET_NO_PRUNING')
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    space=__import__('os').statvfs(root)
    if space.f_bavail*space.f_frsize-extra<1024*1024*1024:
        raise EvidenceError('FILESYSTEM_RESERVE')
    write_once(dataset,payload)
    status={'schema':'heptatrader.current100k-consumer-status.v1','at_ms':int(time.time()*1000),
        'state':'READY_VALUE_LABELS_NOT_EFFICACY' if rows else 'WAITING_CURRENT100K_SETTLED_LABELS',
        'dataset_path':str(dataset),'dataset_sha256':sha(dataset),'dataset_fingerprint':payload['fingerprint'],
        'row_count':len(rows),'current100k_net_targets':[row['net_usd'] for row in rows],
        'observed_original_requests':len(requests),'bound_opens':len(bindings),
        'accounting_component':component,'source_ledger_prefix':report.get('source_prefix'),
        'old25k_rewards_imported':0,'automatic_training':False,'model_promoted':False,'broker_orders_sent':0}
    atomic(root/'status.json',status)
    return status

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args();config=read(args.config)
    try:
        status=export(config)
    except Exception as error:
        atomic(Path(config['output_root'])/'status.json',{'state':'UNAVAILABLE_FAIL_CLOSED',
            'at_ms':int(time.time()*1000),'reason':str(error),'broker_orders_sent':0,'automatic_training':False})
        raise
    print(json.dumps({k:status[k] for k in ('at_ms','state','row_count','dataset_fingerprint','current100k_net_targets')}))

if __name__=='__main__':
    main()
