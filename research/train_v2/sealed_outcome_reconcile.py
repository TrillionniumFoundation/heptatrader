"""Fail-closed reconciliation of original PAPER price outcomes.

One bounded invocation, only after a presealed price input and genuine
original sealed-source coverage. No broker API, model, strategy, promotion
or automatic event loop. Existing outcome receipts are READ-ONLY.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from hepta_decision_attribution import read_private, strict
from historical_training.common import canonical, sha
from .contracts import ContractError, digest
from .original_multihorizon import HORIZONS_MIN, ordered_quote_index
from .postmodel_requote import load_original_quote_archive
from .prospective_multihorizon import (
    INPUT_SCHEMA, SLOT_COUNT, DECISION_DELAY_MS, LAST_CAPTURE_DELAY_MS,
    _load, _persist,
)
from .prospective_horizon_observe import (
    OUTCOME_SCHEMA, MIN_OUTCOME_GRACE_MS, make_result, observe,
)
from .scheduled_horizon import load_roster, RUN_SCHEMA

SCHEMA = "heptatrader.train-v2-sealed-price-outcome-reconcile.v1"


def require(ok, reason):
    if not ok:
        raise ContractError(reason)


def verify_input_and_timer(root, slot, campaign, roster):
    path = Path(root) / ("slot-%02d.input.json" % slot)
    timer = Path(root) / ("slot-%02d.timer-result.json" % slot)
    require(not path.is_symlink() and path.is_file()
            and not timer.is_symlink() and timer.is_file(),
            "OUTCOME_RECONCILE_ORIGINAL_INPUT_OR_TIMER_MISSING")
    frozen = strict(read_private(path, 128 * 1024))
    result = strict(read_private(timer, 128 * 1024))
    decision = frozen.get("decision_at_ms")
    anchor = campaign["anchors_ms"][slot]
    require(frozen.get("schema") == INPUT_SCHEMA
            and frozen.get("campaign_id") == campaign["campaign_id"]
            and frozen.get("slot") == slot
            and frozen.get("input_id") == digest({
                k: v for k, v in frozen.items() if k != "input_id"})
            and frozen.get("all_future_labels_unobserved") is True
            and frozen.get("forecast_model_run") is False
            and frozen.get("trade_authority") is False
            and frozen.get("broker_orders_sent") == 0
            and type(decision) is int
            and anchor + DECISION_DELAY_MS <= decision
            <= anchor + LAST_CAPTURE_DELAY_MS,
            "OUTCOME_RECONCILE_SOURCE_INPUT_INVALID")
    require(result.get("schema") == RUN_SCHEMA
            and result.get("result_id") == digest({
                k: v for k, v in result.items() if k != "result_id"})
            and result.get("roster_id") == roster["roster_id"]
            and result.get("state") == "SOURCE_INPUT_SEALED_NO_LABEL"
            and result.get("slot") == slot
            and result.get("source_input_sha256") == sha(path)
            and result.get("original_label_assigned") is False
            and result.get("model_inference") is False
            and result.get("broker_orders_sent") == 0
            and result.get("worker_started_at_ms") <= decision
            <= result.get("worker_finished_at_ms")
            <= anchor + LAST_CAPTURE_DELAY_MS,
            "OUTCOME_RECONCILE_TIMER_CHANGED")
    return frozen, result


def classify_source(campaign, frozen, slot, horizon, archive):
    """Compute independently from pre-existing sealed original source only."""
    index = ordered_quote_index(archive["rows"])
    originals = [x for x in index["known"]
                 if x[3] == frozen["original_source_id"]]
    if not originals:
        return "WAIT_INPUT_ORIGINAL_SEGMENT_SEAL", None
    require(len(originals) == 1,
            "OUTCOME_RECONCILE_DUPLICATE_ORIGINAL_SOURCE")
    due = frozen["decision_at_ms"] + horizon * 60000
    if index["known_times"][-1] < due + MIN_OUTCOME_GRACE_MS:
        return "WAIT_END_OF_ORIGINAL_SEALED_COVERAGE", None
    expected = make_result(campaign, frozen, originals[0], horizon, index)
    return "READY_ORIGINAL_SEALED_PRICE_ONLY", expected


def verify_immutable_outcome(existing, expected, *, due, archive):
    """Verify original persisted outcome against later original source replay.

    New source segments may extend the archive fingerprint legitimately, but
    the first immutable original result must not change its PRICE label.
    """
    require(isinstance(existing, dict)
            and existing.get("schema") == OUTCOME_SCHEMA
            and existing.get("result_id") == digest({
                k: v for k, v in existing.items() if k != "result_id"})
            and type(existing.get("observed_at_ms")) is int
            and existing["observed_at_ms"] >= due + MIN_OUTCOME_GRACE_MS
            and type(existing.get("sealed_source_segment_count")) is int
            and 1 <= existing["sealed_source_segment_count"]
            <= archive["sealed_segments"]
            and isinstance(existing.get("original_scanned_source_fingerprint_sha256"), str)
            and len(existing["original_scanned_source_fingerprint_sha256"]) == 64
            and all(c in "0123456789abcdef" for c in
                    existing["original_scanned_source_fingerprint_sha256"]),
            "OUTCOME_RECONCILE_EXISTING_RECEIPT_INVALID")
    require(all(existing.get(key) == value for key, value in expected.items()),
            "OUTCOME_RECONCILE_PREVIOUS_PRICE_OR_SCOPE_CHANGED")
    require(existing.get("trading_authority") is False
            and existing.get("actual_ib_fills_or_slippage_attested") is False
            and existing.get("independent_observer_signed") is False
            and existing.get("canonical_owner_enrolled") is False
            and existing.get("model_selection_permitted") is False
            and existing.get("broker_orders_sent") == 0,
            "OUTCOME_RECONCILE_FALSE_AUTHORIZATION")
    state = "VERIFIED_" + (
        "OBSERVED_ORIGINAL_PRICE_ONLY" if
        existing["outcome_state"] == "OBSERVED_NEW_ORIGINAL_MARKET_PRICE_ONLY"
        else "CENSORED_ORIGINAL_PRICE_ONLY")
    return state


def reconcile(root, slot, horizon_min, *, now_ms=None, commit=False):
    """Safe to invoke repeatedly after due; no polling and no forced labels."""
    campaign = _load(root)
    require(type(slot) is int and 0 <= slot < SLOT_COUNT
            and type(horizon_min) is int and horizon_min in HORIZONS_MIN,
            "OUTCOME_RECONCILE_SCOPE")
    roster = load_roster(Path(root) / "timers-roster.json")
    require(roster["campaign_id"] == campaign["campaign_id"],
            "OUTCOME_RECONCILE_ROSTER_DIFFERENT_CAMPAIGN")
    anchor = campaign["anchors_ms"][slot]
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    require(type(now) is int and now > 0, "OUTCOME_RECONCILE_CLOCK")
    report = {
        "schema": SCHEMA,
        "campaign_id": campaign["campaign_id"],
        "roster_id": roster["roster_id"],
        "slot": slot,
        "horizon_min": horizon_min,
        "as_of_ms": now,
        "input_deadline_ms": anchor + LAST_CAPTURE_DELAY_MS,
        "source_original_no_model": True,
        "after_cost_alpha_claimed": False,
        "model_predictions_created": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
    input_file = Path(root) / ("slot-%02d.input.json" % slot)
    timer_file = Path(root) / ("slot-%02d.timer-result.json" % slot)
    require(not input_file.is_symlink() and not timer_file.is_symlink(),
            "OUTCOME_RECONCILE_SOURCE_OR_TIMER_SYMLINK")
    require(input_file.exists() == timer_file.exists(),
            "OUTCOME_RECONCILE_PARTIAL_SOURCE_OR_TIMER_RECEIPT")
    if not input_file.exists():
        # Absence after the original window is a missed input, not a zero-PnL
        # outcome. No retroactive input creation or score is allowed.
        state = ("FUTURE_OR_OPEN_SOURCE_CAPTURE" if
                 now <= anchor + LAST_CAPTURE_DELAY_MS else
                 "MISSED_INPUT_NOT_A_PRICE_OBSERVATION")
        return {**report, "state": state, "outcome_written": False}
    frozen, _ = verify_input_and_timer(root, slot, campaign, roster)
    due = frozen["outcome_maturity_ms"].get(str(horizon_min))
    require(type(due) is int
            and due == frozen["decision_at_ms"] + horizon_min * 60000,
            "OUTCOME_RECONCILE_MATURITY_CHANGED")
    if now < due + MIN_OUTCOME_GRACE_MS:
        return {**report, "state": "WAIT_ORIGINAL_MATURITY",
                "due_at_ms": due, "outcome_written": False}
    output = Path(root) / ("slot-%02d-h%02d.outcome.json" %
                           (slot, horizon_min))
    require(not output.is_symlink(), "OUTCOME_RECONCILE_OUTPUT_SYMLINK")
    # No latest ACTIVE source and no quote interpolations: same verified
    # original archive parser as the preexisting strict observer.
    archive = load_original_quote_archive(
        campaign["source_root"], campaign["period_sha256"])
    state, expected = classify_source(
        campaign, frozen, slot, horizon_min, archive)
    if expected is None:
        require(not output.exists(),
                "OUTCOME_RECONCILE_EXISTING_WITHOUT_ORIGINAL_SOURCE")
        return {**report, "state": state, "due_at_ms": due,
                "outcome_written": False}
    if output.exists():
        previous = strict(read_private(output, 128 * 1024))
        require(type(previous.get("observed_at_ms")) is int
                and previous["observed_at_ms"] <= now,
                "OUTCOME_RECONCILE_RECEIPT_FROM_FUTURE")
        status = verify_immutable_outcome(previous, expected,
                                          due=due, archive=archive)
        return {**report, "state": status, "due_at_ms": due,
                "outcome_written": False,
                "previous_outcome_sha256": sha(output)}
    if not commit:
        return {**report, "state": "READY_ONLY_NO_WRITE",
                "original_outcome_state": expected["outcome_state"],
                "due_at_ms": due, "outcome_written": False}
    # A single original on-demand observer can create the immutable result.
    # Any missing/late source stays WAIT, and any race is reverified below.
    try:
        observed = observe(Path(root), slot, horizon_min, now_ms=now)
    except ContractError as exc:
        if ("PROSPECTIVE_OUTCOME_ALREADY_FINAL" not in str(exc)
                or not output.is_file() or output.is_symlink()):
            raise
    require(output.is_file() and not output.is_symlink(),
            "OUTCOME_RECONCILE_ORIGINAL_OBSERVER_NOT_COMMITTED")
    previous = strict(read_private(output, 128 * 1024))
    require(type(previous.get("observed_at_ms")) is int
            and previous["observed_at_ms"] <= now,
            "OUTCOME_RECONCILE_RECEIPT_FROM_FUTURE")
    status = verify_immutable_outcome(previous, expected,
                                      due=due, archive=archive)
    return {**report, "state": status, "due_at_ms": due,
            "outcome_written": True,
            "outcome_sha256": sha(output)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--horizon-min", type=int, required=True)
    parser.add_argument("--commit-original-outcome", action="store_true")
    opts = parser.parse_args()
    os.umask(0o077)
    report = reconcile(opts.root, opts.slot, opts.horizon_min,
                       commit=opts.commit_original_outcome)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
