"""Independent, bounded PAGED replay of the OLD frozen 3 x 4 price cohort.

Re-verifies every original PAPER segment, but only retains three frozen
62-minute windows. Existing 12 original price receipts and prior closeout
are read-only. Not a new model forecast, trade or after-cost alpha test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from historical_training.common import read, sha
from .contracts import ContractError, digest
from .paged_price_window import verify_paged_price_window
from .price_only_cohort_audit import audit
from .price_cohort_closeout import verify_final_price_audit
from .prospective_multihorizon import _load
from .sealed_outcome_reconcile import verify_input_and_timer
from .scheduled_horizon import load_roster

SCHEMA = "heptatrader.train-v2-paged-frozen-12cell-crosscheck.v1"
LOOKBACK_MS = 60000
EXTRA_ENDPOINT_MS = 60000
MAX_SLOT_WINDOWS = 3
SOURCE_HORIZON_MS = 60 * 60000


def require(ok, why):
    if not ok:
        raise ContractError(why)


def pinned_receipt(path, expected_sha, expected_schema):
    path = Path(path)
    require(not path.is_symlink() and path.is_file()
            and isinstance(expected_sha, str) and len(expected_sha) == 64
            and sha(path) == expected_sha,
            "PAGED_COHORT_PRIOR_RECEIPT_CHANGED")
    result = read(path, 128 * 1024)
    require(result.get("schema") == expected_schema, "PAGED_COHORT_PRIOR_SCHEMA")
    key = ("audit_id" if "audit" in expected_schema else "closeout_id")
    require(result.get(key) == digest({k:v for k,v in result.items()
                                     if k != key}),
            "PAGED_COHORT_PRIOR_DIGEST")
    return result


def combine_frozen_windows(windows, original_audit):
    """Pure fail-closed check: three per-slot views of ONE complete source."""
    require(isinstance(windows, list) and len(windows) == MAX_SLOT_WINDOWS,
            "PAGED_COHORT_THREE_SOURCE_WINDOWS_REQUIRED")
    old_segments = original_audit.get("original_sealed_segment_count")
    old_fingerprint = original_audit.get(
        "original_sealed_archive_fingerprint_sha256")
    require(type(old_segments) is int and old_segments > 0
            and isinstance(old_fingerprint, str)
            and len(old_fingerprint) == 64,
            "PAGED_COHORT_ORIGINAL_FROZEN_FRONTIER_INVALID")
    keys = ("sealed_segments", "numeric_segments_total",
            "source_fingerprint_sha256", "source_archive_page_count",
            "source_archive_page_sha256",
            "verified_original_quote_ids", "unsealed_newest_excluded")
    # verified_original_bytes includes the newest ACTIVE numeric file that
    # naturally grows while collector runs. Compare only SEALED source
    # fingerprints/identities for stable cohort membership; the pager has
    # already verified no active quote is admitted to an outcome.
    first = windows[0]
    for number, item in enumerate(windows):
        require(all(item.get(key) == first.get(key) for key in keys),
                "PAGED_COHORT_SOURCE_ROTATED_DURING_THREE_SCANS")
        require(item.get("all_original_numeric_segment_proofs_verified") is True
                and item.get("frozen_original_prefix_segments") == old_segments
                and item.get("frozen_original_prefix_fingerprint_sha256") ==
                    old_fingerprint
                and type(item.get("window_quote_count")) is int
                and item["window_quote_count"] > 0
                and item.get("broker_orders_sent") == 0
                and item.get("trade_authority") is False,
                "PAGED_COHORT_FROZEN_PREFIX_OR_PRICE_SOURCE_INVALID")
        require(item.get("window_until_ms") > item.get("window_from_ms", 0),
                "PAGED_COHORT_WINDOW_SCOPE_INVALID")
        if number:
            require(windows[number-1]["window_until_ms"] <
                    item["window_from_ms"],
                    "PAGED_COHORT_WINDOW_REUSED_OR_OVERLAPPED")
    combined, seen = [], set()
    for item in windows:
        rows = item["rows"]
        require(isinstance(rows, list)
                and len(rows) == item["window_quote_count"],
                "PAGED_COHORT_WINDOW_ROW_COUNT_CHANGED")
        for row in rows:
            require(isinstance(row, (tuple, list)) and len(row) == 5,
                    "PAGED_COHORT_ROW_SHAPE")
            identity = row[3]
            require(identity not in seen, "PAGED_COHORT_DUPLICATE_QUOTE")
            seen.add(identity)
            combined.append(tuple(row))
    combined.sort(key=lambda q: (q[0], q[1], q[3]))
    require(len(combined) <= MAX_SLOT_WINDOWS * 18000,
            "PAGED_COHORT_SELECTED_ROW_BUDGET")
    return {
        "rows": combined,
        "sealed_segments": first["sealed_segments"],
        "source_fingerprint_sha256": first["source_fingerprint_sha256"],
        "source_archive_page_count": first["source_archive_page_count"],
        "source_archive_page_sha256": first["source_archive_page_sha256"],
        "numeric_segments_total": first["numeric_segments_total"],
        "verified_original_bytes": first["verified_original_bytes"],
        "selected_original_quote_count": len(combined),
        "frozen_prefix_segment_count": old_segments,
        "frozen_prefix_sha256": old_fingerprint,
    }


def same_closeout_without_ephemeral_audit_id(left, right):
    """Two separately timed old audits may have unequal audit IDs.

    Require their complete stable economics/authority/denominator sections
    byte-equivalent; NEVER equate newly derived audit ids with an older one.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    dynamic = {"closeout_id", "verified_price_audit_id"}
    return ({k:v for k,v in left.items() if k not in dynamic} ==
            {k:v for k,v in right.items() if k not in dynamic})


