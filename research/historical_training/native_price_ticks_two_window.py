"""Two disjoint ORIGINAL input/horizon Native tick forecasts, read-only.

Demonstrates 2 distinct predeclared model observations, not independent
execution PnL, a canonical Agentd ACK or a promoted profitable strategy.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

from historical_training.common import read, sha
from laya_cell.integrity import ContractError, digest
from .laya_native_price_ticks_forward import _directory, _plan, verify

SCHEMA = "heptatrader.native-price-ticks-two-window-shadow-audit.v1"
FROZEN_STRESS_COST_MARGIN_BPS = Decimal("10.9133557")
HORIZON_MS = 300000


def two_original_windows(first, second, config_file):
    roots = [Path(first), Path(second)]
    if roots[0] == roots[1] or any(root.is_symlink() for root in roots):
        raise ContractError("TICKS_TWO_WINDOW_IDENTICAL_OR_UNSAFE_ROOT")
    items = []
    for root in roots:
        root = _directory(root)
        verified = verify(root, config_file)
        if (verified.get("new_profile_score_reproduced") is not True
                or verified.get("original_source_rechecked") is not True
                or verified.get("original_f64_bitwise_equal_attested") is not False
                or verified.get("broker_orders_sent") != 0):
            raise ContractError("TICKS_TWO_WINDOW_NO_EXACT_NEW_PROFILE_REPLAY")
        plan = _plan(root)
        inp = read(root / "input.json", 160 * 1024)
        pred = read(root / "prediction.json", 65536)
        ack = read(root / "commit.json", 65536)
        if (plan["forecast_profile"] != ack["future_fixed_score_profile"]
                or plan["target_horizon_sec"] != 300
                or not plan["registered_at_ms"] < inp["quote_known_at_ms"]
                <= inp["source_asof_ms"] <= inp["feature_decision_ms"]
                <= pred["prediction_ready_at_ms"] <= ack["post_fsync_at_ms"]
                <= plan["window_end_ms"]
                or inp["quote_event_at_ms"] != pred["anchor_ms"]
                or pred["source_input_sha256"] != sha(root / "input.json")
                or ack["prediction_sha256"] != sha(root / "prediction.json")
                or ack["input_sha256"] != sha(root / "input.json")
                or plan["no_trade_policy"] != "NO_ACTION_NO_BROKER_ORDER"
                or any(x.get("broker_orders_sent") != 0 for x in (inp, pred, ack))):
            raise ContractError("TICKS_TWO_WINDOW_TIMING_OR_AUTHORITY_CHANGED")
        ticks = pred["future_fixed_score"]["scaled_bps_ticks"]
        if type(ticks) is not int:
            raise ContractError("TICKS_TWO_WINDOW_INVALID_FROZEN_SCORE")
        predicted = Decimal(ticks) / Decimal(100000000)
        items.append({
            "root": str(root),
            "plan_id": plan["plan_id"],
            "registered_at_ms": plan["registered_at_ms"],
            "slot_start_ms": plan["slot_start_ms"],
            "deadline_ms": plan["window_end_ms"],
            "original_quote_event_at_ms": inp["quote_event_at_ms"],
            "original_quote_available_at_ms": inp["quote_known_at_ms"],
            "original_quote_receipt": inp["quote_receipt"],
            "sample_id": inp["sample_id"],
            "feature_decision_at_ms": inp["feature_decision_ms"],
            "prediction_fsynced_at_ms": ack["post_fsync_at_ms"],
            "forecast_deadline_margin_ms":
                plan["window_end_ms"] - ack["post_fsync_at_ms"],
            "frozen_numeric_model_id": plan["baseline_model_id"],
            "frozen_numeric_model_sha256": plan["baseline_model_sha256"],
            "native_binary_sha256": plan["original_native_binary_sha256"],
            "score_id": pred["future_fixed_score"]["score_id"],
            "forecast_mid_bps": str(predicted),
            "hypothetical_absolute_gross_below_cost_plus_margin":
                abs(predicted) < FROZEN_STRESS_COST_MARGIN_BPS,
            "plan_sha256": sha(root / "plan.json"),
            "input_sha256": sha(root / "input.json"),
            "prediction_sha256": sha(root / "prediction.json"),
            "commit_sha256": sha(root / "commit.json"),
        })
    items.sort(key=lambda x: x["slot_start_ms"])
    a, b = items
    if (a["root"] == b["root"]
            or a["plan_id"] == b["plan_id"]
            or a["original_quote_receipt"] == b["original_quote_receipt"]
            or a["sample_id"] == b["sample_id"]
            or a["score_id"] == b["score_id"]
            or a["frozen_numeric_model_sha256"] !=
               b["frozen_numeric_model_sha256"]
            or a["native_binary_sha256"] != b["native_binary_sha256"]
            or a["deadline_ms"] >= b["slot_start_ms"]
            or a["original_quote_event_at_ms"] + HORIZON_MS >=
               b["original_quote_event_at_ms"]):
        raise ContractError("TICKS_TWO_WINDOW_NOT_DISJOINT_OR_FROZEN")
    report = {
        "schema": SCHEMA,
        "state": "TWO_DISJOINT_NEW_NATIVE_TICKS_PRICE_FORECASTS_ONLY",
        "distinct_original_source_decisions": 2,
        "native_future_tick_score_replays": 2,
        "source_price_horizons_disjoint": True,
        "actual_original_quote_price_outcomes_confirmed": 0,
        "actual_broker_execution_net_pnl_samples": 0,
        "statistically_independent_trading_reward_samples": 0,
        "hypothetical_prior_stress_cost_plus_margin_bps":
            str(FROZEN_STRESS_COST_MARGIN_BPS),
        "both_forecast_magnitudes_below_hypothetical_cost":
            all(item["hypothetical_absolute_gross_below_cost_plus_margin"]
                for item in items),
        "model_price_predictions_are_trade_actions": False,
        "legacy_bitwise_f64_predictions_requalified": False,
        "canonical_agentd_owner_enrolled": False,
        "actual_broker_fills_attested": False,
        "actual_slippage_attested": False,
        "after_cost_alpha_qualified": False,
        "automatic_trading": False,
        "automatic_model_selection": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
        "code_sha256": sha(Path(__file__)),
        "forecasts": items,
    }
    return {**report, "audit_id": digest(report)}


def main():
    p = argparse.ArgumentParser()
    for arg in ("first", "second", "config", "output"):
        p.add_argument("--" + arg, type=Path, required=True)
    a = p.parse_args()
    os.umask(0o077)
    report = two_original_windows(a.first, a.second, a.config)
    out = a.output
    if out.exists() or out.is_symlink():
        raise ContractError("TICKS_TWO_WINDOW_NEW_IMMUTABLE_OUTPUT_REQUIRED")
    out.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    raw = (json.dumps(report, sort_keys=True, indent=2)+"\n").encode()
    fd = os.open(out, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    dirfd = os.open(out.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)
    print(json.dumps({
        "state": report["state"],
        "model_outputs": report["native_future_tick_score_replays"],
        "price_horizons_nonoverlapping":
            report["source_price_horizons_disjoint"],
        "hypothetical_pretrading_cost_abstain":
            report["both_forecast_magnitudes_below_hypothetical_cost"],
        "actual_net_pnl_samples": 0,
        "broker_orders_sent": 0,
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
