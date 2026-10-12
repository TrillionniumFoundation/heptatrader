"""Readback verification of source-time REAL market test-only native chain.

Never signs, issues an order, or qualifies original LLM policy probabilities.
Distinguishes actual locally sealed source receipt from production root trust.
"""
from __future__ import annotations

import argparse
from decimal import Decimal,ROUND_HALF_EVEN
import hashlib
import json
import os
from pathlib import Path

from hepta_decision_attribution import read_private, strict
from .contracts import compile_example
from .causal_ttl import sealed_original_broker_expiry
from .original_policy_audit import write_once
from .preaction_shadow_policy import (
    _canonical,_policy_selection,POLICY_ID,CANDIDATES,
)
from .registry import verify_registration
from .real_source_native_signature_verify import verify as verify_signers

SCHEMA="heptatrader.train-v2-real-original-source-shadow-native-all-causal-integrity.v1"


def sha(raw:bytes)->str:
    return hashlib.sha256(raw).hexdigest()


def read(root:Path,name:str,maxbytes=1024*1024)->tuple[dict,bytes]:
    raw=read_private(root/name,maxbytes)
    return strict(raw),raw


def validate_choice(d:dict,quote:dict,policy_sha:str)->dict:
    if (d.get("status")!="RESEARCH_SHADOW_PREACTION_ONLY"
            or d.get("policy_id")!=POLICY_ID
            or d.get("trade_authority") is not False
            or d.get("original_opaque_llm_trading_policy_propensity_qualified") is not False
            or d.get("genuine_behavior_propensity_only_for_new_shadow_policy") is not True
            or d.get("local_episode_claimed_before_rng") is not True
            or d.get("broker_requests_or_orders")!=0
            or d.get("source_quote_receipt")!=quote["source_id"]
            or d.get("source_at_ms")!=quote["event_at_ms"]
            or d.get("source_available_at_ms")!=quote["available_at_ms"]
            or d.get("source_expires_at_ms")!=quote["valid_until_ms"]):
        raise ValueError("ORIGINAL_LOCAL_REAL_SHADOW_CHOICE_SOURCE_CHANGED")
    original=d.copy()
    original.pop("decision_digest",None)
    if sha(_canonical(original))!=d.get("decision_digest"):
        raise ValueError("ORIGINAL_SHADOW_SELF_IDENTITY_CHANGED")
    receipt=d["candidate_set_receipt"]
    digest_candidate=receipt.copy()
    digest_candidate.pop("candidate_set_digest",None)
    if (sha(_canonical(digest_candidate))!=receipt["candidate_set_digest"]
            or receipt["policy_id"]!=POLICY_ID
            or receipt["generator_code_sha256"]!=policy_sha
            or receipt["source_quote_receipt"]!=quote["source_id"]
            or receipt["omitted_count_bound"]!=0
            or receipt["complete_for_shadow_generator"] is not True):
        raise ValueError("ORIGINAL_SHADOW_POLICY_CODE_OR_CANDIDATE_DIGEST_CHANGED")
    selection_word=d.get("selection_word_two_bit")
    if selection_word is not None and (type(selection_word) is not int or
                                      not 0<=selection_word<4):
        raise ValueError("ORIGINAL_SHADOW_SAMPLING_WORD_NOT_TWO_BITS")
    def redraw():
        if selection_word is None:
            raise ValueError("ORIGINAL_SHADOW_CLAIMS_NONRANDOM_SAMPLING_WITHOUT_HARD_FILTER")
        return selection_word
    expected=_policy_selection(quote,draw=redraw)
    for key in ("candidates","selected","selection_word_two_bit",
                "selection_weights","probability_numerator",
                "probability_denominator","selected_propensity_q32_raw",
                "omitted_count_bound","hard_filter_spread_bps",
                "complete_for_shadow_generator"):
        if d.get(key)!=expected[key]:
            raise ValueError("ORIGINAL_SHADOW_RECORDED_SAMPLER_RULE_CHANGED:"+key)
    if (receipt["canonical_order"]!=expected["candidates"]
            or receipt["candidate_count"]!=len(expected["candidates"])
            or receipt["grammar"]!=list(CANDIDATES)):
        raise ValueError("ORIGINAL_SHADOW_POSTFILTER_CANDIDATES_CHANGED")
    return expected


