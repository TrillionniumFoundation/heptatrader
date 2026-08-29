#!/usr/bin/env -S /usr/bin/python3.12 -I -S

"""Root outer coordinator for one fixed external-P1 PAPER canary cycle.

Peer strategy v2/v3 receipts and the inner root-finalizer receipt are
compatibility and transition evidence.  Only the outer cycle-completion
receipt is allowed to
claim P2 success, after the executor and finalizer listener have exited and a
fresh host proof has established the terminal DENY_ALL boundary.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time
from types import ModuleType
from typing import Any, Mapping, Optional, Protocol


VERSION = 1
OUTER_VERSION = 4
DOMAIN = "alpha"
MAX_BYTES = 1024 * 1024
PEER_UID = 2104
PEER_GID = 2104
EXECUTOR_UNIT = "hepta-p1-paper-canary-executor.service"
CAPTURE_UNIT = "hepta-p1-paper-canary-capture.service"
FINALIZER_SOCKET_UNIT = "hepta-p1-paper-canary-finalizer.socket"
COORDINATOR_UNIT = "hepta-p1-paper-canary-root-coordinator.service"
FINALIZER_CONNECTION_GLOB = "hepta-p1-paper-canary-finalizer@*.service"
COORDINATOR_ROLE = "NON_AUTHORIZING_PRODUCER_SELF_EXCLUDED"
ROOT_FINALIZER_SOCKET = Path("/run/hepta-p1-paper-canary-finalizer.sock")
ACTIVE_HANDOFF = Path(
    "/run/hepta-p1-paper-canary/active-execution-handoff.v1.json")
ACTIVE_CAPTURE_REQUEST = Path(
    "/run/hepta-p1-paper-canary/active-capture-request.v1.json")
ACTIVE_COORDINATOR_REQUEST = Path(
    "/run/hepta-p1-paper-canary/active-coordinator-request.v1.json")
CONTROL_ROOT = Path("/var/lib/hepta/p1-paper-canary-control")
ARTIFACT_ROOT = Path("/var/lib/hepta/p1-paper-canary")
STATE_ROOT = Path("/var/lib/hepta-local-ai-paper-agent")
OWNER_AUTHORITY_ROOT = STATE_ROOT / "session-authority"
WAL_PATH = STATE_ROOT / "p1-paper-canary-root-coordinator-transaction.v4.json"
LEGACY_WAL_PATHS = (
    STATE_ROOT / "p1-paper-canary-root-coordinator-transaction.v1.json",
    STATE_ROOT / "p1-paper-canary-root-coordinator-transaction.v2.json",
    STATE_ROOT / "p1-paper-canary-root-coordinator-transaction.v3.json",
)
LOCK_PATH = STATE_ROOT / "p1-paper-canary-root-coordinator.lock"
INSTALLED_COORDINATOR = Path(
    "/usr/libexec/hepta-p1-paper-canary-root-coordinator")
INSTALLED_LAUNCH_JOINER = Path(
    "/usr/libexec/hepta-p1-paper-canary-launch-joiner")
INSTALLED_CRASH_CLOSER = Path(
    "/usr/libexec/hepta-p1-paper-canary-crash-emergency-closer")
INSTALLED_TERMINAL_PROVER = Path(
    "/usr/libexec/hepta-p1-paper-canary-terminal-prover")
COORDINATOR_CREDENTIAL = "hepta-p1-paper-canary-root-coordinator.py"
LAUNCH_JOINER_CREDENTIAL = "hepta-p1-paper-canary-launch-joiner.py"
CRASH_CLOSER_CREDENTIAL = (
    "hepta-p1-paper-canary-crash-emergency-closer.py")
TERMINAL_PROVER_CREDENTIAL = "hepta-p1-paper-canary-terminal-prover.py"
COMPLETION_SCHEMA = "hepta.p1-paper-canary-cycle-completion-receipt.v4"
COORDINATOR_REQUEST_SCHEMA = (
    "hepta.p1-paper-canary-root-coordinator-request.v1")
WAL_SCHEMA = "hepta.p1-paper-canary-root-coordinator-transaction.v4"
RESULT_SCHEMA = "hepta.p1-paper-canary-execution-result.v1"
HANDOFF_SCHEMA = "hepta.p1-paper-canary-execution-handoff.v1"
NORMAL_ROOT_SCHEMA = "hepta.p1-paper-canary-root-cleanup-receipt.v4"
EMERGENCY_ROOT_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-receipt.v1")
NORMAL_REQUEST_SCHEMA = "hepta.p1-paper-canary-root-cleanup-request.v1"
EMERGENCY_REQUEST_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-request.v1")
NORMAL_STATUS = "ROOT_CLEANUP_COMPLETE_DENY_ALL"
EMERGENCY_STATUS = "ROOT_EMERGENCY_CLEANUP_COMPLETE_DENY_ALL"
NORMAL_REQUIRED_ACTIONS = [
    "FINALIZE_DURABLE_OWNER_POST_FENCE",
    "ACK_PURGE_DURABLE_OWNER",
    "STOP_GUARDIAN",
    "DISABLE_EXECUTION_CONTROL",
    "ENGAGE_KILL_SWITCH",
    "ENFORCE_DENY_ALL",
    "DESTROY_OWNER_CREDENTIALS",
    "PROVE_CONNECTOR_ZERO",
]
EMERGENCY_REQUIRED_ACTIONS = [
    "STOP_GUARDIAN",
    "DISABLE_EXECUTION_CONTROL",
    "ENGAGE_KILL_SWITCH",
    "ENFORCE_DENY_ALL",
    "PROVE_CONNECTOR_ZERO",
]
NORMAL_CLEANUP_TIMEOUT_MS = 240_000
EMERGENCY_CLEANUP_TIMEOUT_MS = 45_000
EXECUTOR_WAIT_SECONDS = 600
PUBLIC_WAIT_SECONDS = 960
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

PAPER_AUTHORITY_AND_MUTATION_UNITS = (
    "hepta-p1-watch-activation-reconcile.timer",
    "hepta-p1-watch-activation-reconcile.service",
    "hepta-p1-watch-activation.service",
    "hepta-shadow-watch-collector@alpha.timer",
    "hepta-shadow-watch-collector@alpha.service",
    "hepta-shadow-watch-export@alpha.service",
    "hepta-shadow-watch-custodian-reconcile@alpha.timer",
    "hepta-shadow-watch-custodian-reconcile@alpha.service",
    "hepta-shadow-watch-custodian@alpha.service",
    "hepta-tool-gateway@alpha.service",
    "hepta-tool-gateway@alpha.socket",
    "hepta-tool-session-supervisor@alpha.socket",
    "hepta-execution-simulator@alpha.service",
    "hepta-execution-simulator@alpha.socket",
    "hepta-execution-events-simulator@alpha.socket",
    "hepta-execution-ib-paper@alpha.service",
    "hepta-execution-ib-paper@alpha.socket",
    "hepta-execution-events-ib-paper@alpha.socket",
    "hepta-execution-ib-paper.service",
    "hepta-execution-ib-paper.socket",
    "hepta-execution-events-ib-paper.socket",
    "hepta-ib-paper-domain-preflight@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.socket",
    "hepta-local-ai-paper-agent.service",
)
OUTER_STOPPED_UNITS = (
    EXECUTOR_UNIT, CAPTURE_UNIT, FINALIZER_SOCKET_UNIT,
)

WAL_FIELDS = frozenset({
    "schema", "version", "campaign_id", "cycle_id", "transaction_id",
    "phase", "created_at_ms", "updated_at_ms", "capture_request_file_sha256",
    "normalization_receipt_file_sha256", "no_trade_receipt_file_sha256",
    "handoff_file_sha256", "execution_result_file_sha256",
    "inner_request_file_sha256", "inner_receipt_file_sha256",
    "inner_retry_attempted", "terminal_outcome", "body_sha256",
    "capture_owner_may_exist", "mutation_owner_may_exist",
    "outer_completion_file_sha256", "owner_purge_receipt_file_sha256",
    "owner_purge_receipt_body_sha256",
})
COMPLETION_FIELDS = frozenset({
    "schema", "version", "status", "completed_at_ms", "campaign_id",
    "domain_id", "cycle_id", "coordinator_transaction_id",
    "coordinator_wal_file_sha256", "capture_request_file_sha256",
    "normalization_receipt_file_sha256", "no_trade_receipt_file_sha256",
    "execution_handoff_file_sha256", "execution_handoff_body_sha256",
    "execution_result_file_sha256", "execution_result_body_sha256",
    "inner_cleanup_mode", "inner_cleanup_request_file_sha256",
    "inner_cleanup_request_body_sha256", "inner_cleanup_receipt_file_sha256",
    "inner_cleanup_receipt_body_sha256", "inner_cleanup_retry_attempted",
    "broker_deny_all", "kill_switches_engaged", "permit_absent",
    "identity_count", "identity_manifest_sha256",
    "authorized_connector_count", "runtime_session_count",
    "paper_authority_and_mutation_units",
    "paper_authority_and_mutation_units_sha256",
    "paper_authority_and_mutation_units_inactive", "peer_capture_unit_inactive",
    "peer_executor_unit_inactive", "finalizer_listener_unit_inactive",
    "finalizer_connection_units_inactive", "durable_owner_count",
    "durable_owner_status", "durable_owner_evidence_path",
    "durable_owner_evidence_file_sha256",
    "durable_owner_evidence_body_sha256", "root_coordinator_role",
    "mutation_credentials_destroyed", "credentials_destroyed_scope",
    "retained_root_recovery_bearer_count",
    "retained_root_recovery_bearer_path",
    "retained_root_recovery_bearer_sha256",
    "retained_root_recovery_bearer_mutation_authority",
    "root_coordinator_self_excluded", "peer_receipts_compatibility_only",
    "p2_success", "recovery_required", "paper_only", "live_authorized",
    "authority_granted", "body_sha256",
})
TERMINAL_PROOF_FIELDS = frozenset({
    "broker_deny_all", "kill_switches_engaged", "permit_absent",
    "identity_count", "identity_manifest_sha256",
    "authorized_connector_count", "runtime_session_count",
    "paper_authority_and_mutation_units",
    "paper_authority_and_mutation_units_inactive", "peer_capture_unit_inactive",
    "peer_executor_unit_inactive", "finalizer_listener_unit_inactive",
    "finalizer_connection_units_inactive", "durable_owner_count",
    "durable_owner_status", "durable_owner_evidence_path",
    "durable_owner_evidence_file_sha256",
    "durable_owner_evidence_body_sha256",
})
COORDINATOR_REQUEST_FIELDS = frozenset({
    "schema", "version", "status", "created_at_ms", "campaign_id",
    "domain_id", "cycle_id", "paper_only", "live_authorized",
    "authority_granted", "body_sha256",
})
OWNER_PURGE_RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "domain_id", "cycle_id",
    "owner_purge_intent_path", "owner_purge_intent_file_sha256",
    "owner_purge_intent_body_sha256", "outer_completion_path",
    "outer_completion_file_sha256", "outer_completion_body_sha256",
    "root_cleanup_receipt_path", "root_cleanup_receipt_file_sha256",
    "root_cleanup_receipt_body_sha256", "owner_retirement_receipt_path",
    "owner_retirement_receipt_file_sha256",
    "owner_retirement_receipt_body_sha256", "terminal_ack_receipt_sha256",
    "revoke_bearer_file_sha256", "owner_bearer_purged",
    "durable_owner_credential_count", "paper_only", "live_authorized",
    "authority_granted", "body_sha256",
})
PHASES = (
    "BEGIN", "OWNER_MAY_EXIST", "LAUNCH_COMPLETE", "EXECUTOR_EXITED",
    "INNER_JOINED", "TERMINAL_PROVEN", "COMPLETION_PUBLISHED",
    "OWNER_PURGE_CONFIRMED",
)


class CoordinatorError(RuntimeError):
    pass


@dataclass(frozen=True)
class LaunchOutcome:
    status: str
    capture_request_raw: bytes
    normalization_raw: Optional[bytes]
    no_trade_raw: Optional[bytes]
    handoff_raw: Optional[bytes]


class Control(Protocol):
    def now_ms(self) -> int: ...
    def load_wal(self, campaign: str, cycle: str) -> Optional[bytes]: ...
    def persist_wal(self, raw: bytes) -> None: ...
    def clear_wal(self) -> None: ...
    def ensure_prelaunch_deny_all(self, campaign: str, cycle: str) -> None: ...
    def enable_capture_surface(self, campaign: str, cycle: str) -> None: ...
    def launch(self, campaign: str, cycle: str) -> LaunchOutcome: ...
    def reopen_launch(
            self, campaign: str, cycle: str) -> Optional[LaunchOutcome]: ...
    def run_executor(self, handoff_raw: bytes) -> bytes: ...
    def reopen_execution_result(
            self, campaign: str, cycle: str) -> Optional[bytes]: ...
    def reopen_inner_request(
            self, campaign: str, cycle: str) -> Optional[bytes]: ...
    def reopen_inner_receipt(
            self, campaign: str, cycle: str) -> Optional[bytes]: ...
    def retry_same_inner_request(self, request_raw: bytes) -> bytes: ...
    def emergency_close(
            self, campaign: str, cycle: str) -> tuple[bytes, bytes]: ...
    def recover_pre_handoff_owners(self, campaign: str, cycle: str) -> None: ...
    def force_deny_all(self, campaign: str, cycle: str) -> None: ...
    def stop_outer_units(self) -> None: ...
    def prove_terminal(
            self, campaign: str, cycle: str,
            inner_receipt: Optional[dict[str, Any]],
            expected_owner_status: str) -> Mapping[str, Any]: ...
    def publish_completion(
            self, campaign: str, cycle: str, raw: bytes) -> None: ...
    def reopen_completion(
            self, campaign: str, cycle: str) -> Optional[bytes]: ...
    def confirm_owner_purge(
            self, campaign: str, cycle: str) -> bytes: ...


def canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise CoordinatorError("COORDINATOR_NON_CANONICAL_VALUE") from error


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical_sha(value: Any) -> str:
    return sha(canonical_json(value))


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def strict_json(raw: bytes, reason: str) -> dict[str, Any]:
    if not raw or len(raw) > MAX_BYTES or not raw.endswith(b"\n"):
        raise CoordinatorError(reason)
    try:
        value = json.loads(
            raw.decode("ascii"), object_pairs_hook=_pairs,
            parse_float=lambda _value: (_ for _ in ()).throw(
                ValueError("float forbidden")),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("constant forbidden")))
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise CoordinatorError(reason) from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise CoordinatorError(reason)
    return value


def _digest(value: Any, reason: str) -> str:
    if (
            not isinstance(value, str) or DIGEST.fullmatch(value) is None or
            value == "sha256:" + "0" * 64):
        raise CoordinatorError(reason)
    return value


def sealed(document: dict[str, Any], reason: str) -> str:
    claimed = _digest(document.get("body_sha256"), reason)
    body = dict(document)
    del body["body_sha256"]
    if canonical_sha(body) != claimed:
        raise CoordinatorError(reason)
    return claimed


def sealed_body(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "body_sha256": canonical_sha(body)}


def _exact(value: Any, fields: frozenset[str], reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise CoordinatorError(reason)
    return value


def _identifier(value: Any, reason: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise CoordinatorError(reason)
    return value


def _optional_digest(raw: Optional[bytes]) -> Optional[str]:
    return None if raw is None else sha(raw)


def _new_wal(campaign: str, cycle: str, now_ms: int) -> dict[str, Any]:
    body = {
        "schema": WAL_SCHEMA, "version": OUTER_VERSION,
        "campaign_id": campaign, "cycle_id": cycle,
        "transaction_id": hashlib.sha256(
            f"{campaign}\0{cycle}".encode("ascii")).hexdigest()[:40],
        "phase": "BEGIN", "created_at_ms": now_ms, "updated_at_ms": now_ms,
        "capture_request_file_sha256": None,
        "normalization_receipt_file_sha256": None,
        "no_trade_receipt_file_sha256": None,
        "handoff_file_sha256": None, "execution_result_file_sha256": None,
        "inner_request_file_sha256": None,
        "inner_receipt_file_sha256": None,
        "inner_retry_attempted": False, "terminal_outcome": None,
        "capture_owner_may_exist": False,
        "mutation_owner_may_exist": False,
        "outer_completion_file_sha256": None,
        "owner_purge_receipt_file_sha256": None,
        "owner_purge_receipt_body_sha256": None,
    }
    return sealed_body(body)


def _advance(wal: dict[str, Any], phase: str, now_ms: int,
             **updates: Any) -> dict[str, Any]:
    body = dict(wal)
    body.pop("body_sha256", None)
    body.update(updates)
    body["phase"] = phase
    body["updated_at_ms"] = max(now_ms, body["created_at_ms"])
    return sealed_body(body)


def _validate_handoff(raw: bytes, campaign: str, cycle: str) -> dict[str, Any]:
    value = strict_json(raw, "COORDINATOR_HANDOFF_INVALID")
    if (
            value.get("schema") != HANDOFF_SCHEMA or
            value.get("campaign_id") != campaign or value.get("cycle_id") != cycle or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            value.get("authority_granted") is not False):
        raise CoordinatorError("COORDINATOR_HANDOFF_INVALID")
    sealed(value, "COORDINATOR_HANDOFF_INVALID")
    return value


def _validate_result(raw: bytes, handoff: dict[str, Any]) -> dict[str, Any]:
    value = strict_json(raw, "COORDINATOR_EXECUTION_RESULT_INVALID")
    if (
            value.get("schema") != RESULT_SCHEMA or
            value.get("handoff_body_sha256") != handoff["body_sha256"] or
            value.get("authority_granted") is not False or
            value.get("status") not in {"SUCCESS", "RECOVERY_REQUIRED"}):
        raise CoordinatorError("COORDINATOR_EXECUTION_RESULT_INVALID")
    sealed(value, "COORDINATOR_EXECUTION_RESULT_INVALID")
    return value


def _validate_inner_request(
        raw: bytes, handoff: dict[str, Any]) -> dict[str, Any]:
    value = strict_json(raw, "COORDINATOR_INNER_REQUEST_INVALID")
    root_call = handoff["root_cleanup_call"]
    normal = value.get("schema") == NORMAL_REQUEST_SCHEMA
    expected_actions = NORMAL_REQUIRED_ACTIONS if normal else \
        EMERGENCY_REQUIRED_ACTIONS
    expected_timeout = NORMAL_CLEANUP_TIMEOUT_MS if normal else \
        EMERGENCY_CLEANUP_TIMEOUT_MS
    if (
            value.get("schema") not in {
                NORMAL_REQUEST_SCHEMA, EMERGENCY_REQUEST_SCHEMA} or
            value.get("version") != VERSION or
            value.get("cleanup_tool_call_id") != root_call["tool_call_id"] or
            value.get("cleanup_command_id") != root_call["command_id"] or
            value.get("tool_descriptor_sha256") !=
                root_call["tool_descriptor_sha256"] or
            value.get("campaign_id") != handoff["campaign_id"] or
            value.get("cycle_id") != handoff["cycle_id"] or
            value.get("required_actions") != expected_actions or
            not isinstance(value.get("issued_at_ms"), int) or
            isinstance(value.get("issued_at_ms"), bool) or
            not isinstance(value.get("expires_at_ms"), int) or
            isinstance(value.get("expires_at_ms"), bool) or
            value["expires_at_ms"] - value["issued_at_ms"] !=
                expected_timeout or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            value.get("authority_granted") is not False):
        raise CoordinatorError("COORDINATOR_INNER_REQUEST_INVALID")
    sealed(value, "COORDINATOR_INNER_REQUEST_INVALID")
    return value


def _validate_inner_receipt(
        raw: bytes, handoff: dict[str, Any], request: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    value = strict_json(raw, "COORDINATOR_INNER_RECEIPT_INVALID")
    normal = request["schema"] == NORMAL_REQUEST_SCHEMA
    expected_schema = NORMAL_ROOT_SCHEMA if normal else EMERGENCY_ROOT_SCHEMA
    expected_status = NORMAL_STATUS if normal else EMERGENCY_STATUS
    expected_version = OUTER_VERSION if normal else VERSION
    expected_actions = NORMAL_REQUIRED_ACTIONS if normal else \
        EMERGENCY_REQUIRED_ACTIONS
    if (
            value.get("schema") != expected_schema or
            value.get("version") != expected_version or
            value.get("status") != expected_status or
            value.get("cleanup_tool_call_id") !=
                request["cleanup_tool_call_id"] or
            value.get("cleanup_command_id") != request["cleanup_command_id"] or
            value.get("campaign_id") != handoff["campaign_id"] or
            value.get("cycle_id") != handoff["cycle_id"] or
            value.get("broker_deny_all") is not True or
            value.get("authorized_connector_count") != 0 or
            value.get("identity_count") != 0 or
            value.get("runtime_session_count") != 0 or
            value.get("completed_actions") != expected_actions or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            value.get("authority_granted") is not False):
        raise CoordinatorError("COORDINATOR_INNER_RECEIPT_INVALID")
    if normal:
        expected_owner_path = str(
            CONTROL_ROOT / handoff["campaign_id"] / handoff["cycle_id"] /
            "durable-owner-retirement-receipt.v4.json")
        if (
                value.get("durable_owner_count") != 0 or
                value.get("durable_owner_status") != "RETIRED" or
                value.get("mutation_credentials_destroyed") is not True or
                value.get("credentials_destroyed_scope") !=
                    "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY" or
                value.get("retained_root_recovery_bearer_count") != 1 or
                value.get("retained_root_recovery_bearer_path") !=
                    str(STATE_ROOT / "session-authority" /
                        "session.token.revoke-token") or
                value.get("retained_root_recovery_bearer_sha256") !=
                    handoff["session_owner_reference"][
                        "revoke_bearer_sha256"] or
                value.get(
                    "retained_root_recovery_bearer_mutation_authority") is not
                    False or
                value.get("durable_owner_retirement_receipt_path") !=
                    expected_owner_path):
            raise CoordinatorError("COORDINATOR_INNER_RECEIPT_INVALID")
        for field in (
                "durable_owner_retirement_receipt_file_sha256",
                "durable_owner_retirement_receipt_body_sha256"):
            _digest(value.get(field), "COORDINATOR_INNER_RECEIPT_INVALID")
        mode = "NORMAL"
    else:
        if (
                value.get("durable_owner_count") != 1 or
                value.get("durable_owner_status") != "RECOVERY_ONLY" or
                value.get("recovery_required") is not True or
                value.get("broker_flat_proven") is not False):
            raise CoordinatorError("COORDINATOR_INNER_RECEIPT_INVALID")
        mode = "EMERGENCY"
    sealed(value, "COORDINATOR_INNER_RECEIPT_INVALID")
    return mode, value


def _terminal_proof(
        value: Mapping[str, Any], *, expected_owner_status: str
) -> dict[str, Any]:
    proof = _exact(
        dict(value), TERMINAL_PROOF_FIELDS,
        "COORDINATOR_TERMINAL_PROOF_FIELDS_INVALID")
    for field in (
            "broker_deny_all", "kill_switches_engaged", "permit_absent",
            "paper_authority_and_mutation_units_inactive",
            "peer_capture_unit_inactive", "peer_executor_unit_inactive",
            "finalizer_listener_unit_inactive",
            "finalizer_connection_units_inactive"):
        if proof[field] is not True:
            raise CoordinatorError("COORDINATOR_TERMINAL_PROOF_INCOMPLETE")
    if (
            proof["identity_count"] != 0 or
            proof["authorized_connector_count"] != 0 or
            proof["runtime_session_count"] != 0 or
            proof["paper_authority_and_mutation_units"] !=
                list(PAPER_AUTHORITY_AND_MUTATION_UNITS) or
            proof["durable_owner_status"] != expected_owner_status):
        raise CoordinatorError("COORDINATOR_TERMINAL_PROOF_INCOMPLETE")
    _digest(
        proof["identity_manifest_sha256"],
        "COORDINATOR_TERMINAL_PROOF_INCOMPLETE")
    expected_count = 0 if expected_owner_status in {"RETIRED", "NONE"} else 1
    if proof["durable_owner_count"] != expected_count:
        raise CoordinatorError("COORDINATOR_TERMINAL_PROOF_INCOMPLETE")
    if expected_owner_status == "NONE":
        if any(proof[field] is not None for field in (
                "durable_owner_evidence_path",
                "durable_owner_evidence_file_sha256",
                "durable_owner_evidence_body_sha256")):
            raise CoordinatorError("COORDINATOR_TERMINAL_PROOF_INCOMPLETE")
    else:
        if not isinstance(proof["durable_owner_evidence_path"], str):
            raise CoordinatorError("COORDINATOR_TERMINAL_PROOF_INCOMPLETE")
        _digest(proof["durable_owner_evidence_file_sha256"],
                "COORDINATOR_TERMINAL_PROOF_INCOMPLETE")
        _digest(proof["durable_owner_evidence_body_sha256"],
                "COORDINATOR_TERMINAL_PROOF_INCOMPLETE")
    return proof


def _phase_index(phase: Any) -> int:
    if not isinstance(phase, str) or phase not in PHASES:
        raise CoordinatorError("COORDINATOR_WAL_PHASE_INVALID")
    return PHASES.index(phase)


def _require_wal_digest(
        wal: Mapping[str, Any], field: str, raw: Optional[bytes],
) -> None:
    if wal[field] != _optional_digest(raw):
        raise CoordinatorError("COORDINATOR_WAL_ARTIFACT_DRIFT")


def _validate_launch_shape(launch: LaunchOutcome) -> None:
    if launch.status == "NO_TRADE":
        if (
                launch.no_trade_raw is None or
                launch.normalization_raw is not None or
                launch.handoff_raw is not None):
            raise CoordinatorError("COORDINATOR_NO_TRADE_LAUNCH_INVALID")
    elif launch.status == "TRADE":
        if (
                launch.normalization_raw is None or
                launch.no_trade_raw is not None or
                launch.handoff_raw is None):
            raise CoordinatorError("COORDINATOR_TRADE_LAUNCH_INVALID")
    else:
        raise CoordinatorError("COORDINATOR_LAUNCH_OUTCOME_INVALID")


def _validate_resumed_launch(
        wal: Mapping[str, Any], launch: LaunchOutcome,
) -> None:
    _validate_launch_shape(launch)
    _require_wal_digest(
        wal, "capture_request_file_sha256", launch.capture_request_raw)
    _require_wal_digest(
        wal, "normalization_receipt_file_sha256", launch.normalization_raw)
    _require_wal_digest(
        wal, "no_trade_receipt_file_sha256", launch.no_trade_raw)
    _require_wal_digest(wal, "handoff_file_sha256", launch.handoff_raw)


def _validate_owner_purge(
        raw: bytes, *, campaign: str, cycle: str, completion_raw: bytes,
) -> dict[str, Any]:
    failure = "COORDINATOR_OWNER_PURGE_RECEIPT_INVALID"
    value = _exact(strict_json(raw, failure), OWNER_PURGE_RECEIPT_FIELDS, failure)
    sealed(value, failure)
    expected_path = CONTROL_ROOT / campaign / cycle / \
        "cycle-completion-receipt.v4.json"
    completion = strict_json(completion_raw, failure)
    sealed(completion, failure)
    if (
            value["schema"] !=
                "hepta.p1-paper-canary-outer-owner-purge-receipt.v1" or
            value["version"] != VERSION or
            value["status"] != "OWNER_BEARER_PURGED" or
            value["campaign_id"] != campaign or value["domain_id"] != DOMAIN or
            value["cycle_id"] != cycle or
            value["outer_completion_path"] != str(expected_path) or
            value["outer_completion_file_sha256"] != sha(completion_raw) or
            value["outer_completion_body_sha256"] != completion["body_sha256"] or
            value["owner_bearer_purged"] is not True or
            value["durable_owner_credential_count"] != 0 or
            value["paper_only"] is not True or
            value["live_authorized"] is not False or
            value["authority_granted"] is not False):
        raise CoordinatorError(failure)
    for field in (
            "owner_purge_intent_file_sha256",
            "owner_purge_intent_body_sha256",
            "root_cleanup_receipt_file_sha256",
            "root_cleanup_receipt_body_sha256",
            "owner_retirement_receipt_file_sha256",
            "owner_retirement_receipt_body_sha256",
            "terminal_ack_receipt_sha256", "revoke_bearer_file_sha256"):
        _digest(value[field], failure)
    return value


def coordinate(campaign: str, cycle: str, control: Control) -> bytes:
    campaign = _identifier(campaign, "COORDINATOR_CAMPAIGN_INVALID")
    cycle = _identifier(cycle, "COORDINATOR_CYCLE_INVALID")
    existing_wal_raw = control.load_wal(campaign, cycle)
    if existing_wal_raw is None:
        wal = _new_wal(campaign, cycle, control.now_ms())
    else:
        wal = _exact(
            strict_json(existing_wal_raw, "COORDINATOR_WAL_INVALID"),
            WAL_FIELDS, "COORDINATOR_WAL_INVALID")
        sealed(wal, "COORDINATOR_WAL_INVALID")
        if (
                wal["schema"] != WAL_SCHEMA or
                wal["version"] != OUTER_VERSION or
                wal["campaign_id"] != campaign or wal["cycle_id"] != cycle):
            raise CoordinatorError("COORDINATOR_WAL_CONFLICT")
    phase = _phase_index(wal["phase"])
    control.persist_wal(canonical_json(wal))

    launch: Optional[LaunchOutcome] = None
    result_raw: Optional[bytes] = None
    result: Optional[dict[str, Any]] = None
    handoff: Optional[dict[str, Any]] = None
    request_raw: Optional[bytes] = None
    request: Optional[dict[str, Any]] = None
    receipt_raw: Optional[bytes] = None
    receipt: Optional[dict[str, Any]] = None
    inner_mode = "NOT_APPLICABLE"
    retried = bool(wal["inner_retry_attempted"])
    terminal_outcome: Optional[str] = None
    expected_owner: Optional[str] = None
    owner_may_exist = bool(
        wal["capture_owner_may_exist"] or wal["mutation_owner_may_exist"])
    operation_error: Optional[BaseException] = None

    try:
        if phase == _phase_index("BEGIN"):
            control.ensure_prelaunch_deny_all(campaign, cycle)
            control.enable_capture_surface(campaign, cycle)
            wal = _advance(
                wal, "OWNER_MAY_EXIST", control.now_ms(),
                capture_owner_may_exist=True, mutation_owner_may_exist=True)
            control.persist_wal(canonical_json(wal))
            phase = _phase_index("OWNER_MAY_EXIST")
            owner_may_exist = True
            launch = control.launch(campaign, cycle)
        else:
            # A durable WAL is a resume instruction, never permission to invoke
            # the joiner again.  Reopen only the stable artifacts produced by
            # the original cycle ID.
            launch = control.reopen_launch(campaign, cycle)
            if launch is None:
                raise CoordinatorError("COORDINATOR_LAUNCH_RESUME_INCOMPLETE")
        _validate_launch_shape(launch)
        if phase == _phase_index("OWNER_MAY_EXIST"):
            wal = _advance(
                wal, "LAUNCH_COMPLETE", control.now_ms(),
                capture_request_file_sha256=sha(launch.capture_request_raw),
                normalization_receipt_file_sha256=
                    _optional_digest(launch.normalization_raw),
                no_trade_receipt_file_sha256=
                    _optional_digest(launch.no_trade_raw),
                handoff_file_sha256=_optional_digest(launch.handoff_raw),
                capture_owner_may_exist=False,
                mutation_owner_may_exist=(launch.status == "TRADE"))
            control.persist_wal(canonical_json(wal))
            phase = _phase_index("LAUNCH_COMPLETE")
        else:
            _validate_resumed_launch(wal, launch)

        if launch.status == "NO_TRADE":
            terminal_outcome, expected_owner = "NO_TRADE", "NONE"
            owner_may_exist = False
        else:
            assert launch.handoff_raw is not None
            handoff = _validate_handoff(launch.handoff_raw, campaign, cycle)
            owner_may_exist = bool(wal["mutation_owner_may_exist"])
            if phase <= _phase_index("LAUNCH_COMPLETE"):
                result_raw = control.run_executor(launch.handoff_raw)
                result = _validate_result(result_raw, handoff)
                wal = _advance(
                    wal, "EXECUTOR_EXITED", control.now_ms(),
                    execution_result_file_sha256=sha(result_raw))
                control.persist_wal(canonical_json(wal))
                phase = _phase_index("EXECUTOR_EXITED")
            else:
                result_raw = control.reopen_execution_result(campaign, cycle)
                if result_raw is None:
                    raise CoordinatorError(
                        "COORDINATOR_EXECUTION_RESULT_RESUME_MISSING")
                _require_wal_digest(
                    wal, "execution_result_file_sha256", result_raw)
                result = _validate_result(result_raw, handoff)

            request_raw = control.reopen_inner_request(campaign, cycle)
            if request_raw is None:
                raise CoordinatorError("COORDINATOR_INNER_REQUEST_MISSING")
            request = _validate_inner_request(request_raw, handoff)
            receipt_raw = control.reopen_inner_receipt(campaign, cycle)
            if phase <= _phase_index("EXECUTOR_EXITED") and receipt_raw is None:
                retried = True
                try:
                    returned = control.retry_same_inner_request(request_raw)
                except Exception:
                    returned = b""
                receipt_raw = returned or control.reopen_inner_receipt(
                    campaign, cycle)
            if receipt_raw is None:
                raise CoordinatorError("COORDINATOR_INNER_RECEIPT_MISSING")
            inner_mode, receipt = _validate_inner_receipt(
                receipt_raw, handoff, request)
            if result["status"] == "SUCCESS" and inner_mode == "NORMAL":
                terminal_outcome, expected_owner = "P2_SUCCESS", "RETIRED"
            else:
                terminal_outcome = "RECOVERY_REQUIRED"
                expected_owner = "RECOVERY_ONLY" if inner_mode == \
                    "EMERGENCY" else "RETIRED"
            owner_may_exist = False
            if phase <= _phase_index("EXECUTOR_EXITED"):
                wal = _advance(
                    wal, "INNER_JOINED", control.now_ms(),
                    inner_request_file_sha256=sha(request_raw),
                    inner_receipt_file_sha256=sha(receipt_raw),
                    inner_retry_attempted=retried,
                    terminal_outcome=terminal_outcome,
                    capture_owner_may_exist=False,
                    mutation_owner_may_exist=False)
                control.persist_wal(canonical_json(wal))
                phase = _phase_index("INNER_JOINED")
            else:
                _require_wal_digest(
                    wal, "inner_request_file_sha256", request_raw)
                _require_wal_digest(
                    wal, "inner_receipt_file_sha256", receipt_raw)
        if phase >= _phase_index("INNER_JOINED") and \
                wal["terminal_outcome"] != terminal_outcome:
            raise CoordinatorError("COORDINATOR_WAL_OUTCOME_DRIFT")
    except Exception as error:
        operation_error = error
        if owner_may_exist:
            if handoff is None:
                try:
                    control.recover_pre_handoff_owners(campaign, cycle)
                    owner_may_exist = False
                except BaseException as recovery_error:
                    operation_error = CoordinatorError(
                        "COORDINATOR_ORPHAN_RECOVERY_FAILED")
                    operation_error.__cause__ = recovery_error
            else:
                try:
                    candidate_request_raw, candidate_receipt_raw = \
                        control.emergency_close(campaign, cycle)
                    candidate_request = _validate_inner_request(
                        candidate_request_raw, handoff)
                    candidate_mode, candidate_receipt = _validate_inner_receipt(
                        candidate_receipt_raw, handoff, candidate_request)
                    if candidate_mode != "EMERGENCY":
                        raise CoordinatorError(
                            "COORDINATOR_EMERGENCY_RECEIPT_MODE_INVALID")
                    request_raw, request = candidate_request_raw, candidate_request
                    receipt_raw, receipt = candidate_receipt_raw, candidate_receipt
                    inner_mode = "EMERGENCY"
                    terminal_outcome, expected_owner = \
                        "RECOVERY_REQUIRED", "RECOVERY_ONLY"
                    wal = _advance(
                        wal, "INNER_JOINED", control.now_ms(),
                        inner_request_file_sha256=sha(request_raw),
                        inner_receipt_file_sha256=sha(receipt_raw),
                        inner_retry_attempted=False,
                        terminal_outcome=terminal_outcome,
                        capture_owner_may_exist=False,
                        mutation_owner_may_exist=False)
                    control.persist_wal(canonical_json(wal))
                    phase = _phase_index("INNER_JOINED")
                    operation_error = None
                except BaseException as emergency_error:
                    operation_error = CoordinatorError(
                        "COORDINATOR_EMERGENCY_CLOSE_FAILED")
                    operation_error.__cause__ = emergency_error
    finally:
        safety_errors: list[BaseException] = []
        try:
            control.force_deny_all(campaign, cycle)
        except BaseException as error:
            safety_errors.append(error)
        try:
            control.stop_outer_units()
        except BaseException as error:
            safety_errors.append(error)
        if safety_errors and operation_error is None:
            operation_error = CoordinatorError(
                "COORDINATOR_MONOTONIC_FAIL_CLOSE_FAILED")
            operation_error.__cause__ = safety_errors[0]

    if operation_error is not None:
        if isinstance(operation_error, CoordinatorError):
            raise operation_error
        raise CoordinatorError("COORDINATOR_TRANSACTION_FAILED") from \
            operation_error
    if launch is None or terminal_outcome is None or expected_owner is None:
        raise CoordinatorError("COORDINATOR_TRANSACTION_INCOMPLETE")

    completion_raw: Optional[bytes] = None
    if phase < _phase_index("COMPLETION_PUBLISHED"):
        proof = _terminal_proof(
            control.prove_terminal(
                campaign, cycle, receipt, expected_owner),
            expected_owner_status=expected_owner)
        if phase < _phase_index("TERMINAL_PROVEN"):
            wal = _advance(
                wal, "TERMINAL_PROVEN", control.now_ms(),
                terminal_outcome=terminal_outcome)
            control.persist_wal(canonical_json(wal))
            phase = _phase_index("TERMINAL_PROVEN")
        owner_reference = handoff["session_owner_reference"] if handoff else None
        if expected_owner == "RETIRED":
            if receipt is None or owner_reference is None:
                raise CoordinatorError("COORDINATOR_RETAINED_BEARER_INVALID")
            mutation_credentials_destroyed = True
            credentials_destroyed_scope = \
                "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY"
            retained_bearer_count = 1
            retained_bearer_path = receipt[
                "retained_root_recovery_bearer_path"]
            retained_bearer_sha = receipt[
                "retained_root_recovery_bearer_sha256"]
        elif expected_owner == "RECOVERY_ONLY":
            if owner_reference is None:
                raise CoordinatorError("COORDINATOR_RETAINED_BEARER_INVALID")
            mutation_credentials_destroyed = False
            credentials_destroyed_scope = "RECOVERY_ONLY_OWNER_RETAINED"
            retained_bearer_count = 1
            retained_bearer_path = owner_reference["revoke_bearer_path"]
            retained_bearer_sha = owner_reference["revoke_bearer_sha256"]
        else:
            mutation_credentials_destroyed = False
            credentials_destroyed_scope = "NOT_APPLICABLE"
            retained_bearer_count = 0
            retained_bearer_path = None
            retained_bearer_sha = None
        body = {
            "schema": COMPLETION_SCHEMA, "version": OUTER_VERSION,
            "status": terminal_outcome,
            # Bound to the immutable TERMINAL_PROVEN WAL so a crash after the
            # no-replace publish reconstructs byte-identical completion bytes.
            "completed_at_ms": wal["updated_at_ms"],
            "campaign_id": campaign, "domain_id": DOMAIN, "cycle_id": cycle,
            "coordinator_transaction_id": wal["transaction_id"],
            "coordinator_wal_file_sha256": sha(canonical_json(wal)),
            "capture_request_file_sha256": sha(launch.capture_request_raw),
            "normalization_receipt_file_sha256":
                _optional_digest(launch.normalization_raw),
            "no_trade_receipt_file_sha256":
                _optional_digest(launch.no_trade_raw),
            "execution_handoff_file_sha256":
                _optional_digest(launch.handoff_raw),
            "execution_handoff_body_sha256":
                handoff.get("body_sha256") if handoff else None,
            "execution_result_file_sha256": _optional_digest(result_raw),
            "execution_result_body_sha256":
                result.get("body_sha256") if result else None,
            "inner_cleanup_mode": inner_mode,
            "inner_cleanup_request_file_sha256": _optional_digest(request_raw),
            "inner_cleanup_request_body_sha256":
                request.get("body_sha256") if request else None,
            "inner_cleanup_receipt_file_sha256": _optional_digest(receipt_raw),
            "inner_cleanup_receipt_body_sha256":
                receipt.get("body_sha256") if receipt else None,
            "inner_cleanup_retry_attempted": retried,
            "mutation_credentials_destroyed":
                mutation_credentials_destroyed,
            "credentials_destroyed_scope": credentials_destroyed_scope,
            "retained_root_recovery_bearer_count": retained_bearer_count,
            "retained_root_recovery_bearer_path": retained_bearer_path,
            "retained_root_recovery_bearer_sha256": retained_bearer_sha,
            "retained_root_recovery_bearer_mutation_authority": False,
            **proof,
            "paper_authority_and_mutation_units_sha256": canonical_sha(
                proof["paper_authority_and_mutation_units"]),
            "root_coordinator_role": COORDINATOR_ROLE,
            "root_coordinator_self_excluded": True,
            "peer_receipts_compatibility_only": True,
            "p2_success": terminal_outcome == "P2_SUCCESS",
            "recovery_required": terminal_outcome == "RECOVERY_REQUIRED",
            "paper_only": True, "live_authorized": False,
            "authority_granted": False,
        }
        completion = sealed_body(body)
        _exact(
            completion, COMPLETION_FIELDS,
            "COORDINATOR_COMPLETION_FIELDS_INVALID")
        completion_raw = canonical_json(completion)
        control.publish_completion(campaign, cycle, completion_raw)
        wal = _advance(
            wal, "COMPLETION_PUBLISHED", control.now_ms(),
            outer_completion_file_sha256=sha(completion_raw))
        control.persist_wal(canonical_json(wal))
        phase = _phase_index("COMPLETION_PUBLISHED")
    else:
        completion_raw = control.reopen_completion(campaign, cycle)
        if completion_raw is None:
            raise CoordinatorError("COORDINATOR_COMPLETION_RESUME_MISSING")
        completion = _exact(
            strict_json(completion_raw, "COORDINATOR_COMPLETION_INVALID"),
            COMPLETION_FIELDS, "COORDINATOR_COMPLETION_INVALID")
        sealed(completion, "COORDINATOR_COMPLETION_INVALID")
        if (
                completion["schema"] != COMPLETION_SCHEMA or
                completion["version"] != OUTER_VERSION or
                completion["campaign_id"] != campaign or
                completion["cycle_id"] != cycle or
                completion["status"] != terminal_outcome or
                completion["coordinator_transaction_id"] !=
                    wal["transaction_id"] or
                completion["retained_root_recovery_bearer_count"] !=
                    (0 if expected_owner == "NONE" else 1) or
                completion[
                    "retained_root_recovery_bearer_mutation_authority"] is not
                    False or
                completion["authority_granted"] is not False):
            raise CoordinatorError("COORDINATOR_COMPLETION_INVALID")
        _require_wal_digest(
            wal, "outer_completion_file_sha256", completion_raw)

    assert completion_raw is not None
    if expected_owner == "RETIRED":
        purge_raw = control.confirm_owner_purge(campaign, cycle)
        purge = _validate_owner_purge(
            purge_raw, campaign=campaign, cycle=cycle,
            completion_raw=completion_raw)
        if phase < _phase_index("OWNER_PURGE_CONFIRMED"):
            wal = _advance(
                wal, "OWNER_PURGE_CONFIRMED", control.now_ms(),
                owner_purge_receipt_file_sha256=sha(purge_raw),
                owner_purge_receipt_body_sha256=purge["body_sha256"])
            control.persist_wal(canonical_json(wal))
        else:
            _require_wal_digest(
                wal, "owner_purge_receipt_file_sha256", purge_raw)
            if wal["owner_purge_receipt_body_sha256"] != purge["body_sha256"]:
                raise CoordinatorError("COORDINATOR_OWNER_PURGE_RECEIPT_DRIFT")
    elif phase < _phase_index("OWNER_PURGE_CONFIRMED"):
        wal = _advance(
            wal, "OWNER_PURGE_CONFIRMED", control.now_ms(),
            owner_purge_receipt_file_sha256=None,
            owner_purge_receipt_body_sha256=None)
        control.persist_wal(canonical_json(wal))
    control.clear_wal()
    return completion_raw


class _Lock:
    def __enter__(self) -> "_Lock":
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.descriptor = os.open(
            LOCK_PATH, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.fchmod(self.descriptor, 0o600)
        os.fchown(self.descriptor, 0, 0)
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(self.descriptor)
            raise CoordinatorError("COORDINATOR_BUSY") from error
        return self

    def __exit__(self, *_args: Any) -> None:
        os.close(self.descriptor)


class ProductionControl:
    """Fixed-unit production adapter.

    Launch graph derivation is delegated to the credential-loaded installed
    launch joiner.  No caller-provided path, unit, UID, socket, or tool name is
    accepted here.
    """

    def __init__(self) -> None:
        self.campaign = ""
        self.cycle = ""

    @staticmethod
    def _credential_path(name: str) -> Path:
        directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
        if not directory or not Path(directory).is_absolute():
            raise CoordinatorError("COORDINATOR_CREDENTIAL_DIRECTORY_MISSING")
        return Path(directory) / name

    @classmethod
    def _credential_script(cls, name: str, installed: Path) -> Path:
        credential = cls._credential_path(name)
        credential_raw = _root_read(credential, modes={0o400})
        installed_raw = _root_read(installed, modes={0o755})
        if credential_raw != installed_raw:
            raise CoordinatorError("COORDINATOR_CREDENTIAL_IMAGE_MISMATCH")
        return credential

    @staticmethod
    def _service_environment() -> dict[str, str]:
        directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
        if not directory:
            raise CoordinatorError("COORDINATOR_CREDENTIAL_DIRECTORY_MISSING")
        return {
            "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/usr/sbin",
            "CREDENTIALS_DIRECTORY": directory,
        }

    @classmethod
    def _run_credential_script(
            cls, credential_name: str, installed: Path, *arguments: str,
            timeout: int) -> subprocess.CompletedProcess[bytes]:
        script = cls._credential_script(credential_name, installed)
        try:
            return subprocess.run(
                ["/usr/bin/python3.12", "-I", "-S", str(script), *arguments],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd="/", env=cls._service_environment(),
                close_fds=True, check=False, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CoordinatorError("COORDINATOR_CREDENTIAL_HELPER_FAILED") \
                from error

    @classmethod
    def _load_credential_module(
            cls, credential_name: str, installed: Path,
            module_name: str) -> ModuleType:
        script = cls._credential_script(credential_name, installed)
        raw = _root_read(script, modes={0o400})
        module = ModuleType(module_name)
        module.__file__ = str(script)
        sys.modules[module_name] = module
        try:
            exec(compile(raw, str(script), "exec"), module.__dict__)
        except Exception as error:
            raise CoordinatorError("COORDINATOR_CREDENTIAL_IMAGE_INVALID") \
                from error
        return module

    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def load_wal(self, campaign: str, cycle: str) -> Optional[bytes]:
        if any(path.exists() or path.is_symlink()
               for path in LEGACY_WAL_PATHS):
            raise CoordinatorError("COORDINATOR_LEGACY_WAL_PRESENT")
        if not (WAL_PATH.exists() or WAL_PATH.is_symlink()):
            return None
        return _root_read(WAL_PATH, modes={0o600})

    def persist_wal(self, raw: bytes) -> None:
        WAL_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = WAL_PATH.with_name(
            f".{WAL_PATH.name}.{os.getpid()}.tmp")
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        _root_publish(temporary, raw)
        os.replace(temporary, WAL_PATH)
        descriptor = os.open(WAL_PATH.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def clear_wal(self) -> None:
        try:
            WAL_PATH.unlink()
        except FileNotFoundError:
            return
        descriptor = os.open(WAL_PATH.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _systemctl(*arguments: str, timeout: int = 60) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["/usr/bin/systemctl", *arguments], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/",
                env={"LC_ALL": "C"}, close_fds=True, check=False,
                timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CoordinatorError("COORDINATOR_SYSTEMCTL_FAILED") from error

    @classmethod
    def _unit_properties(cls, unit: str) -> dict[str, str]:
        completed = cls._systemctl(
            "show", unit, "-p", "LoadState", "-p", "ActiveState",
            "-p", "SubState", "-p", "Result", "-p", "ExecMainStatus",
            "-p", "Job")
        values: dict[str, str] = {}
        try:
            lines = completed.stdout.decode("ascii", errors="strict").splitlines()
        except UnicodeError as error:
            raise CoordinatorError("COORDINATOR_UNIT_STATE_INVALID") from error
        for line in lines:
            key, separator, value = line.partition("=")
            if not separator or key in values:
                raise CoordinatorError("COORDINATOR_UNIT_STATE_INVALID")
            values[key] = value
        if completed.returncode != 0 or set(values) != {
                "LoadState", "ActiveState", "SubState", "Result",
                "ExecMainStatus", "Job"}:
            raise CoordinatorError("COORDINATOR_UNIT_STATE_INVALID")
        return values

    @classmethod
    def _unit_inactive(cls, unit: str) -> bool:
        values = cls._unit_properties(unit)
        return (
            values["LoadState"] in {"loaded", "not-found"} and
            values["ActiveState"] == "inactive" and values["Job"] == "")

    @classmethod
    def _finalizer_connections(cls) -> list[str]:
        completed = cls._systemctl(
            "list-units", FINALIZER_CONNECTION_GLOB, "--all", "--plain",
            "--no-legend", "--no-pager")
        if completed.returncode != 0:
            raise CoordinatorError("COORDINATOR_FINALIZER_ENUMERATION_FAILED")
        try:
            lines = completed.stdout.decode("ascii", errors="strict").splitlines()
        except UnicodeError as error:
            raise CoordinatorError(
                "COORDINATOR_FINALIZER_ENUMERATION_FAILED") from error
        units: list[str] = []
        for line in lines:
            fields = line.split()
            if not fields:
                continue
            unit = fields[0]
            if not re.fullmatch(
                    r"hepta-p1-paper-canary-finalizer@[A-Za-z0-9_.:-]+\.service",
                    unit):
                raise CoordinatorError(
                    "COORDINATOR_FINALIZER_ENUMERATION_FAILED")
            units.append(unit)
        return sorted(set(units))

    @classmethod
    def _wait_inactive(cls, units: tuple[str, ...] | list[str],
                       *, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if all(cls._unit_inactive(unit) for unit in units):
                return
            if time.monotonic() >= deadline:
                raise CoordinatorError("COORDINATOR_UNIT_STOP_TIMEOUT")
            time.sleep(0.1)

    def ensure_prelaunch_deny_all(self, campaign: str, cycle: str) -> None:
        self.campaign, self.cycle = campaign, cycle
        if not all(self._unit_inactive(unit) for unit in OUTER_STOPPED_UNITS[:2]):
            raise CoordinatorError("COORDINATOR_PRELAUNCH_UNIT_ACTIVE")
        for path in (ACTIVE_HANDOFF, ACTIVE_CAPTURE_REQUEST):
            if path.exists() or path.is_symlink():
                raise CoordinatorError("COORDINATOR_ACTIVE_CREDENTIAL_PRESENT")
        # The installed local-control verifier is the authoritative prelaunch
        # DENY_ALL proof.  The coordinator refuses to infer it from unit state.
        completed = self._run_credential_script(
            TERMINAL_PROVER_CREDENTIAL, INSTALLED_TERMINAL_PROVER,
            "--campaign-id", campaign, "--cycle-id", cycle,
            "--phase", "prelaunch", timeout=60)
        if completed.returncode != 0:
            raise CoordinatorError("COORDINATOR_PRELAUNCH_DENY_ALL_UNPROVEN")

    def enable_capture_surface(self, campaign: str, cycle: str) -> None:
        completed = self._run_credential_script(
            TERMINAL_PROVER_CREDENTIAL, INSTALLED_TERMINAL_PROVER,
            "--campaign-id", campaign, "--cycle-id", cycle,
            "--phase", "enable-paper", timeout=90)
        if completed.returncode != 0:
            raise CoordinatorError("COORDINATOR_GUARDIAN_ENABLE_FAILED")
        value = strict_json(
            completed.stdout, "COORDINATOR_GUARDIAN_ENABLE_RESULT_INVALID")
        if (
                value.get("schema") !=
                    "hepta.p1-paper-canary-paper-enable-result.v1" or
                value.get("status") != "EXTERNAL_PAPER_ENABLED" or
                value.get("campaign_id") != campaign or
                value.get("cycle_id") != cycle or
                value.get("authority_granted") is not False):
            raise CoordinatorError("COORDINATOR_GUARDIAN_ENABLE_RESULT_INVALID")

    def launch(self, campaign: str, cycle: str) -> LaunchOutcome:
        # A root join transaction publishes ACTIVE_CAPTURE_REQUEST and starts
        # the fixed credential-pinned peer capture unit.  The helper returns a
        # sealed outcome document and cannot start the executor.
        completed = self._run_credential_script(
            LAUNCH_JOINER_CREDENTIAL, INSTALLED_LAUNCH_JOINER,
            "--campaign-id", campaign, "--cycle-id", cycle, timeout=120)
        if completed.returncode not in {0, 4}:
            raise CoordinatorError("COORDINATOR_LAUNCH_TRANSACTION_FAILED")
        outcome = strict_json(completed.stdout, "COORDINATOR_LAUNCH_RESULT_INVALID")
        directory = ARTIFACT_ROOT / campaign / cycle
        control = CONTROL_ROOT / campaign / cycle
        capture_request = _root_read(
            control / "capture-request.v1.json", modes={0o600})
        no_trade_path = control / "no-trade-launch-receipt.v1.json"
        normalization_path = control / "normalized-launch-intent-receipt.v1.json"
        handoff_path = control / "execution-handoff.v1.json"
        no_trade = _root_read(no_trade_path, modes={0o600}) \
            if no_trade_path.exists() else None
        normalization = _root_read(normalization_path, modes={0o600}) \
            if normalization_path.exists() else None
        handoff = _root_read(handoff_path, modes={0o600}) \
            if handoff_path.exists() else None
        status = outcome.get("status")
        if status not in {"TRADE", "NO_TRADE"}:
            raise CoordinatorError("COORDINATOR_LAUNCH_RESULT_INVALID")
        return LaunchOutcome(
            status, capture_request, normalization, no_trade, handoff)

    def reopen_launch(
            self, campaign: str, cycle: str) -> Optional[LaunchOutcome]:
        control = CONTROL_ROOT / campaign / cycle
        capture_path = control / "capture-request.v1.json"
        retirement_path = control / "capture-session-retirement-receipt.v1.json"
        if not (capture_path.exists() or capture_path.is_symlink()):
            return None
        if not (retirement_path.exists() or retirement_path.is_symlink()):
            return None
        capture = _root_read(capture_path, modes={0o600})
        retirement_raw = _root_read(retirement_path, modes={0o600})
        retirement = strict_json(
            retirement_raw, "COORDINATOR_CAPTURE_RETIREMENT_INVALID")
        sealed(retirement, "COORDINATOR_CAPTURE_RETIREMENT_INVALID")
        if (
                retirement.get("schema") !=
                    "hepta.p1-paper-canary-capture-owner-retirement.v1" or
                retirement.get("status") != "RETIRED" or
                retirement.get("campaign_id") != campaign or
                retirement.get("cycle_id") != cycle or
                retirement.get("authority_granted") is not False):
            raise CoordinatorError("COORDINATOR_CAPTURE_RETIREMENT_INVALID")
        no_trade_path = control / "no-trade-launch-receipt.v1.json"
        normalization_path = control / "normalized-launch-intent-receipt.v1.json"
        handoff_path = control / "execution-handoff.v1.json"
        no_trade = _root_read(no_trade_path, modes={0o600}) \
            if no_trade_path.exists() or no_trade_path.is_symlink() else None
        normalization = _root_read(normalization_path, modes={0o600}) \
            if normalization_path.exists() or normalization_path.is_symlink() \
            else None
        handoff = _root_read(handoff_path, modes={0o600}) \
            if handoff_path.exists() or handoff_path.is_symlink() else None
        if no_trade is not None and normalization is None and handoff is None:
            return LaunchOutcome("NO_TRADE", capture, None, no_trade, None)
        if no_trade is None and normalization is not None and handoff is not None:
            return LaunchOutcome("TRADE", capture, normalization, None, handoff)
        return None

    def run_executor(self, handoff_raw: bytes) -> bytes:
        path = ARTIFACT_ROOT / self.campaign / self.cycle / "execution-result-v1.json"
        if path.exists() or path.is_symlink():
            return _peer_read(path)
        _root_publish_active(ACTIVE_HANDOFF, handoff_raw)
        state = self._unit_properties(EXECUTOR_UNIT)
        if state["ActiveState"] == "inactive" and state["Job"] == "":
            started = self._systemctl("start", EXECUTOR_UNIT, timeout=10)
            if started.returncode != 0:
                raise CoordinatorError("COORDINATOR_EXECUTOR_START_FAILED")
        elif state["ActiveState"] not in {"activating", "active"}:
            raise CoordinatorError("COORDINATOR_EXECUTOR_STATE_INVALID")
        deadline = time.monotonic() + EXECUTOR_WAIT_SECONDS
        terminal: dict[str, str] | None = None
        while time.monotonic() < deadline:
            state = self._unit_properties(EXECUTOR_UNIT)
            if state["ActiveState"] in {"inactive", "failed"} and \
                    state["Job"] == "":
                terminal = state
                break
            time.sleep(0.1)
        if terminal is None:
            self._systemctl("stop", EXECUTOR_UNIT, timeout=15)
            raise CoordinatorError("COORDINATOR_EXECUTOR_TIMEOUT")
        if (
                terminal["Result"] not in {"success", "exit-code"} or
                terminal["ExecMainStatus"] not in {"0", "1"}):
            raise CoordinatorError("COORDINATOR_EXECUTOR_EXIT_INVALID")
        if not (path.exists() or path.is_symlink()):
            raise CoordinatorError("COORDINATOR_EXECUTION_RESULT_MISSING")
        return _peer_read(path)

    def reopen_execution_result(
            self, campaign: str, cycle: str) -> Optional[bytes]:
        path = ARTIFACT_ROOT / campaign / cycle / "execution-result-v1.json"
        if not (path.exists() or path.is_symlink()):
            return None
        return _peer_read(path)

    def reopen_inner_request(self, campaign: str, cycle: str) -> Optional[bytes]:
        directory = ARTIFACT_ROOT / campaign / cycle
        for name in (
                "root-cleanup-request.v1.json",
                "root-emergency-cleanup-request.v1.json"):
            path = directory / name
            if path.exists() or path.is_symlink():
                return _peer_read(path)
        return None

    def reopen_inner_receipt(self, campaign: str, cycle: str) -> Optional[bytes]:
        directory = CONTROL_ROOT / campaign / cycle
        legacy = (
            directory / "root-cleanup-receipt.v1.json",
            directory / "root-cleanup-receipt.v2.json",
            directory / "root-cleanup-receipt.v3.json",
        )
        if any(path.exists() or path.is_symlink() for path in legacy):
            raise CoordinatorError("COORDINATOR_LEGACY_INNER_RECEIPT_PRESENT")
        for name in (
                "root-cleanup-receipt.v4.json",
                "root-emergency-cleanup-receipt.v1.json"):
            path = directory / name
            if path.exists() or path.is_symlink():
                return _root_read(path, modes={0o600})
        return None

    def retry_same_inner_request(self, request_raw: bytes) -> bytes:
        request = strict_json(
            request_raw, "COORDINATOR_INNER_RETRY_REQUEST_INVALID")
        timeout_seconds = (
            NORMAL_CLEANUP_TIMEOUT_MS / 1000 + 5 if
            request.get("schema") == NORMAL_REQUEST_SCHEMA else
            EMERGENCY_CLEANUP_TIMEOUT_MS / 1000)
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        response = bytearray()
        try:
            channel.settimeout(timeout_seconds)
            channel.connect(str(ROOT_FINALIZER_SOCKET))
            channel.sendall(request_raw)
            channel.shutdown(socket.SHUT_WR)
            while len(response) <= MAX_BYTES:
                chunk = channel.recv(min(65536, MAX_BYTES + 1 - len(response)))
                if not chunk:
                    break
                response.extend(chunk)
        except OSError as error:
            raise CoordinatorError("COORDINATOR_INNER_RETRY_UNCERTAIN") from error
        finally:
            channel.close()
        if len(response) > MAX_BYTES:
            raise CoordinatorError("COORDINATOR_INNER_RETRY_INVALID")
        return bytes(response)

    def emergency_close(
            self, campaign: str, cycle: str) -> tuple[bytes, bytes]:
        state = self._unit_properties(FINALIZER_SOCKET_UNIT)
        if state["ActiveState"] == "inactive" and state["Job"] == "":
            started = self._systemctl(
                "start", FINALIZER_SOCKET_UNIT, timeout=15)
            if started.returncode != 0:
                raise CoordinatorError(
                    "COORDINATOR_EMERGENCY_FINALIZER_START_FAILED")
        elif state["ActiveState"] not in {"activating", "active"}:
            raise CoordinatorError(
                "COORDINATOR_EMERGENCY_FINALIZER_STATE_INVALID")
        module = self._load_credential_module(
            CRASH_CLOSER_CREDENTIAL, INSTALLED_CRASH_CLOSER,
            "_hepta_p1_canary_coordinator_crash_closer")
        try:
            result = module.close(campaign, cycle)
        except Exception as error:
            raise CoordinatorError("COORDINATOR_EMERGENCY_CLOSER_FAILED") \
                from error
        if (
                not isinstance(result, dict) or
                result.get("status") != "RECOVERY_ONLY_DENY_ALL" or
                result.get("campaign_id") != campaign or
                result.get("cycle_id") != cycle or
                result.get("authority_granted") is not False):
            raise CoordinatorError("COORDINATOR_EMERGENCY_CLOSER_INVALID")
        request = _peer_read(
            ARTIFACT_ROOT / campaign / cycle /
            "root-emergency-cleanup-request.v1.json")
        receipt = _root_read(
            CONTROL_ROOT / campaign / cycle /
            "root-emergency-cleanup-receipt.v1.json", modes={0o600})
        return request, receipt

    def recover_pre_handoff_owners(self, campaign: str, cycle: str) -> None:
        module = self._load_credential_module(
            CRASH_CLOSER_CREDENTIAL, INSTALLED_CRASH_CLOSER,
            "_hepta_p1_canary_coordinator_orphan_closer")
        reconcile = getattr(module, "reconcile_orphan_owners", None)
        if not callable(reconcile):
            raise CoordinatorError("COORDINATOR_ORPHAN_RECOVERY_UNAVAILABLE")
        try:
            result = reconcile(campaign, cycle)
        except Exception as error:
            raise CoordinatorError("COORDINATOR_ORPHAN_RECOVERY_FAILED") from error
        if (not isinstance(result, dict) or
                result.get("status") not in {"NO_OWNER", "OWNERS_RETIRED"} or
                result.get("authority_granted") is not False):
            raise CoordinatorError("COORDINATOR_ORPHAN_RECOVERY_INVALID")

    def force_deny_all(self, campaign: str, cycle: str) -> None:
        completed = self._run_credential_script(
            TERMINAL_PROVER_CREDENTIAL, INSTALLED_TERMINAL_PROVER,
            "--campaign-id", campaign, "--cycle-id", cycle,
            "--phase", "force-deny-all", timeout=90)
        if completed.returncode != 0:
            raise CoordinatorError("COORDINATOR_FORCE_DENY_ALL_FAILED")

    def stop_outer_units(self) -> None:
        for unit in (EXECUTOR_UNIT, CAPTURE_UNIT, FINALIZER_SOCKET_UNIT):
            completed = self._systemctl("stop", unit, timeout=45)
            if completed.returncode != 0:
                raise CoordinatorError("COORDINATOR_UNIT_STOP_FAILED")
        connections = self._finalizer_connections()
        for unit in connections:
            completed = self._systemctl("stop", unit, timeout=45)
            if completed.returncode != 0:
                raise CoordinatorError("COORDINATOR_UNIT_STOP_FAILED")
        for path in (ACTIVE_HANDOFF, ACTIVE_CAPTURE_REQUEST):
            if path.exists() or path.is_symlink():
                path.unlink()
        self._wait_inactive(
            [*OUTER_STOPPED_UNITS, *connections], timeout_seconds=20)
        # Re-enumerate after the bounded wait to close the socket-activation
        # race; no late connection instance may remain active.
        late = self._finalizer_connections()
        self._wait_inactive(late, timeout_seconds=5)

    def prove_terminal(
            self, campaign: str, cycle: str,
            inner_receipt: Optional[dict[str, Any]],
            expected_owner_status: str) -> Mapping[str, Any]:
        del inner_receipt
        completed = self._run_credential_script(
            TERMINAL_PROVER_CREDENTIAL, INSTALLED_TERMINAL_PROVER,
            "--campaign-id", campaign, "--cycle-id", cycle,
            "--phase", "outer-terminal", "--expected-owner-status",
            expected_owner_status, timeout=90)
        if completed.returncode != 0:
            raise CoordinatorError("COORDINATOR_TERMINAL_PROVER_FAILED")
        return strict_json(
            completed.stdout, "COORDINATOR_TERMINAL_PROVER_INVALID")

    def publish_completion(self, campaign: str, cycle: str, raw: bytes) -> None:
        path = CONTROL_ROOT / campaign / cycle / "cycle-completion-receipt.v4.json"
        if path.exists() or path.is_symlink():
            if _root_read(path, modes={0o600}) != raw:
                raise CoordinatorError("COORDINATOR_COMPLETION_CONFLICT")
            return
        _root_publish(path, raw)

    def reopen_completion(
            self, campaign: str, cycle: str) -> Optional[bytes]:
        return _completion_raw(campaign, cycle)

    def confirm_owner_purge(self, campaign: str, cycle: str) -> bytes:
        completed = self._run_credential_script(
            TERMINAL_PROVER_CREDENTIAL, INSTALLED_TERMINAL_PROVER,
            "--campaign-id", campaign, "--cycle-id", cycle,
            "--phase", "outer-purge", timeout=90)
        if completed.returncode != 0:
            raise CoordinatorError("COORDINATOR_OWNER_PURGE_FAILED")
        return completed.stdout


def _stable_read(path: Path, *, uid: int, gid: int, modes: set[int]) -> bytes:
    try:
        before = os.lstat(path)
        if (
                stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
                before.st_nlink != 1 or before.st_uid != uid or
                before.st_gid != gid or stat.S_IMODE(before.st_mode) not in modes or
                before.st_size < 1 or before.st_size > MAX_BYTES):
            raise CoordinatorError("COORDINATOR_FILE_METADATA_INVALID")
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            raw = bytearray()
            while len(raw) <= MAX_BYTES:
                chunk = os.read(
                    descriptor, min(65536, MAX_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CoordinatorError("COORDINATOR_FILE_UNAVAILABLE") from error
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink, item.st_uid,
        item.st_gid, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    if (
            len(raw) > MAX_BYTES or identity(before) != identity(opened) or
            identity(opened) != identity(after)):
        raise CoordinatorError("COORDINATOR_FILE_CHANGED")
    return bytes(raw)


def _root_read(path: Path, *, modes: set[int]) -> bytes:
    return _stable_read(path, uid=0, gid=0, modes=modes)


def _peer_read(path: Path) -> bytes:
    return _stable_read(path, uid=PEER_UID, gid=PEER_GID, modes={0o600})


def _root_publish(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(raw):
                offset += os.write(descriptor, raw[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CoordinatorError("COORDINATOR_PUBLISH_FAILED") from error


def _root_publish_active(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(path.parent, 0, 0)
    os.chmod(path.parent, 0o700)
    _root_publish(path, raw)


def _completion_raw(campaign: str, cycle: str) -> Optional[bytes]:
    directory = CONTROL_ROOT / campaign / cycle
    legacy = (
        directory / "cycle-completion-receipt.v1.json",
        directory / "cycle-completion-receipt.v2.json",
        directory / "cycle-completion-receipt.v3.json",
    )
    if any(path.exists() or path.is_symlink() for path in legacy):
        raise CoordinatorError("COORDINATOR_LEGACY_COMPLETION_PRESENT")
    path = directory / "cycle-completion-receipt.v4.json"
    if not (path.exists() or path.is_symlink()):
        return None
    raw = _root_read(path, modes={0o600})
    value = _exact(
        strict_json(raw, "COORDINATOR_COMPLETION_INVALID"),
        COMPLETION_FIELDS, "COORDINATOR_COMPLETION_INVALID")
    sealed(value, "COORDINATOR_COMPLETION_INVALID")
    if (
            value["schema"] != COMPLETION_SCHEMA or
            value["version"] != OUTER_VERSION or
            value["campaign_id"] != campaign or value["cycle_id"] != cycle or
            value["status"] not in {
                "P2_SUCCESS", "NO_TRADE", "RECOVERY_REQUIRED"} or
            value["retained_root_recovery_bearer_count"] !=
                (0 if value["durable_owner_status"] == "NONE" else 1) or
            value["retained_root_recovery_bearer_mutation_authority"] is not
                False or
            value["authority_granted"] is not False):
        raise CoordinatorError("COORDINATOR_COMPLETION_CONFLICT")
    return raw


def _completion_fully_joined(
        campaign: str, cycle: str, completion_raw: bytes) -> bool:
    completion = strict_json(
        completion_raw, "COORDINATOR_COMPLETION_INVALID")
    status = completion.get("durable_owner_status")
    if status != "RETIRED":
        return status in {"NONE", "RECOVERY_ONLY"}
    path = CONTROL_ROOT / campaign / cycle / \
        "outer-owner-purge-receipt.v1.json"
    if not (path.exists() or path.is_symlink()):
        return False
    purge_raw = _root_read(path, modes={0o600})
    _validate_owner_purge(
        purge_raw, campaign=campaign, cycle=cycle,
        completion_raw=completion_raw)
    try:
        residue = list(OWNER_AUTHORITY_ROOT.iterdir())
    except FileNotFoundError:
        residue = []
    except OSError as error:
        raise CoordinatorError("COORDINATOR_OWNER_PURGE_RECEIPT_INVALID") \
            from error
    return not residue


def _service_request() -> tuple[str, str]:
    current = _root_read(Path(__file__), modes={0o400})
    installed = _root_read(INSTALLED_COORDINATOR, modes={0o755})
    if current != installed:
        raise CoordinatorError("COORDINATOR_SELF_CREDENTIAL_MISMATCH")
    path = ProductionControl._credential_path(
        "active-coordinator-request.v1.json")
    raw = _root_read(path, modes={0o400})
    value = _exact(
        strict_json(raw, "COORDINATOR_REQUEST_INVALID"),
        COORDINATOR_REQUEST_FIELDS, "COORDINATOR_REQUEST_INVALID")
    sealed(value, "COORDINATOR_REQUEST_INVALID")
    campaign = _identifier(
        value["campaign_id"], "COORDINATOR_CAMPAIGN_INVALID")
    cycle = _identifier(value["cycle_id"], "COORDINATOR_CYCLE_INVALID")
    if (
            value["schema"] != COORDINATOR_REQUEST_SCHEMA or
            value["version"] != VERSION or value["status"] != "REQUESTED" or
            value["domain_id"] != DOMAIN or value["paper_only"] is not True or
            value["live_authorized"] is not False or
            value["authority_granted"] is not False):
        raise CoordinatorError("COORDINATOR_REQUEST_INVALID")
    return campaign, cycle


def _remove_active_request() -> None:
    try:
        ACTIVE_COORDINATOR_REQUEST.unlink()
    except FileNotFoundError:
        return
    descriptor = os.open(
        ACTIVE_COORDINATOR_REQUEST.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _public_start(campaign: str, cycle: str) -> bytes:
    existing = _completion_raw(campaign, cycle)
    if existing is not None and _completion_fully_joined(
            campaign, cycle, existing):
        return existing
    if any(path.exists() or path.is_symlink()
           for path in LEGACY_WAL_PATHS):
        raise CoordinatorError("COORDINATOR_LEGACY_WAL_PRESENT")
    if ACTIVE_COORDINATOR_REQUEST.exists() or \
            ACTIVE_COORDINATOR_REQUEST.is_symlink():
        raise CoordinatorError("COORDINATOR_REQUEST_BUSY")
    now_ms = time.time_ns() // 1_000_000
    request = sealed_body({
        "schema": COORDINATOR_REQUEST_SCHEMA, "version": VERSION,
        "status": "REQUESTED", "created_at_ms": now_ms,
        "campaign_id": campaign, "domain_id": DOMAIN, "cycle_id": cycle,
        "paper_only": True, "live_authorized": False,
        "authority_granted": False,
    })
    _root_publish_active(ACTIVE_COORDINATOR_REQUEST, canonical_json(request))
    started = ProductionControl._systemctl(
        "start", COORDINATOR_UNIT, timeout=20)
    deadline = time.monotonic() + PUBLIC_WAIT_SECONDS
    terminal = False
    try:
        # Even when systemctl reports the Main process's fast failure, wait for
        # ExecStopPost to leave the unit.  The request remains installed until
        # that request-independent crash closer has finished.
        while time.monotonic() < deadline:
            state = ProductionControl._unit_properties(COORDINATOR_UNIT)
            if state["ActiveState"] in {"inactive", "failed"} and \
                    state["Job"] == "":
                terminal = True
                break
            time.sleep(0.1)
        if not terminal:
            raise CoordinatorError("COORDINATOR_SERVICE_TIMEOUT")
        raw = _completion_raw(campaign, cycle)
        if raw is None:
            reason = "COORDINATOR_SERVICE_START_FAILED" if \
                started.returncode != 0 else "COORDINATOR_SERVICE_FAILED"
            raise CoordinatorError(reason)
        if not _completion_fully_joined(campaign, cycle, raw):
            raise CoordinatorError("COORDINATOR_OWNER_PURGE_INCOMPLETE")
        return raw
    finally:
        if terminal:
            _remove_active_request()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--campaign-id")
    parser.add_argument("--cycle-id")
    parser.add_argument("--service-run", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        if os.geteuid() != 0 or os.getegid() != 0:
            raise CoordinatorError("COORDINATOR_ROOT_REQUIRED")
        if arguments.service_run:
            if arguments.campaign_id is not None or \
                    arguments.cycle_id is not None:
                raise CoordinatorError("COORDINATOR_ARGUMENTS_INVALID")
            campaign, cycle = _service_request()
            with _Lock():
                raw = _completion_raw(campaign, cycle)
                if raw is None:
                    raw = coordinate(campaign, cycle, ProductionControl())
            service_run = True
        else:
            if arguments.campaign_id is None or arguments.cycle_id is None:
                raise CoordinatorError("COORDINATOR_ARGUMENTS_INVALID")
            campaign = _identifier(
                arguments.campaign_id, "COORDINATOR_CAMPAIGN_INVALID")
            cycle = _identifier(
                arguments.cycle_id, "COORDINATOR_CYCLE_INVALID")
            raw = _public_start(campaign, cycle)
            service_run = False
    except CoordinatorError as error:
        print(f"hepta-p1-paper-canary-root-coordinator: FAIL {error}")
        return 2
    completion = strict_json(raw, "COORDINATOR_COMPLETION_INVALID")
    print(canonical_json({
        "authority_granted": False, "status": completion["status"],
        "completion_receipt_file_sha256": sha(raw),
    }).decode("ascii"), end="")
    if service_run:
        return 0
    return 0 if completion["status"] in {"P2_SUCCESS", "NO_TRADE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
