#!/usr/bin/env -S /usr/bin/python3.12 -I -S

"""Fixed launch join for one external-P1 EUR.USD PAPER canary.

The public root entry accepts only a campaign and cycle identifier.  Peer
capture and strategy evaluation are credential-fed and READ_ONLY.  The root
joiner independently reopens the official decision, runs the installed
decision validator, and emits a distinct decimal-normalization receipt.  A
NO_TRADE decision is terminal and can never produce an execution handoff.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import time
from typing import Any, Callable, Mapping, Optional


VERSION = 1
DOMAIN = "alpha"
PEER_UID = 2104
PEER_GID = 2104
MAX_BYTES = 1024 * 1024
MAX_QUOTE_AGE_MS = 5_000
MAX_INTENT_HORIZON_MS = 60_000
MAX_NOTIONAL = Decimal("5000")
NORMALIZATION_VERSION = "hepta.p1-paper-canary-decimal-normalization.v1"
NORMALIZATION_SCHEMA = (
    "hepta.p1-paper-canary-normalized-launch-intent-receipt.v1")
NO_TRADE_SCHEMA = "hepta.p1-paper-canary-no-trade-launch-receipt.v1"
CAPTURE_SCHEMA = "hepta.p1-paper-canary-read-only-capture.v1"
CAPTURE_REQUEST_SCHEMA = "hepta.p1-paper-canary-capture-request.v1"
TOOL_CATALOG_SCHEMA = "hepta.p1-paper-canary-tool-catalog.v1"
HANDOFF_SCHEMA = "hepta.p1-paper-canary-execution-handoff.v1"
BACKEND_TRANSFORM_VERSION = "hepta.p1-paper-canary-backend-transform.v1"
ROOT_CLEANUP_REQUEST_SCHEMA = (
    "hepta.p1-paper-canary-root-cleanup-request.v1")
ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-request.v1")
ROOT_CLEANUP_RECEIPT_SCHEMA = (
    "hepta.p1-paper-canary-root-cleanup-receipt.v4")
ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-receipt.v1")
ROOT_FINALIZER_SOCKET = "/run/hepta-p1-paper-canary-finalizer.sock"
ROOT_CLEANUP_OPERATION = "FINALIZE_EXTERNAL_P1"
ROOT_CLEANUP_TIMEOUT_MS = 240_000

POLICY_PATH = Path("/etc/heptatrader/paper-campaigns/alpha.json")
RUNTIME_PROFILE_PATH = Path(
    "/etc/heptatrader/trust-domains/alpha.ib-paper.env")
CONTROL_ROOT = Path("/var/lib/hepta/p1-paper-canary-control")
ARTIFACT_ROOT = Path("/var/lib/hepta/p1-paper-canary")
ACTIVE_CAPTURE_REQUEST = Path(
    "/run/hepta-p1-paper-canary/active-capture-request.v1.json")
LAUNCH_INPUT_POINTER = Path(
    "/var/lib/hepta/p1-admission/paper-canary-launch-input-current.v1.json")
CAPTURE_CREDENTIAL = Path(
    "/run/credentials/hepta-p1-paper-canary-capture/"
    "capture-request.v1.json")
CAPTURE_TOKEN_SOURCE = Path(
    "/run/hepta-p1-paper-canary/read-only-capture-session.token")
CAPTURE_TOKEN_CREDENTIAL = Path(
    "/run/credentials/hepta-p1-paper-canary-capture/session.token")
SESSIONCTL = Path("/usr/bin/hepta-sessionctl")
SESSION_SUPERVISOR_SOCKET = Path(
    "/run/hepta-tool-gateway-alpha/session-supervisor.sock")
HEPTACTL = Path("/usr/bin/heptactl")
TOOL_SOCKET = Path("/run/hepta-agent-alpha/tools.sock")
DECISION_VALIDATOR = Path(
    "/usr/libexec/validate_hepta_strategy_decision_receipt.py")
STRATEGY_RUNNER = Path("/usr/libexec/hepta_strategy_shadow_runner.py")
STRATEGY_PATH = Path(
    "/usr/share/heptatrader/strategies/"
    "eurusd-confirmed-momentum-shadow-v2.json")
OWNER_PROVISIONER = Path(
    "/usr/libexec/hepta-p1-paper-canary-owner-provisioner")
OWNER_PROVISIONER_CREDENTIAL = "hepta-p1-paper-canary-owner-provisioner.py"
INSTALLED_SELF = Path("/usr/libexec/hepta-p1-paper-canary-launch-joiner")
OWNER_TOKEN_PATH = Path("/run/hepta-agent-alpha/sessions/session.token")
OWNER_ROOT = Path("/var/lib/hepta-local-ai-paper-agent/session-authority")
OWNER_AUTHORITY_PATH = OWNER_ROOT / "session.token.authority.json"
OWNER_REVOKE_PATH = OWNER_ROOT / "session.token.revoke-token"
OWNER_INTENT_PATH = OWNER_ROOT / "session.token.owner-may-exist.v1.json"

PROFILE_KEYS = (
    "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY",
    "HEPTA_EXECUTION_MAX_ORDER_NOTIONAL", "HEPTA_IB_EXECUTION_MODE",
    "HEPTA_IB_PAPER_ACCOUNT", "HEPTA_IB_PAPER_HOST",
    "HEPTA_IB_PAPER_PORT", "HEPTA_IB_PAPER_CLIENT_ID",
    "HEPTA_IB_PAPER_MAX_ORDER_QTY", "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL",
    "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE",
    "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS",
    "HEPTA_IB_PAPER_MAX_GROSS_POSITION", "HEPTA_IB_PAPER_QUOTE_CONTRACTS",
    "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT",
    "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS", "HEPTA_IB_EXECUTION_GATEWAY_UID",
    "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID", "HEPTA_IB_EXECUTION_DOMAIN_ID",
    "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES",
    "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS",
    "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS",
    "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS",
)
PAPER_ACCOUNT = re.compile(r"DU[0-9]{1,16}")

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
DECIMAL_TOKEN = re.compile(r"(?:0|[1-9][0-9]*)(?:\.([0-9]{1,8}))?")
REASON = re.compile(r"[A-Z][A-Z0-9_]{0,95}")

ROLE_PLAN = (
    ("preflight-health", "system.get_health", "READ_ONLY", "PREFLIGHT"),
    ("preflight-quote", "market.get_quote", "READ_ONLY", "PREFLIGHT"),
    ("preflight-account", "account.get_summary", "READ_ONLY", "PREFLIGHT"),
    ("preflight-positions", "portfolio.list_positions", "READ_ONLY", "PREFLIGHT"),
    ("preflight-orders", "orders.list", "READ_ONLY", "PREFLIGHT"),
    ("preflight-risk", "risk.get_limits", "READ_ONLY", "PREFLIGHT"),
    ("preflight-campaign", "campaign.status", "READ_ONLY", "PREFLIGHT"),
    ("open", "campaign.open_cycle", "CONTROL", "OPEN"),
    ("preview-order", "risk.preview_order", "READ_ONLY", "PREVIEW"),
    ("place", "trade.place_order", "MUTATION", "PLACE"),
    ("close", "campaign.close_cycle", "CONTROL", "CLOSE"),
    ("reconcile-orders", "orders.list", "READ_ONLY", "SNAPSHOT"),
    ("reconcile-positions", "portfolio.list_positions", "READ_ONLY", "SNAPSHOT"),
    ("cancel-order", "trade.cancel_order", "MUTATION", "CANCEL"),
    ("preview-flatten", "risk.preview_flatten", "READ_ONLY", "PREVIEW"),
    ("flatten-position", "trade.flatten_position", "MUTATION", "FLATTEN"),
    ("final-health", "system.get_health", "READ_ONLY", "RECONCILE"),
    # Orders are proven empty before the final position/account/risk reads.
    # With the campaign mutation window already closed, this ordering closes
    # the late-fill race: an order visible here fails; a fill before this read
    # is necessarily visible in the later position/risk boundary.
    ("final-orders", "orders.list", "READ_ONLY", "RECONCILE"),
    ("final-account", "account.get_summary", "READ_ONLY", "RECONCILE"),
    ("final-positions", "portfolio.list_positions", "READ_ONLY", "RECONCILE"),
    ("cleanup-risk", "risk.get_limits", "READ_ONLY", "CLEANUP"),
)
FRESH_READS = (
    ("system.get_health", ()),
    ("market.get_quote", ("instrument=EUR.USD",)),
    ("account.get_summary", ()),
    ("portfolio.list_positions", ()),
    ("orders.list", ()),
    ("risk.get_limits", ()),
)
IMAGE_PATHS = (
    ("executor", "/usr/libexec/hepta-p1-paper-canary-executor"),
    ("receipt-validator-v3", "/usr/libexec/hepta-paper-receipt-contracts"),
    ("receipt-validator-v2", "/usr/libexec/hepta-paper-receipt-contracts-v2-compat"),
    ("backend-adapter", "/usr/libexec/hepta-p1-paper-canary-backend-adapter"),
    ("handoff-producer", "/usr/libexec/hepta-p1-paper-canary-handoff-producer"),
    ("native-tool-client", "/usr/bin/heptactl"),
    ("campaign-operator", "/usr/libexec/hepta-ib-paper-campaign-operator"),
    ("root-finalizer", "/usr/libexec/hepta-p1-paper-canary-finalizer"),
    ("launch-joiner", "/usr/libexec/hepta-p1-paper-canary-launch-joiner"),
    ("owner-provisioner",
     "/usr/libexec/hepta-p1-paper-canary-owner-provisioner"),
    ("root-coordinator",
     "/usr/libexec/hepta-p1-paper-canary-root-coordinator"),
    ("crash-emergency-closer",
     "/usr/libexec/hepta-p1-paper-canary-crash-emergency-closer"),
    ("terminal-prover",
     "/usr/libexec/hepta-p1-paper-canary-terminal-prover"),
)

INTENT_FIELDS = frozenset({
    "schema", "paper_only", "strategy_id", "strategy_version",
    "strategy_sha256", "intent_id", "instrument", "symbol", "currency",
    "sec_type", "exchange", "side", "quantity", "order_type",
    "limit_price", "tif", "observed_bid", "observed_ask",
    "observed_at_ms", "expires_at_ms", "entry_thesis",
    "invalidation_condition", "max_holding_ms", "max_adverse_move",
    "expected_slippage", "exit_plan",
})
DECISION_FIELDS = frozenset({
    "schema", "campaign_id", "strategy_id", "strategy_version",
    "strategy_sha256", "decision_id", "cycle_id", "started_at_ms",
    "finished_at_ms", "paper_only", "live_authorized", "shadow_only",
    "information_packet_sha256", "catalog_sha256", "descriptor_sha256",
    "preflight_sha256", "regime", "setup_gates", "risk_challenges",
    "evidence_refs", "conflicts", "decision", "reason_codes",
    "trade_intent", "trade_intent_sha256", "campaign_open_request_id",
    "campaign_close_request_id", "mutation_attempted", "direct_broker_access",
    "final_outcome",
})
NORMALIZATION_FIELDS = frozenset({
    "schema", "version", "status", "created_at_ms", "campaign_id",
    "domain_id", "cycle_id", "original_decision_path",
    "original_decision_file_sha256", "original_decision_id",
    "original_decision_cycle_id", "original_trade_intent_sha256",
    "official_validator_path", "official_validator_file_sha256",
    "strategy_path", "strategy_file_sha256", "information_packet_path",
    "information_packet_file_sha256", "tool_catalog_path",
    "tool_catalog_file_sha256", "tool_catalog_body_sha256",
    "capture_path", "capture_file_sha256", "capture_body_sha256",
    "normalization_version", "normalized_intent", "normalized_intent_sha256",
    "quote_age_ms", "max_quote_age_ms", "max_intent_horizon_ms",
    "max_notional", "paper_only", "live_authorized",
    "direct_broker_access", "authority_granted", "body_sha256",
})
NO_TRADE_FIELDS = frozenset({
    "schema", "version", "status", "created_at_ms", "campaign_id",
    "domain_id", "requested_cycle_id", "original_decision_path",
    "original_decision_file_sha256", "official_validator_path",
    "official_validator_file_sha256", "strategy_path", "strategy_file_sha256",
    "information_packet_path", "information_packet_file_sha256",
    "capture_path", "capture_file_sha256", "capture_body_sha256",
    "decision_id", "reason_codes", "handoff_created", "authority_started",
    "paper_only", "live_authorized", "direct_broker_access",
    "authority_granted", "body_sha256",
})
PINNED_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "size", "mode", "uid", "gid",
    "nlink",
})
PLAIN_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "size", "mode", "uid", "gid", "nlink",
})
LAUNCH_INPUT_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "policy_reference",
    "source_baseline_sha256", "p1_audit_reference",
    "watch_handoff_reference", "zero_exposure_reference",
    "admission_finalization_reference", "capture_token_path",
    "runtime_profile_reference", "installed_images",
    "execution_service_epoch", "execution_service_fencing_generation",
    "strategy_policy_reference", "strategy_reference",
    "strategy_snapshot_reference", "quote_history_reference",
    "bar_history_reference", "calendar_reference", "information_reference",
    "information_packet_reference", "evaluated_at_ms", "paper_only",
    "live_authorized", "direct_broker_access", "authority_granted",
    "body_sha256",
})


class JoinError(RuntimeError):
    pass


class _NumberToken(str):
    pass


@dataclass(frozen=True)
class Reference:
    path: str
    raw: bytes
    document: dict[str, Any]
    file_sha256: str
    body_sha256: Optional[str]


@dataclass(frozen=True)
class JoinResult:
    status: str
    normalization_raw: Optional[bytes]
    no_trade_raw: Optional[bytes]
    handoff_raw: Optional[bytes]


def canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise JoinError("JOIN_NON_CANONICAL_VALUE") from error


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical_sha(value: Any) -> str:
    return sha(canonical_json(value))


def _identifier(value: Any, reason: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise JoinError(reason)
    return value


def _digest(value: Any, reason: str) -> str:
    if (
            not isinstance(value, str) or DIGEST.fullmatch(value) is None or
            value == "sha256:" + "0" * 64):
        raise JoinError(reason)
    return value


def _exact(value: Any, fields: frozenset[str], reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise JoinError(reason)
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite")


def strict_json(raw: bytes, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not raw or len(raw) > MAX_BYTES or not raw.endswith(b"\n"):
        raise JoinError(f"{label}_FRAME_INVALID")
    try:
        standard = json.loads(
            raw.decode("ascii"), object_pairs_hook=_pairs,
            parse_constant=_reject_constant)
        lexical = json.loads(
            raw.decode("ascii"), object_pairs_hook=_pairs,
            parse_float=_NumberToken, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise JoinError(f"{label}_JSON_INVALID") from error
    if (
            not isinstance(standard, dict) or not isinstance(lexical, dict) or
            canonical_json(standard) != raw):
        raise JoinError(f"{label}_NON_CANONICAL")
    return standard, lexical


def sealed(document: dict[str, Any], reason: str) -> str:
    claimed = _digest(document.get("body_sha256"), reason)
    body = dict(document)
    del body["body_sha256"]
    if canonical_sha(body) != claimed:
        raise JoinError(reason)
    return claimed


def sealed_body(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "body_sha256": canonical_sha(body)}


def stable_read(
        path: Path, *, uid: int, gid: int, modes: frozenset[int],
        maximum: int = MAX_BYTES) -> bytes:
    try:
        before = os.lstat(path)
        if (
                stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != uid or before.st_gid != gid or
                stat.S_IMODE(before.st_mode) not in modes or
                before.st_size < 1 or before.st_size > maximum):
            raise JoinError("JOIN_ARTIFACT_METADATA_INVALID")
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            raw = bytearray()
            while len(raw) <= maximum:
                chunk = os.read(
                    descriptor, min(65536, maximum + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise JoinError("JOIN_ARTIFACT_UNAVAILABLE") from error
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink, item.st_uid,
        item.st_gid, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    if (
            len(raw) > maximum or identity(before) != identity(opened) or
            identity(opened) != identity(after)):
        raise JoinError("JOIN_ARTIFACT_CHANGED")
    return bytes(raw)


def reference(
        path: Path, *, uid: int, gid: int, modes: frozenset[int],
        sealed_required: bool = True) -> Reference:
    raw = stable_read(path, uid=uid, gid=gid, modes=modes)
    document, _lexical = strict_json(raw, "JOIN_REFERENCE")
    body = sealed(document, "JOIN_REFERENCE_BODY_INVALID") \
        if sealed_required else None
    return Reference(str(path), raw, document, sha(raw), body)


def _decimal(value: Any, reason: str, *, positive: bool) -> str:
    if isinstance(value, bool) or not isinstance(value, (_NumberToken, int)):
        raise JoinError(reason)
    token = str(value)
    match = DECIMAL_TOKEN.fullmatch(token)
    if match is None:
        raise JoinError(reason)
    fraction = match.group(1)
    normalized = token
    if fraction is not None:
        normalized = token.rstrip("0").rstrip(".")
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as error:
        raise JoinError(reason) from error
    if parsed < 0 or (positive and parsed == 0):
        raise JoinError(reason)
    return normalized


def _validate_official_decision(
        decision: dict[str, Any], decision_lexical: dict[str, Any],
        *, campaign_id: str) -> None:
    _exact(decision, DECISION_FIELDS, "JOIN_DECISION_FIELDS_INVALID")
    _exact(decision_lexical, DECISION_FIELDS, "JOIN_DECISION_FIELDS_INVALID")
    if (
            decision["schema"] !=
                "hepta.autonomous-paper-decision-receipt.v1" or
            decision["campaign_id"] != campaign_id or
            decision["paper_only"] is not True or
            decision["live_authorized"] is not False or
            decision["shadow_only"] is not True or
            decision["mutation_attempted"] is not False or
            decision["direct_broker_access"] is not False or
            decision["decision"] not in {"TRADE", "NO_TRADE"}):
        raise JoinError("JOIN_DECISION_BOUNDARY_INVALID")
    if decision["decision"] == "NO_TRADE":
        if (
                decision["cycle_id"] is not None or
                decision["trade_intent"] is not None or
                decision["trade_intent_sha256"] is not None or
                decision["final_outcome"] != "NO_TRADE"):
            raise JoinError("JOIN_NO_TRADE_DECISION_INVALID")
        reasons = decision["reason_codes"]
        if (
                not isinstance(reasons, list) or not reasons or
                any(not isinstance(item, str) or REASON.fullmatch(item) is None
                    for item in reasons)):
            raise JoinError("JOIN_NO_TRADE_REASONS_INVALID")
        return
    if (
            not isinstance(decision["trade_intent"], dict) or
            not isinstance(decision_lexical["trade_intent"], dict) or
            decision["cycle_id"] is None or
            decision["final_outcome"] != "SHADOW_TRADE" or
            decision["trade_intent_sha256"] != canonical_sha(
                decision["trade_intent"])):
        raise JoinError("JOIN_TRADE_DECISION_INVALID")


def run_official_validator(
        decision_path: Path, *, runner: Callable[..., subprocess.CompletedProcess[bytes]]
        = subprocess.run) -> tuple[str, str]:
    validator_raw = stable_read(
        DECISION_VALIDATOR, uid=0, gid=0, modes=frozenset({0o755}))
    try:
        completed = runner(
            ["/usr/bin/python3.12", "-I", "-S", str(DECISION_VALIDATOR),
             str(decision_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd="/", env={"LC_ALL": "C"},
            close_fds=True, check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise JoinError("JOIN_OFFICIAL_VALIDATOR_UNAVAILABLE") from error
    if (
            completed.returncode != 0 or len(completed.stdout) > 65536 or
            len(completed.stderr) > 65536):
        raise JoinError("JOIN_OFFICIAL_VALIDATOR_REJECTED")
    return str(DECISION_VALIDATOR), sha(validator_raw)


def normalize_trade_decision(
        *, decision_raw: bytes, decision_path: str, campaign_id: str,
        requested_cycle_id: str, now_ms: int, validator_path: str,
        validator_file_sha256: str, strategy_path: str,
        strategy_file_sha256: str, information_packet_path: str,
        information_packet_file_sha256: str, catalog: Reference,
        capture: Reference) -> tuple[dict[str, Any], bytes]:
    decision, lexical = strict_json(decision_raw, "JOIN_DECISION")
    _validate_official_decision(decision, lexical, campaign_id=campaign_id)
    if decision["decision"] != "TRADE":
        raise JoinError("JOIN_TRADE_REQUIRED")
    original = _exact(
        decision["trade_intent"], INTENT_FIELDS,
        "JOIN_ORIGINAL_INTENT_FIELDS_INVALID")
    tokens = _exact(
        lexical["trade_intent"], INTENT_FIELDS,
        "JOIN_ORIGINAL_INTENT_FIELDS_INVALID")
    if (
            original["schema"] != "hepta.trade-intent.v1" or
            original["paper_only"] is not True or
            original["strategy_id"] != decision["strategy_id"] or
            original["strategy_version"] != decision["strategy_version"] or
            original["strategy_sha256"] != decision["strategy_sha256"] or
            original["instrument"] != "EUR.USD" or
            original["symbol"] != "EUR" or original["currency"] != "USD" or
            original["sec_type"] != "CASH" or
            original["exchange"] != "IDEALPRO" or
            original["side"] not in {"BUY", "SELL"} or
            original["quantity"] != 1 or original["order_type"] != "LMT" or
            original["tif"] != "DAY"):
        raise JoinError("JOIN_ORIGINAL_INTENT_BOUNDARY_INVALID")
    bid = _decimal(tokens["observed_bid"], "JOIN_DECIMAL_QUOTE_INVALID",
                   positive=True)
    ask = _decimal(tokens["observed_ask"], "JOIN_DECIMAL_QUOTE_INVALID",
                   positive=True)
    limit_price = _decimal(
        tokens["limit_price"], "JOIN_DECIMAL_QUOTE_INVALID", positive=True)
    adverse = _decimal(
        tokens["max_adverse_move"], "JOIN_DECIMAL_RISK_INVALID",
        positive=False)
    slippage = _decimal(
        tokens["expected_slippage"], "JOIN_DECIMAL_RISK_INVALID",
        positive=False)
    if Decimal(bid) > Decimal(ask) or limit_price != (
            ask if original["side"] == "BUY" else bid):
        raise JoinError("JOIN_EXECUTABLE_LIMIT_INVALID")
    if Decimal(limit_price) * Decimal(original["quantity"]) > MAX_NOTIONAL:
        raise JoinError("JOIN_NOTIONAL_INVALID")
    observed = original["observed_at_ms"]
    expires = original["expires_at_ms"]
    holding = original["max_holding_ms"]
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (
            observed, expires, holding)):
        raise JoinError("JOIN_INTENT_TIME_INVALID")
    quote_age = now_ms - observed
    if (
            observed > now_ms + 1_000 or quote_age > MAX_QUOTE_AGE_MS or
            quote_age < -1_000 or expires <= now_ms or expires <= observed or
            expires - observed > MAX_INTENT_HORIZON_MS or
            not 1 <= holding <= MAX_INTENT_HORIZON_MS):
        raise JoinError("JOIN_INTENT_TIME_INVALID")
    normalized = {
        **original,
        "limit_price": limit_price,
        "observed_bid": bid,
        "observed_ask": ask,
        "max_adverse_move": adverse,
        "expected_slippage": slippage,
    }
    body = {
        "schema": NORMALIZATION_SCHEMA, "version": VERSION,
        "status": "NORMALIZED_TRADE", "created_at_ms": now_ms,
        "campaign_id": campaign_id, "domain_id": DOMAIN,
        "cycle_id": requested_cycle_id,
        "original_decision_path": decision_path,
        "original_decision_file_sha256": sha(decision_raw),
        "original_decision_id": decision["decision_id"],
        "original_decision_cycle_id": decision["cycle_id"],
        "original_trade_intent_sha256": decision["trade_intent_sha256"],
        "official_validator_path": validator_path,
        "official_validator_file_sha256": validator_file_sha256,
        "strategy_path": strategy_path,
        "strategy_file_sha256": strategy_file_sha256,
        "information_packet_path": information_packet_path,
        "information_packet_file_sha256": information_packet_file_sha256,
        "tool_catalog_path": catalog.path,
        "tool_catalog_file_sha256": catalog.file_sha256,
        "tool_catalog_body_sha256": catalog.body_sha256,
        "capture_path": capture.path,
        "capture_file_sha256": capture.file_sha256,
        "capture_body_sha256": capture.body_sha256,
        "normalization_version": NORMALIZATION_VERSION,
        "normalized_intent": normalized,
        "normalized_intent_sha256": canonical_sha(normalized),
        "quote_age_ms": max(quote_age, 0),
        "max_quote_age_ms": MAX_QUOTE_AGE_MS,
        "max_intent_horizon_ms": MAX_INTENT_HORIZON_MS,
        "max_notional": "5000", "paper_only": True,
        "live_authorized": False, "direct_broker_access": False,
        "authority_granted": False,
    }
    receipt = sealed_body(body)
    _exact(receipt, NORMALIZATION_FIELDS, "JOIN_NORMALIZATION_FIELDS_INVALID")
    return receipt, canonical_json(receipt)


def no_trade_receipt(
        *, decision_raw: bytes, decision_path: str, campaign_id: str,
        requested_cycle_id: str, now_ms: int, validator_path: str,
        validator_file_sha256: str, strategy_path: str,
        strategy_file_sha256: str, information_packet_path: str,
        information_packet_file_sha256: str, capture: Reference
) -> tuple[dict[str, Any], bytes]:
    decision, lexical = strict_json(decision_raw, "JOIN_DECISION")
    _validate_official_decision(decision, lexical, campaign_id=campaign_id)
    if decision["decision"] != "NO_TRADE":
        raise JoinError("JOIN_NO_TRADE_REQUIRED")
    body = {
        "schema": NO_TRADE_SCHEMA, "version": VERSION,
        "status": "NO_TRADE", "created_at_ms": now_ms,
        "campaign_id": campaign_id, "domain_id": DOMAIN,
        "requested_cycle_id": requested_cycle_id,
        "original_decision_path": decision_path,
        "original_decision_file_sha256": sha(decision_raw),
        "official_validator_path": validator_path,
        "official_validator_file_sha256": validator_file_sha256,
        "strategy_path": strategy_path,
        "strategy_file_sha256": strategy_file_sha256,
        "information_packet_path": information_packet_path,
        "information_packet_file_sha256": information_packet_file_sha256,
        "capture_path": capture.path,
        "capture_file_sha256": capture.file_sha256,
        "capture_body_sha256": capture.body_sha256,
        "decision_id": decision["decision_id"],
        "reason_codes": decision["reason_codes"],
        "handoff_created": False, "authority_started": False,
        "paper_only": True, "live_authorized": False,
        "direct_broker_access": False, "authority_granted": False,
    }
    receipt = sealed_body(body)
    _exact(receipt, NO_TRADE_FIELDS, "JOIN_NO_TRADE_FIELDS_INVALID")
    return receipt, canonical_json(receipt)


def deterministic_call_id(campaign: str, cycle: str, role: str) -> str:
    return "p1c-" + hashlib.sha256(
        f"{campaign}\0{cycle}\0{role}".encode("ascii")).hexdigest()[:40]


def _root_cleanup_descriptor() -> dict[str, Any]:
    return {
        "schema": "hepta.p1-paper-canary-root-cleanup-operation-descriptor.v1",
        "version": 1, "call_role": "cleanup-control",
        "tool_name": "host.finalize_external_p1",
        "operation": ROOT_CLEANUP_OPERATION, "effect": "CONTROL",
        "phase": "ROOT_CLEANUP", "socket_path": ROOT_FINALIZER_SOCKET,
        "request_schema": ROOT_CLEANUP_REQUEST_SCHEMA,
        "emergency_request_schema": ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA,
        "response_schema": ROOT_CLEANUP_RECEIPT_SCHEMA,
        "emergency_response_schema": ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA,
        "max_request_bytes": MAX_BYTES, "max_response_bytes": MAX_BYTES,
        "timeout_ms": ROOT_CLEANUP_TIMEOUT_MS, "paper_only": True,
        "live_authorized": False, "authority_granted": False,
    }


def build_handoff(
        *, campaign_id: str, cycle_id: str, now_ms: int,
        normalization: dict[str, Any], policy: Reference,
        p1_audit: Reference, watch_handoff: Reference,
        zero_exposure: Reference, admission_finalization: Reference,
        catalog: Reference, owner: dict[str, Any],
        runtime_profile: Mapping[str, Any], images: list[dict[str, Any]],
        execution_service_epoch: str, fencing_generation: int,
        source_baseline_sha256: str, executor_validator: Callable[..., Any]
) -> bytes:
    _exact(normalization, NORMALIZATION_FIELDS, "JOIN_NORMALIZATION_FIELDS_INVALID")
    sealed(normalization, "JOIN_NORMALIZATION_BODY_INVALID")
    if (
            normalization["status"] != "NORMALIZED_TRADE" or
            normalization["campaign_id"] != campaign_id or
            normalization["cycle_id"] != cycle_id):
        raise JoinError("JOIN_NORMALIZATION_BINDING_INVALID")
    catalog_document = catalog.document
    tools = catalog_document.get("tools")
    if (
            catalog_document.get("schema") != TOOL_CATALOG_SCHEMA or
            catalog_document.get("version") != VERSION or
            not isinstance(tools, list) or len(tools) != len(ROLE_PLAN) or
            catalog_document.get("catalog_sha256") != canonical_sha(tools)):
        raise JoinError("JOIN_CATALOG_INVALID")
    calls: list[dict[str, Any]] = []
    for item, expected in zip(tools, ROLE_PLAN):
        role, tool_name, effect, phase = expected
        if item != {
                "call_role": role, "tool_name": tool_name,
                "tool_descriptor_sha256": item.get("tool_descriptor_sha256"),
                "effect": effect, "phase": phase}:
            raise JoinError("JOIN_CATALOG_PLAN_INVALID")
        _digest(item["tool_descriptor_sha256"], "JOIN_CATALOG_PLAN_INVALID")
        call_id = deterministic_call_id(campaign_id, cycle_id, role)
        calls.append({
            **item, "tool_call_id": call_id,
            "command_id": None if effect == "READ_ONLY" else call_id,
        })
    projection = [{
        key: item[key] for key in (
            "tool_call_id", "tool_name", "tool_descriptor_sha256", "effect")
    } for item in calls]
    cleanup_id = deterministic_call_id(campaign_id, cycle_id, "cleanup-control")
    cleanup = {
        "call_role": "cleanup-control",
        "tool_name": "host.finalize_external_p1",
        "operation": ROOT_CLEANUP_OPERATION, "effect": "CONTROL",
        "phase": "ROOT_CLEANUP", "socket_path": ROOT_FINALIZER_SOCKET,
        "request_schema": ROOT_CLEANUP_REQUEST_SCHEMA,
        "emergency_request_schema": ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA,
        "response_schema": ROOT_CLEANUP_RECEIPT_SCHEMA,
        "emergency_response_schema": ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA,
        "tool_call_id": cleanup_id, "command_id": cleanup_id,
        "tool_descriptor_sha256": canonical_sha(_root_cleanup_descriptor()),
    }
    issued = (now_ms // 1000) * 1000
    body = {
        "schema": HANDOFF_SCHEMA, "version": VERSION,
        "issued_at_ms": issued, "expires_at_ms": issued + 300_000,
        "campaign_id": campaign_id, "domain_id": DOMAIN,
        "policy_sha256": policy.file_sha256,
        "source_baseline_sha256": _digest(
            source_baseline_sha256, "JOIN_SOURCE_BASELINE_INVALID"),
        "p1_audit_receipt_sha256": p1_audit.file_sha256,
        "watch_handoff_receipt_file_sha256": watch_handoff.file_sha256,
        "watch_handoff_receipt_body_sha256": watch_handoff.body_sha256,
        "zero_exposure_attestation_sha256": zero_exposure.file_sha256,
        "admission_finalization_receipt_sha256":
            admission_finalization.file_sha256,
        "strategy_id": normalization["normalized_intent"]["strategy_id"],
        "strategy_version": normalization[
            "normalized_intent"]["strategy_version"],
        "strategy_sha256": normalization[
            "normalized_intent"]["strategy_sha256"],
        "decision_id": "normalized-" + hashlib.sha256(
            canonical_json(normalization)).hexdigest()[:32],
        "decision_sha256": sha(canonical_json(normalization)),
        "cycle_id": cycle_id,
        "intent": normalization["normalized_intent"],
        "intent_sha256": normalization["normalized_intent_sha256"],
        "tool_catalog_sha256": catalog_document["catalog_sha256"],
        "tool_descriptor_set_sha256": canonical_sha(projection),
        "tool_calls": calls, "root_cleanup_call": cleanup,
        "installed_images": images,
        "installed_images_sha256": canonical_sha(images),
        "runtime_profile_reference": dict(runtime_profile),
        "backend_transform_version": BACKEND_TRANSFORM_VERSION,
        "execution_service_epoch": execution_service_epoch,
        "execution_service_fencing_generation": fencing_generation,
        "session_owner_reference": owner,
        "paper_only": True, "live_authorized": False,
        "direct_broker_access": False, "authority_granted": False,
        "one_order_only": True, "end_flat_required": True,
    }
    handoff = sealed_body(body)
    raw = canonical_json(handoff)
    executor_validator(raw, now_ms=now_ms)
    return raw


def _descriptor_digest(payload: dict[str, Any]) -> str:
    for field in ("schema_hash", "descriptor_sha256", "tool_descriptor_sha256"):
        value = payload.get(field)
        if isinstance(value, str) and DIGEST.fullmatch(value):
            return value
    return canonical_sha(payload)


def _heptactl(
        token_path: str, arguments: list[str], *, expected_tool: str
) -> tuple[dict[str, Any], bytes]:
    command = [
        str(HEPTACTL), "--socket", str(TOOL_SOCKET),
        "--token-file", token_path, "--io-timeout-ms", "5000", *arguments,
    ]
    try:
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd="/", env={"LC_ALL": "C"},
            close_fds=True, check=False, timeout=8)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise JoinError("CAPTURE_TOOL_UNAVAILABLE") from error
    if completed.returncode != 0 or completed.stderr or len(completed.stdout) > MAX_BYTES:
        raise JoinError("CAPTURE_TOOL_FAILED")
    document, _lexical = strict_json(completed.stdout, "CAPTURE_TOOL_RESPONSE")
    if (
            document.get("status") != "ok" or
            document.get("tool") != expected_tool or
            document.get("reason_code") != "" or
            document.get("detail") != "" or
            not isinstance(document.get("payload"), dict)):
        raise JoinError("CAPTURE_TOOL_RESPONSE_INVALID")
    return document["payload"], completed.stdout


def peer_capture(request_raw: bytes, *, now_ms: int) -> dict[str, bytes]:
    request, _lexical = strict_json(request_raw, "CAPTURE_REQUEST")
    required = {
        "schema", "version", "campaign_id", "cycle_id", "token_path",
        "strategy_command", "strategy_output_path", "information_packet_path",
        "paper_only", "live_authorized", "authority_granted", "body_sha256",
    }
    _exact(request, frozenset(required), "CAPTURE_REQUEST_FIELDS_INVALID")
    if (
            request["schema"] != CAPTURE_REQUEST_SCHEMA or
            request["version"] != VERSION or request["paper_only"] is not True or
            request["live_authorized"] is not False or
            request["authority_granted"] is not False):
        raise JoinError("CAPTURE_REQUEST_BOUNDARY_INVALID")
    sealed(request, "CAPTURE_REQUEST_BODY_INVALID")
    token_path = request["token_path"]
    entries: list[dict[str, Any]] = []
    catalog_tools: list[dict[str, Any]] = []
    for sequence, (role, tool_name, effect, phase) in enumerate(ROLE_PLAN, 1):
        payload, response_raw = _heptactl(
            token_path, ["tools", "describe", tool_name],
            expected_tool="system.tools.describe")
        descriptor = _descriptor_digest(payload)
        catalog_tools.append({
            "call_role": role, "tool_name": tool_name,
            "tool_descriptor_sha256": descriptor,
            "effect": effect, "phase": phase,
        })
        entries.append({
            "sequence": sequence, "operation": "DESCRIBE",
            "tool_name": "system.tools.describe", "target_tool": tool_name,
            "effect": "READ_ONLY", "response_sha256": sha(response_raw),
            "tool_descriptor_sha256": descriptor,
        })
    fresh_payloads: dict[str, Any] = {}
    for tool_name, arguments in FRESH_READS:
        payload, response_raw = _heptactl(
            token_path, ["call", tool_name, *arguments],
            expected_tool=tool_name)
        fresh_payloads[tool_name] = payload
        entries.append({
            "sequence": len(entries) + 1, "operation": "CALL",
            "tool_name": tool_name, "target_tool": tool_name,
            "effect": "READ_ONLY", "response_sha256": sha(response_raw),
            "tool_descriptor_sha256": next(
                item["tool_descriptor_sha256"] for item in catalog_tools
                if item["tool_name"] == tool_name),
        })
    catalog_body = {
        "schema": TOOL_CATALOG_SCHEMA, "version": VERSION,
        "catalog_sha256": canonical_sha(catalog_tools),
        "tools": catalog_tools, "authority_granted": False,
    }
    catalog = sealed_body(catalog_body)
    capture_body = {
        "schema": CAPTURE_SCHEMA, "version": VERSION,
        "captured_at_ms": now_ms, "campaign_id": request["campaign_id"],
        "domain_id": DOMAIN, "cycle_id": request["cycle_id"],
        "describe_count": len(ROLE_PLAN), "fresh_read_count": len(FRESH_READS),
        "entries": entries, "entries_sha256": canonical_sha(entries),
        "fresh_payloads": fresh_payloads,
        "fresh_payloads_sha256": canonical_sha(fresh_payloads),
        "mutation_count": 0, "control_count": 0,
        "paper_only": True, "live_authorized": False,
        "authority_granted": False,
    }
    capture = sealed_body(capture_body)
    strategy_command = request["strategy_command"]
    if (
            not isinstance(strategy_command, list) or not strategy_command or
            any(not isinstance(item, str) for item in strategy_command) or
            strategy_command[0] != "/usr/bin/python3.12" or
            str(STRATEGY_RUNNER) not in strategy_command):
        raise JoinError("CAPTURE_STRATEGY_COMMAND_INVALID")
    try:
        completed = subprocess.run(
            strategy_command, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/",
            env={"LC_ALL": "C"}, close_fds=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise JoinError("CAPTURE_STRATEGY_UNAVAILABLE") from error
    if completed.returncode != 0 or len(completed.stdout) > 65536 or \
            len(completed.stderr) > 65536:
        raise JoinError("CAPTURE_STRATEGY_REJECTED")
    decision_path = Path(request["strategy_output_path"])
    decision_raw = stable_read(
        decision_path, uid=PEER_UID, gid=PEER_GID, modes=frozenset({0o600}))
    decision, lexical = strict_json(decision_raw, "CAPTURE_DECISION")
    _validate_official_decision(
        decision, lexical, campaign_id=request["campaign_id"])
    return {
        "tool-catalog.v1.json": canonical_json(catalog),
        "read-only-capture.v1.json": canonical_json(capture),
        "original-strategy-decision.v1.json": decision_raw,
    }


def _write_exclusive(path: Path, raw: bytes, *, uid: int, gid: int,
                     mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, mode)
        try:
            os.fchown(descriptor, uid, gid)
            os.fchmod(descriptor, mode)
            offset = 0
            while offset < len(raw):
                offset += os.write(descriptor, raw[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise JoinError("JOIN_ARTIFACT_PUBLISH_FAILED") from error
    try:
        directory = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as error:
        raise JoinError("JOIN_ARTIFACT_PUBLISH_FAILED") from error


def _write_or_same(path: Path, raw: bytes, *, uid: int, gid: int,
                   mode: int) -> None:
    try:
        _write_exclusive(path, raw, uid=uid, gid=gid, mode=mode)
        return
    except JoinError:
        if not (path.exists() or path.is_symlink()):
            raise
    reopened = stable_read(path, uid=uid, gid=gid, modes=frozenset({mode}))
    if reopened != raw:
        raise JoinError("JOIN_ARTIFACT_CONFLICT")


def _sessionctl(arguments: list[str]) -> tuple[int, dict[str, Any]]:
    try:
        completed = subprocess.run(
            [str(SESSIONCTL), "--socket", str(SESSION_SUPERVISOR_SOCKET),
             *arguments], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd="/", env={"LC_ALL": "C"},
            close_fds=True, check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise JoinError("JOIN_CAPTURE_SESSION_UNCERTAIN") from error
    if len(completed.stdout) > 65536 or len(completed.stderr) > 65536:
        raise JoinError("JOIN_CAPTURE_SESSION_RESPONSE_INVALID")
    try:
        response = json.loads(
            completed.stdout.decode("ascii"), object_pairs_hook=lambda pairs:
            _pairs(pairs), parse_float=lambda _value: (_ for _ in ()).throw(
                ValueError()), parse_constant=lambda _value:
            (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise JoinError("JOIN_CAPTURE_SESSION_RESPONSE_INVALID") from error
    if not isinstance(response, dict):
        raise JoinError("JOIN_CAPTURE_SESSION_RESPONSE_INVALID")
    return completed.returncode, response


def _capture_session_id(campaign: str, cycle: str) -> str:
    return "p1-capture-" + hashlib.sha256(
        f"{campaign}\0{cycle}".encode("ascii")).hexdigest()[:32]


def _capture_owner_begin(
        control: Path, campaign: str, cycle: str, now_ms: int
) -> tuple[dict[str, Any], bytes]:
    intent_path = control / "capture-session-owner.v1.json"
    retirement_path = control / "capture-session-retirement-receipt.v1.json"
    if any(path.exists() or path.is_symlink() for path in (
            CAPTURE_TOKEN_SOURCE, intent_path, retirement_path)):
        raise JoinError("JOIN_CAPTURE_OWNER_PREEXISTING")
    token = os.urandom(32).hex().encode("ascii") + b"\n"
    _write_exclusive(
        CAPTURE_TOKEN_SOURCE, token, uid=0, gid=0, mode=0o400)
    session_id = _capture_session_id(campaign, cycle)
    body = {
        "schema": "hepta.p1-paper-canary-capture-owner.v1", "version": 1,
        "status": "OWNER_MAY_EXIST", "created_at_ms": now_ms,
        "campaign_id": campaign, "domain_id": DOMAIN, "cycle_id": cycle,
        "template_id": "watch", "session_id": session_id,
        "expected_lease_generation": 1, "peer_uid": PEER_UID,
        "token_path": str(CAPTURE_TOKEN_SOURCE), "token_sha256": sha(token),
        "paper_only": True, "live_authorized": False,
        "authority_granted": False,
    }
    intent = sealed_body(body)
    intent_raw = canonical_json(intent)
    # The generation-bound intent is durable before HSL can be mutated.
    _write_exclusive(intent_path, intent_raw, uid=0, gid=0, mode=0o600)
    code, response = _sessionctl([
        "provision", "--template", "watch", "--token-file",
        str(CAPTURE_TOKEN_SOURCE), "--agent-id", "hepta-agent-alpha",
        "--session-id", session_id, "--peer-uid", str(PEER_UID),
        "--ttl-sec", "300",
    ])
    if response != {
            "accepted": True, "reason_code": "OK", "lease_generation": 1
    } or code != 0:
        raise JoinError("JOIN_CAPTURE_SESSION_PROVISION_REJECTED")
    return intent, token


def _capture_owner_retire(
        control: Path, intent: Mapping[str, Any], token: bytes, now_ms: int
) -> bytes:
    if (intent.get("expected_lease_generation") != 1 or
            intent.get("token_sha256") != sha(token)):
        raise JoinError("JOIN_CAPTURE_OWNER_BINDING_INVALID")
    revoke_arguments = [
        "revoke", "--token-file", str(CAPTURE_TOKEN_SOURCE),
        "--generation", "1", "--token-owner-uid", "0",
    ]
    code, response = _sessionctl(revoke_arguments)
    if code != 0 or response != {
            "accepted": True, "reason_code": "OK", "lease_generation": 1
    }:
        # Never delete the only recovery bearer when durable HSL retirement is
        # uncertain.  ExecStopPost can replay this exact generation.
        raise JoinError("JOIN_CAPTURE_SESSION_REVOKE_UNCERTAIN")
    audit_code, audit_response = _sessionctl(revoke_arguments)
    if not (
            audit_code == 4 and set(audit_response) == {
                "accepted", "reason_code", "lease_generation"} and
            audit_response.get("accepted") is False and
            audit_response.get("reason_code") in {
                "SESSION_NOT_FOUND", "SESSION_LEASE_NOT_FOUND"} and
            audit_response.get("lease_generation") in {0, 1}):
        raise JoinError("JOIN_CAPTURE_SESSION_REVOKE_AUDIT_UNCERTAIN")
    body = {
        "schema": "hepta.p1-paper-canary-capture-owner-retirement.v1",
        "version": 1, "status": "RETIRED", "completed_at_ms": now_ms,
        "campaign_id": intent["campaign_id"], "domain_id": DOMAIN,
        "cycle_id": intent["cycle_id"], "template_id": "watch",
        "session_id": intent["session_id"], "lease_generation": 1,
        "token_sha256": intent["token_sha256"],
        "owner_intent_body_sha256": intent["body_sha256"],
        "revoke_accepted": True, "revoke_reason_code": "OK",
        "revoke_audit_reason_code": audit_response["reason_code"],
        "durable_hsl_audit": "GENERATION_ABSENT_AFTER_REVOKE",
        "paper_only": True, "live_authorized": False,
        "authority_granted": False,
    }
    receipt = sealed_body(body)
    raw = canonical_json(receipt)
    _write_exclusive(
        control / "capture-session-retirement-receipt.v1.json", raw,
        uid=0, gid=0, mode=0o600)
    try:
        CAPTURE_TOKEN_SOURCE.unlink()
        directory = os.open(
            CAPTURE_TOKEN_SOURCE.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as error:
        raise JoinError("JOIN_CAPTURE_BEARER_DESTROY_FAILED") from error
    return raw


def _pinned_reference(value: Any, *, sealed_required: bool = True) -> Reference:
    fields = PINNED_REFERENCE_FIELDS if sealed_required else \
        PLAIN_REFERENCE_FIELDS
    item = _exact(value, fields, "JOIN_PINNED_REFERENCE_INVALID")
    path = Path(item["path"])
    if not path.is_absolute() or path.as_posix() != item["path"]:
        raise JoinError("JOIN_PINNED_REFERENCE_INVALID")
    raw = stable_read(
        path, uid=item["uid"], gid=item["gid"],
        modes=frozenset({item["mode"]}))
    if (
            sha(raw) != item["file_sha256"] or len(raw) != item["size"] or
            item["nlink"] != 1):
        raise JoinError("JOIN_PINNED_REFERENCE_INVALID")
    body = None
    if sealed_required:
        document, _lexical = strict_json(raw, "JOIN_PINNED_REFERENCE")
        body = sealed(document, "JOIN_PINNED_REFERENCE_BODY_INVALID")
        if body != item["body_sha256"]:
            raise JoinError("JOIN_PINNED_REFERENCE_BODY_INVALID")
    else:
        try:
            document, _lexical = strict_json(raw, "JOIN_PINNED_REFERENCE")
        except JoinError:
            document = {}
    return Reference(str(path), raw, document, sha(raw), body)


def _installed_images(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(IMAGE_PATHS):
        raise JoinError("JOIN_INSTALLED_IMAGES_INVALID")
    result: list[dict[str, Any]] = []
    for item, (role, path_value) in zip(value, IMAGE_PATHS):
        if (
                not isinstance(item, dict) or set(item) != {
                    "role", "path", "file_sha256", "mode", "uid", "gid",
                    "nlink"} or item["role"] != role or
                item["path"] != path_value or item["mode"] != 0o755 or
                item["uid"] != 0 or item["gid"] != 0 or item["nlink"] != 1):
            raise JoinError("JOIN_INSTALLED_IMAGES_INVALID")
        raw = stable_read(
            Path(path_value), uid=0, gid=0, modes=frozenset({0o755}))
        if sha(raw) != item["file_sha256"]:
            raise JoinError("JOIN_INSTALLED_IMAGES_INVALID")
        result.append(dict(item))
    return result


def _load_executor_validator(image: Mapping[str, Any]) -> Callable[..., Any]:
    raw = stable_read(
        Path(image["path"]), uid=0, gid=0, modes=frozenset({0o755}))
    if sha(raw) != image["file_sha256"]:
        raise JoinError("JOIN_EXECUTOR_IMAGE_INVALID")
    from types import ModuleType
    import sys
    module = ModuleType("_hepta_p1_canary_join_executor_validator")
    module.__file__ = image["path"]
    sys.modules[module.__name__] = module
    try:
        exec(compile(raw, image["path"], "exec"), module.__dict__)
    except Exception as error:
        raise JoinError("JOIN_EXECUTOR_IMAGE_INVALID") from error
    function = getattr(module, "validate_handoff", None)
    if not callable(function):
        raise JoinError("JOIN_EXECUTOR_IMAGE_INVALID")
    return function


def _owner_reference(
        campaign: str, cycle: str, execution_service_epoch: str,
        fencing_generation: int, owner_account: str,
        owner_execution_domain: str) -> dict[str, Any]:
    if any(path.exists() or path.is_symlink() for path in (
            OWNER_AUTHORITY_PATH, OWNER_REVOKE_PATH, OWNER_INTENT_PATH)):
        raise JoinError("JOIN_OWNER_PREEXISTING")
    if (PAPER_ACCOUNT.fullmatch(owner_account) is None or
            owner_execution_domain != "PAPER:alpha"):
        raise JoinError("JOIN_OWNER_SCOPE_INVALID")
    directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
    if not directory or not Path(directory).is_absolute():
        raise JoinError("JOIN_OWNER_CREDENTIAL_DIRECTORY_MISSING")
    credential = Path(directory) / OWNER_PROVISIONER_CREDENTIAL
    credential_raw = stable_read(
        credential, uid=0, gid=0, modes=frozenset({0o400}),
        maximum=4 * MAX_BYTES)
    installed_raw = stable_read(
        OWNER_PROVISIONER, uid=0, gid=0, modes=frozenset({0o755}),
        maximum=4 * MAX_BYTES)
    if credential_raw != installed_raw:
        raise JoinError("JOIN_OWNER_PROVISIONER_IMAGE_MISMATCH")
    try:
        completed = subprocess.run(
            ["/usr/bin/python3.12", "-I", "-S", str(credential),
             "--campaign-id", campaign,
             "--cycle-id", cycle, "--execution-service-epoch",
             execution_service_epoch, "--fencing-generation",
             str(fencing_generation), "--owner-account", owner_account,
             "--owner-execution-domain", owner_execution_domain],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/",
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/usr/sbin",
                 "CREDENTIALS_DIRECTORY": directory},
            close_fds=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise JoinError("JOIN_OWNER_PROVISION_FAILED") from error
    if completed.returncode != 0:
        raise JoinError("JOIN_OWNER_PROVISION_FAILED")
    parent = os.lstat(OWNER_ROOT)
    if (
            stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode) or
            parent.st_uid != 0 or parent.st_gid != 0 or
            stat.S_IMODE(parent.st_mode) != 0o700):
        raise JoinError("JOIN_OWNER_PARENT_INVALID")
    token = stable_read(
        OWNER_TOKEN_PATH, uid=PEER_UID, gid=PEER_GID,
        modes=frozenset({0o400}), maximum=4096)
    revoke = stable_read(
        OWNER_REVOKE_PATH, uid=0, gid=0, modes=frozenset({0o600}),
        maximum=4096)
    authority_ref = reference(
        OWNER_AUTHORITY_PATH, uid=0, gid=0, modes=frozenset({0o600}))
    authority = authority_ref.document
    required = {
        "schema", "version", "created_at_ms", "campaign_id", "domain_id",
        "cycle_id",
        "token_name", "lease_generation", "session_id", "peer_uid",
        "peer_gid", "token_sha256", "execution_service_epoch",
        "execution_service_fencing_generation", "owner_account",
        "owner_execution_domain", "paper_only",
        "live_authorized", "authority_granted", "body_sha256",
    }
    _exact(authority, frozenset(required), "JOIN_OWNER_AUTHORITY_INVALID")
    if (
            authority["campaign_id"] != campaign or
            authority["cycle_id"] != cycle or authority["domain_id"] != DOMAIN or
            authority["token_name"] != "session.token" or
            authority["peer_uid"] != PEER_UID or
            authority["peer_gid"] != PEER_GID or token != revoke or
            re.fullmatch(rb"[0-9a-f]{64}\n", revoke) is None or
            sha(token) != authority["token_sha256"] or
            authority["execution_service_epoch"] != execution_service_epoch or
            authority["execution_service_fencing_generation"] !=
                fencing_generation or
            authority["owner_account"] != owner_account or
            authority["owner_execution_domain"] != owner_execution_domain or
            authority["paper_only"] is not True or
            authority["live_authorized"] is not False or
            authority["authority_granted"] is not False):
        raise JoinError("JOIN_OWNER_AUTHORITY_INVALID")
    return {
        "token_name": "session.token", "token_path": str(OWNER_TOKEN_PATH),
        "authority_path": str(OWNER_AUTHORITY_PATH),
        "authority_file_sha256": authority_ref.file_sha256,
        "authority_body_sha256": authority_ref.body_sha256,
        "lease_generation": authority["lease_generation"],
        "session_id": authority["session_id"], "peer_uid": PEER_UID,
        "peer_gid": PEER_GID, "token_sha256": sha(token),
        "revoke_bearer_path": str(OWNER_REVOKE_PATH),
        "revoke_bearer_sha256": sha(revoke),
        "owner_account": owner_account,
        "owner_execution_domain": owner_execution_domain,
    }


def _control_directories(campaign: str, cycle: str) -> tuple[Path, Path]:
    control_campaign = CONTROL_ROOT / campaign
    control_campaign.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(control_campaign, 0, 0)
    os.chmod(control_campaign, 0o700)
    control = control_campaign / cycle
    control.mkdir(exist_ok=True, mode=0o700)
    os.chown(control, 0, 0)
    os.chmod(control, 0o700)
    artifact_campaign = ARTIFACT_ROOT / campaign
    artifact_campaign.mkdir(parents=True, exist_ok=True, mode=0o711)
    os.chown(artifact_campaign, 0, 0)
    os.chmod(artifact_campaign, 0o711)
    artifact = artifact_campaign / cycle
    artifact.mkdir(exist_ok=True, mode=0o1730)
    os.chown(artifact, 0, PEER_GID)
    os.chmod(artifact, 0o1730)
    return control, artifact


def _strategy_command(
        pointer: dict[str, Any], campaign: str, artifact: Path,
        now_ms: int) -> list[str]:
    return [
        "/usr/bin/python3.12", "-I", "-S", str(STRATEGY_RUNNER),
        "--campaign-id", campaign, "--iteration", "1",
        "--evaluated-at-ms", str(now_ms),
        "--policy", pointer["strategy_policy_reference"]["path"],
        "--strategy", pointer["strategy_reference"]["path"],
        "--snapshot", pointer["strategy_snapshot_reference"]["path"],
        "--quote-history", pointer["quote_history_reference"]["path"],
        "--bar-history", pointer["bar_history_reference"]["path"],
        "--calendar", pointer["calendar_reference"]["path"],
        "--information", pointer["information_reference"]["path"],
        "--receipt-output", str(artifact / "original-strategy-decision.v1.json"),
        "--state", str(artifact / "canary-strategy-state.v1.json"),
    ]


def root_join(campaign: str, cycle: str, *, now_ms: int) -> dict[str, Any]:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise JoinError("JOIN_ROOT_REQUIRED")
    pointer_raw = stable_read(
        LAUNCH_INPUT_POINTER, uid=0, gid=0, modes=frozenset({0o600}))
    pointer, _lexical = strict_json(pointer_raw, "JOIN_INPUT_POINTER")
    _exact(pointer, LAUNCH_INPUT_FIELDS, "JOIN_INPUT_POINTER_FIELDS_INVALID")
    sealed(pointer, "JOIN_INPUT_POINTER_BODY_INVALID")
    if (
            pointer["schema"] !=
                "hepta.p1-paper-canary-launch-input-pointer.v1" or
            pointer["version"] != VERSION or pointer["status"] != "FINALIZED" or
            pointer["campaign_id"] != campaign or
            pointer["paper_only"] is not True or
            pointer["live_authorized"] is not False or
            pointer["direct_broker_access"] is not False or
            pointer["authority_granted"] is not False or
            pointer["capture_token_path"] !=
                str(CAPTURE_TOKEN_SOURCE)):
        raise JoinError("JOIN_INPUT_POINTER_BOUNDARY_INVALID")
    policy = _pinned_reference(
        pointer["policy_reference"], sealed_required=False)
    p1_audit = _pinned_reference(pointer["p1_audit_reference"])
    watch = _pinned_reference(pointer["watch_handoff_reference"])
    zero = _pinned_reference(pointer["zero_exposure_reference"])
    finalization = _pinned_reference(
        pointer["admission_finalization_reference"])
    strategy_policy = _pinned_reference(
        pointer["strategy_policy_reference"])
    strategy = _pinned_reference(
        pointer["strategy_reference"], sealed_required=False)
    for field in (
            "strategy_snapshot_reference", "quote_history_reference",
            "bar_history_reference", "calendar_reference",
            "information_reference", "information_packet_reference"):
        _pinned_reference(pointer[field])
    images = _installed_images(pointer["installed_images"])
    runtime_profile = pointer["runtime_profile_reference"]
    profile_ref = _pinned_reference(runtime_profile, sealed_required=False)
    profile_text = profile_ref.raw.decode("ascii", errors="strict")
    values: dict[str, str] = {}
    ordered_keys: list[str] = []
    for line in profile_text.splitlines():
        if not line or line.startswith("#") or line.count("=") != 1:
            raise JoinError("JOIN_RUNTIME_PROFILE_INVALID")
        key, value = line.split("=", 1)
        if key in values or not value:
            raise JoinError("JOIN_RUNTIME_PROFILE_INVALID")
        values[key] = value
        ordered_keys.append(key)
    exact_profile = {
        "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY": "1",
        "HEPTA_EXECUTION_MAX_ORDER_NOTIONAL": "5000",
        "HEPTA_IB_EXECUTION_MODE": "PAPER",
        "HEPTA_IB_PAPER_HOST": "127.0.0.1",
        "HEPTA_IB_PAPER_PORT": "4002",
        "HEPTA_IB_PAPER_CLIENT_ID": "701",
        "HEPTA_IB_PAPER_MAX_ORDER_QTY": "1",
        "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL": "5000",
        "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE": "1",
        "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS": "1",
        "HEPTA_IB_PAPER_MAX_GROSS_POSITION": "1",
        "HEPTA_IB_PAPER_QUOTE_CONTRACTS":
            "EUR.USD|EUR|CASH|IDEALPRO|USD",
        "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT": "EUR.USD",
        "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS": "5000",
        "HEPTA_IB_EXECUTION_GATEWAY_UID": "2101",
        "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID": "alpha",
        "HEPTA_IB_EXECUTION_DOMAIN_ID": "alpha",
        "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES": "16384",
        "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS": "2500",
        "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS": "30000",
        "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS": "180000",
    }
    if (
            tuple(ordered_keys) != PROFILE_KEYS or set(values) != set(PROFILE_KEYS) or
            any(values.get(key) != expected
                for key, expected in exact_profile.items()) or
            PAPER_ACCOUNT.fullmatch(values.get("HEPTA_IB_PAPER_ACCOUNT", ""))
                is None):
        raise JoinError("JOIN_RUNTIME_PROFILE_INVALID")
    control, artifact = _control_directories(campaign, cycle)
    capture_owner, capture_token = _capture_owner_begin(
        control, campaign, cycle, now_ms)
    capture_body = {
        "schema": CAPTURE_REQUEST_SCHEMA, "version": VERSION,
        "campaign_id": campaign, "cycle_id": cycle,
        "token_path": str(CAPTURE_TOKEN_CREDENTIAL),
        "strategy_command": _strategy_command(pointer, campaign, artifact, now_ms),
        "strategy_output_path": str(
            artifact / "original-strategy-decision.v1.json"),
        "information_packet_path": pointer[
            "information_packet_reference"]["path"],
        "paper_only": True, "live_authorized": False,
        "authority_granted": False,
    }
    capture_request = sealed_body(capture_body)
    capture_raw = canonical_json(capture_request)
    _write_or_same(
        control / "capture-request.v1.json", capture_raw,
        uid=0, gid=0, mode=0o600)
    ACTIVE_CAPTURE_REQUEST.parent.mkdir(
        parents=True, exist_ok=True, mode=0o700)
    os.chown(ACTIVE_CAPTURE_REQUEST.parent, 0, 0)
    os.chmod(ACTIVE_CAPTURE_REQUEST.parent, 0o700)
    _write_or_same(
        ACTIVE_CAPTURE_REQUEST, capture_raw, uid=0, gid=0, mode=0o600)
    capture_failure: Optional[BaseException] = None
    try:
        completed = subprocess.run(
            ["/usr/bin/systemctl", "start", "hepta-p1-paper-canary-capture.service"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd="/", env={"LC_ALL": "C"},
            close_fds=True, check=False, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as error:
        capture_failure = error
        completed = None
    if completed is not None and completed.returncode != 0:
        capture_failure = JoinError("JOIN_CAPTURE_UNIT_FAILED")
    try:
        _capture_owner_retire(
            control, capture_owner, capture_token,
            time.time_ns() // 1_000_000)
    except BaseException as error:
        raise JoinError("JOIN_CAPTURE_OWNER_RECOVERY_REQUIRED") from error
    if capture_failure is not None:
        raise JoinError("JOIN_CAPTURE_UNIT_FAILED") from capture_failure
    try:
        ACTIVE_CAPTURE_REQUEST.unlink()
    except FileNotFoundError:
        pass
    catalog = reference(
        artifact / "tool-catalog.v1.json", uid=PEER_UID, gid=PEER_GID,
        modes=frozenset({0o600}))
    capture = reference(
        artifact / "read-only-capture.v1.json", uid=PEER_UID, gid=PEER_GID,
        modes=frozenset({0o600}))
    decision_path = artifact / "original-strategy-decision.v1.json"
    decision_raw = stable_read(
        decision_path, uid=PEER_UID, gid=PEER_GID,
        modes=frozenset({0o600}))
    validator_path, validator_sha = run_official_validator(decision_path)
    decision_now = time.time_ns() // 1_000_000
    decision, lexical = strict_json(decision_raw, "JOIN_DECISION")
    _validate_official_decision(decision, lexical, campaign_id=campaign)
    if decision["decision"] == "NO_TRADE":
        receipt, raw = no_trade_receipt(
            decision_raw=decision_raw, decision_path=str(decision_path),
            campaign_id=campaign, requested_cycle_id=cycle,
            now_ms=decision_now,
            validator_path=validator_path,
            validator_file_sha256=validator_sha,
            strategy_path=strategy.path, strategy_file_sha256=strategy.file_sha256,
            information_packet_path=pointer[
                "information_packet_reference"]["path"],
            information_packet_file_sha256=pointer[
                "information_packet_reference"]["file_sha256"],
            capture=capture)
        _write_or_same(
            control / "no-trade-launch-receipt.v1.json", raw,
            uid=0, gid=0, mode=0o600)
        return {
            "schema": "hepta.p1-paper-canary-launch-result.v1",
            "version": VERSION, "status": "NO_TRADE",
            "campaign_id": campaign, "cycle_id": cycle,
            "capture_request_file_sha256": sha(capture_raw),
            "no_trade_receipt_file_sha256": sha(raw),
            "normalization_receipt_file_sha256": None,
            "handoff_file_sha256": None, "authority_started": False,
            "authority_granted": False,
        }
    normalization, normalization_raw = normalize_trade_decision(
        decision_raw=decision_raw, decision_path=str(decision_path),
        campaign_id=campaign, requested_cycle_id=cycle, now_ms=decision_now,
        validator_path=validator_path, validator_file_sha256=validator_sha,
        strategy_path=strategy.path, strategy_file_sha256=strategy.file_sha256,
        information_packet_path=pointer[
            "information_packet_reference"]["path"],
        information_packet_file_sha256=pointer[
            "information_packet_reference"]["file_sha256"],
        catalog=catalog, capture=capture)
    _write_or_same(
        control / "normalized-launch-intent-receipt.v1.json",
        normalization_raw, uid=0, gid=0, mode=0o600)
    owner = _owner_reference(
        campaign, cycle, pointer["execution_service_epoch"],
        pointer["execution_service_fencing_generation"],
        values["HEPTA_IB_PAPER_ACCOUNT"], "PAPER:alpha")
    if (
            owner["lease_generation"] < 1 or
            pointer["execution_service_epoch"] == "" or
            pointer["execution_service_fencing_generation"] < 1):
        raise JoinError("JOIN_EXECUTION_OWNER_INVALID")
    handoff_raw = build_handoff(
        campaign_id=campaign, cycle_id=cycle, now_ms=decision_now,
        normalization=normalization, policy=policy, p1_audit=p1_audit,
        watch_handoff=watch, zero_exposure=zero,
        admission_finalization=finalization, catalog=catalog, owner=owner,
        runtime_profile=runtime_profile, images=images,
        execution_service_epoch=pointer["execution_service_epoch"],
        fencing_generation=pointer["execution_service_fencing_generation"],
        source_baseline_sha256=pointer["source_baseline_sha256"],
        executor_validator=_load_executor_validator(images[0]))
    _write_or_same(
        control / "execution-handoff.v1.json", handoff_raw,
        uid=0, gid=0, mode=0o600)
    return {
        "schema": "hepta.p1-paper-canary-launch-result.v1",
        "version": VERSION, "status": "TRADE", "campaign_id": campaign,
        "cycle_id": cycle, "capture_request_file_sha256": sha(capture_raw),
        "no_trade_receipt_file_sha256": None,
        "normalization_receipt_file_sha256": sha(normalization_raw),
        "handoff_file_sha256": sha(handoff_raw), "authority_started": True,
        "authority_granted": False,
    }


def peer_main() -> int:
    if os.geteuid() != PEER_UID or os.getegid() != PEER_GID:
        raise JoinError("CAPTURE_PEER_IDENTITY_INVALID")
    raw = stable_read(
        CAPTURE_CREDENTIAL, uid=PEER_UID, gid=PEER_GID,
        modes=frozenset({0o400, 0o600}))
    request, _lexical = strict_json(raw, "CAPTURE_REQUEST")
    campaign = _identifier(request.get("campaign_id"), "CAPTURE_CAMPAIGN_INVALID")
    cycle = _identifier(request.get("cycle_id"), "CAPTURE_CYCLE_INVALID")
    directory = ARTIFACT_ROOT / campaign / cycle
    artifacts = peer_capture(raw, now_ms=time.time_ns() // 1_000_000)
    for name, payload in artifacts.items():
        _write_or_same(
            directory / name, payload, uid=PEER_UID, gid=PEER_GID, mode=0o600)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--campaign-id")
    parser.add_argument("--cycle-id")
    parser.add_argument("--peer-capture", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        if arguments.peer_capture:
            if arguments.campaign_id is not None or arguments.cycle_id is not None:
                raise JoinError("CAPTURE_ARGUMENTS_INVALID")
            return peer_main()
        campaign = _identifier(arguments.campaign_id, "JOIN_CAMPAIGN_INVALID")
        cycle = _identifier(arguments.cycle_id, "JOIN_CYCLE_INVALID")
        if os.geteuid() != 0 or os.getegid() != 0:
            raise JoinError("JOIN_ROOT_REQUIRED")
        result = root_join(
            campaign, cycle, now_ms=time.time_ns() // 1_000_000)
        print(canonical_json(result).decode("ascii"), end="")
        return 0 if result["status"] == "TRADE" else 4
    except JoinError as error:
        print(f"hepta-p1-paper-canary-launch-joiner: FAIL {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
