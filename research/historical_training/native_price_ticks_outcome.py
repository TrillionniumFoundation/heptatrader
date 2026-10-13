"""Independent original sealed PAPER price-only outcome for NEW tick canary.

Never infer fill, realized net PnL, after-cost alpha, Canonical Agentd
LedgerWriter acceptance or an independent product Observer signature.
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
from train_v2.postmodel_requote import load_original_quote_archive
from train_v2.original_multihorizon import ordered_quote_index, label_after
from .laya_native_price_ticks_forward import verify, _plan, _directory

SCHEMA = "heptatrader.native-price-ticks-sealed-outcome.v1"
HORIZON_MS = 300000
ENDPOINT_GRACE_MS = 5000
PRIOR_HYPOTHETICAL_STRESS_COST_MARGIN = Decimal("10.9133557")


def assess(root, config_file, original_source_root):
    root = _directory(root)
    strict = verify(root, config_file)
    if (strict.get("new_profile_score_reproduced") is not True
            or strict.get("original_source_rechecked") is not True
            or strict.get("original_f64_bitwise_equal_attested") is not False
            or strict.get("broker_orders_sent") != 0):
        raise ContractError("NATIVE_TICKS_OUTCOME_VERIFIER_NOT_VALID")
    plan = _plan(root)
    inp = read(root / "input.json", 160 * 1024)
    pred = read(root / "prediction.json", 65536)
    ack = read(root / "commit.json", 65536)
    source_root = Path(original_source_root)
    if (source_root.is_symlink() or not source_root.is_absolute()
            or str(source_root) != plan["original_feed_directory"]):
        raise ContractError("NATIVE_TICKS_OUTCOME_SOURCE_ROOT_CHANGED")
    if (pred["anchor_ms"] != inp["quote_event_at_ms"]
            or pred["source_decision_at_ms"] != inp["feature_decision_ms"]
            or not plan["registered_at_ms"] < inp["quote_known_at_ms"]
            <= inp["source_asof_ms"] <= inp["feature_decision_ms"]
            <= pred["prediction_ready_at_ms"] <= ack["post_fsync_at_ms"]
            <= plan["window_end_ms"]
            or pred.get("target_horizon_sec") != 300
            or ack.get("broker_orders_sent") != 0):
        raise ContractError("NATIVE_TICKS_OUTCOME_FROZEN_TIMELINE_CHANGED")
    archive = load_original_quote_archive(
        source_root, plan["original_period_sha256"])
    index = ordered_quote_index(archive["rows"])
    candidates = [q for q in index["known"]
                  if q[3] == inp["quote_receipt"]]
    if len(candidates) > 1:
        raise ContractError("NATIVE_TICKS_OUTCOME_ORIGINAL_RECEIPT_NOT_UNIQUE")
    reference = candidates[0] if candidates else None
    # The real input may still be in the ACTIVE unsealed quote segment.
    # verify() has already rechecked its original append-only prefix.
    # Never describe an absent SEALED input as a missing/zero price label.
    if reference is not None:
        if ((reference[0], reference[1], reference[3]) !=
                (inp["quote_known_at_ms"], inp["quote_event_at_ms"],
                 inp["quote_receipt"])):
            raise ContractError("NATIVE_TICKS_OUTCOME_REFERENCE_TIMESTAMP_CHANGED")
        mid_value = inp["feature_state"].get("reference_mid")
        if mid_value is None or Decimal(str(mid_value)) != reference[4]:
            raise ContractError("NATIVE_TICKS_OUTCOME_REFERENCE_MID_CHANGED")
    due = pred["anchor_ms"] + HORIZON_MS
    score = pred["future_fixed_score"]
    predicted = Decimal(score["scaled_bps_ticks"]) / Decimal(100000000)
    if not predicted.is_finite() or abs(predicted) > Decimal("1000000"):
        raise ContractError("NATIVE_TICKS_OUTCOME_FUTURE_SCORE_INVALID")
    if ack["post_fsync_at_ms"] >= due:
        raise ContractError("NATIVE_TICKS_OUTCOME_NOT_FUTURE_PREDICTION")
    waiting_input = reference is None
    waiting_endpoint = (not index.get("known_times") or
                        index["known_times"][-1] < due + ENDPOINT_GRACE_MS)
    if waiting_input:
        state, realized = "WAIT_ORIGINAL_SEALED_INPUT_COVERAGE", None
    elif waiting_endpoint:
        state, realized = "WAIT_ORIGINAL_SEALED_PRICE_COVERAGE", None
    else:
        state, amount = label_after(
            index, reference, pred["anchor_ms"], HORIZON_MS)
        if amount is None:
            if not state.startswith("CENSORED_"):
                raise ContractError("NATIVE_TICKS_OUTCOME_BAD_CENSORED_LABEL")
            realized = None
        else:
            realized = Decimal(str(amount))
            if not realized.is_finite():
                raise ContractError("NATIVE_TICKS_OUTCOME_NONFINITE_PRICE")
            state = "OBSERVED_ORIGINAL_PAPER_MID_PRICE_ONLY"
    entry = {
        "schema": SCHEMA,
        "plan_id": plan["plan_id"],
        "source_quote_receipt": inp["quote_receipt"],
        "source_event_at_ms": inp["quote_event_at_ms"],
        "original_quote_known_at_ms": inp["quote_known_at_ms"],
        "prediction_fsynced_at_ms": ack["post_fsync_at_ms"],
        "original_price_due_at_ms": due,
        "frozen_model_sha256": plan["baseline_model_sha256"],
        "model_score_id": score["score_id"],
        "model_score_fixed_bps": str(predicted),
        "original_price_change_bps": str(realized) if realized is not None else None,
        "price_direction_match": (
            ((predicted > 0) == (realized > 0))
            if realized is not None and predicted != 0 and realized != 0
            else None),
        "absolute_price_move_clears_prior_hypothetical_cost": (
            abs(realized) >= PRIOR_HYPOTHETICAL_STRESS_COST_MARGIN
            if realized is not None else None),
        "state": state,
        "original_source_sealed_segments": archive["sealed_segments"],
        "original_source_fingerprint_sha256": archive["source_fingerprint_sha256"],
        "active_unsealed_segment_excluded": archive["unsealed_newest_excluded"],
        "native_score_new_profile_replayed": True,
        "native_f64_exact_replay_attested": False,
        "original_source_sealed_price_observed": realized is not None,
        "original_price_only_not_executable_fill": True,
        "actual_broker_fills": 0,
        "actual_executed_net_pnl_samples": 0,
        "economically_independent_trading_samples": 0,
        "canonical_agentd_owner_enrolled": False,
        "model_promotion_permitted": False,
        "automatic_trading": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
        "source_verifier_sha256": sha(
            Path(__file__).with_name("laya_native_price_ticks_forward.py")),
        "outcome_evaluator_sha256": sha(Path(__file__)),
    }
    return {**entry, "audit_id": digest(entry)}


def main():
    p = argparse.ArgumentParser()
    for name in ("root", "config", "original-source-root", "output"):
        p.add_argument("--" + name, required=True, type=Path)
    args = p.parse_args()
    os.umask(0o077)
    report = assess(args.root, args.config, args.original_source_root)
    out = args.output
    if out.exists() or out.is_symlink():
        raise ContractError("NATIVE_TICKS_OUTCOME_IMMUTABLE_NEW_OUTPUT_REQUIRED")
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False)+"\n").encode()
    fd = os.open(out, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    parent = os.open(out.parent, os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    print(json.dumps({
        "state": report["state"],
        "original_price_only_observed": report["original_source_sealed_price_observed"],
        "native_score_ticks_replayed": report["native_score_new_profile_replayed"],
        "old_f64_requalified": False,
        "broker_orders_sent": 0,
        "sha256": hashlib.sha256(data).hexdigest(),
    }, sort_keys=True))


if __name__=="__main__":
    main()
