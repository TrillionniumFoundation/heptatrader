"""Read-only two-forecast cohort closure on naturally SEALED PAPER quotes.

This is a POST-HOC diagnostic of TWO already pre-registered individual
price forecasts. It is NOT itself a pre-registered efficacy trial,
signed Canonical Observer, executable-trade score or model promotion.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path

from historical_training.common import sha
from laya_cell.integrity import ContractError, digest
from .native_price_ticks_outcome import (
    assess as independent_outcome, PRIOR_HYPOTHETICAL_STRESS_COST_MARGIN,
)
from .native_price_ticks_two_window import two_original_windows

SCHEMA = "heptatrader.native-price-ticks-pair-original-closeout.v1"
OBSERVED = "OBSERVED_ORIGINAL_PAPER_MID_PRICE_ONLY"
WAITS = frozenset({
    "WAIT_ORIGINAL_SEALED_INPUT_COVERAGE",
    "WAIT_ORIGINAL_SEALED_PRICE_COVERAGE",
})
CENSORED_PREFIX = "CENSORED_"
EXPECTED_OUTCOME_SCHEMA = "heptatrader.native-price-ticks-sealed-outcome.v1"
EXPECTED_FORECAST_SCHEMA = "heptatrader.native-price-ticks-two-window-shadow-audit.v1"


def _safe_decimal(value):
    if isinstance(value, bool) or value is None:
        raise ContractError("TICKS_CLOSEOUT_NONFINITE_PRICE")
    try:
        result = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ContractError("TICKS_CLOSEOUT_NONFINITE_PRICE") from exc
    if not result.is_finite() or abs(result) > Decimal("1000000"):
        raise ContractError("TICKS_CLOSEOUT_NONFINITE_PRICE")
    return result


def audit_pair(first, second, config_file, original_source_root):
    """Verify BOTH original pinned forecasts before looking at source outcome.

    Each individual original outcome validator rereads naturally sealed
    source. The two returned archive identities must match: no silently
    mixing a pre-rotation and a post-rotation quote frontier.
    """
    roots = [Path(first), Path(second)]
    preview = two_original_windows(*roots, config_file)
    if (preview.get("schema") != EXPECTED_FORECAST_SCHEMA
            or preview.get("distinct_original_source_decisions") != 2
            or preview.get("native_future_tick_score_replays") != 2
            or preview.get("source_price_horizons_disjoint") is not True
            or preview.get("broker_orders_sent") != 0
            or preview.get("actual_broker_execution_net_pnl_samples") != 0
            or preview.get("after_cost_alpha_qualified") is not False
            or len(preview.get("forecasts", [])) != 2):
        raise ContractError("TICKS_CLOSEOUT_FORECAST_COHORT_NOT_VERIFIED")
    original = [independent_outcome(root, config_file, original_source_root)
                for root in roots]
    if len(original) != 2:
        raise ContractError("TICKS_CLOSEOUT_OUTCOME_COUNT")
    original_by_plan = {row.get("plan_id"): row for row in original}
    if len(original_by_plan) != 2:
        raise ContractError("TICKS_CLOSEOUT_DUPLICATE_PLAN_OR_OUTCOME")
    expected = {row["plan_id"] for row in preview["forecasts"]}
    if set(original_by_plan) != expected:
        raise ContractError("TICKS_CLOSEOUT_OUTCOME_PLAN_NOT_FROZEN")
    archives = {
        (row.get("original_source_fingerprint_sha256"),
         row.get("original_source_sealed_segments"),
         row.get("active_unsealed_segment_excluded"))
        for row in original
    }
    if len(archives) != 1:
        raise ContractError("TICKS_CLOSEOUT_SOURCE_FRONTIER_CHANGED_DURING_READ")
    snap = next(iter(archives))
    if (not isinstance(snap[0], str) or len(snap[0]) != 64
            or type(snap[1]) is not int or snap[1] < 1
            or type(snap[2]) is not bool):
        raise ContractError("TICKS_CLOSEOUT_SOURCE_FRONTIER_INVALID")
    details = []
    for frozen in preview["forecasts"]:
        row = original_by_plan[frozen["plan_id"]]
        fixed = _safe_decimal(frozen["forecast_mid_bps"])
        if (row.get("schema") != EXPECTED_OUTCOME_SCHEMA
                or row.get("source_quote_receipt") != frozen["original_quote_receipt"]
                or row.get("source_event_at_ms") != frozen["original_quote_event_at_ms"]
                or row.get("original_quote_known_at_ms") != frozen["original_quote_available_at_ms"]
                or row.get("prediction_fsynced_at_ms") != frozen["prediction_fsynced_at_ms"]
                or row.get("frozen_model_sha256") != frozen["frozen_numeric_model_sha256"]
                or row.get("model_score_id") != frozen["score_id"]
                or _safe_decimal(row.get("model_score_fixed_bps")) != fixed
                or row.get("original_price_due_at_ms") != (
                    frozen["original_quote_event_at_ms"] + 300000)
                or row.get("native_score_new_profile_replayed") is not True
                or row.get("native_f64_exact_replay_attested") is not False
                or row.get("broker_orders_sent") != 0
                or row.get("actual_broker_fills") != 0
                or row.get("actual_executed_net_pnl_samples") != 0
                or row.get("model_promotion_permitted") is not False
                or row.get("automatic_trading") is not False
                or row.get("canonical_agentd_owner_enrolled") is not False
                or row.get("original_price_only_not_executable_fill") is not True):
            raise ContractError("TICKS_CLOSEOUT_FROZEN_PREDICTION_OUTCOME_CHANGED")
        state = row.get("state")
        if state not in WAITS and state != OBSERVED and not (
                isinstance(state, str) and state.startswith(CENSORED_PREFIX)):
            raise ContractError("TICKS_CLOSEOUT_UNKNOWN_OR_SILENT_OUTCOME")
        observed = row.get("original_price_change_bps")
        if state == OBSERVED:
            realized = _safe_decimal(observed)
            if row.get("price_direction_match") is not (
                    (fixed > 0) == (realized > 0)
                    if fixed != 0 and realized != 0 else None):
                raise ContractError("TICKS_CLOSEOUT_DIRECTION_MISMATCH")
            if row.get("original_source_sealed_price_observed") is not True:
                raise ContractError("TICKS_CLOSEOUT_PRICE_NOT_SEALED")
            if row.get("absolute_price_move_clears_prior_hypothetical_cost") is not (
                    abs(realized) >= PRIOR_HYPOTHETICAL_STRESS_COST_MARGIN):
                raise ContractError("TICKS_CLOSEOUT_HYPOTHETICAL_COST_FLAG_CHANGED")
        elif (observed is not None or row.get("price_direction_match") is not None
              or row.get("original_source_sealed_price_observed") is not False
              or row.get("absolute_price_move_clears_prior_hypothetical_cost") is not None):
            raise ContractError("TICKS_CLOSEOUT_MISSING_PRICE_WAS_FABRICATED")
        details.append({
            "plan_id": frozen["plan_id"],
            "original_quote_receipt": frozen["original_quote_receipt"],
            "score_id": frozen["score_id"],
            "forecast_bps": str(fixed),
            "outcome_due_ms": row["original_price_due_at_ms"],
            "outcome_state": state,
            "sealed_price_change_bps": str(_safe_decimal(observed)) if
                observed is not None else None,
            "direction_match": row["price_direction_match"],
            "price_move_clears_old_hypothetical_cost": row.get(
                "absolute_price_move_clears_prior_hypothetical_cost"),
            "original_outcome_audit_id": row.get("audit_id"),
            "source_fingerprint_sha256": row["original_source_fingerprint_sha256"],
        })
    states = [row["outcome_state"] for row in details]
    observed = [row for row in details if row["outcome_state"] == OBSERVED]
    censored = [s for s in states if s.startswith(CENSORED_PREFIX)]
    waits = [s for s in states if s in WAITS]
    if waits:
        state = "WAIT_NATURAL_ORIGINAL_SOURCE_SEAL"
    elif censored:
        state = "CENSORED_PRICE_COHORT_NO_EFFICACY_INFERENCE"
    elif len(observed) == 2:
        state = "TWO_SEALED_PRICE_OUTCOMES_ONLY_NO_ALPHA"
    else:
        raise ContractError("TICKS_CLOSEOUT_UNRECOGNIZED_FINAL_STATES")
    comparisons = [r["direction_match"] for r in observed]
    body = {
        "schema": SCHEMA,
        "state": state,
        "exact_frozen_forecast_cohort_id": preview["audit_id"],
        "original_sealed_archive_fingerprint_sha256": snap[0],
        "original_sealed_segments": snap[1],
        "active_unsealed_segment_excluded": snap[2],
        "forecast_count": 2,
        "mature_sealed_price_outcomes": len(observed),
        "censored_price_outcomes": len(censored),
        "awaiting_natural_seal_or_endpoint": len(waits),
        "observed_price_direction_matches": sum(s is True for s in comparisons),
        "observed_price_direction_mismatches": sum(s is False for s in comparisons),
        "prior_assumed_cost_plus_margin_bps": str(
            PRIOR_HYPOTHETICAL_STRESS_COST_MARGIN),
        "observed_price_moves_below_prior_assumed_cost": sum(
            row["price_move_clears_old_hypothetical_cost"] is False
            for row in details),
        "all_price_horizons_disjoint_not_statistically_independent": True,
        "cohort_rule_was_not_pre_registered_as_independent_efficacy_trial": True,
        "price_only_no_broker_fill": True,
        "previous_f64_strict_replay_failures_remain_failures": True,
        "statistical_alpha_qualified": False,
        "actual_executable_cost_and_slippage_verified": False,
        "actual_net_pnl_trade_samples": 0,
        "financial_reward_generated": False,
        "model_promotion_permitted": False,
        "canonical_agentd_owner_enrolled": False,
        "automatic_trading": False,
        "broker_requests_sent": 0,
        "broker_orders_sent": 0,
        "per_forecast": details,
        "cohort_evaluator_sha256": sha(Path(__file__)),
    }
    return {**body, "audit_id": digest(body)}


def main():
    cli = argparse.ArgumentParser()
    for key in ("first", "second", "config", "original-source-root", "output"):
        cli.add_argument("--"+key, required=True, type=Path)
    args = cli.parse_args()
    os.umask(0o077)
    report = audit_pair(args.first, args.second, args.config, args.original_source_root)
    target = args.output
    if target.exists() or target.is_symlink():
        raise ContractError("TICKS_CLOSEOUT_NEW_IMMUTABLE_RECEIPT_REQUIRED")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())
    directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    print(json.dumps({
        "state": report["state"],
        "forecast_count": 2,
        "observed_price_outcomes": report["mature_sealed_price_outcomes"],
        "waiting": report["awaiting_natural_seal_or_endpoint"],
        "censored": report["censored_price_outcomes"],
        "actual_executable_trade_samples": 0,
        "broker_orders_sent": 0,
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
