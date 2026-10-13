"""Immutable ex-post cause matrix for four already frozen PAPER quote windows.

This does NOT re-admit any old CENSORED event. The first window has no
at-decision monitor bytes, so its exact health cause remains unprovable.
Three later windows do retain whole health snapshots; a READY stale monitor
is NOT evidence that the actual live broker quote was stale at decision.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import stat

from .fx_original_readiness_custody import (
    CustodyHold, ORIGINAL_PLAN_SHA, ORIGINAL_SEAL_SHA,
    RULE,SCHEMA as HEALTH_SCHEMA,
    canonical, digest,read_exact,strict,evaluate_health,private_dir,write_once,
)

SCHEMA="heptatrader.fx-forward-original-health-causal-asof-matrix.v1"
PRIOR_CUSTODY_SHA="9bdb268359c2befeecb66ff11cddf45161f72543678875ed86428cb3a6420737"
N=4
PRICE_HORIZONS=(15,30,60,120)

class AuditHold(ValueError):pass
def require(ok,reason):
    if not ok:raise AuditHold(reason)

def file_doc(p,max_len=256*1024):
    raw=read_exact(p,max_len)
    return strict(raw),digest(raw)

def classify(health,attempt,decision,original_anchor,period,raw_hash):
    """Classification is a lossless diagnostic, never an approval."""
    require(attempt.get("schema")==HEALTH_SCHEMA+".snapshot-attempt.v1"
            and attempt.get("anchor_ms")==original_anchor
            and type(attempt.get("attempt_started_at_ms")) is int
            and original_anchor+1000<=attempt["attempt_started_at_ms"]<=original_anchor+4500
            and attempt.get("window") in (1,2,3),
            "CUSTODY_ATTEMPT_NOT_IN_ORIGINAL_SLOT")
    require(attempt.get("source_health_status_sha256")==raw_hash
            and attempt.get("source_health_status_bytes")>0
            and attempt.get("readiness_health_at_ms")==health.get("at_ms")
            and attempt.get("recomputed_source_usable_probes")==health.get("usable"),
            "RAW_CUSTODY_BYTES_OR_SUMMARY_CHANGED")
    require(attempt.get("state") in ("HOLD","CORROBORATING_SOURCE_READY_NO_TRADE")
            and attempt.get("original_study_entry_readiness_proven") is False
            and attempt.get("broker_orders_sent")==0
            and attempt.get("trade_authority") is False,
            "CUSTODY_CANNOT_PROMOTE_ORIGINAL_RESEARCH")
    require(decision.get("schema")=="heptatrader.fx-original-future-price-cost-calendar.v1.attempt.v1"
            and decision.get("stage")=="decision"
            and decision.get("state")=="CENSORED"
            and decision.get("reason_code")=="EXCEPTION_Hold_ORIGINAL_READINESS_HOLD_OR_STALE"
            and decision.get("broker_orders_sent")==0
            and decision.get("automatic_retry_allowed") is False
            and original_anchor+1000<=decision["attempt_recorded_at_ms"]<=original_anchor+4500,
            "ORIGINAL_ENTRY_REJECT_CHANGED")
    expected_health_issues=evaluate_health(health,{"period_sha256":period},
                                         attempt["attempt_started_at_ms"])
    require(sorted(set(expected_health_issues))==attempt.get("reasons"),
            "HEALTH_RAW_RECOMPUTATION_DISAGREES_WITH_SEALED_RECEIPT")
    age=attempt["attempt_started_at_ms"]-health["at_ms"]
    require(age>0,"MONITOR_ASOF_AFTER_RESEARCH_DECISION")
    source_latest_at_asof=health.get("latest_quote_age_ms")
    status=health.get("state")
    category=(
        "SOURCE_MONITOR_SNAPSHOT_TOO_OLD__NOT_PROOF_OF_LIVE_QUOTE_STALENESS"
        if expected_health_issues==["READINESS_SNAPSHOT_STALE_OR_FUTURE"]
           and status=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
           and type(source_latest_at_asof) is int and 0<=source_latest_at_asof<=5000
        else "MORE_THAN_MONITOR_AGE_FAILED__DO_NOT_RECLASSIFY")
    return {
        "window":attempt["window"],
        "anchor_ms":original_anchor,
        "original_decision_state":"CENSORED",
        "original_decision_reason":decision["reason_code"],
        "original_decision_sha256":None,
        "custody_snapshot_sha256":raw_hash,
        "source_monitor_asof_ms":health["at_ms"],
        "source_monitor_age_at_original_slot_ms":age,
        "last_quote_age_at_monitor_asof_ms":source_latest_at_asof,
        "original_monitor_reported_ready":status=="READY_FOR_REVIEW_NOT_AUTHORIZATION",
        "monitor_usable_probe_count":health["usable"],
        "recomputed_health_errors":expected_health_issues,
        "cause_class":category,
        "original_broker_quote_freshness_at_slot_attested":False,
        "future_model_or_economic_admission":False,
        "no_retroactive_relabel":True,
    }

def audit(original_root,custody_root,output):
    original_root,custody_root=private_dir(original_root),private_dir(custody_root)
    plan,p_sha=file_doc(original_root/"plan.json")
    seal,s_sha=file_doc(original_root/"sealed.json")
    custody,c_sha=file_doc(custody_root/"witness-plan.json")
    require(p_sha==ORIGINAL_PLAN_SHA and s_sha==ORIGINAL_SEAL_SHA
            and c_sha==PRIOR_CUSTODY_SHA
            and plan.get("schema")=="heptatrader.fx-original-future-price-cost-calendar.v1"
            and plan.get("trade_authority") is False
            and plan.get("automatic_training") is False
            and plan.get("behavior_policy")=="NO_TRADE"
            and custody.get("schema")==HEALTH_SCHEMA+".plan.v1"
            and custody.get("original_plan_sha256")==p_sha
            and custody.get("original_seal_sha256")==s_sha
            and len(plan["anchors_ms"])==N
            and len(seal["windows"])==N,
            "EXACT_ORIGINAL_AND_CUSTODY_PLAN_NOT_PINNED")
    require(custody.get("planned_capture_windows")==[
        {"index":i,"anchor_ms":plan["anchors_ms"][i],
         "capture_first_ms":plan["anchors_ms"][i]+1000,
         "capture_last_ms":plan["anchors_ms"][i]+4500}
        for i in (1,2,3)],
        "RETROACTIVE_CAPTURE_TIME_CHANGED")
    old=original_root/"window-00"
    old_attempt,old_sha=file_doc(old/"decision-attempt.json")
    require(old_attempt.get("state")=="CENSORED"
            and old_attempt.get("reason_code")=="EXCEPTION_Hold_ORIGINAL_READINESS_HOLD_OR_STALE"
            and not (old/"preaction.json").exists()
            and not (custody_root/"window-00-health-original.json").exists(),
            "PAST_UNATTESTED_WINDOW_REWRITTEN")
    summaries=[]
    for i in (1,2,3):
        p=original_root/("window-%02d"%i)
        d,decision_sha=file_doc(p/"decision-attempt.json")
        snap,snap_sha=file_doc(custody_root/("window-%02d-health-original.json"%i))
        attempt,receipt_sha=file_doc(custody_root/("window-%02d-health-attempt.json"%i))
        require(attempt.get("source_health_status_bytes")==len(read_exact(
                    custody_root/("window-%02d-health-original.json"%i))),
                "ORIGINAL_HEALTH_BYTE_LENGTH_CHANGED")
        row=classify(snap,attempt,d,plan["anchors_ms"][i],
                     plan["period_sha256"],snap_sha)
        require(row["window"]==i,"CUSTODY_WINDOW_INDEX_SWAP")
        row.update(original_decision_sha256=decision_sha,
                   custody_attempt_sha256=receipt_sha)
        require(not (p/"preaction.json").exists()
                and not (p/"maturity-015.json").exists(),
                "REJECTED_WINDOW_HAS_PRICE_OR_PREDICTION")
        summaries.append(row)
    assert len(summaries)==3
    require(all(x["cause_class"]==
                "SOURCE_MONITOR_SNAPSHOT_TOO_OLD__NOT_PROOF_OF_LIVE_QUOTE_STALENESS"
                for x in summaries),
            "STORED_THREE_MONITOR_FAILURES_DIFFER_FROM_ORIGINAL")
    report={
        "schema":SCHEMA,
        "audited_at_utc":datetime.now(timezone.utc).isoformat(),
        "auditor_code_sha256":digest(Path(__file__).read_bytes()),
        "original_plan_sha256":p_sha,
        "original_seal_sha256":s_sha,
        "original_custody_plan_sha256":c_sha,
        "original_window_zero_attempt_sha256":old_sha,
        "past_window_zero_at_decision_health_raw_was_not_preserved":True,
        "independently_retained_future_health_rows":3,
        "confirmed_monitor_snapshot_stale_slots":3,
        "rows":summaries,
        "source_continuity_at_exact_original_decision_proven":False,
        "broker_signed_original_market_data_proven":False,
        "production_native_owner_witness_or_ledger_ack":False,
        "previously_censored_quotes_or_labels_reenabled":False,
        "not_a_new_prospective_prediction_or_trading_result":True,
        "broker_queries_or_orders_sent":0,"trade_authority":False,
        "training_or_promotion_allowed":False,
    }
    output=Path(output)
    require(output.is_absolute() and not output.is_symlink() and not output.exists(),
            "OUTPUT_MUST_BE_NEW")
    private_dir(output.parent,create=False)
    write_once(output,canonical(report)+b"\n")
    return report

def main():
    p=argparse.ArgumentParser()
    for name in ("original-root","custody-root","output"):
        p.add_argument("--"+name,required=True,type=Path)
    a=p.parse_args()
    r=audit(a.original_root,a.custody_root,a.output)
    print(json.dumps({
        "state":"THREE_FROZEN_SOURCE_MONITOR_AGE_FAILURES_RECONCILED",
        "stale_slots":r["confirmed_monitor_snapshot_stale_slots"],
        "source_freshness_at_decision_proven":False,
        "model_admission":False,"orders_sent":0
    },sort_keys=True),flush=True)

if __name__=="__main__":main()
