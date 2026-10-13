"""Off-host audit of immutable original vs cached 10:05 FX source health.

No broker, trading, product Owner, model fit or quote-substitution capability.
Original and cache statuses are distinct observations at their own as-of times.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from collections import Counter
import json,os
from pathlib import Path
from .fx_first_three_original_crosshost import (
    strict,sha,canonical,private_dir,read_file,write_report, CrosshostHold,demand,
    PLAN_SHA,SEAL_SHA,PERIOD,RULE
)
SCHEMA="heptatrader.fx-1005-crosshost-original-vs-cache.v1"
CANARY_SHA="10fea7101627dde050a0b6f6ec1cfa93d7dc746ad0fc57c13b4b7224f7659943"
PRETRIAL_SHA="1cf6f67ee369650ccc2a8880f3bb2b8203666a370431cffd5a8896d1cb32560e"
ANCHOR=1791857100000
WINDOW_START=ANCHOR+1000
WINDOW_END=ANCHOR+4500
SOURCE="heptatrader.fx-original-health-cache-fixedslot-shadow.v1"
CUSTODY="heptatrader.fx-forward-original-health-custody.v1"
RESEARCH="heptatrader.fx-original-future-price-cost-calendar.v1"
STATUS_SET={"USABLE","PATH_GAP","ENDPOINT_GAP","INPUT_UNAVAILABLE","OUTCOME_UNAVAILABLE"}

def load(root,name):
    raw=read_file(Path(root)/name,262144)
    return strict(raw),sha(raw)

def source_status(status,at):
    demand(status.get("schema")=="heptatrader.quote-readiness-status.v1"
           and status.get("period_sha256")==PERIOD
           and status.get("rule")==RULE
           and status.get("automatic_trial_registration") is False
           and status.get("trading_authority") is False
           and status.get("orders_sent")==0,
           "SOURCE_MONITOR_SCOPE_OR_AUTHORITY_CHANGED")
    probes=status.get("probes")
    demand(type(probes) is list and len(probes)==24 and
           all(type(p) is dict and p.get("status") in STATUS_SET for p in probes),
           "READINESS_PROBE_POPULATION_CHANGED")
    counts=dict(Counter(p["status"] for p in probes))
    demand(status.get("counts")==counts
           and status.get("usable")==counts.get("USABLE",0)
           and status.get("unusable")==24-counts.get("USABLE",0),
           "READINESS_PROBE_COUNTS_CHANGED")
    prefixes=status.get("source_prefixes")
    demand(type(prefixes) is list and
           status.get("source_prefix_digest")==sha(canonical(prefixes)),
           "SOURCE_PROVENANCE_DIGEST_CHANGED")
    stamp=status.get("at_ms")
    source_at=status.get("latest_source_quote_ms")
    quote_age=status.get("latest_quote_age_ms")
    demand(type(stamp) is int and type(source_at) is int
           and type(quote_age) is int
           and stamp-source_at==quote_age
           and 0<=quote_age<=5000,
           "MONITOR_QUOTE_TIMESTAMP_CHANGED")
    age=at-stamp
    demand(0<=age<300000,"MONITOR_STATE_NONCAUSAL_OR_TOO_OLD")
    ready=(age<=30000
           and status.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
           and status.get("reasons")==[]
           and counts.get("USABLE",0)>=20
           and 24-counts.get("USABLE",0)<=4)
    return {"age_ms":age,"state":status["state"],"usable":status["usable"],
            "missing":status["unusable"],"fresh_status":age<=30000,
            "healthy_under_frozen_monitor_rules":ready,
            "quote_event_age_at_assessment_ms":quote_age,
            "independently_broker_signed":False}

def verify(root):
    root=private_dir(root)
    orig,p_sha=load(root,"original-plan-pretrial.json")
    seal,s_sha=load(root,"original-seal-pretrial.json")
    plan,c_sha=load(root,"canary-plan-pretrial.json")
    pre,pr_sha=load(root,"canary-physical-precommit.json")
    demand((p_sha,s_sha,c_sha,pr_sha)==
           (PLAN_SHA,SEAL_SHA,CANARY_SHA,PRETRIAL_SHA),
           "PRETRIAL_PHYSICAL_HOST_PIN_CHANGED")
    demand(orig.get("period_sha256")==PERIOD and
           orig.get("trade_authority") is False
           and orig.get("anchors_ms",[])[3]==ANCHOR
           and seal.get("plan_sha256")==PLAN_SHA
           and plan.get("original_plan_sha256")==PLAN_SHA
           and plan.get("original_seal_sha256")==SEAL_SHA
           and plan.get("fixed_anchor_ms")==ANCHOR
           and plan.get("capture_exact_target_ms")==ANCHOR+2000
           and plan.get("capture_permitted_window_ms")==[WINDOW_START,WINDOW_END]
           and plan.get("cache_enabled_under_sandbox_only") is True
           and plan.get("source_rules_not_relaxed") is True
           and plan.get("original_trial_not_changed") is True
           and plan.get("only_compare_source_health_not_execute") is True
           and plan.get("broker_orders_sent")==0
           and plan.get("trade_authority") is False,
           "REGISTERED_TWO_MONITOR_PLAN_CHANGED")
    t=datetime.fromisoformat(pre["readback_utc"])
    demand(t.tzinfo is not None and int(t.timestamp()*1000)<plan["canary_start_ms"]
           and pre.get("verified_before_canary_worker_start") is True
           and pre.get("canary_plan_sha256")==CANARY_SHA
           and pre.get("original_pretrial_plan_sha256")==PLAN_SHA
           and pre.get("independent_ib_market_data_received") is False
           and pre.get("trade_authority") is False,
           "PHYSICAL_CANARY_PLAN_NOT_PRECOMMITTED")
    comparison,comparison_sha=load(root,"comparison-original.json")
    capture=comparison.get("attempt_at_ms")
    durable=comparison.get("comparison_durable_at_ms")
    demand(comparison.get("schema")==SOURCE+".comparison.v1"
           and comparison.get("state")=="TWO_READONLY_SOURCE_HEALTH_SNAPSHOTS_PRESERVED"
           and type(capture) is int and WINDOW_START<=capture<=WINDOW_END
           and type(durable) is int and capture<=durable<=WINDOW_END
           and comparison.get("canary_precommit_sha256")==CANARY_SHA
           and comparison.get("original_plan_sha256")==PLAN_SHA
           and comparison.get("original_seal_sha256")==SEAL_SHA
           and comparison.get("canary_is_shadow_not_original_study_input") is True
           and comparison.get("original_trade_entry_eligibility_not_reclassified") is True
           and comparison.get("trade_authority") is False
           and comparison.get("broker_orders_sent")==0,
           "FROZEN_COMPARISON_CHANGED")
    original_decision,decision_sha=load(root,"original-entry-attempt.json")
    oldtime=original_decision.get("attempt_recorded_at_ms")
    demand(original_decision.get("schema")==RESEARCH+".attempt.v1"
           and original_decision.get("stage")=="decision"
           and original_decision.get("state")=="CENSORED"
           and original_decision.get("reason_code")==
               "EXCEPTION_Hold_ORIGINAL_READINESS_HOLD_OR_STALE"
           and type(oldtime) is int and WINDOW_START<=oldtime<=WINDOW_END
           and original_decision.get("broker_orders_sent")==0
           and original_decision.get("automatic_retry_allowed") is False
           and original_decision.get("model_promotion_allowed") is False,
           "ORIGINAL_RESEARCH_ENTRY_RELABELED")
    health_attempt,health_attempt_sha=load(root,"original-health-custody-attempt.json")
    original_health,original_health_sha=load(root,"original-health-custody-raw.json")
    baseline,baseline_sha=load(root,"baseline-health-original.json")
    canary,canary_sha=load(root,"canary-health-original.json")
    demand(original_health_sha==baseline_sha
           and health_attempt.get("schema")==CUSTODY+".snapshot-attempt.v1"
           and health_attempt.get("window")==3
           and health_attempt.get("state")=="HOLD"
           and health_attempt.get("reasons")==["READINESS_SNAPSHOT_STALE_OR_FUTURE"]
           and health_attempt.get("source_health_status_sha256")==baseline_sha
           and health_attempt.get("readiness_health_at_ms")==baseline.get("at_ms")
           and health_attempt.get("original_study_entry_readiness_proven") is False
           and health_attempt.get("broker_orders_sent")==0,
           "ORIGINAL_HEALTH_CUSTODY_NOT_MATCHING_COMPARISON")
    out={}
    for role,status,digest in (("baseline",baseline,baseline_sha),
                               ("canary",canary,canary_sha)):
        derived=source_status(status,capture)
        expected=comparison[role]
        demand(expected.get("captured_sha256")==digest and
               expected.get("source_producer_at_ms")==status["at_ms"]
               and expected.get("age_at_comparison_ms")==derived["age_ms"]
               and expected.get("source_probes_usable")==derived["usable"]
               and expected.get("source_state")==derived["state"]
               and expected.get("healthy_at_exact_time")==
                    derived["healthy_under_frozen_monitor_rules"],
               "FUTURE_HEALTH_REPORTED_VS_RECOMPUTED_MISMATCH")
        out[role]={**derived,"source_sha256":digest}
    demand(out["baseline"]["usable"]==24 and out["canary"]["usable"]==24
           and out["baseline"]["age_ms"]==86092
           and out["canary"]["age_ms"]==18000
           and not out["baseline"]["healthy_under_frozen_monitor_rules"]
           and out["canary"]["healthy_under_frozen_monitor_rules"]
           and comparison["baseline"]["reasons"]==
                 ["STATUS_SNAPSHOT_NOT_CAUSALLY_FRESH"]
           and comparison["canary"]["reasons"]==[],
           "FIXED_1005_VERDICT_NOT_REPRODUCED")
    demand(oldtime-baseline["at_ms"]>30000,
           "ORIGINAL_ENTRY_HEALTH_WAS_NOT_STALE")
    return {
        "schema":SCHEMA,
        "verified_at_utc":datetime.now(timezone.utc).isoformat(),
        "qianqi_pretrial_canary_plan_sha256":c_sha,
        "qianqi_pretrial_original_plan_sha256":p_sha,
        "qianqi_canary_receipt_sha256":pr_sha,
        "original_comparison_sha256":comparison_sha,
        "original_entry_decision_sha256":decision_sha,
        "original_health_custody_attempt_sha256":health_attempt_sha,
        "original_entry_health_age_ms":oldtime-baseline["at_ms"],
        "source_health_exact_time_comparison":out,
        "original_entry_state":"CENSORED",
        "no_frozen_entry_relabeling":True,
        "production_sla_rollout_authorized":False,
        "original_ib_signed_quotation_verified":False,
        "native_owner_or_witness_approved":False,
        "real_trade_net_pnl_proven":False,
        "broker_queries_or_orders_sent_by_auditor":0,
        "trade_authority":False,
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    report=verify(a.root)
    write_report(a.output,report)
    print(json.dumps({"status":"RESEARCH_1005_FROZEN_CACHE_FRESHNESS_CONFIRMED",
        "baseline_age_ms":report["source_health_exact_time_comparison"]["baseline"]["age_ms"],
        "cache_age_ms":report["source_health_exact_time_comparison"]["canary"]["age_ms"],
        "original_entry":"CENSORED",
        "production_admission":False},sort_keys=True))
if __name__=="__main__":main()
