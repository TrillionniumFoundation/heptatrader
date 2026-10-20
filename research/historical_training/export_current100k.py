"""One-shot export from existing local evidence to a separate research dataset.
No broker connection, trading configuration changes, model fit, or promotion.
"""
from __future__ import annotations
import argparse,fcntl,hashlib,json,os,time
from pathlib import Path
from .common import EvidenceError,read,sha,digest,atomic,write_once,canonical
from .current100k import BEHAVIOR,validate_config,to_row

def publish_incremental(root, payload, budget_bytes):
    """One immutable receipt per settled episode; one bounded replaceable view.

    Prior full dataset snapshots are preserved, but never counted against this
    separate incremental budget. Readers must pin the current view by SHA256.
    An episode whose result changes or disappears fails rather than rewriting
    a previously learned reward.
    """
    if type(budget_bytes) is not int or not 16*1024**2 <= budget_bytes <= 512*1024**2:
        raise EvidenceError('INCREMENTAL_STORAGE_BUDGET_INVALID')
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    store = root / 'incremental-v1'
    store.mkdir(mode=0o700, exist_ok=True)
    lock = store / 'publisher.lock'
    with lock.open('a+b') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        entries = store / 'episodes'
        entries.mkdir(mode=0o700, exist_ok=True)
        current = {}
        for row in payload['rows']:
            episode = row['episode_id']
            if type(episode) is not str or not episode or len(episode) > 1024:
                raise EvidenceError('INCREMENTAL_EPISODE_ID')
            key = hashlib.sha256(episode.encode('utf-8')).hexdigest()
            if key in current:
                raise EvidenceError('INCREMENTAL_DUPLICATE_EPISODE')
            current[key] = {'schema': 'heptatrader.current100k-episode.v1',
                            'episode_id': episode, 'row_id': row['row_id'], 'row': row}
        found = set()
        for path in entries.iterdir():
            if path.is_symlink() or not path.is_file() or not path.name.endswith('.json'):
                raise EvidenceError('INCREMENTAL_ENTRY_INVALID')
            found.add(path.stem)
            if path.stem not in current or read(path) != current[path.stem]:
                raise EvidenceError('INCREMENTAL_REWARD_REWRITTEN_OR_WITHDRAWN')
        new_entries = [(entries / (key+'.json'), value)
                       for key, value in current.items() if key not in found]
        latest = store / 'dataset-current.json'
        if latest.is_symlink() or (latest.exists() and not latest.is_file()):
            raise EvidenceError('INCREMENTAL_VIEW_INVALID')
        used = sum(path.stat().st_size for path in store.rglob('*') if path.is_file())
        view_bytes = len(canonical(payload)) + 1
        old_view_bytes = latest.stat().st_size if latest.exists() else 0
        projected = (used - old_view_bytes + view_bytes
                     + sum(len(canonical(value))+1 for _, value in new_entries))
        if projected > budget_bytes:
            raise EvidenceError('INCREMENTAL_STORAGE_BUDGET_NO_PRUNING')
        vfs = os.statvfs(root)
        if vfs.f_bavail * vfs.f_frsize < max(1024**3, projected-used+1024**3):
            raise EvidenceError('FILESYSTEM_RESERVE')
        for path, value in new_entries:
            write_once(path, value)
        atomic(latest, payload)
        return latest, {'schema':'heptatrader.current100k-incremental-status.v1',
                        'budget_bytes': budget_bytes, 'projected_used_bytes': projected,
                        'durable_episodes': len(current), 'new_episodes': len(new_entries),
                        'legacy_snapshots_preserved': True}

def export(config):
    """Public one-shot path always validates its complete pinned configuration."""
    return export_validated(config, validate_config(config))


