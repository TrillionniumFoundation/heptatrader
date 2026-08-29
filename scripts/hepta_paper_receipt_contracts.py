#!/usr/bin/env python3

"""Strict, non-authorizing contracts for Hepta PAPER evidence receipts.

The module validates evidence only.  A structurally valid receipt never grants
session, broker, PAPER, or LIVE authority and never replaces an authoritative
broker reconciliation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Optional


BINDINGS_SCHEMA = "hepta.paper-receipt-bindings.v3"
EVIDENCE_BINDINGS_SCHEMA = "hepta.paper-receipt-evidence-bindings.v3"
DECISION_SCHEMA = "hepta.paper-decision-receipt.v3"
CYCLE_SCHEMA = "hepta.paper-cycle-receipt.v3"
VERSION = 3
MAX_BYTES = 1024 * 1024

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
DOMAIN = re.compile(r"[a-z][a-z0-9-]{0,31}")
TOOL_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,127}")
REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{1,95}")

BINDING_DOCUMENT_FIELDS = {
    "schema", "version", "bindings", "bindings_sha256",
}
EVIDENCE_BINDING_DOCUMENT_FIELDS = {
    "schema", "version", "evidence_bindings",
    "evidence_bindings_sha256",
}
EVIDENCE_BINDING_FIELDS = {
    "bindings_sha256", "receipt_schema", "payload_sha256", "evidence",
}
DECISION_EVIDENCE_BINDING_FIELDS = {
    "information_packet_sha256", "preflight_sha256",
    "tool_evidence_sha256",
}
CYCLE_EVIDENCE_BINDING_FIELDS = {
    "preflight_sha256", "preview_receipt_sha256",
    "broker_order_id_sha256", "journal_sha256",
    "event_summary_sha256", "tool_evidence_sha256",
    "final_authoritative_state_sha256", "final_snapshot_sha256",
    "final_service_epoch", "final_fencing_generation",
}
BINDING_FIELDS = {
    "campaign_id", "domain_id", "policy_sha256",
    "strategy_id", "strategy_version", "strategy_sha256",
    "decision_id", "decision_sha256", "cycle_id",
    "intent_id", "intent_sha256",
    "tool_catalog_sha256", "tool_descriptor_set_sha256", "tool_calls",
}
TOOL_BINDING_FIELDS = {
    "tool_call_id", "tool_name", "tool_descriptor_sha256", "effect",
}
RECEIPT_FIELDS = {
    "schema", "version", "payload", "payload_sha256",
}
DECISION_PAYLOAD_FIELDS = {
    "bindings", "bindings_sha256",
    "started_at_ms", "finished_at_ms",
    "paper_only", "live_authorized", "direct_broker_access",
    "shadow_only", "information_packet_sha256", "preflight_sha256",
    "decision", "reason_codes", "mutation_attempted",
    "tool_evidence", "final_outcome",
}
CYCLE_PAYLOAD_FIELDS = {
    "bindings", "bindings_sha256",
    "started_at_ms", "finished_at_ms",
    "paper_only", "live_authorized", "direct_broker_access",
    "execution_mode", "preflight_sha256", "preview_receipt_sha256",
    "broker_order_id_sha256", "journal_sha256",
    "event_summary_sha256", "mutation_attempted",
    "tool_evidence", "final_authoritative_state",
    "cleanup_complete", "reason_codes", "final_outcome",
}
TOOL_EVIDENCE_FIELDS = {
    "tool_call_id", "tool_name", "tool_descriptor_sha256", "effect",
    "phase", "request_sha256", "response_sha256", "status", "reason_code",
}
FINAL_STATE_FIELDS = {
    "authoritative", "account_complete", "snapshot_sha256",
    "service_epoch", "fencing_generation",
    "active_order_id_sha256s", "positions",
    "gross_absolute_position", "authorized_connector_count", "end_flat",
}
POSITION_FIELDS = {"instrument", "quantity"}

# Closed against PAPER-visible TradingToolRegistry tools plus the four
# canonical hepta-campaignctl actions.  WATCH_ONLY_NON_PAPER_TOOLS are
# deliberately invalid in PAPER receipts.  Adding or renaming a PAPER-visible
# tool is a receipt-contract change: bump TOOL_POLICY_VERSION and the receipt
# schema before extending this map.  Descriptor-provided effect text is
# evidence, never authority for this classification.
TOOL_POLICY_VERSION = 2
WATCH_ONLY_NON_PAPER_TOOLS = frozenset({"watch.get_snapshot"})
CANONICAL_TOOL_EFFECTS = {
    "system.tools.list": "READ_ONLY",
    "system.tools.describe": "READ_ONLY",
    "system.cancel_request": "READ_ONLY",
    "market.get_quote": "READ_ONLY",
    "account.get_summary": "READ_ONLY",
    "portfolio.list_positions": "READ_ONLY",
    "orders.list": "READ_ONLY",
    "risk.get_limits": "READ_ONLY",
    "risk.preview_order": "READ_ONLY",
    "events.wait": "READ_ONLY",
    "system.get_health": "READ_ONLY",
    "execution.get_command_status": "READ_ONLY",
    "trade.place_order": "MUTATION",
    "trade.cancel_order": "MUTATION",
    "risk.preview_flatten": "READ_ONLY",
    "trade.flatten_position": "MUTATION",
    "campaign.status": "READ_ONLY",
    "campaign.open_cycle": "CONTROL",
    "campaign.close_cycle": "CONTROL",
    "campaign.halt": "CONTROL",
}
EFFECTS = {"READ_ONLY", "CONTROL", "MUTATION"}
PHASES = {
    "DISCOVERY", "DESCRIBE", "PREFLIGHT", "SNAPSHOT", "DECISION",
    "OPEN", "PREVIEW", "PLACE", "CANCEL", "FLATTEN", "CLOSE",
    "RECONCILE", "CLEANUP", "HALT",
}
DECISION_PHASES = {
    "DISCOVERY", "DESCRIBE", "PREFLIGHT", "SNAPSHOT", "DECISION",
}
PHASE_EFFECTS = {
    "DISCOVERY": "READ_ONLY",
    "DESCRIBE": "READ_ONLY",
    "PREFLIGHT": "READ_ONLY",
    "SNAPSHOT": "READ_ONLY",
    "DECISION": "READ_ONLY",
    "OPEN": "CONTROL",
    "PREVIEW": "READ_ONLY",
    "PLACE": "MUTATION",
    "CANCEL": "MUTATION",
    "FLATTEN": "MUTATION",
    "CLOSE": "CONTROL",
    "RECONCILE": "READ_ONLY",
    "CLEANUP": "READ_ONLY",
    "HALT": "CONTROL",
}
PHASE_TOOL_NAMES = {
    "DISCOVERY": frozenset({"system.tools.list"}),
    "DESCRIBE": frozenset({"system.tools.describe"}),
    "PREFLIGHT": frozenset({
        "system.get_health", "market.get_quote", "account.get_summary",
        "portfolio.list_positions", "orders.list", "risk.get_limits",
        "campaign.status",
    }),
    "SNAPSHOT": frozenset({
        "system.get_health", "market.get_quote", "account.get_summary",
        "portfolio.list_positions", "orders.list", "risk.get_limits",
    }),
    "DECISION": frozenset({"market.get_quote", "events.wait"}),
    "OPEN": frozenset({"campaign.open_cycle"}),
    "PREVIEW": frozenset({
        "risk.preview_order", "risk.preview_flatten",
    }),
    "PLACE": frozenset({"trade.place_order"}),
    "CANCEL": frozenset({"trade.cancel_order"}),
    "FLATTEN": frozenset({"trade.flatten_position"}),
    "CLOSE": frozenset({"campaign.close_cycle"}),
    "RECONCILE": frozenset({
        "system.get_health", "account.get_summary",
        "portfolio.list_positions", "orders.list", "events.wait",
        "campaign.status", "execution.get_command_status",
    }),
    "CLEANUP": frozenset({
        "system.cancel_request", "system.get_health",
        "account.get_summary", "portfolio.list_positions", "orders.list",
        "risk.get_limits", "campaign.status", "execution.get_command_status",
    }),
    "HALT": frozenset({"campaign.halt"}),
}
if (
    set(PHASE_EFFECTS) != PHASES
    or set(PHASE_TOOL_NAMES) != PHASES
    or set(CANONICAL_TOOL_EFFECTS) & WATCH_ONLY_NON_PAPER_TOOLS
    or set().union(*PHASE_TOOL_NAMES.values())
    != set(CANONICAL_TOOL_EFFECTS)
    or any(
        CANONICAL_TOOL_EFFECTS.get(tool_name) != PHASE_EFFECTS[phase]
        for phase, tool_names in PHASE_TOOL_NAMES.items()
        for tool_name in tool_names
    )
):
    raise RuntimeError(
        "canonical PAPER receipt tool/phase policy is not closed")
CALL_STATUSES = {
    "OK", "PERMISSION_DENIED", "INVALID_TOOL", "REJECTED",
    "DUPLICATE", "UNCERTAIN", "ERROR",
}
DECISIONS = {"TRADE_CANDIDATE", "NO_TRADE"}
CYCLE_OUTCOMES = {
    "NO_TRADE", "CANCELLED_FLAT", "FILLED_AND_FLAT",
    "RECOVERED", "RECOVERY_REQUIRED",
}
SUCCESSFUL_CYCLE_OUTCOMES = {
    "NO_TRADE", "CANCELLED_FLAT", "FILLED_AND_FLAT", "RECOVERED",
}
FINAL_AUTHORITATIVE_READ_TOOLS = frozenset({
    "system.get_health",
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
})


class ReceiptContractError(RuntimeError):
    """Raised when a receipt or its independently pinned bindings are invalid."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _reject_float(_value: str) -> None:
    # Contract timestamps, quantities, and counts are exact integers.  Rejecting
    # all JSON floats also closes exponent-overflow paths such as 1e999.
    raise ValueError("non-integral JSON number")