def audit_existing_frozen_12cell(campaign_root, old_audit_path, old_audit_sha,
                                 old_closeout_path, old_closeout_sha,
                                 *, now_ms=None, pager=verify_paged_price_window):
    root = Path(campaign_root)
    campaign = _load(root)
    roster = load_roster(root / "timers-roster.json")
    require(roster["campaign_id"] == campaign["campaign_id"],
            "PAGED_COHORT_ROSTER_CHANGED")
    previous = pinned_receipt(
        old_audit_path, old_audit_sha,
        "heptatrader.train-v2-original-price-cohort-audit.v2")
    old_closeout = pinned_receipt(
        old_closeout_path, old_closeout_sha,
        "heptatrader.train-v2-price-cohort-closeout.v1")
    require(previous["campaign_id"] == campaign["campaign_id"]
            and previous["roster_id"] == roster["roster_id"]
            and previous["campaign_sha256"] == sha(root/"campaign.json")
            and previous["roster_sha256"] == sha(root/"timers-roster.json")
            and same_closeout_without_ephemeral_audit_id(
                old_closeout, verify_final_price_audit(previous))
            and previous["sealed_original_price_horizon_labels"] == 12
            and previous["captured_original_decision_slots"] == 3,
            "PAGED_COHORT_PRIOR_FULL_CLOSEOUT_NOT_VERIFIED")
    now = time.time_ns() // 1000000 if now_ms is None else now_ms
    require(type(now) is int and now > 0 and now >=
            previous["actual_read_at_ms"],
            "PAGED_COHORT_CURRENT_CLOCK_INVALID")
    windows = []
    for slot in range(3):
        frozen, timer = verify_input_and_timer(root, slot, campaign, roster)
        decision = frozen["decision_at_ms"]
        require(type(decision) is int
                and timer["worker_finished_at_ms"] <= now
                and decision == previous["distinct_original_decision_receipts"][slot][
                    "source_decision_at_ms"]
                and frozen["original_source_id"] ==
                    previous["distinct_original_decision_receipts"][slot][
                        "original_source_quote_id"],
                "PAGED_COHORT_ORIGINAL_DECISION_CHANGED")
        first, last = (decision - LOOKBACK_MS,
                       decision + SOURCE_HORIZON_MS + EXTRA_ENDPOINT_MS)
        window = pager(campaign["source_root"], campaign["period_sha256"],
                       first, last, frozen_prefix_segments=
                       previous["original_sealed_segment_count"])
        require(window["window_from_ms"] == first
                and window["window_until_ms"] == last
                and any(row[3] == frozen["original_source_id"]
                        for row in window["rows"]),
                "PAGED_COHORT_SOURCE_WINDOW_OR_ORIGINAL_QUOTE")
        windows.append(window)
    archive = combine_frozen_windows(windows, previous)
    fresh = audit(root, now_ms=now,
                  archive_loader=lambda source, period: require_archive(
                      archive, campaign, source, period))
    # The frozen original input, timer, price labels, attribution and
    # censoring semantics MUST be literally unchanged after source growth.
    require(fresh["all_slots"] == previous["all_slots"]
            and fresh["state_counts"] == previous["state_counts"]
            and fresh["distinct_original_decision_receipts"] ==
                previous["distinct_original_decision_receipts"]
            and fresh["sealed_original_price_horizon_labels"] == 12,
            "PAGED_COHORT_OLD_PRICE_OR_DENOMINATOR_CHANGED")
    closeout = verify_final_price_audit(fresh)
    for key in (
        "terminal_state", "verified_distinct_original_source_decisions",
        "verified_original_horizon_labels", "observed_price_labels",
        "explicitly_censored_price_labels", "largest_abs_hindsight_midprice_move_bps",
        "hindsight_midprice_cells_at_or_above_stress_hurdle",
        "fixed_original_stress_cost_plus_margin_bps",
    ):
        require(closeout[key] == old_closeout[key],
                "PAGED_COHORT_FROZEN_CLOSEOUT_ECONOMICS_CHANGED")
    body = {
        "schema": SCHEMA,
        "original_campaign_id": campaign["campaign_id"],
        "review_code_sha256": {
            name: sha(Path(__file__).with_name(name))
            for name in (
                "paged_price_cohort_replay.py", "paged_price_window.py",
                "price_only_cohort_audit.py", "price_cohort_closeout.py",
                "sealed_outcome_reconcile.py", "prospective_horizon_observe.py",
                "original_multihorizon.py",
            )
        },
        "frozen_original_audit_sha256": old_audit_sha,
        "frozen_original_closeout_sha256": old_closeout_sha,
        "frozen_original_archive_prefix_sha256": archive["frozen_prefix_sha256"],
        "frozen_original_archive_prefix_segments": archive["frozen_prefix_segment_count"],
        "new_complete_source_fingerprint_sha256": archive["source_fingerprint_sha256"],
        "current_sealed_original_segments": archive["sealed_segments"],
        "verified_all_numeric_segments": archive["numeric_segments_total"],
        "verified_original_archive_bytes": archive["verified_original_bytes"],
        "verified_original_archive_page_count": archive["source_archive_page_count"],
        "original_source_archive_page_sha256": archive["source_archive_page_sha256"],
        "verified_sealed_original_quote_id_count": windows[0]["verified_original_quote_ids"],
        "active_original_file_bytes_not_frozen_between_scans": True,
        "newly_appended_original_segments_after_freeze":
            archive["sealed_segments"] - archive["frozen_prefix_segment_count"],
        "three_disjoint_verified_price_windows": [{
            "slot": i, "from_ms": w["window_from_ms"],
            "until_ms": w["window_until_ms"],
            "selected_quote_count": w["window_quote_count"],
            "original_full_source_fingerprint_sha256": w["source_fingerprint_sha256"],
        } for i, w in enumerate(windows)],
        "selected_price_quotes_total": archive["selected_original_quote_count"],
        "original_outcome_cells_reverified_exact": 12,
        "actual_original_decisions": 3,
        "correlated_price_horizons": 12,
        "original_horizon_economics_unchanged": True,
        "max_abs_hindsight_midprice_bps":
            closeout["largest_abs_hindsight_midprice_move_bps"],
        "original_cost_plus_margin_bps":
            closeout["fixed_original_stress_cost_plus_margin_bps"],
        "price_cells_above_original_cost_margin":
            closeout["hindsight_midprice_cells_at_or_above_stress_hurdle"],
        "native_signed_forecasts_created": 0,
        "actual_broker_trade_rewards": 0,
        "canonical_owner_enrolled": False,
        "new_forward_trial_created": False,
        "automatic_training": False,
        "model_promotion_allowed": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
    return {**body, "audit_id": digest(body)}


def require_archive(archive, campaign, source, period):
    require(source == campaign["source_root"]
            and period == campaign["period_sha256"],
            "PAGED_COHORT_LOADER_WRONG_SOURCE_SCOPE")
    return archive


def main():
    ap = argparse.ArgumentParser()
    for name in ("root", "prior-audit", "prior-audit-sha",
                 "prior-closeout", "prior-closeout-sha", "output"):
        ap.add_argument("--"+name, required=True,
                        type=Path if not name.endswith("-sha") else str)
    args = ap.parse_args()
    os.umask(0o077)
    receipt = audit_existing_frozen_12cell(
        args.root, args.prior_audit, args.prior_audit_sha,
        args.prior_closeout, args.prior_closeout_sha)
    destination = args.output
    require(not destination.exists() and not destination.is_symlink(),
            "PAGED_COHORT_CREATE_ONLY")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (json.dumps(receipt, sort_keys=True, indent=2)+"\n").encode()
    fd = os.open(destination, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as file:
        file.write(raw);file.flush();os.fsync(file.fileno())
    parent = os.open(destination.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(parent)
    finally: os.close(parent)
    print(json.dumps({
        "state":"OLD_3x4_PRICE_LABELS_PAGED_REPLAY_VERIFIED_NO_ECONOMIC_CLAIM",
        "original_source_decisions":3,
        "correlated_price_labels":12,
        "current_sealed_original_segments":receipt["current_sealed_original_segments"],
        "old_frozen_segment_count":receipt["frozen_original_archive_prefix_segments"],
        "new_forecasts":0,
        "actual_trade_samples":0,
        "broker_orders_sent":0,
        "sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True))

if __name__ == "__main__":
    main()
