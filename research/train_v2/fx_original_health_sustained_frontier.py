"""Independent read-only contiguous-prefix audit of an IN-PROGRESS 480-cycle source archive.

This does not change the sealed producer, schedule, old censored windows,
risk, trading, user monitor service, or production permissions. Verification
of an early prefix is NOT an overall 480-cycle acceptance claim.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from .fx_original_health_sustained_audit import (
    COHORT, N, STEP, MAX_AGE, RULE, check_status, digest, encode, raw_obj,
    demand, original_bytes, strict, persist,
)

SCHEMA="heptatrader.fx-original-sustained-contiguous-prefix-audit.v1"
PREFIX_COUNT=32
ROOT_SEQ=re.compile(r"cycle-(\d\d)-receipt\.json\Z")
SOURCE_SEQ=re.compile(r"cycle-(\d\d)-status\.json\Z")

def verify_prefix(root, count=PREFIX_COUNT):
    root=Path(root)
    demand(root.is_absolute() and not root.is_symlink()
           and root.stat().st_uid==os.geteuid()
           and stat.S_IMODE(root.stat().st_mode)==0o700,
           "PRIVATE_ROOT_INVALID")
    demand(type(count) is int and 1<=count<N,
           "INCOMPLETE_PREFIX_COUNT_REQUIRED")
    plan,plan_hash=raw_obj(root,"plan.json",65536)
    start,start_hash=raw_obj(root,"worker-start.json",8192)
    sandbox,sandbox_hash=raw_obj(root,"sandbox.json",8192)
    demand(plan.get("schema")==COHORT+".plan.v1"
           and plan.get("full_snapshots_count")==N
           and plan.get("original_monitor_rules")==RULE
           and plan.get("candidate_assessment_cadence_ms")==STEP
           and plan.get("maximum_status_age_ms")==MAX_AGE
           and plan.get("maximum_schedule_delay_ms")==3000
           and plan.get("cycle_schedule_ms")==[
              plan["first_cycle_start_ms"]+STEP*i for i in range(N)]
           and plan.get("no_training_or_trading") is True
           and plan.get("no_production_monitor_cutover") is True
           and plan.get("trade_authority") is False
           and plan.get("broker_requests_or_orders_sent")==0,
           "PRESEALED_PRODUCTION_SCOPE_CHANGED")
    demand(start.get("schema")==COHORT+".worker.v1"
           and start.get("plan_sha256")==plan_hash
           and start.get("no_restart") is True
           and start.get("production_monitor_not_modified") is True
           and start.get("broker_orders_sent")==0
           and start.get("start_ms")<plan["first_cycle_start_ms"],
           "FROZEN_WORKER_IDENTITY_CHANGED")
    demand(type(sandbox) is dict
           and all(sandbox.get(k) is True for k in (
               "landlock_enforced","seccomp_enforced",
               "socket_creation_denied","bearer_read_denied",
               "only_output_writable","output_write_verified")),
           "SOURCE_SANDBOX_CONTROLS_NOT_ATTESTED")
    demand(not (root/"final.json").exists(),
           "FINAL_ALREADY_EXISTS_USE_FULL_INDEPENDENT_VERIFIER")
    previous="0"*64
    sample=[]
    for i in range(count):
        rec,rec_sha=raw_obj(root,"cycle-%02d-receipt.json"%i,16384)
        target=plan["cycle_schedule_ms"][i]
        demand(rec.get("schema")==COHORT+".cycle.v1"
               and rec.get("index")==i
               and rec.get("frozen_target_ms")==target
               and rec.get("previous_receipt_sha256")==previous
               and rec.get("original_period_sha256")==plan["period_sha256"]
               and rec.get("orders_sent")==0
               and rec.get("trade_authority") is False,
               "HISTORICAL_CYCLE_ORDER_OR_HASH_CHAIN_CHANGED")
        previous=rec_sha
        started=rec.get("assessment_started_ms")
        sealed=rec.get("record_sealed_at_ms")
        demand(type(started) is int and type(sealed) is int
               and target<=started<=sealed
               and started<=target+3000,
               "HISTORICAL_CYCLE_SCHEDULE_OR_PROCESS_DELAY")
        if rec.get("status_sha256") is None:
            demand(rec.get("state") in (
                "CENSORED_MISSED_FROZEN_CYCLE",
                "HOLD_SOURCE_ASSESSMENT_FAILURE")
                and rec.get("publisher_eligible") is False
                and rec.get("source_ready") is False
                and not (root/("cycle-%02d-status.json"%i)).exists(),
                "UNSAFE_MISSING_STATUS_RELABELED")
            sample.append({"index":i,"state":rec["state"],
                           "receipt_sha256":rec_sha,
                           "capture":"CENSORED"})
            continue
        s,s_sha=raw_obj(root,"cycle-%02d-status.json"%i,1048576)
        demand(s_sha==rec["status_sha256"],
               "ORIGINAL_SOURCE_BYTES_CHANGED")
        info=check_status(s,plan["period_sha256"],rec)
        demand(rec.get("original_asof_ms")==info["original_asof_ms"]
               and rec.get("source_usable_probes")==info["usable"]
               and rec.get("source_prefix_digest")==info["source_prefixes_sha256"]
               and rec.get("source_ready")==(
                   info["state"]=="READY_FOR_REVIEW_NOT_AUTHORIZATION"),
               "ORIGINAL_SOURCE_METADATA_CHANGED")
        late=rec["assessment_started_ms"]-target
        age=info["status_age_at_published_ms"]
        demand(age>=0, "MONITOR_PUBLISHED_BEFORE_ASOF")
        eligible=bool(rec.get("publisher_eligible"))
        if eligible:
            demand(rec.get("issues")==[] and
                   rec.get("state")=="CAPTURED_ORIGINAL_HEALTH"
                   and age<=MAX_AGE,"FALSE_ELIGIBLE_SOURCE_PUBLICATION")
        else:
            demand(rec.get("issues") and
                   rec.get("state")=="HOLD_UNQUALIFIED_SNAPSHOT",
                   "HOLD_SOURCE_ISSUES_CHANGED")
        sample.append({
            "index":i,"state":rec["state"],"source_ready":rec["source_ready"],
            "usable":info["usable"],"receipt_sha256":rec_sha,
            "status_sha256":s_sha,"published_age_ms":age,
            "asof_ms":info["original_asof_ms"],
            "published_at_ms":rec["published_at_ms"],
            "assessment_started_ms":started,"clock_lag_ms":late,
            "publisher_eligible":eligible,
            "source_status":info["state"]
        })
    # All qualifying source observations must be as originally observed;
    # output may be aggregated but cannot be upgraded to a production pass.
    valid=[s for s in sample if "asof_ms" in s]
    ready=sum(s.get("source_ready") is True for s in valid)
    hold=sum(s.get("source_ready") is False for s in valid)
    max_age=max((s["published_age_ms"] for s in valid),default=None)
    max_lag=max((s["clock_lag_ms"] for s in sample if "clock_lag_ms" in s),default=None)
    next_pub=max(
        (b["published_at_ms"]-a["asof_ms"] for a,b in zip(sample,sample[1:])
         if "asof_ms" in a and "asof_ms" in b),
        default=None
    )
    return {
        "schema":SCHEMA,
        "verified_at_utc":datetime.now(timezone.utc).isoformat(),
        "original_plan_sha256":plan_hash,
        "original_worker_start_sha256":start_hash,
        "original_sandbox_sha256":sandbox_hash,
        "frozen_expected_cycles":N,
        "contiguous_prefix_cycles_checked":count,
        "future_cycles_NOT_verified":N-count,
        "prefix_original_receipt_hash_chain_head":previous,
        "origin_period_sha256":plan["period_sha256"],
        "captured_source_statuses_verified":len(valid),
        "censored_source_cycles":count-len(valid),
        "source_ready_snapshots":ready,
        "source_hold_snapshots":hold,
        "worst_published_original_source_age_ms":max_age,
        "worst_status_age_at_next_publication_ms":next_pub,
        "worst_assessment_start_delay_ms":max_lag,
        "snapshot_age_30s_limit_ms":MAX_AGE,
        "cohort_complete":False,
        "production_monitor_cadence_qualified":False,
        "model_promotion_allowed":False,
        "market_quote_signed_by_ib":False,
        "independent_hardware_timestamp_witness":False,
        "actual_execution_after_cost_pnl_attested":False,
        "broker_queries_or_orders_sent":0,
        "trade_authority":False,
        "per_cycle":sample,
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--count",type=int,default=PREFIX_COUNT)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    out=verify_prefix(args.root,args.count)
    h=persist(out,args.output)
    print(json.dumps({
        "state":"INDEPENDENT_PREFIX_VERIFIED_NOT_FULL_480",
        "prefix":out["contiguous_prefix_cycles_checked"],
        "target":N,
        "ready":out["source_ready_snapshots"],
        "hold":out["source_hold_snapshots"],
        "max_publish_age_ms":out["worst_published_original_source_age_ms"],
        "worst_next_publication_age_ms":
            out["worst_status_age_at_next_publication_ms"],
        "output_sha256":h,"broker_orders_sent":0
    },sort_keys=True))
if __name__=="__main__":main()
