"""Independent off-host finite 4x4 original FX PAPER research closeout.

Runs only on *previously retained* qian-qi source copies. Earlier 3x4
frozen-window and 10:05 exact-cache validators are reused as strict, SHA-pinned
independent contracts; no broker, replay synthesis, strategy or native Witness.

Prior to the final 120m deadline, reports PARTIAL, never acceptance.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json, time
from pathlib import Path

from .fx_first_three_original_crosshost import (
    PLAN_SHA, SEAL_SHA, HORIZONS, PERIOD, TRIAL, DECISION_REJECT, MATURITY_REJECT,
    CrosshostHold, demand, evidence, private_dir, read_file, sha, write_report,
    verify as verify_first_three,
)
from .fx_1005_cache_crosshost import verify as verify_1005

SCHEMA="heptatrader.fx-original-allfour-physical-independent-closeout.v1"
ANCHOR_10_05=1791857100000
BEFORE_LAST_DUE_MS=ANCHOR_10_05+120*60000+15000

def exact_maturity(row,index,horizon,anchor):
    need_type=type(row.get("attempt_recorded_at_ms")) is int
    due=anchor+horizon*60000
    demand(row.get("schema")==TRIAL+".attempt.v1"
           and row.get("stage")=="maturity-%03d"%horizon
           and row.get("state")=="CENSORED"
           and row.get("reason_code")==MATURITY_REJECT
           and need_type and due+6500<=row["attempt_recorded_at_ms"]<=due+15000
           and row.get("automatic_retry_allowed") is False
           and row.get("historical_price_or_future_quote_backfilled") is False
           and row.get("broker_orders_sent")==0
           and row.get("broker_queries_sent")==0
           and row.get("trade_authority") is False
           and row.get("model_promotion_allowed") is False,
           "FOURTH_MATURITY_RELABELED_OR_NOT_ORIGINAL")
    return {"horizon_minutes":horizon,"state":"CENSORED",
            "reason_code":MATURITY_REJECT}

def verify_4(*,firstthree_root,cache1005_root,fourth_root,
             audit_now_ms=None,require_complete=False):
    first=verify_first_three(firstthree_root)
    cached=verify_1005(cache1005_root)
    demand(first["original_decisions_censored"]==3
           and first["original_maturity_attempts_censored"]==12
           and first["original_plan_sha256"]==PLAN_SHA
           and first["original_seal_sha256"]==SEAL_SHA
           and first["window_zero_full_health_snapshot_unattested"] is True
           and first["later_ready_health_snapshots_were_stale_at_original_decisions"] is True
           and first["price_only_matured_labels"]==0,
           "FIRST_THREE_INDEPENDENT_CLOSURE_CHANGED")
    demand(cached["original_entry_state"]=="CENSORED"
           and cached["original_entry_health_age_ms"]==85031
           and cached["source_health_exact_time_comparison"]["baseline"]["age_ms"]==86092
           and cached["source_health_exact_time_comparison"]["canary"]["age_ms"]==18000
           and cached["broker_queries_or_orders_sent_by_auditor"]==0
           and cached["production_sla_rollout_authorized"] is False,
           "ORIGINAL_1005_VERSUS_SANDBOX_CACHE_RESULT_CHANGED")
    fourth_root=private_dir(fourth_root)
    original,osum=evidence(Path(cache1005_root)/"original-entry-attempt.json")
    decision,dsha=evidence(fourth_root/"decision-attempt.json")
    demand(dsha==osum
           and decision.get("schema")==TRIAL+".attempt.v1"
           and decision.get("stage")=="decision"
           and decision.get("state")=="CENSORED"
           and decision.get("reason_code")==DECISION_REJECT
           and type(decision.get("attempt_recorded_at_ms")) is int
           and ANCHOR_10_05+1000<=decision["attempt_recorded_at_ms"]<=ANCHOR_10_05+4500
           and decision.get("broker_orders_sent")==0
           and decision.get("trade_authority") is False
           and decision.get("automatic_retry_allowed") is False
           and decision.get("model_promotion_allowed") is False
           and decision.get("historical_price_or_future_quote_backfilled") is False,
           "FOURTH_ORIGINAL_ENTRY_CHANGED_OR_NOT_CENSORED")
    demand(not (fourth_root/"preaction.json").exists(),
           "CENSORED_FOURTH_ENTRY_CANNOT_HAVE_PREACTION")
    now=int(time.time()*1000) if audit_now_ms is None else audit_now_ms
    demand(type(now) is int and now>=decision["attempt_recorded_at_ms"],
           "AUDIT_CLOCK_NOT_CAUSAL")
    results=[]
    pending=[]
    for horizon in HORIZONS:
        path=fourth_root/("maturity-%03d-attempt.json"%horizon)
        price=fourth_root/("maturity-%03d.json"%horizon)
        demand(not price.exists() and not price.is_symlink(),
               "CENSORED_FOURTH_MATURITY_HAS_PRICE_LABEL")
        if not path.is_file():
            due=ANCHOR_10_05+horizon*60000+15000
            if require_complete or now>=due:
                raise CrosshostHold("MISSING_FOURTH_MATURITY_AFTER_ORIGINAL_DEADLINE")
            pending.append(horizon)
            continue
        record,rs=evidence(path)
        r=exact_maturity(record,3,horizon,ANCHOR_10_05)
        r["original_maturity_receipt_sha256"]=rs
        results.append(r)
    if require_complete:
        demand(not pending and len(results)==len(HORIZONS)
               and now>=BEFORE_LAST_DUE_MS,
               "FULL_FOUR_WINDOW_AUDIT_PREMATURE")
    completed=len(results)==len(HORIZONS)
    return {
        "schema":SCHEMA,
        "verified_at_utc":datetime.now(timezone.utc).isoformat(),
        "result":("COMPLETE_4X4_CENSORED_RESEARCH_PROVEN_NO_TRADE"
                  if completed else "PARTIAL_AWAIT_ORIGINAL_FUTURE_MATURITIES"),
        "result_is_production_release":False,
        "independent_host_archive_is_not_broker_signed":True,
        "all_original_decision_windows":4,
        "original_decision_censored_count":4,
        "original_complete_matured_censored_count":12+len(results),
        "original_expected_maturity_count":16,
        "fourth_future_horizons_pending":pending,
        "zero_original_eligible_price_labels":True,
        "crosshost_pretrial_copy_before_first_window":True,
        "first_three_source_evidence_replay_sha256":[
            z["original_decision_sha256"] for z in first["first_three"]],
        "original_plan_sha256":PLAN_SHA,
        "original_seal_sha256":SEAL_SHA,
        "original_1005_decision_sha256":dsha,
        "original_1005_uncorrected_readiness_age_ms":
            cached["original_entry_health_age_ms"],
        "cached_canary_1005_timing_was_only_counterfactual":True,
        "original_1005_detailed_maturities":results,
        "price_labels_proven":0,"actual_execution_pnl_proven":False,
        "production_owner_or_witness_enrolled":False,
        "all_original_failures_preserved":True,
        "broker_queries_or_orders_sent_by_auditor":0,
        "trade_authority":False,"model_promotion_allowed":False,
    }

def main():
    p=argparse.ArgumentParser()
    for x in ("firstthree","cache1005","fourth","output"):
        p.add_argument("--"+x,required=True,type=Path)
    p.add_argument("--require-complete",action="store_true")
    a=p.parse_args()
    r=verify_4(firstthree_root=a.firstthree,cache1005_root=a.cache1005,
       fourth_root=a.fourth,require_complete=a.require_complete)
    write_report(a.output,r)
    print(json.dumps({"result":r["result"],
      "maturity_censored":r["original_complete_matured_censored_count"],
      "expected":16,"pending":r["fourth_future_horizons_pending"],
      "price_labels":0,"trade_authority":False},sort_keys=True))
if __name__=="__main__":main()
