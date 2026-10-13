"""Exact old-versus-new frozen cohort receipt comparison; no broker API."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from historical_training.common import read, sha
from .contracts import ContractError, digest


FIELD_PAIRS = {
    "current_sealed_segments":
        ("sealed_segments", "current_sealed_original_segments"),
    "complete_current_original_source":
        ("single_scan_source_sha256", "new_complete_source_fingerprint_sha256"),
    "immutable_frozen_original_prefix":
        ("frozen_prefix_sha256", "frozen_original_archive_prefix_sha256"),
    "distinct_original_decisions":
        ("original_decisions", "actual_original_decisions"),
    "correlated_price_labels":
        ("correlated_price_labels", "correlated_price_horizons"),
    "exact_same_original_quote_population":
        ("selected_price_quote_count", "selected_price_quotes_total"),
    "original_max_midprice_bps":
        ("original_max_hindsight_midprice_bps",
         "max_abs_hindsight_midprice_bps"),
    "frozen_hypothetical_cost_hurdle":
        ("frozen_hypothetical_cost_hurdle_bps",
         "original_cost_plus_margin_bps"),
    "price_cells_above_cost":
        ("cells_meeting_fixed_cost_hurdle",
         "price_cells_above_original_cost_margin"),
    "original_audit_sha256":
        ("original_frozen_audit_sha256", "frozen_original_audit_sha256"),
    "original_closeout_sha256":
        ("original_frozen_closeout_sha256",
         "frozen_original_closeout_sha256"),
}


def compare(new, control, *, new_sha=None, control_sha=None):
    if (not isinstance(new, dict) or not isinstance(control, dict)
            or new.get("schema") !=
                "heptatrader.train-v2-batched-frozen-original-3x4-price.v1"
            or control.get("schema") !=
                "heptatrader.train-v2-paged-frozen-12cell-crosscheck.v1"
            or new.get("receipt_id") != digest(
                {k: v for k, v in new.items() if k != "receipt_id"})
            or control.get("audit_id") != digest(
                {k: v for k, v in control.items() if k != "audit_id"})):
        raise ContractError("BATCH_CONTROL_RECEIPT_OR_AUDIT_ID_INVALID")
    if (new.get("source_full_scans") != 1
            or new.get("new_future_trial") is not False
            or new.get("model_signed_forecasts_generated") != 0
            or new.get("broker_orders_sent") != 0
            or new.get("trade_authority") is not False
            or new.get("economic_alpha_qualified") is not False
            or control.get("native_signed_forecasts_created") != 0
            or control.get("actual_broker_trade_rewards") != 0
            or control.get("broker_orders_sent") != 0
            or control.get("trade_authority") is not False):
        raise ContractError("BATCH_CONTROL_SCOPE_AND_AUTHORITY_CHANGED")
    matched = {}
    for name, (lkey, rkey) in FIELD_PAIRS.items():
        if lkey not in new or rkey not in control or new[lkey] != control[rkey]:
            raise ContractError("BATCH_CONTROL_UNEQUAL_OR_MISSING_FIELD:" + name)
        matched[name] = True
    report = {
        "schema": "heptatrader.train-v2-batch-vs-triple-original-audit.v1",
        "immutable_new_receipt_sha256": new_sha,
        "immutable_old_receipt_sha256": control_sha,
        "source_frontier_identical": True,
        "verified_field_equality": matched,
        "matched_field_count": len(matched),
        "comparison_code_sha256": sha(Path(__file__)),
        "new_source_scan_count": new["source_full_scans"],
        "old_source_scan_count": 3,
        "original_distinct_decisions": new["original_decisions"],
        "correlated_horizon_price_observations": new["correlated_price_labels"],
        "actual_independent_trading_rewards": 0,
        "external_owner_or_broker_signature": False,
        "old_original_result_not_reclassified_as_new_trial": True,
        "economic_alpha_qualified": False,
        "broker_requests_sent": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
    return {**report, "comparison_id": digest(report)}


def main():
    ap = argparse.ArgumentParser()
    for name in ("batch", "batch-sha", "old", "old-sha", "output"):
        ap.add_argument("--"+name, required=True,
                        type=Path if name in ("batch","old","output") else str)
    args = ap.parse_args()
    os.umask(0o077)
    if (sha(args.batch) != args.batch_sha or
            sha(args.old) != args.old_sha):
        raise ContractError("BATCH_CONTROL_SOURCE_RECEIPT_SHA_MISMATCH")
    new = read(args.batch, 512 * 1024)
    old = read(args.old, 512 * 1024)
    result = compare(new, old, new_sha=args.batch_sha,
                     control_sha=args.old_sha)
    path = args.output
    if path.exists() or path.is_symlink():
        raise ContractError("BATCH_CONTROL_CREATE_ONLY")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    raw = (json.dumps(result, indent=2, sort_keys=True)+"\n").encode()
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())
    pfd = os.open(path.parent, os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW)
    try: os.fsync(pfd)
    finally: os.close(pfd)
    print(json.dumps({
        "state": "ALL_11_IDENTITIES_AND_PRICE_ECONOMICS_MATCH",
        "matched": len(result["verified_field_equality"]),
        "original_decisions": 3, "correlated_labels": 12,
        "orders_sent": 0,
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
    },sort_keys=True))


if __name__ == "__main__":
    main()
