#!/usr/bin/env python3

"""Validate the fail-closed SHADOW subset of the canonical decision receipt."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys
from typing import Any

from hepta_strategy_contracts import (
    ContractError,
    digest_document,
    load_document,
    require_bool,
    require_digest,
    require_exact_fields,
    require_int,
    require_number,
    require_text,
)


RECEIPT_FIELDS = frozenset({
    "schema", "campaign_id", "strategy_id", "strategy_version",
    "strategy_sha256", "decision_id", "cycle_id", "started_at_ms",
    "finished_at_ms", "paper_only", "live_authorized", "shadow_only",
    "information_packet_sha256", "catalog_sha256", "descriptor_sha256",
    "preflight_sha256", "regime", "setup_gates", "risk_challenges",
    "evidence_refs", "conflicts", "decision", "reason_codes",
    "trade_intent", "trade_intent_sha256", "campaign_open_request_id",
    "campaign_close_request_id", "mutation_attempted",
    "direct_broker_access", "final_outcome",
})
INTENT_FIELDS = frozenset({
    "schema", "paper_only", "strategy_id", "strategy_version",
    "strategy_sha256", "intent_id", "instrument", "symbol", "currency",
    "sec_type", "exchange", "side", "quantity", "order_type",
    "limit_price", "tif", "observed_bid", "observed_ask",
    "observed_at_ms", "expires_at_ms", "entry_thesis",
    "invalidation_condition", "max_holding_ms", "max_adverse_move",
    "expected_slippage", "exit_plan",
})
REGIMES = frozenset({
    "trend", "range", "event", "illiquid", "transition", "unknown",
})


def _identifier_list(
    value: Any,
    reason: str,
    *,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ContractError(reason)
    result = [
        require_text(item, reason, identifier=True)
        for item in value
    ]
    if len(result) != len(set(result)):
        raise ContractError(reason)
    return result


def _digest_list(value: Any, reason: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ContractError(reason)
    result = [require_digest(item, reason) for item in value]
    if len(result) != len(set(result)):
        raise ContractError(reason)
    return result


def _validate_intent(
    intent_value: Any,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    intent = require_exact_fields(
        intent_value, INTENT_FIELDS, "RECEIPT_INTENT_FIELDS_INVALID")
    if (
            intent["schema"] != "hepta.trade-intent.v1" or
            intent["paper_only"] is not True or
            intent["strategy_id"] != receipt["strategy_id"] or
            intent["strategy_version"] != receipt["strategy_version"] or
            intent["strategy_sha256"] != receipt["strategy_sha256"] or
            intent["instrument"] != "EUR.USD" or
            intent["symbol"] != "EUR" or
            intent["currency"] != "USD" or
            intent["sec_type"] != "CASH" or
            intent["exchange"] != "IDEALPRO" or
            intent["side"] not in {"BUY", "SELL"} or
            intent["order_type"] != "LMT" or
            intent["tif"] != "DAY"):
        raise ContractError("RECEIPT_INTENT_BINDING_INVALID")
    require_text(
        intent["intent_id"], "RECEIPT_INTENT_ID_INVALID", identifier=True)
    require_int(
        intent["quantity"], "RECEIPT_INTENT_QUANTITY_INVALID",
        minimum=1, maximum=1000)
    limit_price = require_number(
        intent["limit_price"], "RECEIPT_INTENT_PRICE_INVALID", positive=True)
    observed_bid = require_number(
        intent["observed_bid"], "RECEIPT_INTENT_PRICE_INVALID", positive=True)
    observed_ask = require_number(
        intent["observed_ask"], "RECEIPT_INTENT_PRICE_INVALID", positive=True)
    if observed_bid > observed_ask:
        raise ContractError("RECEIPT_INTENT_PRICE_INVALID")
    expected_limit = (
        observed_ask if intent["side"] == "BUY" else observed_bid)
    if not math.isclose(
            limit_price, expected_limit, rel_tol=0.0, abs_tol=1e-12):
        raise ContractError("RECEIPT_INTENT_LIMIT_RULE_INVALID")
    observed_at_ms = require_int(
        intent["observed_at_ms"], "RECEIPT_INTENT_TIME_INVALID", minimum=0)
    expires_at_ms = require_int(
        intent["expires_at_ms"], "RECEIPT_INTENT_TIME_INVALID",
        minimum=observed_at_ms + 1)
    if (
            observed_at_ms > receipt["started_at_ms"] or
            expires_at_ms < receipt["finished_at_ms"]):
        raise ContractError("RECEIPT_INTENT_TIME_INVALID")
    require_text(
        intent["entry_thesis"], "RECEIPT_INTENT_TEXT_INVALID",
        maximum=4096)
    require_text(
        intent["invalidation_condition"], "RECEIPT_INTENT_TEXT_INVALID",
        maximum=4096)
    require_text(
        intent["exit_plan"], "RECEIPT_INTENT_TEXT_INVALID", maximum=4096)
    require_int(
        intent["max_holding_ms"], "RECEIPT_INTENT_HOLDING_INVALID",
        minimum=1, maximum=86400000)
    require_number(
        intent["max_adverse_move"], "RECEIPT_INTENT_RISK_INVALID",
        minimum=0.0)
    require_number(
        intent["expected_slippage"], "RECEIPT_INTENT_RISK_INVALID",
        minimum=0.0)
    return intent


def validate(document: dict[str, Any]) -> None:
    receipt = require_exact_fields(
        document, RECEIPT_FIELDS, "RECEIPT_FIELDS_INVALID")
    if receipt["schema"] != "hepta.autonomous-paper-decision-receipt.v1":
        raise ContractError("RECEIPT_SCHEMA_INVALID")
    for field in (
            "campaign_id", "strategy_id", "strategy_version", "decision_id"):
        require_text(
            receipt[field], f"RECEIPT_{field.upper()}_INVALID",
            identifier=True)
    require_digest(
        receipt["strategy_sha256"], "RECEIPT_STRATEGY_DIGEST_INVALID")
    require_digest(
        receipt["information_packet_sha256"],
        "RECEIPT_PACKET_DIGEST_INVALID")
    require_digest(
        receipt["catalog_sha256"], "RECEIPT_CATALOG_DIGEST_INVALID")
    require_digest(
        receipt["descriptor_sha256"], "RECEIPT_DESCRIPTOR_DIGEST_INVALID")
    started_at_ms = require_int(
        receipt["started_at_ms"], "RECEIPT_TIME_INVALID", minimum=0)
    require_int(
        receipt["finished_at_ms"], "RECEIPT_TIME_INVALID",
        minimum=started_at_ms)
    require_bool(
        receipt["paper_only"], True, "RECEIPT_PAPER_BOUNDARY_INVALID")
    require_bool(
        receipt["live_authorized"], False, "RECEIPT_LIVE_BOUNDARY_INVALID")
    require_bool(
        receipt["shadow_only"], True, "RECEIPT_SHADOW_BOUNDARY_INVALID")
    require_bool(
        receipt["mutation_attempted"], False,
        "RECEIPT_MUTATION_BOUNDARY_INVALID")
    require_bool(
        receipt["direct_broker_access"], False,
        "RECEIPT_BROKER_BOUNDARY_INVALID")
    if (
            receipt["preflight_sha256"] is not None or
            receipt["campaign_open_request_id"] is not None or
            receipt["campaign_close_request_id"] is not None):
        raise ContractError("RECEIPT_SHADOW_REQUEST_BOUNDARY_INVALID")
    if receipt["regime"] not in REGIMES:
        raise ContractError("RECEIPT_REGIME_INVALID")
    setup_gates = _identifier_list(
        receipt["setup_gates"], "RECEIPT_SETUP_GATES_INVALID")
    risk_challenges = _identifier_list(
        receipt["risk_challenges"], "RECEIPT_RISK_CHALLENGES_INVALID")
    _digest_list(receipt["evidence_refs"], "RECEIPT_EVIDENCE_REFS_INVALID")
    _identifier_list(receipt["conflicts"], "RECEIPT_CONFLICTS_INVALID")
    reason_codes = _identifier_list(
        receipt["reason_codes"], "RECEIPT_REASON_CODES_INVALID")

    if receipt["decision"] == "NO_TRADE":
        if (
                receipt["cycle_id"] is not None or
                receipt["trade_intent"] is not None or
                receipt["trade_intent_sha256"] is not None or
                receipt["final_outcome"] != "NO_TRADE" or
                setup_gates or
                not reason_codes or
                risk_challenges != reason_codes):
            raise ContractError("RECEIPT_NO_TRADE_CONTRACT_INVALID")
        return
    if receipt["decision"] != "TRADE":
        raise ContractError("RECEIPT_DECISION_INVALID")
    require_text(
        receipt["cycle_id"], "RECEIPT_CYCLE_ID_INVALID", identifier=True)
    intent = _validate_intent(receipt["trade_intent"], receipt)
    intent_digest = require_digest(
        receipt["trade_intent_sha256"],
        "RECEIPT_INTENT_DIGEST_INVALID")
    if intent_digest != digest_document(intent):
        raise ContractError("RECEIPT_INTENT_DIGEST_MISMATCH")
    if (
            receipt["final_outcome"] != "SHADOW_TRADE" or
            not setup_gates or
            risk_challenges or
            reason_codes):
        raise ContractError("RECEIPT_SHADOW_TRADE_CONTRACT_INVALID")


def validate_observation_policy_binding(
    document: dict[str, Any],
    *,
    policy_sha256: str,
    campaign_sha256: str,
) -> None:
    validate(document)
    required = {
        require_digest(
            policy_sha256,
            "RECEIPT_POLICY_DIGEST_INVALID",
        ),
        require_digest(
            campaign_sha256,
            "RECEIPT_CAMPAIGN_DIGEST_INVALID",
        ),
    }
    if not required.issubset(set(document["evidence_refs"])):
        raise ContractError("RECEIPT_OBSERVATION_POLICY_BINDING_INVALID")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", type=Path)
    arguments = parser.parse_args()
    try:
        document = load_document(
            arguments.receipt, "RECEIPT", maximum_bytes=262144)
        validate(document)
    except (ContractError, OSError, ValueError) as error:
        print(
            "validate_hepta_strategy_decision_receipt: FAIL " + str(error),
            file=sys.stderr,
        )
        return 1
    print("validate_hepta_strategy_decision_receipt: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
