#!/usr/bin/env -S /usr/bin/python3.12 -I -S

"""Root crash closer for a pre-bound external-P1 canary owner.

The normal peer is responsible for the ordinary cleanup request.  This image
is used only after the peer has died or its result is unavailable.  It reopens
the immutable handoff and append-only peer journal, derives PRE_TOOL versus
TOOL_CALL uncertainty, and emits the executor's emergency evidence/request
with the *same* pre-bound cleanup call and command identifiers.  It never
issues a PAPER tool call and never invents a replacement cleanup identifier.
"""

from __future__ import annotations

import argparse
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
from typing import Any, Mapping, Optional


MAX_BYTES = 1024 * 1024
MAX_HPE1_BYTES = 12 * 1024
PEER_UID = 2104
PEER_GID = 2104
CONTROL_ROOT = Path("/var/lib/hepta/p1-paper-canary-control")
ARTIFACT_ROOT = Path("/var/lib/hepta/p1-paper-canary")
ACTIVE_COORDINATOR_REQUEST = Path(
    "/run/hepta-p1-paper-canary/active-coordinator-request.v1.json")
ACTIVE_EXECUTION_HANDOFF = Path(
    "/run/hepta-p1-paper-canary/active-execution-handoff.v1.json")
FINALIZER_SOCKET = Path("/run/hepta-p1-paper-canary-finalizer.sock")
SESSIONCTL = Path("/usr/bin/hepta-sessionctl")
SESSION_SUPERVISOR_SOCKET = Path(
    "/run/hepta-tool-gateway-alpha/session-supervisor.sock")
TERMINAL_EVIDENCE_PATH = Path(
    "/run/hepta/paper-terminal-witness/alpha/terminal-evidence.v1")
OWNER_ROOT = Path("/var/lib/hepta-local-ai-paper-agent/session-authority")
ROOT_FINALIZER_WAL = Path(
    "/var/lib/hepta-local-ai-paper-agent/"
    "p1-paper-canary-root-cleanup-transaction.v4.json")
LEGACY_ROOT_FINALIZER_WALS = tuple(
    ROOT_FINALIZER_WAL.with_name(name) for name in (
        "p1-paper-canary-root-cleanup-transaction.json",
        "p1-paper-canary-root-cleanup-transaction.v1.json",
        "p1-paper-canary-root-cleanup-transaction.v2.json",
        "p1-paper-canary-root-cleanup-transaction.v3.json",
    ))
OWNER_INTENT = OWNER_ROOT / "session.token.owner-may-exist.v1.json"
OWNER_AUTHORITY = OWNER_ROOT / "session.token.authority.json"
OWNER_REVOKE = OWNER_ROOT / "session.token.revoke-token"
OWNER_PROVISIONING = OWNER_ROOT / ".session.token.provisioning"
OWNER_RUNTIME_TOKEN = Path("/run/hepta-agent-alpha/sessions/session.token")
CAPTURE_TOKEN = Path(
    "/run/hepta-p1-paper-canary/read-only-capture-session.token")
INSTALLED_EXECUTOR = Path("/usr/libexec/hepta-p1-paper-canary-executor")
INSTALLED_CLOSER = Path(
    "/usr/libexec/hepta-p1-paper-canary-crash-emergency-closer")
INSTALLED_TERMINAL_PROVER = Path(
    "/usr/libexec/hepta-p1-paper-canary-terminal-prover")
EXECUTOR_CREDENTIAL = "hepta-p1-paper-canary-executor.py"
LOCAL_CONTROL_CREDENTIAL = "hepta-local-paper-control.py"
TERMINAL_PROVER_CREDENTIAL = "hepta-p1-paper-canary-terminal-prover.py"
COORDINATOR_REQUEST_CREDENTIAL = "active-coordinator-request.v1.json"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
HSL8_TERMINAL_PROOF = "HSL8_POST_CUTOFF_SIGNED_TERMINAL_ACK_V3"
OWNER_RETIREMENT_FIELDS = frozenset({
    "schema", "version", "completed_at_ms", "campaign_id", "domain_id",
    "cycle_id", "cleanup_command_id", "session_owner_reference_sha256",
    "token_sha256", "lease_generation", "session_id",
    "paper_finalization_required", "recovery_id", "finalization_id",
    "expected_owner_set_sha256", "expected_owner_count",
    "owner_set_canonical_hex", "owner_token_sha256", "query_command_id",
    "recovery_query_result", "finalization_receipt_sha256",
    "finalization_receipt", "finalization_result",
    "terminal_ack_receipt_sha256", "terminal_ack_receipt",
    "terminal_ack_result", "terminal_acknowledged", "durable_hsl_audit",
    "hsl_owner_purged", "broker_flat_proven", "terminal_flat_proof_kind",
    "pre_cleanup_flat_evidence_role", "authority_path",
    "authority_file_sha256", "authority_body_sha256", "revoke_bearer_path",
    "revoke_bearer_file_sha256", "credentials_destroyed",
    "mutation_credentials_destroyed", "credentials_destroyed_scope",
    "retained_root_recovery_bearer_count",
    "retained_root_recovery_bearer_path",
    "retained_root_recovery_bearer_sha256",
    "retained_root_recovery_bearer_mutation_authority",
    "runtime_session_count", "durable_owner_count", "durable_owner_status",
    "paper_only", "live_authorized", "authority_granted", "body_sha256",
})
TERMINAL_ACK_RESULT_FIELDS = frozenset({
    "accepted", "reason_code", "lease_generation",
    "paper_finalization_state", "paper_finalization_required",
    "recovery_id", "finalization_id", "expected_owner_set_sha256",
    "expected_owner_count", "owner_token_sha256",
    "finalization_receipt_sha256", "finalization_receipt",
    "owner_audit_authoritative", "owner_audit_complete",
    "owner_active_order_count", "owner_uncertain_command_count",
    "owner_account", "owner_execution_domain", "execution_service_epoch",
    "execution_service_fencing_generation", "broker_connection_epoch",
    "broker_active_generation", "broker_terminal_generation",
    "broker_risk_generation", "broker_account_generation",
    "broker_position_generation", "broker_fx_cash_generation",
    "broker_exposure_generation", "broker_terminal_exposure_generation",
    "broker_risk_absorbed_exposure_generation",
    "broker_global_active_order_count",
    "broker_post_fill_risk_reconciliation_pending",
    "broker_recovery_audit_barrier_complete",
    "broker_recovery_audit_new_connection_epoch_required",
    "broker_position_quantity", "broker_gross_absolute_position",
    "preliminary_finalization_receipt_sha256",
    "terminalization_service_epoch",
    "terminalization_service_fencing_generation",
    "terminalization_generation", "terminal_latch_sha256",
    "execution_mutation_gate_closed", "broker_transport_connected",
    "broker_event_ingress_halted", "broker_callback_queue_drained",
    "broker_callbacks_in_flight", "broker_reconnect_permitted",
    "terminal_latch_durable", "terminal_runtime_latch_loaded",
    "terminal_runtime_verified", "terminal_replay",
    "terminal_proof_kind", "terminal_external_halt_latch_sha256",
    "transport_cutoff_receipt_file_sha256",
    "transport_cutoff_receipt_body_sha256",
    "post_cutoff_terminal_witness_file_sha256",
    "post_cutoff_terminal_witness_body_sha256",
    "terminal_evidence_sha256", "terminal_evidence_body_sha256",
    "egress_policy_sha256", "egress_publisher_pid",
    "egress_publisher_start_ticks", "provider_trust_policy_body_sha256",
    "signed_account_signature_sha256", "terminal_external_latch_loaded",
    "terminal_current_evidence_verified",
})
TERMINAL_ACK_RECEIPT_KEYS = (
    "schema", "version", "status", "terminal_proof_kind",
    "recovery_id", "finalization_id", "campaign_id", "cycle_id",
    "expected_owner_set_sha256", "expected_owner_count",
    "owner_set_canonical_hex", "preliminary_finalization_receipt_sha256",
    "owner_agent_id", "owner_session_id", "owner_account",
    "owner_execution_domain", "account_id_sha256",
    "execution_service_epoch", "execution_service_fencing_generation",
    "recovery_ingress_fence", "terminalization_generation",
    "terminalizing_latch_sha256", "terminal_external_halt_latch_sha256",
    "transport_cutoff_receipt_file_sha256",
    "transport_cutoff_receipt_body_sha256",
    "post_cutoff_terminal_witness_file_sha256",
    "post_cutoff_terminal_witness_body_sha256",
    "provider_trust_policy_file_sha256",
    "provider_trust_policy_body_sha256", "provider_id",
    "provider_capability", "signed_account_payload_sha256",
    "signed_account_signature_sha256", "host_boot_id",
    "egress_publisher_pid", "egress_publisher_start_ticks",
    "egress_policy_generation", "egress_policy_sha256",
    "query_started_after_challenge", "observed_after_cutoff",
    "snapshot_consistency", "causal_watermark_dominates_cutoff",
    "causal_watermark_dominates_all_mutations", "account_queries_complete",
    "active_orders_complete", "completed_orders_complete",
    "executions_complete", "positions_complete", "cash_fx_complete",
    "risk_complete", "known_mutation_command_set_sha256",
    "known_mutation_command_count", "known_correlation_set_sha256",
    "known_correlation_count", "all_known_mutation_commands_settled",
    "settled_mutation_command_count", "unknown_mutation_command_count",
    "unresolved_mutation_command_count", "unknown_active_order_count",
    "active_order_count", "position_count", "nonzero_cash_fx_count",
    "gross_absolute_position", "gross_fx_exposure", "gross_risk",
    "mutation_connector_count", "broker_socket_count",
    "broker_process_count", "broker_credential_count",
    "execution_service_inactive", "paper_units_inactive",
    "execution_mutation_gate_closed", "broker_transport_connected",
    "broker_reconnect_permitted", "read_only_authority",
    "mutation_attempted", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access",
    "order_submission_authorized", "order_authorized", "paper_only",
    "authority_granted", "terminal_external_halt_latch_durable",
    "terminal_witness_durable", "current_host_boundary_verified",
    "terminal_evidence_file_sha256", "terminal_evidence_body_sha256",
)
# HPE1 is an independently produced, root-owned stable account witness.  The
# crash closer deliberately carries this wire contract locally instead of
# importing the privileged producer/verifier.  The terminal ACK receipt and
# HPE1 share every provenance field except the receipt's schema/version/status
# labels and its two file/body digest labels (HPE1 calls the latter
# ``evidence_body_sha256``).  Keeping the tuple explicit prevents a future
# producer import or an accidental field-order drift from widening this trust
# boundary.
EXTERNAL_TERMINAL_EVIDENCE_KEYS = (
    "schema", "version", "status", "terminal_proof_kind", "recovery_id",
    "finalization_id", "campaign_id", "cycle_id",
    "expected_owner_set_sha256", "expected_owner_count",
    "owner_set_canonical_hex", "preliminary_finalization_receipt_sha256",
    "owner_agent_id", "owner_session_id", "owner_account",
    "owner_execution_domain", "account_id_sha256",
    "execution_service_epoch", "execution_service_fencing_generation",
    "recovery_ingress_fence", "terminalization_generation",
    "terminalizing_latch_sha256", "terminal_external_halt_latch_sha256",
    "transport_cutoff_receipt_file_sha256",
    "transport_cutoff_receipt_body_sha256",
    "post_cutoff_terminal_witness_file_sha256",
    "post_cutoff_terminal_witness_body_sha256",
    "provider_trust_policy_file_sha256",
    "provider_trust_policy_body_sha256", "provider_id",
    "provider_capability", "signed_account_payload_sha256",
    "signed_account_signature_sha256", "host_boot_id",
    "egress_publisher_pid", "egress_publisher_start_ticks",
    "egress_policy_generation", "egress_policy_sha256",
    "query_started_after_challenge", "observed_after_cutoff",
    "snapshot_consistency", "causal_watermark_dominates_cutoff",
    "causal_watermark_dominates_all_mutations", "account_queries_complete",
    "active_orders_complete", "completed_orders_complete",
    "executions_complete", "positions_complete", "cash_fx_complete",
    "risk_complete", "known_mutation_command_set_sha256",
    "known_mutation_command_count", "known_correlation_set_sha256",
    "known_correlation_count", "all_known_mutation_commands_settled",
    "settled_mutation_command_count", "unknown_mutation_command_count",
    "unresolved_mutation_command_count", "unknown_active_order_count",
    "active_order_count", "position_count", "nonzero_cash_fx_count",
    "gross_absolute_position", "gross_fx_exposure", "gross_risk",
    "mutation_connector_count", "broker_socket_count",
    "broker_process_count", "broker_credential_count",
    "execution_service_inactive", "paper_units_inactive",
    "execution_mutation_gate_closed", "broker_transport_connected",
    "broker_reconnect_permitted", "read_only_authority",
    "mutation_attempted", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access",
    "order_submission_authorized", "order_authorized", "paper_only",
    "authority_granted", "terminal_external_halt_latch_durable",
    "terminal_witness_durable", "current_host_boundary_verified",
    "evidence_body_sha256",
)
# Audit-facing synonym matching the verifier's wire-contract name.  It is a
# local tuple, not an imported privileged constant.
HPE1_KEYS = EXTERNAL_TERMINAL_EVIDENCE_KEYS


