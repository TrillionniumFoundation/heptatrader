"""Two genuinely preregistered Native/Numeric forecasts: original-price outcomes.

No broker calls or orders; no new predictions or model fitting. Actual future
labels come ONLY from naturally sealed, hash-verified PAPER quote segments.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import time

from historical_training.common import read, sha
from laya_cell.integrity import ContractError, digest
from .laya_native_price_forward import verify, _plan, _config, predict_point
from train_v2.postmodel_requote import load_original_quote_archive
from train_v2.original_multihorizon import ordered_quote_index, label_after

SCHEMA = "heptatrader.native-price-prospective-original-outcomes.v1"
HORIZON_MS = 300000
GRACE_MS = 5000
ASSUMED_STRESS_COST_MARGIN_BPS = Decimal("10.9133557")
ROSTER = ("real-future-slot-01", "real-future-slot-02")


def fail(condition, code):
    if not condition:
        raise ContractError(code)


def _read_root(root):
    root = Path(root)
    fail(root.is_absolute() and ".." not in root.parts and not root.is_symlink()
         and root.is_dir(), "NATIVE_OUTCOME_ROOT_INVALID")
    st = root.stat()
    fail(st.st_uid == os.geteuid() and st.st_mode & 0o077 == 0,
         "NATIVE_OUTCOME_ROOT_NOT_PRIVATE")
    return root


def _one(root, config_path):
    root = _read_root(root)
    exact_native = True
    ulp_distance = 0
    try:
        checked = verify(root, config_path)
        fail(checked.get("native_model_output_recomputed") is True
             and checked.get("full_original_source_reverified") is True,
             "NATIVE_OUTCOME_ORIGINAL_MODEL_NOT_REPRODUCED")
    except ContractError as exc:
        # The original STRICT verifier must remain unchanged. It verifies
        # original source/plan/model hashes and full as-of replay BEFORE its
        # final bitwise-float comparison. A 1..4 ULP diagnostic is not a
        # retroactive upgrade of that original frozen acceptance protocol.
        if str(exc) != "NATIVE_FUTURE_MODEL_OUTPUT_MISMATCH":
            raise
        frozen = _plan(root)
        cfg = _config(config_path, frozen)
        stored = read(root / "prediction.json", 65536)
        original = read(root / "input.json", 131072)
        actual, native_source_sha = predict_point(cfg, frozen, original["sample"])
        expected = stored["frozen_numeric_model_expected_mid_bps"]
        fail(type(expected) is float and math.isfinite(expected)
             and math.isfinite(actual)
             and stored["native_source_sha"] == native_source_sha
             and math.ulp(expected) > 0,
             "NATIVE_OUTCOME_ROUNDOFF_NOT_ISOLATED")
        ulp_distance = abs(actual - expected) / math.ulp(expected)
        fail(0 < ulp_distance <= 4,
             "NATIVE_OUTCOME_NOT_A_BOUNDED_FLOAT_DIAGNOSTIC")
        exact_native = False
        checked = {
            "full_original_source_reverified": True,
            "native_model_output_recomputed": False,
            "original_strict_bitwise_verification_failed": True,
            "engineering_bounded_replay_only": True,
        }
    plan = read(root / "plan.json", 65536)
    src = read(root / "input.json", 131072)
    forecast = read(root / "prediction.json", 65536)
    commit = read(root / "commit.json", 65536)
    for item in (plan, src, forecast, commit):
        fail(item.get("plan_id") == plan["plan_id"], "NATIVE_OUTCOME_PLAN_IDENTITY")
    fail(plan.get("target_horizon_sec") == 300
         and forecast.get("target_horizon_sec") == 300
         and forecast.get("new_prospective_numeric_prediction") is True
         and forecast.get("laya_model_inference") is False
         and forecast.get("automatic_trading") is False
         and plan.get("trade_authority") is False
         and plan.get("broker_orders_sent") == 0
         and commit.get("broker_orders_sent") == 0
         and plan.get("canonical_agentd_owner_enrolled") is False,
         "NATIVE_OUTCOME_NO_TRADING_OR_CANONICAL_AUTHORITY")
    fail(plan["registered_at_ms"] < src["quote_known_at_ms"]
         <= src["source_asof_ms"] <= src["feature_decision_ms"]
         <= forecast["prediction_ready_at_ms"] <= commit["post_fsync_at_ms"]
         <= plan["window_end_ms"],
         "NATIVE_OUTCOME_PREDECLARED_TIMING")
    fail(src["quote_event_at_ms"] == forecast["anchor_ms"]
         and src["quote_receipt"]
         and forecast["source_input_sha256"] == sha(root / "input.json")
         and commit["prediction_sha256"] == sha(root / "prediction.json")
         and commit["input_sha256"] == sha(root / "input.json"),
         "NATIVE_OUTCOME_ORIGINAL_SOURCE_BINDING")
    return {"root": root, "plan": plan, "input": src,
            "forecast": forecast, "commit": commit,
            "verified": checked,
            "native_strict_bitwise_replay_passed": exact_native,
            "engineering_ulp_distance": ulp_distance}


def _score(item, index):
    source = item["input"]
    prediction = item["forecast"]
    original = [q for q in index["known"] if q[3] == source["quote_receipt"]]
    fail(len(original) == 1, "NATIVE_OUTCOME_ORIGINAL_QUOTE_NOT_UNIQUE")
    reference = original[0]
    fail((reference[0], reference[1], reference[3]) ==
         (source["quote_known_at_ms"], source["quote_event_at_ms"], source["quote_receipt"]),
         "NATIVE_OUTCOME_ORIGINAL_QUOTE_TIME_OR_ID_CHANGED")
    fail(reference[4] == Decimal(str(source["feature_state"]["reference_mid"])),
         "NATIVE_OUTCOME_REFERENCE_PRICE_CHANGED")
    due = prediction["anchor_ms"] + HORIZON_MS
    predicted = Decimal(str(prediction["frozen_numeric_model_expected_mid_bps"]))
    fail(predicted.is_finite() and abs(predicted) <= Decimal("1000000"),
         "NATIVE_OUTCOME_PREDICTION_INVALID")
    entry = {
        "plan_id": item["plan"]["plan_id"],
        "sample_id": prediction["sample_id"],
        "quote_receipt": source["quote_receipt"],
        "original_quote_known_at_ms": source["quote_known_at_ms"],
        "anchor_ms": prediction["anchor_ms"],
        "feature_decision_at_ms": source["feature_decision_ms"],
        "prediction_fsynced_at_ms": item["commit"]["post_fsync_at_ms"],
        "prediction_file_sha256": sha(item["root"] / "prediction.json"),
        "commit_file_sha256": sha(item["root"] / "commit.json"),
        "frozen_model_sha256": prediction["baseline_model_sha256"],
        "native_binary_sha256": prediction["native_binary_sha256"],
        "native_strict_bitwise_replay_passed": item[
            "native_strict_bitwise_replay_passed"],
        "engineering_numeric_replay_ulp_distance": str(
            item["engineering_ulp_distance"]),
        "numeric_engineering_tolerance_is_product_qualification": False,
        "horizon_ms": HORIZON_MS,
        "original_price_due_at_ms": due,
        "predicted_mid_return_bps": str(predicted),
        "prediction_precedes_original_outcome": item["commit"]["post_fsync_at_ms"] < due,
        "trading_action": "NO_TRADE",
        "broker_orders_sent": 0,
    }
    fail(entry["prediction_precedes_original_outcome"],
         "NATIVE_OUTCOME_PREDICTION_NOT_BEFORE_DUE")
    if index["known_times"][-1] < due + GRACE_MS:
        return {**entry, "state": "WAIT_ORIGINAL_SEALED_PRICE_COVERAGE",
                "original_price_change_bps": None,
                "price_direction_match": None}
    state, price_return = label_after(index, reference, prediction["anchor_ms"], HORIZON_MS)
    if price_return is None:
        fail(state.startswith("CENSORED_"),
             "NATIVE_OUTCOME_SOURCE_STATUS_INVALID")
        return {**entry, "state": state,
                "original_price_change_bps": None,
                "price_direction_match": None}
    realized = Decimal(str(price_return))
    fail(realized.is_finite(), "NATIVE_OUTCOME_NONFINITE_REALIZED_PRICE")
    # These two overlapping forecast horizons are NOT independent trades.
    matching = ((predicted > 0 and realized > 0)
                or (predicted < 0 and realized < 0))
    return {**entry,
            "state": "OBSERVED_ORIGINAL_PAPER_MID_PRICE_ONLY",
            "original_price_change_bps": str(realized),
            "forecast_minus_realized_bps": str(predicted-realized),
            "price_direction_match": matching,
            "absolute_price_move_clears_hypothetical_stress_hurdle": (
                abs(realized) >= ASSUMED_STRESS_COST_MARGIN_BPS),
            "original_broker_fill_or_slippage_attested": False,
            "actual_trading_return_attested": False}


def evaluate(first, second, config_path, source_root):
    items = [_one(first, config_path), _one(second, config_path)]
    p, q = items
    fail(p["root"] != q["root"]
         and p["plan"]["plan_id"] != q["plan"]["plan_id"]
         and p["input"]["quote_receipt"] != q["input"]["quote_receipt"]
         and p["plan"]["window_end_ms"] < q["plan"]["slot_start_ms"]
         and p["forecast"]["baseline_model_sha256"] ==
             q["forecast"]["baseline_model_sha256"]
         and p["plan"]["original_period_sha256"] ==
             q["plan"]["original_period_sha256"],
         "NATIVE_OUTCOME_DISTINCT_SLOTS_MODEL_PERIOD_REQUIRED")
    source_root = _read_root(source_root)
    fail(all(Path(row["plan"]["original_feed_directory"]) == source_root
             for row in items),
         "NATIVE_OUTCOME_ORIGINAL_SOURCE_ROOT_CHANGED")
    archive = load_original_quote_archive(
        source_root, p["plan"]["original_period_sha256"])
    idx = ordered_quote_index(archive["rows"])
    scores = [_score(row, idx) for row in items]
    waiting = any(x["state"].startswith("WAIT_") for x in scores)
    observed = sum(x["state"]=="OBSERVED_ORIGINAL_PAPER_MID_PRICE_ONLY"
                   for x in scores)
    censored = len(scores)-observed-sum(x["state"].startswith("WAIT_")
                                     for x in scores)
    overlap = (scores[0]["original_price_due_at_ms"] >
               scores[1]["anchor_ms"])
    strict_all = all(row["native_strict_bitwise_replay_passed"]
                     for row in items)
    report = {
        "schema": SCHEMA,
        "state": ("WAIT_ORIGINAL_SEALED_SOURCE_DO_NOT_SCORE_MISSING"
                  if waiting else
                  ("ALL_ORIGINAL_PRICE_OUTCOMES_TERMINAL"
                   if strict_all else
                   "PRICE_OUTCOMES_OBSERVED_NATIVE_EXACT_REPLAY_HOLD")),
        "all_original_strict_native_bitwise_replays_passed": strict_all,
        "engineering_roundoff_diagnostic_does_not_upgrade_frozen_plan": True,
        "original_future_forecasts": 2,
        "model_identity": p["forecast"]["baseline_model_sha256"],
        "distinct_original_input_receipts": 2,
        "observed_original_paper_price_outcomes": observed,
        "censored_original_paper_outcomes": censored,
        "price_forecast_horizons_overlap": overlap,
        "statistically_independent_trading_samples": 0,
        "auditor_and_original_model_code_sha256": {
            name: sha(Path(__file__).with_name(name))
            for name in (
                "native_price_forecast_outcome.py", "native_score_ticks.py",
                "laya_native_price_forward.py",
            )
        },
        "original_price_source_segments_sealed": archive["sealed_segments"],
        "original_source_fingerprint_sha256": archive["source_fingerprint_sha256"],
        "active_unsealed_original_segment_excluded": archive["unsealed_newest_excluded"],
        "hypothetical_stress_cost_plus_margin_bps": str(
            ASSUMED_STRESS_COST_MARGIN_BPS),
        "all_price_movements_below_hypothetical_hurdle": (
            not waiting and censored==0 and
            all(not x["absolute_price_move_clears_hypothetical_stress_hurdle"]
                for x in scores)),
        "new_model_training": False,
        "laya_gpu_inference_performed": False,
        "canonical_agentd_owner_selected": False,
        "actual_broker_fills_attested": False,
        "independent_product_observer_signed": False,
        "actual_executed_net_profit_qualified": False,
        "after_cost_alpha_qualified": False,
        "model_promotion_permitted": False,
        "automatic_trading": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
        "windows": scores,
    }
    return {**report, "audit_id": digest(report)}


def main():
    ap = argparse.ArgumentParser()
    for key in ("first","second","config","original-source-root","output"):
        ap.add_argument("--"+key, required=True, type=Path)
    args = ap.parse_args()
    os.umask(0o077)
    report = evaluate(args.first,args.second,args.config,args.original_source_root)
    if report["state"] == "WAIT_ORIGINAL_SEALED_SOURCE_DO_NOT_SCORE_MISSING":
        print(json.dumps({"state": report["state"],"observed":report[
            "observed_original_paper_price_outcomes"],"broker_orders_sent":0}))
        return
    path = args.output
    fail(not path.is_symlink() and not path.exists(),
         "NATIVE_OUTCOME_IMMUTABLE_NEW_OUTPUT_REQUIRED")
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    raw = (json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    dirfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)
    print(json.dumps({
        "state":report["state"],
        "real_forward_predictions":2,
        "observed_original_price_labels":report[
            "observed_original_paper_price_outcomes"],
        "directions_correct":sum(x.get("price_direction_match") is True
                               for x in report["windows"]),
        "economic_alpha_qualified":False,
        "sha256":hashlib.sha256(raw).hexdigest(),
        "broker_orders_sent":0},sort_keys=True))

if __name__ == "__main__":
    main()