def export_validated(config, manifest):
    """Internal launcher path after validation and sandbox entry."""
    from .consumer_checkpoint import (
        input_identity, load_checkpoint, save_checkpoint, forget_checkpoint)
    from laya_cell.resident_rewards import current
    from laya_cell.financial_forward_source import ingress
    from laya_cell.financial_value_data import ExactRequestIndex
    started=time.perf_counter();now_ms=int(time.time()*1000)
    root=Path(config['output_root'])
    before=input_identity(config,manifest)
    checkpoint=load_checkpoint(root,before,now_ms)
    if checkpoint is not None:
        status={**checkpoint['status'],'at_ms':now_ms,'input_checked_at_ms':now_ms,
                'evaluation_skipped':True,'input_checkpoint_reusable':True,
                'refresh_duration_ms':(time.perf_counter()-started)*1000.,
                'incremental':{**checkpoint['status']['incremental'],'new_episodes':0}}
        atomic(root/'status.json',status)
        return status
    forget_checkpoint(root)
    previous={}
    try:
        previous=read(root/'status.json')
    except (OSError,ValueError):
        pass
    registration=config['registration']
    _,requests,bindings,issues=ingress(registration,config['period_sha256'])
    if issues:
        raise EvidenceError('ATTRIBUTION_ISSUES:'+str(issues))
    bound={item['command']['command_id']:item for item in bindings}
    source={**config['reward_config'],
        'decision_attribution_registrations':{'EUR.USD':[registration]},
        'period_path':config['period_path'],'period_sha256':config['period_sha256'],
        'reward_instruments':['EUR.USD'],'export_directories':{},'export_directory':''}
    future=[]
    report=current(source,now_ms,source_observer=lambda stamp:future.append(stamp>now_ms))
    component=report['components'].get('EUR.USD',{})
    if component.get('ledger_complete') is not True:
        raise EvidenceError('ACCOUNTING_EVIDENCE_UNAVAILABLE')
    source_index=ExactRequestIndex(report['rewards'])
    rows=[]
    for reward in report['rewards']:
        binding=bound[reward['open_command_id']]
        item=requests[binding['request_id']]
        rows.append(to_row(reward,item,binding,registration['sha256'],
                           config['period_sha256'],source_index=source_index))
    rows.sort(key=lambda row:(row['input_started_at_ms'],row['row_id']))
    if len(rows)>4096 or len({r['episode_id'] for r in rows})!=len(rows):
        raise EvidenceError('DUPLICATE_OR_EXCESSIVE_ROWS')
    payload={'schema':'heptatrader.current100k-value-dataset.v1',
        'period_sha256':config['period_sha256'],'producer_manifest_sha256':registration['sha256'],
        'behavior_profile':BEHAVIOR,'rows':rows,'synthetic_rewards':0,'old25k_rewards':0,
        'automatic_training':False,'automatic_selection':False,'trade_authority':False}
    payload['fingerprint']=digest(payload)
    # A changing source cannot publish a mixture of two evidence snapshots.
    after=input_identity(config,manifest)
    if before!=after:
        raise EvidenceError('CONSUMER_INPUT_CHANGED_DURING_REFRESH')
    dataset,storage=publish_incremental(
        root,payload,config.get('incremental_budget_bytes',128*1024*1024))
    last_growth=previous.get('last_inventory_growth_at_ms')
    if storage['new_episodes']:
        last_growth=now_ms
    status={'schema':'heptatrader.current100k-consumer-status.v1','at_ms':int(time.time()*1000),
        'input_checked_at_ms':now_ms,'last_full_evaluation_at_ms':now_ms,
        'last_inventory_growth_at_ms':last_growth,
        'evaluation_skipped':False,'input_checkpoint_reusable':not any(future),
        'refresh_duration_ms':(time.perf_counter()-started)*1000.,
        'evidence_prefix_reads':source_index.prefix_reads,
        'state':'READY_VALUE_LABELS_NOT_EFFICACY' if rows else 'WAITING_CURRENT100K_SETTLED_LABELS',
        'dataset_path':str(dataset),'dataset_sha256':sha(dataset),'dataset_fingerprint':payload['fingerprint'],
        'row_count':len(rows),'current100k_net_targets':[row['net_usd'] for row in rows],
        'incremental':storage,
        'observed_original_requests':len(requests),'bound_opens':len(bindings),
        'accounting_component':component,'source_ledger_prefix':report.get('source_prefix'),
        'old25k_rewards_imported':0,'automatic_training':False,'model_promoted':False,'broker_orders_sent':0}
    atomic(root/'status.json',status)
    if not any(future):
        save_checkpoint(root,after,status,now_ms)
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
    print(json.dumps({k:status[k] for k in ('at_ms','state','row_count','dataset_fingerprint','evaluation_skipped','refresh_duration_ms')}))

if __name__=='__main__':
    main()