class CloserError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise CloserError("CRASH_CLOSER_CANONICALIZATION_FAILED") from error


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns,
    )


def stable_read(
        path: Path, *, uid: int, gid: int, mode: int,
        maximum: int = MAX_BYTES) -> bytes:
    try:
        before = os.lstat(path)
        if (
                stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != uid or before.st_gid != gid or
                stat.S_IMODE(before.st_mode) != mode or
                not 1 <= before.st_size <= maximum):
            raise CloserError("CRASH_CLOSER_FILE_METADATA_INVALID")
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            payload = bytearray()
            while len(payload) <= maximum:
                chunk = os.read(
                    descriptor, min(65536, maximum + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CloserError("CRASH_CLOSER_FILE_UNAVAILABLE") from error
    if (
            len(payload) > maximum or _identity(before) != _identity(opened) or
            _identity(opened) != _identity(after)):
        raise CloserError("CRASH_CLOSER_FILE_CHANGED")
    return bytes(payload)


def _strict(raw: bytes, reason: str) -> dict[str, Any]:
    if not raw.endswith(b"\n") or len(raw) > MAX_BYTES:
        raise CloserError(reason)
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=lambda pairs: _unique(pairs, reason),
            parse_float=lambda _value: (_ for _ in ()).throw(ValueError()),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise CloserError(reason) from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise CloserError(reason)
    return value


def _sealed(value: dict[str, Any], reason: str) -> str:
    claimed = value.get("body_sha256")
    if not isinstance(claimed, str) or DIGEST.fullmatch(claimed) is None:
        raise CloserError(reason)
    body = dict(value)
    del body["body_sha256"]
    if sha(canonical_json(body)) != claimed:
        raise CloserError(reason)
    return claimed


def _exact(value: Any, fields: frozenset[str], reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise CloserError(reason)
    return value


def _terminal_ack_receipt(value: Any, reason: str) -> tuple[dict[str, str], bytes]:
    if not isinstance(value, str):
        raise CloserError(reason)
    try:
        raw = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise CloserError(reason) from error
    if (not 1 <= len(raw) <= 12288 or not raw.endswith(b"\n") or
            b"\r" in raw or b"\x00" in raw):
        raise CloserError(reason)
    rows = raw[:-1].split(b"\n")
    if len(rows) != len(TERMINAL_ACK_RECEIPT_KEYS):
        raise CloserError(reason)
    receipt: dict[str, str] = {}
    for row, expected in zip(rows, TERMINAL_ACK_RECEIPT_KEYS, strict=True):
        key, separator, raw_value = row.partition(b"=")
        try:
            decoded_key = key.decode("ascii")
            decoded_value = raw_value.decode("ascii")
        except UnicodeDecodeError as error:
            raise CloserError(reason) from error
        if separator != b"=" or decoded_key != expected:
            raise CloserError(reason)
        receipt[decoded_key] = decoded_value
    return receipt, raw


def _terminal_evidence(
        value: Any,
) -> tuple[dict[str, str], bytes, bytes]:
    """Parse an HPE1 witness without loading the privileged producer.

    HPE1 is an ordered ASCII line file.  Its final body digest covers the
    complete prefix, including the ``HPE1`` marker, so changing a receipt and
    recomputing only the receipt hash cannot make an unrelated witness valid.
    Return the parsed values, exact bytes, and the digest-covered prefix.
    """
    failure = "CRASH_CLOSER_TERMINAL_EVIDENCE_INVALID"
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise CloserError(failure)
    raw = bytes(value)
    if not 1 <= len(raw) <= MAX_HPE1_BYTES:
        raise CloserError(failure)
    try:
        raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise CloserError(failure) from error
    rows = raw.splitlines(keepends=True)
    if (
            len(rows) != len(EXTERNAL_TERMINAL_EVIDENCE_KEYS) + 1 or
            rows[0] != b"HPE1\n" or
            any(not row.endswith(b"\n") or b"\r" in row for row in rows)):
        raise CloserError(failure)
    values: dict[str, str] = {}
    for row, expected_key in zip(
            rows[1:], EXTERNAL_TERMINAL_EVIDENCE_KEYS, strict=True):
        prefix = (expected_key + "=").encode("ascii")
        if (
                not row.startswith(prefix) or row == prefix + b"\n" or
                b"=" in row[len(prefix):-1] or expected_key in values):
            raise CloserError(failure)
        try:
            decoded = row[len(prefix):-1].decode(
                "ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise CloserError(failure) from error
        if not decoded:
            raise CloserError(failure)
        values[expected_key] = decoded
    if tuple(values) != EXTERNAL_TERMINAL_EVIDENCE_KEYS:
        raise CloserError(failure)
    body_prefix = b"".join(rows[:-1])
    claimed = values["evidence_body_sha256"]
    if (
            DIGEST.fullmatch(claimed) is None or
            claimed != sha(body_prefix)):
        raise CloserError(failure)
    return values, raw, body_prefix


def _validate_terminal_evidence_binding(
        evidence_raw: bytes, *, receipt: dict[str, str],
        result: dict[str, Any], retirement: dict[str, Any],
        root_receipt: dict[str, Any],
) -> None:
    """Bind the HSL8 ACK to independent HPE1 provenance.

    The normal HSL8 checks establish the receipt's internal consistency.  This
    second check is intentionally local and byte-for-byte: all fields shared
    by the receipt and HPE1 must agree, including provider, signed-account,
    egress, and known-command/correlation provenance.  A receipt hash is not
    an independent witness and is therefore never sufficient by itself.
    """
    failure = "CRASH_CLOSER_NORMAL_RECEIPT_INVALID"
    try:
        evidence, exact, _prefix = _terminal_evidence(evidence_raw)
    except (CloserError, TypeError, ValueError) as error:
        raise CloserError(failure) from error
    file_digest = sha(exact)
    body_digest = evidence["evidence_body_sha256"]
    if (
            result.get("terminal_evidence_sha256") != file_digest or
            receipt.get("terminal_evidence_file_sha256") != file_digest or
            result.get("terminal_evidence_body_sha256") != body_digest or
            receipt.get("terminal_evidence_body_sha256") != body_digest):
        raise CloserError(failure)

    identity = {
        "schema": "hepta.paper-terminal-witness-evidence.v1",
        "version": "1",
        "status": "CURRENT_POST_CUTOFF_TERMINAL_WITNESS_VERIFIED",
        "terminal_proof_kind": "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1",
        "recovery_id": str(retirement.get("recovery_id", "")),
        "finalization_id": str(retirement.get("finalization_id", "")),
        "campaign_id": str(retirement.get("campaign_id", "")),
        "cycle_id": str(retirement.get("cycle_id", "")),
        "owner_agent_id": "hepta-agent-alpha",
        "owner_session_id": str(retirement.get("session_id", "")),
    }
    if any(evidence.get(field) != expected
           for field, expected in identity.items()):
        raise CloserError(failure)
    if (
            evidence.get("campaign_id") != root_receipt.get("campaign_id") or
            evidence.get("cycle_id") != root_receipt.get("cycle_id") or
            evidence.get("recovery_id") != receipt.get("recovery_id") or
            evidence.get("finalization_id") != receipt.get("finalization_id")):
        raise CloserError(failure)

    # All common receipt fields are textual in both wire formats.  The two
    # evidence digest names and HPE1's own schema/version/status are the only
    # intentional differences.
    for field in TERMINAL_ACK_RECEIPT_KEYS:
        if field in {
                "schema", "version", "status",
                "terminal_evidence_file_sha256",
                "terminal_evidence_body_sha256"}:
            continue
        if field not in EXTERNAL_TERMINAL_EVIDENCE_KEYS or \
                receipt.get(field) != evidence.get(field):
            raise CloserError(failure)

    # Preserve the independent fail-closed boundary even if a future HSL8
    # validator accidentally drops one of these checks.
    required_true = (
        "query_started_after_challenge", "observed_after_cutoff",
        "causal_watermark_dominates_cutoff",
        "causal_watermark_dominates_all_mutations",
        "account_queries_complete", "active_orders_complete",
        "completed_orders_complete", "executions_complete",
        "positions_complete", "cash_fx_complete", "risk_complete",
        "all_known_mutation_commands_settled", "execution_service_inactive",
        "paper_units_inactive", "execution_mutation_gate_closed",
        "read_only_authority", "paper_only",
        "terminal_external_halt_latch_durable", "terminal_witness_durable",
        "current_host_boundary_verified")
    required_false = (
        "broker_transport_connected", "broker_reconnect_permitted",
        "mutation_attempted", "paper_authorized", "live_authorized",
        "mutation_authorized", "direct_broker_access",
        "order_submission_authorized", "order_authorized",
        "authority_granted")
    if (
            any(evidence.get(field) != "1" for field in required_true) or
            any(evidence.get(field) != "0" for field in required_false)):
        raise CloserError(failure)


def _current_terminal_evidence() -> bytes:
    """Read and syntactically validate the current root-owned HPE1 witness."""
    if not (TERMINAL_EVIDENCE_PATH.exists() or
            TERMINAL_EVIDENCE_PATH.is_symlink()):
        raise CloserError("CRASH_CLOSER_TERMINAL_WITNESS_REQUIRED")
    try:
        raw = stable_read(
            TERMINAL_EVIDENCE_PATH, uid=0, gid=0, mode=0o400,
            maximum=MAX_HPE1_BYTES)
        _terminal_evidence(raw)
    except CloserError as error:
        if str(error) == "CRASH_CLOSER_TERMINAL_WITNESS_REQUIRED":
            raise
        raise CloserError(
            "CRASH_CLOSER_TERMINAL_EVIDENCE_INVALID") from error
    return raw


# Keep the explicit ``external`` spellings available to audit/test callers;
# these wrappers do not import or execute any privileged producer code.
def _external_parse_terminal_evidence(
        value: Any) -> tuple[dict[str, str], bytes, bytes]:
    return _terminal_evidence(value)


def _external_validate_terminal_evidence_binding(
        evidence_raw: bytes, *, receipt: dict[str, str],
        result: dict[str, Any], retirement: dict[str, Any],
        root_receipt: dict[str, Any]) -> None:
    _validate_terminal_evidence_binding(
        evidence_raw, receipt=receipt, result=result,
        retirement=retirement, root_receipt=root_receipt)


def _external_current_terminal_evidence() -> bytes:
    return _current_terminal_evidence()


def _reject_legacy_hsl8_retirement(
        retirement: dict[str, Any], root_receipt: dict[str, Any]) -> None:
    # Retained only as a structural record of the retired local-ACK contract.
    # No production caller may interpret those fields as terminal evidence.
    raise CloserError("CRASH_CLOSER_LEGACY_NORMAL_RECEIPT_PRESENT")


def _validate_hsl8_retirement(
        retirement: dict[str, Any], root_receipt: dict[str, Any], *,
        terminal_evidence_raw: bytes | None = None) -> None:
    reason = "CRASH_CLOSER_NORMAL_RECEIPT_INVALID"
    _exact(retirement, OWNER_RETIREMENT_FIELDS, reason)
    terminal = _exact(
        retirement["terminal_ack_result"], TERMINAL_ACK_RESULT_FIELDS, reason)
    receipt, receipt_raw = _terminal_ack_receipt(
        retirement["terminal_ack_receipt"], reason)
    recovery = retirement.get("recovery_query_result")
    if not isinstance(recovery, dict):
        raise CloserError(reason)

    boolean_fields = {
        "accepted", "paper_finalization_required",
        "owner_audit_authoritative", "owner_audit_complete",
        "broker_post_fill_risk_reconciliation_pending",
        "broker_recovery_audit_barrier_complete",
        "broker_recovery_audit_new_connection_epoch_required",
        "execution_mutation_gate_closed", "broker_transport_connected",
        "broker_event_ingress_halted", "broker_callback_queue_drained",
        "broker_reconnect_permitted", "terminal_latch_durable",
        "terminal_runtime_latch_loaded", "terminal_runtime_verified",
        "terminal_replay", "terminal_external_latch_loaded",
        "terminal_current_evidence_verified",
    }
    integer_fields = {
        "lease_generation", "expected_owner_count",
        "owner_active_order_count", "owner_uncertain_command_count",
        "execution_service_fencing_generation", "broker_connection_epoch",
        "broker_active_generation", "broker_terminal_generation",
        "broker_risk_generation", "broker_account_generation",
        "broker_position_generation", "broker_fx_cash_generation",
        "broker_exposure_generation", "broker_terminal_exposure_generation",
        "broker_risk_absorbed_exposure_generation",
        "broker_global_active_order_count",
        "terminalization_service_fencing_generation",
        "terminalization_generation", "broker_callbacks_in_flight",
        "egress_publisher_pid", "egress_publisher_start_ticks",
    }
    if (any(type(terminal[field]) is not bool for field in boolean_fields) or
            any(type(terminal[field]) is not int for field in integer_fields) or
            any(not isinstance(terminal[field], str)
                for field in set(terminal) - boolean_fields - integer_fields)):
        raise CloserError(reason)

    digest_fields = {
        "expected_owner_set_sha256",
        "preliminary_finalization_receipt_sha256", "account_id_sha256",
        "terminalizing_latch_sha256",
        "terminal_external_halt_latch_sha256",
        "transport_cutoff_receipt_file_sha256",
        "transport_cutoff_receipt_body_sha256",
        "post_cutoff_terminal_witness_file_sha256",
        "post_cutoff_terminal_witness_body_sha256",
        "provider_trust_policy_file_sha256",
        "provider_trust_policy_body_sha256",
        "signed_account_payload_sha256", "signed_account_signature_sha256",
        "egress_policy_sha256", "known_mutation_command_set_sha256",
        "known_correlation_set_sha256", "terminal_evidence_file_sha256",
        "terminal_evidence_body_sha256",
    }
    truth_fields = {
        "query_started_after_challenge", "observed_after_cutoff",
        "causal_watermark_dominates_cutoff",
        "causal_watermark_dominates_all_mutations",
        "account_queries_complete", "active_orders_complete",
        "completed_orders_complete", "executions_complete",
        "positions_complete", "cash_fx_complete", "risk_complete",
        "all_known_mutation_commands_settled",
        "execution_service_inactive", "paper_units_inactive",
        "execution_mutation_gate_closed", "read_only_authority",
        "paper_only", "terminal_external_halt_latch_durable",
        "terminal_witness_durable", "current_host_boundary_verified",
    }
    false_fields = {
        "broker_transport_connected", "broker_reconnect_permitted",
        "mutation_attempted", "paper_authorized", "live_authorized",
        "mutation_authorized", "direct_broker_access",
        "order_submission_authorized", "order_authorized",
        "authority_granted",
    }
    zero_fields = {
        "unknown_mutation_command_count", "unresolved_mutation_command_count",
        "unknown_active_order_count", "active_order_count", "position_count",
        "nonzero_cash_fx_count", "gross_absolute_position",
        "gross_fx_exposure", "gross_risk", "mutation_connector_count",
        "broker_socket_count", "broker_process_count",
        "broker_credential_count",
    }
    def unsigned(field: str) -> int:
        value = receipt[field]
        if re.fullmatch(r"0|[1-9][0-9]*", value) is None:
            raise CloserError(reason)
        return int(value)

    if (
            retirement["schema"] !=
                "hepta.p1-paper-canary-durable-owner-retirement-receipt.v4" or
            retirement["version"] != 4 or
            retirement["campaign_id"] != root_receipt.get("campaign_id") or
            retirement["cycle_id"] != root_receipt.get("cycle_id") or
            retirement["domain_id"] != "alpha" or
            retirement["cleanup_command_id"] !=
                root_receipt.get("cleanup_command_id") or
            retirement["paper_finalization_required"] is not True or
            retirement["terminal_acknowledged"] is not True or
            retirement["hsl_owner_purged"] is not True or
            retirement["broker_flat_proven"] is not True or
            retirement["durable_hsl_audit"] != HSL8_TERMINAL_PROOF or
            retirement["terminal_flat_proof_kind"] != HSL8_TERMINAL_PROOF or
            retirement["pre_cleanup_flat_evidence_role"] !=
                "DIAGNOSTIC_ONLY_NOT_TERMINAL_PROOF" or
            retirement["credentials_destroyed"] is not True or
            retirement["mutation_credentials_destroyed"] is not True or
            retirement["credentials_destroyed_scope"] !=
                "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY" or
            retirement["retained_root_recovery_bearer_count"] != 1 or
            retirement["retained_root_recovery_bearer_path"] !=
                retirement["revoke_bearer_path"] or
            retirement["retained_root_recovery_bearer_sha256"] !=
                retirement["revoke_bearer_file_sha256"] or
            retirement["retained_root_recovery_bearer_mutation_authority"] is
                not False or
            retirement["runtime_session_count"] != 0 or
            retirement["durable_owner_count"] != 0 or
            retirement["durable_owner_status"] != "RETIRED" or
            retirement["paper_only"] is not True or
            retirement["live_authorized"] is not False or
            retirement["authority_granted"] is not False or
            retirement["terminal_ack_receipt_sha256"] != sha(receipt_raw) or
            terminal["finalization_receipt_sha256"] !=
                retirement["terminal_ack_receipt_sha256"] or
            terminal["finalization_receipt"] !=
                retirement["terminal_ack_receipt"] or
            terminal["accepted"] is not True or
            terminal["reason_code"] != "PAPER_FINALIZATION_TERMINAL_ACKED" or
            terminal["paper_finalization_state"] != "ACKED" or
            terminal["paper_finalization_required"] is not True or
            terminal["recovery_id"] != retirement["recovery_id"] or
            terminal["finalization_id"] != retirement["finalization_id"] or
            terminal["expected_owner_set_sha256"] !=
                retirement["expected_owner_set_sha256"] or
            terminal["expected_owner_count"] !=
                retirement["expected_owner_count"] or
            terminal["owner_token_sha256"] != retirement["owner_token_sha256"] or
            terminal["lease_generation"] != retirement["lease_generation"] or
            terminal["preliminary_finalization_receipt_sha256"] !=
                retirement["finalization_receipt_sha256"] or
            terminal["owner_account"] != recovery.get("owner_account") or
            terminal["owner_execution_domain"] !=
                recovery.get("owner_execution_domain") or
            terminal["execution_service_epoch"] !=
                root_receipt.get("execution_service_epoch") or
            terminal["execution_service_fencing_generation"] !=
                root_receipt.get("execution_service_fencing_generation") or
            terminal["terminalization_service_epoch"] !=
                terminal["execution_service_epoch"] or
            terminal["terminalization_service_fencing_generation"] !=
                terminal["execution_service_fencing_generation"] or
            terminal["terminalization_generation"] != 1 or
            terminal["execution_mutation_gate_closed"] is not True or
            terminal["broker_transport_connected"] is not False or
            terminal["broker_event_ingress_halted"] is not True or
            terminal["broker_callback_queue_drained"] is not False or
            terminal["broker_callbacks_in_flight"] != 0 or
            terminal["broker_reconnect_permitted"] is not False or
            terminal["terminal_latch_durable"] is not True or
            terminal["terminal_runtime_latch_loaded"] is not False or
            terminal["terminal_runtime_verified"] is not False or
            terminal["terminal_external_latch_loaded"] is not True or
            terminal["terminal_current_evidence_verified"] is not True or
            terminal["terminal_replay"] is not True or
            terminal["terminal_proof_kind"] !=
                "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" or
            terminal["owner_audit_authoritative"] is not True or
            terminal["owner_audit_complete"] is not True or
            any(terminal[field] != 0 for field in (
                "broker_connection_epoch", "broker_active_generation",
                "broker_terminal_generation", "broker_risk_generation",
                "broker_account_generation", "broker_position_generation",
                "broker_fx_cash_generation", "broker_exposure_generation",
                "broker_terminal_exposure_generation",
                "broker_risk_absorbed_exposure_generation",
                "broker_global_active_order_count", "owner_active_order_count",
                "owner_uncertain_command_count")) or
            terminal["broker_post_fill_risk_reconciliation_pending"] is not
                False or
            terminal["broker_recovery_audit_barrier_complete"] is not False or
            terminal["broker_recovery_audit_new_connection_epoch_required"] is
                not False or
            terminal["broker_position_quantity"] != "0" or
            terminal["broker_gross_absolute_position"] != "0" or
            terminal["egress_publisher_pid"] < 1 or
            terminal["egress_publisher_start_ticks"] < 1 or
            receipt["schema"] !=
                "hepta.paper-session-terminal-ack-receipt.v3" or
            receipt["version"] != "3" or receipt["status"] != "TERMINAL_ACKED" or
            receipt["terminal_proof_kind"] !=
                "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" or
            receipt["recovery_id"] != retirement["recovery_id"] or
            receipt["finalization_id"] != retirement["finalization_id"] or
            receipt["campaign_id"] != retirement["campaign_id"] or
            receipt["cycle_id"] != retirement["cycle_id"] or
            receipt["expected_owner_set_sha256"] !=
                retirement["expected_owner_set_sha256"] or
            receipt["expected_owner_count"] != str(
                retirement["expected_owner_count"]) or
            receipt["owner_set_canonical_hex"] !=
                retirement["owner_set_canonical_hex"] or
            receipt["preliminary_finalization_receipt_sha256"] !=
                retirement["finalization_receipt_sha256"] or
            receipt["owner_agent_id"] != "hepta-agent-alpha" or
            receipt["owner_session_id"] != retirement["session_id"] or
            receipt["owner_account"] != terminal["owner_account"] or
            receipt["owner_execution_domain"] !=
                terminal["owner_execution_domain"] or
            receipt["account_id_sha256"] != sha(
                terminal["owner_account"].encode("ascii")) or
            receipt["execution_service_epoch"] !=
                terminal["execution_service_epoch"] or
            receipt["execution_service_fencing_generation"] != str(
                terminal["execution_service_fencing_generation"]) or
            receipt["recovery_ingress_fence"] != str(
                retirement["lease_generation"]) or
            receipt["terminalization_generation"] != "1" or
            receipt["provider_capability"] !=
                "ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1" or
            receipt["snapshot_consistency"] not in {
                "ATOMIC_ACCOUNT", "CAUSAL_WATERMARK"} or
            any(DIGEST.fullmatch(receipt[field]) is None or
                receipt[field] == "sha256:" + "0" * 64
                for field in digest_fields) or
            any(receipt[field] != "1" for field in truth_fields) or
            any(receipt[field] != "0" for field in false_fields | zero_fields) or
            any(unsigned(field) < 1 for field in (
                "execution_service_fencing_generation",
                "recovery_ingress_fence", "terminalization_generation",
                "egress_publisher_pid", "egress_publisher_start_ticks",
                "egress_policy_generation")) or
            unsigned("known_mutation_command_count") > 4096 or
            unsigned("known_correlation_count") > 4096 or
            unsigned("settled_mutation_command_count") !=
                unsigned("known_mutation_command_count") or
            terminal["terminal_latch_sha256"] !=
                receipt["terminalizing_latch_sha256"] or
            terminal["terminal_external_halt_latch_sha256"] !=
                receipt["terminal_external_halt_latch_sha256"] or
            terminal["terminal_evidence_sha256"] !=
                receipt["terminal_evidence_file_sha256"] or
            terminal["terminal_evidence_body_sha256"] !=
                receipt["terminal_evidence_body_sha256"] or
            terminal["egress_policy_sha256"] != receipt["egress_policy_sha256"] or
            str(terminal["egress_publisher_pid"]) !=
                receipt["egress_publisher_pid"] or
            str(terminal["egress_publisher_start_ticks"]) !=
                receipt["egress_publisher_start_ticks"] or
            terminal["provider_trust_policy_body_sha256"] !=
                receipt["provider_trust_policy_body_sha256"] or
            terminal["signed_account_signature_sha256"] !=
                receipt["signed_account_signature_sha256"]):
        raise CloserError(reason)
    if terminal_evidence_raw is None:
        # HSL8's self-hash is not an independent terminal witness.  Callers
        # must provide the current root-owned HPE1 bytes explicitly.
        raise CloserError("CRASH_CLOSER_TERMINAL_WITNESS_REQUIRED")
    _validate_terminal_evidence_binding(
        terminal_evidence_raw, receipt=receipt, result=terminal,
        retirement=retirement, root_receipt=root_receipt)


def _fresh_terminal_replay(
        retirement: dict[str, Any], root_receipt: dict[str, Any], *,
        terminal_evidence_raw: bytes | None = None,
) -> dict[str, Any]:
    reason = "CRASH_CLOSER_CURRENT_RUNTIME_REPLAY_REQUIRED"
    if terminal_evidence_raw is None:
        try:
            terminal_evidence_raw = _current_terminal_evidence()
        except CloserError as error:
            raise CloserError(reason) from error
    _validate_hsl8_retirement(
        retirement, root_receipt,
        terminal_evidence_raw=terminal_evidence_raw)
    bearer_path = Path(retirement["revoke_bearer_path"])
    if bearer_path != OWNER_REVOKE:
        raise CloserError(reason)
    bearer = stable_read(
        bearer_path, uid=0, gid=0, mode=0o600, maximum=65)
    if (
            len(bearer) != 65 or
            re.fullmatch(rb"[0-9a-f]{64}\n", bearer) is None or
            sha(bearer) != retirement["revoke_bearer_file_sha256"]):
        raise CloserError(reason)
    # The exact bytes validated above are the bytes whose digest is passed to
    # the runtime ACK command.  Do not substitute a receipt-provided hash.
    terminal_evidence_sha256 = sha(terminal_evidence_raw)
    arguments = [
        str(SESSIONCTL), "--socket", str(SESSION_SUPERVISOR_SOCKET),
        "--io-timeout-ms", "25000", "paper-terminal-witness-ack",
        "--token-file", str(bearer_path), "--generation",
        str(retirement["lease_generation"]), "--recovery-id",
        retirement["recovery_id"], "--finalization-id",
        retirement["finalization_id"], "--expected-owner-set-sha256",
        retirement["expected_owner_set_sha256"], "--expected-owner-count",
        str(retirement["expected_owner_count"]), "--receipt-sha256",
        retirement["finalization_receipt_sha256"], "--token-owner-uid", "0",
        "--terminal-evidence-file", str(TERMINAL_EVIDENCE_PATH),
        "--terminal-evidence-sha256", terminal_evidence_sha256,
    ]
    try:
        completed = subprocess.run(
            arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd="/", env={"LC_ALL": "C"},
            close_fds=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CloserError(reason) from error
    if completed.returncode != 0:
        raise CloserError(reason)
    replayed = _strict(completed.stdout, reason)
    if replayed != retirement["terminal_ack_result"]:
        raise CloserError(reason)
    receipt, _receipt_raw = _terminal_ack_receipt(
        retirement["terminal_ack_receipt"], reason)
    try:
        _validate_terminal_evidence_binding(
            terminal_evidence_raw, receipt=receipt, result=replayed,
            retirement=retirement, root_receipt=root_receipt)
    except CloserError as error:
        raise CloserError(reason) from error
    return replayed


def _unique(pairs: list[tuple[str, Any]], reason: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CloserError(reason)
        result[key] = value
    return result


def _credential_path(name: str) -> Path:
    directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
    if not directory or not Path(directory).is_absolute():
        raise CloserError("CRASH_CLOSER_CREDENTIAL_DIRECTORY_MISSING")
    return Path(directory) / name


def _load_verified_module(
        *, credential_name: str, installed_path: Path,
        module_name: str, expected_sha256: Optional[str] = None) -> ModuleType:
    installed = stable_read(installed_path, uid=0, gid=0, mode=0o755,
                            maximum=4 * MAX_BYTES)
    credential = stable_read(
        _credential_path(credential_name), uid=0, gid=0, mode=0o400,
        maximum=4 * MAX_BYTES)
    if credential != installed or (
            expected_sha256 is not None and sha(installed) != expected_sha256):
        raise CloserError("CRASH_CLOSER_CREDENTIAL_IMAGE_MISMATCH")
    module = ModuleType(module_name)
    module.__file__ = str(_credential_path(credential_name))
    sys.modules[module_name] = module
    try:
        exec(compile(credential, module.__file__, "exec"), module.__dict__)
    except Exception as error:
        raise CloserError("CRASH_CLOSER_CREDENTIAL_IMAGE_INVALID") from error
    return module


def _provisional_handoff(raw: bytes, campaign: str, cycle: str) -> dict[str, Any]:
    value = _strict(raw, "CRASH_CLOSER_HANDOFF_INVALID")
    if (
            value.get("schema") !=
                "hepta.p1-paper-canary-execution-handoff.v1" or
            value.get("campaign_id") != campaign or value.get("cycle_id") != cycle or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            value.get("authority_granted") is not False):
        raise CloserError("CRASH_CLOSER_HANDOFF_INVALID")
    images = value.get("installed_images")
    if not isinstance(images, list):
        raise CloserError("CRASH_CLOSER_HANDOFF_INVALID")
    by_role = {
        item.get("role"): item for item in images
        if isinstance(item, dict) and isinstance(item.get("role"), str)
    }
    executor = by_role.get("executor")
    closer = by_role.get("crash-emergency-closer")
    if (
            not isinstance(executor, dict) or
            executor.get("path") != str(INSTALLED_EXECUTOR) or
            not isinstance(executor.get("file_sha256"), str) or
            DIGEST.fullmatch(executor["file_sha256"]) is None):
        raise CloserError("CRASH_CLOSER_EXECUTOR_PIN_INVALID")
    # New handoffs must bind the closer.  Keeping this check explicit makes a
    # pre-upgrade handoff fail closed instead of silently trusting this image.
    if (
            not isinstance(closer, dict) or
            closer.get("path") != str(INSTALLED_CLOSER) or
            closer.get("mode") != 0o755 or closer.get("uid") != 0 or
            closer.get("gid") != 0 or closer.get("nlink") != 1 or
            not isinstance(closer.get("file_sha256"), str) or
            DIGEST.fullmatch(closer["file_sha256"]) is None):
        raise CloserError("CRASH_CLOSER_SELF_PIN_INVALID")
    current = stable_read(
        Path(__file__), uid=0, gid=0, mode=0o400, maximum=4 * MAX_BYTES)
    installed_closer = stable_read(
        INSTALLED_CLOSER, uid=0, gid=0, mode=0o755, maximum=4 * MAX_BYTES)
    if current != installed_closer or sha(current) != closer["file_sha256"]:
        raise CloserError("CRASH_CLOSER_SELF_PIN_INVALID")
    return value


def _publish_peer_or_same(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o1730)
    if path.exists() or path.is_symlink():
        if stable_read(path, uid=PEER_UID, gid=PEER_GID, mode=0o600) != raw:
            raise CloserError("CRASH_CLOSER_ARTIFACT_CONFLICT")
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchown(descriptor, PEER_UID, PEER_GID)
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(raw):
                offset += os.write(descriptor, raw[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CloserError("CRASH_CLOSER_ARTIFACT_PUBLISH_FAILED") from error


def _send_request(raw: bytes) -> bytes:
    response = bytearray()
    channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        channel.settimeout(45)
        channel.connect(str(FINALIZER_SOCKET))
        channel.sendall(raw)
        channel.shutdown(socket.SHUT_WR)
        while len(response) <= MAX_BYTES:
            chunk = channel.recv(min(65536, MAX_BYTES + 1 - len(response)))
            if not chunk:
                break
            response.extend(chunk)
    except OSError as error:
        raise CloserError("CRASH_CLOSER_FINALIZER_UNCERTAIN") from error
    finally:
        channel.close()
    if len(response) > MAX_BYTES:
        raise CloserError("CRASH_CLOSER_FINALIZER_RESPONSE_INVALID")
    return bytes(response)


class _CrashBackend:
    def __init__(self, module: ModuleType, handoff: Any) -> None:
        self.module = module
        self.handoff = handoff
        self.directory = ARTIFACT_ROOT / handoff.document["campaign_id"] / \
            handoff.document["cycle_id"]
        self.journal = Path(module.journal_path_for(handoff))

    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def append_journal(self, record: bytes) -> None:
        if self.journal.exists() or self.journal.is_symlink():
            raise CloserError("CRASH_CLOSER_JOURNAL_ALREADY_EXISTS")
        _publish_peer_or_same(self.journal, record)

    def reopen_journal(self) -> Mapping[str, Any]:
        raw = stable_read(
            self.journal, uid=PEER_UID, gid=PEER_GID, mode=0o600)
        return {
            "path": str(self.journal), "raw": raw, "secure_reopen": True,
            "mode": 0o600, "nlink": 1,
        }

    def publish_checkpoint(self, artifacts: Mapping[str, bytes]) -> None:
        if set(artifacts) != {
                "root-emergency-cleanup-evidence.v1.json",
                "root-emergency-cleanup-request.v1.json"}:
            raise CloserError("CRASH_CLOSER_CHECKPOINT_SET_INVALID")
        for name, raw in artifacts.items():
            _publish_peer_or_same(self.directory / name, raw)

    def finalize_root_cleanup(self, request: bytes) -> bytes:
        return _send_request(request)


def _restore_run(module: ModuleType, handoff: Any,
                 backend: _CrashBackend) -> tuple[Any, Any]:
    journal_path = Path(module.journal_path_for(handoff))
    if not (journal_path.exists() or journal_path.is_symlink()):
        run = module._Run(handoff, backend, backend.now_ms())
        run.append_header()
    snapshot = module._journal_snapshot(backend)
    records = module._parse_journal(snapshot.raw, handoff)
    run = module._Run(handoff, backend, backend.now_ms())
    run.evidence = module._journal_evidence(records, handoff)
    requests: dict[str, dict[str, Any]] = {}
    responses: dict[str, dict[str, Any]] = {}
    for record in records[1:]:
        role = record["call_role"]
        if record["event"] == "REQUEST":
            requests[role] = record
        else:
            responses[role] = record
            run.service_epoch = record["service_epoch"]
            run.fencing_generation = record["fencing_generation"]
            if record["status"] == "OK":
                run.last_completed_phase = record["phase"]
    pending = [record for role, record in requests.items() if role not in responses]
    if pending:
        last = max(pending, key=lambda item: item["sequence"])
        run.uncertain_phase = last["phase"]
        run.uncertain_tool_call_id = last["tool_call_id"]
    request_roles = set(requests)
    run.cycle_opened = "open" in request_roles
    run.cycle_closed = (
        "close" in responses and responses["close"]["status"] == "OK")
    run.place_attempted = "place" in request_roles
    run.close_attempted = "close" in request_roles
    if "place" in requests and "place" not in responses:
        run.close_outcome = "PLACE_UNCERTAIN"
    elif run.close_attempted and not run.cycle_closed:
        run.close_outcome = "OPERATOR_ABORT"
    else:
        run.close_outcome = "NOT_APPLICABLE"
    run.fail("COORDINATOR_CRASH_EMERGENCY")
    return run, snapshot


def _emergency_checkpoint(
        module: ModuleType, run: Any, snapshot: Any,
        backend: _CrashBackend) -> tuple[dict[str, Any], bytes,
                                         dict[str, Any], bytes]:
    evidence_path = backend.directory / \
        "root-emergency-cleanup-evidence.v1.json"
    request_path = backend.directory / \
        "root-emergency-cleanup-request.v1.json"
    evidence_exists = evidence_path.exists() or evidence_path.is_symlink()
    request_exists = request_path.exists() or request_path.is_symlink()
    if request_exists and not evidence_exists:
        raise CloserError("CRASH_CLOSER_CHECKPOINT_PARTIAL_INVALID")
    if not evidence_exists:
        return module._emergency_cleanup_documents(run, snapshot)

    evidence_raw = stable_read(
        evidence_path, uid=PEER_UID, gid=PEER_GID, mode=0o600)
    try:
        evidence = module._load_canonical(
            evidence_raw, "ROOT_EMERGENCY_CLEANUP_EVIDENCE")
        module._exact(
            evidence, module.ROOT_EMERGENCY_EVIDENCE_FIELDS,
            "ROOT_EMERGENCY_EVIDENCE_FIELDS_INVALID")
        module._sealed(
            evidence, "ROOT_EMERGENCY_EVIDENCE_BODY_INVALID")
    except Exception as error:
        raise CloserError("CRASH_CLOSER_CHECKPOINT_INVALID") from error
    handoff = run.handoff.document
    expected_evidence = {
        "schema": module.ROOT_EMERGENCY_EVIDENCE_SCHEMA,
        "version": module.VERSION,
        "campaign_id": handoff["campaign_id"],
        "domain_id": handoff["domain_id"],
        "cycle_id": handoff["cycle_id"],
        "handoff_path": module.handoff_path_for(run.handoff),
        "handoff_file_sha256": run.handoff.file_sha256,
        "handoff_body_sha256": run.handoff.body_sha256,
        "journal_path": snapshot.path,
        "journal_sha256": module.sha256_bytes(snapshot.raw),
        "journal_size": len(snapshot.raw),
        "session_owner_reference_sha256": module.canonical_sha256(
            handoff["session_owner_reference"]),
        "broker_flat_proven": False,
        "authority_granted": False,
    }
    if any(evidence.get(key) != value
           for key, value in expected_evidence.items()):
        raise CloserError("CRASH_CLOSER_CHECKPOINT_BINDING_INVALID")

    if request_exists:
        request_raw = stable_read(
            request_path, uid=PEER_UID, gid=PEER_GID, mode=0o600)
        try:
            request = module._load_canonical(
                request_raw, "ROOT_EMERGENCY_CLEANUP_REQUEST")
            module._exact(
                request, module.ROOT_EMERGENCY_CLEANUP_REQUEST_FIELDS,
                "ROOT_EMERGENCY_REQUEST_FIELDS_INVALID")
            module._sealed(request, "ROOT_EMERGENCY_REQUEST_BODY_INVALID")
        except Exception as error:
            raise CloserError("CRASH_CLOSER_CHECKPOINT_INVALID") from error
    else:
        # publish_checkpoint writes evidence first.  Reconstruct a request
        # after a crash in that single-file seam from the sealed evidence's
        # original timestamp, preserving one immutable checkpoint lineage.
        created_at_ms = evidence["created_at_ms"]
        expires_at_ms = min(handoff["expires_at_ms"], created_at_ms + 45_000)
        if expires_at_ms <= created_at_ms:
            raise CloserError("CRASH_CLOSER_CHECKPOINT_EXPIRED")
        root_call = handoff["root_cleanup_call"]
        body = {
            "schema": module.ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA,
            "version": module.VERSION,
            "issued_at_ms": created_at_ms,
            "expires_at_ms": expires_at_ms,
            "campaign_id": handoff["campaign_id"],
            "domain_id": handoff["domain_id"],
            "cycle_id": handoff["cycle_id"],
            "cleanup_tool_call_id": root_call["tool_call_id"],
            "cleanup_command_id": root_call["command_id"],
            "tool_descriptor_sha256": root_call["tool_descriptor_sha256"],
            "handoff_file_sha256": run.handoff.file_sha256,
            "handoff_body_sha256": run.handoff.body_sha256,
            "session_owner_reference_sha256": module.canonical_sha256(
                handoff["session_owner_reference"]),
            "execution_service_epoch": handoff["execution_service_epoch"],
            "execution_service_fencing_generation": handoff[
                "execution_service_fencing_generation"],
            "emergency_evidence_path":
                module.emergency_cleanup_evidence_path_for(run.handoff),
            "emergency_evidence_file_sha256":
                module.sha256_bytes(evidence_raw),
            "emergency_evidence_body_sha256": evidence["body_sha256"],
            "recovery_reason_codes": evidence["recovery_reason_codes"],
            "required_actions": list(module.ROOT_EMERGENCY_CLEANUP_ACTIONS),
            "broker_flat_proven": False,
            "paper_only": True,
            "live_authorized": False,
            "authority_granted": False,
        }
        request = module._sealed_body(body)
        request_raw = module.canonical_json(request)

    root_call = handoff["root_cleanup_call"]
    expected_request = {
        "schema": module.ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA,
        "version": module.VERSION,
        "campaign_id": handoff["campaign_id"],
        "domain_id": handoff["domain_id"],
        "cycle_id": handoff["cycle_id"],
        "cleanup_tool_call_id": root_call["tool_call_id"],
        "cleanup_command_id": root_call["command_id"],
        "tool_descriptor_sha256": root_call["tool_descriptor_sha256"],
        "handoff_file_sha256": run.handoff.file_sha256,
        "handoff_body_sha256": run.handoff.body_sha256,
        "emergency_evidence_path":
            module.emergency_cleanup_evidence_path_for(run.handoff),
        "emergency_evidence_file_sha256": module.sha256_bytes(evidence_raw),
        "emergency_evidence_body_sha256": evidence["body_sha256"],
        "recovery_reason_codes": evidence["recovery_reason_codes"],
        "required_actions": list(module.ROOT_EMERGENCY_CLEANUP_ACTIONS),
        "broker_flat_proven": False,
        "paper_only": True,
        "live_authorized": False,
        "authority_granted": False,
    }
    if any(request.get(key) != value
           for key, value in expected_request.items()):
        raise CloserError("CRASH_CLOSER_CHECKPOINT_BINDING_INVALID")
    if (
            not isinstance(request.get("issued_at_ms"), int) or
            not isinstance(request.get("expires_at_ms"), int) or
            request["issued_at_ms"] > request["expires_at_ms"] or
            evidence.get("created_at_ms") != request["issued_at_ms"]):
        raise CloserError("CRASH_CLOSER_CHECKPOINT_TIME_INVALID")
    run.emergency_cleanup_evidence = evidence
    run.emergency_cleanup_evidence_raw = evidence_raw
    run.root_emergency_cleanup_request = request
    run.root_emergency_cleanup_request_raw = request_raw
    return evidence, evidence_raw, request, request_raw


def _force_deny_all() -> None:
    control = _load_verified_module(
        credential_name=LOCAL_CONTROL_CREDENTIAL,
        installed_path=Path("/usr/libexec/hepta-local-paper-control"),
        module_name="_hepta_p1_canary_crash_local_control")
    try:
        control.guardian_fail_close()
    except Exception as error:
        raise CloserError("CRASH_CLOSER_FAIL_CLOSE_FAILED") from error


def _fsync_parent(path: Path) -> None:
    try:
        descriptor = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CloserError("CRASH_CLOSER_OWNER_DURABILITY_FAILED") from error


def _publish_root_or_same(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(path.parent, 0, 0)
    os.chmod(path.parent, 0o700)
    if path.exists() or path.is_symlink():
        if stable_read(path, uid=0, gid=0, mode=0o600) != raw:
            raise CloserError("CRASH_CLOSER_OWNER_RECEIPT_CONFLICT")
        return
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
        raise CloserError("CRASH_CLOSER_OWNER_RECEIPT_FAILED") from error
    _fsync_parent(path)


def _revoke_owner(token_path: Path, generation: int) -> str:
    arguments = [
        str(SESSIONCTL), "--socket", str(SESSION_SUPERVISOR_SOCKET),
        "revoke", "--token-file", str(token_path), "--generation",
        str(generation), "--token-owner-uid", "0"]

    def invoke() -> tuple[int, dict[str, Any]]:
        try:
            completed = subprocess.run(
                arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd="/", env={"LC_ALL": "C"},
                close_fds=True, check=False, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CloserError("CRASH_CLOSER_OWNER_REVOKE_UNCERTAIN") from error
        try:
            response = json.loads(
                completed.stdout.decode("ascii"),
                object_pairs_hook=lambda pairs: _unique(
                    pairs, "CRASH_CLOSER_OWNER_REVOKE_INVALID"),
                parse_float=lambda _value: (_ for _ in ()).throw(ValueError()),
                parse_constant=lambda _value:
                    (_ for _ in ()).throw(ValueError()))
        except (UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise CloserError("CRASH_CLOSER_OWNER_REVOKE_INVALID") from error
        if not isinstance(response, dict):
            raise CloserError("CRASH_CLOSER_OWNER_REVOKE_INVALID")
        return completed.returncode, response

    code, response = invoke()
    accepted = code == 0 and response == {
        "accepted": True, "reason_code": "OK",
        "lease_generation": generation}
    absent = (
        code == 4 and set(response) == {
            "accepted", "reason_code", "lease_generation"} and
        response.get("accepted") is False and
        response.get("reason_code") in {
            "SESSION_NOT_FOUND", "SESSION_LEASE_NOT_FOUND"} and
        response.get("lease_generation") in {0, generation})
    if not accepted and not absent:
        raise CloserError("CRASH_CLOSER_OWNER_REVOKE_UNCERTAIN")
    if accepted:
        code, response = invoke()
        absent = (
            code == 4 and set(response) == {
                "accepted", "reason_code", "lease_generation"} and
            response.get("accepted") is False and
            response.get("reason_code") in {
                "SESSION_NOT_FOUND", "SESSION_LEASE_NOT_FOUND"} and
            response.get("lease_generation") in {0, generation})
        if not absent:
            raise CloserError("CRASH_CLOSER_OWNER_REVOKE_UNCERTAIN")
    return str(response["reason_code"])


def _unlink_owner_path(path: Path, expected: Optional[str] = None) -> None:
    if not (path.exists() or path.is_symlink()):
        _fsync_parent(path)
        return
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or \
            metadata.st_nlink != 1:
        raise CloserError("CRASH_CLOSER_OWNER_MATERIAL_INVALID")
    raw = stable_read(
        path, uid=metadata.st_uid, gid=metadata.st_gid,
        mode=stat.S_IMODE(metadata.st_mode), maximum=MAX_BYTES)
    if expected is not None and sha(raw) != expected:
        raise CloserError("CRASH_CLOSER_OWNER_MATERIAL_CHANGED")
    path.unlink()
    _fsync_parent(path)


def _recover_intent(
        *, intent_path: Path, token_candidates: tuple[Path, ...],
        receipt_path: Path, campaign: str, cycle: str, template: str
) -> bool:
    if not (intent_path.exists() or intent_path.is_symlink()):
        return False
    intent_raw = stable_read(intent_path, uid=0, gid=0, mode=0o600)
    intent = _strict(intent_raw, "CRASH_CLOSER_OWNER_INTENT_INVALID")
    _sealed(intent, "CRASH_CLOSER_OWNER_INTENT_INVALID")
    expected_schema = (
        "hepta.p1-paper-canary-owner-may-exist.v1" if template == "paper"
        else "hepta.p1-paper-canary-capture-owner.v1")
    generation_field = (
        "expected_lease_generation" if template == "watch"
        else "expected_lease_generation")
    if (
            intent.get("schema") != expected_schema or
            intent.get("campaign_id") != campaign or
            intent.get("cycle_id") != cycle or
            intent.get("domain_id") != "alpha" or
            intent.get(generation_field) != 1 or
            intent.get("session_id") is None or
            intent.get("peer_uid") != PEER_UID or
            intent.get("paper_only") is not True or
            intent.get("live_authorized") is not False or
            intent.get("authority_granted") is not False or
            (template == "paper" and (
                re.fullmatch(r"DU[0-9]{1,16}", str(
                    intent.get("owner_account"))) is None or
                intent.get("owner_execution_domain") != "PAPER:alpha"))):
        raise CloserError("CRASH_CLOSER_OWNER_INTENT_INVALID")
    bearer = next((path for path in token_candidates
                   if path.exists() and not path.is_symlink()), None)
    if bearer is None:
        # If the bearer is already absent, only a prior sealed retirement can
        # prove the generation was removed from HSL.
        receipt_candidates = [receipt_path]
        if template == "watch":
            receipt_candidates.insert(
                0, intent_path.parent /
                "capture-session-retirement-receipt.v1.json")
        for candidate in receipt_candidates:
            if not candidate.exists() or candidate.is_symlink():
                continue
            receipt_raw = stable_read(candidate, uid=0, gid=0, mode=0o600)
            receipt = _strict(
                receipt_raw, "CRASH_CLOSER_OWNER_RECEIPT_INVALID")
            _sealed(receipt, "CRASH_CLOSER_OWNER_RECEIPT_INVALID")
            orphan = candidate == receipt_path
            expected_receipt_schema = (
                "hepta.p1-paper-canary-orphan-owner-retirement.v1" if orphan
                else "hepta.p1-paper-canary-capture-owner-retirement.v1")
            if (receipt.get("schema") == expected_receipt_schema and
                    receipt.get("status") == "RETIRED" and
                    receipt.get("campaign_id") == campaign and
                    receipt.get("cycle_id") == cycle and
                    receipt.get("template_id") == template and
                    receipt.get("session_id") == intent["session_id"] and
                    receipt.get("lease_generation") == 1 and
                    receipt.get("token_sha256") == intent["token_sha256"] and
                    receipt.get("owner_intent_body_sha256") ==
                        intent["body_sha256"] and
                    ((orphan and receipt.get("revoke_reason_code") in {
                        "SESSION_NOT_FOUND", "SESSION_LEASE_NOT_FOUND"}) or
                     (not orphan and
                      receipt.get("revoke_accepted") is True and
                      receipt.get("revoke_reason_code") == "OK" and
                      receipt.get("revoke_audit_reason_code") in {
                          "SESSION_NOT_FOUND", "SESSION_LEASE_NOT_FOUND"})) and
                    receipt.get("durable_hsl_audit") ==
                        "GENERATION_ABSENT_AFTER_REVOKE"):
                _unlink_owner_path(intent_path, sha(intent_raw))
                return True
        raise CloserError("CRASH_CLOSER_OWNER_BEARER_MISSING")
    bearer_raw = stable_read(
        bearer, uid=0, gid=0, mode=0o400 if template == "watch" else 0o600,
        maximum=4096)
    if sha(bearer_raw) != intent.get("token_sha256"):
        raise CloserError("CRASH_CLOSER_OWNER_BEARER_CHANGED")
    reason = _revoke_owner(bearer, 1)
    completed_at = time.time_ns() // 1_000_000
    body = {
        "schema": "hepta.p1-paper-canary-orphan-owner-retirement.v1",
        "version": 1, "status": "RETIRED", "completed_at_ms": completed_at,
        "campaign_id": campaign, "domain_id": "alpha", "cycle_id": cycle,
        "template_id": template, "session_id": intent["session_id"],
        "lease_generation": 1, "token_sha256": intent["token_sha256"],
        "owner_intent_body_sha256": intent["body_sha256"],
        "revoke_reason_code": reason,
        "durable_hsl_audit": "GENERATION_ABSENT_AFTER_REVOKE",
        "paper_only": True, "live_authorized": False,
        "authority_granted": False,
    }
    document = {**body, "body_sha256": sha(canonical_json(body))}
    _publish_root_or_same(receipt_path, canonical_json(document))
    for path in token_candidates:
        if path.exists() and not path.is_symlink():
            _unlink_owner_path(path, intent["token_sha256"])
    _unlink_owner_path(intent_path, sha(intent_raw))
    return True


def _regular_single_link(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1


def _prove_paper_owner_is_strictly_pre_handoff(
        campaign: str, cycle: str) -> None:
    """Prove generic revoke cannot race any PAPER mutation attempt.

    The PAPER owner is provisioned only after the read-only capture and
    normalization have completed, but before the immutable execution handoff
    is published.  Generic HSL revoke is safe only inside that narrow seam.
    Once a handoff, executor journal, cleanup artifact, or unknown per-cycle
    artifact exists, only the HSL8 finalization protocol may retire the owner.
    """
    control = CONTROL_ROOT / campaign / cycle
    artifact = ARTIFACT_ROOT / campaign / cycle
    if any(path.exists() or path.is_symlink() for path in (
            ACTIVE_EXECUTION_HANDOFF,
            control / "execution-handoff.v1.json",
            ROOT_FINALIZER_WAL, *LEGACY_ROOT_FINALIZER_WALS)):
        raise CloserError("CRASH_CLOSER_PAPER_OWNER_REQUIRES_HSL8")

    required_control = {
        "capture-request.v1.json",
        "normalized-launch-intent-receipt.v1.json",
    }
    allowed_control = required_control | {
        "capture-session-owner.v1.json",
        "capture-session-retirement-receipt.v1.json",
        "capture-session-orphan-retirement-receipt.v1.json",
        "mutation-session-orphan-retirement-receipt.v1.json",
    }
    required_artifact = {
        "tool-catalog.v1.json",
        "read-only-capture.v1.json",
        "original-strategy-decision.v1.json",
        "canary-strategy-state.v1.json",
    }
    try:
        control_entries = {path.name: path for path in control.iterdir()}
        artifact_entries = {path.name: path for path in artifact.iterdir()}
    except OSError as error:
        raise CloserError(
            "CRASH_CLOSER_PRE_HANDOFF_PROOF_UNAVAILABLE") from error
    if (
            not required_control.issubset(control_entries) or
            not required_artifact.issubset(artifact_entries) or
            not set(control_entries).issubset(allowed_control) or
            set(artifact_entries) != required_artifact or
            any(not _regular_single_link(path)
                for path in (*control_entries.values(),
                             *artifact_entries.values()))):
        raise CloserError("CRASH_CLOSER_PAPER_OWNER_REQUIRES_HSL8")


def reconcile_orphan_owners(campaign: str, cycle: str) -> dict[str, Any]:
    if (IDENTIFIER.fullmatch(campaign) is None or
            IDENTIFIER.fullmatch(cycle) is None):
        raise CloserError("CRASH_CLOSER_IDENTIFIER_INVALID")
    directory = CONTROL_ROOT / campaign / cycle
    capture = _recover_intent(
        intent_path=directory / "capture-session-owner.v1.json",
        token_candidates=(CAPTURE_TOKEN,),
        receipt_path=directory /
            "capture-session-orphan-retirement-receipt.v1.json",
        campaign=campaign, cycle=cycle, template="watch")
    if OWNER_INTENT.exists() or OWNER_INTENT.is_symlink():
        _prove_paper_owner_is_strictly_pre_handoff(campaign, cycle)
    mutation = _recover_intent(
        intent_path=OWNER_INTENT,
        token_candidates=(OWNER_REVOKE, OWNER_PROVISIONING),
        receipt_path=directory /
            "mutation-session-orphan-retirement-receipt.v1.json",
        campaign=campaign, cycle=cycle, template="paper")
    if mutation:
        for path in (OWNER_RUNTIME_TOKEN, OWNER_AUTHORITY):
            if path.exists() and not path.is_symlink():
                _unlink_owner_path(path)
    # A token without an intent predates the only possible HSL call and is safe
    # to remove; an authority without its intent remains a hard failure.
    if not capture and CAPTURE_TOKEN.exists() and not CAPTURE_TOKEN.is_symlink():
        _unlink_owner_path(CAPTURE_TOKEN)
    if not mutation and OWNER_PROVISIONING.exists() and \
            not OWNER_PROVISIONING.is_symlink():
        _unlink_owner_path(OWNER_PROVISIONING)
    if not mutation and any(path.exists() or path.is_symlink() for path in (
            OWNER_AUTHORITY, OWNER_REVOKE, OWNER_RUNTIME_TOKEN)):
        raise CloserError("CRASH_CLOSER_UNJOURNALED_OWNER_PRESENT")
    return {
        "schema": "hepta.p1-paper-canary-orphan-recovery-result.v1",
        "version": 1,
        "status": "OWNERS_RETIRED" if capture or mutation else "NO_OWNER",
        "campaign_id": campaign, "cycle_id": cycle,
        "capture_owner_retired": capture, "mutation_owner_retired": mutation,
        "authority_granted": False,
    }


def close(campaign: str, cycle: str) -> dict[str, Any]:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise CloserError("CRASH_CLOSER_ROOT_REQUIRED")
    if (
            IDENTIFIER.fullmatch(campaign) is None or
            IDENTIFIER.fullmatch(cycle) is None):
        raise CloserError("CRASH_CLOSER_IDENTIFIER_INVALID")
    handoff_path = CONTROL_ROOT / campaign / cycle / "execution-handoff.v1.json"
    handoff_raw = stable_read(handoff_path, uid=0, gid=0, mode=0o600)
    provisional = _provisional_handoff(handoff_raw, campaign, cycle)
    executor_pin = next(
        item["file_sha256"] for item in provisional["installed_images"]
        if item["role"] == "executor")
    module = _load_verified_module(
        credential_name=EXECUTOR_CREDENTIAL,
        installed_path=INSTALLED_EXECUTOR,
        module_name="_hepta_p1_canary_crash_executor",
        expected_sha256=executor_pin)
    try:
        handoff = module.validate_handoff(handoff_raw, require_fresh=False)
    except Exception as error:
        raise CloserError("CRASH_CLOSER_HANDOFF_INVALID") from error
    backend = _CrashBackend(module, handoff)
    run, snapshot = _restore_run(module, handoff, backend)
    try:
        evidence, evidence_raw, request, request_raw = \
            _emergency_checkpoint(module, run, snapshot, backend)
        backend.publish_checkpoint({
            "root-emergency-cleanup-evidence.v1.json": evidence_raw,
            "root-emergency-cleanup-request.v1.json": request_raw,
        })
        receipt_path = CONTROL_ROOT / campaign / cycle / \
            "root-emergency-cleanup-receipt.v1.json"
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt_raw = stable_read(
                receipt_path, uid=0, gid=0, mode=0o600)
        else:
            receipt_raw = backend.finalize_root_cleanup(request_raw)
        receipt = module._validate_root_emergency_cleanup_receipt(
            receipt_raw, run=run, evidence_raw=evidence_raw,
            request_raw=request_raw, journal=snapshot)
    except Exception as error:
        try:
            _force_deny_all()
        except Exception:
            pass
        raise CloserError("CRASH_CLOSER_EMERGENCY_FINALIZATION_FAILED") from error
    return {
        "schema": "hepta.p1-paper-canary-crash-emergency-close-result.v1",
        "version": 1, "status": "RECOVERY_ONLY_DENY_ALL",
        "campaign_id": campaign, "cycle_id": cycle,
        "cleanup_command_id": request["cleanup_command_id"],
        "emergency_request_file_sha256": sha(request_raw),
        "emergency_request_body_sha256": request["body_sha256"],
        "emergency_receipt_file_sha256": sha(receipt_raw),
        "emergency_receipt_body_sha256": receipt["body_sha256"],
        "authority_granted": False,
    }


def _request_identity() -> tuple[str, str]:
    request_path = _credential_path(COORDINATOR_REQUEST_CREDENTIAL)
    raw = stable_read(
        request_path, uid=0, gid=0, mode=0o400,
        maximum=65536)
    value = _strict(raw, "CRASH_CLOSER_COORDINATOR_REQUEST_INVALID")
    campaign = value.get("campaign_id")
    cycle = value.get("cycle_id")
    if (
            not isinstance(campaign, str) or not isinstance(cycle, str) or
            IDENTIFIER.fullmatch(campaign) is None or
            IDENTIFIER.fullmatch(cycle) is None):
        raise CloserError("CRASH_CLOSER_COORDINATOR_REQUEST_INVALID")
    return campaign, cycle


def _completion_present(campaign: str, cycle: str) -> bool:
    directory = CONTROL_ROOT / campaign / cycle
    legacy = (
        directory / "cycle-completion-receipt.v1.json",
        directory / "cycle-completion-receipt.v2.json",
        directory / "cycle-completion-receipt.v3.json",
    )
    if any(path.exists() or path.is_symlink() for path in legacy):
        raise CloserError("CRASH_CLOSER_LEGACY_COMPLETION_PRESENT")
    path = directory / "cycle-completion-receipt.v4.json"
    if not (path.exists() or path.is_symlink()):
        return False
    raw = stable_read(path, uid=0, gid=0, mode=0o600)
    value = _strict(raw, "CRASH_CLOSER_COMPLETION_INVALID")
    _sealed(value, "CRASH_CLOSER_COMPLETION_INVALID")
    if (
            value.get("schema") !=
                "hepta.p1-paper-canary-cycle-completion-receipt.v4" or
            value.get("version") != 4 or
            value.get("campaign_id") != campaign or
            value.get("cycle_id") != cycle or
            value.get("status") not in {
                "P2_SUCCESS", "NO_TRADE", "RECOVERY_REQUIRED"} or
            value.get("broker_deny_all") is not True or
            value.get("authority_granted") is not False):
        raise CloserError("CRASH_CLOSER_COMPLETION_INVALID")
    return True


def _normal_owner_retired(campaign: str, cycle: str) -> bool:
    directory = CONTROL_ROOT / campaign / cycle
    legacy = (
        directory / "root-cleanup-receipt.v1.json",
        directory / "root-cleanup-receipt.v2.json",
        directory / "root-cleanup-receipt.v3.json",
        directory / "durable-owner-retirement-receipt.v1.json",
        directory / "durable-owner-retirement-receipt.v2.json",
        directory / "durable-owner-retirement-receipt.v3.json",
    )
    if any(path.exists() or path.is_symlink() for path in legacy):
        raise CloserError("CRASH_CLOSER_LEGACY_NORMAL_RECEIPT_PRESENT")
    path = directory / "root-cleanup-receipt.v4.json"
    if not (path.exists() or path.is_symlink()):
        return False
    raw = stable_read(path, uid=0, gid=0, mode=0o600)
    value = _strict(raw, "CRASH_CLOSER_NORMAL_RECEIPT_INVALID")
    _sealed(value, "CRASH_CLOSER_NORMAL_RECEIPT_INVALID")
    if (
            value.get("schema") !=
                "hepta.p1-paper-canary-root-cleanup-receipt.v4" or
            value.get("version") != 4 or
            value.get("status") != "ROOT_CLEANUP_COMPLETE_DENY_ALL" or
            value.get("campaign_id") != campaign or
            value.get("cycle_id") != cycle or
            value.get("broker_deny_all") is not True or
            value.get("durable_owner_count") != 0 or
            value.get("durable_owner_status") != "RETIRED" or
            value.get("mutation_credentials_destroyed") is not True or
            value.get("credentials_destroyed_scope") !=
                "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY" or
            value.get("retained_root_recovery_bearer_count") != 1 or
            value.get("retained_root_recovery_bearer_path") !=
                str(OWNER_REVOKE) or
            value.get(
                "retained_root_recovery_bearer_mutation_authority") is not
                False or
            value.get("completed_actions") != [
                "FINALIZE_DURABLE_OWNER_POST_FENCE",
                "ACK_PURGE_DURABLE_OWNER",
                "STOP_GUARDIAN",
                "DISABLE_EXECUTION_CONTROL",
                "ENGAGE_KILL_SWITCH",
                "ENFORCE_DENY_ALL",
                "DESTROY_OWNER_CREDENTIALS",
                "PROVE_CONNECTOR_ZERO",
            ] or
            value.get("authority_granted") is not False):
        raise CloserError("CRASH_CLOSER_NORMAL_RECEIPT_INVALID")
    retirement_path = directory / "durable-owner-retirement-receipt.v4.json"
    if value.get("durable_owner_retirement_receipt_path") != \
            str(retirement_path):
        raise CloserError("CRASH_CLOSER_NORMAL_RECEIPT_INVALID")
    retirement_raw = stable_read(
        retirement_path, uid=0, gid=0, mode=0o600)
    retirement = _strict(
        retirement_raw, "CRASH_CLOSER_NORMAL_RECEIPT_INVALID")
    retirement_body = _sealed(
        retirement, "CRASH_CLOSER_NORMAL_RECEIPT_INVALID")
    if (
            value.get("durable_owner_retirement_receipt_file_sha256") !=
                sha(retirement_raw) or
            value.get("durable_owner_retirement_receipt_body_sha256") !=
                retirement_body or
            value.get("retained_root_recovery_bearer_sha256") !=
                retirement.get("revoke_bearer_file_sha256")):
        raise CloserError("CRASH_CLOSER_NORMAL_RECEIPT_INVALID")
    # A cached terminal_replay=true field is only historical evidence.  While
    # outer completion is absent the crash closer must reopen the current
    # root-owned HPE1 witness and query the execution runtime using the
    # retained root bearer on every invocation.
    try:
        terminal_evidence_raw = _current_terminal_evidence()
    except CloserError as error:
        if str(error) == "CRASH_CLOSER_TERMINAL_WITNESS_REQUIRED":
            raise
        raise CloserError("CRASH_CLOSER_NORMAL_RECEIPT_INVALID") from error
    _fresh_terminal_replay(
        retirement, value,
        terminal_evidence_raw=terminal_evidence_raw)
    return True


def _confirm_outer_owner_purge(campaign: str, cycle: str) -> None:
    directory = CONTROL_ROOT / campaign / cycle
    completion_raw = stable_read(
        directory / "cycle-completion-receipt.v4.json",
        uid=0, gid=0, mode=0o600)
    completion = _strict(
        completion_raw, "CRASH_CLOSER_COMPLETION_INVALID")
    if completion.get("durable_owner_status") != "RETIRED":
        return
    credential = _credential_path(TERMINAL_PROVER_CREDENTIAL)
    credential_raw = stable_read(
        credential, uid=0, gid=0, mode=0o400, maximum=4 * MAX_BYTES)
    installed_raw = stable_read(
        INSTALLED_TERMINAL_PROVER, uid=0, gid=0, mode=0o755,
        maximum=4 * MAX_BYTES)
    if credential_raw != installed_raw:
        raise CloserError("CRASH_CLOSER_TERMINAL_PROVER_IMAGE_MISMATCH")
    try:
        completed = subprocess.run(
            ["/usr/bin/python3.12", "-I", "-S", str(credential),
             "--campaign-id", campaign, "--cycle-id", cycle,
             "--phase", "outer-purge"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd="/",
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/usr/sbin",
                 "CREDENTIALS_DIRECTORY":
                    os.environ.get("CREDENTIALS_DIRECTORY", "")},
            close_fds=True, check=False, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CloserError("CRASH_CLOSER_OWNER_PURGE_FAILED") from error
    if completed.returncode != 0:
        raise CloserError("CRASH_CLOSER_OWNER_PURGE_FAILED")
    receipt = _strict(completed.stdout, "CRASH_CLOSER_OWNER_PURGE_FAILED")
    if (
            receipt.get("schema") !=
                "hepta.p1-paper-canary-outer-owner-purge-receipt.v1" or
            receipt.get("status") != "OWNER_BEARER_PURGED" or
            receipt.get("campaign_id") != campaign or
            receipt.get("cycle_id") != cycle or
            receipt.get("owner_bearer_purged") is not True or
            receipt.get("durable_owner_credential_count") != 0 or
            receipt.get("authority_granted") is not False):
        raise CloserError("CRASH_CLOSER_OWNER_PURGE_FAILED")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--campaign-id")
    parser.add_argument("--cycle-id")
    parser.add_argument("--exec-stop-post", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        if arguments.exec_stop_post:
            if arguments.campaign_id is not None or arguments.cycle_id is not None:
                raise CloserError("CRASH_CLOSER_ARGUMENTS_INVALID")
            campaign, cycle = _request_identity()
        else:
            if arguments.campaign_id is None or arguments.cycle_id is None:
                raise CloserError("CRASH_CLOSER_ARGUMENTS_INVALID")
            campaign, cycle = arguments.campaign_id, arguments.cycle_id
        handoff = CONTROL_ROOT / campaign / cycle / "execution-handoff.v1.json"
        if _completion_present(campaign, cycle):
            _force_deny_all()
            _confirm_outer_owner_purge(campaign, cycle)
            result = {
                "schema": "hepta.p1-paper-canary-crash-emergency-close-result.v1",
                "version": 1, "status": "TERMINAL_COMPLETION_PRESENT",
                "campaign_id": campaign, "cycle_id": cycle,
                "authority_granted": False,
            }
        elif _normal_owner_retired(campaign, cycle):
            _force_deny_all()
            result = {
                "schema": "hepta.p1-paper-canary-crash-emergency-close-result.v1",
                "version": 1, "status": "RETIRED_OWNER_DENY_ALL",
                "campaign_id": campaign, "cycle_id": cycle,
                "authority_granted": False,
            }
        elif not (handoff.exists() or handoff.is_symlink()):
            orphan = reconcile_orphan_owners(campaign, cycle)
            _force_deny_all()
            result = {
                "schema": "hepta.p1-paper-canary-crash-emergency-close-result.v1",
                "version": 1, "status": (
                    "DENY_ALL_ORPHAN_OWNERS_RETIRED" if
                    orphan["status"] == "OWNERS_RETIRED" else
                    "DENY_ALL_NO_OWNER"),
                "campaign_id": campaign, "cycle_id": cycle,
                "authority_granted": False,
            }
        else:
            result = close(campaign, cycle)
    except CloserError as error:
        try:
            _force_deny_all()
        except Exception:
            pass
        print(f"hepta-p1-paper-canary-crash-emergency-closer: FAIL {error}")
        return 2
    print(canonical_json(result).decode("ascii"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
