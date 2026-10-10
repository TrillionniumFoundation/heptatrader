"""Exact Numeric and Laya price-question parity on one causal quote source.

This is semantic parity only: the old 16-dimensional Neuron input and seven-bin
Laya price question are distinct from Train V2's generic 4-value feature spec.
ACT/ESCALATE and BUY/SELL are never treated as the same action vocabulary.
"""
from __future__ import annotations

import math
from decimal import Decimal
from .contracts import CAUSAL, LIVE, ContractError, compile_example, digest, numeric, ordered_observations, validate_spec


def price_family_contracts():
    from data import FEATURE_VERSION, HORIZONS
    from laya_cell.dataset import BIN_EDGES_BPS
    from laya_cell.financial_value_data import PROFILE
    from historical_training.current100k import BEHAVIOR
    return {
        "numeric": {
            "input_profile": FEATURE_VERSION,
            "input_width": 16,
            "target": "FUTURE_MID_PRICE_RETURN",
            "horizons_sec": list(HORIZONS),
            "action_vocabulary": [],
            "can_use_generic_v2_price4_as_direct_model_input": False,
        },
        "laya-price": {
            "input_profile": "CAUSAL_QUOTE_WINDOW_TO_ORIGINAL_LAYA_SEVENBIN_QUESTION",
            "input_width": "VARIABLE_EXACT_CAUSAL_QUOTES",
            "target": "FUTURE_MID_SEVENBIN_PRICE_CLASS",
            "bins_bps": list(BIN_EDGES_BPS),
            "horizons_sec": list(HORIZONS),
            "auxiliary_action_vocabulary": ["ACT", "ESCALATE"],
            "trade_action_head": False,
            "can_use_generic_v2_price4_as_direct_model_input": False,
        },
        "value-eur25k": {
            "input_profile": PROFILE["representation"],
            "feature_target": "EXACT_BEHAVIOR_REQUEST_PLUS_SELECTED_SIDE",
            "target": "REALIZED_CLOSED_BROKER_NET_USD_AFTER_REPORTED_EXECUTION_COMMISSION",
            "quantity_eur": PROFILE["quantity"],
            "action_vocabulary": ["BUY", "SELL"],
            "on_policy": False,
            "can_use_generic_v2_price4_as_direct_model_input": False,
        },
        "value-usd100k": {
            "input_profile": "EXACT_ORIGINAL_USD100K_BEHAVIOR_REQUEST_AND_SIDE",
            "behavior_profile": BEHAVIOR,
            "reference_notional_usd": 100000,
            "target": "REALIZED_CLOSED_BROKER_NET_USD_AFTER_REPORTED_EXECUTION_COMMISSION",
            "action_vocabulary": ["BUY", "SELL"],
            "on_policy": False,
            "can_use_generic_v2_price4_as_direct_model_input": False,
        },
    }


def require_semantic_compatibility(left, right, *, proposed_repurpose=False):
    """Keep cross-task model/target swapping fail-closed."""
    profiles = price_family_contracts()
    if left not in profiles or right not in profiles:
        raise ContractError("MODEL_FAMILY_UNKNOWN")
    if left != right:
        raise ContractError("MODEL_SEMANTICS_NOT_EQUIVALENT")
    if proposed_repurpose:
        raise ContractError("NO_AUTOMATIC_TRAIN_TO_TRADING_REPURPOSE")
    return {
        "family": left,
        "profile_digest": digest(profiles[left]),
        "can_auto_select": False,
        "trade_authority": False,
    }


