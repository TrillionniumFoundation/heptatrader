"""Pocket4 fail-closed check of X230 signed independent computation.

This verifies only a SELF-ATTESTED ephemeral X230 public key and that X230
recomputed the identical immutable price outcome from a full copy of the
locally chained Pocket4 source. Not a product Root authorized Observer.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from hepta_decision_attribution import read_private, strict
from .original_policy_audit import write_once
from .live_forward_x230_witness import DOMAIN, canonical

SCHEMA="heptatrader.train-v2-live-x230-crossmachine-outcome-confirmation.v1"
ENVELOPE_SCHEMA="heptatrader.train-v2-x230-observer-ephemeral-testonly-signed.v1"


def digest(raw:bytes)->str:
    return sha256(raw).hexdigest()


def verify(root:Path, observer:dict, *, read_source_bytes=None)->dict:
    root=Path(root)
    decision_raw=read_private(root/"real-shadow-decision-v1.json",131072)
    pre_raw=read_private(root/"causal-feature-before-outcome.json",131072)
    outcome_raw=read_private(root/"real-five-minute-midprice-outcome.json",131072)
    decision=strict(decision_raw)
    pre=strict(pre_raw)
    outcome=strict(outcome_raw)
    if (observer.get("envelope_schema")!=ENVELOPE_SCHEMA
            or observer.get("signature_domain_hex")!=DOMAIN.hex()
            or observer.get("test_private_key_generated_in_x230_memory_only") is not True
            or observer.get("real_production_private_key_used") is not False
            or observer.get("native_product_root_trust_enrolled") is not False):
        raise ValueError("X230_OBSERVER_PRODUCT_TRUST_NOT_AUTHORIZED")
    signed=observer.get("signed_observation")
    if not isinstance(signed,dict) or signed.get("schema")!="heptatrader.train-v2-x230-independently-recomputed-original-quote-label.v1":
        raise ValueError("X230_OBSERVER_EVIDENCE_SCHEMA")
    pub=bytes.fromhex(observer["public_key_hex"])
    sig=bytes.fromhex(observer["signature_hex"])
    if len(pub)!=32 or len(sig)!=64:
        raise ValueError("X230_SIGNING_KEY_OR_SIGNATURE_SHAPE")
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(sig,DOMAIN+canonical(signed))
    except (ValueError,InvalidSignature) as err:
        raise ValueError("X230_SIGNED_RECOMPUTATION_INVALID_SIGNATURE") from err
    endpoint=outcome["actual_original_endpoint_quote_receipt"]
    if (signed.get("signed_on_physical_host")!="qian-ThinkPad-X230"
            or signed.get("role")!="SEPARATE_HOST_TEST_ONLY_OBSERVER"
            or signed.get("production_independent_authority") is not False
            or signed.get("real_trade_fills_or_net_pnl_verified") is not False
            or signed.get("model_promotion_or_trading_authorized") is not False
            or signed.get("source_is_a_copy_of_pocket4_local_chain_not_separate_ib_feed") is not True
            or signed.get("reported_outcome_sha256")!=digest(outcome_raw)
            or signed.get("decision_precommit_sha256")!=digest(decision_raw)
            or signed.get("frozen_preoutcome_sha256")!=digest(pre_raw)
            or signed.get("frozen_feature_id")!=pre["full_feature_id"]
            or signed.get("recomputed_five_minute_midprice_return_bps")
              !=outcome["actual_market_mid_price_return_bps"]
            or signed.get("original_observer_quote_receipt")!=endpoint["source_id"]
            or signed.get("original_input_quote_receipts")!=decision["source_history_quote_receipts"]
            or not isinstance(decision.get("source_history_quote_receipts"),list)
            or len(decision["source_history_quote_receipts"])!=6
            or outcome.get("original_quote_entry_sha256")!=decision.get("source_quote_receipt")
            or outcome.get("outcome_due_ms")!=decision.get("decision_at_ms",0)+300000
            or signed.get("decision_at_ms")!=decision["decision_at_ms"]
            or signed.get("due_at_ms")!=outcome["outcome_due_ms"]
            or signed.get("endpoint_at_ms")!=endpoint["event_at_ms"]
            or not outcome["outcome_due_ms"]<=endpoint["event_at_ms"]<=outcome["outcome_due_ms"]+5000
            or signed.get("reported_at_ms")!=outcome["evaluated_at_ms"]
            or signed.get("observer_computed_at_ms",0)<outcome["evaluated_at_ms"]
            or signed.get("verified_quote_path_points",0)<6
            or outcome.get("actual_endpoint_or_censoring_status")!="OBSERVED"
            or outcome.get("trading_authority") is not False):
        raise ValueError("X230_SIGNED_RECOMPUTATION_ORIGINAL_IDENTITY_CHANGED")
    # Original source file still lives on Pocket4, separate from X230's
    # owner-private copy. Verify that exact snapshot is an unchanged PREFIX
    # of the locally chained original file; file may have grown after transfer.
    original=Path(outcome["source_segment_path"])
    if (not original.is_absolute() or original.is_symlink()
            or not original.is_file() or original.name!="quotes.jsonl"
            or not original.parent.name.startswith("segment-")):
        raise ValueError("X230_SOURCE_BINDING_NOT_ORIGINAL")
    from laya_cell.shadow_source import read_source
    header,quotes,prefix=read_source(original)
    if header.get("period_sha256")!=pre["original_period_sha256"]:
        raise ValueError("X230_ORIGINAL_SOURCE_PERIOD_CHANGED")
    n=signed["copied_original_source_bytes"]
    if type(n) is not int or not 0<n<5*1024*1024:
        raise ValueError("X230_SOURCE_COPY_BOUND")
    if read_source_bytes is None:
        fd=os.open(original,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            copy=os.read(fd,n)
        finally:os.close(fd)
    else:
        copy=read_source_bytes
    if len(copy)!=n or digest(copy)!=signed["copied_original_source_sha256"]:
        raise ValueError("X230_ORIGINAL_QUOTE_COPY_NO_LONGER_MATCHES_SOURCE")
    return {
        "schema":SCHEMA,
        "x230_ephemeral_test_public_key_sha256":digest(pub),
        "valid_ed25519_signature_from_x230_test_key":True,
        "physically_separate_observer_reported_by_ssh_host":True,
        "hardware_host_identity_attested":False,
        "real_original_quote_copy_matches_pocket4_prefix":True,
        "x230_independently_recomputed_midprice_return_bps":
            signed["recomputed_five_minute_midprice_return_bps"],
        "actual_original_endpoint_sha256":endpoint["source_id"],
        "frozen_feature_id":pre["full_feature_id"],
        "registered_shadow_plan_id":decision["local_prospective_plan_id"],
        "matched_original_quote_path_points":
            signed["verified_quote_path_points"],
        "actual_trade_pnl_and_real_model_propensity_proven":False,
        "production_observer_and_root_enrolled":False,
        "native_product_v3_dataset_admitted":False,
        "trading_authority":False,
        "promotion_allowed":False,
    }


def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--observer",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    os.umask(0o077)
    signed=strict(read_private(args.observer,131072))
    result=verify(args.root,signed)
    sha=write_once(args.output,result)
    print(json.dumps({"x230_offline_signature_revalidated":
       result["valid_ed25519_signature_from_x230_test_key"],
       "price_return_bps":result["x230_independently_recomputed_midprice_return_bps"],
       "source_file_prefix_matched":result["real_original_quote_copy_matches_pocket4_prefix"],
       "production_authority":False,
       "receipt_sha256":sha},sort_keys=True))


if __name__=="__main__":
    main()
