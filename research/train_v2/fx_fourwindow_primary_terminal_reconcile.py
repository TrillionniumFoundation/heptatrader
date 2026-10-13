"""Strict primary-final to off-host 4x4 receipt reconciliation; FX PAPER research.

The existing physical verifier proves 16 original attempted maturities. This
separate final gate ALSO requires the actual finite research worker's own
write-once final.json to reference the same original 4 entry/16 horizon SHA256s.
No product authorization, broker I/O, post-hoc source price, or trade command.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from .fx_first_three_original_crosshost import (
    PLAN_SHA, SEAL_SHA, HORIZONS, TRIAL, CrosshostHold,
    demand, evidence, sha, write_report, verify as first_three_verify,
)
from .fx_all_four_final_crosshost import (
    verify_4, BEFORE_LAST_DUE_MS, SCHEMA as ORIGINAL_PHYSICAL_SCHEMA,
)

SCHEMA="heptatrader.fx-original-four-window-primary-terminal-reconcile.v1"

def validate_manifest(manifest, early, physical):
    """Testable content-only check. No active host or mutable broker source."""
    demand(manifest.get("schema")==TRIAL+".final.v1"
           and manifest.get("sealed_sha256")==SEAL_SHA
           and manifest.get("window_count")==4
           and manifest.get("original_preaction_frozen_count")==0
           and manifest.get("mature_quote_only_samples")==0
           and manifest.get("all_historical_test_and_loss_facts_unchanged") is True
           and manifest.get("no_model_predictions_or_production_admission") is True
           and manifest.get("broker_orders_sent")==0
           and manifest.get("trading_strategy_changed") is False
           and manifest.get("model_promotion_allowed") is False
           and manifest.get("actual_execution_rewards")==0,
           "PRIMARY_WORKER_FINAL_SCOPE_CHANGED")
    when=manifest.get("completed_at_ms")
    demand(type(when) is int and when>=BEFORE_LAST_DUE_MS-8500,
           "PRIMARY_WORKER_FINAL_PREMATURE_OR_MISSING_TIME")
    demand(physical.get("schema")==ORIGINAL_PHYSICAL_SCHEMA
           and physical.get("result")=="COMPLETE_4X4_CENSORED_RESEARCH_PROVEN_NO_TRADE"
           and physical.get("original_complete_matured_censored_count")==16
           and physical.get("original_expected_maturity_count")==16
           and physical.get("fourth_future_horizons_pending")==[]
           and physical.get("trade_authority") is False
           and physical.get("model_promotion_allowed") is False,
           "INDEPENDENT_PHYSICAL_VERIFIER_NOT_COMPLETE")
    demand(early.get("original_decisions_censored")==3
           and early.get("original_maturity_attempts_censored")==12
           and len(early.get("first_three",[]))==3,
           "EARLY_PHYSICAL_WINDOW_REPORT_MISSING")
    records=manifest.get("windows")
    demand(type(records) is list and len(records)==4,
           "PRIMARY_WINDOW_COUNT_INVALID")
    refs=[]
    for i,w in enumerate(records):
        demand(type(w) is dict and w.get("index")==i
               and w.get("decision_state")=="CENSORED",
               "PRIMARY_FROZEN_DECISION_NOT_CENSORED")
        ref=(early["first_three"][i]["original_decision_sha256"] if i<3
             else physical["original_1005_decision_sha256"])
        demand(w.get("decision_attempt_sha256")==ref,
               "PRIMARY_DECISION_SHA_NOT_SAME_ORIGINAL")
        outcomes=w.get("outcomes")
        demand(type(outcomes) is list and len(outcomes)==len(HORIZONS),
               "PRIMARY_FOUR_MATURITIES_MISSING")
        for k,horizon in enumerate(HORIZONS):
            row=outcomes[k]
            if i<3:
                first_rows=early["first_three"][i]["horizon_outcomes"]
                demand(type(first_rows) is list and len(first_rows)==4
                       and first_rows[k]["horizon_minutes"]==horizon,
                       "EARLY_INDEPENDENT_HORIZON_SCOPE_CHANGED")
                expected=first_rows[k]["original_maturity_attempt_sha256"]
            else:
                last_rows=physical["original_1005_detailed_maturities"]
                demand(type(last_rows) is list and len(last_rows)==4
                       and last_rows[k]["horizon_minutes"]==horizon,
                       "FOURTH_INDEPENDENT_HORIZON_SCOPE_CHANGED")
                expected=last_rows[k]["original_maturity_receipt_sha256"]
            demand(type(row) is dict
                   and row.get("horizon_minutes")==horizon
                   and row.get("state")=="CENSORED"
                   and row.get("attempt_sha256")==expected,
                   "PRIMARY_MATURE_RECEIPT_OR_IDENTITY_CHANGED")
            refs.append(expected)
    demand(len(set(refs))==16,
           "DUPLICATE_PRIMARY_MATURE_RECEIPT_IDENTITIES")
    return {
        "schema":SCHEMA,
        "source":"EXACT_PHYSICAL_ARCHIVED_FROZEN_PAPER_RESEARCH_ONLY",
        "primary_worker_final_completed_at_ms":when,
        "all_four_entry_sha256s_reconciled":True,
        "all_sixteen_maturity_sha256s_reconciled":True,
        "original_4x4_CENSORED_study_verified":True,
        "pretrial_plan_sha256":PLAN_SHA,
        "pretrial_seal_sha256":SEAL_SHA,
        "original_full_final_sha256_available":True,
        "original_no_trade_and_no_model_promotion":True,
        "primary_final_alone_not_independent_systemd_exit_proof":True,
        "production_ib_quote_signed":False,
        "true_broker_execution_pnl_proven":False,
        "native_product_owner_or_witness_enrolled":False,
        "broker_queries_or_orders_sent_by_audit":0,
        "trade_authority":False,
        "model_promotion_allowed":False,
    }

def verify_terminal(*,firstthree_root,cache1005_root,fourth_root,
                    primary_final_file):
    """Requires final to have been copied unchanged to physical audit host."""
    physical=verify_4(firstthree_root=firstthree_root,
                      cache1005_root=cache1005_root,
                      fourth_root=fourth_root,
                      require_complete=True)
    early=first_three_verify(firstthree_root)
    p=Path(primary_final_file)
    final,fdigest=evidence(p.parent,p.name)
    receipt=validate_manifest(final,early,physical)
    receipt.update({
        "verified_at_utc":datetime.now(timezone.utc).isoformat(),
        "original_primary_final_sha256":fdigest,
        "crosshost_allfour_verified":True,
    })
    return receipt

def main():
    p=argparse.ArgumentParser()
    for x in ("firstthree","cache1005","fourth","primary-final","output"):
        p.add_argument("--"+x,required=True,type=Path)
    a=p.parse_args()
    r=verify_terminal(firstthree_root=a.firstthree,cache1005_root=a.cache1005,
                      fourth_root=a.fourth,primary_final_file=a.primary_final)
    write_report(a.output,r)
    print(json.dumps({"state":"ORIGINAL_4X4_RESEARCH_PRIMARY_FINAL_AND_CROSSHOST_RECONCILED",
                     "fully_verified_original_censored_maturities":16,
                     "original_price_model_efficacy_claimed":False,
                     "trade_authority":False},sort_keys=True))
if __name__=="__main__":main()
