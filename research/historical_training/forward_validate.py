"""Independent read-only verification of the fixed one-outcome forward proof.
Never changes original forecast/candidate/result, never submits a broker request.
"""
from __future__ import annotations
import argparse,math
from pathlib import Path
from .common import EvidenceError,read,sha,digest,write_once


def custody(plan,candidate,prediction,result):
    anchor=plan['anchor_seconds'];entry=plan['entry_seconds'];exit_time=plan['exit_seconds'];ready=plan['outcome_ready_seconds']
    if (type(anchor) is not int or anchor%300 or entry!=anchor+300 or exit_time!=entry+900 or ready!=exit_time+300):
        raise EvidenceError('FORWARD_HORIZON_CONFLICT')
    if not candidate['created_at_ms']<=plan['registered_at_ms']<anchor*1000:
        raise EvidenceError('CANDIDATE_OR_PLAN_TOO_LATE')
    if not anchor*1000<=prediction['available_at_ms']<entry*1000:
        raise EvidenceError('FORECAST_NOT_BEFORE_TARGET')
    if prediction['target_entry_ms']!=entry*1000 or prediction['target_exit_ms']!=exit_time*1000:
        raise EvidenceError('PREDICTION_TARGET_CHANGED')
    if (prediction.get('action')!='ABSTAIN' or prediction.get('trade_authority') is not False
        or candidate.get('trade_authority') is not False or result.get('automatic_promotion') is not False):
        raise EvidenceError('READ_ONLY_PROOF_SCOPE')
    if result.get('outcome_count')!=1 or result.get('state')!='OBSERVED_ONE_FORWARD_PRICE_OUTCOME_NOT_EFFICACY':
        raise EvidenceError('NO_SINGLE_OBSERVED_OUTCOME')
    if result['ended_at_ms']<ready*1000:raise EvidenceError('OUTCOME_REPORTED_TOO_EARLY')
    if result.get('outbound_message_counts')!={'20':4,'71':1} or result.get('broker_orders_sent')!=0:
        raise EvidenceError('UNEXPECTED_EGRESS_OR_ORDER')
    probs=prediction['probabilities']
    if len(probs)!=3 or not all(math.isfinite(v) and 0<=v<=1 for v in probs) or abs(sum(probs)-1)>1e-10:
        raise EvidenceError('PROBABILITIES_INVALID')
    return {'registration_before_input':True,'forecast_before_entry':True,'outcome_after_close':True,
        'financial_orders_sent':0,'independent_statistical_efficacy':False}


def audit(root,candidate_path):
    root=Path(root);c=read(candidate_path);plan=read(root/'plan.json');p=read(root/'prediction.json');r=read(root/'result.json')
    if c['candidate_id']!=digest({k:v for k,v in c.items() if k!='candidate_id'}):raise EvidenceError('CANDIDATE_ID')
    if any(v['candidate_id']!=c['candidate_id'] for v in (plan,p,r)):raise EvidenceError('CANDIDATE_SUBJECT')
    if sha(candidate_path)!=plan['candidate_sha256'] or p['candidate_sha256']!=plan['candidate_sha256']:
        raise EvidenceError('FROZEN_CANDIDATE_FILE_CHANGED')
    if r['plan_sha256']!=sha(root/'plan.json') or r['prediction_sha256']!=sha(root/'prediction.json'):
        raise EvidenceError('FORWARD_IMMUTABLE_RECORD_CHANGED')
    checks=custody(plan,c,p,r)
    from .dataset import checked_bar
    from .bar_forward_smoke import bar_input,WindowGuard
    anchor=plan['anchor_seconds'];series={};raw_hashes={}
    for rid,what in ((9101,'MIDPOINT'),(9102,'BID'),(9103,'ASK'),(9104,'MIDPOINT')):
        path=root/(str(rid)+'.json');raw=read(path);raw_hashes[str(rid)]=sha(path)
        if raw['request_id']!=rid or raw['fields']!=WindowGuard(anchor).expected(rid) or raw['errors'] or not raw['end']:
            raise EvidenceError('REQUEST_IDENTITY_OR_COMPLETION')
        if rid!=9104 and raw['received_at_ms']>p['available_at_ms']:raise EvidenceError('INPUT_AFTER_FORECAST')
        if rid==9104 and raw['received_at_ms']<plan['outcome_ready_seconds']*1000:raise EvidenceError('IMMATURE_LABEL_CALLBACK')
        values={};end=anchor if rid!=9104 else plan['outcome_ready_seconds']
        for bar in raw['rows']:
            t,v=checked_bar(bar)
            if t+300>end:continue
            if t in values:raise EvidenceError('DUPLICATE_BAR')
            values[t]=v
        if rid!=9104:series[what]=values
        else:outcomes=values
    if {k:raw_hashes[k] for k in ('9101','9102','9103')}!=p['input_raw_sha256'] or raw_hashes['9104']!=r['label_raw_sha256']:
        raise EvidenceError('RAW_HASH_CHANGED')
    inputs=bar_input(series,anchor)
    if inputs!=read(root/'input.json') or sha(root/'input.json')!=p['input_sha256']:raise EvidenceError('ORIGINAL_FEATURE_RECONSTRUCTION')
    from .calibration_model import predict,calibrate_apply,predict_value
    import numpy as np
    reproduced=calibrate_apply(predict(c['model'],[inputs]),c['calibration'])[0]
    if not np.allclose(reproduced,p['probabilities'],atol=1e-12,rtol=0):raise EvidenceError('FROZEN_PROBABILITY_RECONSTRUCTION')
    if not np.allclose(predict_value(c['value_model'],[inputs])[0],p['value_net_bps'],atol=1e-12,rtol=0):raise EvidenceError('FROZEN_VALUE_RECONSTRUCTION')
    if any(t not in outcomes for t in range(plan['entry_seconds'],plan['outcome_ready_seconds'],300)):
        raise EvidenceError('LABEL_PATH_GAP')
    change=(float(outcomes[plan['exit_seconds']]['open'])/float(outcomes[plan['entry_seconds']]['open'])-1)*10000
    label=0 if change< -1 else 2 if change>1 else 1
    score=sum((float(reproduced[k])-float(label==k))**2 for k in range(3))
    if label!=r['label'] or abs(change-r['target_bps'])>1e-10 or abs(score-r['brier'])>1e-10:
        raise EvidenceError('FUTURE_LABEL_OR_SCORE_RECONSTRUCTION')
    return {'schema':'heptatrader.forward-independent-audit.v1','checks':checks,
        'candidate_sha256':sha(candidate_path),'plan_sha256':sha(root/'plan.json'),
        'forecast_sha256':sha(root/'prediction.json'),'result_sha256':sha(root/'result.json'),
        'raw_sha256':raw_hashes,'independent_input_features_reproduced':True,
        'independent_forecast_reproduced':True,'label':label,'target_bps':change,'brier':score,
        'candidate_id':c['candidate_id'],'outcomes':1,'promoted':False,'orders_sent':0,
        'scope':'LOCAL_IMMUTABLE_CUSTODY_AND_RECONSTRUCTION_NOT_EXTERNAL_CLOCK_ATTESTATION_OR_EFFICACY'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    r=audit(a.root,a.candidate);write_once(a.output,r);print(__import__('json').dumps(r,indent=2))
if __name__=='__main__':main()
