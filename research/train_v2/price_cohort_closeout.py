"""Finalize an immutable original PRICE-ONLY cohort without promoting it.

A full closeout is reached only through re-reading the frozen campaign,
original hash-chained sealed quote archive and each original price receipt.
Four correlated future price horizons never become four independent trades.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path

from .contracts import ContractError, digest
from .original_multihorizon import HORIZONS_MIN
from .price_only_cohort_audit import SCHEMA as AUDIT_SCHEMA, audit

SCHEMA = "heptatrader.train-v2-price-cohort-closeout.v1"
OBSERVED = "VERIFIED_OBSERVED_ORIGINAL_PRICE_ONLY"
CENSORED = "VERIFIED_CENSORED_ORIGINAL_PRICE_ONLY"


def require(condition, code):
    if not condition:
        raise ContractError(code)


def dec(value):
    if isinstance(value, bool) or value is None:
        raise ContractError("CLOSEOUT_PRICE_INVALID")
    try:
        result = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ContractError("CLOSEOUT_PRICE_INVALID") from exc
    if not result.is_finite() or abs(result) > Decimal("1000000"):
        raise ContractError("CLOSEOUT_PRICE_INVALID")
    return result


def verify_final_price_audit(record):
    """Validate the original auditor's output. Never treat it as native PnL."""
    require(isinstance(record, dict) and record.get("schema") == AUDIT_SCHEMA
            and record.get("audit_id") ==
            digest({k: v for k, v in record.items() if k != "audit_id"}),
            "CLOSEOUT_AUDIT_IDENTITY")
    require(record.get("declared_source_slots") == 3
            and record.get("declared_source_horizon_cells") == 12
            and record.get("declared_original_decision_slots") == 3
            and record.get("captured_original_decision_slots") == 3
            and record.get("horizons_min") == list(HORIZONS_MIN),
            "CLOSEOUT_FROZEN_SOURCE_DENOMINATOR")
    require(record.get("horizons_share_one_original_decision_per_slot") is True
            and record.get("horizon_labels_are_independent_economic_trades") is False
            and record.get("independently_executable_trading_samples") == 0
            and record.get("actual_trading_reward_samples") == 0
            and record.get("original_model_forecast_count") == 0
            and record.get("signed_independent_observer_count") == 0
            and record.get("executed_strategy_trades_count") == 0
            and record.get("broker_orders_sent") == 0
            and record.get("trade_authority") is False
            and record.get("financial_alpha_qualified") is False
            and record.get("economic_generalization_qualified") is False
            and record.get("automatic_model_selection") is False,
            "CLOSEOUT_FINANCIAL_AUTHORITY_NOT_ALLOWED")
    people = record.get("distinct_original_decision_receipts")
    require(isinstance(people, list) and len(people) == 3
            and [p.get("slot") for p in people] == [0, 1, 2]
            and len({p.get("preoutcome_input_id") for p in people}) == 3
            and len({p.get("original_source_quote_id") for p in people}) == 3
            and all(isinstance(p.get("source_decision_at_ms"), int)
                    for p in people)
            and all(a["source_decision_at_ms"] < b["source_decision_at_ms"]
                    for a, b in zip(people, people[1:])),
            "CLOSEOUT_SOURCE_DECISIONS_NOT_DISTINCT")
    rows = record.get("all_slots")
    require(isinstance(rows, list) and len(rows) == 3,
            "CLOSEOUT_SLOT_ROWS")
    moves, censored = [], 0
    for slot, row in enumerate(rows):
        require(row.get("slot") == slot
                and row.get("slot_state") == "ORIGINAL_PREOUTCOME_INPUT_SEALED"
                and row.get("original_input_id") ==
                    people[slot]["preoutcome_input_id"]
                and row.get("original_source_quote_id") ==
                    people[slot]["original_source_quote_id"],
                "CLOSEOUT_SOURCE_IDENTITY")
        outcomes = row.get("outcomes")
        require(isinstance(outcomes, list) and len(outcomes) == 4
                and [x.get("horizon_min") for x in outcomes] == list(HORIZONS_MIN),
                "CLOSEOUT_HORIZON_SCOPE")
        for obs in outcomes:
            require(obs.get("state") in (OBSERVED, CENSORED)
                    and obs.get("economically_qualified") is False,
                    "CLOSEOUT_NOT_ALL_ORIGINAL_LABELS_FINAL")
            if obs["state"] == OBSERVED:
                moves.append(abs(dec(obs.get("price_bps"))))
            else:
                censored += 1
                require(obs.get("price_bps") is None,
                        "CLOSEOUT_CENSORED_NOT_ZERO")
    require(len(moves) + censored == 12
            and record.get("sealed_original_price_horizon_labels") == 12
            and record.get("state_counts") ==
                {k: n for k, n in ((OBSERVED, len(moves)), (CENSORED, censored))
                 if n > 0},
            "CLOSEOUT_FINAL_STATUS_MISMATCH")
    hurdle = dec(record.get("fixed_frozen_stressed_cost_plus_margin_bps"))
    require(hurdle > 0, "CLOSEOUT_FROZEN_HURDLE_INVALID")
    # These are hindsight absolute MID returns. A retrospective direction
    # oracle is not a real predictor or an executable bid/ask fill.
    clearing = sum(move >= hurdle for move in moves)
    largest = max(moves, default=Decimal("0"))
    report = {
        "schema": SCHEMA,
        "closeout_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "frozen_campaign_id": record["campaign_id"],
        "frozen_roster_id": record["roster_id"],
        "verified_price_audit_id": record["audit_id"],
        "original_campaign_sha256": record["campaign_sha256"],
        "original_roster_sha256": record["roster_sha256"],
        "original_sealed_source_fingerprint_sha256":
            record["original_sealed_archive_fingerprint_sha256"],
        "declared_original_source_decisions": 3,
        "verified_distinct_original_source_decisions": 3,
        "correlated_declared_horizon_cells": 12,
        "verified_original_horizon_labels": len(moves) + censored,
        "observed_price_labels": len(moves),
        "explicitly_censored_price_labels": censored,
        "terminal_state": ("ALL_ORIGINAL_PRICES_OBSERVED" if censored == 0
                           else "ORIGINAL_COHORT_CLOSED_WITH_CENSORING"),
        "fixed_original_stress_cost_plus_margin_bps": str(hurdle),
        "largest_abs_hindsight_midprice_move_bps": str(largest),
        "hindsight_midprice_cells_at_or_above_stress_hurdle": clearing,
        "hindsight_midprice_comparison_is_executable_pnl": False,
        "price_labels_are_independent_trading_samples": False,
        "actual_broker_fill_profit_samples": 0,
        "independent_model_forecast_samples": 0,
        "true_behavior_propensity_attested": False,
        "no_trade_reference_net_pnl_usd": "0",
        "economic_alpha_qualified": False,
        "canonical_artifact_owner_selected": False,
        "automatic_model_promotion": False,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
    return {**report, "closeout_id": digest(report)}


def closeout(root):
    # Always rebuild from actual ORIGINAL source; never trust submitted labels.
    return verify_final_price_audit(audit(Path(root)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = closeout(args.root)
    output = args.output
    require(not output.exists() and not output.is_symlink(),
            "CLOSEOUT_CREATE_ONCE_OUTPUT_REQUIRED")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(output, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(output.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    print(json.dumps({
        "terminal_state": result["terminal_state"],
        "original_decisions": result["verified_distinct_original_source_decisions"],
        "verified_price_horizons": result["verified_original_horizon_labels"],
        "price_cells_above_assumed_hurdle":
            result["hindsight_midprice_cells_at_or_above_stress_hurdle"],
        "qualified_financial_alpha": False,
        "orders_sent": 0,
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
