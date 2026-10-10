"""Strict feature-time semantics for isolated HeptaTrader Train V2 research.

Strict causal rows are built only from authenticated-by-local-chain PAPER quotes
known by the decision deadline. Revised IB bars are exploratory regardless of
the earlier bar timestamps; history never becomes an earlier live quote.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from typing import Mapping

SPEC_SCHEMA = "heptatrader.train-v2-feature-spec.v1"
OBS_SCHEMA = "heptatrader.train-v2-observation.v1"
DATASET_SCHEMA = "heptatrader.train-v2-dataset.v1"
LIVE = "LIVE_CAUSAL_QUOTE"
REVISED = "IB_REVISED_HISTORICAL_BAR"
CAUSAL = "CAUSAL_SHADOW_ONLY"
EXPLORATORY = "REVISED_HISTORICAL_RESEARCH_ONLY"
EXPECTED_FIELDS = ("mid", "spread_bps", "return_1_bps", "return_window_bps")


class ContractError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def is_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def numeric(value):
    if isinstance(value, bool) or value is None:
        raise ContractError("INVALID_PRICE")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ContractError("INVALID_PRICE") from exc
    if not d.is_finite() or not 0 < d < Decimal("1000000000000"):
        raise ContractError("INVALID_PRICE")
    return d


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ContractError("INVALID_TIME_OR_BOUND")
    return value


def validate_spec(spec):
    if not isinstance(spec, dict) or spec.get("schema") != SPEC_SCHEMA:
        raise ContractError("FEATURE_SPEC_SCHEMA")
    if spec.get("instrument") != "EUR.USD" or not is_digest(spec.get("period_sha256")):
        raise ContractError("FEATURE_SPEC_SCOPE")
    if not isinstance(spec.get("behavior_policy_id"), str) or not spec["behavior_policy_id"] or len(spec["behavior_policy_id"]) > 128:
        raise ContractError("FEATURE_POLICY_IDENTITY")
    if not isinstance(spec.get("feature_profile"), str) or not spec["feature_profile"] or len(spec["feature_profile"]) > 128:
        raise ContractError("FEATURE_PROFILE_IDENTITY")
    if spec.get("fields") != list(EXPECTED_FIELDS):
        raise ContractError("FEATURE_LAYOUT_MISMATCH")
    mode, source = spec.get("mode"), spec.get("source_kind")
    if (mode, source) not in ((CAUSAL, LIVE), (EXPLORATORY, REVISED)):
        raise ContractError("FEATURE_SOURCE_MODE_MISMATCH")
    if any(spec.get(k) is not False for k in ("trade_authority", "promotion_allowed", "actor_update")):
        raise ContractError("FEATURE_HAS_NO_EFFECT_AUTHORITY")
    lookback = _integer(spec.get("lookback"), 3)
    gap = _integer(spec.get("max_gap_ms"), 1)
    age = _integer(spec.get("max_age_ms"), 1)
    horizon = _integer(spec.get("horizon_ms"), 1)
    tolerance = _integer(spec.get("label_tolerance_ms"), 0)
    if lookback > 64 or gap > 300000 or age > 300000 or horizon > 3600000 or tolerance > 300000:
        raise ContractError("FEATURE_SPEC_RESOURCE_OR_TIME_BOUND")
    if source == LIVE and (age > 5000 or gap > 30000 or tolerance > 5000):
        raise ContractError("LIVE_SOURCE_TIME_GUARD")
    return spec


def feature_spec_id(spec):
    validate_spec(spec)
    return digest(spec)


def validate_observation(record, spec):
    validate_spec(spec)
    if not isinstance(record, dict) or record.get("schema") != OBS_SCHEMA:
        raise ContractError("OBSERVATION_SCHEMA")
    if any(record.get(key) != spec[key] for key in ("instrument", "period_sha256", "source_kind")):
        raise ContractError("OBSERVATION_CROSS_SCOPE")
    if not is_digest(record.get("source_id")) or not is_digest(record.get("source_sha256")):
        raise ContractError("OBSERVATION_SOURCE_WITNESS")
    occurred = _integer(record.get("event_at_ms"), 1)
    available = _integer(record.get("available_at_ms"), 1)
    expires = _integer(record.get("valid_until_ms"), 1)
    if not occurred <= available <= expires:
        raise ContractError("OBSERVATION_CLOCK_OR_EXPIRY")
    if record["source_kind"] == LIVE and not available <= occurred + 5000:
        raise ContractError("OBSERVATION_LIVE_LATE")
    bid, ask = numeric(record.get("bid")), numeric(record.get("ask"))
    if bid > ask:
        raise ContractError("OBSERVATION_CROSSED_MARKET")
    return record


def ordered_observations(spec, records):
    validate_spec(spec)
    if not isinstance(records, list) or len(records) > 100000:
        raise ContractError("OBSERVATION_COUNT_BOUND")
    unique_by_id = {}
    by_event = {}
    for raw in records:
        validate_observation(raw, spec)
        identifier = raw["source_id"]
        old = unique_by_id.get(identifier)
        if old is not None:
            if canonical(old) != canonical(raw):
                raise ContractError("OBSERVATION_ID_CONFLICT")
            continue
        unique_by_id[identifier] = raw
        timestamp = raw["event_at_ms"]
        prior = by_event.get(timestamp)
        if prior is not None:
            if numeric(prior["bid"]) != numeric(raw["bid"]) or numeric(prior["ask"]) != numeric(raw["ask"]):
                raise ContractError("OBSERVATION_TIMESTAMP_CONFLICT")
            if raw["available_at_ms"] < prior["available_at_ms"]:
                by_event[timestamp] = raw
        else:
            by_event[timestamp] = raw
    return [by_event[t] for t in sorted(by_event)]


def _rounded(value):
    return str(value.quantize(Decimal("0.00000001")))


def compile_example(spec, observations, decision_at_ms, evaluated_at_ms):
    """Compile one deterministic observed decision without peeking at later input.

    Calling again after the outcome matures may update ONLY the label section:
    features and feature_id are unaffected by outcome availability.
    """
    validate_spec(spec)
    decision = _integer(decision_at_ms, 1)
    evaluated = _integer(evaluated_at_ms, decision)
    obs = ordered_observations(spec, observations)
    source_mode = spec["mode"]
    allowed_input = [
        q for q in obs if q["event_at_ms"] <= decision
        and (source_mode == EXPLORATORY or q["available_at_ms"] <= decision)
    ]
    if len(allowed_input) < spec["lookback"]:
        raise ContractError("FEATURE_HISTORY_NOT_AVAILABLE_AT_DECISION")
    sample = allowed_input[-spec["lookback"]:]
    latest = sample[-1]
    if decision - latest["event_at_ms"] > spec["max_age_ms"]:
        raise ContractError("FEATURE_SOURCE_TOO_OLD")
    if source_mode == CAUSAL and latest["valid_until_ms"] < decision:
        raise ContractError("FEATURE_QUOTE_EXPIRED")
    if any(b["event_at_ms"] - a["event_at_ms"] > spec["max_gap_ms"]
           for a, b in zip(sample, sample[1:])):
        raise ContractError("FEATURE_INPUT_PATH_GAP")
    mids = [(numeric(q["bid"]) + numeric(q["ask"])) / 2 for q in sample]
    spreads = [(numeric(q["ask"]) - numeric(q["bid"])) / mid * Decimal(10000)
               for q, mid in zip(sample, mids)]
    values = {
        "mid": _rounded(mids[-1]),
        "spread_bps": _rounded(spreads[-1]),
        "return_1_bps": _rounded((mids[-1] / mids[-2] - 1) * Decimal(10000)),
        "return_window_bps": _rounded((mids[-1] / mids[0] - 1) * Decimal(10000)),
    }
    evidence = [{
        "source_id": x["source_id"], "source_sha256": x["source_sha256"],
        "event_at_ms": x["event_at_ms"], "available_at_ms": x["available_at_ms"],
    } for x in sample]
    features = {
        "decision_at_ms": decision,
        "feature_spec_id": feature_spec_id(spec),
        "behavior_policy_id": spec["behavior_policy_id"],
        "feature_profile": spec["feature_profile"],
        "values": values,
        "input_receipts": evidence,
    }
    feature_id = digest(features)
    due = decision + spec["horizon_ms"]
    if evaluated < due:
        state, target, outcome = "PENDING_OUTCOME", None, None
    else:
        future = [q for q in obs
                  if due <= q["event_at_ms"] <= due + spec["label_tolerance_ms"]
                  and q["available_at_ms"] <= evaluated]
        if not future:
            state = ("PENDING_OUTCOME" if evaluated < due + spec["label_tolerance_ms"]
                     else "CENSORED_MISSING_OR_LATE_OUTCOME")
            target, outcome = None, None
        else:
            next_quote = future[0]
            path = [q for q in obs if latest["event_at_ms"] <= q["event_at_ms"] <= next_quote["event_at_ms"]
                    and (source_mode == EXPLORATORY or q["available_at_ms"] <= evaluated)]
            if any(b["event_at_ms"] - a["event_at_ms"] > spec["max_gap_ms"]
                   for a, b in zip(path, path[1:])):
                state, target, outcome = "CENSORED_OUTCOME_PATH_GAP", None, None
            else:
                mid_out = (numeric(next_quote["bid"]) + numeric(next_quote["ask"])) / 2
                target = _rounded((mid_out / mids[-1] - 1) * Decimal(10000))
                state = "OBSERVED" if source_mode == CAUSAL else "REVISED_RESEARCH_LABEL"
                outcome = {
                    "source_id": next_quote["source_id"],
                    "source_sha256": next_quote["source_sha256"],
                    "event_at_ms": next_quote["event_at_ms"],
                    "available_at_ms": next_quote["available_at_ms"],
                }
    return {
        "schema": "heptatrader.train-v2-example.v1",
        "feature_id": feature_id,
        "features": features,
        "label_status": state,
        "label_return_bps": target,
        "label_receipt": outcome,
        "label_due_ms": due,
        "locally_point_in_time": source_mode == CAUSAL,
        "revised_historical": source_mode == EXPLORATORY,
        "candidate_training_eligible": source_mode == CAUSAL and state == "OBSERVED",
        "economic_return_attested": False,
        "canonical_owner_admitted": False,
        "trade_authority": False,
    }


def compile_dataset(spec, observations, decisions, evaluated_at_ms):
    validate_spec(spec)
    if not isinstance(decisions, list) or not 1 <= len(decisions) <= 512:
        raise ContractError("DATASET_DECISION_BOUND")
    if any(type(x) is not int for x in decisions) or len(set(decisions)) != len(decisions) or decisions != sorted(decisions):
        raise ContractError("DATASET_DECISION_ORDER")
    examples = [compile_example(spec, observations, when, evaluated_at_ms) for when in decisions]
    record = {
        "schema": DATASET_SCHEMA,
        "feature_spec_id": feature_spec_id(spec),
        "feature_spec": spec,
        "evaluated_at_ms": evaluated_at_ms,
        "examples": examples,
        "dataset_role": spec["mode"],
        "has_prospective_claim": False,
        "economic_return_attested": False,
        "automatic_training": False,
        "promotion_allowed": False,
        "broker_orders_sent": 0,
    }
    return {**record, "dataset_id": digest(record)}
