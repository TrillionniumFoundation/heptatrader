"""PIT-causal actual EUR/USD shadow signal FOLLOW-UP; never actual fill/PnL.

No broker/network call: re-verify ORIGINAL local PAPER quote SHA256 chain and
pre-decision source feature seal. This label may be independently reviewed
after its horizon, but never constitutes Canonical product V3 admission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from hepta_decision_attribution import read_private, strict
from .causal_ttl import sealed_original_broker_expiry
from .contracts import compile_example, validate_spec, ContractError
from .original_policy_audit import write_once
from .preaction_shadow_policy import POLICY_ID

SCHEMA="heptatrader.train-v2-live-original-shadow-forward-price-outcome.v1"
MAX_BYTES=128*1024


def _sha(raw:bytes)->str:
    return hashlib.sha256(raw).hexdigest()


def _load(path:Path)->tuple[dict,bytes]:
    path=Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ContractError("FORWARD_DOCUMENT_PATH_UNSAFE")
    raw=read_private(path,MAX_BYTES)
    return strict(raw),raw


def verify_before_outcome(root:Path,*,asof_ms:int)->tuple[dict,dict,dict,Path]:
    root=Path(root)
    if not root.is_absolute() or root.is_symlink():
        raise ContractError("FORWARD_EVIDENCE_ROOT_UNSAFE")
    if type(asof_ms) is not int or asof_ms<1:
        raise ContractError("FORWARD_REAL_CLOCK_REQUIRED")
    decision,raw_decision=_load(root/"real-shadow-decision-v1.json")
    pre,raw_pre=_load(root/"causal-feature-before-outcome.json")
    plan,_=_load(root/"plan.json")
    spec,_=_load(root/"feature-spec.json")
    validate_spec(spec)
    if (decision.get("schema")!="heptatrader.train-v2-shadow-preaction-policy.v1"
            or decision.get("status")!="RESEARCH_SHADOW_PREACTION_ONLY"
            or decision.get("policy_id")!=POLICY_ID
            or decision.get("trade_authority") is not False
            or decision.get("original_opaque_llm_trading_policy_propensity_qualified") is not False
            or decision.get("local_prospective_plan_presealed") is not True
            or decision.get("local_episode_claimed_before_rng") is not True
            or decision.get("after_cost_execution_pnl_observed") is not False
            or pre.get("schema")!="heptatrader.train-v2-live-original-preoutcome-frozen-feature.v1"
            or pre.get("preoutcome_label_status")!="PENDING_OUTCOME"
            or pre.get("preoutcome_label_value") is not None
            or pre.get("decision_file_sha256")!=_sha(raw_decision)
            or pre.get("decision_digest")!=decision.get("decision_digest")
            or pre.get("registered_plan_id")!=decision.get("local_prospective_plan_id")
            or pre.get("decision_at_ms")!=decision.get("decision_at_ms")
            or pre.get("outcome_due_ms")!=decision.get("outcome_not_available_before_ms")
            or spec.get("behavior_policy_id")!=POLICY_ID
            or spec.get("period_sha256")!=pre.get("original_period_sha256")
            or plan.get("plan_id")!=decision.get("local_prospective_plan_id")
            or plan.get("period_sha256")!=spec["period_sha256"]
            or plan.get("behavior_policy_id")!=POLICY_ID
            or spec.get("horizon_ms")!=300000
            or pre.get("captured_at_ms",0)>=pre.get("outcome_due_ms",0)
            or pre.get("input_quote_receipts")!=decision.get("source_history_quote_receipts")
            or pre.get("input_quote_receipts")!=decision.get("source_history_quote_receipts")):
        raise ContractError("FORWARD_PREDECISION_SCOPE_OR_FROZEN_FEATURE_CHANGED")
    # The frozen feature identifier and exact ordered six receipts are
    # compared again to the CURRENT independently re-read original chain.
    # No trust is conferred by a self-hashed locally writable document.
    evidence=pre.get("input_features",{}).get("input_receipts")
    if (not isinstance(pre.get("full_feature_id"),str)
            or len(pre["full_feature_id"])!=64
            or not isinstance(evidence,list)
            or len(evidence)!=len(decision["source_history_quote_receipts"])
            or any(not isinstance(row,dict)
                   or row.get("source_id")!=sid
                   or row.get("source_sha256")!=sid
                   for row,sid in zip(evidence,decision["source_history_quote_receipts"]))
            or pre["input_features"]["decision_at_ms"]!=decision["decision_at_ms"]):
        raise ContractError("FORWARD_SNAPSHOT_INPUT_RECEIPTS_CHANGED")
    source=Path(pre.get("source_segment_path",""))
    if (not source.is_absolute() or source.is_symlink()
            or source.parent.is_symlink() or source.name!="quotes.jsonl"
            or not source.parent.name.startswith("segment-")):
        raise ContractError("FORWARD_SOURCE_SEGMENT_OUT_OF_SCOPE")
    if not (plan["first_decision_ms"]<=decision["decision_at_ms"]<=plan["last_decision_ms"]
            and pre["captured_at_ms"]<decision["outcome_not_available_before_ms"]):
        raise ContractError("FORWARD_NON_PROSPECTIVE_WINDOW")
    return decision,pre,spec,source


def assess(root:Path,*,asof_ms:int|None=None)->dict:
    now=time.time_ns()//1000000 if asof_ms is None else asof_ms
    decision,pre,spec,source=verify_before_outcome(root,asof_ms=now)
    if now<decision["outcome_not_available_before_ms"]:
        raise ContractError("FORWARD_OUTCOME_MATURITY_NOT_REACHED")
    observations,source_proof=sealed_original_broker_expiry(source,spec)
    receipt_id=decision["source_quote_receipt"]
    original=[q for q in observations if q["source_id"]==receipt_id]
    if (len(original)!=1
            or original[0]["available_at_ms"]!=decision["source_available_at_ms"]
            or original[0]["event_at_ms"]!=decision["source_at_ms"]):
        raise ContractError("FORWARD_ORIGINAL_SOURCE_RECEIPT_CHANGED")
    result=compile_example(spec,observations,decision["decision_at_ms"],now)
    if (result["feature_id"]!=pre["full_feature_id"]
            or result["features"]!=pre["input_features"]
            or [x["source_id"] for x in result["features"]["input_receipts"]]
            !=decision["source_history_quote_receipts"]):
        raise ContractError("FORWARD_FEATURE_ID_CHANGED_AFTER_FUTURE_OBSERVATIONS")
    due=decision["outcome_not_available_before_ms"]
    if result["label_status"]=="PENDING_OUTCOME":
        raise ContractError("FORWARD_OUTCOME_STILL_PENDING")
    if result["label_status"]=="OBSERVED":
        label=result["label_receipt"]
        if (label is None or not due<=label["event_at_ms"]<=due+spec["label_tolerance_ms"]
                or label["available_at_ms"]>now):
            raise ContractError("FORWARD_TERMINAL_OUTCOME_NOT_CAUSAL")
    else:
        if now<due+spec["label_tolerance_ms"]:
            raise ContractError("FORWARD_EARLY_CENSORING_FORBIDDEN")
        label=None
    return {
        "schema":SCHEMA,
        "evaluated_at_ms":now,
        "exact_plan_id":decision["local_prospective_plan_id"],
        "precommitted_decision_sha256":pre["decision_file_sha256"],
        "pre_outcome_frozen_feature_receipt_sha256":_sha(
            (Path(root)/"causal-feature-before-outcome.json").read_bytes()),
        "original_observed_paper_quote_only":True,
        "original_local_chain_not_broker_signed":True,
        "source_segment_path":str(source),
        "post_outcome_source_prefix_sha256_not_decision_time_source_hash":
            source_proof["prefix_sha256"],
        "original_quote_entry_sha256":receipt_id,
        "feature_id_revalidated_exactly":pre["full_feature_id"],
        "feature_values_not_changed_after_outcome":True,
        "input_original_quote_receipts_at_decision":
            decision["source_history_quote_receipts"],
        "decision_at_ms":decision["decision_at_ms"],
        "outcome_due_ms":due,
        "actual_endpoint_or_censoring_status":result["label_status"],
        "actual_market_mid_price_return_bps":result["label_return_bps"],
        "actual_original_endpoint_quote_receipt":label,
        "shadow_signal_selected":decision["selected"],
        "shadow_selected_behavior_propensity_q32_raw":
            decision["selected_propensity_q32_raw"],
        "true_original_llm_policy_behavior_propensity_qualified":False,
        "research_causal_midprice_label_observed":
            result["label_status"]=="OBSERVED",
        "research_input_maturity_contract_causal":True,
        "economic_gross_or_net_trading_pnl_proven":False,
        "actual_order_fills_or_commissions_observed":False,
        "independent_product_observer_enrolled":False,
        "production_native_dataset_snapshot_v3_admitted":False,
        "no_trading_order_or_new_broker_query":True,
        "trading_authority":False,
        "promotion_allowed":False,
    }


def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    os.umask(0o077)
    record=assess(args.root)
    digest=write_once(args.output,record)
    print(json.dumps({
        "state":record["actual_endpoint_or_censoring_status"],
        "real_mid_quote_return_bps":record["actual_market_mid_price_return_bps"],
        "feature_id_unchanged":record["feature_values_not_changed_after_outcome"],
        "receipt_sha256":digest,
        "actual_after_cost_pnl":False,
        "trading_authority":False,
    },sort_keys=True))


if __name__=="__main__":
    main()
