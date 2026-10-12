"""Bind newly preregistered ORIGINAL IB PAPER shadow-only decision to native lab.

Native decision is a strict TEST-only source-time adapter. It is NOT the
original opaque LLM policy, does not call IB, train, or issue an order.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from .contracts import compile_example
from .preaction_shadow_policy import POLICY_ID, SCHEMA as DECISION_SCHEMA
from .causal_ttl import sealed_original_broker_expiry
from .original_policy_audit import write_once
from .registry import verify_registration
from hepta_decision_attribution import read_private, strict

SCHEMA="heptatrader.train-v2-real-market-original-shadow-native-test-only-packet.v1"


def _sha(value:bytes)->str:
    return hashlib.sha256(value).hexdigest()


def _load(root:Path,name:str)->tuple[dict,bytes]:
    raw=read_private(root/name,1024*1024)
    return strict(raw),raw


def build(root:Path,source:Path)->dict:
    root=Path(root)
    source=Path(source)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("TEST_RESEARCH_ROOT_NOT_ABSOLUTE")
    if not source.is_absolute() or source.is_symlink() or source.parent.is_symlink():
        raise ValueError("ORIGINAL_SOURCE_PATH_NOT_PERMITTED")
    plan,_=_load(root,"plan.json")
    spec,_=_load(root,"feature-spec.json")
    decision,raw=_load(root,"real-shadow-decision-v1.json")
    if (decision.get("schema")!=DECISION_SCHEMA
        or decision.get("status")!="RESEARCH_SHADOW_PREACTION_ONLY"
        or decision.get("policy_id")!=POLICY_ID
        or decision.get("trade_authority") is not False
        or decision.get("not_an_original_broker_PREPARED_command") is not True
        or decision.get("original_opaque_llm_trading_policy_propensity_qualified") is not False
        or decision.get("local_episode_claimed_before_rng") is not True
        or decision.get("local_prospective_plan_presealed") is not True
        or decision.get("after_cost_execution_pnl_observed") is not False
        or decision.get("broker_requests_or_orders")!=0
        or decision.get("decision_at_ms",0)!=
          decision.get("outcome_not_available_before_ms",0)-300000
        or decision.get("local_prospective_plan_id")!=plan.get("plan_id")
        or plan.get("behavior_policy_id")!=POLICY_ID
        or plan.get("period_sha256")!=spec.get("period_sha256")
        or plan.get("trade_authority") is not False):
        raise ValueError("SHADOW_ORIGINAL_REQUEST_NOT_TRUE_PREACTION_RESEARCH")
    receipt=verify_registration(root/"registry",plan,spec,source)
    if (receipt["sealed_at_ms"]+30000>decision["decision_at_ms"]
            or not plan["first_decision_ms"]<=decision["decision_at_ms"]<=plan["last_decision_ms"]):
        raise ValueError("LOCAL_PROSPECTIVE_REGISTRATION_NOT_EARLY")
    now=time.time_ns()//1000000
    if not decision["decision_at_ms"]<=now<decision["outcome_not_available_before_ms"]:
        raise ValueError("PROSPECTIVE_SOURCE_OUTCOME_ALREADY_AVAILABLE")
    # Native generator must be signed well before outcome.
    if now-decision["decision_at_ms"]>60000:
        raise ValueError("SOURCE_TIME_NATIVE_SIGNING_WINDOW_TOO_LATE")
    quotes,prefix=sealed_original_broker_expiry(source,spec)
    if (prefix["prefix_sha256"]!=decision["original_source_prefix_sha256"]):
        # The same active segment may have grown; check its verified old
        # prefix by exact original digest with separate source reader.
        from laya_cell.shadow_source import read_source
        header,rows,currently=read_source(source)
        source_prefix=decision["original_source_prefix_sha256"]
        with open(source,"rb") as f:rawfile=f.read()
        # Only original fully completed local-chain frames count.
        hashes=hashlib.sha256()
        found=False
        for line in rawfile.splitlines(keepends=True):
            if not line.endswith(b"\n"):break
            hashes.update(line)
            if hashes.hexdigest()==source_prefix:found=True;break
        if not found:raise ValueError("ORIGINAL_SOURCE_PREFIX_REPLACED_SINCE_DECISION")
    example=compile_example(spec,quotes,decision["decision_at_ms"],
                            decision["decision_at_ms"])
    if (example["label_status"]!="PENDING_OUTCOME"
            or example["label_return_bps"] is not None
            or example["economic_return_attested"] is not False
            or [r["source_id"] for r in example["features"]["input_receipts"]]
            !=decision["source_history_quote_receipts"]
            or len(decision["source_history_quote_receipts"])!=6):
        raise ValueError("SOURCE_PRECOMMITTED_FEATURE_PARITY_FAILED")
    pre={
        "schema":"heptatrader.train-v2-live-original-preoutcome-frozen-feature.v1",
        "captured_at_ms":now,
        "actual_broker_paper_original_quote_local_sha_chain":True,
        "source_profile":spec["feature_profile"],
        "original_period_sha256":spec["period_sha256"],
        "registered_plan_id":plan["plan_id"],
        "decision_file_sha256":_sha(raw),
        "decision_digest":decision["decision_digest"],
        "decision_at_ms":decision["decision_at_ms"],
        "outcome_due_ms":example["label_due_ms"],
        "input_quote_receipts":decision["source_history_quote_receipts"],
        "full_feature_id":example["feature_id"],
        "input_features":example["features"],
        "preoutcome_label_status":"PENDING_OUTCOME",
        "preoutcome_label_value":None,
        "source_chain_verified_prefix_sha256_at_capture_not_at_decision":
           prefix["prefix_sha256"],
        "source_segment_path":str(source),
        "production_source_time_signature_authorized":False,
        "independent_observer_outcome_already_signed":False,
        "economic_return_attested":False,
        "product_owner_admitted":False,
        "broker_queries_or_orders_sent":0,
    }
    prehash=write_once(root/"causal-feature-before-outcome.json",pre)
    record={
        "schema":SCHEMA,
        "policy_id":POLICY_ID,
        "source_kind":"REAL_ORIGINAL_IB_PAPER_LOCAL_SHA_CHAIN",
        "plan_id":plan["plan_id"],
        "episode_id":decision["shadow_episode_id"],
        "original_quote_receipt":decision["source_quote_receipt"],
        "source_input_receipts":decision["source_history_quote_receipts"],
        "preoutcome_feature_id":example["feature_id"],
        "preoutcome_seal_sha256":prehash,
        "shadow_decision_sha256":_sha(raw),
        "generator_code_sha256":decision["candidate_set_receipt"]["generator_code_sha256"],
        "candidate_grammar":decision["candidates"],
        "selected":decision["selected"],
        "selected_propensity_q32_raw":decision["selected_propensity_q32_raw"],
        "first_window_ms":plan["first_decision_ms"],
        "last_window_ms":plan["last_decision_ms"],
        "plan_sealed_at_ms":receipt["sealed_at_ms"],
        "decision_at_ms":decision["decision_at_ms"],
        "outcome_due_ms":decision["outcome_not_available_before_ms"],
        "true_new_shadow_policy_only":True,
        "trade_authority":False,
        "original_llm_policy_probability":False,
        "broker_order_was_sent":False,
        "product_root_enrolled":False,
        "after_cost_trading_pnl":False,
    }
    return record


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--source",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path)
    a=p.parse_args();os.umask(0o077)
    data=build(a.root,a.source)
    sha=write_once(a.output,data)
    print(json.dumps({
        "status":"SOURCE_TIME_REAL_IB_RESEARCH_NATIVE_REQUEST_READY",
        "sha256":sha,
        "source_decision_at_ms":data["decision_at_ms"],
        "source_quote":data["original_quote_receipt"],
        "native_product_trust":False,
        "broker_order":False,
    },sort_keys=True))


if __name__=="__main__": main()