def canonical_json(value: Any) -> bytes:
    """Return the one accepted serialization for a contract document."""

    try:
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ) + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ReceiptContractError("NON_CANONICAL_VALUE") from error


def canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _load_canonical(raw: bytes, label: str) -> dict[str, Any]:
    if len(raw) < 3 or len(raw) > MAX_BYTES:
        raise ReceiptContractError(f"{label}_SIZE_INVALID")
    try:
        document = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ReceiptContractError(f"{label}_JSON_INVALID") from error
    if not isinstance(document, dict):
        raise ReceiptContractError(f"{label}_ROOT_INVALID")
    if raw != canonical_json(document):
        raise ReceiptContractError(f"{label}_NOT_CANONICAL")
    return document


def _stable_read(path: Path, label: str) -> bytes:
    """Read one non-symlink regular file without accepting a metadata race."""

    try:
        before = os.lstat(path)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 3
            or before.st_size > MAX_BYTES
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise ReceiptContractError(f"{label}_METADATA_UNSAFE")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            raw = bytearray()
            while len(raw) <= MAX_BYTES:
                chunk = os.read(
                    descriptor, min(8192, MAX_BYTES + 1 - len(raw))
                )
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except ReceiptContractError:
        raise
    except OSError as error:
        raise ReceiptContractError(f"{label}_IO_ERROR") from error
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns",
    )
    if (
        len(raw) > MAX_BYTES
        or any(
            getattr(before, field) != getattr(opened, field)
            or getattr(opened, field) != getattr(after, field)
            for field in fields
        )
    ):
        raise ReceiptContractError(f"{label}_CHANGED")
    return bytes(raw)


