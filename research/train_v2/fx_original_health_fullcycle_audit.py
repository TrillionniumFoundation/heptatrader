"""Shared sealed-health audit and historical 24-cycle command entry.

No market connection, order, native owner or training. A complete source-health
snapshot is one observational source artifact, not signed broker provenance.
"""
from __future__ import annotations

from collections import Counter
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from datetime import datetime,timezone

SCHEMA="heptatrader.fx-original-fullcycle-health-independent-audit.v1"
COHORT="heptatrader.fx-original-fullcycle-source-archive.v1"
STEP=15000
N=24
MAX_AGE=30000
RULE={"schema":"heptatrader.quote-readiness.v1","input_probes":24,
      "spacing_ms":60000,"horizon_ms":300000,"input_grace_ms":60000,
      "endpoint_tolerance_ms":5000,"path_gap_max_ms":30000,
      "minimum_usable":20,"maximum_missing":4,"source_ttl_ms":5000,
      "statistics_scope":"OVERLAPPING_OPERATIONAL_COVERAGE_NOT_PERFORMANCE"}
class IntegrityHold(ValueError):pass
def demand(ok,msg):
    if not ok: raise IntegrityHold(msg)
def digest(data):return hashlib.sha256(data).hexdigest()
def encode(o):return json.dumps(o,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def strict(data):
    def pairs(items):
        result={}
        for k,v in items:
            demand(k not in result,"DUPLICATE_JSON_KEY")
            result[k]=v
        return result
    return json.loads(data,object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(IntegrityHold("NONFINITE_JSON")))

def original_bytes(path,max_bytes=1024*1024):
    path=Path(path)
    demand(path.is_absolute() and not path.is_symlink() and ".." not in path.parts,
           "ORIGINAL_PATH_UNSAFE")
    fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        st=os.fstat(fd)
        demand(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid()
               and st.st_nlink==1 and st.st_mode&0o077==0
               and 0<st.st_size<=max_bytes,"ORIGINAL_BYTES_MODE_OR_SIZE")
        b=bytearray()
        while len(b)<st.st_size:
            piece=os.read(fd,min(65536,st.st_size-len(b)))
            demand(bool(piece),"ORIGINAL_BYTES_TRUNCATED")
            b.extend(piece)
        later=os.fstat(fd)
        demand((st.st_ino,st.st_dev,st.st_size,st.st_mtime_ns)==
               (later.st_ino,later.st_dev,later.st_size,later.st_mtime_ns),
               "ORIGINAL_BYTES_CHANGED")
        return bytes(b)
    finally:os.close(fd)

def raw_obj(root,filename,max_bytes=1024*1024):
    data=original_bytes(Path(root)/filename,max_bytes)
    return strict(data),digest(data)

def check_status(status,period,receipt):
    demand(status.get("schema")=="heptatrader.quote-readiness-status.v1"
           and status.get("period_sha256")==period
           and status.get("rule")==RULE,
           "ORIGINAL_READINESS_SCOPE_CHANGED")
    probes=status.get("probes")
    demand(type(probes) is list and len(probes)==24
           and all(type(p) is dict and type(p.get("status")) is str
                   for p in probes),"ORIGINAL_24_PROBES_MISSING")
    counts=dict(Counter(p["status"] for p in probes))
    demand(counts==status.get("counts")
           and status.get("usable")==counts.get("USABLE",0)
           and status.get("unusable")==24-counts.get("USABLE",0),
           "INDEPENDENT_PROBE_DENOMINATOR_CHANGED")
    prefixes=status.get("source_prefixes")
    demand(type(prefixes) is list
           and digest(encode(prefixes))==status.get("source_prefix_digest"),
           "ORIGINAL_SOURCE_PREFIX_DIGEST_CHANGED")
    t=status.get("at_ms")
    st=receipt["assessment_started_ms"]
    publish=receipt.get("published_at_ms")
    demand(type(t) is int and type(publish) is int
           and 0<=t-st<=3000 and t<=publish,
           "ORIGINAL_ASOF_OR_PUBLICATION_CLOCK")
    age=status.get("latest_quote_age_ms")
    quote=status.get("latest_source_quote_ms")
    demand(type(age) is int and type(quote) is int and t-quote==age,
           "ORIGINAL_MARKET_QUOTE_AGE_MISMATCH")
    demand(status.get("orders_sent")==0
           and status.get("trading_authority") is False
           and status.get("automatic_trial_registration") is False,
           "ORIGINAL_HEALTH_GAINED_TRADING_AUTHORITY")
    state=status.get("state")
    demand(state in ("HOLD_NEW_FUSION_WINDOW",
                     "READY_FOR_REVIEW_NOT_AUTHORIZATION"),
           "UNRECOGNIZED_ORIGINAL_SOURCE_STATE")
    if state=="READY_FOR_REVIEW_NOT_AUTHORIZATION":
        demand(status.get("reasons")==[]
               and 0<=age<=5000
               and status["usable"]>=20 and status["unusable"]<=4,
               "FALSE_READY_UNDERLYING_IB_SOURCE")
    return {"state":state,"usable":status["usable"],
            "original_asof_ms":t,"status_age_at_published_ms":publish-t,
            "market_quote_age_at_original_assessment_ms":age,
            "source_prefixes_sha256":status["source_prefix_digest"]}

def verify_all(root,*,schema=SCHEMA,cohort=COHORT,count=N,final_max_bytes=15000):
    root=Path(root)
    demand(root.is_absolute() and not root.is_symlink()
           and root.stat().st_uid==os.geteuid()
           and stat.S_IMODE(root.stat().st_mode)==0o700,
           "ARCHIVE_ROOT_NOT_PRIVATE")
    plan,plan_hash=raw_obj(root,"plan.json",15000)
    start,_=raw_obj(root,"worker-start.json",8192)
    sandbox,_=raw_obj(root,"sandbox.json",8192)
    final,final_hash=raw_obj(root,"final.json",final_max_bytes)
    demand(plan.get("schema")==cohort+".plan.v1"
           and plan.get("full_snapshots_count")==count
           and plan.get("original_monitor_rules")==RULE
           and plan.get("candidate_assessment_cadence_ms")==STEP
           and plan.get("maximum_status_age_ms")==MAX_AGE
           and plan.get("cycle_schedule_ms")==[
               plan["first_cycle_start_ms"]+STEP*i for i in range(count)]
           and plan.get("trade_authority") is False
           and plan.get("no_production_monitor_cutover") is True
           and plan.get("broker_requests_or_orders_sent")==0,
           "FROZEN_ARCHIVE_PLAN_CHANGED")
    demand(start.get("plan_sha256")==plan_hash
           and start.get("no_restart") is True
           and start.get("production_monitor_not_modified") is True
           and start.get("broker_orders_sent")==0
           and final.get("schema")==cohort+".final.v1"
           and final.get("plan_sha256")==plan_hash
           and final.get("no_prod_monitor_cutover") is True
           and final.get("production_trading_grant") is False
           and final.get("broker_orders_sent")==0
           and len(final.get("cycles",[]))==count,
           "ORIGINAL_ARCHIVE_PLAN_OR_FINAL_CHANGED")
    previous="0"*64
    collected=[]
    report_summary=[]
    missing=0
    for i,expected in enumerate(plan["cycle_schedule_ms"]):
        row,row_hash=raw_obj(root,"cycle-%02d-receipt.json"%i,16384)
        demand(row.get("schema")==cohort+".cycle.v1"
               and row.get("index")==i and row.get("frozen_target_ms")==expected
               and row.get("previous_receipt_sha256")==previous
               and row.get("original_period_sha256")==plan["period_sha256"]
               and row.get("orders_sent")==0 and row.get("trade_authority") is False,
               "CYCLE_IDENTITY_OR_CHAIN_CHANGED")
        demand(type(row.get("assessment_started_ms")) is int
               and row["assessment_started_ms"]>=expected
               and type(row.get("record_sealed_at_ms")) is int
               and row["record_sealed_at_ms"]>=row["assessment_started_ms"],
               "ASSESSMENT_NOT_AFTER_FROZEN_TARGET")
        demand(final["cycles"][i]=={
            "index":i,"status":row["state"],"receipt_sha256":row_hash,
            "source_ready":row["source_ready"]},
            "TERMINAL_SUMMARY_NOT_BOUND_TO_ORIGINAL_RECEIPT")
        previous=row_hash
        source_file=root/("cycle-%02d-status.json"%i)
        if row["status_sha256"] is None:
            demand(not source_file.exists()
                   and row["publisher_eligible"] is False
                   and row["source_ready"] is False
                   and row["state"] in ("CENSORED_MISSED_FROZEN_CYCLE",
                                        "HOLD_SOURCE_ASSESSMENT_FAILURE"),
                   "MISSING_ORIGINAL_SOURCE_INCONSISTENT")
            missing+=1
            report_summary.append({"cycle":i,"capture":"MISSING_OR_FAILED",
                                   "state":row["state"],"witness_qualified":False})
        else:
            status,status_sha=raw_obj(root,"cycle-%02d-status.json"%i,1048576)
            demand(status_sha==row["status_sha256"],"SNAPSHOT_SHA_NOT_ORIGINAL")
            info=check_status(status,plan["period_sha256"],row)
            demand(row.get("original_asof_ms")==info["original_asof_ms"]
                   and row.get("source_usable_probes")==info["usable"]
                   and row.get("source_prefix_digest")==info["source_prefixes_sha256"]
                   and row.get("source_ready")==(
                       info["state"]=="READY_FOR_REVIEW_NOT_AUTHORIZATION"),
                   "ORIGINAL_SOURCE_SNAPSHOT_METADATA_CHANGED")
            if row["publisher_eligible"]:
                demand(row.get("issues")==[] and row["state"]=="CAPTURED_ORIGINAL_HEALTH",
                       "QUALIFIED_SOURCE_RECORD_CONTRADICTION")
            else:
                demand(row.get("issues") and row["state"]=="HOLD_UNQUALIFIED_SNAPSHOT",
                       "UNQUALIFIED_RECORD_NOT_RETAINED")
            info.update({"cycle":i,"status_sha256":status_sha,
                         "publisher_eligible":row["publisher_eligible"],
                         "clock_lag_ms":row["assessment_started_ms"]-expected})
            collected.append(info)
            report_summary.append(info)
    demand(final["receipt_chain_head"]==previous
           and final["full_snapshots_written"]==len(collected)
           and len(collected)+missing==count,
           "ORIGINAL_24_CYCLES_NOT_RECONCILED")
    # If any source status is missing/failed, timing is not fully proven.
    worst_publish_age=max((r["status_age_at_published_ms"]
                            for r in collected),default=None)
    worst_before_next=None
    if len(collected)==count:
        for first,second in zip(collected,collected[1:]):
            delta=second["status_age_at_published_ms"]+(
                second["original_asof_ms"]-first["original_asof_ms"])
            if worst_before_next is None or delta>worst_before_next:
                worst_before_next=delta
    source_ready=sum(x["state"]=="READY_FOR_REVIEW_NOT_AUTHORIZATION" for x in collected)
    no_late=all(0<=x["clock_lag_ms"]<=3000 for x in collected)
    timing= (len(collected)==count and no_late
             and worst_publish_age is not None and worst_publish_age<=MAX_AGE
             and worst_before_next is not None and worst_before_next<=MAX_AGE)
    return {
        "schema":schema,
        "verified_at_utc":datetime.now(timezone.utc).isoformat(),
        "plan_sha256":plan_hash,
        "final_sha256":final_hash,
        "original_cycle_record_chain_head":previous,
        "cycle_count_expected":count,
        "complete_original_health_json_count":len(collected),
        "missing_or_censored_original_health_count":missing,
        "full_24_probe_snapshots_verified":len(collected)==count,
        "source_ready_cycle_count":source_ready,
        "source_hold_cycle_count":len(collected)-source_ready,
        "worst_local_published_source_age_ms":worst_publish_age,
        "worst_source_age_just_before_next_publication_ms":worst_before_next,
        "all_decision_time_snapshot_ages_within_30s":timing,
        "state":"PASS_FULLBYTE_ORIGINAL_SOURCE_CUSTODY_TIMING_ONLY" if timing
                else "HOLD_INCOMPLETE_OR_STALE_ORIGINAL_SOURCE_SNAPSHOTS",
        "source_100pct_ready_attested":len(collected)==count and source_ready==count,
        "source_signed_by_ib":False,
        "independent_hardware_utc_witness":False,
        "prod_monitor_cadence_switch_approved":False,
        "trade_authority":False,"model_promotion_allowed":False,
        "actual_execution_net_profit_attested":False,
        "broker_queries_or_orders_sent":0,
        "per_cycle":report_summary,
    }

def persist(result,output):
    output=Path(output)
    demand(output.is_absolute() and not output.exists() and not output.is_symlink(),
           "AUDIT_OUTPUT_MUST_BE_NEW")
    root=output.parent
    if not root.exists():root.mkdir(parents=True,mode=0o700)
    demand(root.is_dir() and not root.is_symlink()
           and root.stat().st_uid==os.geteuid()
           and stat.S_IMODE(root.stat().st_mode)==0o700,
           "AUDIT_OUTPUT_ROOT_NOT_PRIVATE")
    raw=encode(result)+b"\n"
    fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    dirfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)
    return digest(raw)

def main(verify_fn=None):
    verify_fn=verify_all if verify_fn is None else verify_fn
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    r=verify_fn(args.root)
    output_sha=persist(r,args.output)
    print(json.dumps({
        "state":r["state"],"complete":r["complete_original_health_json_count"],
        "source_ready":r["source_ready_cycle_count"],
        "source_hold":r["source_hold_cycle_count"],
        "worst_before_next_ms":r["worst_source_age_just_before_next_publication_ms"],
        "report_sha256":output_sha,"broker_orders_sent":0
    },sort_keys=True))
if __name__=="__main__":main()
