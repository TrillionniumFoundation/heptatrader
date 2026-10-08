"""Finite frozen-candidate campaign. Full input/label spans never overlap.
Every predeclared slot ends observed or censored; interrupted slots are not replayed.
No parameter updates, trading orders, source substitution, or automatic promotion.
"""
from __future__ import annotations
import argparse,fcntl,json,math,os,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once,atomic

CANDIDATE='f7f18c2476da44496fe9d5a8e987cbab5e3459701ed7ef9ff421c61290245c1e'
CANDIDATE_SHA='538dd502589cf4ddad56c0dc2c13b7f75f5da402f2d53f08983b09fc6a377466'


def anchors(first):
    if type(first) is not int or first%300:raise EvidenceError('ANCHOR_GRID')
    day=datetime.fromtimestamp(first,timezone.utc)
    if day.weekday()>4 or not 6<=day.hour<=13:raise EvidenceError('START_SESSION_SCOPE')
    dates=[]
    while len(dates)<6:
        if day.weekday()<5:dates.append(day)
        day+=timedelta(days=1)
    return [int((d+timedelta(hours=h)).timestamp()) for d in dates for h in (0,2,4,6)]


def validate_plan(plan):
    if plan.get('campaign_id')!=digest({k:v for k,v in plan.items() if k!='campaign_id'}):raise EvidenceError('CAMPAIGN_HASH')
    if plan.get('candidate_id')!=CANDIDATE or plan.get('candidate_sha256')!=CANDIDATE_SHA:
        raise EvidenceError('FROZEN_CANDIDATE_REQUIRED')
    if (plan.get('trade_authority') is not False or plan.get('action_policy')!='ABSTAIN'
        or plan.get('automatic_promotion') is not False or plan.get('reuse_previous_smoke') is not False):
        raise EvidenceError('SHADOW_ONLY_CAMPAIGN')
    stamps=plan['anchors'];created=plan['registered_at_ms']
    if stamps!=anchors(stamps[0]) or len(stamps)!=24:raise EvidenceError('FIXED_24_SLOTS_SIX_WEEKDAYS')
    if created>=stamps[0]*1000:raise EvidenceError('RETROSPECTIVE_CAMPAIGN')
    if any(a+1500>=b-3600 for a,b in zip(stamps,stamps[1:])):raise EvidenceError('OVERLAPPING_COMPLETE_SPANS')
    return stamps


def slot_action(anchor,now,claimed,finished):
    if finished:return 'FINAL'
    if claimed:return 'CENSOR_INTERRUPTED_NO_REPLAY'
    if now>anchor-5:return 'CENSOR_MISSED_WAKE_NO_REPLAY'
    if now>=anchor-90:return 'RUN_ONCE'
    return 'FUTURE'


def final_assessment(items):
    """Only after ALL slots finalize. Small day blocks do not prove IID data."""
    import numpy as np
    observed=[x for x in items if x['state']=='OBSERVED']
    if len(items)!=24 or any(x['state'] not in ('OBSERVED','CENSORED') for x in items):
        raise EvidenceError('NO_INTERIM_EFFICACY_TEST')
    days={};labels=set();scores={}
    for x in observed:
        day=datetime.fromtimestamp(x['anchor'],timezone.utc).date().isoformat();days.setdefault(day,[]).append(x);labels.add(x['label'])
    eligible=len(observed)>=20 and len(items)-len(observed)<=4 and len(days)>=5 and len(labels)>=2
    for baseline in ('train_prior_brier','uniform_brier'):
        sums=np.array([sum(x[baseline]-x['brier'] for x in xs) for xs in days.values()]);counts=np.array([len(xs) for xs in days.values()])
        if not len(sums):scores[baseline]={'mean_gain':None,'interval_lower':None,'passes':False};continue
        rng=np.random.default_rng(2026100817);indices=rng.integers(0,len(sums),(10000,len(sums)))
        means=sums[indices].sum(1)/counts[indices].sum(1)
        lower=float(np.quantile(means,.025));mean=float(sums.sum()/counts.sum())
        scores[baseline]={'mean_gain':mean,'one_sided_97_5_day_bootstrap_lower':lower,
            'passes':bool(eligible and mean>.001 and lower>0)}
    return {'observed':len(observed),'censored':24-len(observed),'utc_days':len(days),'classes':sorted(labels),
        'coverage_eligible':eligible,'comparisons':scores,'price_review_eligible':all(x['passes'] for x in scores.values()),
        'method':'PAIRED_DAY_BLOCK_BOOTSTRAP_TWO_COMPARISONS_BONFERRONI_POINTWISE_0.025',
        'finite_blocks_limit_inference':True,'statistical_independence_proven':False,
        'automatic_promotion':False,'economic_qualification':False}


def verify_child_slot(plan,anchor,child):
    if (anchor not in plan['anchors'] or child.get('anchor_seconds')!=anchor
        or child.get('entry_seconds')!=anchor+300 or child.get('exit_seconds')!=anchor+1200
        or child.get('outcome_ready_seconds')!=anchor+1500):
        raise EvidenceError('CHILD_WINDOW_NOT_BOUND_TO_CAMPAIGN_SLOT')
    if (child.get('candidate_id')!=plan['candidate_id']
        or child.get('candidate_sha256')!=plan['candidate_sha256']
        or not plan['registered_at_ms']<=child.get('registered_at_ms',0)<anchor*1000):
        raise EvidenceError('CHILD_CANDIDATE_OR_REGISTRATION_CONFLICT')


