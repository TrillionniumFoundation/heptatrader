"""Read-only projection of existing signed-worker results, not a qualification.

Never turns service activity, a commit count, a held attempt, or revised history
into a completed monetary score. No raw OMS read and no broker or worker call.
"""
import argparse, collections, hashlib, json, os, time
from pathlib import Path
from laya_cell.integrity import digest


def read(path):
    p=Path(path)
    if p.is_symlink() or p.stat().st_size>2*1024**2:raise ValueError('UNSAFE_STATUS_FILE')
    raw=p.read_bytes();return json.loads(raw),hashlib.sha256(raw).hexdigest()


def outcome_record(pointer, root):
    value,_=read(pointer);target=Path(value['path'])
    if target.parent.resolve()!=pointer.parent.resolve() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('OUTCOME_POINTER_ESCAPE')
    record,sha=read(target)
    if record.get('record_id')!=value.get('record_id') or digest({k:v for k,v in record.items() if k!='record_id'})!=record.get('record_id'):
        raise ValueError('OUTCOME_DIGEST_MISMATCH')
    return record,sha


def classify(records):
    states=collections.Counter();reasons={k:collections.Counter() for k in ('OBSERVED','PENDING','CENSORED')}
    ids=set()
    for row in records:
        cid=row['command_id'];state=row['status']
        if cid in ids or state not in reasons:raise ValueError('DUPLICATE_COMMAND_OR_UNKNOWN_STATE')
        ids.add(cid);states[state]+=1;reasons[state][row.get('reason','NONE')]+=1
    return {'outcome_command_count':len(ids),'observed':states['OBSERVED'],'pending':states['PENDING'],
            'censored':states['CENSORED'],'pending_by_reason':dict(reasons['PENDING']),
            'censored_by_reason':dict(reasons['CENSORED'])}


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    os.umask(0o077);root=Path(a.root);plans=list(root.glob('plans/*/plan.json'))
    if len(plans)!=1:raise ValueError('EXACTLY_ONE_PLAN')
    plan,_=read(plans[0]);base=plans[0].parent
    predictor,predsha=read(root/'predictor-status.json');assessment,ash=read(base/'assessment.json')
    if assessment.get('plan_id')!=plan.get('plan_id'):raise ValueError('ASSESSMENT_PLAN_MISMATCH')
    records=[];hashes={}
    for pointer in sorted(base.glob('outcomes/*/current.json')):
        record,h=outcome_record(pointer,root);records.append(record);hashes[record['command_id']]=h
    counts=classify(records);failures=collections.Counter()
    for path in base.glob('predictions/*/failure.json'):
        value,_=read(path);failures[value.get('reason','UNKNOWN')]+=1
    result={'schema':'heptatrader.evaluation-progress-review.v1','at_ms':int(time.time()*1000),
            'plan_id':plan['plan_id'],'predictor_worker_state':predictor.get('state'),
            'sealed_prediction_records':len(list(base.glob('predictions/*/commit.json'))),
            'terminal_prediction_failures_by_reason':dict(failures),
            'held_attempt_count_not_independent_samples':predictor.get('retained_failed_or_held_attempts'),
            'latest_hold_issue':predictor.get('last_retained_request_issue'),
            'assessment_state':assessment['state'],'assessment_blockers':assessment.get('reasons',[]),
            'assessment_observed_trades':assessment.get('observed_trades'),
            'assessment_total_original_trades':assessment.get('total_original_trades'),
            'assessment_unresolved_or_censored':assessment.get('unresolved_or_censored'),
            'independent_time_half_counts':assessment.get('time_half_counts'),
            'current_outcome_projection':counts,
            'assessment_count_matches_projection':counts['observed']==assessment.get('observed_trades') and counts['outcome_command_count']==assessment.get('total_original_trades'),
            'source_hashes':{'predictor':predsha,'assessment':ash,'outcomes_digest':digest(hashes)},
            'multi_file_read_not_atomic_broker_snapshot':True,'underlying_signatures_not_reverified':True,
            'raw_oms_read':False,'new_model_inference':False,'broker_queries':0,'trade_authority':False,'promotion_allowed':False}
    with Path(a.output).open('x') as f:json.dump(result,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
