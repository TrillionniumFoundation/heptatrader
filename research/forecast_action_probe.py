"""One bounded NEW-source, no-order timing probe; never requests broker data.

Consumes the already running Laya feed through its original chain verifier.
Uses a declared zero-alpha midpoint baseline and controlled 6.2-second latency,
not a remote LLM performance or profitability claim. Old trials are untouched.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time
from forecast_action_boundary import Quote,Forecast,Policy,decide,require_durable_before_deadline
from laya_cell import shadow_source


def now():return time.time_ns()//1000000

def seal(path: Path, value: dict):
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        position=0
        while position<len(raw):
            count=os.write(fd,raw[position:])
            if count<=0:raise OSError('SHORT_WRITE')
            position+=count
        os.fsync(fd)
    finally:os.close(fd)
    parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:os.fsync(parent)
    finally:os.close(parent)
    return hashlib.sha256(raw).hexdigest(),now()


def next_original(root: Path, after: int, end: float, period=None):
    # One declared deadline, not repeated experiments until a favorable result.
    while time.monotonic()<end:
        files=sorted(root.glob('segment-*/quotes.jsonl'),key=lambda p:p.stat().st_mtime_ns,reverse=True)[:2]
        for path in files:
            header,quotes,proof=shadow_source.read_source(path)
            if period is not None and header['period_sha256']!=period:raise ValueError('PERIOD_CHANGED')
            eligible=[q for q in quotes if q['t']>after and q['known_at_ms']<=now()]
            if not eligible:continue
            selected=min(eligible,key=lambda q:(q['known_at_ms'],q['t']))
            # Reopen only the exact already-verified immutable prefix; refuse changes.
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
            try:
                with os.fdopen(fd,'rb',closefd=False) as source:raw=source.read(proof['bytes'])
            finally:os.close(fd)
            if hashlib.sha256(raw).hexdigest()!=proof['sha256']:raise ValueError('SOURCE_PREFIX_CHANGED')
            record=next(shadow_source.strict(line) for line in raw.splitlines()
                        if shadow_source.strict(line).get('sha256')==selected['receipt'])
            data=record['data'];expiry=min(data['stale_after_ms'],data['observed_at_ms']+5000)
            quote=Quote(data['instrument'],data['subscription_id'],record['sha256'],
                        data['observed_at_ms'],record['at_ms'],expiry,float(data['bid']),float(data['ask']))
            if now()>=quote.expires_at_ms:raise ValueError('FIRST_NEW_QUOTE_EXPIRED_BEFORE_USE')
            return quote,header,proof
        time.sleep(.2)
    raise TimeoutError('DECLARED_SOURCE_WINDOW_EXPIRED')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    os.umask(0o077);args.output.mkdir(mode=0o700,parents=False,exist_ok=False)
    policy=Policy('zero-alpha-timing-6200ms-v1',hashlib.sha256(b'original-input-midpoint-v1').hexdigest(),60000,60000,20,2)
    begin=now();deadline=time.monotonic()+60
    plan={'started_at_ms':begin,'window_ms':60000,'controlled_latency_ms':6200,
          'policy':asdict(policy),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'boundary_sha256':hashlib.sha256(Path(__file__).with_name('forecast_action_boundary.py').read_bytes()).hexdigest(),
          'original_parser_sha256':hashlib.sha256(Path(shadow_source.__file__).read_bytes()).hexdigest(),
          'model':'ZERO_ALPHA_INPUT_MIDPOINT','new_broker_requests':0,'new_orders':0}
    seal(args.output/'plan.json',plan)
    try:
        original,header,first_proof=next_original(args.source_root,begin,deadline)
        started=now();predicted_mid=(original.bid+original.ask)/2
        time.sleep(6.2)  # Deliberate latency injection, not a measured LLM runtime.
        produced=now()
        payload={'policy_id':policy.policy_id,'model_sha256':policy.model_sha256,
                 'input_quote':asdict(original),'started_at_ms':started,
                 'produced_at_ms':produced,'predicted_mid':predicted_mid}
        forecast_hash,committed=seal(args.output/'forecast.json',payload)
        forecast=Forecast(policy.policy_id,policy.model_sha256,original,started,produced,committed,predicted_mid)
        current,second_header,second_proof=next_original(args.source_root,committed,deadline,header['period_sha256'])
        decision=decide(forecast,current,policy,now())
        decision_hash,durable=seal(args.output/'decision.json',asdict(decision))
        require_durable_before_deadline(decision,durable)
        result={'status':'TIMING_PASS_NO_AUTHORIZATION','forecast':asdict(forecast),'current_quote':asdict(current),
                'forecast_sha256':forecast_hash,'decision_sha256':decision_hash,'decision':asdict(decision),
                'persisted_at_ms':durable,'source_prefixes':[first_proof,second_proof],
                'old_input_expired_before_forecast_commit':committed>=original.expires_at_ms,
                'new_quote_to_durable_ms':durable-current.observed_at_ms,
                'remaining_quote_budget_ms':current.expires_at_ms-durable,
                'new_broker_requests':0,'new_orders':0,'authorization_effect':'NONE'}
        seal(args.output/'result.json',result)
        print(json.dumps({k:result[k] for k in ('status','old_input_expired_before_forecast_commit',
              'new_quote_to_durable_ms','remaining_quote_budget_ms','new_broker_requests','new_orders')},sort_keys=True))
    except Exception as exc:
        seal(args.output/'failure.json',{'status':'FAILED_RETAINED','type':type(exc).__name__,
             'reason':str(exc),'at_ms':now(),'new_broker_requests':0,'new_orders':0})
        raise

if __name__=='__main__':main()
