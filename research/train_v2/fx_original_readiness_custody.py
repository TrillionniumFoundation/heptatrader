"""Separate, fixed-date source-health snapshot custody for the already-frozen FX study.

This corroborates source readiness at *future* decision time with original bytes
and period-bound validation. It never changes the ongoing trial or supplies
product authorization. Window 0 is past and intentionally NOT reconstructed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import time

SCHEMA="heptatrader.fx-forward-original-health-custody.v1"
ORIGINAL_PLAN_SHA="02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04"
ORIGINAL_SEAL_SHA="1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b"
STATE_READY="READY_FOR_REVIEW_NOT_AUTHORIZATION"
ALLOWED_STATUSES=frozenset(("USABLE","PATH_GAP","ENDPOINT_GAP","INPUT_UNAVAILABLE","OUTCOME_UNAVAILABLE"))
RULE={
  "schema":"heptatrader.quote-readiness.v1",
  "input_probes":24,"spacing_ms":60000,"horizon_ms":300000,
  "input_grace_ms":60000,"endpoint_tolerance_ms":5000,
  "path_gap_max_ms":30000,"minimum_usable":20,"maximum_missing":4,
  "source_ttl_ms":5000,
  "statistics_scope":"OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE",
}
PRECOMMIT_MS=5*60000
CAPTURE_BEGIN_MS=1000
CAPTURE_END_MS=4500
MAX_STATUS_AGE_MS=30000

class CustodyHold(ValueError):
    pass

def require(value,reason):
    if not value:raise CustodyHold(reason)

def now_ms():
    return time.time_ns()//1000000

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(o):
    return json.dumps(o,sort_keys=True,separators=(",",":"),allow_nan=False).encode()

def code_digest():
    return digest(Path(__file__).read_bytes())

def private_dir(path,create=False):
    path=Path(path)
    require(path.is_absolute() and ".." not in path.parts
            and not path.is_symlink(),"PRIVATE_DIR_PATH")
    if create:
        require(not path.exists(),"EVIDENCE_DESTINATION_ALREADY_EXISTS")
        path.mkdir(mode=0o700)
    st=path.stat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid()
            and stat.S_IMODE(st.st_mode)==0o700,"PRIVATE_DIRECTORY_REQUIRED")
    return path

def read_exact(path,limit=1024*1024):
    path=Path(path)
    require(path.is_absolute() and not path.is_symlink(),"SOURCE_SYMLINK_OR_RELATIVE")
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid()
                and s.st_nlink==1 and stat.S_IMODE(s.st_mode)&0o077==0
                and 0 < s.st_size <= limit,"SOURCE_NOT_PRIVATE_REGULAR")
        raw=b""
        while len(raw)<s.st_size:
            piece=os.read(fd,min(256*1024,s.st_size-len(raw)))
            require(bool(piece),"SHORT_READ")
            raw+=piece
        e=os.fstat(fd)
        require((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==
                (e.st_dev,e.st_ino,e.st_size,e.st_mtime_ns),
                "SOURCE_CHANGED_WHILE_READING")
        return raw
    finally:
        os.close(fd)

def strict(raw):
    def unique(pairs):
        d={}
        for k,v in pairs:
            require(k not in d,"DUPLICATE_JSON_KEY")
            d[k]=v
        return d
    return json.loads(raw,object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(CustodyHold("NONFINITE_JSON")))

def write_once(path,raw):
    path=Path(path)
    private_dir(path.parent)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(d)
    finally:os.close(d)

def verify_frozen(original_root):
    r=private_dir(original_root)
    raw=read_exact(r/"plan.json")
    seal=read_exact(r/"sealed.json")
    require(digest(raw)==ORIGINAL_PLAN_SHA
            and digest(seal)==ORIGINAL_SEAL_SHA,"ORIGINAL_ROOT_PRECOMMIT_CHANGED")
    plan,document=strict(raw),strict(seal)
    require(plan.get("schema")=="heptatrader.fx-original-future-price-cost-calendar.v1"
            and document.get("plan_sha256")==ORIGINAL_PLAN_SHA
            and plan.get("trade_authority") is False
            and plan.get("model_promotion_allowed") is False
            and plan.get("automatic_training") is False
            and plan.get("not_native_train_v2_120min_spec") is True
            and plan.get("behavior_policy")=="NO_TRADE"
            and type(plan.get("anchors_ms")) is list
            and len(plan["anchors_ms"])==4
            and len(document.get("windows",[]))==4,
            "FROZEN_TRIAL_NOT_ABSTENTION")
    for i,w in enumerate(document["windows"]):
        anchor=plan["anchors_ms"][i]
        require(w["index"]==i and w["anchor_ms"]==anchor
                and w["entry_first_ms"]==anchor+1000
                and w["entry_last_ms"]==anchor+4500,
                "TRIAL_WINDOW_CLOCK_CHANGED")
    return plan

def evaluate_health(health,plan,capture_started_ms):
    issues=[]
    if health.get("schema")!="heptatrader.quote-readiness-status.v1":
        issues.append("READINESS_SCHEMA_CHANGED")
    if health.get("period_sha256")!=plan["period_sha256"]:
        issues.append("READINESS_PERIOD_NOT_ORIGINAL_PAPER")
    if health.get("rule")!=RULE:
        issues.append("SOURCE_MONITOR_RULE_CHANGED")
    probes=health.get("probes")
    counters=health.get("counts")
    if not (type(probes) is list and len(probes)==24
            and type(counters) is dict
            and all(type(x) is dict and x.get("status") in ALLOWED_STATUSES
                    for x in probes)):
        issues.append("READINESS_PROBE_POPULATION_INVALID")
    else:
        actual=dict(Counter(p["status"] for p in probes))
        if actual!=counters or health.get("usable")!=actual.get("USABLE",0) or (
            health.get("unusable")!=24-actual.get("USABLE",0)):
            issues.append("READINESS_PROBE_COUNTS_NOT_REPRODUCIBLE")
    src=health.get("source_prefixes")
    finger=health.get("source_prefix_digest")
    if (type(src) is not list or type(finger) is not str
            or digest(canonical(src))!=finger):
        issues.append("READINESS_PREFIX_DIGEST_CHANGED")
    stamp=health.get("at_ms")
    if type(stamp) is not int or not 0<=capture_started_ms-stamp<=MAX_STATUS_AGE_MS:
        issues.append("READINESS_SNAPSHOT_STALE_OR_FUTURE")
    source_clock=health.get("latest_source_quote_ms")
    age=health.get("latest_quote_age_ms")
    if (type(source_clock) is not int or type(age) is not int
            or type(stamp) is not int or stamp-source_clock!=age
            or not 0<=age<=RULE["source_ttl_ms"]):
        issues.append("LAST_ORIGINAL_QUOTE_NOT_FRESH_OR_CLOCK_UNPROVEN")
    if health.get("automatic_trial_registration") is not False or (
        health.get("trading_authority") is not False):
        issues.append("SOURCE_MONITOR_AUTHORITY_CHANGED")
    if (health.get("state")!=STATE_READY or health.get("reasons")!=[]
            or health.get("usable",0)<20
            or health.get("unusable",24)>4):
        issues.append("READINESS_HOLD_OR_INSUFFICIENT")
    return sorted(set(issues))

def preseal(original_root,output_root):
    plan=verify_frozen(original_root)
    current=now_ms()
    require(plan["anchors_ms"][1]-current>=PRECOMMIT_MS,
            "LATE_PROOF_REGISTRATION_CANNOT_BE_PRESEALED")
    private_dir(output_root,create=True)
    obj={
        "schema":SCHEMA+".plan.v1",
        "registered_at_ms":current,
        "original_root_path":str(original_root),
        "original_plan_sha256":ORIGINAL_PLAN_SHA,
        "original_seal_sha256":ORIGINAL_SEAL_SHA,
        "planned_capture_windows":[
            {"index":i,"anchor_ms":a,
             "capture_first_ms":a+CAPTURE_BEGIN_MS,
             "capture_last_ms":a+CAPTURE_END_MS}
            for i,a in enumerate(plan["anchors_ms"]) if i>0],
        "source_monitor_path":plan["readiness_file"],
        "period_sha256":plan["period_sha256"],
        "source_monitor_fixed_rule":RULE,
        "capture_code_sha256":code_digest(),
        "past_window_00_missed_and_cannot_be_reconstructed":True,
        "full_monitor_snapshot_custody_is_local_not_broker_signed":True,
        "does_not_amend_original_study":True,
        "independent_product_witness_or_owner_enrolled":False,
        "trade_authority":False,"broker_queries_sent":0,"broker_orders_sent":0,
        "model_promotion_allowed":False,
    }
    write_once(Path(output_root)/"witness-plan.json",canonical(obj)+b"\n")
    return {"state":"PRESEALED_CUSTODY_FOR_THREE_UNSEEN_REMAINING_WINDOWS",
            "registered_at_ms":current,
            "planned_capture_indices":[1,2,3],
            "plan_sha256":digest(read_exact(Path(output_root)/"witness-plan.json")),
            "broker_orders_sent":0}

def verify_plan(original_root,output_root):
    original=verify_frozen(original_root)
    custody=strict(read_exact(Path(output_root)/"witness-plan.json"))
    require(custody.get("schema")==SCHEMA+".plan.v1"
            and custody.get("capture_code_sha256")==code_digest()
            and custody.get("original_plan_sha256")==ORIGINAL_PLAN_SHA
            and custody.get("original_seal_sha256")==ORIGINAL_SEAL_SHA
            and custody.get("period_sha256")==original["period_sha256"]
            and custody.get("source_monitor_path")==original["readiness_file"]
            and custody.get("source_monitor_fixed_rule")==RULE
            and custody.get("trade_authority") is False
            and custody.get("model_promotion_allowed") is False
            and custody.get("past_window_00_missed_and_cannot_be_reconstructed") is True,
            "READINESS_CUSTODY_PRECOMMIT_CHANGED")
    windows=custody.get("planned_capture_windows")
    require(type(windows) is list and len(windows)==3
            and all(w.get("index")==i and
                    w.get("anchor_ms")==original["anchors_ms"][i]
                    and w.get("capture_first_ms")==original["anchors_ms"][i]+1000
                    and w.get("capture_last_ms")==original["anchors_ms"][i]+4500
                    for i,w in enumerate(windows,1)),
            "CUSTODY_TIMES_NOT_FROM_FROZEN_TRIAL")
    return original,custody

def snapshot_once(original_root,output_root,index,*,captured_at_ms=None):
    original,custody=verify_plan(original_root,output_root)
    require(index in (1,2,3),"PRESENT_OR_FUTURE_ONLY_NO_OLD_WINDOW")
    window=custody["planned_capture_windows"][index-1]
    first,last=window["capture_first_ms"],window["capture_last_ms"]
    started=now_ms() if captured_at_ms is None else captured_at_ms
    base=Path(output_root)
    destination=base/("window-%02d-health-attempt.json"%index)
    require(not destination.exists() and not destination.is_symlink(),
            "NEVER_OVERWRITE_OR_RETRY_WITNESS")
    receipt={
        "schema":SCHEMA+".snapshot-attempt.v1",
        "window":index,"anchor_ms":window["anchor_ms"],
        "attempt_started_at_ms":started,
        "source_health_status_sha256":None,
        "source_health_status_bytes":None,
        "readiness_health_at_ms":None,
        "recomputed_source_usable_probes":None,
        "original_study_entry_readiness_proven":False,
        "copy_is_not_broker_signed_or_production_witness":True,
        "no_retroactive_relabel_of_original_trial":True,
        "broker_queries_sent":0,"broker_orders_sent":0,"trade_authority":False,
        "model_promotion_allowed":False,
    }
    if not first<=started<=last:
        receipt.update(state="MISSED",reasons=["CAPTURE_OUTSIDE_ORIGINAL_WINDOW"])
        write_once(destination,canonical(receipt)+b"\n")
        return receipt
    try:
        raw=read_exact(Path(custody["source_monitor_path"]))
        health=strict(raw)
        reasons=evaluate_health(health,original,started)
        # Durable raw snapshot, even when the monitor is HOLD. Never
        # substitute the later health file into this timed research slot.
        snap_path=base/("window-%02d-health-original.json"%index)
        write_once(snap_path,raw)
        durable=now_ms() if captured_at_ms is None else captured_at_ms+100
        if durable>last:
            reasons.append("HEALTH_WITNESS_DURABLE_AFTER_CAPTURE_DEADLINE")
        receipt.update(
            state="CORROBORATING_SOURCE_READY_NO_TRADE" if not reasons else "HOLD",
            reasons=sorted(set(reasons)),
            durable_at_ms=durable,
            source_health_status_sha256=digest(raw),
            source_health_status_bytes=len(raw),
            readiness_health_at_ms=health.get("at_ms"),
            recomputed_source_usable_probes=(
                health.get("usable") if type(health.get("usable")) is int else None),
        )
    except Exception as err:
        receipt.update(state="HOLD",reasons=[
            "UNTRUSTED_SOURCE_"+type(err).__name__])
    write_once(destination,canonical(receipt)+b"\n")
    return receipt

def wait_to(target):
    while True:
        delta=target-now_ms()
        if delta<=0: return
        time.sleep(min(4,max(0.001,(delta-30)/1000)))

def run(original_root,output_root):
    original,plan=verify_plan(original_root,output_root)
    first=plan["planned_capture_windows"][0]["capture_first_ms"]
    require(now_ms()<first,"RUNNER_STARTED_AFTER_FIRST_FUTURE_CAPTURE")
    start=Path(output_root)/"witness-worker-start.json"
    require(not start.exists(),"RUNNER_ALREADY_STARTED")
    write_once(start,canonical({
        "schema":SCHEMA+".worker-start.v1",
        "started_at_ms":now_ms(),
        "plan_sha256":digest(read_exact(Path(output_root)/"witness-plan.json")),
        "no_restart_no_retry":True,"broker_queries_or_orders_sent":0,
        "no_trading_or_promotions":True
    })+b"\n")
    summary=[]
    for w in plan["planned_capture_windows"]:
        wait_to(w["capture_first_ms"])
        r=snapshot_once(original_root,output_root,w["index"])
        summary.append({"window":w["index"],"state":r["state"],
                        "witness_receipt_sha256":digest(read_exact(
                            Path(output_root)/("window-%02d-health-attempt.json"%w["index"])))})
        print(json.dumps({"window":w["index"],"state":r["state"],
                          "reasons":r["reasons"],"broker_orders_sent":0}),flush=True)
    write_once(Path(output_root)/"witness-final.json",canonical({
        "schema":SCHEMA+".final.v1",
        "plan_sha256":digest(read_exact(Path(output_root)/"witness-plan.json")),
        "windows":summary,
        "original_study_amended":False,
        "production_root_or_witness_authority":False,
        "broker_orders_sent":0
    })+b"\n")
    return {"state":"LOCAL_SOURCE_HEALTH_CORROBORATION_FINISHED",
            "count":len(summary),"no_trade_or_promotion":True}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",required=True,choices=["preseal","verify","run"])
    parser.add_argument("--original-root",required=True,type=Path)
    parser.add_argument("--output-root",required=True,type=Path)
    args=parser.parse_args()
    os.umask(0o077)
    if args.mode=="preseal":
        result=preseal(args.original_root,args.output_root)
    elif args.mode=="verify":
        _,p=verify_plan(args.original_root,args.output_root)
        result={"state":"PINNED_SOURCE_CUSTODY_PLAN_VERIFIED",
                "windows":len(p["planned_capture_windows"]),
                "trade_authority":False}
    else:
        result=run(args.original_root,args.output_root)
    print(json.dumps(result,sort_keys=True),flush=True)

if __name__=="__main__":
    main()
