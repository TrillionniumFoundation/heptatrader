"""Future-only deterministic Native/Numeric PRICE score serialization.

Never repair frozen floating-point predictions. The new profile must be
predeclared and pinned BEFORE any new prospective prediction is recorded.
An 8-ULP guard around rounding boundaries refuses ambiguous float results.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
import math

from laya_cell.integrity import ContractError, digest

SCALE = 100_000_000
STEP = Decimal("0.00000001")
MAX_ROUNDOFF_ULP = 8
PROFILE = "FUTURE_NATIVE_NUMERIC_ROUND_HALF_EVEN_1E8_TICKS_V1"
SCHEMA = "heptatrader.native-future-price-score-ticks.v1"


def _encode(score):
    if type(score) is not float or not math.isfinite(score):
        raise ContractError("NATIVE_SCORE_FINITE_F64_REQUIRED")
    if abs(score) > 1_000_000:
        raise ContractError("NATIVE_SCORE_BOUND")
    original = Decimal(str(score))
    ticks = int((original / STEP).to_integral_value(rounding=ROUND_HALF_EVEN))
    canonical = Decimal(ticks) * STEP
    half_distance = abs(abs(original-canonical) - STEP/Decimal(2))
    if half_distance <= Decimal(str(math.ulp(score))) * MAX_ROUNDOFF_ULP:
        raise ContractError("NATIVE_SCORE_TICK_ROUNDING_AMBIGUOUS")
    return ticks, str(canonical)


def prepare_future_score(score, *, source_model_sha256, native_binary_sha256):
    if (not isinstance(source_model_sha256,str)
            or not isinstance(native_binary_sha256,str)
            or len(source_model_sha256)!=64 or len(native_binary_sha256)!=64
            or any(c not in "0123456789abcdef"
                   for c in source_model_sha256+native_binary_sha256)):
        raise ContractError("NATIVE_SCORE_ORIGINAL_MODEL_IDENTITY")
    ticks, number = _encode(score)
    record = {
        "schema": SCHEMA,
        "profile": PROFILE,
        "scaled_bps_ticks": ticks,
        "scale_ticks_per_one_bps": SCALE,
        "rounded_bps": number,
        "rounding_mode": "DECIMAL_STR_F64_THEN_ROUND_HALF_EVEN",
        "max_half_tick_ambiguity_guard_ulps": MAX_ROUNDOFF_ULP,
        "predeclared_new_future_profile_required": True,
        "existing_original_f64_prediction_rewritten": False,
        "source_model_sha256": source_model_sha256,
        "native_binary_sha256": native_binary_sha256,
        "actual_broker_fill_net_pnl": False,
        "behavior_propensity_attested": False,
        "automatic_model_selection": False,
        "broker_orders_sent": 0,
    }
    return {**record, "score_id":digest(record)}


def verify_future_score_replay(original_record, rerun_score):
    if not isinstance(original_record,dict):
        raise ContractError("NATIVE_SCORE_RECORD_REQUIRED")
    if original_record.get("schema")!=SCHEMA or original_record.get("profile")!=PROFILE:
        raise ContractError("NATIVE_SCORE_PROFILE_CHANGED")
    if original_record.get("score_id")!=digest({
            k:v for k,v in original_record.items() if k!="score_id"}):
        raise ContractError("NATIVE_SCORE_SEAL_CHANGED")
    if (original_record.get("predeclared_new_future_profile_required") is not True
            or original_record.get("existing_original_f64_prediction_rewritten") is not False
            or original_record.get("behavior_propensity_attested") is not False
            or original_record.get("automatic_model_selection") is not False
            or original_record.get("broker_orders_sent") != 0):
        raise ContractError("NATIVE_SCORE_NOT_AN_AUTHORITY_GRANT")
    expected = prepare_future_score(
        rerun_score,
        source_model_sha256=original_record["source_model_sha256"],
        native_binary_sha256=original_record["native_binary_sha256"])
    if expected!=original_record:
        raise ContractError("NATIVE_SCORE_CANONICAL_REPLAY_MISMATCH")
    return {
        "status": "FUTURE_SCORE_TICKS_REPRODUCED_ENGINEERING_ONLY",
        "score_id":original_record["score_id"],
        "canonical_f64_replay_equivalent": True,
        "native_original_bitwise_f64_equal_attested": False,
        "not_a_frozen_legacy_prediction_requalification": True,
        "trade_authority": False,
        "broker_orders_sent": 0,
    }
