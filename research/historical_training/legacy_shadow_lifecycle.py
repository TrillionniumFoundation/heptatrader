"""Pre-import lifecycle check for old fixed-window research predictors.
No Torch, GPU, broker, training or service-control imports. Never changes a
plan or creates a prediction/outcome. Stops expired-model *startup*, not live work.
"""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
from .common import EvidenceError,read,sha,digest

KINDS={
    'paired':('heptatrader.paired-price-prospective-plan.v1','heptatrader.price-complementarity-prospective.v1',24,'effects_authorized'),
    'temporal':('heptatrader.temporal-laya-fusion-plan.v1','heptatrader.temporal-fusion-shadow-rule.v1',12,'trade_permission'),
}


def assess_plan(plan,kind,now_ms):
    if kind not in KINDS:raise EvidenceError('UNKNOWN_FIXED_RESEARCH_KIND')
    schema,rule_schema,slots,permission=KINDS[kind]
    if type(now_ms) is not int or now_ms<0:raise EvidenceError('INVALID_CLOCK')
    if plan.get('schema')!=schema or plan.get('plan_id')!=digest({k:v for k,v in plan.items() if k!='plan_id'}):
        raise EvidenceError('ORIGINAL_PLAN_IDENTITY')
    rule=plan.get('rule',{})
    if (rule.get('schema')!=rule_schema or rule.get(permission) is not False or rule.get('automatic_selection') is not False):
        raise EvidenceError('ORIGINAL_NO_EFFECT_RULE')
    for field,value in [('slots',slots),('slot_spacing_ms',360000),('horizon_ms',300000),('input_grace_ms',60000)]:
        if type(rule.get(field)) is not int or rule[field]!=value:raise EvidenceError('ORIGINAL_TIMING_RULE_CHANGED:'+field)
    for key in ('start_ms','end_ms','registered_at_ms'):
        if type(plan.get(key)) is not int or plan[key]<0:raise EvidenceError('INVALID_PLAN_TIME:'+key)
    registered,start,end=(plan[k] for k in ('registered_at_ms','start_ms','end_ms'))
    if not registered<start<end or now_ms<registered:raise EvidenceError('PLAN_TIME_ORDER_OR_CLOCK_ROLLBACK')
    last_input=start+(slots-1)*rule['slot_spacing_ms']+rule['input_grace_ms']
    last_outcome=last_input+rule['horizon_ms']+5000
    if end<last_outcome:raise EvidenceError('PLAN_END_BEFORE_LAST_OUTCOME')
    expired=now_ms>end
    return {'schema':'heptatrader.fixed-shadow-lifecycle.v1','kind':kind,'plan_id':plan['plan_id'],
        'checked_at_ms':now_ms,'plan_end_ms':end,'last_input_deadline_ms':last_input,
        'last_outcome_deadline_ms':last_outcome,'slots':slots,
        'admit_model_start':not expired,'state':'EXPIRED_FIXED_PLAN_NO_MODEL_LOAD' if expired else 'WINDOW_NOT_EXPIRED',
        'training_authority':False,'broker_orders_sent':0,'candidate_or_calendar_modified':False,
        'active_worker_termination_authorized':False}


def inspect(path,expected_sha,kind,now_ms=None):
    path=Path(path)
    if len(expected_sha)!=64 or any(c not in '0123456789abcdef' for c in expected_sha):raise EvidenceError('EXPECTED_PLAN_HASH_INVALID')
    if sha(path)!=expected_sha:raise EvidenceError('PINNED_PLAN_BYTES_CHANGED')
    value=read(path);report=assess_plan(value,kind,int(time.time()*1000) if now_ms is None else now_ms)
    if sha(path)!=expected_sha:raise EvidenceError('PLAN_CHANGED_DURING_CHECK')
    report['plan_sha256']=expected_sha
    return report


def retirement_evidence(path,expected_sha,kind,now_ms):
    path=Path(path);r=inspect(path,expected_sha,kind,now_ms)
    if r['admit_model_start'] or now_ms<r['plan_end_ms']+60000:raise EvidenceError('NOT_SAFELY_PAST_ORIGINAL_WINDOW')
    # Every original input slot must already have its immutable prediction or failure.
    sealed={}
    for slot in range(r['slots']):
        paths=[path.parent/f'{prefix}-{slot:03}.json' for prefix in ('prediction','failure')]
        existing=[p for p in paths if p.exists()]
        if not existing:raise EvidenceError('ORIGINAL_SLOT_NOT_YET_SEALED:'+str(slot))
        for p in existing:
            row=read(p)
            if type(row.get('slot')) is not int or row['slot']!=slot:raise EvidenceError('SEALED_SLOT_IDENTITY')
            if p.name.startswith('prediction') and row.get('plan_id')!=r['plan_id']:raise EvidenceError('PREDICTION_PLAN_IDENTITY')
            sealed[p.name]=sha(p)
    r.update(all_original_input_slots_sealed=True,immutable_input_receipt_sha256=sealed,
             stop_only_expired_predictor_eligible=True,evaluator_outcomes_not_rewritten=True)
    return r


def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True);p.add_argument('--plan-sha256',required=True)
    p.add_argument('--kind',choices=sorted(KINDS),required=True);a=p.parse_args()
    try:r=inspect(a.plan,a.plan_sha256,a.kind)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({'state':'INVALID_OR_UNAVAILABLE_FIXED_PLAN','admit_model_start':False,'reason':str(exc)}),flush=True)
        raise SystemExit(255)
    print(json.dumps(r),flush=True)
    # ExecCondition 1 skips startup without loading the legacy model or creating a failed task.
    raise SystemExit(0 if r['admit_model_start'] else 1)
if __name__=='__main__':main()
