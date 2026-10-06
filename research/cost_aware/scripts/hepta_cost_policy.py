"""Deterministic PAPER cost admission. No broker, model or order I/O.

Forecast returns are position-neutral underlying MID returns. Operator-approved
past-only calibration is required. LLM confidence is never treated as calibration.
Estimated costs are separate from actual booked commissions and confirmed rebates.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_CEILING, InvalidOperation
from pathlib import Path
import hashlib
import json
import math
import os
import stat

D = Decimal
BPS = D(10000)
POLICY_VERSION = "cost-admission-v1-20261006"
FORECAST_VERSION = "position-neutral-mid-scenarios-v2"
MODEL_ID = "openai/gpt-5.6-luna:" + FORECAST_VERSION
HORIZONS = (300, 900, 3600, 14400)
FEE_VERIFIED_DATE = "2026-10-06"
FEE_SOURCES = {
    "BTC.USD": "https://www.interactivebrokers.com/en/pricing/commissions-cryptocurrencies.php",
    "EUR.USD": "https://www.interactivebrokers.com/en/pricing/commissions-spot-currencies.php",
}


class CostError(ValueError):
    pass


def dec(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, D)):
        raise CostError("INVALID_DECIMAL")
    try:
        result = D(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CostError("INVALID_DECIMAL") from exc
    if not result.is_finite() or abs(result) > D("1e20"):
        raise CostError("NONFINITE_OR_OVERSIZED_NUMBER")
    return result


def positive_int(value, reason):
    if type(value) is not int or value <= 0:
        raise CostError(reason)
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def strict_loads(raw):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CostError("DUPLICATE_JSON_FIELD")
            result[key] = value
        return result
    def bad(_):
        raise CostError("NONFINITE_JSON")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=bad)


@dataclass(frozen=True)
class CostPolicy:
    version: str = POLICY_VERSION
    # Provisional conservative haircuts: not measured live slippage/financing.
    btc_slippage_bps_per_side: str = "2"
    fx_slippage_bps_per_side: str = "0.5"
    holding_bps_per_hour: str = "1"
    btc_min_net_edge_bps: str = "10"
    fx_min_net_edge_bps: str = "2"
    quote_ttl_ms: int = 5000
    forecast_entry_ttl_ms: int = 60000
    reentry_cooldown_ms: int = 120000
    min_calibration_samples: int = 30
    min_calibration_days: int = 2
    max_calibration_age_ms: int = 7 * 86400000
    fee_stress: str = "1"
    slippage_stress: str = "1"

    def validate(self):
        if self.version != POLICY_VERSION:
            raise CostError("COST_POLICY_VERSION_UNSUPPORTED")
        for key, value in asdict(self).items():
            if key == "version":
                continue
            if key.endswith("ms") or key.startswith("min_calibration"):
                positive_int(value, "INVALID_COST_POLICY")
            elif dec(value) < 0:
                raise CostError("INVALID_COST_POLICY")
        if dec(self.fee_stress) < 1 or dec(self.slippage_stress) < 1:
            raise CostError("STRESS_CANNOT_DISCOUNT_COST")
        if self.quote_ttl_ms > 60000 or self.forecast_entry_ttl_ms > 60000:
            raise CostError("COST_TTL_TOO_LONG")
        if self.min_calibration_samples < 30 or self.min_calibration_days < 2:
            raise CostError("CALIBRATION_MINIMUM_TOO_SMALL")
        if max(dec(self.btc_slippage_bps_per_side), dec(self.fx_slippage_bps_per_side)) >= 1000:
            raise CostError("SLIPPAGE_MODEL_OUT_OF_RANGE")
        return self

    def slip(self, instrument):
        value = self.btc_slippage_bps_per_side if instrument == "BTC.USD" else self.fx_slippage_bps_per_side
        return dec(value) * dec(self.slippage_stress)

    def hurdle(self, instrument):
        return dec(self.btc_min_net_edge_bps if instrument == "BTC.USD" else self.fx_min_net_edge_bps)


DEFAULT_POLICY = CostPolicy()


def commission(instrument, notional_usd, *, fee_stress="1"):
    """Estimate per original executed order, NOT per execution callback.

    BTC cash-paid upper estimate uses 18bp/min $1.75 before unconfirmed tier
    and 1%-cap rebates. FX: 0.20bp/min $2; base-currency conversion is not modeled,
    so only USD-valued EUR/USD tickets are supported. Confirmed rebates and actual
    commission receipts remain the existing PnL ledger's responsibility.
    """
    amount, stress = dec(notional_usd), dec(fee_stress)
    if amount < 0 or stress < 1:
        raise CostError("INVALID_COMMISSION_INPUT")
    if instrument not in FEE_SOURCES:
        raise CostError("UNSUPPORTED_COST_INSTRUMENT")
    if amount == 0:
        return D(0)
    rate, minimum = (D(".0018"), D("1.75")) if instrument == "BTC.USD" else (D(".00002"), D("2"))
    return (max(amount * rate, minimum) * stress).quantize(D(".01"), rounding=ROUND_CEILING)


def quote_values(quote, instrument, now_ms, policy=DEFAULT_POLICY):
    policy.validate()
    positive_int(now_ms, "INVALID_CURRENT_TIME")
    if (not isinstance(quote, dict) or quote.get("source") != "IB" or
            quote.get("authoritative") is not True or quote.get("stale") is not False or
            quote.get("instrument") != instrument or not isinstance(quote.get("subscription_id"), str) or
            not quote["subscription_id"]):
        raise CostError("COST_QUOTE_AUTHORITY_MISSING")
    observed = positive_int(quote.get("observed_at_ms"), "COST_QUOTE_TIME_MISSING")
    expires = positive_int(quote.get("stale_after_ms"), "COST_QUOTE_EXPIRY_MISSING")
    if observed > now_ms or expires < observed or now_ms > min(expires, observed + policy.quote_ttl_ms):
        raise CostError("COST_QUOTE_STALE")
    bid, ask = dec(quote.get("bid")), dec(quote.get("ask"))
    if bid <= 0 or ask < bid:
        raise CostError("COST_QUOTE_INVALID")
    return bid, ask, (bid + ask) / 2


def estimate_round_trip(instrument, side, size, quote, directional_move_bps, horizon_sec, *, policy=DEFAULT_POLICY):
    """size is cash USD for BTC BUY, base EUR for FX. MID-return input only.

    Spreads/slippage are subtracted exactly once. Exit fee uses exit notional.
    Future exit spread scales the current relative half-spread; this assumption
    and slippage/holding reserves are explicitly provisional, not market evidence.
    """
    policy.validate()
    if instrument not in FEE_SOURCES or side not in ("BUY", "SELL") or (instrument == "BTC.USD" and side != "BUY"):
        raise CostError("UNSUPPORTED_COST_ORDER")
    bid, ask, size, move = dec(quote.get("bid")), dec(quote.get("ask")), dec(size), dec(directional_move_bps)
    if size <= 0 or bid <= 0 or ask < bid or not -5000 < move < 5000 or type(horizon_sec) is not int or horizon_sec not in HORIZONS:
        raise CostError("INVALID_COST_ESTIMATE_INPUT")
    direction, mid, half = D(1 if side == "BUY" else -1), (bid + ask) / 2, (ask - bid) / 2
    slip = policy.slip(instrument) / BPS
    if slip >= D(".1"):
        raise CostError("SLIPPAGE_MODEL_OUT_OF_RANGE")
    entry_touch = ask if side == "BUY" else bid
    entry = entry_touch * (1 + direction * slip)
    quantity = size / entry if instrument == "BTC.USD" else size
    future_mid = mid * (1 + direction * move / BPS)
    future_half = half * future_mid / mid
    exit_touch = future_mid - direction * future_half
    exit_price = exit_touch * (1 - direction * slip)
    entry_notional = size if instrument == "BTC.USD" else quantity * entry
    exit_notional = quantity * exit_price
    entry_fee = commission(instrument, entry_notional, fee_stress=policy.fee_stress)
    exit_fee = commission(instrument, exit_notional, fee_stress=policy.fee_stress)
    spread_cost = quantity * (half + future_half)
    slippage_cost = quantity * (entry_touch + exit_touch) * slip
    holding = quantity * mid * dec(policy.holding_bps_per_hour) / BPS * D(horizon_sec) / D(3600)
    gross = quantity * (future_mid - mid) * direction
    net_edge = gross - spread_cost - slippage_cost - entry_fee - exit_fee - holding
    return {
        "policy_version": policy.version, "fee_source": FEE_SOURCES[instrument],
        "fee_verified_date": FEE_VERIFIED_DATE, "fee_basis": "CASH_PAID_UPPER_TIER_NO_UNCONFIRMED_REBATE",
        "quote_basis": "MID_TO_MID_MINUS_EXPLICIT_EXECUTION_COSTS",
        "execution_model": "TOUCH_PLUS_PROVISIONAL_HAIRCUT_NOT_LIVE_SLIPPAGE_PROOF",
        "instrument": instrument, "side": side, "size": str(size), "quantity_estimate": str(quantity),
        "entry_notional_usd": str(entry_notional), "exit_notional_usd": str(exit_notional),
        "entry_commission_usd": str(entry_fee), "exit_commission_usd": str(exit_fee),
        "spread_usd": str(spread_cost), "slippage_usd": str(slippage_cost), "holding_reserve_usd": str(holding),
        "gross_edge_usd": str(gross), "net_edge_usd": str(net_edge),
        "minimum_net_edge_usd": str(quantity * mid * policy.hurdle(instrument) / BPS),
        "estimated_entry_price": str(entry), "estimated_exit_price": str(exit_price),
        "horizon_sec": horizon_sec, "quote_observed_at_ms": quote.get("observed_at_ms"),
    }


def validate_forecast(forecast):
    required = {"action", "confidence", "rationale", "horizon_sec", "scenarios", "invalidation_price",
                "forecast_version", "model_id", "issued_at_ms", "reference_mid", "instrument", "position_neutral"}
    if not isinstance(forecast, dict) or set(forecast) != required:
        raise CostError("FORECAST_SCHEMA_REQUIRED")
    if (forecast["forecast_version"] != FORECAST_VERSION or forecast["model_id"] != MODEL_ID or
            forecast["position_neutral"] is not True or forecast["instrument"] not in FEE_SOURCES or
            forecast["action"] not in ("BUY", "SELL", "HOLD") or type(forecast["horizon_sec"]) is not int or
            forecast["horizon_sec"] not in HORIZONS or not isinstance(forecast["rationale"], str) or
            len(forecast["rationale"]) > 8192):
        raise CostError("FORECAST_IDENTITY_INVALID")
    positive_int(forecast["issued_at_ms"], "FORECAST_TIME_INVALID")
    reference = dec(forecast["reference_mid"])
    if reference <= 0 or not 0 <= dec(forecast["confidence"]) <= 1:
        raise CostError("FORECAST_NUMBER_INVALID")
    scenarios = forecast["scenarios"]
    if not isinstance(scenarios, list) or not 2 <= len(scenarios) <= 7:
        raise CostError("FORECAST_DISTRIBUTION_REQUIRED")
    total, mean = D(0), D(0)
    for item in scenarios:
        if not isinstance(item, dict) or set(item) != {"probability", "move_bps"}:
            raise CostError("FORECAST_DISTRIBUTION_INVALID")
        probability, move = dec(item["probability"]), dec(item["move_bps"])
        if not 0 < probability <= 1 or not -2000 < move < 2000:
            raise CostError("FORECAST_DISTRIBUTION_INVALID")
        total += probability
        mean += probability * move
    if abs(total - 1) > D(".000000001"):
        raise CostError("FORECAST_PROBABILITIES_NOT_NORMALIZED")
    invalid = dec(forecast["invalidation_price"])
    if forecast["action"] == "BUY" and not 0 < invalid < reference:
        raise CostError("FORECAST_INVALIDATION_SIDE_MISMATCH")
    if forecast["action"] == "SELL" and not invalid > reference:
        raise CostError("FORECAST_INVALIDATION_SIDE_MISMATCH")
    if forecast["action"] == "HOLD" and invalid != 0:
        raise CostError("HOLD_INVALIDATION_MUST_BE_ZERO")
    return mean


def calibration_key(forecast):
    return "|".join((forecast["instrument"], forecast["model_id"], forecast["action"], str(forecast["horizon_sec"])))


def build_calibration(samples, cutoff_ms, *, policy=DEFAULT_POLICY, validity_ms=86400000):
    """Past-only nonoverlapping cohorts; returns an artifact, never promotes it.

    Empirical 90th-percentile overprediction is a haircut, not a coverage
    guarantee under nonstationarity. Per-cohort time intervals are purged.
    Forecasts outside observed prediction support cannot extrapolate on admission.
    """
    policy.validate()
    positive_int(cutoff_ms, "CALIBRATION_CUTOFF_INVALID")
    if type(validity_ms) is not int or not 0 < validity_ms <= policy.max_calibration_age_ms:
        raise CostError("CALIBRATION_VALIDITY_INVALID")
    cohorts, used, last_end, seen = {}, [], {}, set()
    synthetic = False
    if not isinstance(samples, list) or len(samples) > 100000:
        raise CostError("CALIBRATION_INPUT_BOUND")
    ordered = sorted(samples, key=lambda row: row["forecast"]["issued_at_ms"])
    for row in ordered:
        forecast = row["forecast"]
        mean = validate_forecast(forecast)
        if forecast["action"] == "HOLD":
            continue
        end = positive_int(row.get("label_observed_at_ms"), "CALIBRATION_LABEL_TIME_MISSING")
        due = forecast["issued_at_ms"] + forecast["horizon_sec"] * 1000
        known = positive_int(row.get("label_available_at_ms"), "CALIBRATION_LABEL_KNOWLEDGE_TIME_MISSING")
        available = positive_int(row.get("forecast_available_at_ms"), "CALIBRATION_FORECAST_KNOWLEDGE_TIME_MISSING")
        if (end < due or end > due + policy.quote_ttl_ms or end >= cutoff_ms or
                known < end or known >= cutoff_ms or not forecast["issued_at_ms"] <= available < end or
                available - forecast["issued_at_ms"] > policy.forecast_entry_ttl_ms):
            raise CostError("CALIBRATION_FUTURE_OR_MISALIGNED_LABEL")
        if row.get("source_kind") not in ("IB_CAPTURE", "SYNTHETIC"):
            raise CostError("CALIBRATION_SOURCE_UNPROVEN")
        synthetic |= row["source_kind"] == "SYNTHETIC"
        identity = digest(forecast)
        if identity in seen:
            raise CostError("CALIBRATION_DUPLICATE_CANDIDATE")
        seen.add(identity)
        key = calibration_key(forecast)
        if forecast["issued_at_ms"] <= last_end.get(key, 0):
            continue
        last_end[key] = end
        sign = D(1 if forecast["action"] == "BUY" else -1)
        actual, predicted = dec(row.get("realized_underlying_move_bps")) * sign, mean * sign
        if not -5000 < actual < 5000:
            raise CostError("CALIBRATION_LABEL_OUT_OF_RANGE")
        cohorts.setdefault(key, []).append((predicted, predicted - actual, forecast["issued_at_ms"] // 86400000))
        used.append(row)
    result = {}
    for key, values in cohorts.items():
        days = len({value[2] for value in values})
        if len(values) < policy.min_calibration_samples or days < policy.min_calibration_days:
            continue
        errors = sorted(value[1] for value in values)
        index = min(len(errors) - 1, math.ceil(.9 * (len(errors) + 1)) - 1)
        result[key] = {
            "count": len(values), "days": days, "prediction_min_bps": str(min(value[0] for value in values)),
            "prediction_max_bps": str(max(value[0] for value in values)),
            "overprediction_q90_bps": str(max(D(0), errors[index])),
        }
    body = {
        "schema": "heptatrader.calibration.v1", "forecast_version": FORECAST_VERSION, "model_id": MODEL_ID,
        "policy_version": policy.version, "completed_at_ms": cutoff_ms, "valid_until_ms": cutoff_ms + validity_ms,
        "synthetic": synthetic, "dataset_sha256": digest(used), "cohorts": result,
    }
    return {**body, "calibration_id": digest(body)}


def read_private_json(path, max_bytes=4 * 1024 * 1024):
    """No credential/secret read. Caller provides a fixed operator data path."""
    fd = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1 or
                before.st_mode & 0o077 or before.st_size > max_bytes):
            raise CostError("CALIBRATION_FILE_UNSAFE")
        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
            raw = stream.read(max_bytes + 1)
        after = os.fstat(fd)
        if len(raw.encode()) > max_bytes or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise CostError("CALIBRATION_CHANGED_DURING_READ")
        return strict_loads(raw)
    finally:
        os.close(fd)


def load_calibration(path):
    return read_private_json(path)


def calibrated_move(forecast, artifact, now_ms, *, policy=DEFAULT_POLICY, synthetic_ok=False):
    policy.validate()
    mean = validate_forecast(forecast)
    if not isinstance(artifact, dict):
        raise CostError("CALIBRATION_REQUIRED")
    body = {key: val for key, val in artifact.items() if key != "calibration_id"}
    if (artifact.get("schema") != "heptatrader.calibration.v1" or artifact.get("calibration_id") != digest(body) or
            artifact.get("forecast_version") != FORECAST_VERSION or artifact.get("model_id") != MODEL_ID or
            artifact.get("policy_version") != policy.version):
        raise CostError("CALIBRATION_IDENTITY_INVALID")
    if artifact.get("synthetic") is not False and not (synthetic_ok and artifact.get("synthetic") is True):
        raise CostError("SYNTHETIC_CALIBRATION_NOT_LIVE")
    completed = positive_int(artifact.get("completed_at_ms"), "CALIBRATION_TIME_INVALID")
    until = positive_int(artifact.get("valid_until_ms"), "CALIBRATION_TIME_INVALID")
    if not completed < forecast["issued_at_ms"] <= now_ms < until or until - completed > policy.max_calibration_age_ms:
        raise CostError("CALIBRATION_FUTURE_OR_EXPIRED")
    cohort = artifact.get("cohorts", {}).get(calibration_key(forecast))
    if (not isinstance(cohort, dict) or type(cohort.get("count")) is not int or type(cohort.get("days")) is not int or
            cohort["count"] < policy.min_calibration_samples or cohort["days"] < policy.min_calibration_days):
        raise CostError("CALIBRATION_COHORT_INSUFFICIENT")
    direction = D(1 if forecast["action"] == "BUY" else -1)
    raw = mean * direction
    if not dec(cohort["prediction_min_bps"]) <= raw <= dec(cohort["prediction_max_bps"]):
        raise CostError("FORECAST_OUTSIDE_CALIBRATED_SUPPORT")
    haircut = dec(cohort["overprediction_q90_bps"])
    if haircut < 0:
        raise CostError("CALIBRATION_NEGATIVE_HAIRCUT")
    return raw, raw - haircut


def expected_round_trip(forecast, quote, size, *, overprediction_haircut_bps="0", policy=DEFAULT_POLICY):
    """Price every scenario BEFORE weighting; minimum fees are nonlinear.

    These probabilities remain model estimates. Calibration only supplies an
    empirical overprediction haircut, not guaranteed probability calibration.
    """
    validate_forecast(forecast)
    side, instrument = forecast["action"], forecast["instrument"]
    if side == "HOLD":
        raise CostError("DIRECTION_HOLD")
    bid, ask = dec(quote["bid"]), dec(quote["ask"])
    mid = (bid + ask) / 2
    direction = D(1 if side == "BUY" else -1)
    haircut = dec(overprediction_haircut_bps)
    if haircut < 0:
        raise CostError("CALIBRATION_NEGATIVE_HAIRCUT")
    anchor = dec(forecast["reference_mid"])
    total_weight = sum((dec(x["probability"]) for x in forecast["scenarios"]), D(0))
    scenarios = []
    for item in forecast["scenarios"]:
        directional_move = dec(item["move_bps"]) * direction - haircut
        # Keep the original target anchored to the forecast's source quote.
        remaining = (anchor * (1 + direction * directional_move / BPS) / mid - 1) * BPS * direction
        cost = estimate_round_trip(instrument, side, size, quote, remaining, forecast["horizon_sec"], policy=policy)
        scenarios.append((dec(item["probability"]) / total_weight, cost))
    result = dict(scenarios[0][1])
    for key in ("exit_notional_usd", "entry_commission_usd", "exit_commission_usd", "spread_usd",
                "slippage_usd", "holding_reserve_usd", "gross_edge_usd", "net_edge_usd",
                "minimum_net_edge_usd", "estimated_exit_price"):
        result[key] = str(sum((probability * dec(cost[key]) for probability, cost in scenarios), D(0)))
    result["expectation_basis"] = "SCENARIO_COSTS_BEFORE_PROBABILITY_WEIGHTING"
    result["overprediction_haircut_bps"] = str(haircut)
    result["scenario_net_edges"] = [{"probability": str(probability), "net_edge_usd": cost["net_edge_usd"],
                                     "exit_commission_usd": cost["exit_commission_usd"]}
                                    for probability, cost in scenarios]
    return result


def entry_admission(forecast, quote, size, now_ms, artifact=None, *, last_exit_ms=0,
                    no_trade_band=True, policy=DEFAULT_POLICY, synthetic_ok=False):
    result = {"allowed": False, "authorization_effect": "NONE", "policy_version": policy.version,
              "reasons": [], "forecast_id": None, "calibration_id": None,
              "quote_fingerprint": None, "checked_at_ms": now_ms}
    try:
        validate_forecast(forecast)
        result.update(forecast_id=digest(forecast), quote_fingerprint=digest(quote),
                      calibration_id=artifact.get("calibration_id") if isinstance(artifact, dict) else None)
        instrument, side = forecast["instrument"], forecast["action"]
        if side == "HOLD":
            raise CostError("DIRECTION_HOLD")
        bid, ask, mid = quote_values(quote, instrument, now_ms, policy)
        age = now_ms - forecast["issued_at_ms"]
        if not 0 <= age <= min(policy.forecast_entry_ttl_ms, forecast["horizon_sec"] * 1000):
            raise CostError("FORECAST_STALE")
        if ((side == "BUY" and bid <= dec(forecast["invalidation_price"])) or
                (side == "SELL" and ask >= dec(forecast["invalidation_price"]))):
            raise CostError("FORECAST_ALREADY_INVALIDATED")
        mean_cost = expected_round_trip(forecast, quote, size, policy=policy)
        result["uncalibrated_mean_estimate"] = mean_cost
        raw, lower = calibrated_move(forecast, artifact, now_ms, policy=policy, synthetic_ok=synthetic_ok)
        conservative = expected_round_trip(forecast, quote, size,
                                           overprediction_haircut_bps=raw - lower, policy=policy)
        result.update(mean_estimate=mean_cost, conservative_estimate=conservative)
        if dec(conservative["net_edge_usd"]) <= dec(conservative["minimum_net_edge_usd"]):
            raise CostError("NET_EDGE_BELOW_COST_HURDLE")
        if no_trade_band and last_exit_ms:
            if type(last_exit_ms) is not int or last_exit_ms > now_ms or now_ms - last_exit_ms < policy.reentry_cooldown_ms:
                raise CostError("REENTRY_COOLDOWN")
        result["allowed"] = True
    except (CostError, ValueError, KeyError, TypeError, ArithmeticError) as exc:
        result["reasons"].append(str(exc) or "COST_INPUT_INVALID")
    return result


def risk_exit(position, entry_price, quote, now_ms, *, halt=False, entry_forecast=None, max_adverse=".002"):
    """No calibration, fee budget, cooldown or recorder dependency."""
    pos = dec(position)
    if pos == 0:
        return None
    if halt:
        return "HALT_REDUCE_ONLY"
    bid, ask = dec(quote["bid"]), dec(quote["ask"])
    if entry_price is not None:
        entry = dec(entry_price)
        if ((pos > 0 and bid <= entry * (1 - dec(max_adverse))) or
                (pos < 0 and ask >= entry * (1 + dec(max_adverse)))):
            return "MAX_ADVERSE_MOVE"
    if entry_forecast:
        try:
            validate_forecast(entry_forecast)
            invalid = dec(entry_forecast["invalidation_price"])
            if (pos > 0 and bid <= invalid) or (pos < 0 and ask >= invalid):
                return "FORECAST_INVALIDATION"
            if now_ms >= entry_forecast["issued_at_ms"] + entry_forecast["horizon_sec"] * 1000:
                return "FORECAST_HORIZON_EXPIRED"
        except CostError:
            return "ENTRY_FORECAST_INVALID"
    return None


def discretionary_exit(forecast, quote, position, now_ms, artifact, *, policy=DEFAULT_POLICY,
                       synthetic_ok=False, no_trade_band=True):
    pos = dec(position)
    if pos == 0 or not forecast:
        return False, "NO_POSITION_OR_CANDIDATE"
    opposite = "SELL" if pos > 0 else "BUY"
    if forecast.get("action") != opposite:
        return False, "NO_REVERSAL"
    try:
        _, lower = calibrated_move(forecast, artifact, now_ms, policy=policy, synthetic_ok=synthetic_ok)
        _, _, mid = quote_values(quote, forecast["instrument"], now_ms, policy)
        if not 0 <= now_ms - forecast["issued_at_ms"] <= policy.forecast_entry_ttl_ms:
            raise CostError("FORECAST_STALE")
        direction = D(1 if opposite == "BUY" else -1)
        remaining = (dec(forecast["reference_mid"]) * (1 + direction * lower / BPS) / mid - 1) * BPS * direction
        exit_fee = commission(forecast["instrument"], abs(pos) * mid, fee_stress=policy.fee_stress)
        execution_bps = (dec(quote["ask"]) - dec(quote["bid"])) / (2 * mid) * BPS + policy.slip(forecast["instrument"])
        # Heuristic hysteresis, not an optimal-control claim. Entry fee is sunk.
        band = (policy.hurdle(forecast["instrument"]) + execution_bps + exit_fee / (abs(pos) * mid) * BPS) if no_trade_band else D(0)
        return (True, "CALIBRATED_REVERSAL") if remaining > band else (False, "NO_ADJUSTMENT_BAND")
    except (CostError, KeyError, ValueError, TypeError, ArithmeticError) as exc:
        return False, str(exc)
