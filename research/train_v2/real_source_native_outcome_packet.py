"""One-time ORIGINAL IB PAPER midprice outcome -> test-only native Observer packet.

The native Rust LedgerWriter schema may accept a typed signed outcome here,
but that outcome is a RESEARCH PRICE LABEL, NEVER after-cost financial reward
or product-root enrolled trusted Observer. The X230 snapshot derives from
the same upstream Pocket4 local source, not an independent IB subscription.
"""
from __future__ import annotations
import argparse
from decimal import Decimal,ROUND_HALF_EVEN
import hashlib
import json
import os
from pathlib import Path
import time

from .live_forward_x230_verify import verify
from .original_policy_audit import write_once
from hepta_decision_attribution import read_private,strict

SCHEMA="heptatrader.train-v2-real-source-crosshost-price-label.v1"


def _read(path:Path)->tuple[dict,bytes]:
    path=Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("MARKET_TEST_OBSERVER_FILE_UNSAFE")
    raw=read_private(path,1024*1024)
    return strict(raw),raw


def build(root:Path)->dict:
    root=Path(root)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("MARKET_TEST_EVIDENCE_ROOT_UNSAFE")
    d,draw=_read(root/"real-shadow-decision-v1.json")
    packet,praw=_read(root/"native-real-quote-packet.json")
    price,raw=_read(root/"real-five-minute-midprice-outcome.json")
    att,_=_read(root/"x230-independent-price-observation.json")
    independent=verify(root,att)
    if (packet["schema"]!="heptatrader.train-v2-real-market-original-shadow-native-test-only-packet.v1"
            or packet["episode_id"]!=d["shadow_episode_id"]
            or packet["shadow_decision_sha256"]!=hashlib.sha256(draw).hexdigest()
            or price["precommitted_decision_sha256"]!=packet["shadow_decision_sha256"]
            or price["feature_id_revalidated_exactly"]!=packet["preoutcome_feature_id"]
            or independent["frozen_feature_id"]!=packet["preoutcome_feature_id"]
            or price["exact_plan_id"]!=packet["plan_id"]
            or price["outcome_due_ms"]!=packet["outcome_due_ms"]
            or price["actual_endpoint_or_censoring_status"]!="OBSERVED"
            or independent["x230_independently_recomputed_midprice_return_bps"]
                !=price["actual_market_mid_price_return_bps"]
            or independent["valid_ed25519_signature_from_x230_test_key"] is not True
            or independent["production_observer_and_root_enrolled"] is not False
            or independent["real_original_quote_copy_matches_pocket4_prefix"] is not True
            or price["actual_order_fills_or_commissions_observed"] is not False
            or d["after_cost_execution_pnl_observed"] is not False):
        raise ValueError("TEST_NATIVE_ORIGINAL_SOURCE_OUTCOME_SCOPE_MISMATCH")
    now=time.time_ns()//1000000
    if now<price["evaluated_at_ms"]:
        raise ValueError("TEST_NATIVE_OUTCOME_PREMATURE")
    endpoint=price["actual_original_endpoint_quote_receipt"]
    if not price["outcome_due_ms"]<=endpoint["event_at_ms"]<=price["outcome_due_ms"]+5000:
        raise ValueError("TEST_NATIVE_OUTCOME_PRICE_ENDPOINT_FUTURE_OR_LATE")
    bps=Decimal(price["actual_market_mid_price_return_bps"])
    if not bps.is_finite() or abs(bps)>1000:
        raise ValueError("TEST_NATIVE_REAL_PRICE_LABEL_MAGNITUDE_UNBOUNDED")
    q32=int((bps*Decimal(1<<32)).quantize(Decimal(1),rounding=ROUND_HALF_EVEN))
    if not -(1<<63)<=q32<(1<<63):
        raise ValueError("TEST_NATIVE_BPS_Q32_OVERFLOW")
    return {
      "schema":SCHEMA,
      "research_only":True,
      "production_observer_enrolled":False,
      "trade_pnl_reported":False,
      "source_shadow_packet_sha256":hashlib.sha256(praw).hexdigest(),
      "shadow_decision_sha256":hashlib.sha256(draw).hexdigest(),
      "episode_id":packet["episode_id"],
      "outcome_due_ms":packet["outcome_due_ms"],
      "decision_at_ms":packet["decision_at_ms"],
      "real_original_quote_source_prefix_checked_by_X230":True,
      "x230_report_ed25519_verified":True,
      "x230_signed_report_sha256":hashlib.sha256(
          (root/"x230-independent-price-observation.json").read_bytes()).hexdigest(),
      "endpoint_quote_receipt":endpoint["source_id"],
      "endpoint_at_ms":endpoint["event_at_ms"],
      "local_price_evaluated_at_ms":price["evaluated_at_ms"],
      "verified_at_ms":now,
      "market_midprice_change_bps":format(bps,"f"),
      "midprice_change_basis_points_q32_raw":q32,
      "actual_observer_independently_subscribed_to_broker":False,
      "original_model_behavior_propensity_qualified":False,
      "native_product_owner_credential_enrolled":False,
      "market_broker_queries_or_orders":0,
      "actual_execution_commission_pnl":False,
      "product_trading_promotion_allowed":False,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args();os.umask(0o077)
    value=build(args.root)
    sha=write_once(args.output,value)
    print(json.dumps({
        "research_only_price_bps":value["market_midprice_change_bps"],
        "q32_midprice_basis_points_raw":value["midprice_change_basis_points_q32_raw"],
        "original_IB_source_recomputed_on_X230":True,
        "actual_net_pnl_proven":False,
        "native_packet_sha256":sha,
    },sort_keys=True))


if __name__=="__main__":
    main()
