"""Pocket4 original-request PRE-DECISION Value343 shadow, with no Broker access.

A fixed historical Value model is used only to seal BOTH potential sides of
the exact original REQUEST, while no RESPONSE or DECISION exists yet. Scoring
only happens after separately verified execution + fee finality. No policy,
order, model promotion, live component, or original journal is modified.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time

import numpy as np
from hepta_decision_attribution import digest,read_private,strict
from . import value343_oos as prior

SCHEMA="heptatrader.value343-predecision-two-sided-shadow.v1"
SCORE_SCHEMA="heptatrader.value343-future-original-net-score.v1"
STUDY=Path("/home/alex/.local/state/heptatrader/operator-evidence/value343-chronological-oos-20261016/study-v4.json")
STUDY_SHA="dc0e463e6acc2a11a095afcc7623498959327f00d2835b9b2a0bdc8cf659f060"
SOURCE_CODE_SHA="73354f5d1fdaa0ace5c60211597c9c2d4d2f5f74ecb2f5e9ecef63cb206f246c"
REGISTRATION=prior.CONFIG
COHORT=prior.BEHAVIOR
OUT=Path("/home/alex/.local/state/heptatrader/operator-evidence/value343-predecision-shadow-20261017")
MAX_SOURCE_FILES=128
MAX_SOURCE_TOTAL=256*1024*1024
MAX_SOURCE_FILE=16*1024*1024
MAX_REQUEST_AGE_MS=90000
EXPECTED_MODEL="gpt-6-luna"
NO_AUTHORITY={"broker_orders_sent":0,"automatic_trading":False,
              "model_promotion":False,"policy_gradient":False}


def read_model():
    data=read_private(STUDY,limit=65536)
    if hashlib.sha256(data).hexdigest()!=STUDY_SHA:
        raise ValueError("FROZEN_HISTORICAL_MODEL_SHA_CHANGED")
    if hashlib.sha256(Path(prior.__file__).read_bytes()).hexdigest()!=SOURCE_CODE_SHA:
        raise ValueError("FROZEN_HISTORICAL_FITTER_CODE_CHANGED")
    j=strict(data)
    if (j.get("producer_manifest_sha256")!=COHORT
            or j.get("program_sha256")!=SOURCE_CODE_SHA
            or j.get("snapshot_rows_total")!=343
            or j.get("fixed_behavior_rows")!=335
            or j.get("train_rows")!=217
            or j.get("historical_holdout_rows")!=67
            or j.get("model_qualification")!="HISTORICAL_TIME_SPLIT_RESEARCH_ONLY"
            or j.get("model_promoted") is not False
            or j.get("economic_promotion_supported") is not False
            or j["model"].get("actual_optimizer_steps")!=20
            or tuple(j["model"]["features"])!=prior.FEATURES):
        raise ValueError("FROZEN_HISTORICAL_MODEL_SCOPE_CHANGED")
    co=np.array(j["model"]["coefficients"],dtype=np.float64)
    means=np.array(j["normalizer"]["means"],dtype=np.float64)
    scales=np.array(j["normalizer"]["scales"],dtype=np.float64)
    if (co.shape!=(len(prior.FEATURES)+1,)
            or means.shape!=(len(prior.FEATURES),)
            or scales.shape!=(len(prior.FEATURES),)
            or not all(np.isfinite(a).all() for a in (co,means,scales))
            or np.any(scales<1e-5) or j["normalizer"]["clip_after_train_scaling"]!=5):
        raise ValueError("MODEL_TRAIN_ONLY_STANDARDIZATION_CHANGED")
    median=j["train_baseline_constants_usd"]["training_median_usd"]
    if type(median) not in (float,int) or not math.isfinite(median):
        raise ValueError("TRAIN_MEDIAN_NOT_NUMERIC")
    return j,co,means,scales,float(median)


def registration():
    j=strict(read_private(REGISTRATION,limit=2*1024*1024))
    period=j["period_sha256"]
    candidates=[x for x in j["decision_attribution_registrations"]["EUR.USD"]
                if x.get("sha256")==COHORT]
    if len(candidates)!=1:raise ValueError("PINNED_BEHAVIOR_MANIFEST_NOT_UNIQUE")
    x=candidates[0]
    blob=read_private(x["manifest"],limit=65536)
    if hashlib.sha256(blob).hexdigest()!=COHORT:
        raise ValueError("BEHAVIOR_MANIFEST_HASH_CHANGED")
    manifest=strict(blob)
    if (manifest.get("schema")!="heptatrader.attribution-producer.v1"
            or manifest.get("instrument")!="EUR.USD"
            or manifest.get("period_sha256")!=period
            or manifest.get("execution_domain")!="PAPER"):
        raise ValueError("BEHAVIOR_MANIFEST_NOT_PAPER")
    return manifest


def original_events(manifest):
    """Verify ALL original per-producer record hashes and chain ordering."""
    directory=Path(manifest["journal_directory"])
    st=directory.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid()
            or st.st_mode&0o077):
        raise ValueError("ATTRIBUTION_JOURNAL_ROOT_SCOPE")
    paths=sorted(directory.glob("*.jsonl"))
    if len(paths)>MAX_SOURCE_FILES:raise ValueError("TOO_MANY_JOURNAL_FILES")
    groups=defaultdict(list);total=0
    for path in paths:
        blob=read_private(path,limit=MAX_SOURCE_FILE)
        total+=len(blob)
        if total>MAX_SOURCE_TOTAL:raise ValueError("ATTRIBUTION_SCAN_CAP")
        end=blob.rfind(b"\n")
        if end<0:continue
        good=blob[:end+1]
        lines=good.splitlines(keepends=True)
        first=strict(lines[0])
        if first.get("kind")!="HEADER":raise ValueError("JOURNAL_HEADER_MISSING")
        if first["data"].get("manifest_sha256")!=COHORT:continue
        if (first["data"].get("instrument")!="EUR.USD"
                or first["data"].get("period_sha256")!=manifest["period_sha256"]
                or first["data"].get("execution_domain")!="PAPER"):
            raise ValueError("JOURNAL_HEADER_PERIOD_OR_VENUE")
        producer=first["producer_id"]
        running=hashlib.sha256()
        offset=0
        for line in lines:
            if len(line)>262144:raise ValueError("ATTRIBUTION_RECORD_TOO_LARGE")
            record=strict(line)
            if (record.get("schema")!="heptatrader.exact-decision-attribution.v1"
                    or record.get("producer_id")!=producer
                    or record.get("sha256")!=digest({k:v for k,v in record.items()
                                                    if k!="sha256"})
                    or type(record.get("sequence")) is not int):
                raise ValueError("BROKEN_OR_MIXED_ORIGINAL_RECORD")
            running.update(line);offset+=len(line)
            record["_witness"]={"path":str(path),"bytes":offset,
                                "sha256":running.hexdigest()}
            groups[producer].append(record)
    result=[];requests={}
    for producer,events in sorted(groups.items()):
        events.sort(key=lambda x:x["sequence"])
        previous="0"*64
        for n,event in enumerate(events):
            if event["sequence"]!=n or event["previous"]!=previous:
                raise ValueError("ORIGINAL_RECORD_CHAIN_DISCONTINUITY")
            previous=event["sha256"]
            if event.get("dropped_total",0):
                raise ValueError("ATTRIBUTION_PRODUCER_DROPPED_EVENTS")
            if event["kind"]=="REQUEST":
                request=event["data"]
                request_id=request.get("request_id")
                if not isinstance(request_id,str) or len(request_id)<16:
                    raise ValueError("REQUEST_ID_SCOPE")
                if request_id in requests:raise ValueError("DUPLICATE_ORIGINAL_REQUEST")
                requests[request_id]=event
            result.append(event)
    return result,requests


def predict_two_sides(request,model):
    """The future selected side is not an input; both prices are precommitted."""
    _,co,means,scales,median=model
    if (request.get("model_id")!=EXPECTED_MODEL
            or request.get("input_scope")!="EXACT_LOCAL_INFER_CLI_REQUEST_NOT_UNOBSERVED_PROVIDER_INTERNAL_CONTEXT"
            or request.get("position_at_request")!=0
            or type(request.get("started_at_ms")) is not int):
        raise ValueError("REQUEST_NOT_SAME_ORIGINAL_BEHAVIOR")
    prompt=request.get("prompt");prompt_sha=request.get("prompt_sha256")
    if (not isinstance(prompt,str) or not isinstance(prompt_sha,str)
            or hashlib.sha256(prompt.encode()).hexdigest()!=prompt_sha):
        raise ValueError("ORIGINAL_PROMPT_HASH_CHANGED")
    forecasts={}
    for side in ("BUY","SELL"):
        features=np.asarray(prior.make_features(request,side),dtype=np.float64)
        normalized=np.clip((features-means)/scales,-5,5)
        y=float(np.clip(np.r_[1.,normalized]@co*100,-800,800))
        if not math.isfinite(y):raise ValueError("MODEL_PREDICTION_NONFINITE")
        forecasts[side]=round(y,12)
    return forecasts,median


def write_once(path,obj):
    if path.exists() or path.is_symlink():
        raise FileExistsError(str(path))
    data=(json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
    with os.fdopen(fd,"wb") as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    return hashlib.sha256(data).hexdigest()


def frozen():
    data=read_private(OUT/"freeze.json",limit=16384)
    freeze=strict(data)
    if (freeze.get("schema")!=SCHEMA
            or freeze.get("model_sha256")!=STUDY_SHA
            or freeze.get("behavior_manifest_sha256")!=COHORT
            or type(freeze.get("activated_at_ms")) is not int
            or freeze.get("activated_at_ms")>int(time.time()*1000)
            or freeze.get("code_sha256")!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            or freeze.get("broker_orders_sent")!=0):
        raise ValueError("PRECOMMITTED_MODEL_OR_CODE_CHANGED")
    return freeze


def initialize():
    if OUT.exists():raise FileExistsError("SINGLE_FIXED_SHADOW_OWNER_ALREADY_EXISTS")
    model=read_model()
    manifest=registration()
    OUT.mkdir(mode=0o700,parents=True)
    for name in ("predictions","misses","scores","rejects"):
        (OUT/name).mkdir(mode=0o700)
    freeze={"schema":SCHEMA,"activated_at_ms":int(time.time()*1000),
            "model_sha256":STUDY_SHA,"model_report_id":model[0]["report_id"],
            "behavior_manifest_sha256":COHORT,"source_period_sha256":manifest["period_sha256"],
            "code_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "feature_names":list(prior.FEATURES),"historical_train_rows":217,
            "historical_holdout_rows":67,
            "historical_model_failed_to_beat_trivial_MAE":True,
            "first_new_request_must_be_after_activation":True,
            "minimum_predecision_requirement":"REQUEST_CAPTURED_BEFORE_ANY_RESPONSE",
            "pretrade_predictions":"BOTH_BUY_AND_SELL",
            **NO_AUTHORITY}
    sha=write_once(OUT/"freeze.json",freeze)
    print(json.dumps({"activated_at_ms":freeze["activated_at_ms"],
                     "freeze_sha256":sha,"model_sha256":STUDY_SHA,
                     "mode":"SHADOW_ONLY_NO_ORDER"}))
    return 0


def capture(events,requests,freeze,model):
    after=freeze["activated_at_ms"]
    have_response={x["data"].get("request_id") for x in events
                   if x["kind"] in ("RESPONSE","DECISION","COMMAND_BINDING")}
    now=int(time.time()*1000)
    summary={"new_valid_predictions":0,"new_missed":0,"old_skipped":0,
             "already_sealed":0,"eligible_future_requests":0}
    for rid,ev in sorted(requests.items(),
                         key=lambda kv:(kv[1]["data"].get("started_at_ms",0),kv[0])):
        request=ev["data"]
        start=request.get("started_at_ms")
        if type(start) is not int or start<=after:
            summary["old_skipped"]+=1;continue
        summary["eligible_future_requests"]+=1
        out=OUT/"predictions"/(rid+".json")
        miss=OUT/"misses"/(rid+".json")
        if out.exists() or miss.exists():
            summary["already_sealed"]+=1;continue
        reason=None
        if rid in have_response:reason="RESPONSE_ALREADY_RECORDED"
        elif now-start>MAX_REQUEST_AGE_MS:reason="REQUEST_PAST_FIXED_PREDECISION_DEADLINE"
        elif request.get("position_at_request")!=0:
            reason="NON_FLAT_REQUEST_OUTSIDE_TRAINED_VALUE_SCOPE"
        elif ev.get("captured_at_ms",0)<start or ev.get("written_at_ms",0)>now:
            reason="INCONSISTENT_ORIGINAL_REQUEST_CLOCK"
        if reason:
            write_once(miss,{"schema":SCHEMA,"request_id":rid,
                "status":"MISSED_NOT_RETROACTIVE","reason":reason,
                "observed_at_ms":now,"original_event_sha256":ev["sha256"],
                "broker_orders_sent":0,"model_promotion":False})
            summary["new_missed"]+=1
            continue
        forecasts,median=predict_two_sides(request,model)
        sealed_at=int(time.time()*1000)
        if sealed_at-start>MAX_REQUEST_AGE_MS:
            write_once(miss,{"schema":SCHEMA,"request_id":rid,
                "status":"MISSED_RESEARCH_LATENCY","observed_at_ms":sealed_at,
                "original_event_sha256":ev["sha256"],**NO_AUTHORITY})
            summary["new_missed"]+=1
            continue
        receipt={"schema":SCHEMA,"request_id":rid,
            "request_started_at_ms":start,
            "original_request_capture_at_ms":ev["captured_at_ms"],
            "sealed_at_ms":sealed_at,
            "source_event_sha256":ev["sha256"],
            "source_prefix":ev["_witness"],
            "frozen_model_sha256":STUDY_SHA,
            "model_report_id":freeze["model_report_id"],
            "source_period_sha256":freeze["source_period_sha256"],
            "input_request_sha256":digest(request),
            "prompt_sha256":request["prompt_sha256"],
            "predictions_after_actual_reported_fees_usd":forecasts,
            "train_only_median_baseline_usd":median,
            "selected_side_unknown_before_response":True,
            "predecision_at_capture":True,
            "prospective_holdout_efficacy_unproven":True,
            **NO_AUTHORITY}
        write_once(out,receipt)
        summary["new_valid_predictions"]+=1
    return summary


def score(events,requests,freeze):
    """Only settle future sealed records; never revise previous predictions."""
    paths=list((OUT/"predictions").glob("*.json"))
    if not paths:
        return {"pending":0,"new_scores":0,"new_rejected":0}
    rewards=strict(read_private(prior.REWARDS,limit=32*1024*1024))
    if rewards.get("broker_orders_sent")!=0:raise ValueError("REWARD_OBSERVER_SCOPE_CHANGED")
    reward_by_input=defaultdict(list)
    for r in rewards.get("rewards",[]):
        if r.get("input_attribution",{}).get("producer_manifest_sha256")==COHORT:
            reward_by_input[r.get("input_id")].append(r)
    bindings=defaultdict(list);decisions=defaultdict(list)
    for event in events:
        d=event.get("data",{})
        if event["kind"]=="COMMAND_BINDING":
            bindings[d.get("request_id")].append(d)
        elif event["kind"]=="DECISION":
            decisions[d.get("request_id")].append(d)
    matched=[]
    for path in paths:
        p=strict(read_private(path,limit=16384))
        rid=p["request_id"]
        rlist=reward_by_input.get(p["input_request_sha256"],[])
        if rlist:matched.append((p,rlist,bindings.get(rid,[]),decisions.get(rid,[])))
    if not matched:
        return {"pending":len(paths),"new_scores":0,"new_rejected":0}
    distinct={}
    for p,rlist,bs,ds in matched:
        if len(rlist)!=1 or len(bs)!=1 or len(ds)!=1:
            raise ValueError("FORWARD_FUTURE_ORIGINAL_BINDING_AMBIGUOUS")
        distinct[rlist[0]["reward_id"]]=rlist[0]
    events_by_id,_=prior.original_oms_events(list(distinct.values()))
    changed=0;reject=0
    for p,rewards_list,bindings_list,decisions_list in matched:
        rid=p["request_id"]
        good=OUT/"scores"/(rid+".json")
        bad=OUT/"rejects"/(rid+".json")
        if good.exists() or bad.exists():continue
        source=requests.get(rid)
        if source is None:raise ValueError("ORIGINAL_REQUEST_LOST")
        request=source["data"]
        reward=rewards_list[0]
        binding=bindings_list[0]
        decision=decisions_list[0]
        if (binding.get("request_id")!=rid
                or binding.get("request_sha256")!=digest(request)
                or reward["input_id"]!=digest(request)
                or decision.get("request_sha256")!=digest(request)):
            raise ValueError("FUTURE_BINDING_SOURCE_MISMATCH")
        # No retroactive seal: the prediction must PRECEDE the actual
        # original decision and later outcome first-available time.
        decision_time=decision["available_at_ms"]
        known_at=prior.settled_time(reward,events_by_id,request,binding)
        if (p["sealed_at_ms"]>=decision_time
                or p["sealed_at_ms"]>=binding["command"]["created_at_ms"]
                or p["sealed_at_ms"]>=known_at):
            write_once(bad,{"schema":SCORE_SCHEMA,"request_id":rid,
                "status":"CENSORED_SEAL_NOT_BEFORE_ORIGINAL_DECISION_OR_LABEL",
                "decision_at_ms":decision_time,"label_known_at_ms":known_at,
                "score_valid":False,**NO_AUTHORITY})
            reject+=1;continue
        econ=prior.verify_100k_reward(reward,request,binding)
        side=binding["command"]["side"]
        value=p["predictions_after_actual_reported_fees_usd"][side]
        median=p["train_only_median_baseline_usd"]
        net=float(econ["net_usd"])
        write_once(good,{"schema":SCORE_SCHEMA,"request_id":rid,
            "status":"ORIGINAL_SETTLED_RESEARCH_ONLY","score_valid":True,
            "original_reward_id":reward["reward_id"],
            "prediction_sha256":hashlib.sha256(read_private(
                OUT/"predictions"/(rid+".json"),limit=16384)).hexdigest(),
            "original_input_sha256":reward["input_id"],
            "frozen_model_sha256":STUDY_SHA,
            "original_side":side,
            "prediction_sealed_at_ms":p["sealed_at_ms"],
            "decision_first_available_ms":decision_time,
            "original_execution_and_fees_known_at_ms":known_at,
            "actual_net_after_reported_fees_usd":net,
            "model_prediction_net_usd":value,
            "training_median_prediction_net_usd":median,
            "model_absolute_error_usd":abs(value-net),
            "median_absolute_error_usd":abs(median-net),
            "no_counterfactual_buy_sell_conclusion":True,
            **NO_AUTHORITY})
        changed+=1
    unscored=sum(not (OUT/"scores"/p.name).exists()
                 and not (OUT/"rejects"/p.name).exists() for p in paths)
    return {"pending":unscored,"new_scores":changed,"new_rejected":reject}


def run_once():
    freeze=frozen()
    model=read_model()
    manifest=registration()
    if manifest["period_sha256"]!=freeze["source_period_sha256"]:
        raise ValueError("SOURCE_PERIOD_ROTATED_USE_NEW_FROZEN_STUDY")
    events,requests=original_events(manifest)
    a=capture(events,requests,freeze,model)
    b=score(events,requests,freeze)
    counts={name:len(list((OUT/name).glob("*.json")))
            for name in ("predictions","misses","scores","rejects")}
    print(json.dumps({"schema":SCHEMA,"checked_at_ms":int(time.time()*1000),
        "counts":counts,"capture":a,"score":b,
        "broker_orders_sent":0,"policy_change":False},sort_keys=True))
    return 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode",choices=("init","once","status"),required=True)
    a=p.parse_args()
    if a.mode=="init":return initialize()
    if a.mode=="once":return run_once()
    freeze=frozen()
    print(json.dumps({"schema":SCHEMA,"activated_at_ms":freeze["activated_at_ms"],
        "receipt_counts":{n:len(list((OUT/n).glob("*.json")))
                 for n in ("predictions","misses","scores","rejects")},
        **NO_AUTHORITY},sort_keys=True))
    return 0


if __name__=="__main__":raise SystemExit(main())
