"""Single-scan revalidation of EXISTING frozen 3x4 PAPER price labels.

This is a new isolated policy. It reuses the original strict cohort audit
verbatim through its pager injection, but scans the immutable source only ONCE.
No historical source, code/plan, model, broker or product owner is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from historical_training.common import sha
from .contracts import ContractError, digest
from .paged_price_cohort_replay import (
    audit_existing_frozen_12cell, pinned_receipt,
    LOOKBACK_MS, SOURCE_HORIZON_MS, EXTRA_ENDPOINT_MS,
)
from .paged_batch_original import verify_three_windows_once
from .prospective_multihorizon import _load
from .scheduled_horizon import load_roster
from .sealed_outcome_reconcile import verify_input_and_timer

SCHEMA = "heptatrader.train-v2-batched-frozen-original-3x4-price.v1"


def audit_frozen_batch(campaign_root, old_audit_path, old_audit_sha,
                       old_closeout_path, old_closeout_sha, *,
                       now_ms=None, batched=verify_three_windows_once,
                       original_audit=audit_existing_frozen_12cell):
    root = Path(campaign_root)
    campaign = _load(root)
    roster = load_roster(root/"timers-roster.json")
    old = pinned_receipt(old_audit_path, old_audit_sha,
                         "heptatrader.train-v2-original-price-cohort-audit.v2")
    immutable_closeout = pinned_receipt(
        old_closeout_path, old_closeout_sha,
        "heptatrader.train-v2-price-cohort-closeout.v1")
    if (campaign["campaign_id"] != roster["campaign_id"]
            or old["campaign_id"] != campaign["campaign_id"]):
        raise ContractError("BATCH_COHORT_ORIGINAL_ROSTER_MISMATCH")
    windows = []
    for slot in range(3):
        frozen, timer = verify_input_and_timer(root, slot, campaign, roster)
        decision = frozen["decision_at_ms"]
        if (decision != old["distinct_original_decision_receipts"][slot][
                "source_decision_at_ms"] or
                frozen["original_source_id"] !=
                old["distinct_original_decision_receipts"][slot][
                    "original_source_quote_id"]):
            raise ContractError("BATCH_COHORT_ORIGINAL_SOURCE_DECISION_CHANGED")
        windows.append((decision-LOOKBACK_MS,
                        decision+SOURCE_HORIZON_MS+EXTRA_ENDPOINT_MS))
    monotonic_start = time.monotonic()
    verified = batched(
        campaign["source_root"], campaign["period_sha256"], windows,
        frozen_prefix_segments=old["original_sealed_segment_count"])
    verified_elapsed_ms = (time.monotonic()-monotonic_start)*1000
    if (verified.get("full_original_scan_count") != 1
            or len(verified.get("views", [])) != 3
            or any((view["window_from_ms"], view["window_until_ms"]) != bounds
                   for view, bounds in zip(verified["views"], windows))):
        raise ContractError("BATCH_COHORT_SINGLE_SOURCE_SCAN_REQUIRED")
    catalog = dict(zip(windows, verified["views"]))
    served = set()

    def locked_pager(source_root, period, start, end,
                     *, frozen_prefix_segments=None):
        key = (start, end)
        if (str(source_root) != campaign["source_root"]
                or period != campaign["period_sha256"]
                or frozen_prefix_segments != old["original_sealed_segment_count"]
                or key not in catalog or key in served):
            raise ContractError("BATCH_COHORT_PAGER_SCOPE_OR_REUSE")
        served.add(key)
        return catalog[key]

    strictly_recomputed = original_audit(
        root, old_audit_path, old_audit_sha,
        old_closeout_path, old_closeout_sha,
        now_ms=now_ms, pager=locked_pager)
    if (served != set(windows)
            or strictly_recomputed.get("original_outcome_cells_reverified_exact") != 12
            or strictly_recomputed.get("actual_original_decisions") != 3
            or strictly_recomputed.get("new_complete_source_fingerprint_sha256") !=
                verified["sealed_source_fingerprint_sha256"]
            or strictly_recomputed.get("frozen_original_audit_sha256") !=
                old_audit_sha
            or strictly_recomputed.get("frozen_original_closeout_sha256") !=
                old_closeout_sha
            or strictly_recomputed.get("broker_orders_sent") != 0):
        raise ContractError("BATCH_COHORT_ORIGINAL_AUDIT_DISAGREES")
    # An implementation returning a recomputed valid checksum is not an
    # independent license to change the frozen original economic denominator.
    economic_pairs = (
        ("max_abs_hindsight_midprice_bps",
         "largest_abs_hindsight_midprice_move_bps"),
        ("original_cost_plus_margin_bps",
         "fixed_original_stress_cost_plus_margin_bps"),
        ("price_cells_above_original_cost_margin",
         "hindsight_midprice_cells_at_or_above_stress_hurdle"),
    )
    if any(strictly_recomputed.get(current) != immutable_closeout.get(frozen)
           for current, frozen in economic_pairs):
        raise ContractError("BATCH_COHORT_ORIGINAL_ECONOMICS_WERE_CHANGED")
    if (immutable_closeout.get("verified_distinct_original_source_decisions") != 3
            or immutable_closeout.get("verified_original_horizon_labels") != 12
            or immutable_closeout.get("actual_broker_fill_profit_samples") != 0
            or immutable_closeout.get("automatic_model_promotion") is not False
            or immutable_closeout.get("broker_orders_sent") != 0):
        raise ContractError("BATCH_COHORT_FROZEN_PRICE_NOT_FINANCIAL")
    body = {
        "schema": SCHEMA,
        "original_campaign_id": campaign["campaign_id"],
        "original_frozen_audit_sha256": old_audit_sha,
        "original_frozen_closeout_sha256": old_closeout_sha,
        "single_scan_source_sha256": verified["sealed_source_fingerprint_sha256"],
        "frozen_prefix_sha256": verified["frozen_prefix_sha256"],
        "sealed_segments": verified["verified_sealed_segments"],
        "source_full_scans": 1,
        "source_scan_wall_ms": round(verified_elapsed_ms, 3),
        "exact_original_cohort_replay_id": strictly_recomputed["audit_id"],
        "original_decisions": 3,
        "correlated_price_labels": 12,
        "selected_price_quote_count": verified["selected_quotes"],
        "original_max_hindsight_midprice_bps":
            strictly_recomputed["max_abs_hindsight_midprice_bps"],
        "frozen_hypothetical_cost_hurdle_bps":
            strictly_recomputed["original_cost_plus_margin_bps"],
        "cells_meeting_fixed_cost_hurdle":
            strictly_recomputed["price_cells_above_original_cost_margin"],
        "implementation_sha256": {
            name: sha(Path(__file__).with_name(name))
            for name in ("paged_batch_original.py", "paged_batch_cohort_replay.py",
                         "paged_price_cohort_replay.py", "paged_price_window.py")
        },
        "independent_trade_decisions": 0,
        "actual_fills_and_financing_attested": False,
        "model_signed_forecasts_generated": 0,
        "native_model_training": False,
        "new_future_trial": False,
        "economic_alpha_qualified": False,
        "canonical_owner_admitted": False,
        "broker_requests_sent": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
    return {**body, "receipt_id": digest(body)}


def main():
    ap = argparse.ArgumentParser()
    for key in ("campaign-root", "prior-audit", "prior-audit-sha",
                "prior-closeout", "prior-closeout-sha", "output"):
        ap.add_argument("--"+key, required=True,
                        type=Path if not key.endswith("-sha") else str)
    opts = ap.parse_args()
    os.umask(0o077)
    report = audit_frozen_batch(
        opts.campaign_root, opts.prior_audit, opts.prior_audit_sha,
        opts.prior_closeout, opts.prior_closeout_sha)
    destination = opts.output
    if destination.is_symlink() or destination.exists():
        raise ContractError("BATCH_COHORT_CREATE_ONLY")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (json.dumps(report, sort_keys=True, indent=2)+"\n").encode()
    fd = os.open(destination,
                 os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    parent_fd = os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    print(json.dumps({
        "state":"ONE_SCAN_FROZEN_3X4_PRICE_COHORT_VERIFIED_NO_TRADE",
        "sealed_segments":report["sealed_segments"],
        "source_full_scans":1,
        "source_scan_wall_ms":report["source_scan_wall_ms"],
        "original_decisions":3,
        "correlated_price_labels":12,
        "actual_trade_samples":0,
        "broker_orders_sent":0,
        "sha256":hashlib.sha256(raw).hexdigest(),
    },sort_keys=True))


if __name__ == "__main__":
    main()
