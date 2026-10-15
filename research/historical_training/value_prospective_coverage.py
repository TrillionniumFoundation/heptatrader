"""Observational Value-request coverage, not a prediction or trade authority.

Collect only requests from the original verified attribution stream. Keep
expired requests expired, authenticate committed predictions with the original
public witness and never change old study outcomes or signed trial settings.
"""
from __future__ import annotations
import argparse,collections,json,os,re,statistics,time
from pathlib import Path

WINDOW_MS=12*3600*1000
INPUT_DEADLINE_MS=60000
UUID_RE=re.compile(r'[0-9a-f]{32}')

def classify(request,folder,now_ms,verified_prediction=None):
    ts=request.get('started_at_ms')
    if type(ts) is not int or ts>now_ms or now_ms-ts<INPUT_DEADLINE_MS:
        return 'IMMATURE',None
    commit=folder/'commit.json';failure=folder/'failure.json'
    if commit.exists():
        if verified_prediction is None:
            return 'SIGNED_WITNESS_NOT_REVERIFIED',None
        try:
            prediction=verified_prediction()
            signed=prediction['committed_at_ms']
            if (type(signed) is not int or signed<ts or signed>=ts+INPUT_DEADLINE_MS):
                return 'LATE_OR_INVALID_COMMIT',None
            return 'VERIFIED_PREDEADLINE',signed-ts
        except Exception:
            return 'INVALID_OR_REVOKED_COMMIT',None
    if failure.exists():
        try:
            value=json.loads(failure.read_bytes())
            if value.get('reason')=='REQUEST_ALREADY_OLD_NO_BACKDATING':
                return 'IMMUTABLE_DEADLINE_MISS',None
        except (ValueError,OSError): pass
        return 'IMMUTABLE_OTHER_FAILURE',None
    return 'UNRESOLVED_EXPIRED',None

def percentiles(ms):
    if not ms:return {'p50_ms':None,'p95_ms':None,'max_ms':None}
    s=sorted(ms)
    return {'p50_ms':s[(len(s)-1)//2],
            'p95_ms':s[int((len(s)-1)*.95)],
            'max_ms':s[-1]}

def summarize(requests,root,plan,now_ms,window_start_ms,verifier,
              denial_reasons=None,producer_issues=None):
    end=plan['input_end_ms'];begin=plan['input_start_ms']
    denominator=collections.Counter();attribution=collections.Counter();latencies=[]
    for rid,item in requests.items():
        req=item['request'];ts=req.get('started_at_ms')
        if (not isinstance(rid,str) or not UUID_RE.fullmatch(rid)
                or type(ts) is not int or not begin<=ts<end
                or not window_start_ms<=ts<=now_ms
                or req.get('position_at_request')!=0):
            continue
        folder=root/'plans'/plan['plan_id']/'predictions'/rid
        state,delta=classify(req,folder,now_ms,
               verified_prediction=lambda:verifier(rid))
        if state=='IMMATURE':continue
        denominator[state]+=1
        if delta is not None:latencies.append(delta)
        for cause in set((denial_reasons or {}).get(rid,[])):
            attribution[cause]+=1
    total=sum(denominator.values())
    sealed=denominator['VERIFIED_PREDEADLINE']
    issues=producer_issues or []
    return {'state':'SOURCE_ISSUES_NO_EFFICACY_CLAIM' if issues else 'OBSERVATIONAL_COVERAGE_ONLY',
            'matured_eligible_flat_original_requests':total,
            'verified_predeadline_commits':sealed,
            'coverage_fraction':sealed/total if total else None,
            'outcomes':dict(denominator),
            'resource_denial_reasons':dict(attribution),
            'commit_latency':percentiles(latencies),
            'source_issue_count':len(issues),
            'posthoc_not_efficacy':True,
            'trade_authority':False,
            'selection_performed':False}

def _denials(directory,now_ms,start_ms):
    result=collections.defaultdict(list)
    for path in directory.glob('*.json'):
        try:
            v=json.loads(path.read_bytes());decision=v['decision'];subject=v['subject']
            if decision.get('role')!='value-predictor':continue
            ts=decision.get('at_ms')
            if type(ts) is not int or not start_ms<=ts<=now_ms:continue
            for rid in subject.get('requests',[]):
                if UUID_RE.fullmatch(rid):
                    result[rid].extend(decision.get('reasons',[]))
        except (ValueError,KeyError,TypeError,OSError):continue
    return result

def run(config_path,output_path,cutoff_ms):
    from laya_cell.integrity import read_json,sha_file
    from laya_cell.financial_forward_evaluate import read_prediction,validate_registration
    from laya_cell.financial_forward_source import ingress
    c=read_json(Path(config_path))
    if c.get('automatic_trading') is not False or c.get('actor_update') is not False:
        raise ValueError('OBSERVATIONAL_SCOPE_REQUIRED')
    root=Path(c['forward_root']);plans=list(root.glob('plans/*/plan.json'))
    if len(plans)!=1:raise ValueError('ONE_ORIGINAL_PLAN_REQUIRED')
    plan=read_json(plans[0]);validate_registration(c,plans[0],plan)
    now_ms=int(time.time()*1000)
    if type(cutoff_ms) is not int or cutoff_ms<=0 or cutoff_ms>now_ms:
        raise ValueError('INVALID_RESOURCE_RESTORATION_CUTOFF')
    _,requests,_,issues=ingress(plan['producer_registration'],c['period_sha256'])
    guard=read_json(Path(c['guard_config']))
    if (guard.get('trade_authority') is not False
            or guard.get('period_sha256')!=c['period_sha256']):
        raise ValueError('PROSPECTIVE_RESOURCE_GUARD_SCOPE')
    denials=_denials(Path(guard['state_root'])/'denials',now_ms,now_ms-WINDOW_MS)
    def verified(rid):
        return read_prediction(root,plan,rid,c)
    recent=summarize(requests,root,plan,now_ms,now_ms-WINDOW_MS,verified,denials,issues)
    after=summarize(requests,root,plan,now_ms,cutoff_ms,verified,denials,issues)
    result={
        'schema':'heptatrader.value-original-coverage-observer.v1',
        'at_ms':now_ms,'window_start_ms':now_ms-WINDOW_MS,
        'resource_restoration_cutoff_ms':cutoff_ms,
        'original_plan_id':plan['plan_id'],
        'original_plan_sha256':sha_file(plans[0]),
        'producer_manifest_sha256':plan['producer_registration']['sha256'],
        'windows':{'last_12h':recent,'after_resource_restoration':after},
        'original_trial_mutated':False,
        'no_signing_backdating':True,'broker_queries':0,'broker_orders_sent':0,
        'automatic_model_promotion':False}
    output_path=Path(output_path)
    if output_path.exists():raise ValueError('READONLY_REPORT_NEVER_OVERWRITE')
    from .common import write_once
    write_once(output_path,result)
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',required=True)
    p.add_argument('--after-ms',required=True,type=int)
    p.add_argument('--output',required=True)
    a=p.parse_args();os.umask(0o077)
    result=run(a.config,a.output,a.after_ms)
    print(json.dumps(result['windows'],sort_keys=True),flush=True)
if __name__=='__main__':main()