def compare_numeric_laya_price_inputs(spec, observations, decision_at_ms, *, horizon_sec=300):
    """Run the *original* exact Laya / native Numeric transforms on causal input.

    This deliberately does not claim that the new four-field V2 feature vector
    is numerically identical to either existing model's input representation.
    """
    validate_spec(spec)
    if spec["mode"] != CAUSAL or spec["source_kind"] != LIVE:
        raise ContractError("MODEL_PARITY_REQUIRES_REAL_CAUSAL_SOURCE")
    profiles = price_family_contracts()
    if horizon_sec not in profiles["numeric"]["horizons_sec"]:
        raise ContractError("MODEL_PARITY_UNREGISTERED_HORIZON")
    baseline = compile_example(spec, observations, decision_at_ms, decision_at_ms)
    if baseline["label_status"] != "PENDING_OUTCOME":
        raise ContractError("MODEL_PARITY_PREDECISION_ONLY")
    from data import features, make_sample, FEATURE_VERSION
    from laya_cell.resident_data import contiguous_history
    from laya_cell.dataset import causal_state, question
    ordered = ordered_observations(spec, observations)
    causal = [
        {"t": q["event_at_ms"], "bid": float(numeric(q["bid"])),
         "ask": float(numeric(q["ask"])),
         "mid": float((numeric(q["bid"]) + numeric(q["ask"])) / 2),
         "known_at_ms": q["available_at_ms"], "receipt": q["source_id"]}
        for q in ordered
        if q["event_at_ms"] <= decision_at_ms
        and q["available_at_ms"] <= decision_at_ms
    ]
    if not causal:
        raise ContractError("MODEL_PARITY_EXACT_ARRIVAL_REQUIRED")
    if spec.get("quote_expiry_policy") is not None:
        # Explicit NEW v2 source is bounded by the recorded IB expiry, unlike
        # the arrival-only legacy profile. Compile-example already checks it.
        last = [q for q in ordered if q["event_at_ms"] == causal[-1]["t"]]
        if len(last) != 1 or not causal[-1]["known_at_ms"] <= decision_at_ms <= last[0]["valid_until_ms"]:
            raise ContractError("MODEL_PARITY_BROKER_EXPIRY_DEADLINE")
    elif causal[-1]["known_at_ms"] != decision_at_ms:
        raise ContractError("MODEL_PARITY_EXACT_ARRIVAL_REQUIRED")
    history, reset_at_gap = contiguous_history(causal, decision_at_ms)
    if not 6 <= len(history) <= 24 or reset_at_gap:
        raise ContractError("MODEL_PARITY_INPUT_GAP_OR_HISTORY")
    if any(q["known_at_ms"] > decision_at_ms for q in history):
        raise ContractError("MODEL_PARITY_LATE_INPUT")
    original = make_sample(
        history, decision_at_ms,
        spec["period_sha256"] + ":EUR.USD", "CAUSAL_QUOTE_WINDOW")
    numeric16 = features(history, decision_at_ms)
    if (len(numeric16) != 16 or original["raw_features"] != numeric16
            or not all(math.isfinite(v) for v in numeric16)):
        raise ContractError("MODEL_PARITY_NUMERIC_ORACLE_MISMATCH")
    original_laya = causal_state(original, "EUR.USD", horizon_sec)
    if original_laya["anchor_ms"] != history[-1]["t"]:
        raise ContractError("MODEL_PARITY_LAYA_ANCHOR_MISMATCH")
    q = question(horizon_sec)
    if len(q["crit"]) != 7 or q["t"] != "choice":
        raise ContractError("MODEL_PARITY_LAYA_TARGET_SHAPE")
    return {
        "schema": "heptatrader.train-v2-price-semantic-parity.v1",
        "feature_id_v2_four": baseline["feature_id"],
        "feature_profile_v2_four": spec["feature_profile"],
        "original_numeric_feature_version": FEATURE_VERSION,
        "numeric16": numeric16,
        "native_sample_id": original["sample_id"],
        "native_horizon_sec": horizon_sec,
        "laya_price_question": q,
        "laya_causal_state": original_laya,
        "event_at_ms": history[-1]["t"],
        # The original source arrival is not the later model decision time.
        "available_at_ms": causal[-1]["known_at_ms"],
        "decision_at_ms": decision_at_ms,
        "model_targets_equal": False,
        "direct_four_to_sixteen_tensor_cast_allowed": False,
        "laya_action_is_trade_action": False,
        "economic_return_attested": False,
        "trade_authority": False,
        "automatic_model_update": False,
        "original_inputs_verified_locally": True,
    }