def summarize(root,plan):
    from .forward_validate import audit
    items=[]
    for anchor in plan['anchors']:
        base=root/'slots'/str(anchor);final=base/'final.json'
        if not final.exists():
            items.append({'anchor':anchor,'state':'RUNNING_OR_INTERRUPTED' if (base/'claim.json').exists() else 'SCHEDULED'});continue
        record=read(final)
        if record.get('campaign_id')!=plan['campaign_id'] or record.get('anchor')!=anchor:raise EvidenceError('SLOT_IDENTITY')
        item={'anchor':anchor,'state':record['state'],'reason':record.get('reason'),'receipt_sha256':sha(final)}
        if item['state']=='OBSERVED':
            verify_child_slot(plan,anchor,read(base/'proof/plan.json'))
            proof=audit(base/'proof',root/'candidate.json')
            raw=read(base/'proof/result.json')
            if sha(base/'proof/result.json')!=record['result_sha256']:raise EvidenceError('SLOT_RESULT_CHANGED')
            item.update(brier=proof['brier'],label=proof['label'],train_prior_brier=raw['train_prior_brier'],uniform_brier=raw['uniform_brier'])
        items.append(item)
    closed=sum(x['state'] in ('OBSERVED','CENSORED') for x in items)
    status={'schema':'heptatrader.frozen-multiwindow-status.v1','at_ms':int(time.time()*1000),'campaign_id':plan['campaign_id'],
        'planned':24,'finalized':closed,'observed':sum(x['state']=='OBSERVED' for x in items),
        'censored':sum(x['state']=='CENSORED' for x in items),'slots':items,
        'phase':'COMPLETE_PENDING_REVIEW' if closed==24 else 'ACCUMULATING_NO_EFFICACY_VERDICT',
        'old_smoke_excluded':True,'candidate_unchanged':sha(root/'candidate.json')==CANDIDATE_SHA,
        'trade_authority':False,'automatic_promotion':False,'assessment':final_assessment(items) if closed==24 else None}
    atomic(root/'status.json',status)
    if closed==24:write_once(root/'complete.json',{'campaign_id':plan['campaign_id'],'assessment':status['assessment']})
    return status


def validate_runtime(config):
    root=Path(config['root']);plan=read(root/'plan.json');validate_plan(plan)
    if sha(root/'plan.json')!=config['plan_sha256'] or sha(root/'candidate.json')!=CANDIDATE_SHA:raise EvidenceError('PIN_CHANGED')
    if sha(config['period_path'])!=plan['period_sha256']:raise EvidenceError('ACCOUNTING_PERIOD_CHANGED')
    runtime=Path(config['runtime'])
    for name,wanted in config['runtime_sha256'].items():
        p=Path(name)
        if p.is_absolute() or '..' in p.parts or sha(runtime/p)!=wanted:raise EvidenceError('INSTALLED_RUNTIME_CHANGED')
    hc=read(root/'history.json');sdk=Path(hc['sdk_root'])
    if sha(root/'history.json')!=plan['history_config_sha256']:raise EvidenceError('HISTORY_CONFIG_CHANGED')
    if {str(p.relative_to(sdk)):sha(p) for p in (sdk/'ibapi').rglob('*.py')}!=plan['sdk_sha256']:
        raise EvidenceError('PINNED_SDK_INVENTORY_CHANGED')
    return root,plan


def tick(config):
    os.umask(0o077);root,plan=validate_runtime(config)
    fd=os.open(root/'campaign.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd);return {'state':'ALREADY_RUNNING_NO_REPLAY'}
    try:
        used=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
        if used>256*1024**2:raise EvidenceError('EVIDENCE_QUOTA_NO_PRUNING')
        from .bar_forward_smoke import run
        from .forward_validate import audit
        for anchor in plan['anchors']:
            base=root/'slots'/str(anchor);final=base/'final.json';claim=base/'claim.json'
            action=slot_action(anchor,time.time(),claim.exists(),final.exists())
            if action in ('FINAL','FUTURE'):continue
            record={'schema':'heptatrader.frozen-window-final.v1','anchor':anchor,'campaign_id':plan['campaign_id'],
                'state':'CENSORED','reason':action,'broker_order_authority':False}
            if action=='RUN_ONCE':
                write_once(claim,{'campaign_id':plan['campaign_id'],'anchor':anchor,'claimed_at_ms':int(time.time()*1000),
                    'candidate_sha256':CANDIDATE_SHA,'resend_or_replay_permitted':False})
                try:
                    result=run(root/'candidate.json',root/'history.json',Path(config['health_path']),base/'proof',anchor_seconds=anchor)
                    if result['outcome_count']==1:
                        proof=audit(base/'proof',root/'candidate.json');write_once(base/'audit.json',proof)
                        record.update(state='OBSERVED',reason=None,result_sha256=sha(base/'proof/result.json'),audit_sha256=sha(base/'audit.json'))
                    else:record['reason']=result.get('reason','CENSORED_COLLECTOR')
                except Exception as exc:record['reason']=type(exc).__name__+': '+str(exc)
            record['finalized_at_ms']=int(time.time()*1000);write_once(final,record)
        return summarize(root,plan)
    finally:os.close(fd)


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--status-only',action='store_true');a=p.parse_args()
    c=read(a.config)
    if a.status_only:
        root,plan=validate_runtime(c);r=summarize(root,plan)
    else:r=tick(c)
    print(json.dumps({k:v for k,v in r.items() if k not in ('slots','assessment')}),flush=True)
if __name__=='__main__':main()
