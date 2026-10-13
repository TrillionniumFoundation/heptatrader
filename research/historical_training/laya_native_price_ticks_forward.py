"""NEW preregistered Native/Numeric PRICE canary with frozen tick score.

A separate future-only contract. Never reinterpret legacy f64 forecasts.
No trader, broker API, GPU job, canonical owner or model promotion.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import time
from pathlib import Path

from historical_training.common import read, sha, write_once
from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell.dataset import verify_source_prefixes
from laya_cell.resident_data import forecast_input, read_quotes
from laya_cell.resident_recent_source import read_recent_original_quotes
from .laya_native_price_forward import (
    _model, _config, _directory, predict_point,
    WINDOW_MS, MIN_LEAD_MS, MAX_LEAD_MS, HORIZON_SEC,
)
from .laya_fast_future_canary import _sync
from .native_score_ticks import (
    PROFILE, prepare_future_score, verify_future_score_replay,
)

SCHEMA = "heptatrader.native-price-ticks-future-plan.v1"
INPUT = "heptatrader.native-price-ticks-future-input.v1"
PREDICTION = "heptatrader.native-price-ticks-future-prediction.v1"
COMMIT = "heptatrader.native-price-ticks-future-commit.v1"
BASE = Path(__file__).resolve().parents[1]
FILES = (
    "historical_training/laya_native_price_ticks_forward.py",
    "historical_training/native_score_ticks.py",
    "historical_training/laya_native_price_forward.py",
    "historical_training/laya_fast_future_canary.py",
    "neuron_learning/laya_cell/resident_recent_source.py",
    "neuron_learning/laya_cell/resident_data.py",
    "neuron_learning/laya_cell/native.py",
    "neuron_learning/laya_cell/resident_predict.py",
    "neuron_learning/laya_cell/source_archive.py",
    "neuron_learning/laya_cell/shadow_source.py",
    "neuron_learning/data.py",
)


def now_ms():
    return time.time_ns() // 1000000


def inventory():
    return {name: sha_file(BASE / name) for name in FILES}


def _plan(root):
    root = _directory(root)
    plan = read(root / "plan.json", 65536)
    if (plan.get("schema") != SCHEMA
            or plan.get("plan_id") != digest(
                {k: v for k, v in plan.items() if k != "plan_id"})
            or plan.get("slot_start_ms", 0) + WINDOW_MS !=
                    plan.get("window_end_ms")
            or plan.get("registered_at_ms", 2**63) + MIN_LEAD_MS >
                    plan.get("slot_start_ms", 0)
            or plan.get("slot_start_ms", 0) -
                    plan.get("registered_at_ms", 0) > MAX_LEAD_MS
            or plan.get("raw_f64_equality_is_not_release_criteria") is not True
            or plan.get("future_tick_encoding_must_precede_prediction") is not True
            or plan.get("one_fixed_original_horizon") is not True
            or plan.get("no_trade_policy") != "NO_ACTION_NO_BROKER_ORDER"
            or plan.get("actual_broker_execution_economics") is not False
            or plan.get("forecast_profile") != PROFILE
            or plan.get("target_horizon_sec") != HORIZON_SEC
            or plan.get("original_source_profile") !=
                    "ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2"
            or plan.get("model_inference_allowed") is not True
            or plan.get("trade_authority") is not False
            or plan.get("automatic_model_promotion") is not False
            or plan.get("canonical_agentd_owner_enrolled") is not False
            or plan.get("broker_orders_sent") != 0
            or plan.get("code_sha256") != inventory()):
        raise ContractError("NATIVE_TICKS_FROZEN_PLAN_OR_PROFILE_CHANGED")
    return plan


def register(root, config_file, slot_start_ms):
    if type(slot_start_ms) is not int:
        raise ContractError("NATIVE_TICKS_SLOT_MUST_BE_INTEGER")
    at = now_ms()
    if not MIN_LEAD_MS <= slot_start_ms - at <= MAX_LEAD_MS:
        raise ContractError("NATIVE_TICKS_NOT_PREDECLARED_AHEAD")
    c = _config(config_file)
    model = _model(c)
    if model["trained_until_ms"] >= at:
        raise ContractError("NATIVE_TICKS_MODEL_NOT_FROZEN_BEFORE_REGISTER")
    if sha(c["native_binary"]) != c["native_binary_sha256"]:
        raise ContractError("NATIVE_TICKS_NATIVE_RUNTIME_CHANGED")
    plan = {
        "schema": SCHEMA,
        "registered_at_ms": at,
        "slot_start_ms": slot_start_ms,
        "window_end_ms": slot_start_ms + WINDOW_MS,
        "original_config_path": str(config_file),
        "original_config_sha256": sha(config_file),
        "original_period_sha256": c["period_sha256"],
        "original_feed_directory": c["feed_directory"],
        "original_source_profile": "ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2",
        "forecast_profile": PROFILE,
        "target_horizon_sec": HORIZON_SEC,
        "baseline_model_id": model["model_id"],
        "baseline_model_sha256": sha(
            Path(c["baseline_directory"]) / "model.json"),
        "baseline_freeze_sha256": sha(
            Path(c["baseline_directory"]) / "freeze-manifest.json"),
        "baseline_trained_until_ms": model["trained_until_ms"],
        "original_native_binary_sha256": c["native_binary_sha256"],
        "code_sha256": inventory(),
        "model_inference_allowed": True,
        "raw_f64_equality_is_not_release_criteria": True,
        "future_tick_encoding_must_precede_prediction": True,
        "one_fixed_original_horizon": True,
        "no_trade_policy": "NO_ACTION_NO_BROKER_ORDER",
        "actual_broker_execution_economics": False,
        "canonical_agentd_owner_enrolled": False,
        "automatic_model_promotion": False,
        "trade_authority": False,
        "broker_orders_sent": 0,
    }
    plan["plan_id"] = digest(plan)
    root = _directory(root, create=True)
    write_once(root / "plan.json", plan)
    _sync(root)
    return plan


def capture(root, config_file, *, wait=False):
    root = _directory(root)
    plan = _plan(root)
    c = _config(config_file, plan)
    model = _model(c, plan)
    if (sha(c["native_binary"]) != plan["original_native_binary_sha256"]
            or model["trained_until_ms"] >= plan["slot_start_ms"]):
        raise ContractError("NATIVE_TICKS_PINNED_RUNTIME_CHANGED")
    outputs = ("input.json", "prediction.json", "commit.json", "failure.json")
    if any((root / name).exists() or (root / name).is_symlink()
           for name in outputs):
        raise ContractError("NATIVE_TICKS_CAPTURE_ALREADY_ATTEMPTED")
    fd = os.open(root / "capture.lock",
                 os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any((root / name).exists() or (root / name).is_symlink()
               for name in outputs):
            raise ContractError("NATIVE_TICKS_CAPTURE_ALREADY_ATTEMPTED")
        if now_ms() < plan["slot_start_ms"]:
            if not wait:
                raise ContractError("NATIVE_TICKS_SLOT_NOT_DUE")
            while now_ms() < plan["slot_start_ms"]:
                time.sleep(min(.25, max(.01, (
                    plan["slot_start_ms"] - now_ms()) / 1000)))
        last_reason = "NO_NEW_ORIGINAL_PAPER_SOURCE"
        attempts = 0
        while now_ms() < plan["window_end_ms"] - 1000:
            asof = now_ms()
            attempts += 1
            try:
                source, prefixes = read_recent_original_quotes(c, asof)
                decision = now_ms()
                if decision >= plan["window_end_ms"] - 800:
                    raise ContractError("NATIVE_TICKS_ORIGINAL_READ_TOO_LATE")
                sample, feature = forecast_input(c, source, decision)
                originals = [
                    row for row in source if row["t"] == sample["anchor_ms"]]
                if (len(originals) != 1
                        or not plan["slot_start_ms"] <= sample["anchor_ms"]
                        or not plan["registered_at_ms"] <
                            originals[0]["known_at_ms"] <= asof
                        or model["trained_until_ms"] >= sample["anchor_ms"]):
                    raise ContractError("NATIVE_TICKS_NOT_NEW_ORIGINAL_SOURCE")
                score_f64, native_source_sha = predict_point(
                    c, plan, sample)
                scored = prepare_future_score(
                    score_f64,
                    source_model_sha256=plan["baseline_model_sha256"],
                    native_binary_sha256=plan["original_native_binary_sha256"])
                predicted = now_ms()
                if (predicted > plan["window_end_ms"] - 300
                        or predicted >= sample["anchor_ms"] +
                           HORIZON_SEC * 1000):
                    raise ContractError("NATIVE_TICKS_PREDICTION_TOO_LATE")
                row = {
                    "schema": INPUT, "plan_id": plan["plan_id"],
                    "slot_start_ms": plan["slot_start_ms"],
                    "source_asof_ms": asof,
                    "feature_decision_ms": decision,
                    "quote_event_at_ms": sample["anchor_ms"],
                    "quote_known_at_ms": originals[0]["known_at_ms"],
                    "quote_receipt": originals[0]["receipt"],
                    "sample_id": sample["sample_id"],
                    "sample_sha256": digest(sample),
                    "feature_state_sha256": digest(feature["state"]),
                    "original_source_prefixes": prefixes,
                    "sample": sample, "feature_state": feature["state"],
                    "attempts": attempts,
                    "broker_orders_sent": 0,
                }
                write_once(root / "input.json", row)
                _sync(root)
                prediction = {
                    "schema": PREDICTION, "plan_id": plan["plan_id"],
                    "source_input_sha256": sha(root / "input.json"),
                    "baseline_model_id": plan["baseline_model_id"],
                    "baseline_model_sha256": plan["baseline_model_sha256"],
                    "native_binary_sha256": plan["original_native_binary_sha256"],
                    "native_source_sha": native_source_sha,
                    "sample_id": sample["sample_id"],
                    "anchor_ms": sample["anchor_ms"],
                    "target_horizon_sec": HORIZON_SEC,
                    "future_fixed_score": scored,
                    "f64_model_output_diagnostic_only": score_f64,
                    "strict_legacy_f64_replay_upgrade": False,
                    "prediction_ready_at_ms": predicted,
                    "source_decision_at_ms": decision,
                    "laya_model_inference": False,
                    "trade_action_head": False,
                    "automatic_trading": False,
                    "broker_orders_sent": 0,
                }
                write_once(root / "prediction.json", prediction)
                _sync(root)
                sealed = now_ms()
                if sealed > plan["window_end_ms"]:
                    raise ContractError("NATIVE_TICKS_POST_FSYNC_LATE")
                ack = {
                    "schema": COMMIT, "plan_id": plan["plan_id"],
                    "input_sha256": sha(root / "input.json"),
                    "prediction_sha256": sha(root / "prediction.json"),
                    "post_fsync_at_ms": sealed,
                    "prediction_sealed_before_deadline": True,
                    "future_fixed_score_profile": PROFILE,
                    "legacy_f64_prediction_requalified": False,
                    "canonical_owner_enrolled": False,
                    "broker_orders_sent": 0,
                }
                write_once(root / "commit.json", ack)
                _sync(root)
                return ack
            except ContractError as exc:
                last_reason = str(exc)
                if ((root / "input.json").exists() or
                        (root / "prediction.json").exists()):
                    raise
                time.sleep(.25)
        miss = {
            "schema": "heptatrader.native-price-ticks-missed-slot.v1",
            "plan_id": plan["plan_id"],
            "missed_at_ms": now_ms(),
            "reason": last_reason[:160],
            "prediction_fabricated": False,
            "broker_orders_sent": 0,
        }
        write_once(root / "failure.json", miss)
        _sync(root)
        raise ContractError("NATIVE_TICKS_SLOT_MISSED:" + last_reason)
    finally:
        os.close(fd)


def verify(root, config_file):
    """Independently replay immutable original source and prospective tick score.

    A rounded score match is a NEW PROFILE semantic check, never a legacy
    bitwise-f64 proof, signed native LedgerWriter fact or broker execution.
    """
    root = _directory(root)
    plan = _plan(root)
    c = _config(config_file, plan)
    model = _model(c, plan)
    inp = read(root / "input.json", 160 * 1024)
    pred = read(root / "prediction.json", 65536)
    ack = read(root / "commit.json", 65536)
    if ((root / "failure.json").exists()
            or inp.get("schema") != INPUT
            or pred.get("schema") != PREDICTION
            or ack.get("schema") != COMMIT
            or any(obj.get("plan_id") != plan["plan_id"]
                   for obj in (inp, pred, ack))
            or pred.get("source_input_sha256") != sha(root / "input.json")
            or ack.get("input_sha256") != sha(root / "input.json")
            or ack.get("prediction_sha256") != sha(root / "prediction.json")
            or pred.get("baseline_model_id") != plan["baseline_model_id"]
            or pred.get("baseline_model_sha256") !=
               plan["baseline_model_sha256"]
            or pred.get("native_binary_sha256") !=
               plan["original_native_binary_sha256"]
            or pred.get("target_horizon_sec") != HORIZON_SEC
            or pred.get("strict_legacy_f64_replay_upgrade") is not False
            or pred.get("laya_model_inference") is not False
            or pred.get("automatic_trading") is not False
            or ack.get("prediction_sealed_before_deadline") is not True
            or ack.get("future_fixed_score_profile") != PROFILE
            or ack.get("legacy_f64_prediction_requalified") is not False
            or ack.get("broker_orders_sent") != 0
            or pred.get("broker_orders_sent") != 0
            or inp.get("broker_orders_sent") != 0):
        raise ContractError("NATIVE_TICKS_ORIGINAL_RECEIPT_CHAIN_INVALID")
    at = inp.get("source_asof_ms")
    decision = inp.get("feature_decision_ms")
    completed = pred.get("prediction_ready_at_ms")
    sealed = ack.get("post_fsync_at_ms")
    known = inp.get("quote_known_at_ms")
    anchor = inp.get("quote_event_at_ms")
    if (any(type(x) is not int for x in
            (at, decision, completed, sealed, known, anchor))
            or not plan["registered_at_ms"] < known <= at <= decision
            <= completed <= sealed <= plan["window_end_ms"]
            or at < plan["slot_start_ms"]
            or anchor < plan["slot_start_ms"]
            or anchor > at
            or completed >= anchor + HORIZON_SEC * 1000):
        raise ContractError("NATIVE_TICKS_SOURCE_OR_COMMIT_DEADLINE")
    if (pred.get("sample_id") != inp.get("sample_id")
            or pred.get("anchor_ms") != anchor):
        raise ContractError("NATIVE_TICKS_SAMPLE_IDENTITY_INVALID")
    verify_source_prefixes({
        "source_prefixes": inp["original_source_prefixes"],
        "scope": c["period_sha256"] + ":EUR.USD",
    })
    original, _ = read_quotes(c, at)
    sample, feature = forecast_input(c, original, decision)
    orig = [row for row in original if row["t"] == sample["anchor_ms"]]
    if (len(orig) != 1
            or orig[0]["receipt"] != inp["quote_receipt"]
            or orig[0]["known_at_ms"] != known
            or sample["anchor_ms"] != anchor
            or sample["sample_id"] != inp["sample_id"]
            or digest(sample) != inp["sample_sha256"]
            or digest(feature["state"]) != inp["feature_state_sha256"]
            or inp["sample"] != sample
            or inp["feature_state"] != feature["state"]):
        raise ContractError("NATIVE_TICKS_ORIGINAL_PRICE_SOURCE_CHANGED")
    score, native_sha = predict_point(c, plan, sample)
    if pred.get("native_source_sha") != native_sha:
        raise ContractError("NATIVE_TICKS_NATIVE_BINARY_IDENTITY_CHANGED")
    ticket = verify_future_score_replay(pred["future_fixed_score"], score)
    if ticket["score_id"] != pred["future_fixed_score"]["score_id"]:
        raise ContractError("NATIVE_TICKS_REPLAY_TICKET_IDENTITY")
    return {
        "schema": "heptatrader.native-price-ticks-prospective-verify.v1",
        "plan_id": plan["plan_id"],
        "sample_id": sample["sample_id"],
        "new_profile_score_reproduced": True,
        "score_id": ticket["score_id"],
        "canonical_fixed_scale_ticks": pred["future_fixed_score"]["scaled_bps_ticks"],
        "original_f64_bitwise_equal_attested": False,
        "old_frozen_plan_qualified": False,
        "original_source_rechecked": True,
        "native_model_recomputed": True,
        "original_prediction_sealed_at_ms": sealed,
        "original_deadline_ms": plan["window_end_ms"],
        "canonical_agentd_owner_enrolled": False,
        "independent_economic_return_attested": False,
        "model_promotion_allowed": False,
        "broker_orders_sent": 0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("register", "capture", "verify"),
                        required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--slot-ms", type=int)
    parser.add_argument("--wait", action="store_true")
    a = parser.parse_args()
    os.umask(0o077)
    if a.mode == "register":
        result = register(a.root, a.config, a.slot_ms)
    elif a.mode == "capture":
        result = capture(a.root, a.config, wait=a.wait)
    else:
        result = verify(a.root, a.config)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
