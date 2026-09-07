#!/usr/bin/env python3
"""Validate the source-controlled canonical IB PAPER profile boundary."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ACCOUNT_RE = re.compile(r"^DU[A-Z0-9]{1,16}$")
REQUIRED_KEYS = {
    "HEPTA_IB_EXECUTION_MODE",
    "HEPTA_IB_PAPER_ACCOUNT",
    "HEPTA_IB_PAPER_HOST",
    "HEPTA_IB_PAPER_PORT",
    "HEPTA_IB_PAPER_CLIENT_ID",
    "HEPTA_IB_PAPER_MAX_ORDER_QTY",
    "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL",
    "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE",
    "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS",
    "HEPTA_IB_PAPER_MAX_GROSS_POSITION",
    "HEPTA_IB_PAPER_QUOTE_CONTRACTS",
    "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT",
    "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS",
    "HEPTA_IB_EXECUTION_GATEWAY_UID",
    "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID",
    "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES",
    "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS",
    "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS",
    "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS",
}


class ProfileError(ValueError):
    pass


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError(f"duplicate policy key: {key}")
        result[key] = value
    return result


def load_policy(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ProfileError(f"non-finite policy number: {item}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProfileError(f"cannot load policy: {error}") from error
    if not isinstance(value, dict) or value.get("schema") != (
        "heptatrader.ib-paper-profile-policy.v1"
    ):
        raise ProfileError("unsupported profile policy schema")
    expected_keys = {
        "schema",
        "execution_mode",
        "allowed_hosts",
        "allowed_ports",
        "maximum_active_orders",
        "maximum_quote_contracts",
        "allowed_security_types",
        "require_primary_quote_contract",
        "require_order_quantity_not_above_gross_position",
        "live_authorized",
        "rationale",
    }
    if set(value) != expected_keys:
        raise ProfileError("profile policy fields are not canonical")
    if value["execution_mode"] != "PAPER" or value["live_authorized"] is not False:
        raise ProfileError("profile policy must be PAPER-only and LIVE-disabled")
    if value["maximum_active_orders"] != 1:
        raise ProfileError("profile policy must keep a single active order")
    if value["maximum_quote_contracts"] != 1:
        raise ProfileError("profile policy must keep a single quote contract")
    if value["allowed_security_types"] != ["CASH"]:
        raise ProfileError("profile policy must currently allow only CASH")
    if value["require_primary_quote_contract"] is not True:
        raise ProfileError("primary quote contract binding is required")
    if value["require_order_quantity_not_above_gross_position"] is not True:
        raise ProfileError("quantity/gross bound is required")
    if not isinstance(value["allowed_hosts"], list) or not value["allowed_hosts"]:
        raise ProfileError("allowed_hosts is invalid")
    if not isinstance(value["allowed_ports"], list) or not value["allowed_ports"]:
        raise ProfileError("allowed_ports is invalid")
    if not isinstance(value["rationale"], str) or not value["rationale"]:
        raise ProfileError("profile policy rationale is required")
    return value


def parse_environment(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ProfileError(f"cannot read environment template: {error}") from error
    values: dict[str, str] = {}
    for line_number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ProfileError(f"line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        if KEY_RE.fullmatch(key) is None:
            raise ProfileError(f"line {line_number}: invalid key {key!r}")
        if key in values:
            raise ProfileError(f"line {line_number}: duplicate key {key}")
        if not value or value != value.strip() or any(
            token in value for token in ("$", "`", "\"", "'", "\\")
        ):
            raise ProfileError(f"line {line_number}: non-canonical value for {key}")
        values[key] = value
    missing = sorted(REQUIRED_KEYS - set(values))
    if missing:
        raise ProfileError("missing required keys: " + ", ".join(missing))
    extras = sorted(set(values) - REQUIRED_KEYS)
    if extras:
        raise ProfileError("unexpected profile keys: " + ", ".join(extras))
    return values


def positive_integer(values: dict[str, str], key: str) -> int:
    value = values[key]
    if not value.isascii() or not value.isdecimal():
        raise ProfileError(f"{key} must be a positive decimal integer")
    parsed = int(value, 10)
    if parsed <= 0:
        raise ProfileError(f"{key} must be positive")
    return parsed


def positive_number(values: dict[str, str], key: str) -> float:
    try:
        parsed = float(values[key])
    except ValueError as error:
        raise ProfileError(f"{key} must be numeric") from error
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ProfileError(f"{key} must be a finite positive number")
    return parsed


def split_contracts(value: str) -> list[list[str]]:
    # A future version may use comma-separated contracts. The v1 canonical
    # profile deliberately permits exactly one five-field contract.
    contracts = value.split(",")
    parsed: list[list[str]] = []
    for contract in contracts:
        fields = contract.split("|")
        if len(fields) != 5 or any(not field for field in fields):
            raise ProfileError("quote contract must have instrument|symbol|secType|exchange|currency")
        parsed.append(fields)
    return parsed


def validate(
    policy_path: Path = ROOT / "docs/ib-paper-profile-policy-v1.json",
    environment_path: Path = ROOT / "systemd/hepta-execution-ib-paper.env.example",
) -> list[str]:
    errors: list[str] = []
    try:
        policy = load_policy(policy_path)
        values = parse_environment(environment_path)
        if values["HEPTA_IB_EXECUTION_MODE"] != policy["execution_mode"]:
            raise ProfileError("execution mode does not match policy")
        if values["HEPTA_IB_PAPER_HOST"] not in policy["allowed_hosts"]:
            raise ProfileError("IB host is not an allowed loopback address")
        if positive_integer(values, "HEPTA_IB_PAPER_PORT") not in policy["allowed_ports"]:
            raise ProfileError("IB port is not allowed by policy")
        if ACCOUNT_RE.fullmatch(values["HEPTA_IB_PAPER_ACCOUNT"]) is None:
            raise ProfileError("IB PAPER account must be a canonical DU account")
        active = positive_integer(values, "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS")
        if active > policy["maximum_active_orders"]:
            raise ProfileError("active-order limit exceeds the canonical single-order boundary")
        quantity = positive_number(values, "HEPTA_IB_PAPER_MAX_ORDER_QTY")
        gross = positive_number(values, "HEPTA_IB_PAPER_MAX_GROSS_POSITION")
        if policy["require_order_quantity_not_above_gross_position"] and quantity > gross:
            raise ProfileError("maximum order quantity exceeds maximum gross position")
        positive_number(values, "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL")
        positive_integer(values, "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE")
        positive_integer(values, "HEPTA_IB_PAPER_CLIENT_ID")
        positive_integer(values, "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS")
        positive_integer(values, "HEPTA_IB_EXECUTION_GATEWAY_UID")
        positive_integer(values, "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES")
        positive_integer(values, "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS")
        positive_integer(values, "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS")
        positive_integer(values, "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS")
        contracts = split_contracts(values["HEPTA_IB_PAPER_QUOTE_CONTRACTS"])
        if len(contracts) > policy["maximum_quote_contracts"]:
            raise ProfileError("quote contract count exceeds policy")
        if any(contract[2] not in policy["allowed_security_types"] for contract in contracts):
            raise ProfileError("quote contract security type is not allowed")
        if policy["require_primary_quote_contract"] and (
            values["HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT"] != contracts[0][0]
        ):
            raise ProfileError("primary quote instrument does not match the canonical contract")
        if "LIVE" in "\n".join(f"{key}={value}" for key, value in values.items()):
            raise ProfileError("LIVE token is forbidden in the canonical PAPER profile")
    except ProfileError as error:
        errors.append(str(error))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "docs/ib-paper-profile-policy-v1.json",
    )
    parser.add_argument(
        "--environment",
        type=Path,
        default=ROOT / "systemd/hepta-execution-ib-paper.env.example",
    )
    args = parser.parse_args(argv)
    errors = validate(args.policy, args.environment)
    for error in errors:
        print(f"[IB-PAPER-PROFILE] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[IB-PAPER-PROFILE] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