def _exact_object(value: Any, fields: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ReceiptContractError(code)
    return value


def _text(
    value: Any,
    code: str,
    *,
    pattern: Optional[re.Pattern[str]] = None,
    maximum: int = 1024,
) -> str:
    try:
        encoded_size = (
            len(value.encode("utf-8")) if isinstance(value, str) else -1
        )
    except UnicodeEncodeError as error:
        raise ReceiptContractError(code) from error
    if (
        not isinstance(value, str)
        or not value
        or "\0" in value
        or encoded_size > maximum
        or (pattern is not None and pattern.fullmatch(value) is None)
    ):
        raise ReceiptContractError(code)
    return value


def _enum(value: Any, allowed: set[str], code: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ReceiptContractError(code)
    return value


def _identifier(value: Any, code: str) -> str:
    return _text(value, code, pattern=IDENTIFIER, maximum=128)


def _digest(value: Any, code: str) -> str:
    return _text(value, code, pattern=DIGEST, maximum=71)


def _optional_digest(value: Any, code: str) -> Optional[str]:
    if value is None:
        return None
    return _digest(value, code)


def _integer(
    value: Any,
    code: str,
    *,
    minimum: int = 0,
    maximum: int = 2**63 - 1,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise ReceiptContractError(code)
    return value


def _boolean(value: Any, code: str) -> bool:
    if not isinstance(value, bool):
        raise ReceiptContractError(code)
    return value


def _reason_codes(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 64:
        raise ReceiptContractError("REASON_CODES_INVALID")
    result: list[str] = []
    for item in value:
        result.append(
            _text(
                item,
                "REASON_CODE_INVALID",
                pattern=REASON_CODE,
                maximum=96,
            )
        )
    if len(result) != len(set(result)):
        raise ReceiptContractError("REASON_CODES_DUPLICATE")
    return result


def _validate_tool_bindings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 256:
        raise ReceiptContractError("TOOL_BINDINGS_INVALID")
    calls: list[dict[str, Any]] = []
    call_ids: set[str] = set()
    for value_call in value:
        call = _exact_object(
            value_call, TOOL_BINDING_FIELDS, "TOOL_BINDING_FIELDS_INVALID"
        )
        call_id = _identifier(
            call["tool_call_id"], "TOOL_BINDING_CALL_ID_INVALID"
        )
        if call_id in call_ids:
            raise ReceiptContractError("TOOL_BINDING_CALL_ID_DUPLICATE")
        call_ids.add(call_id)
        tool_name = _text(
            call["tool_name"],
            "TOOL_BINDING_NAME_INVALID",
            pattern=TOOL_NAME,
            maximum=128,
        )
        _digest(
            call["tool_descriptor_sha256"],
            "TOOL_BINDING_DESCRIPTOR_INVALID",
        )
        effect = _enum(
            call["effect"], EFFECTS, "TOOL_BINDING_EFFECT_INVALID")
        if tool_name in WATCH_ONLY_NON_PAPER_TOOLS:
            raise ReceiptContractError(
                "TOOL_BINDING_WATCH_ONLY_FORBIDDEN")
        expected_effect = CANONICAL_TOOL_EFFECTS.get(tool_name)
        if expected_effect is None:
            raise ReceiptContractError("TOOL_BINDING_NAME_UNKNOWN")
        if effect != expected_effect:
            raise ReceiptContractError("TOOL_BINDING_EFFECT_MISMATCH")
        calls.append(call)
    return calls


def _validate_bindings(
    value: Any, *, require_intent: Optional[bool] = None
) -> dict[str, Any]:
    bindings = _exact_object(
        value, BINDING_FIELDS, "BINDING_FIELDS_INVALID"
    )
    _identifier(bindings["campaign_id"], "BINDING_CAMPAIGN_INVALID")
    _text(
        bindings["domain_id"],
        "BINDING_DOMAIN_INVALID",
        pattern=DOMAIN,
        maximum=32,
    )
    _digest(bindings["policy_sha256"], "BINDING_POLICY_INVALID")
    _identifier(bindings["strategy_id"], "BINDING_STRATEGY_ID_INVALID")
    _identifier(
        bindings["strategy_version"], "BINDING_STRATEGY_VERSION_INVALID"
    )
    _digest(
        bindings["strategy_sha256"], "BINDING_STRATEGY_DIGEST_INVALID"
    )
    _identifier(bindings["decision_id"], "BINDING_DECISION_ID_INVALID")
    _digest(
        bindings["decision_sha256"], "BINDING_DECISION_DIGEST_INVALID"
    )
    _identifier(bindings["cycle_id"], "BINDING_CYCLE_INVALID")
    intent_id = bindings["intent_id"]
    intent_digest = bindings["intent_sha256"]
    if (intent_id is None) != (intent_digest is None):
        raise ReceiptContractError("BINDING_INTENT_PAIR_INVALID")
    if intent_id is not None:
        _identifier(intent_id, "BINDING_INTENT_ID_INVALID")
        _digest(intent_digest, "BINDING_INTENT_DIGEST_INVALID")
    if require_intent is True and intent_id is None:
        raise ReceiptContractError("BINDING_INTENT_REQUIRED")
    if require_intent is False and intent_id is not None:
        raise ReceiptContractError("BINDING_INTENT_FORBIDDEN")
    _digest(
        bindings["tool_catalog_sha256"], "BINDING_CATALOG_INVALID"
    )
    descriptor_set = _digest(
        bindings["tool_descriptor_set_sha256"],
        "BINDING_DESCRIPTOR_SET_INVALID",
    )
    calls = _validate_tool_bindings(bindings["tool_calls"])
    if descriptor_set != canonical_sha256(calls):
        raise ReceiptContractError(
            "BINDING_DESCRIPTOR_SET_DIGEST_MISMATCH")
    return bindings


def validate_bindings_document(document: Any) -> dict[str, Any]:
    root = _exact_object(
        document,
        BINDING_DOCUMENT_FIELDS,
        "BINDING_DOCUMENT_FIELDS_INVALID",
    )
    if (
        root["schema"] != BINDINGS_SCHEMA
        or root["version"] != VERSION
    ):
        raise ReceiptContractError("BINDING_DOCUMENT_IDENTITY_INVALID")
    bindings = _validate_bindings(root["bindings"])
    claimed = _digest(
        root["bindings_sha256"], "BINDING_DOCUMENT_DIGEST_INVALID"
    )
    if claimed != canonical_sha256(bindings):
        raise ReceiptContractError("BINDING_DOCUMENT_DIGEST_MISMATCH")
    return root


def validate_evidence_bindings_document(
    document: Any, expected_bindings_document: Any
) -> dict[str, Any]:
    """Validate the separately supplied, post-run evidence trust anchor."""

    expected_bindings = validate_bindings_document(
        expected_bindings_document)
    root = _exact_object(
        document,
        EVIDENCE_BINDING_DOCUMENT_FIELDS,
        "EVIDENCE_BINDING_DOCUMENT_FIELDS_INVALID",
    )
    if (
        root["schema"] != EVIDENCE_BINDINGS_SCHEMA
        or root["version"] != VERSION
    ):
        raise ReceiptContractError(
            "EVIDENCE_BINDING_DOCUMENT_IDENTITY_INVALID")
    evidence_bindings = _exact_object(
        root["evidence_bindings"],
        EVIDENCE_BINDING_FIELDS,
        "EVIDENCE_BINDING_FIELDS_INVALID",
    )
    bindings_digest = _digest(
        evidence_bindings["bindings_sha256"],
        "EVIDENCE_BINDING_BINDINGS_DIGEST_INVALID",
    )
    if bindings_digest != expected_bindings["bindings_sha256"]:
        raise ReceiptContractError(
            "EVIDENCE_BINDING_BINDINGS_DIGEST_MISMATCH")
    receipt_schema = _enum(
        evidence_bindings["receipt_schema"],
        {DECISION_SCHEMA, CYCLE_SCHEMA},
        "EVIDENCE_BINDING_RECEIPT_SCHEMA_INVALID",
    )
    _digest(
        evidence_bindings["payload_sha256"],
        "EVIDENCE_BINDING_PAYLOAD_DIGEST_INVALID",
    )
    if receipt_schema == DECISION_SCHEMA:
        evidence = _exact_object(
            evidence_bindings["evidence"],
            DECISION_EVIDENCE_BINDING_FIELDS,
            "DECISION_EVIDENCE_BINDING_FIELDS_INVALID",
        )
        _digest(
            evidence["information_packet_sha256"],
            "DECISION_EVIDENCE_INFORMATION_PACKET_INVALID",
        )
        _optional_digest(
            evidence["preflight_sha256"],
            "DECISION_EVIDENCE_PREFLIGHT_INVALID",
        )
        _digest(
            evidence["tool_evidence_sha256"],
            "DECISION_EVIDENCE_TOOL_EVIDENCE_INVALID",
        )
    else:
        evidence = _exact_object(
            evidence_bindings["evidence"],
            CYCLE_EVIDENCE_BINDING_FIELDS,
            "CYCLE_EVIDENCE_BINDING_FIELDS_INVALID",
        )
        _digest(
            evidence["preflight_sha256"],
            "CYCLE_EVIDENCE_PREFLIGHT_INVALID",
        )
        _optional_digest(
            evidence["preview_receipt_sha256"],
            "CYCLE_EVIDENCE_PREVIEW_INVALID",
        )
        _optional_digest(
            evidence["broker_order_id_sha256"],
            "CYCLE_EVIDENCE_ORDER_ID_INVALID",
        )
        _digest(
            evidence["journal_sha256"],
            "CYCLE_EVIDENCE_JOURNAL_INVALID",
        )
        _digest(
            evidence["event_summary_sha256"],
            "CYCLE_EVIDENCE_EVENT_SUMMARY_INVALID",
        )
        _digest(
            evidence["tool_evidence_sha256"],
            "CYCLE_EVIDENCE_TOOL_EVIDENCE_INVALID",
        )
        _digest(
            evidence["final_authoritative_state_sha256"],
            "CYCLE_EVIDENCE_FINAL_STATE_INVALID",
        )
        _digest(
            evidence["final_snapshot_sha256"],
            "CYCLE_EVIDENCE_FINAL_SNAPSHOT_INVALID",
        )
        _identifier(
            evidence["final_service_epoch"],
            "CYCLE_EVIDENCE_FINAL_EPOCH_INVALID",
        )
        _integer(
            evidence["final_fencing_generation"],
            "CYCLE_EVIDENCE_FINAL_FENCING_INVALID",
            minimum=1,
        )
    claimed = _digest(
        root["evidence_bindings_sha256"],
        "EVIDENCE_BINDING_DOCUMENT_DIGEST_INVALID",
    )
    if claimed != canonical_sha256(evidence_bindings):
        raise ReceiptContractError(
            "EVIDENCE_BINDING_DOCUMENT_DIGEST_MISMATCH")
    return root


def _validate_tool_evidence(
    value: Any,
    anchored_calls: list[dict[str, Any]],
    *,
    read_only: bool,
) -> bool:
    if not isinstance(value, list) or len(value) != len(anchored_calls):
        raise ReceiptContractError("TOOL_EVIDENCE_COUNT_INVALID")
    mutation_attempted = False
    for index, value_call in enumerate(value):
        call = _exact_object(
            value_call,
            TOOL_EVIDENCE_FIELDS,
            "TOOL_EVIDENCE_FIELDS_INVALID",
        )
        anchor = anchored_calls[index]
        for field in TOOL_BINDING_FIELDS:
            if call[field] != anchor[field]:
                raise ReceiptContractError("TOOL_EVIDENCE_BINDING_MISMATCH")
        phase = _enum(
            call["phase"], PHASES, "TOOL_EVIDENCE_PHASE_INVALID")
        if call["tool_name"] in WATCH_ONLY_NON_PAPER_TOOLS:
            raise ReceiptContractError(
                "TOOL_EVIDENCE_WATCH_ONLY_FORBIDDEN")
        if read_only and call["effect"] != "READ_ONLY":
            raise ReceiptContractError(
                "DECISION_NON_READ_ONLY_TOOL_FORBIDDEN")
        if call["effect"] != PHASE_EFFECTS[phase]:
            raise ReceiptContractError("TOOL_EVIDENCE_PHASE_EFFECT_MISMATCH")
        if call["tool_name"] not in PHASE_TOOL_NAMES[phase]:
            raise ReceiptContractError("TOOL_EVIDENCE_PHASE_NAME_MISMATCH")
        if read_only and phase not in DECISION_PHASES:
            raise ReceiptContractError("DECISION_TOOL_PHASE_FORBIDDEN")
        _digest(
            call["request_sha256"], "TOOL_EVIDENCE_REQUEST_INVALID"
        )
        _digest(
            call["response_sha256"], "TOOL_EVIDENCE_RESPONSE_INVALID"
        )
        status = _enum(
            call["status"], CALL_STATUSES,
            "TOOL_EVIDENCE_STATUS_INVALID")
        reason_code = call["reason_code"]
        if status == "OK":
            if (
                not isinstance(reason_code, str)
                or reason_code not in {"", "OK"}
            ):
                raise ReceiptContractError(
                    "TOOL_EVIDENCE_SUCCESS_REASON_INVALID")
        else:
            _text(
                reason_code,
                "TOOL_EVIDENCE_REASON_REQUIRED",
                pattern=REASON_CODE,
                maximum=96,
            )
        if call["effect"] == "MUTATION":
            mutation_attempted = True
    return mutation_attempted


def _require_reported_tool_reasons(
    calls: list[dict[str, Any]], reasons: list[str]
) -> None:
    unreported = {
        call["reason_code"]
        for call in calls
        if call["status"] != "OK"
    } - set(reasons)
    if unreported:
        raise ReceiptContractError("TOOL_EVIDENCE_REASON_UNREPORTED")


def _phase_calls(
    calls: list[dict[str, Any]],
    phase: str,
    *,
    required: bool,
    maximum: Optional[int] = 1,
) -> list[tuple[int, dict[str, Any]]]:
    matching = [
        (index, call)
        for index, call in enumerate(calls)
        if call["phase"] == phase
    ]
    if (
        (maximum is not None and len(matching) > maximum)
        or (required and not matching)
    ):
        raise ReceiptContractError(f"CYCLE_{phase}_PHASE_INVALID")
    return matching


def _named_phase_calls(
    calls: list[tuple[int, dict[str, Any]]], tool_name: str
) -> list[tuple[int, dict[str, Any]]]:
    matching = [
        item for item in calls if item[1]["tool_name"] == tool_name
    ]
    if len(matching) > 1:
        raise ReceiptContractError("CYCLE_PREVIEW_TOOL_DUPLICATE")
    return matching


def _validate_common_payload(
    payload: dict[str, Any],
    expected_bindings: dict[str, Any],
    *,
    require_intent: Optional[bool],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    bindings = _validate_bindings(
        payload["bindings"], require_intent=require_intent
    )
    if bindings != expected_bindings:
        raise ReceiptContractError("EXPECTED_BINDINGS_MISMATCH")
    binding_digest = _digest(
        payload["bindings_sha256"], "PAYLOAD_BINDINGS_DIGEST_INVALID"
    )
    if binding_digest != canonical_sha256(bindings):
        raise ReceiptContractError("PAYLOAD_BINDINGS_DIGEST_MISMATCH")
    started = _integer(
        payload["started_at_ms"], "PAYLOAD_STARTED_AT_INVALID"
    )
    finished = _integer(
        payload["finished_at_ms"], "PAYLOAD_FINISHED_AT_INVALID"
    )
    if finished < started:
        raise ReceiptContractError("PAYLOAD_TIME_ORDER_INVALID")
    if payload["paper_only"] is not True:
        raise ReceiptContractError("PAPER_ONLY_REQUIRED")
    if payload["live_authorized"] is not False:
        raise ReceiptContractError("LIVE_AUTHORIZATION_FORBIDDEN")
    if payload["direct_broker_access"] is not False:
        raise ReceiptContractError("DIRECT_BROKER_ACCESS_FORBIDDEN")
    calls = _validate_tool_bindings(bindings["tool_calls"])
    if not calls:
        raise ReceiptContractError("TOOL_BINDINGS_REQUIRED")
    return bindings, calls


def _validate_decision_payload(
    payload_value: Any, expected_bindings: dict[str, Any]
) -> dict[str, Any]:
    payload = _exact_object(
        payload_value,
        DECISION_PAYLOAD_FIELDS,
        "DECISION_PAYLOAD_FIELDS_INVALID",
    )
    decision = _enum(
        payload["decision"], DECISIONS, "DECISION_VALUE_INVALID")
    require_intent = decision == "TRADE_CANDIDATE"
    _bindings, calls = _validate_common_payload(
        payload, expected_bindings, require_intent=require_intent
    )
    if payload["shadow_only"] is not True:
        raise ReceiptContractError("DECISION_SHADOW_ONLY_REQUIRED")
    _digest(
        payload["information_packet_sha256"],
        "DECISION_INFORMATION_PACKET_INVALID",
    )
    preflight = _optional_digest(
        payload["preflight_sha256"], "DECISION_PREFLIGHT_INVALID"
    )
    reasons = _reason_codes(payload["reason_codes"])
    if decision == "NO_TRADE":
        if not reasons:
            raise ReceiptContractError("NO_TRADE_REASON_REQUIRED")
    elif reasons or preflight is None:
        raise ReceiptContractError("TRADE_CANDIDATE_EVIDENCE_INVALID")
    if payload["mutation_attempted"] is not False:
        raise ReceiptContractError("DECISION_MUTATION_FORBIDDEN")
    observed_mutation = _validate_tool_evidence(
        payload["tool_evidence"], calls, read_only=True
    )
    if observed_mutation:
        raise ReceiptContractError("DECISION_MUTATION_FORBIDDEN")
    _require_reported_tool_reasons(payload["tool_evidence"], reasons)
    if payload["final_outcome"] != decision:
        raise ReceiptContractError("DECISION_FINAL_OUTCOME_MISMATCH")
    return payload


def _validate_final_state(value: Any) -> dict[str, Any]:
    state = _exact_object(
        value, FINAL_STATE_FIELDS, "FINAL_STATE_FIELDS_INVALID"
    )
    _boolean(state["authoritative"], "FINAL_STATE_AUTHORITY_INVALID")
    _boolean(
        state["account_complete"], "FINAL_STATE_ACCOUNT_COMPLETE_INVALID"
    )
    _digest(state["snapshot_sha256"], "FINAL_STATE_SNAPSHOT_INVALID")
    _identifier(state["service_epoch"], "FINAL_STATE_EPOCH_INVALID")
    _integer(
        state["fencing_generation"],
        "FINAL_STATE_FENCING_INVALID",
        minimum=1,
    )
    order_ids = state["active_order_id_sha256s"]
    if not isinstance(order_ids, list) or len(order_ids) > 128:
        raise ReceiptContractError("FINAL_STATE_ORDERS_INVALID")
    validated_order_ids: list[str] = []
    for order_id in order_ids:
        validated_order_ids.append(
            _digest(order_id, "FINAL_STATE_ORDER_DIGEST_INVALID")
        )
    if len(validated_order_ids) != len(set(validated_order_ids)):
        raise ReceiptContractError("FINAL_STATE_ORDERS_INVALID")
    positions = state["positions"]
    if not isinstance(positions, list) or len(positions) > 128:
        raise ReceiptContractError("FINAL_STATE_POSITIONS_INVALID")
    observed_gross = 0
    seen_instruments: set[str] = set()
    for value_position in positions:
        position = _exact_object(
            value_position, POSITION_FIELDS, "FINAL_STATE_POSITION_FIELDS_INVALID"
        )
        instrument = _identifier(
            position["instrument"], "FINAL_STATE_INSTRUMENT_INVALID"
        )
        if instrument in seen_instruments:
            raise ReceiptContractError("FINAL_STATE_POSITION_DUPLICATE")
        seen_instruments.add(instrument)
        quantity = _integer(
            position["quantity"],
            "FINAL_STATE_QUANTITY_INVALID",
            minimum=-(2**63) + 1,
        )
        if quantity == 0:
            raise ReceiptContractError("FINAL_STATE_QUANTITY_INVALID")
        observed_gross += abs(quantity)
        if observed_gross > 2**63 - 1:
            raise ReceiptContractError("FINAL_STATE_GROSS_INVALID")
    gross = _integer(
        state["gross_absolute_position"], "FINAL_STATE_GROSS_INVALID"
    )
    if gross != observed_gross:
        raise ReceiptContractError("FINAL_STATE_GROSS_MISMATCH")
    _integer(
        state["authorized_connector_count"],
        "FINAL_STATE_CONNECTOR_COUNT_INVALID",
        maximum=1024,
    )
    end_flat = _boolean(
        state["end_flat"], "FINAL_STATE_END_FLAT_INVALID"
    )
    if end_flat != (not order_ids and not positions and gross == 0):
        raise ReceiptContractError("FINAL_STATE_END_FLAT_MISMATCH")
    return state


def _validate_cycle_payload(
    payload_value: Any, expected_bindings: dict[str, Any]
) -> dict[str, Any]:
    payload = _exact_object(
        payload_value,
        CYCLE_PAYLOAD_FIELDS,
        "CYCLE_PAYLOAD_FIELDS_INVALID",
    )
    _bindings, calls = _validate_common_payload(
        payload, expected_bindings, require_intent=True
    )
    if payload["execution_mode"] != "PAPER":
        raise ReceiptContractError("CYCLE_EXECUTION_MODE_INVALID")
    _digest(payload["preflight_sha256"], "CYCLE_PREFLIGHT_INVALID")
    preview = _optional_digest(
        payload["preview_receipt_sha256"], "CYCLE_PREVIEW_INVALID"
    )
    order_id = _optional_digest(
        payload["broker_order_id_sha256"], "CYCLE_ORDER_ID_INVALID"
    )
    _digest(payload["journal_sha256"], "CYCLE_JOURNAL_INVALID")
    _digest(
        payload["event_summary_sha256"], "CYCLE_EVENT_SUMMARY_INVALID"
    )
    claimed_mutation = _boolean(
        payload["mutation_attempted"], "CYCLE_MUTATION_FLAG_INVALID"
    )
    observed_mutation = _validate_tool_evidence(
        payload["tool_evidence"], calls, read_only=False
    )
    if claimed_mutation != observed_mutation:
        raise ReceiptContractError("CYCLE_MUTATION_FLAG_MISMATCH")
    if observed_mutation and preview is None:
        raise ReceiptContractError("CYCLE_MUTATION_WITHOUT_PREVIEW")
    evidence_calls = payload["tool_evidence"]
    open_calls = _phase_calls(
        evidence_calls, "OPEN", required=False)
    preview_calls = _phase_calls(
        evidence_calls, "PREVIEW", required=False, maximum=2)
    order_preview_calls = _named_phase_calls(
        preview_calls, "risk.preview_order")
    flatten_preview_calls = _named_phase_calls(
        preview_calls, "risk.preview_flatten")
    place_calls = _phase_calls(
        evidence_calls, "PLACE", required=False)
    close_calls = _phase_calls(
        evidence_calls, "CLOSE", required=False)
    reconcile_calls = _phase_calls(
        evidence_calls, "RECONCILE", required=False, maximum=None)
    cleanup_calls = _phase_calls(
        evidence_calls, "CLEANUP", required=False, maximum=None)
    cancel_calls = _phase_calls(
        evidence_calls, "CANCEL", required=False)
    flatten_calls = _phase_calls(
        evidence_calls, "FLATTEN", required=False)
    halt_calls = _phase_calls(
        evidence_calls, "HALT", required=False)
    if (
        (preview is None and order_preview_calls)
        or (preview is not None and len(order_preview_calls) != 1)
    ):
        raise ReceiptContractError("CYCLE_PHASE_EVIDENCE_MISMATCH")
    if preview is not None and (
        order_preview_calls[0][1]["response_sha256"] != preview
    ):
        raise ReceiptContractError("CYCLE_PREVIEW_DIGEST_MISMATCH")
    if place_calls:
        if not open_calls or not order_preview_calls or not close_calls:
            raise ReceiptContractError(
                "CYCLE_PLACE_LIFECYCLE_INCOMPLETE")
        critical = (
            open_calls[0][0],
            order_preview_calls[0][0],
            place_calls[0][0],
            close_calls[0][0],
        )
        if critical != tuple(sorted(critical)):
            raise ReceiptContractError("CYCLE_ATOMIC_PHASE_ORDER_INVALID")
        if (
            place_calls[0][0] != order_preview_calls[0][0] + 1
            or close_calls[0][0] != place_calls[0][0] + 1
        ):
            raise ReceiptContractError(
                "CYCLE_ATOMIC_PHASE_ADJACENCY_INVALID")
        if (
            open_calls[0][1]["status"] != "OK"
            or order_preview_calls[0][1]["status"] != "OK"
        ):
            raise ReceiptContractError(
                "CYCLE_PLACE_PREREQUISITE_INVALID")
    elif cancel_calls or flatten_calls:
        raise ReceiptContractError("CYCLE_RISK_REDUCTION_WITHOUT_PLACE")
    if order_preview_calls:
        if (
            not open_calls
            or open_calls[0][0] + 1 != order_preview_calls[0][0]
            or open_calls[0][1]["status"] != "OK"
            or not close_calls
            or close_calls[0][0] <= order_preview_calls[0][0]
        ):
            raise ReceiptContractError(
                "CYCLE_PREVIEW_WINDOW_INVALID")
    if open_calls and open_calls[0][1]["status"] == "OK":
        if (
            not close_calls
            or close_calls[0][0] <= open_calls[0][0]
        ):
            raise ReceiptContractError("CYCLE_OPEN_WINDOW_NOT_CLOSED")
        if (
            not order_preview_calls
            and close_calls[0][0] != open_calls[0][0] + 1
        ):
            raise ReceiptContractError(
                "CYCLE_CLOSE_NOT_IMMEDIATE")
    if (
        order_preview_calls
        and not place_calls
        and close_calls[0][0] != order_preview_calls[0][0] + 1
    ):
        raise ReceiptContractError("CYCLE_CLOSE_NOT_IMMEDIATE")
    if close_calls and (
        not open_calls
        or open_calls[0][1]["status"] != "OK"
        or close_calls[0][0] <= open_calls[0][0]
    ):
        raise ReceiptContractError("CYCLE_CLOSE_WITHOUT_OPEN")
    if cancel_calls and (
        not place_calls
        or not close_calls
        or not (
            place_calls[0][0]
            < close_calls[0][0]
            < cancel_calls[0][0]
        )
    ):
        raise ReceiptContractError("CYCLE_CANCEL_WINDOW_INVALID")
    if flatten_calls and (
        not place_calls
        or not flatten_preview_calls
        or not close_calls
        or not (
            place_calls[0][0]
            < close_calls[0][0]
            < flatten_preview_calls[0][0]
            < flatten_calls[0][0]
        )
        or flatten_preview_calls[0][1]["status"] != "OK"
    ):
        raise ReceiptContractError("CYCLE_FLATTEN_WINDOW_INVALID")
    if (
        flatten_preview_calls
        and not flatten_calls
        and flatten_preview_calls[0][1]["status"] == "OK"
    ):
        raise ReceiptContractError("CYCLE_UNUSED_FLATTEN_PREVIEW")
    if order_id is not None and (
        not place_calls
        or place_calls[0][1]["status"]
        not in {"OK", "DUPLICATE", "UNCERTAIN"}
    ):
        raise ReceiptContractError("CYCLE_ORDER_WITHOUT_PLACE_PHASE")
    state = _validate_final_state(payload["final_authoritative_state"])
    cleanup_complete = _boolean(
        payload["cleanup_complete"], "CYCLE_CLEANUP_FLAG_INVALID"
    )
    reasons = _reason_codes(payload["reason_codes"])
    _require_reported_tool_reasons(evidence_calls, reasons)
    outcome = _enum(
        payload["final_outcome"], CYCLE_OUTCOMES,
        "CYCLE_OUTCOME_INVALID")
    if outcome == "NO_TRADE":
        if observed_mutation or order_id is not None or not reasons:
            raise ReceiptContractError("CYCLE_NO_TRADE_INVARIANT_INVALID")
        if order_preview_calls and (
            order_preview_calls[0][1]["status"] not in {"OK", "REJECTED"}
        ):
            raise ReceiptContractError(
                "CYCLE_NO_TRADE_PREVIEW_STATUS_INVALID")
    elif outcome == "CANCELLED_FLAT":
        if (
            not observed_mutation
            or order_id is None
            or place_calls[0][1]["status"] != "OK"
            or len(cancel_calls) != 1
            or cancel_calls[0][1]["status"] != "OK"
            or flatten_calls
            or flatten_preview_calls
        ):
            raise ReceiptContractError(
                "CYCLE_CANCELLED_EVIDENCE_INVALID")
    elif outcome == "FILLED_AND_FLAT":
        if (
            not observed_mutation
            or order_id is None
            or place_calls[0][1]["status"] != "OK"
            or len(flatten_preview_calls) != 1
            or len(flatten_calls) != 1
            or flatten_preview_calls[0][1]["status"] != "OK"
            or flatten_calls[0][1]["status"] != "OK"
            or cancel_calls
        ):
            raise ReceiptContractError(
                "CYCLE_FILLED_EVIDENCE_INVALID")
    elif outcome == "RECOVERED":
        if (
            not reasons
            or any(call["status"] != "OK" for _, call in cancel_calls)
            or any(call["status"] != "OK" for _, call in flatten_calls)
        ):
            raise ReceiptContractError(
                "CYCLE_RECOVERED_EVIDENCE_INVALID")
    elif not reasons:
        raise ReceiptContractError("RECOVERY_REQUIRED_REASON_MISSING")
    if order_id is not None and not observed_mutation:
        raise ReceiptContractError("CYCLE_ORDER_WITHOUT_MUTATION")
    if outcome in SUCCESSFUL_CYCLE_OUTCOMES:
        if not (
            cleanup_complete
            and state["authoritative"]
            and state["account_complete"]
            and state["end_flat"]
            and state["authorized_connector_count"] == 0
        ):
            raise ReceiptContractError("CYCLE_SUCCESS_NOT_CLOSED")
        if (
            outcome != "RECOVERED"
            and close_calls
            and close_calls[0][1]["status"] != "OK"
        ):
            raise ReceiptContractError(
                "CYCLE_SUCCESSFUL_CLOSE_STATUS_INVALID")
        if outcome == "NO_TRADE" and open_calls and (
            open_calls[0][1]["status"] not in {"OK", "REJECTED"}
        ):
            raise ReceiptContractError(
                "CYCLE_SUCCESSFUL_OPEN_STATUS_INVALID")
        if outcome not in {"NO_TRADE", "RECOVERED"} and (
            open_calls[0][1]["status"] != "OK"
            or order_preview_calls[0][1]["status"] != "OK"
            or place_calls[0][1]["status"] != "OK"
        ):
            raise ReceiptContractError(
                "CYCLE_SUCCESSFUL_CRITICAL_STATUS_INVALID")
        last_effectful_index = max(
            (
                index
                for index, call in enumerate(evidence_calls)
                if call["effect"] in {"CONTROL", "MUTATION"}
            ),
            default=-1,
        )
        final_read_names = {
            call["tool_name"]
            for index, call in reconcile_calls + cleanup_calls
            if index > last_effectful_index
        }
        if (
            not reconcile_calls
            or not cleanup_calls
            or min(index for index, _call in reconcile_calls)
            <= last_effectful_index
            or min(index for index, _call in cleanup_calls)
            <= max(index for index, _call in reconcile_calls)
            or any(call["status"] != "OK" for _, call in reconcile_calls)
            or any(call["status"] != "OK" for _, call in cleanup_calls)
            or (halt_calls and outcome != "RECOVERED")
        ):
            raise ReceiptContractError(
                "CYCLE_SUCCESS_LIFECYCLE_EVIDENCE_INVALID")
        if not FINAL_AUTHORITATIVE_READ_TOOLS.issubset(
            final_read_names
        ):
            raise ReceiptContractError(
                "CYCLE_FINAL_RECONCILIATION_INCOMPLETE")
    elif cleanup_complete:
        raise ReceiptContractError("RECOVERY_REQUIRED_CLEANUP_MISMATCH")
    return payload


def _compare_evidence_bindings(
    schema: str,
    payload: dict[str, Any],
    payload_sha256: str,
    expected_evidence: dict[str, Any],
) -> None:
    """Bind every receipt artifact to the independent post-run anchor."""

    evidence_bindings = expected_evidence["evidence_bindings"]
    if evidence_bindings["receipt_schema"] != schema:
        raise ReceiptContractError(
            "EXPECTED_EVIDENCE_RECEIPT_SCHEMA_MISMATCH")
    evidence = evidence_bindings["evidence"]
    if schema == DECISION_SCHEMA:
        comparisons = (
            (
                "information_packet_sha256",
                payload["information_packet_sha256"],
                "EXPECTED_EVIDENCE_INFORMATION_PACKET_MISMATCH",
            ),
            (
                "preflight_sha256",
                payload["preflight_sha256"],
                "EXPECTED_EVIDENCE_PREFLIGHT_MISMATCH",
            ),
            (
                "tool_evidence_sha256",
                canonical_sha256(payload["tool_evidence"]),
                "EXPECTED_EVIDENCE_TOOL_EVIDENCE_MISMATCH",
            ),
        )
    else:
        state = payload["final_authoritative_state"]
        comparisons = (
            (
                "preflight_sha256",
                payload["preflight_sha256"],
                "EXPECTED_EVIDENCE_PREFLIGHT_MISMATCH",
            ),
            (
                "preview_receipt_sha256",
                payload["preview_receipt_sha256"],
                "EXPECTED_EVIDENCE_PREVIEW_MISMATCH",
            ),
            (
                "broker_order_id_sha256",
                payload["broker_order_id_sha256"],
                "EXPECTED_EVIDENCE_ORDER_ID_MISMATCH",
            ),
            (
                "journal_sha256",
                payload["journal_sha256"],
                "EXPECTED_EVIDENCE_JOURNAL_MISMATCH",
            ),
            (
                "event_summary_sha256",
                payload["event_summary_sha256"],
                "EXPECTED_EVIDENCE_EVENT_SUMMARY_MISMATCH",
            ),
            (
                "tool_evidence_sha256",
                canonical_sha256(payload["tool_evidence"]),
                "EXPECTED_EVIDENCE_TOOL_EVIDENCE_MISMATCH",
            ),
            (
                "final_snapshot_sha256",
                state["snapshot_sha256"],
                "EXPECTED_EVIDENCE_FINAL_SNAPSHOT_MISMATCH",
            ),
            (
                "final_service_epoch",
                state["service_epoch"],
                "EXPECTED_EVIDENCE_FINAL_EPOCH_MISMATCH",
            ),
            (
                "final_fencing_generation",
                state["fencing_generation"],
                "EXPECTED_EVIDENCE_FINAL_FENCING_MISMATCH",
            ),
            (
                "final_authoritative_state_sha256",
                canonical_sha256(state),
                "EXPECTED_EVIDENCE_FINAL_STATE_MISMATCH",
            ),
        )
    for field, actual, code in comparisons:
        if evidence[field] != actual:
            raise ReceiptContractError(code)
    if evidence_bindings["payload_sha256"] != payload_sha256:
        raise ReceiptContractError(
            "EXPECTED_EVIDENCE_PAYLOAD_DIGEST_MISMATCH")


def validate_receipt_document(
    document: Any,
    expected_bindings_document: Any,
    expected_evidence_bindings_document: Any,
) -> dict[str, Any]:
    expected = validate_bindings_document(expected_bindings_document)
    expected_evidence = validate_evidence_bindings_document(
        expected_evidence_bindings_document,
        expected_bindings_document,
    )
    root = _exact_object(
        document, RECEIPT_FIELDS, "RECEIPT_FIELDS_INVALID"
    )
    if root["version"] != VERSION:
        raise ReceiptContractError("RECEIPT_VERSION_INVALID")
    schema = root["schema"]
    if schema == DECISION_SCHEMA:
        payload = _validate_decision_payload(
            root["payload"], expected["bindings"]
        )
    elif schema == CYCLE_SCHEMA:
        payload = _validate_cycle_payload(
            root["payload"], expected["bindings"]
        )
    else:
        raise ReceiptContractError("RECEIPT_SCHEMA_INVALID")
    claimed = _digest(
        root["payload_sha256"], "RECEIPT_PAYLOAD_DIGEST_INVALID"
    )
    if claimed != canonical_sha256(payload):
        raise ReceiptContractError("RECEIPT_PAYLOAD_DIGEST_MISMATCH")
    _compare_evidence_bindings(
        schema, payload, claimed, expected_evidence)
    return {
        "schema": schema,
        "payload_sha256": claimed,
        "bindings_sha256": expected["bindings_sha256"],
        "evidence_bindings_sha256":
            expected_evidence["evidence_bindings_sha256"],
    }


def load_and_validate(
    receipt_raw: bytes,
    expected_bindings_raw: bytes,
    expected_evidence_bindings_raw: bytes,
) -> dict[str, Any]:
    receipt = _load_canonical(receipt_raw, "RECEIPT")
    expected = _load_canonical(expected_bindings_raw, "EXPECTED_BINDINGS")
    expected_evidence = _load_canonical(
        expected_evidence_bindings_raw,
        "EXPECTED_EVIDENCE_BINDINGS",
    )
    return validate_receipt_document(
        receipt, expected, expected_evidence)


def make_bindings_document(bindings: dict[str, Any]) -> dict[str, Any]:
    """Build an unsigned canonicalizable binding anchor for tests/producers."""

    validated = _validate_bindings(bindings)
    return {
        "schema": BINDINGS_SCHEMA,
        "version": VERSION,
        "bindings": validated,
        "bindings_sha256": canonical_sha256(validated),
    }


def make_receipt(schema: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build a digest envelope; validation still requires an external anchor."""

    _enum(
        schema,
        {DECISION_SCHEMA, CYCLE_SCHEMA},
        "RECEIPT_SCHEMA_INVALID",
    )
    return {
        "schema": schema,
        "version": VERSION,
        "payload": payload,
        "payload_sha256": canonical_sha256(payload),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a canonical Hepta PAPER receipt against an independently "
            "pinned pre-run bindings document and a separately produced "
            "post-run evidence-bindings document. This does not grant "
            "authority."
        )
    )
    parser.add_argument("receipt", type=Path)
    parser.add_argument(
        "--expected-bindings",
        type=Path,
        required=True,
        help="independently pinned canonical pre-run bindings",
    )
    parser.add_argument(
        "--expected-evidence-bindings",
        type=Path,
        required=True,
        help=(
            "independently produced canonical post-run artifact and "
            "authoritative-state bindings"
        ),
    )
    arguments = parser.parse_args()
    try:
        result = load_and_validate(
            _stable_read(arguments.receipt, "RECEIPT"),
            _stable_read(arguments.expected_bindings, "EXPECTED_BINDINGS"),
            _stable_read(
                arguments.expected_evidence_bindings,
                "EXPECTED_EVIDENCE_BINDINGS",
            ),
        )
    except (OSError, ReceiptContractError) as error:
        code = (
            error.code
            if isinstance(error, ReceiptContractError)
            else "RECEIPT_IO_ERROR"
        )
        print(
            f"hepta_paper_receipt_contracts: FAIL {code}",
            file=sys.stderr,
        )
        return 2
    print(
        canonical_json(
            {
                "authority_granted": False,
                "bindings_sha256": result["bindings_sha256"],
                "evidence_bindings_sha256":
                    result["evidence_bindings_sha256"],
                "payload_sha256": result["payload_sha256"],
                "receipt_schema": result["schema"],
                "status": "valid",
            }
        ).decode("ascii"),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