def inspect(root:Path)->dict:
    root=Path(root)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("REAL_NATIVE_EVIDENCE_ROOT_UNSAFE")
    decision,dbytes=read(root,"real-shadow-decision-v1.json")
    pre,prebytes=read(root,"causal-feature-before-outcome.json")
    plan,_=read(root,"plan.json")
    spec,_=read(root,"feature-spec.json")
    packet,packetbytes=read(root,"native-real-quote-packet.json")
    obs,_=read(root,"real-five-minute-midprice-outcome.json")
    native,obbytes=read(root,"native-real-price-observer-packet.json")
    init,_=read(root/"native-lab-v1","native-source-decision-ack.json")
    done,_=read(root/"native-lab-v1","native-price-outcome-and-dataset-test-ack.json")
    code=Path(__file__).parent/"preaction_shadow_policy.py"
    from .live_forward_x230_verify import verify as verify_external
    evidence,_=read(root,"x230-independent-price-observation.json")
    verified_observer=verify_external(root,evidence)
    source=Path(pre["source_segment_path"])
    if not source.is_absolute() or source.is_symlink() or source.parent.is_symlink():
        raise ValueError("ORIGINAL_LOCAL_SOURCE_PATH_FORGED")
    registration=verify_registration(root/"registry",plan,spec,source)
    original,proof=sealed_original_broker_expiry(source,spec)
    at=decision["decision_at_ms"]
    original_history=[x for x in original
       if x["event_at_ms"]<=at and x["available_at_ms"]<=at][-6:]
    if (len(original_history)!=6
            or [x["source_id"] for x in original_history]
                !=decision["source_history_quote_receipts"]):
        raise ValueError("ORIGINAL_SOURCE_HISTORY_CHANGED_AT_DECISION")
    candidate=original_history[-1]
    selection=validate_choice(decision,candidate,sha(code.read_bytes()))
    if (at<registration["sealed_at_ms"]+30000
            or at<plan["first_decision_ms"] or at>plan["last_decision_ms"]
            or at>candidate["valid_until_ms"]
            or not 0<=at-candidate["available_at_ms"]<=1500
            or pre["captured_at_ms"]>=obs["outcome_due_ms"]
            or obs["outcome_due_ms"]!=at+300000):
        raise ValueError("ORIGINAL_SOURCE_TIME_CAUSAL_WINDOW_CHANGED")
    regenerated=compile_example(spec,original,at,at)
    if (regenerated["label_status"]!="PENDING_OUTCOME"
            or regenerated["feature_id"]!=pre["full_feature_id"]
            or regenerated["features"]!=pre["input_features"]
            or pre["decision_file_sha256"]!=sha(dbytes)
            or packet["preoutcome_feature_id"]!=pre["full_feature_id"]
            or packet["preoutcome_seal_sha256"]!=sha(prebytes)
            or packet["shadow_decision_sha256"]!=sha(dbytes)):
        raise ValueError("ORIGINAL_PREOUTCOME_FEATURE_CHANGED")
    if (native["source_shadow_packet_sha256"]!=sha(packetbytes)
            or native["episode_id"]!=packet["episode_id"]
            or native["midprice_change_basis_points_q32_raw"]!=
               int((Decimal(obs["actual_market_mid_price_return_bps"])*Decimal(1<<32))
                   .quantize(Decimal(1),rounding=ROUND_HALF_EVEN))
            or native["market_midprice_change_bps"]!=obs["actual_market_mid_price_return_bps"]
            or native["trade_pnl_reported"] is not False
            or verified_observer["x230_independently_recomputed_midprice_return_bps"]!=obs["actual_market_mid_price_return_bps"]):
        raise ValueError("ORIGINAL_CROSSHOST_TERMINAL_PRICE_OR_Q32_CHANGED")
    signature=verify_signers(root)
    if (signature["public_only_ed25519_messages_verified"]!=4
            or signature["native_witness_ack_sequence"]!=2
            or init["native_acknowledged_at_ms"]>=obs["outcome_due_ms"]
            or done["native_V3_dataset_is_product_market_eligible"] is not False):
        raise ValueError("ORIGINAL_NATIVE_SOURCE_SIGNATURE_OR_WITNESS_NOT_CAUSAL")
    return {
        "schema":SCHEMA,
        "research_only_actual_IB_PAPER_quote_chain":True,
        "new_shadow_CSPRNG_two_bit_and_exact_Q32_recomputed":True,
        "independent_original_quote_snapshot_at_source_time":True,
        "original_shadow_quote_entry_receipt":candidate["source_id"],
        "actual_original_source_first_input_receipt":original_history[0]["source_id"],
        "real_market_quote_snapshot_prefix_verified_after_the_fact":
            proof["prefix_sha256"],
        "original_preaction_feature_frozen_and_regenerated_unchanged":True,
        "shadow_plan_seal_lead_ms":plan["first_decision_ms"]-registration["sealed_at_ms"],
        "one_preoutcome_shadow_choice":decision["selected"],
        "shadow_only_true_selected_propensity_q32_raw":selection["selected_propensity_q32_raw"],
        "native_Mac_Generator_signature_before_price_horizon":True,
        "native_X230_observer_result_after_price_horizon":True,
        "actual_observed_5min_original_market_midprice_bps":
            obs["actual_market_mid_price_return_bps"],
        "actual_observer_price_calculation_from_same_source_copy":True,
        "original_native_on_disk_witness_record_count":2,
        "native_original_test_V3_frozen_and_revalidated":True,
        "source_time_live_opaque_LLM_action_probability_attested":False,
        "original_policy_full_decision_opportunity_set_enumerated":False,
        "independent_organizational_production_root_enrolled":False,
        "native_datasetV3_eligible_for_product_training":False,
        "after_cost_broker_fills_PnL_efficacy":False,
        "broker_orders_sent_by_trial":0,
        "automatic_model_promotion":False,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args();os.umask(0o077)
    report=inspect(args.root)
    digest=write_once(args.output,report)
    print(json.dumps({
        "real_shadow_selection":report["one_preoutcome_shadow_choice"],
        "real_selected_propensity_q32_raw":report["shadow_only_true_selected_propensity_q32_raw"],
        "native_witness_events":report["original_native_on_disk_witness_record_count"],
        "real_5min_midprice_bps":report["actual_observed_5min_original_market_midprice_bps"],
        "product_authority":False,"receipt_sha256":digest
    },sort_keys=True))


if __name__=="__main__":main()
