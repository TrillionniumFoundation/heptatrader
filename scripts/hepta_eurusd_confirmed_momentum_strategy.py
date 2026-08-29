#!/usr/bin/env python3

"""Deterministic EUR.USD confirmed-momentum SHADOW evaluator."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from hepta_strategy_contracts import (
    ContractError,
    atomic_write_json,
    digest_document,
    digest_file,
    load_document,
    require_bool,
    require_digest,
    require_exact_fields,
    require_int,
    require_number,
    require_text,
)


CONFIG_FIELDS = frozenset({
    "schema", "strategy_id", "strategy_version", "paper_only",
    "live_authorized", "instrument", "feature_calculation_version",
    "evidence_requirements", "freshness_limits", "feature_windows",
    "regime", "setup", "shadow_intent_contract", "decision_policy",
})
INSTRUMENT_FIELDS = frozenset({
    "canonical_symbol", "asset_class", "exchange", "symbol", "currency",
})
EVIDENCE_FIELDS = frozenset({
    "authoritative_quote", "authoritative_health", "authoritative_account",
    "authoritative_positions", "authoritative_orders", "authoritative_risk",
    "minimum_raw_quote_observations",
    "minimum_resampled_quote_observations",
    "minimum_history_span_seconds", "minimum_bar_observations",
    "economic_calendar_required", "information_provenance_required",
})
FRESHNESS_FIELDS = frozenset({
    "quote_age_ms", "bar_age_ms", "portfolio_snapshot_age_ms",
    "economic_calendar_age_ms", "information_observation_age_ms",
})
WINDOW_FIELDS = frozenset({
    "fast_ema_bars", "slow_ema_bars", "slope_lookback_bars",
    "atr_bars", "breakout_bars", "bar_interval_seconds",
    "quote_lookback_seconds", "quote_resample_seconds",
    "quote_confirmation_seconds", "quote_maximum_gap_seconds",
})
REGIME_FIELDS = frozenset({
    "minimum_trend_ema_separation_bps", "minimum_trend_slope_bps",
    "maximum_range_ema_separation_bps",
})
SETUP_FIELDS = frozenset({
    "allowed_regimes", "minimum_window_return_bps",
    "maximum_window_return_bps",
    "minimum_confirmation_return_bps", "maximum_spread_bps",
    "minimum_step_volatility_bps", "maximum_step_volatility_bps",
    "minimum_cost_multiple", "estimated_slippage_bps",
    "event_exclusion_before_seconds", "event_exclusion_after_seconds",
    "require_zero_active_orders", "require_zero_position",
    "require_zero_gross_exposure", "conflicting_information_action",
})
INTENT_FIELDS = frozenset({
    "maximum_quantity", "order_type", "time_in_force",
    "maximum_intent_horizon_seconds", "maximum_holding_seconds",
    "limit_price_rule", "stop_atr_multiple",
})
POLICY_FIELDS = frozenset({
    "missing_required_evidence", "stale_or_conflicting_evidence",
    "unknown_or_transition_regime", "mutation_attempted",
    "direct_broker_access",
})
PACKET_FIELDS = frozenset({
    "schema", "packet_id", "campaign_id", "iteration", "mode",
    "created_at_ms", "evaluated_at_ms", "instrument", "strategy",
    "context_builder", "source_snapshot", "authority", "freshness",
    "provenance", "market", "session", "history", "features",
    "economic_calendar", "information", "portfolio", "service",
    "privacy", "evidence_refs", "body_sha256",
})
STRATEGY_BINDING_FIELDS = frozenset({
    "strategy_id", "strategy_version", "pinned_sha256", "config_sha256",
    "evaluator_sha256", "builder_sha256", "normalizer_sha256",
    "contracts_sha256",
    "sha256_verified",
})
CONTEXT_BUILDER_FIELDS = frozenset({
    "schema", "builder_sha256", "normalizer_sha256", "contracts_sha256",
    "feature_calculation_version",
})
AUTHORITY_KEYS = (
    "health_authoritative", "quote_authoritative",
    "account_authoritative", "positions_authoritative",
    "orders_authoritative", "risk_authoritative",
)


def load_strategy(path: Path) -> dict[str, Any]:
    config = require_exact_fields(
        load_document(path, "STRATEGY_CONFIG"),
        CONFIG_FIELDS,
        "STRATEGY_CONFIG_FIELDS_INVALID",
    )
    if config["schema"] != "hepta.confirmed-momentum-strategy.v2":
        raise ContractError("STRATEGY_CONFIG_SCHEMA_INVALID")
    require_text(
        config["strategy_id"], "STRATEGY_ID_INVALID", identifier=True)
    require_text(
        config["strategy_version"], "STRATEGY_VERSION_INVALID",
        identifier=True)
    require_bool(config["paper_only"], True, "STRATEGY_PAPER_BOUNDARY_INVALID")
    require_bool(
        config["live_authorized"], False, "STRATEGY_LIVE_BOUNDARY_INVALID")
    require_text(
        config["feature_calculation_version"],
        "STRATEGY_FEATURE_VERSION_INVALID",
        identifier=True,
    )

    instrument = require_exact_fields(
        config["instrument"], INSTRUMENT_FIELDS,
        "STRATEGY_INSTRUMENT_FIELDS_INVALID")
    expected_instrument = {
        "canonical_symbol": "EUR.USD",
        "asset_class": "CASH",
        "exchange": "IDEALPRO",
        "symbol": "EUR",
        "currency": "USD",
    }
    if instrument != expected_instrument:
        raise ContractError("STRATEGY_INSTRUMENT_INVALID")

    evidence = require_exact_fields(
        config["evidence_requirements"], EVIDENCE_FIELDS,
        "STRATEGY_EVIDENCE_FIELDS_INVALID")
    for field in (
            "authoritative_quote", "authoritative_health",
            "authoritative_account", "authoritative_positions",
            "authoritative_orders", "authoritative_risk",
            "economic_calendar_required", "information_provenance_required"):
        require_bool(evidence[field], True, "STRATEGY_EVIDENCE_VALUE_INVALID")
    require_int(
        evidence["minimum_raw_quote_observations"],
        "STRATEGY_RAW_QUOTE_COUNT_INVALID", minimum=3, maximum=1000000)
    require_int(
        evidence["minimum_resampled_quote_observations"],
        "STRATEGY_RESAMPLED_QUOTE_COUNT_INVALID", minimum=3, maximum=4096)
    require_int(
        evidence["minimum_history_span_seconds"],
        "STRATEGY_HISTORY_SPAN_INVALID", minimum=60, maximum=604800)
    require_int(
        evidence["minimum_bar_observations"],
        "STRATEGY_BAR_COUNT_INVALID", minimum=10, maximum=100000)

    freshness = require_exact_fields(
        config["freshness_limits"], FRESHNESS_FIELDS,
        "STRATEGY_FRESHNESS_FIELDS_INVALID")
    for field in FRESHNESS_FIELDS:
        require_int(
            freshness[field], "STRATEGY_FRESHNESS_VALUE_INVALID",
            minimum=1, maximum=86400000)

    windows = require_exact_fields(
        config["feature_windows"], WINDOW_FIELDS,
        "STRATEGY_WINDOW_FIELDS_INVALID")
    for field in WINDOW_FIELDS:
        require_int(
            windows[field], "STRATEGY_WINDOW_VALUE_INVALID",
            minimum=1, maximum=86400)
    if windows["fast_ema_bars"] >= windows["slow_ema_bars"]:
        raise ContractError("STRATEGY_EMA_WINDOW_ORDER_INVALID")
    if (
            windows["quote_lookback_seconds"] %
            windows["quote_resample_seconds"] != 0 or
            windows["quote_confirmation_seconds"] %
            windows["quote_resample_seconds"] != 0 or
            windows["quote_confirmation_seconds"] >
            windows["quote_lookback_seconds"] or
            windows["quote_maximum_gap_seconds"] > 30 or
            not 5 <= windows["quote_maximum_gap_seconds"] <= 15):
        raise ContractError("STRATEGY_QUOTE_WINDOW_INVALID")
    expected_resampled = (
        windows["quote_lookback_seconds"] //
        windows["quote_resample_seconds"] + 1
    )
    if (
            evidence["minimum_resampled_quote_observations"] !=
            expected_resampled or
            evidence["minimum_history_span_seconds"] !=
            windows["quote_lookback_seconds"]):
        raise ContractError("STRATEGY_QUOTE_WINDOW_BINDING_INVALID")

    regime = require_exact_fields(
        config["regime"], REGIME_FIELDS, "STRATEGY_REGIME_FIELDS_INVALID")
    for field in REGIME_FIELDS:
        require_number(
            regime[field], "STRATEGY_REGIME_VALUE_INVALID", minimum=0.0)

    setup = require_exact_fields(
        config["setup"], SETUP_FIELDS, "STRATEGY_SETUP_FIELDS_INVALID")
    if setup["allowed_regimes"] != ["trend"]:
        raise ContractError("STRATEGY_ALLOWED_REGIME_INVALID")
    for field in (
            "minimum_window_return_bps",
            "maximum_window_return_bps",
            "minimum_confirmation_return_bps",
            "maximum_spread_bps", "minimum_step_volatility_bps",
            "maximum_step_volatility_bps", "minimum_cost_multiple",
            "estimated_slippage_bps"):
        require_number(
            setup[field], "STRATEGY_SETUP_VALUE_INVALID", minimum=0.0)
    if (
            setup["minimum_window_return_bps"] >=
            setup["maximum_window_return_bps"] or
            setup["minimum_step_volatility_bps"] >=
            setup["maximum_step_volatility_bps"]):
        raise ContractError("STRATEGY_SETUP_RANGE_INVALID")
    for field in (
            "event_exclusion_before_seconds",
            "event_exclusion_after_seconds"):
        require_int(
            setup[field], "STRATEGY_EVENT_WINDOW_INVALID",
            minimum=0, maximum=86400)
    for field in (
            "require_zero_active_orders", "require_zero_position",
            "require_zero_gross_exposure"):
        require_bool(setup[field], True, "STRATEGY_FLAT_REQUIREMENT_INVALID")
    if setup["conflicting_information_action"] != "NO_TRADE":
        raise ContractError("STRATEGY_CONFLICT_POLICY_INVALID")

    intent = require_exact_fields(
        config["shadow_intent_contract"], INTENT_FIELDS,
        "STRATEGY_INTENT_FIELDS_INVALID")
    require_int(
        intent["maximum_quantity"], "STRATEGY_INTENT_QUANTITY_INVALID",
        minimum=1, maximum=1000)
    if (
            intent["order_type"] != "LMT" or
            intent["time_in_force"] != "DAY" or
            intent["limit_price_rule"] !=
            "BUY_AT_AUTHORITATIVE_ASK_OR_SELL_AT_AUTHORITATIVE_BID"):
        raise ContractError("STRATEGY_INTENT_ORDER_INVALID")
    require_int(
        intent["maximum_intent_horizon_seconds"],
        "STRATEGY_INTENT_HORIZON_INVALID", minimum=2, maximum=3600)
    require_int(
        intent["maximum_holding_seconds"],
        "STRATEGY_HOLDING_TIME_INVALID", minimum=60, maximum=86400)
    require_number(
        intent["stop_atr_multiple"], "STRATEGY_STOP_MULTIPLE_INVALID",
        positive=True, maximum=10.0)

    policy = require_exact_fields(
        config["decision_policy"], POLICY_FIELDS,
        "STRATEGY_POLICY_FIELDS_INVALID")
    for field in (
            "missing_required_evidence", "stale_or_conflicting_evidence",
            "unknown_or_transition_regime"):
        if policy[field] != "NO_TRADE":
            raise ContractError("STRATEGY_POLICY_VALUE_INVALID")
    require_bool(
        policy["mutation_attempted"], False,
        "STRATEGY_MUTATION_BOUNDARY_INVALID")
    require_bool(
        policy["direct_broker_access"], False,
        "STRATEGY_BROKER_BOUNDARY_INVALID")
    return config


def strategy_package_digest(config_path: Path) -> str:
    config = load_strategy(config_path)
    script_root = Path(__file__).resolve().parent
    package = {
        "schema": "hepta.strategy-package-binding.v3",
        "strategy_id": config["strategy_id"],
        "strategy_version": config["strategy_version"],
        "config_sha256": digest_file(config_path),
        "evaluator_sha256": digest_file(Path(__file__).resolve()),
        "builder_sha256": digest_file(
            script_root / "hepta_market_context_builder.py"),
        "normalizer_sha256": digest_file(
            script_root / "hepta_market_evidence_normalizer.py"),
        "contracts_sha256": digest_file(
            script_root / "hepta_strategy_contracts.py"),
    }
    return digest_document(package)


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _classify_regime(
    packet: dict[str, Any],
    config: dict[str, Any],
) -> str:
    calendar = packet["economic_calendar"]
    if calendar.get("high_impact_event_window_active") is True:
        return "event"
    features = packet["features"]
    setup = config["setup"]
    if (
            packet["market"]["spread_bps"] > setup["maximum_spread_bps"] or
            features["step_volatility_bps"] >
            setup["maximum_step_volatility_bps"]):
        return "illiquid"
    separation = features["ema_separation_bps"]
    slope = features["ema_fast_slope_bps"]
    regime = config["regime"]
    if (
            abs(separation) >=
            regime["minimum_trend_ema_separation_bps"] and
            abs(slope) >= regime["minimum_trend_slope_bps"] and
            separation * slope > 0.0):
        return "trend"
    if abs(separation) <= regime["maximum_range_ema_separation_bps"]:
        return "range"
    return "transition"


def _flat_portfolio_reasons(
    packet: dict[str, Any],
    config: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    setup = config["setup"]
    portfolio = packet["portfolio"]
    if (
            setup["require_zero_active_orders"] and
            portfolio["active_order_count"] != 0):
        reasons.append("ACTIVE_ORDER_PRESENT")
    if (
            setup["require_zero_position"] and
            portfolio["position_quantity"] != 0.0):
        reasons.append("NONZERO_POSITION")
    if (
            setup["require_zero_gross_exposure"] and
            portfolio["gross_exposure"] != 0.0):
        reasons.append("NONZERO_GROSS_EXPOSURE")
    return reasons


def _evidence_reasons(
    packet: dict[str, Any],
    config: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    for key in AUTHORITY_KEYS:
        if packet["authority"].get(key) is not True:
            _append_reason(reasons, key.upper() + "_MISSING")
    if packet["freshness"].get("quote_fresh") is not True:
        _append_reason(reasons, "STALE_QUOTE")
    if packet["freshness"].get("portfolio_freshness_provable") is not True:
        _append_reason(reasons, "UNPROVABLE_PORTFOLIO_FRESHNESS")
    if packet["provenance"].get("information_provenance_present") is not True:
        _append_reason(reasons, "MISSING_INFORMATION_PROVENANCE")
    if packet["provenance"].get("calendar_provenance_present") is not True:
        _append_reason(reasons, "UNPROVABLE_ECONOMIC_CALENDAR")
    if packet["economic_calendar"].get("present") is not True:
        _append_reason(reasons, "MISSING_ECONOMIC_CALENDAR")
    if packet["history"].get("quote_provenance_provable") is not True:
        _append_reason(reasons, "UNPROVABLE_QUOTE_HISTORY")
    if packet["history"].get("quote_complete") is not True:
        _append_reason(reasons, "INCOMPLETE_QUOTE_HISTORY")
    if packet["history"].get("bar_provenance_provable") is not True:
        _append_reason(reasons, "UNPROVABLE_BAR_HISTORY")
    if packet["history"].get("bar_complete") is not True:
        _append_reason(reasons, "INCOMPLETE_BAR_HISTORY")
    if packet["freshness"].get("bar_fresh") is not True:
        _append_reason(reasons, "STALE_BAR_HISTORY")
    evidence = config["evidence_requirements"]
    if (
            packet["history"]["raw_quote_observations"] <
            evidence["minimum_raw_quote_observations"]):
        _append_reason(reasons, "INSUFFICIENT_RAW_QUOTE_HISTORY")
    if (
            packet["history"]["resampled_quote_observations"] <
            evidence["minimum_resampled_quote_observations"]):
        _append_reason(reasons, "INSUFFICIENT_RESAMPLED_QUOTE_HISTORY")
    if (
            packet["history"]["span_seconds"] <
            evidence["minimum_history_span_seconds"]):
        _append_reason(reasons, "INSUFFICIENT_HISTORY_SPAN")
    if (
            packet["history"]["bar_observations"] <
            evidence["minimum_bar_observations"]):
        _append_reason(reasons, "INSUFFICIENT_BAR_HISTORY")
    if packet["strategy"].get("sha256_verified") is not True:
        _append_reason(reasons, "STRATEGY_DIGEST_UNVERIFIED")
    if packet["information"].get("conflicts"):
        _append_reason(reasons, "CONFLICTING_INFORMATION")
    reasons.extend(_flat_portfolio_reasons(packet, config))
    return reasons


def _momentum_direction(
    packet: dict[str, Any],
    config: dict[str, Any],
    reasons: list[str],
) -> str | None:
    features = packet["features"]
    setup = config["setup"]
    full_return = features["window_return_bps"]
    confirmation = features["confirmation_return_bps"]
    if abs(full_return) < setup["minimum_window_return_bps"]:
        _append_reason(reasons, "MOMENTUM_TOO_WEAK")
    if abs(full_return) > setup["maximum_window_return_bps"]:
        _append_reason(reasons, "MOMENTUM_OVEREXTENDED")
    if abs(confirmation) < setup["minimum_confirmation_return_bps"]:
        _append_reason(reasons, "MOMENTUM_UNCONFIRMED")
    if full_return * confirmation <= 0.0:
        _append_reason(reasons, "MOMENTUM_DIRECTION_CONFLICT")
    if features["ema_separation_bps"] * full_return <= 0.0:
        _append_reason(reasons, "EMA_DIRECTION_CONFLICT")
    if features["ema_fast_slope_bps"] * full_return <= 0.0:
        _append_reason(reasons, "EMA_SLOPE_CONFLICT")
    if reasons:
        return None
    return "BUY" if full_return > 0.0 else "SELL"


def _cost_reasons(
    packet: dict[str, Any],
    config: dict[str, Any],
) -> list[str]:
    setup = config["setup"]
    features = packet["features"]
    reasons: list[str] = []
    if packet["market"]["spread_bps"] > setup["maximum_spread_bps"]:
        reasons.append("SPREAD_TOO_WIDE")
    volatility = features["step_volatility_bps"]
    if volatility < setup["minimum_step_volatility_bps"]:
        reasons.append("VOLATILITY_TOO_LOW")
    if volatility > setup["maximum_step_volatility_bps"]:
        reasons.append("VOLATILITY_TOO_HIGH")
    estimated_cost = (
        packet["market"]["spread_bps"] +
        setup["estimated_slippage_bps"] * 2.0
    )
    expected_move = max(
        abs(features["confirmation_return_bps"]),
        features["atr_bps"],
    )
    if (
            estimated_cost <= 0.0 or
            expected_move / estimated_cost < setup["minimum_cost_multiple"]):
        reasons.append("EXPECTED_MOVE_INSUFFICIENT_FOR_COST")
    return reasons


def _intent(
    packet: dict[str, Any],
    config: dict[str, Any],
    strategy_sha256: str,
    side: str,
) -> dict[str, Any]:
    quote = packet["market"]
    intent_config = config["shadow_intent_contract"]
    mid = quote["mid"]
    maximum_adverse_move = (
        mid * packet["features"]["atr_bps"] / 10000.0 *
        intent_config["stop_atr_multiple"]
    )
    expected_slippage = (
        mid * config["setup"]["estimated_slippage_bps"] / 10000.0
    )
    limit_price = quote["ask"] if side == "BUY" else quote["bid"]
    return {
        "schema": "hepta.trade-intent.v1",
        "paper_only": True,
        "strategy_id": config["strategy_id"],
        "strategy_version": config["strategy_version"],
        "strategy_sha256": strategy_sha256,
        "intent_id": "shadow-intent-" + digest_document(packet)[7:39],
        "instrument": "EUR.USD",
        "symbol": "EUR",
        "currency": "USD",
        "sec_type": "CASH",
        "exchange": "IDEALPRO",
        "side": side,
        "quantity": intent_config["maximum_quantity"],
        "order_type": "LMT",
        "limit_price": round(limit_price, 8),
        "tif": "DAY",
        "observed_bid": round(quote["bid"], 8),
        "observed_ask": round(quote["ask"], 8),
        "observed_at_ms": packet["freshness"]["quote_observed_at_ms"],
        "expires_at_ms": (
            packet["evaluated_at_ms"] +
            intent_config["maximum_intent_horizon_seconds"] * 1000
        ),
        "entry_thesis": (
            f"{side} confirmed momentum: "
            f"window_return={packet['features']['window_return_bps']:.4f}bps "
            f"confirmation="
            f"{packet['features']['confirmation_return_bps']:.4f}bps "
            f"ema_separation="
            f"{packet['features']['ema_separation_bps']:.4f}bps"
        ),
        "invalidation_condition": (
            "Opposite three-observation momentum or adverse move of "
            f"{maximum_adverse_move:.8f}"
        ),
        "max_holding_ms": intent_config["maximum_holding_seconds"] * 1000,
        "max_adverse_move": round(maximum_adverse_move, 8),
        "expected_slippage": round(expected_slippage, 8),
        "exit_plan": (
            "Canonical monitor followed by cancel or atomic reduce-only "
            "flatten and authoritative reconcile"
        ),
    }


def evaluate(
    packet: dict[str, Any],
    config: dict[str, Any],
    strategy_sha256: str,
    *,
    started_at_ms: int | None = None,
    finished_at_ms: int | None = None,
) -> dict[str, Any]:
    require_exact_fields(packet, PACKET_FIELDS, "STRATEGY_PACKET_FIELDS_INVALID")
    expected_body_sha256 = require_digest(
        packet["body_sha256"], "STRATEGY_PACKET_DIGEST_INVALID")
    packet_body = dict(packet)
    packet_body.pop("body_sha256")
    if digest_document(packet_body) != expected_body_sha256:
        raise ContractError("STRATEGY_PACKET_DIGEST_INVALID")
    if (
            packet["schema"] != "hepta.market-information-packet.v1" or
            packet["mode"] != "SHADOW" or
            packet["instrument"] != "EUR.USD"):
        raise ContractError("STRATEGY_PACKET_BOUNDARY_INVALID")
    strategy_binding = require_exact_fields(
        packet["strategy"], STRATEGY_BINDING_FIELDS,
        "STRATEGY_PACKET_BINDING_FIELDS_INVALID")
    context_builder = require_exact_fields(
        packet["context_builder"], CONTEXT_BUILDER_FIELDS,
        "STRATEGY_CONTEXT_BUILDER_FIELDS_INVALID")
    script_root = Path(__file__).resolve().parent
    evaluator_sha256 = digest_file(Path(__file__).resolve())
    builder_sha256 = digest_file(
        script_root / "hepta_market_context_builder.py")
    normalizer_sha256 = digest_file(
        script_root / "hepta_market_evidence_normalizer.py")
    contracts_sha256 = digest_file(
        script_root / "hepta_strategy_contracts.py")
    if (
            strategy_binding["strategy_id"] != config["strategy_id"] or
            strategy_binding["strategy_version"] !=
            config["strategy_version"] or
            strategy_binding["pinned_sha256"] != strategy_sha256 or
            strategy_binding["evaluator_sha256"] != evaluator_sha256 or
            strategy_binding["builder_sha256"] != builder_sha256 or
            strategy_binding["normalizer_sha256"] != normalizer_sha256 or
            strategy_binding["contracts_sha256"] != contracts_sha256 or
            strategy_binding["sha256_verified"] is not True or
            context_builder["schema"] !=
            "hepta.market-context-builder.v3" or
            context_builder["builder_sha256"] != builder_sha256 or
            context_builder["normalizer_sha256"] != normalizer_sha256 or
            context_builder["contracts_sha256"] != contracts_sha256 or
            context_builder["feature_calculation_version"] !=
            config["feature_calculation_version"]):
        raise ContractError("STRATEGY_PACKET_BINDING_INVALID")
    if (
            packet["authority"].get("paper_authorized") is not False or
            packet["authority"].get("live_authorized") is not False or
            packet["source_snapshot"].get("mutation_attempted") is not False or
            packet["source_snapshot"].get("direct_broker_access") is not False):
        raise ContractError("STRATEGY_PACKET_AUTHORITY_INVALID")
    evidence_refs = packet["evidence_refs"]
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise ContractError("STRATEGY_EVIDENCE_REFS_INVALID")
    for evidence_ref in evidence_refs:
        require_digest(evidence_ref, "STRATEGY_EVIDENCE_REFS_INVALID")

    evaluated_at_ms = require_int(
        packet["evaluated_at_ms"], "STRATEGY_PACKET_TIME_INVALID", minimum=0)
    started = evaluated_at_ms if started_at_ms is None else started_at_ms
    finished = started if finished_at_ms is None else finished_at_ms
    require_int(started, "STRATEGY_DECISION_TIME_INVALID", minimum=0)
    require_int(finished, "STRATEGY_DECISION_TIME_INVALID", minimum=started)

    reasons = _evidence_reasons(packet, config)
    regime = "unknown" if reasons else _classify_regime(packet, config)
    if regime not in config["setup"]["allowed_regimes"]:
        _append_reason(reasons, "REGIME_" + regime.upper() + "_NOT_ALLOWED")
    if regime == "event":
        _append_reason(reasons, "HIGH_IMPACT_EVENT_WINDOW")
    for reason in _cost_reasons(packet, config):
        _append_reason(reasons, reason)

    direction_reasons: list[str] = []
    side = _momentum_direction(packet, config, direction_reasons)
    for reason in direction_reasons:
        _append_reason(reasons, reason)

    decision_id = (
        f"{packet['campaign_id']}-decision-"
        f"{require_int(packet['iteration'], 'STRATEGY_ITERATION_INVALID', minimum=1):04d}"
    )
    base = {
        "decision_id": decision_id,
        "started_at_ms": started,
        "finished_at_ms": finished,
        "regime": regime,
        "evidence_refs": list(packet["evidence_refs"]),
        "conflicts": list(packet["information"]["conflicts"]),
        "campaign_open_request_id": None,
        "campaign_close_request_id": None,
    }
    if reasons or side is None:
        return {
            **base,
            "cycle_id": None,
            "setup_gates": [],
            "risk_challenges": reasons,
            "decision": "NO_TRADE",
            "reason_codes": reasons or ["NO_DIRECTION"],
            "trade_intent": None,
            "final_outcome": "NO_TRADE",
        }
    return {
        **base,
        "cycle_id": "shadow-cycle-" + digest_document(packet)[7:39],
        "setup_gates": [
            "AUTHORITATIVE_EVIDENCE",
            "FRESH_EVIDENCE",
            "REGIME_TREND",
            "MOMENTUM_CONFIRMED",
            "EMA_DIRECTION_CONFIRMED",
            "EVENT_WINDOW_CLEAR",
            "PORTFOLIO_FLAT",
            "EXPECTED_MOVE_COVERS_COST",
        ],
        "risk_challenges": [],
        "decision": "TRADE",
        "reason_codes": [],
        "trade_intent": _intent(packet, config, strategy_sha256, side),
        "final_outcome": "SHADOW_TRADE",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--started-at-ms", type=int)
    parser.add_argument("--finished-at-ms", type=int)
    arguments = parser.parse_args()
    try:
        config = load_strategy(arguments.strategy)
        package_sha256 = strategy_package_digest(arguments.strategy)
        packet = load_document(arguments.packet, "STRATEGY_PACKET")
        decision = evaluate(
            packet,
            config,
            package_sha256,
            started_at_ms=arguments.started_at_ms,
            finished_at_ms=arguments.finished_at_ms,
        )
        atomic_write_json(arguments.output, decision)
    except (ContractError, OSError, ValueError) as error:
        print(
            "hepta_eurusd_confirmed_momentum_strategy: FAIL: " + str(error),
            file=sys.stderr,
        )
        return 78
    print(
        f"hepta_eurusd_confirmed_momentum_strategy: PASS "
        f"{decision['decision']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
