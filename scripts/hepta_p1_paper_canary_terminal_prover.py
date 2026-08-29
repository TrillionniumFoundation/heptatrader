#!/usr/bin/env -S /usr/bin/python3.12 -I -S

"""Credential-pinned root PAPER boundary controller and terminal prover."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import ModuleType
from typing import Any, Optional


MAX_BYTES = 16 * 1024 * 1024
POLICY_PATH = Path("/etc/heptatrader/paper-campaigns/alpha.json")
CONTROL_ROOT = Path("/var/lib/hepta/p1-paper-canary-control")
SESSION_ROOT = Path("/run/hepta-agent-alpha/sessions")
OWNER_ROOT = Path("/var/lib/hepta-local-ai-paper-agent/session-authority")
SESSIONCTL = Path("/usr/bin/hepta-sessionctl")
SESSION_SUPERVISOR_SOCKET = Path(
    "/run/hepta-tool-gateway-alpha/session-supervisor.sock")
TERMINAL_EVIDENCE_PATH = Path(
    "/run/hepta/paper-terminal-witness/alpha/terminal-evidence.v1")
CAPTURE_TOKEN = Path(
    "/run/hepta-p1-paper-canary/read-only-capture-session.token")
INSTALLED_CONTROL = Path("/usr/libexec/hepta-local-paper-control")
INSTALLED_SELF = Path("/usr/libexec/hepta-p1-paper-canary-terminal-prover")
CONTROL_CREDENTIAL = "hepta-local-paper-control.py"
SELF_CREDENTIAL = "hepta-p1-paper-canary-terminal-prover.py"
EXECUTOR_UNIT = "hepta-p1-paper-canary-executor.service"
CAPTURE_UNIT = "hepta-p1-paper-canary-capture.service"
FINALIZER_SOCKET_UNIT = "hepta-p1-paper-canary-finalizer.socket"
FINALIZER_CONNECTION_GLOB = "hepta-p1-paper-canary-finalizer@*.service"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
NORMAL_ROOT_RECEIPT_SCHEMA = (
    "hepta.p1-paper-canary-root-cleanup-receipt.v4")
NORMAL_ROOT_RECEIPT_VERSION = 4
OWNER_RETIREMENT_SCHEMA = (
    "hepta.p1-paper-canary-durable-owner-retirement-receipt.v4")
OWNER_RETIREMENT_VERSION = 4
HSL8_TERMINAL_PROOF = "HSL8_POST_CUTOFF_SIGNED_TERMINAL_ACK_V3"
PRE_CLEANUP_DIAGNOSTIC_ROLE = "DIAGNOSTIC_ONLY_NOT_TERMINAL_PROOF"
OUTER_COMPLETION_SCHEMA = "hepta.p1-paper-canary-cycle-completion-receipt.v4"
OWNER_PURGE_INTENT_SCHEMA = (
    "hepta.p1-paper-canary-outer-owner-purge-intent.v1")
OWNER_PURGE_RECEIPT_SCHEMA = (
    "hepta.p1-paper-canary-outer-owner-purge-receipt.v1")
OWNER_PURGE_INTENT_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "domain_id", "cycle_id",
    "outer_completion_path", "outer_completion_file_sha256",
    "outer_completion_body_sha256", "root_cleanup_receipt_path",
    "root_cleanup_receipt_file_sha256", "root_cleanup_receipt_body_sha256",
    "owner_retirement_receipt_path", "owner_retirement_receipt_file_sha256",
    "owner_retirement_receipt_body_sha256", "terminal_ack_receipt_sha256",
    "revoke_bearer_path", "revoke_bearer_file_sha256",
    "current_runtime_replay_verified", "paper_only", "live_authorized",
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
NORMAL_ROOT_RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "completed_at_ms", "campaign_id",
    "domain_id", "cycle_id", "cleanup_tool_call_id", "cleanup_command_id",
    "tool_descriptor_sha256", "execution_handoff_path",
    "execution_handoff_file_sha256", "execution_handoff_body_sha256",
    "watch_handoff_file_sha256", "watch_handoff_body_sha256",
    "intent_sha256", "installed_images_sha256", "executor_image_sha256",
    "backend_adapter_image_sha256", "root_finalizer_image_sha256",
    "backend_transform_version", "session_owner_reference_sha256",
    "execution_service_epoch", "execution_service_fencing_generation",
    "journal_path", "journal_sha256", "journal_size",
    "journal_last_sequence", "tool_evidence_sha256",
    "pre_cleanup_evidence_path", "pre_cleanup_evidence_file_sha256",
    "pre_cleanup_evidence_body_sha256", "root_cleanup_request_path",
    "root_cleanup_request_file_sha256", "root_cleanup_request_body_sha256",
    "guardian_request_id", "local_control_transaction_id",
    "local_control_request_sha256", "guardian_active_receipt_file_sha256",
    "guardian_active_receipt_body_sha256", "completed_actions",
    "guardian_stopped", "execution_control_disabled",
    "kill_switch_engaged", "global_kill_switch_engaged", "broker_deny_all",
    "broker_mutation_units_inactive", "broker_mutation_units",
    "broker_mutation_units_sha256", "permit_absent",
    "runtime_session_count", "guardian_runtime_absent",
    "durable_owner_reference_sha256", "durable_owner_count",
    "durable_owner_status", "durable_owner_retirement_receipt_path",
    "durable_owner_retirement_receipt_file_sha256",
    "durable_owner_retirement_receipt_body_sha256",
    "mutation_credentials_destroyed", "credentials_destroyed_scope",
    "retained_root_recovery_bearer_count",
    "retained_root_recovery_bearer_path",
    "retained_root_recovery_bearer_sha256",
    "retained_root_recovery_bearer_mutation_authority",
    "authorized_connector_count", "identity_count",
    "identity_manifest_sha256", "broker_policy_sha256", "paper_only",
    "live_authorized", "authority_granted", "body_sha256",
})
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
RECOVERY_QUERY_RESULT_FIELDS = frozenset({
    "accepted", "reason_code", "lease_generation",
    "authoritative_command_status", "command_id", "command_status",
    "command_reason_code", "order_id", "recovery_only",
    "paper_finalization_required", "owner_fenced",
    "execution_service_epoch", "execution_service_fencing_generation",
    "recovery_expires_at_ms", "owner_audit_authoritative",
    "owner_audit_complete", "owner_active_order_count",
    "owner_uncertain_command_count", "broker_connection_epoch",
    "broker_active_generation", "broker_terminal_generation",
    "owner_account", "owner_execution_domain",
})
FINALIZATION_RESULT_FIELDS = frozenset({
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
})
TERMINAL_ACK_RESULT_FIELDS = frozenset(FINALIZATION_RESULT_FIELDS | {
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
FINALIZATION_RECEIPT_KEYS = (
    "schema", "version", "status", "recovery_id", "finalization_id",
    "expected_owner_set_sha256", "expected_owner_count",
    "owner_set_canonical_hex", "owner_account", "owner_execution_domain",
    "execution_service_epoch", "execution_service_fencing_generation",
    "broker_connection_epoch", "broker_active_generation",
    "broker_terminal_generation", "broker_risk_generation",
    "broker_account_generation", "broker_position_generation",
    "broker_fx_cash_generation", "broker_exposure_generation",
    "broker_terminal_exposure_generation",
    "broker_risk_absorbed_exposure_generation",
    "broker_global_active_order_count", "owner_active_order_count",
    "owner_uncertain_command_count",
    "broker_post_fill_risk_reconciliation_pending",
    "broker_recovery_audit_barrier_complete",
    "broker_recovery_audit_new_connection_epoch_required",
    "broker_position_quantity", "broker_gross_absolute_position",
    "paper_only", "live_authorized",
)
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

# HPE1 is deliberately parsed here instead of importing the privileged
# witness producer.  The terminal prover is a separate root trust boundary:
# accepting a receipt hash alone would let a producer rewrite both the
# receipt and its hash without proving that the independently durable witness
# contains the same provenance.
TERMINAL_EVIDENCE_KEYS = (
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
    "active_orders_complete", "completed_orders_complete", "executions_complete",
    "positions_complete", "cash_fx_complete", "risk_complete",
    "known_mutation_command_set_sha256", "known_mutation_command_count",
    "known_correlation_set_sha256", "known_correlation_count",
    "all_known_mutation_commands_settled", "settled_mutation_command_count",
    "unknown_mutation_command_count", "unresolved_mutation_command_count",
    "unknown_active_order_count", "active_order_count", "position_count",
    "nonzero_cash_fx_count", "gross_absolute_position", "gross_fx_exposure",
    "gross_risk", "mutation_connector_count", "broker_socket_count",
    "broker_process_count", "broker_credential_count",
    "execution_service_inactive", "paper_units_inactive",
    "execution_mutation_gate_closed", "broker_transport_connected",
    "broker_reconnect_permitted", "read_only_authority", "mutation_attempted",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized",
    "order_authorized", "paper_only", "authority_granted",
    "terminal_external_halt_latch_durable", "terminal_witness_durable",
    "current_host_boundary_verified", "evidence_body_sha256",
)

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


class ProverError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ProverError("TERMINAL_PROVER_CANONICALIZATION_FAILED") from error


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
            raise ProverError("TERMINAL_PROVER_FILE_METADATA_INVALID")
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
        raise ProverError("TERMINAL_PROVER_FILE_UNAVAILABLE") from error
    if (
            len(payload) > maximum or _identity(before) != _identity(opened) or
            _identity(opened) != _identity(after)):
        raise ProverError("TERMINAL_PROVER_FILE_CHANGED")
    return bytes(payload)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ProverError("TERMINAL_PROVER_DURABLE_WRITE_FAILED") from error


def _publish_or_same(path: Path, raw: bytes) -> None:
    if path.exists() or path.is_symlink():
        if stable_read(path, uid=0, gid=0, mode=0o600) != raw:
            raise ProverError("TERMINAL_PROVER_DURABLE_ARTIFACT_CONFLICT")
        return
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            os.fchown(descriptor, 0, 0)
            offset = 0
            while offset < len(raw):
                offset += os.write(descriptor, raw[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(path.parent)
    except FileExistsError:
        if stable_read(path, uid=0, gid=0, mode=0o600) != raw:
            raise ProverError("TERMINAL_PROVER_DURABLE_ARTIFACT_CONFLICT")
    except OSError as error:
        raise ProverError("TERMINAL_PROVER_DURABLE_WRITE_FAILED") from error


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def strict_json(raw: bytes, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("ascii"), object_pairs_hook=_pairs,
            parse_float=lambda _value: (_ for _ in ()).throw(ValueError()),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ProverError(reason) from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ProverError(reason)
    return value


def sealed(document: dict[str, Any], reason: str) -> str:
    claimed = document.get("body_sha256")
    if not isinstance(claimed, str) or DIGEST.fullmatch(claimed) is None:
        raise ProverError(reason)
    body = dict(document)
    del body["body_sha256"]
    if sha(canonical_json(body)) != claimed:
        raise ProverError(reason)
    return claimed


def _exact(value: Any, fields: frozenset[str], reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ProverError(reason)
    return value


def _digest(value: Any, reason: str) -> str:
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise ProverError(reason)
    return value


def _ordered_receipt(
        value: Any, keys: tuple[str, ...], failure: str, *,
        maximum: int = 4096,
) -> tuple[dict[str, str], bytes]:
    if not isinstance(value, str):
        raise ProverError(failure)
    try:
        raw = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ProverError(failure) from error
    if (not 1 <= len(raw) <= maximum or not raw.endswith(b"\n") or
            b"\r" in raw or b"\x00" in raw):
        raise ProverError(failure)
    rows = raw[:-1].split(b"\n")
    if len(rows) != len(keys):
        raise ProverError(failure)
    receipt: dict[str, str] = {}
    for row, expected in zip(rows, keys, strict=True):
        key, separator, raw_value = row.partition(b"=")
        try:
            decoded_key = key.decode("ascii")
            decoded_value = raw_value.decode("ascii")
        except UnicodeDecodeError as error:
            raise ProverError(failure) from error
        if separator != b"=" or decoded_key != expected:
            raise ProverError(failure)
        receipt[decoded_key] = decoded_value
    return receipt, raw


def _finalization_receipt(value: Any) -> tuple[dict[str, str], bytes]:
    return _ordered_receipt(
        value, FINALIZATION_RECEIPT_KEYS,
        "TERMINAL_PROVER_HSL8_PRELIMINARY_INVALID")


def _terminal_ack_receipt(value: Any) -> tuple[dict[str, str], bytes]:
    return _ordered_receipt(
        value, TERMINAL_ACK_RECEIPT_KEYS,
        "TERMINAL_PROVER_HSL8_TERMINAL_ACK_INVALID", maximum=12288)


def _parse_terminal_evidence(
        evidence: Any,
) -> tuple[dict[str, str], bytes, bytes]:
    """Parse the independent HPE1 stable terminal witness.

    This intentionally duplicates the small wire parser rather than loading
    the privileged producer.  The final ``evidence_body_sha256`` line covers
    the complete prefix, including the HPE1 header.
    """
    failure = "TERMINAL_PROVER_HSL8_TERMINAL_ACK_INVALID"
    if not isinstance(evidence, (bytes, bytearray, memoryview)):
        raise ProverError(failure)
    raw = bytes(evidence)
    if not 1 <= len(raw) <= 12288:
        raise ProverError(failure)
    try:
        raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise ProverError(failure) from error
    rows = raw.splitlines(keepends=True)
    if (len(rows) != len(TERMINAL_EVIDENCE_KEYS) + 1 or
            rows[0] != b"HPE1\n" or
            any(not row.endswith(b"\n") or b"\r" in row for row in rows)):
        raise ProverError(failure)
    values: dict[str, str] = {}
    for row, expected_key in zip(
            rows[1:], TERMINAL_EVIDENCE_KEYS, strict=True):
        prefix = (expected_key + "=").encode("ascii")
        if (not row.startswith(prefix) or row == prefix + b"\n" or
                b"=" in row[len(prefix):-1] or expected_key in values):
            raise ProverError(failure)
        try:
            decoded = row[len(prefix):-1].decode("ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise ProverError(failure) from error
        if not decoded:
            raise ProverError(failure)
        values[expected_key] = decoded
    if tuple(values) != TERMINAL_EVIDENCE_KEYS:
        raise ProverError(failure)
    prefix = b"".join(rows[:-1])
    body = values["evidence_body_sha256"]
    if DIGEST.fullmatch(body) is None or body != sha(prefix):
        raise ProverError(failure)
    return values, raw, prefix


def _current_terminal_evidence() -> bytes:
    """Read and syntactically validate the current root-owned HPE1 witness."""
    raw = stable_read(
        TERMINAL_EVIDENCE_PATH, uid=0, gid=0, mode=0o400, maximum=12288)
    _parse_terminal_evidence(raw)
    return raw


def _validate_terminal_evidence_binding(
        evidence_raw: bytes, *, receipt: dict[str, str],
        result: dict[str, Any], checkpoint: dict[str, Any],
) -> None:
    """Bind every repeated HSL8 provenance field to exact HPE1 bytes."""
    failure = "TERMINAL_PROVER_HSL8_TERMINAL_ACK_INVALID"
    evidence, exact, _prefix = _parse_terminal_evidence(evidence_raw)
    file_digest = sha(exact)
    body_digest = evidence["evidence_body_sha256"]
    if (result.get("terminal_evidence_sha256") != file_digest or
            receipt.get("terminal_evidence_file_sha256") != file_digest or
            result.get("terminal_evidence_body_sha256") != body_digest or
            receipt.get("terminal_evidence_body_sha256") != body_digest):
        raise ProverError(failure)
    identity = {
        "schema": "hepta.paper-terminal-witness-evidence.v1",
        "version": "1",
        "status": "CURRENT_POST_CUTOFF_TERMINAL_WITNESS_VERIFIED",
        "terminal_proof_kind": "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1",
        "recovery_id": str(checkpoint.get("recovery_id", "")),
        "finalization_id": str(checkpoint.get("finalization_id", "")),
        "campaign_id": str(checkpoint.get("campaign_id", "")),
        "cycle_id": str(checkpoint.get("cycle_id", "")),
    }
    if any(evidence.get(key) != expected
           for key, expected in identity.items()):
        raise ProverError(failure)
    for field in TERMINAL_ACK_RECEIPT_KEYS:
        if field in {
                "schema", "version", "status",
                "terminal_evidence_file_sha256",
                "terminal_evidence_body_sha256"}:
            continue
        if field in TERMINAL_EVIDENCE_KEYS and receipt.get(field) != evidence.get(field):
            raise ProverError(failure)
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
    if (any(evidence.get(field) != "1" for field in required_true) or
            any(evidence.get(field) != "0" for field in required_false)):
        raise ProverError(failure)


def _receipt_unsigned(value: Any, failure: str) -> int:
    if (not isinstance(value, str) or
            re.fullmatch(r"0|[1-9][0-9]*", value) is None):
        raise ProverError(failure)
    try:
        return int(value)
    except ValueError as error:
        raise ProverError(failure) from error


def _validate_terminal_ack_receipt(
        value: Any, receipt_sha256: Any, evidence: dict[str, Any],
        root_receipt: dict[str, Any], recovery: dict[str, Any],
        preliminary: dict[str, Any],
) -> dict[str, str]:
    failure = "TERMINAL_PROVER_HSL8_TERMINAL_ACK_INVALID"
    receipt, raw = _terminal_ack_receipt(value)
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
    if (
            _digest(receipt_sha256, failure) != sha(raw) or
            receipt["schema"] !=
                "hepta.paper-session-terminal-ack-receipt.v3" or
            receipt["version"] != "3" or
            receipt["status"] != "TERMINAL_ACKED" or
            receipt["terminal_proof_kind"] !=
                "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" or
            receipt["recovery_id"] != evidence["recovery_id"] or
            receipt["finalization_id"] != evidence["finalization_id"] or
            receipt["campaign_id"] != evidence["campaign_id"] or
            receipt["cycle_id"] != evidence["cycle_id"] or
            receipt["expected_owner_set_sha256"] !=
                evidence["expected_owner_set_sha256"] or
            receipt["expected_owner_count"] != "1" or
            receipt["owner_set_canonical_hex"] !=
                evidence["owner_set_canonical_hex"] or
            receipt["preliminary_finalization_receipt_sha256"] !=
                preliminary["finalization_receipt_sha256"] or
            receipt["owner_agent_id"] != "hepta-agent-alpha" or
            receipt["owner_session_id"] != evidence["session_id"] or
            receipt["owner_account"] != recovery["owner_account"] or
            receipt["owner_execution_domain"] !=
                recovery["owner_execution_domain"] or
            receipt["account_id_sha256"] != sha(
                recovery["owner_account"].encode("ascii")) or
            receipt["execution_service_epoch"] !=
                root_receipt["execution_service_epoch"] or
            receipt["execution_service_fencing_generation"] != str(
                root_receipt["execution_service_fencing_generation"]) or
            receipt["recovery_ingress_fence"] != str(
                evidence["lease_generation"]) or
            receipt["terminalization_generation"] != "1" or
            receipt["provider_capability"] !=
                "ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1" or
            receipt["snapshot_consistency"] not in {
                "ATOMIC_ACCOUNT", "CAUSAL_WATERMARK"} or
            any(DIGEST.fullmatch(receipt[field]) is None or
                receipt[field] == "sha256:" + "0" * 64
                for field in digest_fields) or
            any(receipt[field] != "1" for field in truth_fields) or
            any(receipt[field] != "0" for field in false_fields | zero_fields)):
        raise ProverError(failure)
    positive = {
        "execution_service_fencing_generation", "recovery_ingress_fence",
        "terminalization_generation", "egress_publisher_pid",
        "egress_publisher_start_ticks", "egress_policy_generation",
    }
    if (any(_receipt_unsigned(receipt[field], failure) < 1
            for field in positive) or
            _receipt_unsigned(receipt["known_mutation_command_count"], failure) >
                4096 or
            _receipt_unsigned(receipt["known_correlation_count"], failure) >
                4096 or
            _receipt_unsigned(receipt["settled_mutation_command_count"], failure) !=
                _receipt_unsigned(
                    receipt["known_mutation_command_count"], failure) or
            IDENTIFIER.fullmatch(receipt["provider_id"]) is None or
            re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}",
                receipt["host_boot_id"]) is None):
        raise ProverError(failure)
    return receipt


def _validate_recovery_query(
        value: Any, evidence: dict[str, Any], receipt: dict[str, Any],
) -> dict[str, Any]:
    failure = "TERMINAL_PROVER_HSL8_RECOVERY_QUERY_INVALID"
    result = _exact(value, RECOVERY_QUERY_RESULT_FIELDS, failure)
    boolean_fields = {
        "accepted", "authoritative_command_status", "recovery_only",
        "paper_finalization_required", "owner_fenced",
        "owner_audit_authoritative", "owner_audit_complete",
    }
    integer_fields = {
        "lease_generation", "order_id",
        "execution_service_fencing_generation", "recovery_expires_at_ms",
        "owner_active_order_count", "owner_uncertain_command_count",
        "broker_connection_epoch", "broker_active_generation",
        "broker_terminal_generation",
    }
    if (any(type(result[field]) is not bool for field in boolean_fields) or
            any(type(result[field]) is not int for field in integer_fields) or
            any(not isinstance(result[field], str)
                for field in set(result) - boolean_fields - integer_fields)):
        raise ProverError(failure)
    if (
            result["accepted"] is not True or
            result["reason_code"] !=
                "RECOVERY_QUERY_NOT_FOUND_PROVEN_RECOVERY_ONLY" or
            result["lease_generation"] != evidence["lease_generation"] or
            result["authoritative_command_status"] is not True or
            result["command_id"] != evidence["query_command_id"] or
            result["command_status"] != "not_found" or
            result["command_reason_code"] != "EXECUTION_COMMAND_NOT_FOUND" or
            result["order_id"] != -1 or result["recovery_only"] is not True or
            result["paper_finalization_required"] is not True or
            result["owner_fenced"] is not False or
            result["owner_audit_authoritative"] is not True or
            result["owner_audit_complete"] is not True or
            result["owner_active_order_count"] != 0 or
            result["owner_uncertain_command_count"] != 0 or
            result["execution_service_epoch"] !=
                receipt["execution_service_epoch"] or
            result["execution_service_fencing_generation"] !=
                receipt["execution_service_fencing_generation"] or
            any(result[field] < 1 for field in (
                "recovery_expires_at_ms", "broker_connection_epoch",
                "broker_active_generation", "broker_terminal_generation")) or
            not isinstance(result["owner_account"], str) or
            re.fullmatch(r"DU[0-9]{1,16}", result["owner_account"]) is None or
            result["owner_execution_domain"] != "PAPER:alpha"):
        raise ProverError(failure)
    return result


def _validate_finalization_result(
        value: Any, evidence: dict[str, Any], root_receipt: dict[str, Any],
        recovery: dict[str, Any], *, state: str,
) -> dict[str, Any]:
    failure = "TERMINAL_PROVER_HSL8_PRELIMINARY_INVALID"
    result = _exact(value, FINALIZATION_RESULT_FIELDS, failure)
    boolean_fields = {
        "accepted", "paper_finalization_required",
        "owner_audit_authoritative", "owner_audit_complete",
        "broker_post_fill_risk_reconciliation_pending",
        "broker_recovery_audit_barrier_complete",
        "broker_recovery_audit_new_connection_epoch_required",
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
    }
    if (state != "AUDIT_SEALED" or
            any(type(result[field]) is not bool for field in boolean_fields) or
            any(type(result[field]) is not int for field in integer_fields) or
            any(not isinstance(result[field], str)
                for field in set(result) - boolean_fields - integer_fields)):
        raise ProverError(failure)
    if (
            result["accepted"] is not True or
            result["reason_code"] != "PAPER_FINALIZATION_AUDIT_SEALED" or
            result["paper_finalization_state"] != "AUDIT_SEALED" or
            result["paper_finalization_required"] is not True or
            result["recovery_id"] != evidence["recovery_id"] or
            result["finalization_id"] != evidence["finalization_id"] or
            result["expected_owner_set_sha256"] !=
                evidence["expected_owner_set_sha256"] or
            result["expected_owner_count"] != 1 or
            result["owner_token_sha256"] != evidence["owner_token_sha256"] or
            result["lease_generation"] != evidence["lease_generation"] or
            result["owner_account"] != recovery["owner_account"] or
            result["owner_execution_domain"] !=
                recovery["owner_execution_domain"] or
            result["execution_service_epoch"] !=
                root_receipt["execution_service_epoch"] or
            result["execution_service_fencing_generation"] !=
                root_receipt["execution_service_fencing_generation"] or
            result["execution_service_epoch"] !=
                recovery["execution_service_epoch"] or
            result["execution_service_fencing_generation"] !=
                recovery["execution_service_fencing_generation"]):
        raise ProverError(failure)
    parsed, raw = _finalization_receipt(result["finalization_receipt"])
    if (
            _digest(result["finalization_receipt_sha256"], failure) != sha(raw) or
            parsed["schema"] !=
                "hepta.paper-session-finalization-receipt.v1" or
            parsed["version"] != "1" or parsed["status"] != "AUDIT_SEALED" or
            parsed["recovery_id"] != evidence["recovery_id"] or
            parsed["finalization_id"] != evidence["finalization_id"] or
            parsed["expected_owner_set_sha256"] !=
                evidence["expected_owner_set_sha256"] or
            parsed["expected_owner_count"] != "1" or
            parsed["owner_set_canonical_hex"] !=
                evidence["owner_set_canonical_hex"] or
            parsed["owner_account"] != recovery["owner_account"] or
            parsed["owner_execution_domain"] !=
                recovery["owner_execution_domain"] or
            parsed["execution_service_epoch"] !=
                root_receipt["execution_service_epoch"] or
            parsed["broker_position_quantity"] !=
                result["broker_position_quantity"] or
            parsed["broker_gross_absolute_position"] !=
                result["broker_gross_absolute_position"] or
            parsed["paper_only"] != "1" or
            parsed["live_authorized"] != "0"):
        raise ProverError(failure)
    paired_integers = integer_fields - {"lease_generation", "expected_owner_count"}
    if any(parsed[field] != str(result[field]) for field in paired_integers):
        raise ProverError(failure)
    positive = {
        "execution_service_fencing_generation", "broker_connection_epoch",
        "broker_active_generation", "broker_terminal_generation",
        "broker_risk_generation", "broker_account_generation",
        "broker_position_generation", "broker_fx_cash_generation",
    }
    if (
            any(result[field] < 1 for field in positive) or
            result["broker_exposure_generation"] < 0 or
            result["broker_terminal_exposure_generation"] < 0 or
            result["broker_risk_absorbed_exposure_generation"] < 0 or
            result["broker_terminal_exposure_generation"] >
                result["broker_risk_absorbed_exposure_generation"] or
            result["broker_risk_absorbed_exposure_generation"] !=
                result["broker_exposure_generation"] or
            any(result[field] != 0 for field in (
                "owner_active_order_count", "owner_uncertain_command_count",
                "broker_global_active_order_count")) or
            result["owner_audit_authoritative"] is not True or
            result["owner_audit_complete"] is not True or
            result["broker_post_fill_risk_reconciliation_pending"] is not
                False or
            result["broker_recovery_audit_barrier_complete"] is not True or
            result["broker_recovery_audit_new_connection_epoch_required"] is
                not False or
            result["broker_position_quantity"] != "0" or
            result["broker_gross_absolute_position"] != "0" or
            parsed["broker_post_fill_risk_reconciliation_pending"] != "0" or
            parsed["broker_recovery_audit_barrier_complete"] != "1" or
            parsed["broker_recovery_audit_new_connection_epoch_required"] !=
                "0"):
        raise ProverError(failure)
    return result


def _validate_terminal_ack_result(
        value: Any, evidence: dict[str, Any], root_receipt: dict[str, Any],
        recovery: dict[str, Any], preliminary: dict[str, Any], *,
        terminal_evidence_raw: bytes | None = None,
) -> dict[str, Any]:
    failure = "TERMINAL_PROVER_HSL8_TERMINAL_ACK_INVALID"
    result = _exact(value, TERMINAL_ACK_RESULT_FIELDS, failure)
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
    if (any(type(result[field]) is not bool for field in boolean_fields) or
            any(type(result[field]) is not int for field in integer_fields) or
            any(not isinstance(result[field], str)
                for field in set(result) - boolean_fields - integer_fields)):
        raise ProverError(failure)
    if (
            result["accepted"] is not True or
            result["reason_code"] != "PAPER_FINALIZATION_TERMINAL_ACKED" or
            result["paper_finalization_state"] != "ACKED" or
            result["paper_finalization_required"] is not True or
            result["recovery_id"] != evidence["recovery_id"] or
            result["finalization_id"] != evidence["finalization_id"] or
            result["expected_owner_set_sha256"] !=
                evidence["expected_owner_set_sha256"] or
            result["expected_owner_count"] != 1 or
            result["owner_token_sha256"] != evidence["owner_token_sha256"] or
            result["lease_generation"] != evidence["lease_generation"] or
            result["preliminary_finalization_receipt_sha256"] !=
                preliminary["finalization_receipt_sha256"] or
            result["finalization_receipt_sha256"] ==
                preliminary["finalization_receipt_sha256"] or
            result["owner_account"] != recovery["owner_account"] or
            result["owner_execution_domain"] !=
                recovery["owner_execution_domain"] or
            result["terminalization_service_epoch"] !=
                root_receipt["execution_service_epoch"] or
            result["terminalization_service_fencing_generation"] !=
                root_receipt["execution_service_fencing_generation"] or
            result["terminalization_generation"] != 1 or
            result["execution_service_epoch"] !=
                root_receipt["execution_service_epoch"] or
            result["execution_service_fencing_generation"] !=
                root_receipt["execution_service_fencing_generation"] or
            not isinstance(result["execution_service_epoch"], str) or
            IDENTIFIER.fullmatch(result["execution_service_epoch"]) is None or
            result["owner_audit_authoritative"] is not True or
            result["owner_audit_complete"] is not True or
            result["execution_mutation_gate_closed"] is not True or
            result["broker_transport_connected"] is not False or
            result["broker_event_ingress_halted"] is not True or
            result["broker_callback_queue_drained"] is not False or
            result["broker_callbacks_in_flight"] != 0 or
            result["broker_reconnect_permitted"] is not False or
            result["terminal_latch_durable"] is not True or
            result["terminal_runtime_latch_loaded"] is not False or
            result["terminal_runtime_verified"] is not False or
            result["terminal_external_latch_loaded"] is not True or
            result["terminal_current_evidence_verified"] is not True or
            result["terminal_proof_kind"] !=
                "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" or
            any(result[field] != 0 for field in (
                "broker_connection_epoch", "broker_active_generation",
                "broker_terminal_generation", "broker_risk_generation",
                "broker_account_generation", "broker_position_generation",
                "broker_fx_cash_generation", "broker_exposure_generation",
                "broker_terminal_exposure_generation",
                "broker_risk_absorbed_exposure_generation",
                "broker_global_active_order_count", "owner_active_order_count",
                "owner_uncertain_command_count")) or
            result["broker_post_fill_risk_reconciliation_pending"] is not
                False or
            result["broker_recovery_audit_barrier_complete"] is not False or
            result["broker_recovery_audit_new_connection_epoch_required"] is
                not False or
            result["broker_position_quantity"] != "0" or
            result["broker_gross_absolute_position"] != "0" or
            result["egress_publisher_pid"] < 1 or
            result["egress_publisher_start_ticks"] < 1 or
            result["terminal_replay"] is not True):
        raise ProverError(failure)
    receipt = _validate_terminal_ack_receipt(
        result["finalization_receipt"],
        result["finalization_receipt_sha256"], evidence, root_receipt,
        recovery, preliminary)
    if (
            result["terminalization_generation"] != _receipt_unsigned(
                receipt["terminalization_generation"], failure) or
            result["terminal_latch_sha256"] !=
                receipt["terminalizing_latch_sha256"] or
            result["terminal_external_halt_latch_sha256"] !=
                receipt["terminal_external_halt_latch_sha256"] or
            result["transport_cutoff_receipt_file_sha256"] !=
                receipt["transport_cutoff_receipt_file_sha256"] or
            result["transport_cutoff_receipt_body_sha256"] !=
                receipt["transport_cutoff_receipt_body_sha256"] or
            result["post_cutoff_terminal_witness_file_sha256"] !=
                receipt["post_cutoff_terminal_witness_file_sha256"] or
            result["post_cutoff_terminal_witness_body_sha256"] !=
                receipt["post_cutoff_terminal_witness_body_sha256"] or
            result["terminal_evidence_sha256"] !=
                receipt["terminal_evidence_file_sha256"] or
            result["terminal_evidence_body_sha256"] !=
                receipt["terminal_evidence_body_sha256"] or
            result["egress_policy_sha256"] != receipt["egress_policy_sha256"] or
            str(result["egress_publisher_pid"]) !=
                receipt["egress_publisher_pid"] or
            str(result["egress_publisher_start_ticks"]) !=
                receipt["egress_publisher_start_ticks"] or
            result["provider_trust_policy_body_sha256"] !=
                receipt["provider_trust_policy_body_sha256"] or
            result["signed_account_signature_sha256"] !=
                receipt["signed_account_signature_sha256"]):
        raise ProverError(failure)
    if terminal_evidence_raw is None:
        raise ProverError(failure)
    _validate_terminal_evidence_binding(
        terminal_evidence_raw, receipt=receipt, result=result,
        checkpoint={
            "recovery_id": evidence["recovery_id"],
            "finalization_id": evidence["finalization_id"],
            "campaign_id": evidence["campaign_id"],
            "cycle_id": evidence["cycle_id"],
        })
    return result


def _validate_hsl8_retirement(
        evidence: dict[str, Any], root_receipt: dict[str, Any],
) -> None:
    failure = "TERMINAL_PROVER_OWNER_EVIDENCE_INVALID"
    _exact(evidence, OWNER_RETIREMENT_FIELDS, failure)
    if (
            evidence["schema"] != OWNER_RETIREMENT_SCHEMA or
            evidence["version"] != OWNER_RETIREMENT_VERSION or
            evidence["campaign_id"] != root_receipt["campaign_id"] or
            evidence["cycle_id"] != root_receipt["cycle_id"] or
            evidence["domain_id"] != "alpha" or
            evidence["cleanup_command_id"] !=
                root_receipt["cleanup_command_id"] or
            evidence["query_command_id"] != evidence["cleanup_command_id"] or
            evidence["session_owner_reference_sha256"] !=
                root_receipt["durable_owner_reference_sha256"] or
            evidence["token_sha256"] != evidence["owner_token_sha256"] or
            evidence["revoke_bearer_file_sha256"] !=
                evidence["token_sha256"] or
            evidence["expected_owner_count"] != 1 or
            evidence["paper_finalization_required"] is not True or
            evidence["terminal_acknowledged"] is not True or
            evidence["hsl_owner_purged"] is not True or
            evidence["broker_flat_proven"] is not True or
            evidence["durable_hsl_audit"] != HSL8_TERMINAL_PROOF or
            evidence["terminal_flat_proof_kind"] != HSL8_TERMINAL_PROOF or
            evidence["pre_cleanup_flat_evidence_role"] !=
                PRE_CLEANUP_DIAGNOSTIC_ROLE or
            evidence["credentials_destroyed"] is not True or
            evidence["mutation_credentials_destroyed"] is not True or
            evidence["credentials_destroyed_scope"] !=
                "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY" or
            evidence["retained_root_recovery_bearer_count"] != 1 or
            evidence["retained_root_recovery_bearer_path"] !=
                evidence["revoke_bearer_path"] or
            evidence["retained_root_recovery_bearer_sha256"] !=
                evidence["revoke_bearer_file_sha256"] or
            evidence[
                "retained_root_recovery_bearer_mutation_authority"] is not
                False or
            evidence["runtime_session_count"] != 0 or
            evidence["durable_owner_count"] != 0 or
            evidence["durable_owner_status"] != "RETIRED" or
            evidence["paper_only"] is not True or
            evidence["live_authorized"] is not False or
            evidence["authority_granted"] is not False or
            type(evidence["completed_at_ms"]) is not int or
            evidence["completed_at_ms"] < 1 or
            not isinstance(evidence["recovery_id"], str) or
            IDENTIFIER.fullmatch(evidence["recovery_id"]) is None or
            not isinstance(evidence["finalization_id"], str) or
            IDENTIFIER.fullmatch(evidence["finalization_id"]) is None or
            not isinstance(evidence["owner_set_canonical_hex"], str) or
            evidence["authority_path"] != str(
                OWNER_ROOT / "session.token.authority.json") or
            evidence["revoke_bearer_path"] != str(
                OWNER_ROOT / "session.token.revoke-token") or
            type(evidence["lease_generation"]) is not int or
            evidence["lease_generation"] < 1 or
            not isinstance(evidence["session_id"], str) or
            IDENTIFIER.fullmatch(evidence["session_id"]) is None):
        raise ProverError(failure)
    for field in (
            "session_owner_reference_sha256", "token_sha256",
            "owner_token_sha256", "expected_owner_set_sha256",
            "finalization_receipt_sha256", "terminal_ack_receipt_sha256",
            "authority_file_sha256",
            "authority_body_sha256", "revoke_bearer_file_sha256"):
        _digest(evidence[field], failure)
    recovery = _validate_recovery_query(
        evidence["recovery_query_result"], evidence, root_receipt)
    try:
        canonical = (
            f"{evidence['owner_token_sha256']}\t"
            f"{evidence['lease_generation']}\t"
            f"{recovery['owner_account'].encode('utf-8').hex()}\t"
            f"{recovery['owner_execution_domain'].encode('utf-8').hex()}\n"
        ).encode("ascii")
    except UnicodeEncodeError as error:
        raise ProverError(failure) from error
    try:
        expected_finalization_id = "paper-finalization-" + hashlib.sha256((
            evidence["recovery_id"] + "\n" +
            evidence["expected_owner_set_sha256"] + "\n1\n").encode(
                "ascii")).hexdigest()[:32]
    except UnicodeEncodeError as error:
        raise ProverError(failure) from error
    if (
            sha(canonical) != evidence["expected_owner_set_sha256"] or
            canonical.hex() != evidence["owner_set_canonical_hex"] or
            evidence["finalization_id"] != expected_finalization_id):
        raise ProverError(failure)
    sealed_result = _validate_finalization_result(
        evidence["finalization_result"], evidence, root_receipt, recovery,
        state="AUDIT_SEALED")
    try:
        terminal_evidence_raw = _current_terminal_evidence()
    except ProverError as error:
        raise ProverError(failure) from error
    acknowledged = _validate_terminal_ack_result(
        evidence["terminal_ack_result"], evidence, root_receipt, recovery,
        sealed_result, terminal_evidence_raw=terminal_evidence_raw)
    if (
            sealed_result["finalization_receipt_sha256"] !=
                evidence["finalization_receipt_sha256"] or
            sealed_result["finalization_receipt"] !=
                evidence["finalization_receipt"] or
            acknowledged["finalization_receipt_sha256"] !=
                evidence["terminal_ack_receipt_sha256"] or
            acknowledged["finalization_receipt"] !=
                evidence["terminal_ack_receipt"] or
            evidence["terminal_ack_receipt_sha256"] ==
                evidence["finalization_receipt_sha256"]):
        raise ProverError(failure)


def _fresh_terminal_replay(
        evidence: dict[str, Any], root_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Revalidate the durable terminal latch against the current runtime."""
    failure = "TERMINAL_PROVER_CURRENT_RUNTIME_REPLAY_REQUIRED"
    _validate_hsl8_retirement(evidence, root_receipt)
    bearer_path = Path(evidence["revoke_bearer_path"])
    if bearer_path != OWNER_ROOT / "session.token.revoke-token":
        raise ProverError(failure)
    bearer = stable_read(
        bearer_path, uid=0, gid=0, mode=0o600, maximum=65)
    if (
            len(bearer) != 65 or
            re.fullmatch(rb"[0-9a-f]{64}\n", bearer) is None or
            sha(bearer) != evidence["revoke_bearer_file_sha256"]):
        raise ProverError(failure)
    try:
        terminal_evidence_raw = _current_terminal_evidence()
    except ProverError as error:
        raise ProverError(failure) from error
    arguments = [
        str(SESSIONCTL), "--socket", str(SESSION_SUPERVISOR_SOCKET),
        "--io-timeout-ms", "25000", "paper-terminal-witness-ack",
        "--token-file", str(bearer_path), "--generation",
        str(evidence["lease_generation"]), "--recovery-id",
        evidence["recovery_id"], "--finalization-id",
        evidence["finalization_id"], "--expected-owner-set-sha256",
        evidence["expected_owner_set_sha256"], "--expected-owner-count",
        str(evidence["expected_owner_count"]), "--receipt-sha256",
        evidence["finalization_receipt_sha256"],
        "--terminal-evidence-file", str(TERMINAL_EVIDENCE_PATH),
        "--terminal-evidence-sha256", sha(terminal_evidence_raw),
        "--token-owner-uid", "0",
    ]
    try:
        completed = subprocess.run(
            arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd="/", env={"LC_ALL": "C"},
            close_fds=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProverError(failure) from error
    if completed.returncode != 0:
        raise ProverError(failure)
    response = strict_json(completed.stdout, failure)
    recovery = _validate_recovery_query(
        evidence["recovery_query_result"], evidence, root_receipt)
    preliminary = _validate_finalization_result(
        evidence["finalization_result"], evidence, root_receipt, recovery,
        state="AUDIT_SEALED")
    replayed = _validate_terminal_ack_result(
        response, evidence, root_receipt, recovery, preliminary,
        terminal_evidence_raw=terminal_evidence_raw)
    if replayed != evidence["terminal_ack_result"]:
        raise ProverError(failure)
    return replayed


def _credential_path(name: str) -> Path:
    directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
    if not directory or not Path(directory).is_absolute():
        raise ProverError("TERMINAL_PROVER_CREDENTIAL_DIRECTORY_MISSING")
    return Path(directory) / name


def _load_control() -> ModuleType:
    # Both the prover and control image are copied by systemd LoadCredential.
    # Equality with the fixed root-owned installed bytes closes pathname swaps
    # for the entire coordinator service lifetime.
    installed_self = stable_read(
        INSTALLED_SELF, uid=0, gid=0, mode=0o755, maximum=4 * MAX_BYTES)
    credential_self = stable_read(
        Path(__file__), uid=0, gid=0, mode=0o400, maximum=4 * MAX_BYTES)
    if credential_self != installed_self:
        raise ProverError("TERMINAL_PROVER_SELF_CREDENTIAL_MISMATCH")
    installed = stable_read(
        INSTALLED_CONTROL, uid=0, gid=0, mode=0o755, maximum=4 * MAX_BYTES)
    credential = stable_read(
        _credential_path(CONTROL_CREDENTIAL), uid=0, gid=0, mode=0o400,
        maximum=4 * MAX_BYTES)
    if credential != installed:
        raise ProverError("TERMINAL_PROVER_CONTROL_CREDENTIAL_MISMATCH")
    module = ModuleType("_hepta_p1_canary_terminal_local_control")
    module.__file__ = str(_credential_path(CONTROL_CREDENTIAL))
    sys.modules[module.__name__] = module
    try:
        exec(compile(credential, module.__file__, "exec"), module.__dict__)
    except Exception as error:
        raise ProverError("TERMINAL_PROVER_CONTROL_IMAGE_INVALID") from error
    return module


def _policy(campaign: str) -> tuple[dict[str, Any], bytes]:
    raw = stable_read(POLICY_PATH, uid=0, gid=0, mode=0o600)
    value = strict_json(raw, "TERMINAL_PROVER_POLICY_INVALID")
    if (
            value.get("schema") != "hepta.ib-paper-campaign-policy.v5" or
            value.get("version") != 5 or value.get("campaign_id") != campaign or
            value.get("domain_id") != "alpha" or
            value.get("admission_mode") != "external-p1-finalized" or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            value.get("max_cycles") != 1 or value.get("max_quantity") != 1 or
            value.get("max_active_orders") != 1 or
            value.get("order_type") != "LMT" or value.get("tif") != "DAY" or
            value.get("allowed_instruments") != ["EUR.USD"] or
            value.get("watch_handoff_receipt_path") !=
                "/var/lib/hepta/p1-admission/"
                "p1-watch-to-paper-handoff-receipt-v2.json"):
        raise ProverError("TERMINAL_PROVER_POLICY_INVALID")
    for field in (
            "source_baseline_sha256", "watch_handoff_receipt_file_sha256",
            "watch_handoff_receipt_body_sha256",
            "p1_audit_receipt_file_sha256", "p1_audit_receipt_body_sha256",
            "admission_receipt_file_sha256", "admission_receipt_body_sha256"):
        item = value.get(field)
        if (
                not isinstance(item, str) or DIGEST.fullmatch(item) is None or
                item == "sha256:" + "0" * 64):
            raise ProverError("TERMINAL_PROVER_POLICY_INVALID")
    return value, raw


def _verify_external(control: ModuleType, policy: dict[str, Any], *,
                     fresh: bool, inert: bool, residue_absent: bool) -> dict[str, Any]:
    try:
        return control.verify_external_p1(
            handoff_path=control.EXTERNAL_P1_HANDOFF_PATH,
            expected_file_sha256=policy[
                "watch_handoff_receipt_file_sha256"],
            expected_body_sha256=policy[
                "watch_handoff_receipt_body_sha256"],
            campaign_id=policy["campaign_id"],
            source_baseline_sha256=policy["source_baseline_sha256"],
            require_fresh=fresh, require_inert=inert,
            require_residue_absent=residue_absent)
    except Exception as error:
        raise ProverError("TERMINAL_PROVER_EXTERNAL_BOUNDARY_INVALID") from error


def _systemctl(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["/usr/bin/systemctl", *arguments], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/",
            env={"LC_ALL": "C"}, close_fds=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProverError("TERMINAL_PROVER_SYSTEMCTL_FAILED") from error


def _unit_inactive(unit: str) -> bool:
    completed = _systemctl(
        "show", unit, "-p", "LoadState", "-p", "ActiveState", "-p", "Job")
    values: dict[str, str] = {}
    try:
        for line in completed.stdout.decode("ascii", errors="strict").splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in values:
                raise ProverError("TERMINAL_PROVER_UNIT_STATE_INVALID")
            values[key] = value
    except UnicodeError as error:
        raise ProverError("TERMINAL_PROVER_UNIT_STATE_INVALID") from error
    return bool(
        completed.returncode == 0 and values == {
            "LoadState": values.get("LoadState"), "ActiveState": "inactive",
            "Job": ""} and values["LoadState"] in {"loaded", "not-found"})


def _connection_units() -> list[str]:
    completed = _systemctl(
        "list-units", FINALIZER_CONNECTION_GLOB, "--all", "--plain",
        "--no-legend", "--no-pager")
    if completed.returncode != 0:
        raise ProverError("TERMINAL_PROVER_CONNECTION_ENUMERATION_FAILED")
    try:
        lines = completed.stdout.decode("ascii", errors="strict").splitlines()
    except UnicodeError as error:
        raise ProverError("TERMINAL_PROVER_CONNECTION_ENUMERATION_FAILED") from error
    result: list[str] = []
    for line in lines:
        fields = line.split()
        if not fields:
            continue
        unit = fields[0]
        if not unit.startswith("hepta-p1-paper-canary-finalizer@") or \
                not unit.endswith(".service") or any(ch.isspace() for ch in unit):
            raise ProverError("TERMINAL_PROVER_CONNECTION_ENUMERATION_FAILED")
        result.append(unit)
    return sorted(set(result))


def _entries(path: Path) -> list[str]:
    try:
        return sorted(item.name for item in path.iterdir())
    except FileNotFoundError:
        return []
    except OSError as error:
        raise ProverError("TERMINAL_PROVER_RUNTIME_ENUMERATION_FAILED") from error


def _owner_evidence(
        campaign: str, cycle: str, expected: str
) -> tuple[int, Optional[str], Optional[str], Optional[str]]:
    directory = CONTROL_ROOT / campaign / cycle
    normal = directory / "root-cleanup-receipt.v4.json"
    legacy_normal = directory / "root-cleanup-receipt.v1.json"
    legacy_normal_v2 = directory / "root-cleanup-receipt.v2.json"
    legacy_normal_v3 = directory / "root-cleanup-receipt.v3.json"
    legacy_retirement = directory / "durable-owner-retirement-receipt.v1.json"
    legacy_retirement_v2 = directory / \
        "durable-owner-retirement-receipt.v2.json"
    legacy_retirement_v3 = directory / \
        "durable-owner-retirement-receipt.v3.json"
    emergency = directory / "root-emergency-cleanup-receipt.v1.json"
    if (legacy_normal.exists() or legacy_normal.is_symlink() or
            legacy_normal_v2.exists() or legacy_normal_v2.is_symlink() or
            legacy_normal_v3.exists() or legacy_normal_v3.is_symlink() or
            legacy_retirement.exists() or legacy_retirement.is_symlink() or
            legacy_retirement_v2.exists() or legacy_retirement_v2.is_symlink() or
            legacy_retirement_v3.exists() or legacy_retirement_v3.is_symlink()):
        raise ProverError("TERMINAL_PROVER_LEGACY_NORMAL_EVIDENCE_REJECTED")
    if expected == "NONE":
        if any(path.exists() or path.is_symlink() for path in (
                normal, emergency,
                directory / "durable-owner-retirement-receipt.v4.json",
                directory / "durable-recovery-owner-reference.v1.json")):
            raise ProverError("TERMINAL_PROVER_UNEXPECTED_OWNER_EVIDENCE")
        if _entries(OWNER_ROOT):
            raise ProverError("TERMINAL_PROVER_UNEXPECTED_OWNER_EVIDENCE")
        return 0, None, None, None
    receipt_path = normal if expected == "RETIRED" else emergency
    receipt_raw = stable_read(receipt_path, uid=0, gid=0, mode=0o600)
    receipt = strict_json(receipt_raw, "TERMINAL_PROVER_ROOT_RECEIPT_INVALID")
    sealed(receipt, "TERMINAL_PROVER_ROOT_RECEIPT_INVALID")
    if expected == "RETIRED":
        _exact(
            receipt, NORMAL_ROOT_RECEIPT_FIELDS,
            "TERMINAL_PROVER_ROOT_RECEIPT_INVALID")
        path_field = "durable_owner_retirement_receipt_path"
        file_field = "durable_owner_retirement_receipt_file_sha256"
        body_field = "durable_owner_retirement_receipt_body_sha256"
        expected_count = 0
        if (
                receipt.get("schema") != NORMAL_ROOT_RECEIPT_SCHEMA or
                receipt.get("version") != NORMAL_ROOT_RECEIPT_VERSION or
                receipt.get("status") != "ROOT_CLEANUP_COMPLETE_DENY_ALL" or
                receipt.get("completed_actions") != NORMAL_REQUIRED_ACTIONS or
                receipt.get("paper_only") is not True or
                receipt.get("live_authorized") is not False or
                receipt.get("authority_granted") is not False or
                receipt.get("guardian_stopped") is not True or
                receipt.get("execution_control_disabled") is not True or
                receipt.get("kill_switch_engaged") is not True or
                receipt.get("global_kill_switch_engaged") is not True or
                receipt.get("broker_mutation_units_inactive") is not True or
                receipt.get("permit_absent") is not True or
                receipt.get("guardian_runtime_absent") is not True or
                receipt.get("mutation_credentials_destroyed") is not True or
                receipt.get("credentials_destroyed_scope") !=
                    "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY" or
                receipt.get("retained_root_recovery_bearer_count") != 1 or
                receipt.get("retained_root_recovery_bearer_path") !=
                    str(OWNER_ROOT / "session.token.revoke-token") or
                receipt.get(
                    "retained_root_recovery_bearer_mutation_authority") is not
                    False or
                not isinstance(receipt.get("broker_mutation_units"), list) or
                sha(canonical_json(receipt["broker_mutation_units"])) !=
                    receipt.get("broker_mutation_units_sha256")):
            raise ProverError("TERMINAL_PROVER_ROOT_RECEIPT_INVALID")
    else:
        path_field = "durable_recovery_owner_reference_path"
        file_field = "durable_recovery_owner_reference_file_sha256"
        body_field = "durable_recovery_owner_reference_body_sha256"
        expected_count = 1
        if (
                receipt.get("schema") !=
                    "hepta.p1-paper-canary-root-emergency-cleanup-receipt.v1" or
                receipt.get("status") !=
                    "ROOT_EMERGENCY_CLEANUP_COMPLETE_DENY_ALL"):
            raise ProverError("TERMINAL_PROVER_ROOT_RECEIPT_INVALID")
    if (
            receipt.get("campaign_id") != campaign or
            receipt.get("cycle_id") != cycle or
            receipt.get("durable_owner_count") != expected_count or
            receipt.get("durable_owner_status") != expected or
            receipt.get("runtime_session_count") != 0 or
            receipt.get("broker_deny_all") is not True or
            receipt.get("identity_count") != 0 or
            receipt.get("authorized_connector_count") != 0):
        raise ProverError("TERMINAL_PROVER_ROOT_RECEIPT_INVALID")
    evidence_path_value = receipt.get(path_field)
    if not isinstance(evidence_path_value, str):
        raise ProverError("TERMINAL_PROVER_OWNER_EVIDENCE_INVALID")
    evidence_path = Path(evidence_path_value)
    expected_name = (
        "durable-owner-retirement-receipt.v4.json" if expected == "RETIRED"
        else "durable-recovery-owner-reference.v1.json")
    if evidence_path != directory / expected_name:
        raise ProverError("TERMINAL_PROVER_OWNER_EVIDENCE_INVALID")
    evidence_raw = stable_read(evidence_path, uid=0, gid=0, mode=0o600)
    evidence = strict_json(
        evidence_raw, "TERMINAL_PROVER_OWNER_EVIDENCE_INVALID")
    evidence_body = sealed(
        evidence, "TERMINAL_PROVER_OWNER_EVIDENCE_INVALID")
    if (
            sha(evidence_raw) != receipt.get(file_field) or
            evidence_body != receipt.get(body_field) or
            evidence.get("campaign_id") != campaign or
            evidence.get("cycle_id") != cycle or
            evidence.get("durable_owner_count") != expected_count or
            evidence.get("durable_owner_status") != expected):
        raise ProverError("TERMINAL_PROVER_OWNER_EVIDENCE_INVALID")
    if expected == "RETIRED":
        _validate_hsl8_retirement(evidence, receipt)
        if (
                receipt["retained_root_recovery_bearer_sha256"] !=
                    evidence["revoke_bearer_file_sha256"] or
                receipt["retained_root_recovery_bearer_path"] !=
                    evidence["revoke_bearer_path"]):
            raise ProverError("TERMINAL_PROVER_OWNER_EVIDENCE_INVALID")
        # The mutation token and authority document are gone, while the sole
        # root revoke bearer remains available until the outer completion is
        # durable.  A cached terminal_replay=true bit is not proof of current
        # runtime state: replay the exact HSL8 witness now.
        if _entries(OWNER_ROOT) != ["session.token.revoke-token"]:
            raise ProverError("TERMINAL_PROVER_OWNER_CREDENTIAL_REMAINS")
        _fresh_terminal_replay(evidence, receipt)
    return expected_count, str(evidence_path), sha(evidence_raw), evidence_body


def _seal_body(body: dict[str, Any]) -> dict[str, Any]:
    result = dict(body)
    result["body_sha256"] = sha(canonical_json(result))
    return result


def _normal_owner_documents(
        campaign: str, cycle: str,
) -> tuple[Path, bytes, dict[str, Any], Path, bytes, dict[str, Any]]:
    directory = CONTROL_ROOT / campaign / cycle
    root_path = directory / "root-cleanup-receipt.v4.json"
    root_raw = stable_read(root_path, uid=0, gid=0, mode=0o600)
    root = _exact(
        strict_json(root_raw, "TERMINAL_PROVER_ROOT_RECEIPT_INVALID"),
        NORMAL_ROOT_RECEIPT_FIELDS, "TERMINAL_PROVER_ROOT_RECEIPT_INVALID")
    root_body = sealed(root, "TERMINAL_PROVER_ROOT_RECEIPT_INVALID")
    retirement_path = directory / "durable-owner-retirement-receipt.v4.json"
    if (
            root.get("schema") != NORMAL_ROOT_RECEIPT_SCHEMA or
            root.get("version") != NORMAL_ROOT_RECEIPT_VERSION or
            root.get("campaign_id") != campaign or
            root.get("cycle_id") != cycle or
            root.get("mutation_credentials_destroyed") is not True or
            root.get("credentials_destroyed_scope") !=
                "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY" or
            root.get("retained_root_recovery_bearer_count") != 1 or
            root.get("retained_root_recovery_bearer_path") !=
                str(OWNER_ROOT / "session.token.revoke-token") or
            root.get(
                "retained_root_recovery_bearer_mutation_authority") is not
                False or
            root.get("durable_owner_retirement_receipt_path") !=
                str(retirement_path)):
        raise ProverError("TERMINAL_PROVER_ROOT_RECEIPT_INVALID")
    retirement_raw = stable_read(
        retirement_path, uid=0, gid=0, mode=0o600)
    retirement = strict_json(
        retirement_raw, "TERMINAL_PROVER_OWNER_EVIDENCE_INVALID")
    retirement_body = sealed(
        retirement, "TERMINAL_PROVER_OWNER_EVIDENCE_INVALID")
    if (
            sha(retirement_raw) != root.get(
                "durable_owner_retirement_receipt_file_sha256") or
            retirement_body != root.get(
                "durable_owner_retirement_receipt_body_sha256")):
        raise ProverError("TERMINAL_PROVER_OWNER_EVIDENCE_INVALID")
    _validate_hsl8_retirement(retirement, root)
    if (
            root["retained_root_recovery_bearer_sha256"] !=
                retirement["revoke_bearer_file_sha256"] or
            root["retained_root_recovery_bearer_path"] !=
                retirement["revoke_bearer_path"]):
        raise ProverError("TERMINAL_PROVER_OWNER_EVIDENCE_INVALID")
    return root_path, root_raw, root, retirement_path, retirement_raw, retirement


def _validate_purge_intent(
        value: dict[str, Any], *, campaign: str, cycle: str,
        completion_path: Path, completion_raw: bytes, completion: dict[str, Any],
        root_path: Path, root_raw: bytes, root: dict[str, Any],
        retirement_path: Path, retirement_raw: bytes,
        retirement: dict[str, Any],
) -> dict[str, Any]:
    failure = "TERMINAL_PROVER_OWNER_PURGE_INTENT_INVALID"
    intent = _exact(value, OWNER_PURGE_INTENT_FIELDS, failure)
    sealed(intent, failure)
    if (
            intent["schema"] != OWNER_PURGE_INTENT_SCHEMA or
            intent["version"] != 1 or
            intent["status"] != "CURRENT_RUNTIME_REPLAY_VERIFIED" or
            intent["campaign_id"] != campaign or
            intent["domain_id"] != "alpha" or intent["cycle_id"] != cycle or
            intent["outer_completion_path"] != str(completion_path) or
            intent["outer_completion_file_sha256"] != sha(completion_raw) or
            intent["outer_completion_body_sha256"] !=
                completion["body_sha256"] or
            intent["root_cleanup_receipt_path"] != str(root_path) or
            intent["root_cleanup_receipt_file_sha256"] != sha(root_raw) or
            intent["root_cleanup_receipt_body_sha256"] !=
                root["body_sha256"] or
            intent["owner_retirement_receipt_path"] != str(retirement_path) or
            intent["owner_retirement_receipt_file_sha256"] !=
                sha(retirement_raw) or
            intent["owner_retirement_receipt_body_sha256"] !=
                retirement["body_sha256"] or
            intent["terminal_ack_receipt_sha256"] !=
                retirement["terminal_ack_receipt_sha256"] or
            intent["revoke_bearer_path"] !=
                str(OWNER_ROOT / "session.token.revoke-token") or
            intent["revoke_bearer_file_sha256"] !=
                retirement["revoke_bearer_file_sha256"] or
            intent["current_runtime_replay_verified"] is not True or
            intent["paper_only"] is not True or
            intent["live_authorized"] is not False or
            intent["authority_granted"] is not False):
        raise ProverError(failure)
    return intent


def purge_owner_after_completion(campaign: str, cycle: str) -> dict[str, Any]:
    """Purge the retained root bearer only after durable outer completion."""
    directory = CONTROL_ROOT / campaign / cycle
    completion_path = directory / "cycle-completion-receipt.v4.json"
    completion_raw = stable_read(
        completion_path, uid=0, gid=0, mode=0o600)
    completion = strict_json(
        completion_raw, "TERMINAL_PROVER_OUTER_COMPLETION_INVALID")
    sealed(completion, "TERMINAL_PROVER_OUTER_COMPLETION_INVALID")
    if (
            completion.get("schema") != OUTER_COMPLETION_SCHEMA or
            completion.get("version") != 4 or
            completion.get("campaign_id") != campaign or
            completion.get("cycle_id") != cycle or
            completion.get("status") not in {"P2_SUCCESS", "RECOVERY_REQUIRED"} or
            completion.get("durable_owner_status") != "RETIRED" or
            completion.get("broker_deny_all") is not True or
            completion.get("mutation_credentials_destroyed") is not True or
            completion.get("credentials_destroyed_scope") !=
                "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY" or
            completion.get("retained_root_recovery_bearer_count") != 1 or
            completion.get("retained_root_recovery_bearer_path") !=
                str(OWNER_ROOT / "session.token.revoke-token") or
            completion.get(
                "retained_root_recovery_bearer_mutation_authority") is not
                False or
            completion.get("authority_granted") is not False):
        raise ProverError("TERMINAL_PROVER_OUTER_COMPLETION_INVALID")
    (root_path, root_raw, root, retirement_path, retirement_raw,
     retirement) = _normal_owner_documents(campaign, cycle)
    if completion.get("retained_root_recovery_bearer_sha256") != \
            retirement["revoke_bearer_file_sha256"]:
        raise ProverError("TERMINAL_PROVER_OUTER_COMPLETION_INVALID")
    intent_path = directory / "outer-owner-purge-intent.v1.json"
    receipt_path = directory / "outer-owner-purge-receipt.v1.json"
    intent_body = {
        "schema": OWNER_PURGE_INTENT_SCHEMA, "version": 1,
        "status": "CURRENT_RUNTIME_REPLAY_VERIFIED",
        "campaign_id": campaign, "domain_id": "alpha", "cycle_id": cycle,
        "outer_completion_path": str(completion_path),
        "outer_completion_file_sha256": sha(completion_raw),
        "outer_completion_body_sha256": completion["body_sha256"],
        "root_cleanup_receipt_path": str(root_path),
        "root_cleanup_receipt_file_sha256": sha(root_raw),
        "root_cleanup_receipt_body_sha256": root["body_sha256"],
        "owner_retirement_receipt_path": str(retirement_path),
        "owner_retirement_receipt_file_sha256": sha(retirement_raw),
        "owner_retirement_receipt_body_sha256": retirement["body_sha256"],
        "terminal_ack_receipt_sha256":
            retirement["terminal_ack_receipt_sha256"],
        "revoke_bearer_path": str(OWNER_ROOT / "session.token.revoke-token"),
        "revoke_bearer_file_sha256":
            retirement["revoke_bearer_file_sha256"],
        "current_runtime_replay_verified": True, "paper_only": True,
        "live_authorized": False, "authority_granted": False,
    }
    expected_intent = _seal_body(intent_body)
    expected_intent_raw = canonical_json(expected_intent)
    expected_receipt = _seal_body({
        "schema": OWNER_PURGE_RECEIPT_SCHEMA, "version": 1,
        "status": "OWNER_BEARER_PURGED", "campaign_id": campaign,
        "domain_id": "alpha", "cycle_id": cycle,
        "owner_purge_intent_path": str(intent_path),
        "owner_purge_intent_file_sha256": sha(expected_intent_raw),
        "owner_purge_intent_body_sha256": expected_intent["body_sha256"],
        "outer_completion_path": str(completion_path),
        "outer_completion_file_sha256": sha(completion_raw),
        "outer_completion_body_sha256": completion["body_sha256"],
        "root_cleanup_receipt_path": str(root_path),
        "root_cleanup_receipt_file_sha256": sha(root_raw),
        "root_cleanup_receipt_body_sha256": root["body_sha256"],
        "owner_retirement_receipt_path": str(retirement_path),
        "owner_retirement_receipt_file_sha256": sha(retirement_raw),
        "owner_retirement_receipt_body_sha256": retirement["body_sha256"],
        "terminal_ack_receipt_sha256":
            retirement["terminal_ack_receipt_sha256"],
        "revoke_bearer_file_sha256":
            retirement["revoke_bearer_file_sha256"],
        "owner_bearer_purged": True, "durable_owner_credential_count": 0,
        "paper_only": True, "live_authorized": False,
        "authority_granted": False,
    })
    if receipt_path.exists() or receipt_path.is_symlink():
        if not (intent_path.exists() or intent_path.is_symlink()):
            raise ProverError("TERMINAL_PROVER_OWNER_PURGE_INVALID")
        intent_raw = stable_read(intent_path, uid=0, gid=0, mode=0o600)
        intent = _validate_purge_intent(
            strict_json(intent_raw, "TERMINAL_PROVER_OWNER_PURGE_INVALID"),
            campaign=campaign, cycle=cycle, completion_path=completion_path,
            completion_raw=completion_raw, completion=completion,
            root_path=root_path, root_raw=root_raw, root=root,
            retirement_path=retirement_path, retirement_raw=retirement_raw,
            retirement=retirement)
        if intent_raw != expected_intent_raw or intent != expected_intent:
            raise ProverError("TERMINAL_PROVER_OWNER_PURGE_INVALID")
        receipt_raw = stable_read(receipt_path, uid=0, gid=0, mode=0o600)
        receipt = _exact(
            strict_json(receipt_raw, "TERMINAL_PROVER_OWNER_PURGE_INVALID"),
            OWNER_PURGE_RECEIPT_FIELDS, "TERMINAL_PROVER_OWNER_PURGE_INVALID")
        sealed(receipt, "TERMINAL_PROVER_OWNER_PURGE_INVALID")
        if receipt != expected_receipt or _entries(OWNER_ROOT):
            raise ProverError("TERMINAL_PROVER_OWNER_PURGE_INVALID")
        return receipt
    if intent_path.exists() or intent_path.is_symlink():
        intent_raw = stable_read(intent_path, uid=0, gid=0, mode=0o600)
        intent = _validate_purge_intent(
            strict_json(intent_raw, "TERMINAL_PROVER_OWNER_PURGE_INTENT_INVALID"),
            campaign=campaign, cycle=cycle, completion_path=completion_path,
            completion_raw=completion_raw, completion=completion,
            root_path=root_path, root_raw=root_raw, root=root,
            retirement_path=retirement_path, retirement_raw=retirement_raw,
            retirement=retirement)
        if intent_raw != expected_intent_raw or intent != expected_intent:
            raise ProverError("TERMINAL_PROVER_OWNER_PURGE_INTENT_INVALID")
    else:
        if _entries(OWNER_ROOT) != ["session.token.revoke-token"]:
            raise ProverError("TERMINAL_PROVER_OWNER_PURGE_BEARER_INVALID")
        _fresh_terminal_replay(retirement, root)
        _publish_or_same(intent_path, expected_intent_raw)
    entries = _entries(OWNER_ROOT)
    if entries == ["session.token.revoke-token"]:
        # Revalidate again after any intent replay and immediately before the
        # irreversible credential unlink.
        _fresh_terminal_replay(retirement, root)
        bearer = OWNER_ROOT / "session.token.revoke-token"
        try:
            bearer.unlink()
            _fsync_directory(OWNER_ROOT)
        except OSError as error:
            raise ProverError(
                "TERMINAL_PROVER_OWNER_PURGE_FAILED") from error
    elif entries:
        raise ProverError("TERMINAL_PROVER_OWNER_PURGE_BEARER_INVALID")
    receipt = expected_receipt
    _publish_or_same(receipt_path, canonical_json(receipt))
    if _entries(OWNER_ROOT):
        raise ProverError("TERMINAL_PROVER_OWNER_PURGE_FAILED")
    return receipt


def _capture_evidence(campaign: str, cycle: str) -> None:
    directory = CONTROL_ROOT / campaign / cycle
    intent_path = directory / "capture-session-owner.v1.json"
    receipt_path = directory / "capture-session-retirement-receipt.v1.json"
    if CAPTURE_TOKEN.exists() or CAPTURE_TOKEN.is_symlink():
        raise ProverError("TERMINAL_PROVER_CAPTURE_BEARER_REMAINS")
    intent_raw = stable_read(intent_path, uid=0, gid=0, mode=0o600)
    receipt_raw = stable_read(receipt_path, uid=0, gid=0, mode=0o600)
    intent = strict_json(intent_raw, "TERMINAL_PROVER_CAPTURE_OWNER_INVALID")
    receipt = strict_json(
        receipt_raw, "TERMINAL_PROVER_CAPTURE_RETIREMENT_INVALID")
    intent_body = sealed(intent, "TERMINAL_PROVER_CAPTURE_OWNER_INVALID")
    sealed(receipt, "TERMINAL_PROVER_CAPTURE_RETIREMENT_INVALID")
    if (
            intent.get("schema") !=
                "hepta.p1-paper-canary-capture-owner.v1" or
            intent.get("status") != "OWNER_MAY_EXIST" or
            intent.get("campaign_id") != campaign or
            intent.get("cycle_id") != cycle or
            intent.get("domain_id") != "alpha" or
            intent.get("template_id") != "watch" or
            intent.get("expected_lease_generation") != 1 or
            intent.get("peer_uid") != 2104 or
            intent.get("paper_only") is not True or
            intent.get("live_authorized") is not False or
            intent.get("authority_granted") is not False or
            receipt.get("schema") !=
                "hepta.p1-paper-canary-capture-owner-retirement.v1" or
            receipt.get("status") != "RETIRED" or
            receipt.get("campaign_id") != campaign or
            receipt.get("cycle_id") != cycle or
            receipt.get("session_id") != intent.get("session_id") or
            receipt.get("lease_generation") != 1 or
            receipt.get("token_sha256") != intent.get("token_sha256") or
            receipt.get("owner_intent_body_sha256") != intent_body or
            receipt.get("revoke_accepted") is not True or
            receipt.get("revoke_reason_code") != "OK" or
            receipt.get("revoke_audit_reason_code") not in {
                "SESSION_NOT_FOUND", "SESSION_LEASE_NOT_FOUND"} or
            receipt.get("durable_hsl_audit") !=
                "GENERATION_ABSENT_AFTER_REVOKE" or
            receipt.get("paper_only") is not True or
            receipt.get("live_authorized") is not False or
            receipt.get("authority_granted") is not False):
        raise ProverError("TERMINAL_PROVER_CAPTURE_RETIREMENT_INVALID")


def force_deny_all(control: ModuleType) -> dict[str, Any]:
    try:
        # Stopping the guardian triggers the static unit's credential-pinned
        # ExecStopPost, then the direct call reconciles any interrupted WAL.
        _systemctl("stop", control.GUARDIAN_UNIT)
        result = control.guardian_fail_close()
        control._require_kill_switch(
            control.DOMAIN_KILL_SWITCH_PATH, control.PAPER_CONTROL_GID)
        control._require_kill_switch(
            control.GLOBAL_KILL_SWITCH_PATH,
            control.GLOBAL_PAPER_CONTROL_GID)
        status = control.status(control.DEFAULT_IDENTITIES)
    except Exception as error:
        raise ProverError("TERMINAL_PROVER_FORCE_DENY_ALL_FAILED") from error
    if (
            status.get("mode") != "DENY_ALL" or
            status.get("paper_authorized") is not False or
            status.get("live_authorized") is not False or
            status.get("identity_count") != 0 or
            status.get("effective_state_verified") is not True or
            status.get("egress_verified") is not True):
        raise ProverError("TERMINAL_PROVER_FORCE_DENY_ALL_FAILED")
    return result


def prove_terminal(
        control: ModuleType, policy: dict[str, Any], campaign: str, cycle: str,
        expected_owner: str) -> dict[str, Any]:
    boundary = _verify_external(
        control, policy, fresh=False, inert=True, residue_absent=False)
    if _entries(SESSION_ROOT):
        raise ProverError("TERMINAL_PROVER_RUNTIME_SESSION_REMAINS")
    for path in (
            control.GUARDIAN_REQUEST_PATH, control.GUARDIAN_ACTIVE_PATH,
            control.BROKER_START_PERMIT_PATH):
        if path.exists() or path.is_symlink():
            raise ProverError("TERMINAL_PROVER_PERMIT_REMAINS")
    mutation_inactive = all(
        _unit_inactive(unit) for unit in PAPER_AUTHORITY_AND_MUTATION_UNITS)
    capture_inactive = _unit_inactive(CAPTURE_UNIT)
    executor_inactive = _unit_inactive(EXECUTOR_UNIT)
    listener_inactive = _unit_inactive(FINALIZER_SOCKET_UNIT)
    connections = _connection_units()
    connection_inactive = all(_unit_inactive(unit) for unit in connections)
    if not all((
            mutation_inactive, capture_inactive, executor_inactive,
            listener_inactive, connection_inactive)):
        raise ProverError("TERMINAL_PROVER_UNIT_ACTIVE")
    owner_count, owner_path, owner_file, owner_body = _owner_evidence(
        campaign, cycle, expected_owner)
    # The sealed generation-1 revoke receipt is the terminal, authoritative
    # durable-HSL proof for the independent WATCH capture owner.
    _capture_evidence(campaign, cycle)
    return {
        "broker_deny_all": True, "kill_switches_engaged": True,
        "permit_absent": True, "identity_count": 0,
        "identity_manifest_sha256": boundary["identity_manifest_sha256"],
        "authorized_connector_count": 0, "runtime_session_count": 0,
        "paper_authority_and_mutation_units":
            list(PAPER_AUTHORITY_AND_MUTATION_UNITS),
        "paper_authority_and_mutation_units_inactive": True,
        "peer_capture_unit_inactive": True,
        "peer_executor_unit_inactive": True,
        "finalizer_listener_unit_inactive": True,
        "finalizer_connection_units_inactive": True,
        "durable_owner_count": owner_count,
        "durable_owner_status": expected_owner,
        "durable_owner_evidence_path": owner_path,
        "durable_owner_evidence_file_sha256": owner_file,
        "durable_owner_evidence_body_sha256": owner_body,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--cycle-id", required=True)
    parser.add_argument("--phase", required=True, choices=(
        "prelaunch", "enable-paper", "force-deny-all", "outer-terminal",
        "outer-purge"))
    parser.add_argument(
        "--expected-owner-status", choices=("NONE", "RETIRED", "RECOVERY_ONLY"))
    arguments = parser.parse_args(argv)
    try:
        if os.geteuid() != 0 or os.getegid() != 0:
            raise ProverError("TERMINAL_PROVER_ROOT_REQUIRED")
        if (
                IDENTIFIER.fullmatch(arguments.campaign_id) is None or
                IDENTIFIER.fullmatch(arguments.cycle_id) is None):
            raise ProverError("TERMINAL_PROVER_IDENTIFIER_INVALID")
        if (arguments.phase == "outer-terminal") != (
                arguments.expected_owner_status is not None):
            raise ProverError("TERMINAL_PROVER_ARGUMENTS_INVALID")
        control = _load_control()
        policy, policy_raw = _policy(arguments.campaign_id)
        if arguments.phase == "prelaunch":
            boundary = _verify_external(
                control, policy, fresh=True, inert=True, residue_absent=True)
            output = {
                "schema": "hepta.p1-paper-canary-prelaunch-deny-proof.v1",
                "version": 1, "status": "DENY_ALL_PROVEN",
                "campaign_id": arguments.campaign_id,
                "cycle_id": arguments.cycle_id,
                "policy_file_sha256": sha(policy_raw),
                "boundary_fingerprint": boundary["boundary_fingerprint"],
                "authority_granted": False,
            }
        elif arguments.phase == "enable-paper":
            try:
                result = control.enable(
                    domain="alpha", authority_path=control.DEFAULT_AUTHORITY,
                    identities_path=control.DEFAULT_IDENTITIES,
                    env_root=control.DEFAULT_ENV_ROOT,
                    drop_in_path=control.DEFAULT_DROP_IN,
                    gateway_env_root=control.DEFAULT_GATEWAY_ENV_ROOT,
                    external_p1_finalized=True,
                    handoff_path=control.EXTERNAL_P1_HANDOFF_PATH,
                    handoff_file_sha256=policy[
                        "watch_handoff_receipt_file_sha256"],
                    handoff_body_sha256=policy[
                        "watch_handoff_receipt_body_sha256"],
                    campaign_id=arguments.campaign_id,
                    source_baseline_sha256=policy["source_baseline_sha256"])
            except Exception as error:
                raise ProverError("TERMINAL_PROVER_ENABLE_FAILED") from error
            if (
                    result.get("mode") != "LOCAL_PAPER" or
                    result.get("paper_authorized") is not True or
                    result.get("live_authorized") is not False or
                    result.get("guardian_supervised") is not True):
                raise ProverError("TERMINAL_PROVER_ENABLE_FAILED")
            output = {
                "schema": "hepta.p1-paper-canary-paper-enable-result.v1",
                "version": 1, "status": "EXTERNAL_PAPER_ENABLED",
                "campaign_id": arguments.campaign_id,
                "cycle_id": arguments.cycle_id,
                "guardian_request_id": result["guardian_request_id"],
                "authority_granted": False,
            }
        elif arguments.phase == "force-deny-all":
            force_deny_all(control)
            output = {
                "schema": "hepta.p1-paper-canary-force-deny-result.v1",
                "version": 1, "status": "DENY_ALL",
                "campaign_id": arguments.campaign_id,
                "cycle_id": arguments.cycle_id,
                "authority_granted": False,
            }
        elif arguments.phase == "outer-purge":
            _verify_external(
                control, policy, fresh=False, inert=True, residue_absent=False)
            if _entries(SESSION_ROOT):
                raise ProverError("TERMINAL_PROVER_RUNTIME_SESSION_REMAINS")
            for path in (
                    control.GUARDIAN_REQUEST_PATH, control.GUARDIAN_ACTIVE_PATH,
                    control.BROKER_START_PERMIT_PATH):
                if path.exists() or path.is_symlink():
                    raise ProverError("TERMINAL_PROVER_PERMIT_REMAINS")
            output = purge_owner_after_completion(
                arguments.campaign_id, arguments.cycle_id)
        else:
            output = prove_terminal(
                control, policy, arguments.campaign_id, arguments.cycle_id,
                arguments.expected_owner_status)
    except ProverError as error:
        print(f"hepta-p1-paper-canary-terminal-prover: FAIL {error}")
        return 2
    print(canonical_json(output).decode("ascii"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
