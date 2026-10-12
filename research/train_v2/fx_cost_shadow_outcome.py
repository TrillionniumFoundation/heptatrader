"""Mature one previously sealed FX zero-return cost shadow on ORIGINAL PAPER quotes.

Price-only, not fill/commission or a live PnL reward. A source event after the
predeclared horizon must genuinely arrive in the unchanged local IB hash-chain.
No broker request, position change, retraining, model selection or order.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import time

from hepta_decision_attribution import read_private
from .adapters import make_spec
from .causal_ttl import sealed_original_broker_expiry
from .original_policy_audit import write_once
from . import fx_cost_shadow as shadow


class MaturityError(ValueError):
    pass


def ensure(ok, reason):
    if not ok:
        raise MaturityError(reason)


def sha(x):
    return hashlib.sha256(x).hexdigest()


def _read_doc(p):
    return shadow._load_private(Path(p))


def exact_historical_prefix(raw: bytes, expected: str):
    """Find the exact ORIGINAL decision-time line boundary within an append-only file.

    Merely using a present-day whole-file digest would wrongly confuse
    after-decision appended rows with the pre-decision source snapshot.
    """
    ensure(raw.endswith(b"\n"), "ORIGINAL_SOURCE_INCOMPLETE_TAIL")
    ensure(isinstance(expected,str) and len(expected)==64,"ORIGINAL_PREFIX_SHA_INVALID")
    h=hashlib.sha256()
    offset=0
    for line in raw.splitlines(keepends=True):
        h.update(line)
        offset+=len(line)
        if h.hexdigest()==expected:
            return raw[:offset]
    raise MaturityError("PREDECISION_SOURCE_PREFIX_NOT_FOUND_OR_TAMPERED")


def evaluate(*,root,source_file,config_path,cost_path,registry_root,
             now_ms=None,reader=sealed_original_broker_expiry):
    """Never accepts revised historical bars, replayed labels or price lookahead."""
    root=Path(root)
    source_file=Path(source_file)
    config,period_sha,cost,cost_sha=shadow._scope(config_path,cost_path)
    shadow._source_file_scope(config,source_file)
    manifest,manifest_raw=_read_doc(root/"manifest.json")
    spec,_=_read_doc(root/"feature-spec.json")
    plan,_=_read_doc(root/"plan.json")
    decision,decision_raw=_read_doc(root/"frozen-decision.json")
    qualification,_=_read_doc(root/"qualification.json")
    registration,_=_read_doc(root/"local-registration.json")
    ensure(manifest==shadow._manifest(period_sha,cost_sha)
           and spec==make_spec(period_sha,manifest["behavior_policy_id"],
                               lookback=shadow.LOOKBACK,
                               horizon_ms=shadow.HORIZON_MS,
                               broker_reported_expiry=True)
           and plan.get("plan_id")==decision.get("plan_id")==registration.get("plan_id")
           and qualification.get("decision_sha256")==sha(decision_raw)
           and qualification.get("decision_durable_before_expiry") is True
           and qualification.get("state")==
                   "RESEARCH_PREACTION_NO_TRADE_DURABLE_BEFORE_ORIGINAL_TTL"
           and decision.get("state")=="FROZEN_ORIGINAL_PAPER_COST_SHADOW_NO_TRADE"
           and decision.get("selected_action")=="NO_TRADE"
           and decision.get("trained_model_applied") is False
           and decision.get("new_market_order_prepared") is False
           and decision.get("model_promotion_authorized") is False
           and decision.get("trade_authority") is False
           and decision.get("cost_receipt_sha256")==cost_sha
           and decision.get("manifest_sha256")==sha(manifest_raw),
           "FROZEN_RESEARCH_SOURCE_OR_DECISION_CHANGED")
    from .registry import verify_registration, MIN_LEAD_MS
    receipt=verify_registration(registry_root,plan,spec,source_file)
    ensure(receipt==registration and
           plan["first_decision_ms"] <= decision["decision_at_ms"] <= plan["last_decision_ms"]
           and decision["decision_at_ms"]>=receipt["sealed_at_ms"]+MIN_LEAD_MS,
           "PRECOMMIT_NOT_PROVEN")
    moment=time.time_ns()//1000000 if now_ms is None else now_ms
    due=decision["outcome_not_available_before_ms"]
    ensure(type(moment) is int and moment>=due,"OUTCOME_NOT_MATURE_NO_LOOKAHEAD")
    ensure(due==decision["decision_at_ms"]+shadow.HORIZON_MS,
           "ORIGINAL_HORIZON_TAMPERED")
    ensure(decision["source_quote_available_at_ms"]<=decision["decision_at_ms"]
           <=decision["source_quote_expiry_ms"]
           and qualification["durable_at_ms"]<=decision["source_quote_expiry_ms"],
           "ORIGINAL_QUOTE_EXPIRED_BEFORE_DURABLE_DECISION")
    # Independently prove the original decision prefix is still present byte
    # for byte in the immutable-source chain; appends are fine, edits are not.
    raw=read_private(source_file,64*1024*1024)
    prefix=exact_historical_prefix(raw,decision["source_prefix_sha256"])
    original_rows,provenance=reader(source_file,spec)
    ensure(provenance.get("broker_orders_sent")==0,
           "ORIGINAL_SOURCE_HAS_MUTATION_SCOPE")
    # The verifier may read a later prefix, but it can never use rows
    # appended *after decision* to redefine the decision input.
    from laya_cell.shadow_source import strict, SCHEMA as SOURCE_SCHEMA
    # The pinned legacy quote reader intentionally keeps the FIRST chained
    # receipt for a given broker observed_at_ms. Later identical-value
    # callbacks at that timestamp are not distinct forecast features.
    # Reconstruct exactly that first-witness-per-timestamp identity, rather
    # than incorrectly comparing with the last raw callback in the prefix.
    frozen_first_by_time={}
    for line in prefix.splitlines():
        event=strict(line)
        if event.get("schema")!=SOURCE_SCHEMA or event.get("kind")!="QUOTE":
            continue
        entry=event.get("data")
        ensure(isinstance(entry,dict)
               and type(entry.get("observed_at_ms")) is int
               and entry["observed_at_ms"]>0,
               "ORIGINAL_PREFIX_QUOTE_SHAPE_INVALID")
        key=entry["observed_at_ms"]
        if key in frozen_first_by_time:
            previous=frozen_first_by_time[key]["data"]
            ensure(Decimal(str(entry.get("bid")))==Decimal(str(previous["bid"]))
                   and Decimal(str(entry.get("ask")))==Decimal(str(previous["ask"])),
                   "ORIGINAL_PREFIX_SAME_TIME_CONFLICT")
        else:
            frozen_first_by_time[key]=event
    ensure(len(frozen_first_by_time)>=shadow.LOOKBACK
           and frozen_first_by_time[max(frozen_first_by_time)]["sha256"]==
               decision["original_quote_source_id"],
           "ORIGINAL_DECISION_SOURCE_IS_NOT_EXACT_FROZEN_PREFIX")
    originally_recorded=next((x for x in original_rows
                              if x["source_id"]==decision["original_quote_source_id"]),None)
    ensure(originally_recorded is not None
           and originally_recorded["available_at_ms"]==
               decision["source_quote_available_at_ms"]
           and originally_recorded["valid_until_ms"]==
               decision["source_quote_expiry_ms"],
           "ORIGINAL_INPUT_SOURCE_MISMATCH")
    ensure([q["source_id"] for q in original_rows
            if q["source_id"] in set(decision["input_quote_source_ids"])
            and q["event_at_ms"]<=decision["decision_at_ms"]][-shadow.LOOKBACK:]
           ==decision["input_quote_source_ids"],
           "ORIGINAL_SIX_INPUT_PROVENANCE_CHANGED")
    matches=[q for q in original_rows if due<=q["event_at_ms"]<=due+5000
             and q["available_at_ms"]<=moment
             and q["event_at_ms"]<=q["available_at_ms"]<=q["valid_until_ms"]]
    ensure(matches,"MATURE_ORIGINAL_OUTCOME_QUOTE_UNAVAILABLE")
    selected=min(matches,key=lambda q:(q["event_at_ms"],q["available_at_ms"]))
    initial_mid=(Decimal(originally_recorded["bid"])+Decimal(originally_recorded["ask"]))/2
    outcome_mid=(Decimal(selected["bid"])+Decimal(selected["ask"]))/2
    ensure(initial_mid>0 and outcome_mid>0, "INVALID_ORIGINAL_MID")
    price_only_bps=(outcome_mid/initial_mid-1)*10000
    frozen_cost=shadow.cost_frontier(bid=originally_recorded["bid"],
                                    ask=originally_recorded["ask"],cost=cost)
    ensure(frozen_cost==decision["economic_cost_hurdle"],
           "PREDECISION_COSTS_CHANGED")
    return {
        "schema":"heptatrader.fx-cost-shadow-original-price-maturity.v1",
        "status":"RESEARCH_PRICE_ONLY_OUTCOME_MATURED_NO_ALPHA_CLAIM",
        "original_plan_id":plan["plan_id"],
        "original_decision_sha256":sha(decision_raw),
        "original_predecision_source_prefix_sha256":sha(prefix),
        "original_input_quote_id":decision["original_quote_source_id"],
        "mature_original_outcome_quote_id":selected["source_id"],
        "original_quote_at_ms":originally_recorded["event_at_ms"],
        "preaction_decision_at_ms":decision["decision_at_ms"],
        "original_horizon_due_ms":due,
        "mature_quote_event_at_ms":selected["event_at_ms"],
        "mature_quote_available_at_ms":selected["available_at_ms"],
        "mature_result_sealed_at_ms":moment,
        "price_only_midpoint_return_bps":format(price_only_bps,"f"),
        "previously_frozen_action":"NO_TRADE",
        "no_trade_hypothetical_pnl_usd":"0",
        "actual_execution_pnl_attested":False,
        "trained_forecaster_efficacy_tested":False,
        "model_promoted":False,
        "independently_sourced_broker_statement_attested":False,
        "original_market_source_locally_hashed_not_broker_signed":True,
        "native_product_ledgerwriter_acked":False,
        "trade_authority":False,
        "broker_queries_sent":0,
        "broker_orders_sent":0,
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("root","source-file","config","cost","registry-root","output"):
        parser.add_argument("--"+name,required=True,type=Path)
    args=parser.parse_args()
    os.umask(0o077)
    report=evaluate(root=args.root,source_file=args.source_file,
                    config_path=args.config,cost_path=args.cost,
                    registry_root=args.registry_root)
    proof_sha=write_once(args.output,report)
    print(json.dumps({"status":report["status"],
                     "price_only_midpoint_return_bps":report["price_only_midpoint_return_bps"],
                     "real_execution_pnl_attested":False,
                     "model_promotion_allowed":False,
                     "receipt_sha256":proof_sha,"broker_orders_sent":0},sort_keys=True))


if __name__=="__main__":
    main()
