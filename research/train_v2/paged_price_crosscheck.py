"""Cross-check ORIGINAL sealed Native price labels through a PAGED source window.

A post-hoc reader only. No old forecast, sealed price outcome, as-of plan or
model is rewritten. The shared original full-inventory fingerprint must equal
the earlier immutable outcome's fingerprint exactly.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path

from historical_training.common import read, sha
from laya_cell.integrity import ContractError, digest
from .paged_price_window import verify_paged_price_window
from .original_multihorizon import ordered_quote_index, label_after

SCHEMA = "heptatrader.train-v2-paged-original-price-crosscheck.v1"
PRICE_ONLY = "OBSERVED_ORIGINAL_PAPER_MID_PRICE_ONLY"
FROM_LOOKBACK_MS = 60000
END_GRACE_MS = 5000


def _safe_decimal(value):
    if isinstance(value, bool) or value is None:
        raise ContractError("PAGED_PRICE_CHECK_NONFINITE")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ContractError("PAGED_PRICE_CHECK_NONFINITE") from exc
    if not amount.is_finite() or abs(amount) > Decimal("1000000"):
        raise ContractError("PAGED_PRICE_CHECK_NONFINITE")
    return amount


def inspect_prior_outcome(outcome_path, expected_sha256, source_root, period):
    source = Path(outcome_path)
    if source.is_symlink() or not source.is_file() or sha(source) != expected_sha256:
        raise ContractError("PAGED_PRICE_PRIOR_OUTCOME_BYTES_CHANGED")
    original = read(source, 64 * 1024)
    if (original.get("schema") != "heptatrader.native-price-ticks-sealed-outcome.v1"
            or original.get("state") != PRICE_ONLY
            or original.get("original_source_sealed_price_observed") is not True
            or original.get("original_price_only_not_executable_fill") is not True
            or original.get("actual_broker_fills") != 0
            or original.get("actual_executed_net_pnl_samples") != 0
            or original.get("native_f64_exact_replay_attested") is not False
            or original.get("model_promotion_permitted") is not False
            or original.get("automatic_trading") is not False
            or original.get("canonical_agentd_owner_enrolled") is not False
            or original.get("broker_orders_sent") != 0):
        raise ContractError("PAGED_PRICE_OLD_OUTCOME_NOT_RESEARCH_ONLY")
    known = original.get("original_quote_known_at_ms")
    observed = original.get("source_event_at_ms")
    due = original.get("original_price_due_at_ms")
    if (type(known) is not int or type(observed) is not int
            or type(due) is not int or not 0 < observed <= known < due
            or due != observed + 300000
            or not isinstance(original.get("source_quote_receipt"), str)
            or len(original["source_quote_receipt"]) != 64):
        raise ContractError("PAGED_PRICE_PRIOR_HORIZON_CHANGED")
    original_count = original.get("original_source_sealed_segments")
    window = verify_paged_price_window(
        source_root, period, known - FROM_LOOKBACK_MS, due + END_GRACE_MS,
        frozen_prefix_segments=original_count)
    if (window["frozen_original_prefix_fingerprint_sha256"] !=
            original.get("original_source_fingerprint_sha256")
            or window["frozen_original_prefix_segments"] != original_count):
        raise ContractError("PAGED_PRICE_ORIGINAL_SOURCE_FRONTIER_CHANGED")
    indexed = ordered_quote_index(window["rows"])
    candidates = [
        q for q in indexed["known"]
        if q[3] == original["source_quote_receipt"]
    ]
    if len(candidates) != 1:
        raise ContractError("PAGED_PRICE_PRIOR_INPUT_NOT_UNIQUE")
    point = candidates[0]
    if (point[0] != known or point[1] != observed):
        raise ContractError("PAGED_PRICE_PRIOR_SOURCE_TIME_CHANGED")
    result, price = label_after(indexed, point, observed, 300000)
    if result != "OBSERVED_RETROSPECTIVE_PRICE_ONLY" or price is None:
        raise ContractError("PAGED_PRICE_ORIGINAL_OUTCOME_NOT_REPRODUCED")
    original_amount = _safe_decimal(original.get("original_price_change_bps"))
    if price != original_amount:
        raise ContractError("PAGED_PRICE_PRIOR_VALUE_CHANGED")
    frozen_score = _safe_decimal(original.get("model_score_fixed_bps"))
    matching = ((frozen_score > 0) == (price > 0)
                if frozen_score != 0 and price != 0 else None)
    if original.get("price_direction_match") is not matching:
        raise ContractError("PAGED_PRICE_ORIGINAL_DIRECTION_CHANGED")
    return {
        "plan_id": original["plan_id"],
        "original_quote_receipt": original["source_quote_receipt"],
        "original_outcome_sha256": expected_sha256,
        "original_archive_fingerprint": window["source_fingerprint_sha256"],
        "original_archive_sealed_segments": window["sealed_segments"],
        "original_frozen_prefix_fingerprint":
            window["frozen_original_prefix_fingerprint_sha256"],
        "original_frozen_prefix_sealed_segments": original_count,
        "naturally_appended_sealed_successors_verified":
            window["sealed_successors_after_frozen_prefix"],
        "verified_all_numeric_original_segments": window["numeric_segments_total"],
        "verified_archived_page_count": window["source_archive_page_count"],
        "selected_original_window_quotes": window["window_quote_count"],
        "frozen_score_bps": str(frozen_score),
        "old_price_change_bps": str(original_amount),
        "new_paged_price_change_bps": str(price),
        "old_price_label_exactly_reproduced": True,
        "direction_match": matching,
        "actual_broker_fills": 0,
        "economic_promotion_allowed": False,
    }


def audit_pair(first, first_sha, second, second_sha, source_root, period):
    one = inspect_prior_outcome(first, first_sha, source_root, period)
    two = inspect_prior_outcome(second, second_sha, source_root, period)
    if (one["plan_id"] == two["plan_id"]
            or one["original_quote_receipt"] == two["original_quote_receipt"]
            or one["original_archive_fingerprint"] != two["original_archive_fingerprint"]
            or one["verified_archived_page_count"] != two["verified_archived_page_count"]
            or one["original_archive_sealed_segments"] != two["original_archive_sealed_segments"]):
        raise ContractError("PAGED_PRICE_PAIR_SOURCE_FRONTIER_OR_IDENTITY")
    receipt = {
        "schema": SCHEMA,
        "state": "TWO_PRIOR_PRICE_LABELS_EXACTLY_REPRODUCED_RESEARCH_ONLY",
        "source_period_sha256": period,
        "original_source_full_snapshot_sha256": one["original_archive_fingerprint"],
        "original_source_total_sealed_segments": one["original_archive_sealed_segments"],
        "historical_outcome_receipts_unchanged": True,
        "earlier_strict_f64_failures_not_requalified": True,
        "new_native_inference_executed": False,
        "new_forecast_registered": False,
        "original_predictions_resealed": False,
        "statistically_independent_trading_outcomes": 0,
        "actually_executed_trades": 0,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
        "rows": [one, two],
    }
    return {**receipt, "audit_id": digest(receipt)}


def main():
    import argparse
    p = argparse.ArgumentParser()
    for key in ("source-root", "period", "first", "first-sha",
                "second", "second-sha", "output"):
        p.add_argument("--"+key, required=True, type=Path if key not in
                       ("first-sha", "second-sha") else str)
    args = p.parse_args()
    os.umask(0o077)
    report = audit_pair(
        args.first, args.first_sha, args.second, args.second_sha,
        args.source_root, sha(args.period))
    destination = args.output
    if destination.exists() or destination.is_symlink():
        raise ContractError("PAGED_PRICE_IMMUTABLE_NEW_DESTINATION_REQUIRED")
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    raw = (json.dumps(report, sort_keys=True, indent=2)+"\n").encode()
    fd = os.open(destination, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(destination.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)
    print(json.dumps({
        "state": report["state"],
        "original_price_labels_reproduced": len(report["rows"]),
        "original_segment_count": report["original_source_total_sealed_segments"],
        "new_forecasts": 0,
        "broker_orders_sent": 0,
        "receipt_sha256": hashlib.sha256(raw).hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    main()
