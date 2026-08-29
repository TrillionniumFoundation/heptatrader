#!/usr/bin/env python3
"""Commit one post-cutoff PAPER terminal witness without broker authority.

This root-only recovery executable never starts the normal Execution Service.
It independently validates the signed terminal account bundle, holds the
host-wide authority lease, proves the current host is inert, asks the isolated
exec-UID committer to create HPW1, and publishes one stable HPE1.  A separate
post-completion operation can later replay an already-committed HSL8 ACK,
durably authorize removal, and remove the matching host owner.  Neither
operation grants PAPER/LIVE.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from types import ModuleType
from typing import Any, Callable, Mapping, Sequence


ROOT_UID = 0
ROOT_GID = 0
EXEC_UID = 2121
EXEC_GID = 2121
DOMAIN = "alpha"
PAPER_DOMAIN = "PAPER:alpha"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_LINE_FILE_BYTES = 192 * 1024
MAX_HPE1_BYTES = 12 * 1024
MAX_COMMAND_BYTES = 1024 * 1024

HOST_AUTHORITY_DIRECTORY = Path("/run/hepta/ib-paper-host-authority")
HOST_AUTHORITY_LEASE_PATH = HOST_AUTHORITY_DIRECTORY / "lease.lock"
HOST_AUTHORITY_OWNER_PATH = HOST_AUTHORITY_DIRECTORY / "owner.v1"
RUNTIME_ROOT = Path("/run/hepta/paper-terminal-witness/alpha")
CAPSULE_OUTPUT = RUNTIME_ROOT / "commit-capsule.v1"
EVIDENCE_OUTPUT = RUNTIME_ROOT / "terminal-evidence.v1"
CUTOFF_OUTPUT = RUNTIME_ROOT / "transport-cutoff-receipt.v1.json"
TERMINAL_COMPLETION_PREFIX = "terminal-owner-completion."
TERMINAL_COMPLETION_POINTER_PREFIX = "terminal-owner-current."
STATE_DIRECTORY = Path("/var/lib/hepta-ib-execution-alpha")
HPT1_PATH = STATE_DIRECTORY / "ib-paper-terminal-halt.v1"
HPW1_PATH = STATE_DIRECTORY / "ib-paper-terminal-external-halt.v1"
SUPERVISOR_SOCKET = Path(
    "/run/hepta-tool-gateway-alpha/session-supervisor.sock")
SESSIONCTL = Path("/usr/libexec/hepta-sessionctl")
ATTESTOR = Path("/usr/libexec/hepta-p1-paper-zero-exposure-attestor")
BROKER_POLICY_HELPER = Path("/usr/libexec/hepta-broker-egress-policy")
BROKER_POLICY_UNIT = "hepta-broker-egress-policy.service"
PAPER_IDENTITIES_PATH = Path(
    "/etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json")
PAPER_IDENTITIES_SCHEMA = "hepta.agent-trust-domain-paper-identities.v1"
PAPER_EGRESS_DROP_IN = Path(
    "/etc/systemd/system/hepta-broker-egress-policy.service.d/"
    "20-local-paper.conf")
PRODUCER_MODULE = Path(
    "/usr/libexec/hepta-p1-paper-zero-exposure-snapshot-producer")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
DOMAIN_KILL_SWITCH = Path("/run/hepta/ib-paper-control-alpha/kill-switch")
GLOBAL_KILL_SWITCH = Path("/run/hepta/ib-paper-control/kill-switch")

COMMITTER_UNIT = "hepta-paper-terminal-latch-committer@alpha.service"
LOCAL_PAPER_AUTHORITY_UNIT = "hepta-local-paper-authority@alpha.service"
LOCAL_PAPER_FAIL_CLOSE_UNIT = \
    "hepta-local-paper-fail-close@alpha.service"
PAPER_UNITS = (
    "hepta-execution-ib-paper@alpha.service",
    "hepta-execution-ib-paper@alpha.socket",
    "hepta-execution-events-ib-paper@alpha.socket",
    "hepta-ib-paper-domain-preflight@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.socket",
    "hepta-p1-paper-canary-capture.service",
    "hepta-p1-paper-canary-executor.service",
    "hepta-p1-paper-canary-root-coordinator.service",
)
PAPER_INERT_UNITS = PAPER_UNITS + (
    LOCAL_PAPER_AUTHORITY_UNIT, LOCAL_PAPER_FAIL_CLOSE_UNIT)

SAFE_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C", "LC_ALL": "C", "PYTHONNOUSERSITE": "1",
}

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ACCOUNT = re.compile(r"DU[0-9]{1,16}")

REQUEST_SCHEMA = "hepta.paper-terminal-witness-verifier-request.v1"
CUTOFF_REQUEST_SCHEMA = "hepta.paper-terminal-cutoff-request.v1"
CUTOFF_OWNER_SCHEMA = "hepta.paper-terminal-cutoff-owner.v1"
WITNESS_SCHEMA = "hepta.paper-post-cutoff-terminal-witness.v1"
CUTOFF_SCHEMA = "hepta.paper-transport-cutoff-receipt.v1"
TRUST_SCHEMA = "hepta.paper-terminal-account-provider-trust-policy.v1"
CHALLENGE_SCHEMA = "hepta.paper-terminal-account-evidence-challenge.v1"
SIGNED_ENVELOPE_SCHEMA = \
    "hepta.remote-authoritative-account-evidence-envelope.v1"
EGRESS_SCHEMA = "hepta.broker-egress-current-boundary.v1"
PROOF_KIND = "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1"
PROVIDER_CAPABILITY = \
    "ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1"
TERMINAL_COMPLETION_SCHEMA = \
    "hepta.paper-terminal-host-owner-completion.v1"
TERMINAL_COMPLETION_POINTER_SCHEMA = \
    "hepta.paper-terminal-host-owner-current.v1"

REQUEST_FIELDS = frozenset({
    "schema", "version", "status", "transport_cutoff_receipt",
    "provider_trust_policy", "challenge", "signed_account_evidence",
    "provider_request", "provider_response", "terminal_witness",
    "token_file", "token_generation", "expected_source_baseline_sha256",
    "expected_campaign_id", "expected_cycle_id", "expected_recovery_id",
    "expected_finalization_id", "body_sha256",
})
CUTOFF_REQUEST_FIELDS = frozenset({
    "schema", "version", "status", "expected_source_baseline_sha256",
    "expected_campaign_id", "expected_cycle_id", "expected_recovery_id",
    "expected_finalization_id", "preliminary_finalization_receipt_sha256",
    "owner_set_sha256", "owner_count", "owner_set_canonical_hex",
    "account_id_sha256", "service_pid", "service_start_ticks",
    "broker_socket_identity_sha256", "mutation_fence_generation",
    "token_file", "token_generation",
    "known_mutation_command_set_sha256", "known_mutation_command_count",
    "known_correlation_set_sha256", "known_correlation_count",
    "body_sha256",
})
BOUNDARY_FIELDS = (
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized",
)
TRANSPORT_CUTOFF_FIELDS = frozenset({
    "schema", "version", "status", "completed_at_ms",
    "completed_monotonic_ns", "round", "domain", "campaign_id",
    "source_baseline_sha256", "cycle_id", "recovery_id", "finalization_id",
    "boot_id", "service_pid", "service_start_ticks",
    "broker_socket_identity_sha256", "account_id_sha256", "owner_ids",
    "owner_set_sha256", "owner_set_canonical_hex", "owner_count",
    "execution_service_epoch", "execution_service_fencing_generation",
    "mutation_fence_generation", "known_mutation_command_set_sha256",
    "known_mutation_command_count", "known_correlation_set_sha256",
    "known_correlation_count", "egress_policy_generation",
    "egress_policy_sha256", "authorized_connectors", "authorized_uids",
    "broker_socket_count", "broker_process_count", "credential_exposure_count",
    "process_inventory_complete", "socket_inventory_complete",
    "credential_inventory_complete", "mutation_gate_closed",
    "reconnect_permitted", *BOUNDARY_FIELDS, "body_sha256",
})
CUTOFF_OWNER_FIELDS = frozenset({
    "schema", "version", "status", "boot_id", "campaign_id", "cycle_id",
    "recovery_id", "finalization_id", "terminalizing_latch_sha256",
    "transport_cutoff_file_sha256", "transport_cutoff_body_sha256",
    "transport_cutoff_document", "next_consumer", "paper_authorized",
    "live_authorized", "mutation_authorized", "direct_broker_access",
    "order_submission_authorized", "body_sha256",
})

HPT1_KEYS = (
    "state", "finalization_id",
    "preliminary_finalization_receipt_sha256", "owner_agent_id",
    "owner_session_id", "owner_account", "owner_execution_domain",
    "recovery_ingress_fence", "terminalization_service_epoch",
    "terminalization_service_fencing_generation",
    "terminalization_generation",
)

HPC1_KEYS = (
    "schema", "version", "status", "terminal_proof_kind", "recovery_id",
    "finalization_id", "campaign_id", "cycle_id",
    "expected_owner_set_sha256", "expected_owner_count",
    "owner_set_canonical_hex", "preliminary_finalization_receipt_sha256",
    "owner_agent_id", "owner_session_id", "owner_account",
    "owner_execution_domain", "account_id_sha256",
    "execution_service_epoch", "execution_service_fencing_generation",
    "recovery_ingress_fence", "terminalization_generation",
    "terminalizing_latch_sha256", "transport_cutoff_receipt_file_sha256",
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
    "authority_granted", "terminal_witness_durable", "capsule_body_sha256",
)

HPW1_KEYS = (
    "schema", "version", "state", "terminal_proof_kind", "recovery_id",
    "finalization_id", "campaign_id", "cycle_id",
    "expected_owner_set_sha256", "expected_owner_count",
    "preliminary_finalization_receipt_sha256", "owner_account",
    "owner_execution_domain", "account_id_sha256",
    "execution_service_epoch", "execution_service_fencing_generation",
    "recovery_ingress_fence", "terminalization_generation",
    "terminalizing_latch_sha256", "commit_capsule_file_sha256",
    "commit_capsule_body_sha256", "transport_cutoff_receipt_file_sha256",
    "transport_cutoff_receipt_body_sha256",
    "post_cutoff_terminal_witness_file_sha256",
    "post_cutoff_terminal_witness_body_sha256",
    "terminal_external_halt_latch_durable", "paper_authorized",
    "live_authorized", "mutation_authorized",
    "order_submission_authorized", "order_authorized", "paper_only",
    "authority_granted", "latch_body_sha256",
)

HPE1_KEYS = (
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

TERMINAL_COMPLETION_FIELDS = frozenset({
    "schema", "version", "status", "completed_at_ms", "boot_id",
    "recovery_id", "finalization_id", "campaign_id", "cycle_id",
    "expected_owner_set_sha256", "expected_owner_count",
    "preliminary_finalization_receipt_sha256",
    "terminal_challenge_file_sha256", "terminal_challenge_body_sha256",
    "terminalizing_latch_sha256", "terminal_external_halt_latch_sha256",
    "terminal_evidence_file_sha256", "terminal_evidence_body_sha256",
    "terminal_ack_receipt_sha256", "terminal_ack_receipt",
    "egress_publisher_pid", "egress_publisher_start_ticks",
    "egress_policy_generation", "egress_policy_sha256",
    "owner_removal_required_after_commit", "terminal_replay_verified",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized", "body_sha256",
})
TERMINAL_COMPLETION_POINTER_FIELDS = frozenset({
    "schema", "version", "status", "committed_at_ms", "boot_id",
    "recovery_id", "finalization_id", "campaign_id", "cycle_id",
    "terminal_completion_path", "terminal_completion_file_sha256",
    "terminal_completion_body_sha256", "owner_removal_authorized",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized", "body_sha256",
})


class VerifierError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise VerifierError(reason)


def canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise VerifierError("TERMINAL_WITNESS_CANONICALIZATION_FAILED") from error


def sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def terminal_completion_paths(finalization_id: str) -> tuple[Path, Path]:
    require(isinstance(finalization_id, str) and
            IDENTIFIER.fullmatch(finalization_id) is not None,
            "TERMINAL_WITNESS_COMPLETION_INVALID")
    suffix = hashlib.sha256(finalization_id.encode("ascii")).hexdigest()
    return (
        HOST_AUTHORITY_DIRECTORY /
            f"{TERMINAL_COMPLETION_PREFIX}{suffix}.v1.json",
        HOST_AUTHORITY_DIRECTORY /
            f"{TERMINAL_COMPLETION_POINTER_PREFIX}{suffix}.v1.json",
    )


def sealed_document(body: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    document = dict(body)
    require("body_sha256" not in document,
            "TERMINAL_WITNESS_CANONICALIZATION_FAILED")
    document["body_sha256"] = sha256(canonical_json(document))
    return document, canonical_json(document)


def strict_json(payload: bytes, reason: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            require(key not in value, reason)
            value[key] = item
        return value
    try:
        value = json.loads(
            payload.decode("ascii", errors="strict"),
            object_pairs_hook=unique,
            parse_float=lambda _value: (_ for _ in ()).throw(
                VerifierError(reason)),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                VerifierError(reason)))
    except VerifierError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise VerifierError(reason) from error
    require(isinstance(value, dict) and payload == canonical_json(value), reason)
    return value


def command_json(payload: bytes, reason: str) -> dict[str, Any]:
    """Parse a command response without imposing producer file encoding.

    Sessionctl owns its stdout representation.  The verifier rejects duplicate
    keys, floats, constants, trailing data, and non-ASCII, but does not pretend
    that a CLI response is one of the canonical durable JSON artifacts.
    """
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            require(key not in value, reason)
            value[key] = item
        return value
    try:
        value = json.loads(
            payload.decode("ascii", errors="strict"),
            object_pairs_hook=unique,
            parse_float=lambda _value: (_ for _ in ()).throw(
                VerifierError(reason)),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                VerifierError(reason)))
    except VerifierError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise VerifierError(reason) from error
    require(isinstance(value, dict), reason)
    return value


def validate_seal(document: dict[str, Any], schema: str, reason: str) -> None:
    require(
        document.get("schema") == schema and document.get("version") == 1 and
        isinstance(document.get("body_sha256"), str), reason)
    body = dict(document)
    claimed = body.pop("body_sha256")
    require(DIGEST.fullmatch(claimed) is not None and
            claimed == sha256(canonical_json(body)), reason)


def _open_parent(path: Path, reason: str) -> tuple[int, tuple[int, ...]]:
    require(path.is_absolute() and path == Path(os.path.normpath(path)) and
            path.name not in {"", ".", ".."}, reason)
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in path.parent.parts[1:]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
            metadata = os.fstat(child)
            require(stat.S_ISDIR(metadata.st_mode), reason)
            os.close(descriptor)
            descriptor = child
        parent = os.fstat(descriptor)
        require(
            parent.st_uid == ROOT_UID and parent.st_gid == ROOT_GID and
            stat.S_IMODE(parent.st_mode) & 0o022 == 0, reason)
        return descriptor, (
            parent.st_dev, parent.st_ino, parent.st_mode,
            parent.st_uid, parent.st_gid)
    except Exception:
        os.close(descriptor)
        raise


def secure_read(
        path: Path, *, expected_uid: int, expected_gid: int,
        modes: frozenset[int], maximum: int, reason: str) -> bytes:
    parent, parent_identity = _open_parent(path, reason)
    try:
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK |
            getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        try:
            opened = os.fstat(descriptor)
            identity = lambda item: (
                item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
                item.st_uid, item.st_gid, item.st_size, item.st_mtime_ns,
                item.st_ctime_ns)
            require(
                stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
                opened.st_uid == expected_uid and opened.st_gid == expected_gid and
                stat.S_IMODE(opened.st_mode) in modes and
                0 < opened.st_size <= maximum and identity(before) ==
                    identity(opened), reason)
            result = bytearray()
            while len(result) <= maximum:
                chunk = os.read(
                    descriptor, min(65536, maximum + 1 - len(result)))
                if not chunk:
                    break
                result.extend(chunk)
            after = os.fstat(descriptor)
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            current_parent = os.fstat(parent)
            require(
                0 < len(result) <= maximum and
                identity(opened) == identity(after) == identity(named) and
                parent_identity == (
                    current_parent.st_dev, current_parent.st_ino,
                    current_parent.st_mode, current_parent.st_uid,
                    current_parent.st_gid), reason)
            return bytes(result)
        finally:
            os.close(descriptor)
    except (OSError, VerifierError) as error:
        if isinstance(error, VerifierError):
            raise
        raise VerifierError(reason) from error
    finally:
        os.close(parent)


def publish_no_replace(
        path: Path, payload: bytes, maximum: int, *, mode: int = 0o400) -> bool:
    reason = "TERMINAL_WITNESS_OUTPUT_UNSAFE"
    require(mode in {0o400, 0o600}, reason)
    require(0 < len(payload) <= maximum, reason)
    try:
        existing = secure_read(
            path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({mode}), maximum=maximum, reason=reason)
    except VerifierError:
        try:
            os.lstat(path)
        except FileNotFoundError:
            existing = None
        else:
            raise
    if existing is not None:
        require(existing == payload, "TERMINAL_WITNESS_OUTPUT_CONFLICT")
        return True
    parent, _identity = _open_parent(path, reason)
    descriptor = -1
    try:
        descriptor = os.open(
            path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=parent)
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            require(count > 0, reason)
            offset += count
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == ROOT_UID and metadata.st_gid == ROOT_GID and
            stat.S_IMODE(metadata.st_mode) == mode, reason)
        os.close(descriptor)
        descriptor = -1
        os.fsync(parent)
    except (OSError, VerifierError) as error:
        if isinstance(error, VerifierError):
            raise
        raise VerifierError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)
    committed = secure_read(
        path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({mode}), maximum=maximum, reason=reason)
    require(committed == payload, reason)
    return False


def parse_lines(
        payload: bytes, header: str, keys: Sequence[str], reason: str
) -> tuple[dict[str, str], bytes]:
    try:
        text = payload.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise VerifierError(reason) from error
    lines = text.splitlines(keepends=True)
    require(
        len(lines) == len(keys) + 1 and lines[0] == header + "\n" and
        all(line.endswith("\n") for line in lines), reason)
    values: dict[str, str] = {}
    for key, line in zip(keys, lines[1:]):
        prefix = key + "="
        require(line.startswith(prefix) and line != prefix + "\n", reason)
        value = line[len(prefix):-1]
        require("=" not in value and key not in values, reason)
        values[key] = value
    return values, "".join(lines[:-1]).encode("ascii")


def build_lines(
        header: str, keys: Sequence[str], values: Mapping[str, str],
        hash_key: str) -> bytes:
    require(keys[-1] == hash_key and set(values) == set(keys) - {hash_key},
            "TERMINAL_WITNESS_LINE_CONTRACT_INVALID")
    out = [header + "\n"]
    for key in keys[:-1]:
        value = values[key]
        require(value and "=" not in value and "\n" not in value and
                "\r" not in value, "TERMINAL_WITNESS_LINE_VALUE_INVALID")
        out.append(f"{key}={value}\n")
    prefix = "".join(out).encode("ascii")
    out.append(f"{hash_key}={sha256(prefix)}\n")
    return "".join(out).encode("ascii")


def read_boot_id() -> str:
    try:
        value = BOOT_ID_PATH.read_text(encoding="ascii", errors="strict")
    except (OSError, UnicodeError) as error:
        raise VerifierError("TERMINAL_WITNESS_BOOT_ID_INVALID") from error
    require(value.endswith("\n") and BOOT_ID.fullmatch(value[:-1]) is not None
            and value[:-1] != "00000000-0000-0000-0000-000000000000",
            "TERMINAL_WITNESS_BOOT_ID_INVALID")
    return value[:-1]


@dataclass(frozen=True)
class Artifact:
    path: Path
    payload: bytes
    document: dict[str, Any]

    @property
    def file_sha256(self) -> str:
        return sha256(self.payload)


def read_json_artifact(path: Path, schema: str, reason: str) -> Artifact:
    payload = secure_read(
        path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o400, 0o600}), maximum=MAX_JSON_BYTES,
        reason=reason)
    document = strict_json(payload, reason)
    validate_seal(document, schema, reason)
    return Artifact(path, payload, document)


def load_producer_module(path: Path = PRODUCER_MODULE) -> ModuleType:
    payload = secure_read(
        path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o440, 0o555, 0o755}), maximum=MAX_JSON_BYTES,
        reason="TERMINAL_WITNESS_PRODUCER_IMAGE_INVALID")
    del payload
    loader = importlib.machinery.SourceFileLoader(
        "hepta_terminal_witness_producer_runtime", str(path))
    specification = importlib.util.spec_from_loader(loader.name, loader)
    require(specification is not None and specification.loader is not None,
            "TERMINAL_WITNESS_PRODUCER_IMAGE_INVALID")
    module = importlib.util.module_from_spec(specification)
    sys.modules[loader.name] = module
    specification.loader.exec_module(module)
    return module


CommandRunner = Callable[[Sequence[str], int], subprocess.CompletedProcess[bytes]]


def run_command(arguments: Sequence[str], timeout: int) \
        -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            tuple(arguments), check=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=SAFE_ENVIRONMENT, cwd="/", close_fds=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise VerifierError("TERMINAL_WITNESS_COMMAND_FAILED") from error
    require(
        len(result.stdout) <= MAX_COMMAND_BYTES and
        len(result.stderr) <= MAX_COMMAND_BYTES,
        "TERMINAL_WITNESS_COMMAND_OUTPUT_EXCEEDED")
    return result


@dataclass(frozen=True)
class BoundaryObservation:
    boot_id: str
    generation: int
    state_sha256: str
    receipt_file_sha256: str
    receipt_body_sha256: str
    publisher_pid: int
    publisher_start_ticks: int
    authorized_connector_count: int
    broker_socket_count: int
    broker_process_count: int
    broker_credential_count: int
    execution_service_inactive: bool
    paper_units_inactive: bool
    kill_switches_engaged: bool

    @property
    def stable_identity(self) -> tuple[Any, ...]:
        return (
            self.boot_id, self.generation, self.state_sha256,
            self.publisher_pid, self.publisher_start_ticks,
            self.authorized_connector_count)


class ProductionBoundaryObserver:
    def __init__(
            self, *, command: CommandRunner = run_command,
            producer_module: Path = PRODUCER_MODULE) -> None:
        self.command = command
        self.producer = load_producer_module(producer_module)

    def _show(self, unit: str) -> dict[str, str]:
        result = self.command((
            "/usr/bin/systemctl", "show", "--no-pager",
            "--property=LoadState,ActiveState,SubState,Job,MainPID,ControlPID",
            unit), 15)
        require(result.returncode == 0 and result.stderr == b"",
                "TERMINAL_WITNESS_SYSTEMD_STATE_INVALID")
        values: dict[str, str] = {}
        try:
            for line in result.stdout.decode("ascii", errors="strict").splitlines():
                key, value = line.split("=", 1)
                require(key not in values,
                        "TERMINAL_WITNESS_SYSTEMD_STATE_INVALID")
                values[key] = value
        except (UnicodeError, ValueError) as error:
            raise VerifierError(
                "TERMINAL_WITNESS_SYSTEMD_STATE_INVALID") from error
        require(set(values) == {
            "LoadState", "ActiveState", "SubState", "Job", "MainPID",
            "ControlPID"}, "TERMINAL_WITNESS_SYSTEMD_STATE_INVALID")
        return values

    def observe(self) -> BoundaryObservation:
        identities_payload = secure_read(
            PAPER_IDENTITIES_PATH, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o600}),
            maximum=64 * 1024,
            reason="TERMINAL_WITNESS_DENY_ALL_INPUT_INVALID")
        identities = strict_json(
            identities_payload, "TERMINAL_WITNESS_DENY_ALL_INPUT_INVALID")
        require(
            set(identities) == {
                "schema", "version", "identities", "paper_authorized",
                "live_authorized", "source_policy_sha256"} and
            identities.get("schema") == PAPER_IDENTITIES_SCHEMA and
            identities.get("version") == 1 and
            identities.get("identities") == [] and
            identities.get("paper_authorized") is False and
            identities.get("live_authorized") is False and
            DIGEST.fullmatch(str(
                identities.get("source_policy_sha256"))) is not None,
            "TERMINAL_WITNESS_DENY_ALL_INPUT_INVALID")
        try:
            os.lstat(PAPER_EGRESS_DROP_IN)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise VerifierError(
                "TERMINAL_WITNESS_DENY_ALL_INPUT_INVALID") from error
        else:
            raise VerifierError("TERMINAL_WITNESS_DENY_ALL_INPUT_INVALID")
        receipt_result = self.command((
            "/usr/bin/python3.12", "-I", "-S", str(BROKER_POLICY_HELPER),
            "--read-current-boundary"), 15)
        require(
            receipt_result.returncode == 0 and
            receipt_result.stderr == b"", "TERMINAL_WITNESS_EGRESS_INVALID")
        receipt = strict_json(
            receipt_result.stdout, "TERMINAL_WITNESS_EGRESS_INVALID")
        validate_seal(receipt, EGRESS_SCHEMA, "TERMINAL_WITNESS_EGRESS_INVALID")
        require(
            receipt.get("status") == "EXACT_DENY_ALL" and
            receipt.get("state") == "DENY_ALL" and
            DIGEST.fullmatch(str(receipt.get("source_policy_sha256"))) is not None and
            receipt.get("authorized_connector_count") == 0 and
            receipt.get("authorized_connectors") == [] and
            receipt.get("authorized_uids") == [] and
            receipt.get("protected_port_count") == 4 and
            receipt.get("paper_authorized") is False and
            receipt.get("live_authorized") is False and
            type(receipt.get("generation")) is int and
            receipt["generation"] > 0 and
            DIGEST.fullmatch(str(receipt.get("state_sha256"))) is not None,
            "TERMINAL_WITNESS_EGRESS_INVALID")
        egress = self._show(BROKER_POLICY_UNIT)
        require(
            egress["LoadState"] == "loaded" and
            egress["ActiveState"] == "active" and
            egress["SubState"] == "running" and egress["Job"] == "" and
            egress["ControlPID"] == "0" and
            egress["MainPID"].isdecimal() and
            int(egress["MainPID"]) == receipt["publisher_pid"],
            "TERMINAL_WITNESS_EGRESS_INVALID")
        states = {unit: self._show(unit) for unit in PAPER_INERT_UNITS}
        inactive = all(
            value["LoadState"] == "loaded" and
            value["ActiveState"] == "inactive" and
            value["SubState"] == "dead" and value["Job"] == "" and
            value["MainPID"] == "0" and value["ControlPID"] == "0"
            for value in states.values())
        processes, sockets = \
            self.producer.ProductionReadOnlyObserver.\
                _process_and_socket_inventory()
        credentials = self.producer.ProductionReadOnlyObserver.\
            _credential_inventory()
        for path, gid in (
                (DOMAIN_KILL_SWITCH, 2121),
                (GLOBAL_KILL_SWITCH, 2003)):
            payload = secure_read(
                path, expected_uid=ROOT_UID, expected_gid=gid,
                modes=frozenset({0o440}), maximum=8,
                reason="TERMINAL_WITNESS_KILL_SWITCH_INVALID")
            require(payload == b"engaged",
                    "TERMINAL_WITNESS_KILL_SWITCH_INVALID")
        return BoundaryObservation(
            boot_id=str(receipt["boot_id"]), generation=receipt["generation"],
            state_sha256=str(receipt["state_sha256"]),
            receipt_file_sha256=sha256(receipt_result.stdout),
            receipt_body_sha256=str(receipt["body_sha256"]),
            publisher_pid=int(receipt["publisher_pid"]),
            publisher_start_ticks=int(receipt["publisher_start_ticks"]),
            authorized_connector_count=0, broker_socket_count=sockets,
            broker_process_count=processes,
            broker_credential_count=credentials,
            execution_service_inactive=inactive,
            paper_units_inactive=inactive, kill_switches_engaged=True)


def validate_boundary(
        observation: BoundaryObservation, *, expected_boot: str,
        expected_generation: int, expected_sha256: str) -> None:
    require(
        type(observation) is BoundaryObservation and
        observation.boot_id == expected_boot and
        observation.generation == expected_generation and
        observation.state_sha256 == expected_sha256 and
        DIGEST.fullmatch(observation.receipt_file_sha256) is not None and
        DIGEST.fullmatch(observation.receipt_body_sha256) is not None and
        observation.authorized_connector_count == 0 and
        observation.broker_socket_count == 0 and
        observation.broker_process_count == 0 and
        observation.broker_credential_count == 0 and
        observation.execution_service_inactive and
        observation.paper_units_inactive and observation.kill_switches_engaged,
        "TERMINAL_WITNESS_CURRENT_BOUNDARY_INVALID")


def acquire_host_lease(*, require_owner: bool | None = True) \
        -> tuple[int, int, bytes | None]:
    reason = "TERMINAL_WITNESS_HOST_AUTHORITY_LEASE_INVALID"
    directory = os.open(
        HOST_AUTHORITY_DIRECTORY,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    lease = -1
    try:
        metadata = os.fstat(directory)
        require(
            stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == ROOT_UID and
            metadata.st_gid == ROOT_GID and
            stat.S_IMODE(metadata.st_mode) == 0o700, reason)
        lease = os.open(
            HOST_AUTHORITY_LEASE_PATH.name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory)
        lease_metadata = os.fstat(lease)
        require(
            stat.S_ISREG(lease_metadata.st_mode) and
            lease_metadata.st_nlink == 1 and lease_metadata.st_size == 0 and
            lease_metadata.st_uid == ROOT_UID and
            lease_metadata.st_gid == ROOT_GID and
            stat.S_IMODE(lease_metadata.st_mode) == 0o600, reason)
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise VerifierError("TERMINAL_WITNESS_HOST_AUTHORITY_LEASE_BUSY") \
                from error
        if require_owner:
            owner = secure_read(
                HOST_AUTHORITY_OWNER_PATH, expected_uid=ROOT_UID,
                expected_gid=ROOT_GID, modes=frozenset({0o600}),
                maximum=MAX_JSON_BYTES,
                reason="TERMINAL_WITNESS_OWNER_INVALID")
        else:
            try:
                os.stat(
                    HOST_AUTHORITY_OWNER_PATH.name, dir_fd=directory,
                    follow_symlinks=False)
            except FileNotFoundError:
                owner = None
            except OSError as error:
                raise VerifierError(
                    "TERMINAL_WITNESS_OWNER_INVALID") from error
            else:
                if require_owner is None:
                    owner = secure_read(
                        HOST_AUTHORITY_OWNER_PATH, expected_uid=ROOT_UID,
                        expected_gid=ROOT_GID, modes=frozenset({0o600}),
                        maximum=MAX_JSON_BYTES,
                        reason="TERMINAL_WITNESS_OWNER_INVALID")
                    return directory, lease, owner
                raise VerifierError("TERMINAL_WITNESS_OWNER_PRESENT")
        return directory, lease, owner
    except Exception:
        if lease >= 0:
            os.close(lease)
        os.close(directory)
        raise


def release_host_lease(directory: int, lease: int) -> None:
    try:
        fcntl.flock(lease, fcntl.LOCK_UN)
    finally:
        os.close(lease)
        os.close(directory)


def validate_request(path: Path) -> dict[str, Any]:
    payload = secure_read(
        path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o400, 0o440, 0o600}), maximum=64 * 1024,
        reason="TERMINAL_WITNESS_REQUEST_INVALID")
    request = strict_json(payload, "TERMINAL_WITNESS_REQUEST_INVALID")
    require(set(request) == REQUEST_FIELDS, "TERMINAL_WITNESS_REQUEST_INVALID")
    validate_seal(
        request, REQUEST_SCHEMA, "TERMINAL_WITNESS_REQUEST_INVALID")
    require(request.get("status") == "REQUESTED" and
            type(request.get("token_generation")) is int and
            request["token_generation"] > 0,
            "TERMINAL_WITNESS_REQUEST_INVALID")
    for field in (
            "expected_campaign_id", "expected_cycle_id",
            "expected_recovery_id", "expected_finalization_id"):
        require(isinstance(request.get(field), str) and
                IDENTIFIER.fullmatch(request[field]) is not None,
                "TERMINAL_WITNESS_REQUEST_INVALID")
    require(DIGEST.fullmatch(str(
        request.get("expected_source_baseline_sha256"))) is not None,
        "TERMINAL_WITNESS_REQUEST_INVALID")
    for field in (
            "transport_cutoff_receipt", "provider_trust_policy", "challenge",
            "signed_account_evidence", "provider_request",
            "provider_response", "terminal_witness", "token_file"):
        value = request.get(field)
        require(isinstance(value, str) and Path(value).is_absolute() and
                Path(value) == Path(os.path.normpath(value)),
                "TERMINAL_WITNESS_REQUEST_INVALID")
    paths = [Path(request[field]) for field in (
        "transport_cutoff_receipt", "provider_trust_policy", "challenge",
        "signed_account_evidence", "provider_request", "provider_response",
        "terminal_witness", "token_file")]
    require(len(set(paths)) == len(paths), "TERMINAL_WITNESS_REQUEST_INVALID")
    return request


def validate_cutoff_request(path: Path) -> dict[str, Any]:
    reason = "TERMINAL_CUTOFF_REQUEST_INVALID"
    payload = secure_read(
        path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o400, 0o440, 0o600}), maximum=64 * 1024,
        reason=reason)
    request = strict_json(payload, reason)
    require(set(request) == CUTOFF_REQUEST_FIELDS, reason)
    validate_seal(request, CUTOFF_REQUEST_SCHEMA, reason)
    require(request.get("status") == "REQUESTED", reason)
    for field in (
            "expected_campaign_id", "expected_cycle_id",
            "expected_recovery_id", "expected_finalization_id"):
        require(isinstance(request.get(field), str) and
                IDENTIFIER.fullmatch(request[field]) is not None, reason)
    for field in (
            "expected_source_baseline_sha256",
            "preliminary_finalization_receipt_sha256", "owner_set_sha256",
            "account_id_sha256", "broker_socket_identity_sha256",
            "known_mutation_command_set_sha256",
            "known_correlation_set_sha256"):
        require(DIGEST.fullmatch(str(request.get(field))) is not None and
                request[field] != "sha256:" + "0" * 64, reason)
    for field, maximum in (
            ("owner_count", 128), ("service_pid", (1 << 31) - 1),
            ("service_start_ticks", (1 << 63) - 1),
            ("mutation_fence_generation", (1 << 63) - 1),
            ("token_generation", (1 << 63) - 1),
            ("known_mutation_command_count", 4096),
            ("known_correlation_count", 4096)):
        value = request.get(field)
        require(type(value) is int and 0 < value <= maximum, reason)
    canonical = request.get("owner_set_canonical_hex")
    require(isinstance(canonical, str) and 0 < len(canonical) <= 131072 and
            len(canonical) % 2 == 0 and
            re.fullmatch(r"[0-9a-f]+", canonical) is not None, reason)
    token_file = request.get("token_file")
    require(isinstance(token_file, str) and Path(token_file).is_absolute() and
            Path(token_file) == Path(os.path.normpath(token_file)) and
            request["token_generation"] ==
                request["mutation_fence_generation"], reason)
    return request


def prepare_terminal_witness(
        request: Mapping[str, Any], command: CommandRunner) -> bytes:
    """Create/replay the durable HPT1 intent before any host cutoff claim."""
    reason = "TERMINAL_CUTOFF_PREPARE_REJECTED"
    token_path = Path(str(request["token_file"]))
    token = secure_read(
        token_path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o400}), maximum=4096, reason=reason)
    result = command((
        str(SESSIONCTL), "--socket", str(SUPERVISOR_SOCKET),
        "paper-terminal-witness-prepare", "--token-file", str(token_path),
        "--generation", str(request["token_generation"]), "--recovery-id",
        str(request["expected_recovery_id"]), "--finalization-id",
        str(request["expected_finalization_id"]),
        "--expected-owner-set-sha256", str(request["owner_set_sha256"]),
        "--expected-owner-count", str(request["owner_count"]),
        "--receipt-sha256",
        str(request["preliminary_finalization_receipt_sha256"]),
        "--token-owner-uid", "0"), 120)
    require(result.stderr == b"" and 0 < len(result.stdout) <= MAX_COMMAND_BYTES,
            reason)
    response = command_json(result.stdout, reason)
    accepted = response.get("accepted")
    reason_code = response.get("reason_code")
    require(
        ((result.returncode == 0 and accepted is True and
          reason_code == "PAPER_TERMINAL_WITNESS_PREPARED") or
         (result.returncode == 4 and accepted is False and
          reason_code == "PAPER_TERMINAL_WITNESS_PREPARE_INTENT_PENDING")) and
        response.get("paper_finalization_state") == "AUDIT_SEALED" and
        response.get("paper_finalization_required") is True and
        response.get("recovery_id") == request["expected_recovery_id"] and
        response.get("finalization_id") == request["expected_finalization_id"] and
        response.get("expected_owner_set_sha256") ==
            request["owner_set_sha256"] and
        response.get("expected_owner_count") == request["owner_count"] and
        response.get("finalization_receipt_sha256") ==
            request["preliminary_finalization_receipt_sha256"] and
        response.get("lease_generation") == request["token_generation"],
        reason)
    require(secure_read(
        token_path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o400}), maximum=4096, reason=reason) == token, reason)
    return token


def validate_owner_set(
        encoded: str, expected_sha256: str, expected_count: int,
        account: str, domain: str) -> None:
    reason = "TERMINAL_CUTOFF_OWNER_SET_INVALID"
    try:
        canonical = bytes.fromhex(encoded)
        text_value = canonical.decode("utf-8", errors="strict")
    except (UnicodeError, ValueError) as error:
        raise VerifierError(reason) from error
    require(canonical.endswith(b"\n") and sha256(canonical) == expected_sha256,
            reason)
    lines = text_value.splitlines()
    require(len(lines) == expected_count and lines == sorted(set(lines)), reason)
    for line in lines:
        fields = line.split("\t")
        require(len(fields) == 4 and DIGEST.fullmatch(fields[0]) is not None and
                fields[1].isdecimal() and int(fields[1]) > 0, reason)
        try:
            observed_account = bytes.fromhex(fields[2]).decode("utf-8")
            observed_domain = bytes.fromhex(fields[3]).decode("utf-8")
        except (UnicodeError, ValueError) as error:
            raise VerifierError(reason) from error
        require(observed_account == account and observed_domain == domain,
                reason)


def record_transport_cutoff(
        request: Mapping[str, Any], *, command: CommandRunner = run_command,
        observer: Any | None = None) -> dict[str, Any]:
    """Stop PAPER execution and durably record the post-HPT1 host cutoff."""
    token = prepare_terminal_witness(request, command)
    hpt1_payload = secure_read(
        HPT1_PATH, expected_uid=EXEC_UID, expected_gid=EXEC_GID,
        modes=frozenset({0o600}), maximum=16 * 1024,
        reason="TERMINAL_CUTOFF_HPT1_INVALID")
    hpt1 = validate_hpt1(hpt1_payload)
    require(
        hpt1["finalization_id"] == request["expected_finalization_id"] and
        hpt1["preliminary_finalization_receipt_sha256"] ==
            request["preliminary_finalization_receipt_sha256"] and
        hpt1["owner_execution_domain"] == PAPER_DOMAIN and
        sha256(hpt1["owner_account"].encode("ascii")) ==
            request["account_id_sha256"] and
        int(hpt1["recovery_ingress_fence"]) ==
            request["mutation_fence_generation"] ==
            request["token_generation"],
        "TERMINAL_CUTOFF_HPT1_BINDING_MISMATCH")
    validate_owner_set(
        str(request["owner_set_canonical_hex"]),
        str(request["owner_set_sha256"]), int(request["owner_count"]),
        hpt1["owner_account"], hpt1["owner_execution_domain"])

    # Do not hold the host-authority lease while stopping PAPER.  The active
    # preflight guard owns that same lease for its lifetime and must first run
    # its DENY_ALL/owner cleanup while systemd stops it.  Acquiring here before
    # the stop would turn every real cutoff into a lock-order deadlock (or an
    # immediate BUSY failure).  If another activation wins the small interval
    # after the stop, its non-cutoff owner is observed below and we fail closed.
    stopped = command(("/usr/bin/systemctl", "stop", *PAPER_UNITS), 120)
    require(stopped.returncode == 0 and stopped.stderr == b"" and
            stopped.stdout == b"", "TERMINAL_CUTOFF_STOP_FAILED")

    # The preflight stop revokes the live runtime owner and tightens the
    # kernel table, but the PAPER identity manifest/drop-in can still describe
    # the authorizing supervisor. Stop its lifecycle guardian, then run the
    # static fail-close transaction so those inputs are normalized and a new,
    # live --supervise-deny-all publisher owns the current boundary receipt.
    # A receipt from the former ACTIVE publisher is never reused.
    guardian_stopped = command((
        "/usr/bin/systemctl", "stop", LOCAL_PAPER_AUTHORITY_UNIT), 180)
    require(guardian_stopped.returncode == 0 and
            guardian_stopped.stderr == b"" and
            guardian_stopped.stdout == b"",
            "TERMINAL_CUTOFF_GUARDIAN_STOP_FAILED")
    fail_closed = command((
        "/usr/bin/systemctl", "start", LOCAL_PAPER_FAIL_CLOSE_UNIT), 180)
    require(fail_closed.returncode == 0 and fail_closed.stderr == b"" and
            fail_closed.stdout == b"",
            "TERMINAL_CUTOFF_DENY_ALL_CUSTODIAN_FAILED")

    directory, lease, owner = acquire_host_lease(require_owner=None)
    try:
        owner_document: dict[str, Any] | None = None
        expected_owner_payload = owner
        if owner is not None:
            owner_document = strict_json(owner, "TERMINAL_CUTOFF_OWNER_INVALID")
            require(set(owner_document) == CUTOFF_OWNER_FIELDS,
                    "TERMINAL_CUTOFF_OWNER_INVALID")
            validate_seal(
                owner_document, CUTOFF_OWNER_SCHEMA,
                "TERMINAL_CUTOFF_OWNER_INVALID")
        current_observer = observer or ProductionBoundaryObserver(
            command=command)
        first = current_observer.observe()
        validate_boundary(
            first, expected_boot=first.boot_id,
            expected_generation=first.generation,
            expected_sha256=first.state_sha256)
        second = current_observer.observe()
        validate_boundary(
            second, expected_boot=first.boot_id,
            expected_generation=first.generation,
            expected_sha256=first.state_sha256)
        require(first.stable_identity == second.stable_identity,
                "TERMINAL_CUTOFF_BOUNDARY_DRIFT")
        require(secure_read(
            HPT1_PATH, expected_uid=EXEC_UID, expected_gid=EXEC_GID,
            modes=frozenset({0o600}), maximum=16 * 1024,
            reason="TERMINAL_CUTOFF_HPT1_INVALID") == hpt1_payload,
            "TERMINAL_CUTOFF_HPT1_DRIFT")
        require(secure_read(
            Path(str(request["token_file"])), expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o400}), maximum=4096,
            reason="TERMINAL_CUTOFF_PREPARE_REJECTED") == token,
            "TERMINAL_CUTOFF_TOKEN_DRIFT")
        completed_ms = time.time_ns() // 1_000_000
        completed_mono = time.monotonic_ns()
        body: dict[str, Any] = {
            "schema": CUTOFF_SCHEMA, "version": 1,
            "status": "TRANSPORT_CUTOFF_DURABLE",
            "completed_at_ms": completed_ms,
            "completed_monotonic_ns": completed_mono, "round": 114,
            "domain": DOMAIN,
            "campaign_id": request["expected_campaign_id"],
            "source_baseline_sha256":
                request["expected_source_baseline_sha256"],
            "cycle_id": request["expected_cycle_id"],
            "recovery_id": request["expected_recovery_id"],
            "finalization_id": request["expected_finalization_id"],
            "boot_id": second.boot_id, "service_pid": request["service_pid"],
            "service_start_ticks": request["service_start_ticks"],
            "broker_socket_identity_sha256":
                request["broker_socket_identity_sha256"],
            "account_id_sha256": request["account_id_sha256"],
            "owner_ids": sorted({line.split("\t", 1)[0] for line in
                bytes.fromhex(str(request["owner_set_canonical_hex"]))
                    .decode("utf-8").splitlines()}),
            "owner_set_sha256": request["owner_set_sha256"],
            "owner_set_canonical_hex": request["owner_set_canonical_hex"],
            "owner_count": request["owner_count"],
            "execution_service_epoch":
                hpt1["terminalization_service_epoch"],
            "execution_service_fencing_generation": int(
                hpt1["terminalization_service_fencing_generation"]),
            "mutation_fence_generation":
                request["mutation_fence_generation"],
            "known_mutation_command_set_sha256":
                request["known_mutation_command_set_sha256"],
            "known_mutation_command_count":
                request["known_mutation_command_count"],
            "known_correlation_set_sha256":
                request["known_correlation_set_sha256"],
            "known_correlation_count": request["known_correlation_count"],
            "egress_policy_generation": second.generation,
            "egress_policy_sha256": second.state_sha256,
            "authorized_connectors": 0, "authorized_uids": [],
            "broker_socket_count": 0, "broker_process_count": 0,
            "credential_exposure_count": 0,
            "process_inventory_complete": True,
            "socket_inventory_complete": True,
            "credential_inventory_complete": True,
            "mutation_gate_closed": True, "reconnect_permitted": False,
            **{field: False for field in BOUNDARY_FIELDS},
        }
        if owner_document is not None:
            planned = owner_document.get("transport_cutoff_document")
            require(
                isinstance(planned, dict) and
                set(planned) == TRANSPORT_CUTOFF_FIELDS and
                owner_document.get("schema") == CUTOFF_OWNER_SCHEMA and
                owner_document.get("version") == 1 and
                owner_document.get("status") ==
                    "CUTOFF_HELD_FOR_TERMINAL_CHALLENGE" and
                owner_document.get("boot_id") == second.boot_id and
                owner_document.get("campaign_id") ==
                    request["expected_campaign_id"] and
                owner_document.get("cycle_id") ==
                    request["expected_cycle_id"] and
                owner_document.get("recovery_id") ==
                    request["expected_recovery_id"] and
                owner_document.get("finalization_id") ==
                    request["expected_finalization_id"] and
                owner_document.get("terminalizing_latch_sha256") ==
                    sha256(hpt1_payload) and
                owner_document.get("next_consumer") ==
                    "TERMINAL_ACCOUNT_CHALLENGE" and
                all(owner_document.get(field) is False
                    for field in BOUNDARY_FIELDS),
                "TERMINAL_CUTOFF_OWNER_INVALID")
            validate_seal(planned, CUTOFF_SCHEMA,
                          "TERMINAL_CUTOFF_OWNER_INVALID")
            planned_payload = canonical_json(planned)
            require(
                owner_document.get("transport_cutoff_file_sha256") ==
                    sha256(planned_payload) and
                owner_document.get("transport_cutoff_body_sha256") ==
                    planned.get("body_sha256"),
                "TERMINAL_CUTOFF_OWNER_INVALID")
            old_stable = dict(planned)
            new_stable = dict(body)
            for field in ("completed_at_ms", "completed_monotonic_ns"):
                old_stable.pop(field, None)
                new_stable.pop(field, None)
            old_stable.pop("body_sha256", None)
            require(old_stable == new_stable,
                    "TERMINAL_CUTOFF_OWNER_CONFLICT")
            document = planned
            payload = planned_payload
        else:
            document, payload = sealed_document(body)
            require(set(document) == TRANSPORT_CUTOFF_FIELDS,
                    "TERMINAL_CUTOFF_OUTPUT_INVALID")
            owner_document, owner_payload = sealed_document({
                "schema": CUTOFF_OWNER_SCHEMA, "version": 1,
                "status": "CUTOFF_HELD_FOR_TERMINAL_CHALLENGE",
                "boot_id": second.boot_id,
                "campaign_id": request["expected_campaign_id"],
                "cycle_id": request["expected_cycle_id"],
                "recovery_id": request["expected_recovery_id"],
                "finalization_id": request["expected_finalization_id"],
                "terminalizing_latch_sha256": sha256(hpt1_payload),
                "transport_cutoff_file_sha256": sha256(payload),
                "transport_cutoff_body_sha256": document["body_sha256"],
                "transport_cutoff_document": document,
                "next_consumer": "TERMINAL_ACCOUNT_CHALLENGE",
                **{field: False for field in BOUNDARY_FIELDS},
            })
            require(set(owner_document) == CUTOFF_OWNER_FIELDS,
                    "TERMINAL_CUTOFF_OWNER_INVALID")
            # Owner first: a crash from here is fail-closed and the embedded
            # exact cutoff bytes let the same request finish publication.
            publish_no_replace(
                HOST_AUTHORITY_OWNER_PATH, owner_payload, MAX_JSON_BYTES,
                mode=0o600)
            require(secure_read(
                HOST_AUTHORITY_OWNER_PATH, expected_uid=ROOT_UID,
                expected_gid=ROOT_GID, modes=frozenset({0o600}),
                maximum=MAX_JSON_BYTES,
                reason="TERMINAL_CUTOFF_OWNER_INVALID") == owner_payload,
                "TERMINAL_CUTOFF_OWNER_INVALID")
            expected_owner_payload = owner_payload
        try:
            existing = read_json_artifact(
                CUTOFF_OUTPUT, CUTOFF_SCHEMA,
                "TERMINAL_CUTOFF_OUTPUT_INVALID")
        except VerifierError:
            try:
                os.lstat(CUTOFF_OUTPUT)
            except FileNotFoundError:
                existing = None
            else:
                raise
        if existing is not None:
            require(set(existing.document) == TRANSPORT_CUTOFF_FIELDS,
                    "TERMINAL_CUTOFF_OUTPUT_INVALID")
            require(existing.payload == payload and
                    existing.document == document,
                    "TERMINAL_CUTOFF_OUTPUT_CONFLICT")
            replay = True
        else:
            replay = publish_no_replace(
                CUTOFF_OUTPUT, payload, MAX_JSON_BYTES)
        require(secure_read(
            CUTOFF_OUTPUT, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o400}), maximum=MAX_JSON_BYTES,
            reason="TERMINAL_CUTOFF_OUTPUT_INVALID") == payload,
            "TERMINAL_CUTOFF_OUTPUT_INVALID")
        require(expected_owner_payload is not None and secure_read(
            HOST_AUTHORITY_OWNER_PATH, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o600}),
            maximum=MAX_JSON_BYTES,
            reason="TERMINAL_CUTOFF_OWNER_INVALID") == expected_owner_payload,
            "TERMINAL_CUTOFF_OWNER_INVALID")
        final = current_observer.observe()
        validate_boundary(
            final, expected_boot=first.boot_id,
            expected_generation=first.generation,
            expected_sha256=first.state_sha256)
        require(final.stable_identity == first.stable_identity,
                "TERMINAL_CUTOFF_BOUNDARY_DRIFT")
        return document | {"_terminal_replay": replay}
    finally:
        release_host_lease(directory, lease)


def attest_bundle(request: Mapping[str, Any], command: CommandRunner) -> None:
    arguments = (
        "/usr/bin/python3.12", "-I", "-S", str(ATTESTOR), "--run",
        "--validate-terminal-witness", "--transport-cutoff-receipt",
        str(request["transport_cutoff_receipt"]), "--provider-trust-policy",
        str(request["provider_trust_policy"]), "--challenge",
        str(request["challenge"]), "--signed-account-evidence",
        str(request["signed_account_evidence"]), "--provider-request",
        str(request["provider_request"]), "--provider-response",
        str(request["provider_response"]), "--terminal-witness",
        str(request["terminal_witness"]),
        "--expected-source-baseline-sha256",
        str(request["expected_source_baseline_sha256"]),
        "--expected-campaign-id", str(request["expected_campaign_id"]),
        "--expected-cycle-id", str(request["expected_cycle_id"]),
        "--expected-recovery-id", str(request["expected_recovery_id"]),
        "--expected-finalization-id",
        str(request["expected_finalization_id"]))
    result = command(arguments, 120)
    require(
        result.returncode == 0 and result.stderr == b"" and
        result.stdout ==
            b"STATUS=POST_CUTOFF_TERMINAL_FLAT_PROVEN\n"
            b"PAPER_AUTHORIZED=false\n"
            b"ORDER_SUBMISSION_AUTHORIZED=false\n",
        "TERMINAL_WITNESS_BUNDLE_ATTESTATION_FAILED")


def _boolean(value: bool) -> str:
    return "1" if value else "0"


def stable_values(
        *, request: Mapping[str, Any], cutoff: Artifact, trust: Artifact,
        challenge: Artifact, evidence: Artifact, witness: Artifact,
        hpt1: Mapping[str, str], hpt1_sha256: str,
        boundary: BoundaryObservation) -> dict[str, str]:
    c = cutoff.document
    t = trust.document
    w = witness.document
    envelope = evidence.document
    payload = envelope.get("payload")
    require(isinstance(payload, dict), "TERMINAL_WITNESS_EVIDENCE_INVALID")
    try:
        signature = base64.b64decode(
            str(envelope["signature_base64"]).encode("ascii"), validate=True)
    except (KeyError, UnicodeError, ValueError) as error:
        raise VerifierError("TERMINAL_WITNESS_EVIDENCE_INVALID") from error
    signed_payload_sha = sha256(canonical_json(payload))
    signature_sha = sha256(signature)
    signature_proof = w.get("signature_verification")
    require(
        envelope.get("schema") == SIGNED_ENVELOPE_SCHEMA and
        isinstance(signature_proof, dict) and
        signature_proof.get("signed_payload_sha256") == signed_payload_sha and
        signature_proof.get("signature_sha256") == signature_sha,
        "TERMINAL_WITNESS_EVIDENCE_INVALID")
    identity = {
        "campaign_id": request["expected_campaign_id"],
        "cycle_id": request["expected_cycle_id"],
        "recovery_id": request["expected_recovery_id"],
        "finalization_id": request["expected_finalization_id"],
        "source_baseline_sha256": request["expected_source_baseline_sha256"],
    }
    require(
        all(c.get(field) == value and w.get(field) == value
            for field, value in identity.items()) and
        c.get("domain") == DOMAIN and w.get("domain") == DOMAIN and
        c.get("boot_id") == boundary.boot_id == w.get("boot_id") and
        c.get("egress_policy_generation") == boundary.generation ==
            w.get("egress_policy_generation") and
        c.get("egress_policy_sha256") == boundary.state_sha256 ==
            w.get("egress_policy_sha256") and
        hpt1["finalization_id"] == identity["finalization_id"] and
        hpt1["preliminary_finalization_receipt_sha256"] ==
            str(c.get("preliminary_finalization_receipt_sha256",
                      hpt1["preliminary_finalization_receipt_sha256"])) and
        hpt1["owner_account"] and ACCOUNT.fullmatch(
            hpt1["owner_account"]) is not None and
        hpt1["owner_execution_domain"] == PAPER_DOMAIN and
        hpt1["terminalization_service_epoch"] ==
            c.get("execution_service_epoch") ==
            w.get("execution_service_epoch") and
        int(hpt1["terminalization_service_fencing_generation"]) ==
            c.get("execution_service_fencing_generation") ==
            w.get("execution_service_fencing_generation") and
        int(hpt1["recovery_ingress_fence"]) ==
            request["token_generation"] and
        hpt1["terminalization_generation"] == "1" and
        c.get("account_id_sha256") == w.get("account_id_sha256") ==
            sha256(hpt1["owner_account"].encode("ascii")),
        "TERMINAL_WITNESS_IDENTITY_MISMATCH")
    complete_fields = (
        "active_orders_complete", "completed_orders_complete",
        "executions_complete", "positions_complete", "cash_fx_complete",
        "risk_complete")
    require(
        w.get("terminal_proof_kind") == PROOF_KIND and
        w.get("provider_capability") == PROVIDER_CAPABILITY and
        all(w.get(field) is True for field in complete_fields) and
        w.get("query_started_after_challenge") is True and
        w.get("consistency_dominates_cutoff") is True and
        w.get("consistency_dominates_all_mutations") is True and
        w.get("active_order_count") == 0 and w.get("position_count") == 0 and
        w.get("cash_fx_exposure_count") == 0 and
        w.get("gross_absolute_position") == "0" and
        w.get("gross_fx_exposure") == "0" and w.get("gross_risk") == "0" and
        w.get("unknown_mutation_command_count") == 0 and
        w.get("unresolved_mutation_command_count") == 0 and
        w.get("settled_mutation_command_count") ==
            w.get("known_mutation_command_count") and
        w.get("read_only_authority") is True and
        w.get("mutation_attempted") is False and
        all(w.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_authorized",
            "direct_broker_access", "order_submission_authorized")),
        "TERMINAL_WITNESS_ACCOUNT_OR_AUTHORITY_INVALID")
    return {
        "schema": "hepta.paper-terminal-external-halt-commit-capsule.v1",
        "version": "1", "status": "POST_CUTOFF_TERMINAL_WITNESS_VERIFIED",
        "terminal_proof_kind": PROOF_KIND,
        "recovery_id": str(identity["recovery_id"]),
        "finalization_id": str(identity["finalization_id"]),
        "campaign_id": str(identity["campaign_id"]),
        "cycle_id": str(identity["cycle_id"]),
        "expected_owner_set_sha256": str(w["owner_set_sha256"]),
        "expected_owner_count": str(w["owner_count"]),
        "owner_set_canonical_hex": str(w["owner_set_canonical_hex"]),
        "preliminary_finalization_receipt_sha256":
            hpt1["preliminary_finalization_receipt_sha256"],
        "owner_agent_id": hpt1["owner_agent_id"],
        "owner_session_id": hpt1["owner_session_id"],
        "owner_account": hpt1["owner_account"],
        "owner_execution_domain": hpt1["owner_execution_domain"],
        "account_id_sha256": str(w["account_id_sha256"]),
        "execution_service_epoch": hpt1["terminalization_service_epoch"],
        "execution_service_fencing_generation":
            hpt1["terminalization_service_fencing_generation"],
        "recovery_ingress_fence": hpt1["recovery_ingress_fence"],
        "terminalization_generation": "1",
        "terminalizing_latch_sha256": hpt1_sha256,
        "transport_cutoff_receipt_file_sha256": cutoff.file_sha256,
        "transport_cutoff_receipt_body_sha256": str(c["body_sha256"]),
        "post_cutoff_terminal_witness_file_sha256": witness.file_sha256,
        "post_cutoff_terminal_witness_body_sha256": str(w["body_sha256"]),
        "provider_trust_policy_file_sha256": trust.file_sha256,
        "provider_trust_policy_body_sha256": str(t["body_sha256"]),
        "provider_id": str(w["provider_id"]),
        "provider_capability": str(w["provider_capability"]),
        "signed_account_payload_sha256": signed_payload_sha,
        "signed_account_signature_sha256": signature_sha,
        "host_boot_id": boundary.boot_id,
        "egress_publisher_pid": str(boundary.publisher_pid),
        "egress_publisher_start_ticks":
            str(boundary.publisher_start_ticks),
        "egress_policy_generation": str(boundary.generation),
        "egress_policy_sha256": boundary.state_sha256,
        "query_started_after_challenge": "1",
        # Signed causal/atomic dominance, never cross-host wall-clock order.
        "observed_after_cutoff": "1",
        "snapshot_consistency": str(w["snapshot_consistency"]),
        "causal_watermark_dominates_cutoff": "1",
        "causal_watermark_dominates_all_mutations": "1",
        "account_queries_complete": "1",
        **{field: "1" for field in complete_fields},
        "known_mutation_command_set_sha256":
            str(w["known_mutation_command_set_sha256"]),
        "known_mutation_command_count": str(w["known_mutation_command_count"]),
        "known_correlation_set_sha256":
            str(w["known_correlation_set_sha256"]),
        "known_correlation_count": str(w["known_correlation_count"]),
        "all_known_mutation_commands_settled": "1",
        "settled_mutation_command_count":
            str(w["settled_mutation_command_count"]),
        "unknown_mutation_command_count": "0",
        "unresolved_mutation_command_count": "0",
        "unknown_active_order_count": "0", "active_order_count": "0",
        "position_count": "0", "nonzero_cash_fx_count": "0",
        "gross_absolute_position": "0", "gross_fx_exposure": "0",
        "gross_risk": "0", "mutation_connector_count": "0",
        "broker_socket_count": "0", "broker_process_count": "0",
        "broker_credential_count": "0", "execution_service_inactive": "1",
        "paper_units_inactive": "1", "execution_mutation_gate_closed": "1",
        "broker_transport_connected": "0", "broker_reconnect_permitted": "0",
        "read_only_authority": "1", "mutation_attempted": "0",
        "paper_authorized": "0", "live_authorized": "0",
        "mutation_authorized": "0", "direct_broker_access": "0",
        "order_submission_authorized": "0", "order_authorized": "0",
        "paper_only": "1", "authority_granted": "0",
        "terminal_witness_durable": "1",
    }


def validate_hpt1(payload: bytes) -> dict[str, str]:
    values, _prefix = parse_lines(
        payload, "HPT1", HPT1_KEYS, "TERMINAL_WITNESS_HPT1_INVALID")
    require(
        values["state"] == "TERMINALIZING" and
        all(IDENTIFIER.fullmatch(values[field]) is not None for field in (
            "finalization_id", "owner_agent_id", "owner_session_id",
            "owner_execution_domain", "terminalization_service_epoch")) and
        ACCOUNT.fullmatch(values["owner_account"]) is not None and
        DIGEST.fullmatch(values[
            "preliminary_finalization_receipt_sha256"]) is not None and
        all(value.isdecimal() and int(value) > 0 for value in (
            values["recovery_ingress_fence"],
            values["terminalization_service_fencing_generation"])) and
        values["terminalization_generation"] == "1",
        "TERMINAL_WITNESS_HPT1_INVALID")
    return values


def validate_hpw1(
        payload: bytes, *, stable: Mapping[str, str], capsule: bytes,
        hpt1_sha256: str) -> dict[str, str]:
    values, prefix = parse_lines(
        payload, "HPW1", HPW1_KEYS, "TERMINAL_WITNESS_HPW1_INVALID")
    require(values["latch_body_sha256"] == sha256(prefix) and
            values["schema"] ==
                "hepta.paper-terminal-external-halt-latch.v1" and
            values["version"] == "1" and
            values["state"] == "TERMINAL_EXTERNAL_HALTED" and
            values["terminal_proof_kind"] == PROOF_KIND and
            values["terminalizing_latch_sha256"] == hpt1_sha256 and
            values["commit_capsule_file_sha256"] == sha256(capsule) and
            all(values[field] == stable[field] for field in (
                "recovery_id", "finalization_id", "campaign_id", "cycle_id",
                "expected_owner_set_sha256", "expected_owner_count",
                "preliminary_finalization_receipt_sha256", "owner_account",
                "owner_execution_domain", "account_id_sha256",
                "execution_service_epoch",
                "execution_service_fencing_generation",
                "recovery_ingress_fence", "terminalization_generation",
                "transport_cutoff_receipt_file_sha256",
                "transport_cutoff_receipt_body_sha256",
                "post_cutoff_terminal_witness_file_sha256",
                "post_cutoff_terminal_witness_body_sha256")) and
            values["terminal_external_halt_latch_durable"] == "1" and
            values["paper_authorized"] == values["live_authorized"] ==
                values["mutation_authorized"] ==
                values["order_submission_authorized"] ==
                values["order_authorized"] == values["authority_granted"] == "0"
            and values["paper_only"] == "1",
            "TERMINAL_WITNESS_HPW1_INVALID")
    return values


def build_hpe1(
        stable: Mapping[str, str], hpw1_sha256: str) -> bytes:
    values = {key: stable[key] for key in HPC1_KEYS[:-1]}
    values.update({
        "schema": "hepta.paper-terminal-witness-evidence.v1",
        "status": "CURRENT_POST_CUTOFF_TERMINAL_WITNESS_VERIFIED",
        "terminal_external_halt_latch_sha256": hpw1_sha256,
        "terminal_external_halt_latch_durable": "1",
        "current_host_boundary_verified": "1",
    })
    return build_lines("HPE1", HPE1_KEYS, values, "evidence_body_sha256")


def validate_ack(
        result: subprocess.CompletedProcess[bytes], *, hpe1: bytes,
        hpt1_sha256: str, hpw1_sha256: str,
        stable: Mapping[str, str]) -> dict[str, Any]:
    reason = "TERMINAL_WITNESS_ACK_REJECTED"
    require(result.returncode == 0 and result.stderr == b"" and
            0 < len(result.stdout) <= MAX_COMMAND_BYTES, reason)
    response = command_json(result.stdout, reason)
    terminal_receipt = response.get("finalization_receipt")
    terminal_receipt_sha256 = response.get("finalization_receipt_sha256")
    require(
        isinstance(terminal_receipt, str) and
        isinstance(terminal_receipt_sha256, str) and
        DIGEST.fullmatch(terminal_receipt_sha256) is not None,
        reason)
    try:
        terminal_receipt_raw = terminal_receipt.encode(
            "ascii", errors="strict")
    except UnicodeError as error:
        raise VerifierError(reason) from error
    require(
        response.get("accepted") is True and
        0 < len(terminal_receipt_raw) <= MAX_HPE1_BYTES and
        sha256(terminal_receipt_raw) == terminal_receipt_sha256 and
        response.get("paper_finalization_state") == "ACKED" and
        response.get("reason_code") == "PAPER_FINALIZATION_TERMINAL_ACKED" and
        response.get("preliminary_finalization_receipt_sha256") ==
            stable["preliminary_finalization_receipt_sha256"] and
        response.get("terminal_proof_kind") == PROOF_KIND and
        response.get("terminal_latch_sha256") == hpt1_sha256 and
        response.get("terminal_external_halt_latch_sha256") == hpw1_sha256 and
        response.get("transport_cutoff_receipt_file_sha256") ==
            stable["transport_cutoff_receipt_file_sha256"] and
        response.get("post_cutoff_terminal_witness_file_sha256") ==
            stable["post_cutoff_terminal_witness_file_sha256"] and
        response.get("terminal_evidence_file_sha256") == sha256(hpe1) and
        response.get("terminal_evidence_body_sha256") ==
            parse_lines(hpe1, "HPE1", HPE1_KEYS, reason)[0][
                "evidence_body_sha256"] and
        response.get("terminal_external_latch_loaded") is True and
        response.get("terminal_current_evidence_verified") is True and
        type(response.get("terminal_replay")) is bool,
        reason)
    return response


def _ack_arguments(
        request: Mapping[str, Any], stable: Mapping[str, str],
        hpe1: bytes) -> tuple[str, ...]:
    return (
        str(SESSIONCTL), "--socket", str(SUPERVISOR_SOCKET),
        "paper-terminal-witness-ack", "--token-file",
        str(request["token_file"]), "--generation",
        str(request["token_generation"]), "--recovery-id",
        str(request["expected_recovery_id"]), "--finalization-id",
        str(request["expected_finalization_id"]),
        "--expected-owner-set-sha256",
        stable["expected_owner_set_sha256"], "--expected-owner-count",
        stable["expected_owner_count"], "--receipt-sha256",
        stable["preliminary_finalization_receipt_sha256"],
        "--terminal-evidence-file", str(EVIDENCE_OUTPUT),
        "--terminal-evidence-sha256", sha256(hpe1),
        "--token-owner-uid", "0")


def _validate_completion_documents(
        *, completion: Artifact, pointer: Artifact,
        request: Mapping[str, Any], hpt1_sha256: str, hpw1_sha256: str,
        hpe1: bytes, owner_payload: bytes | None = None) -> None:
    reason = "TERMINAL_WITNESS_COMPLETION_INVALID"
    document = completion.document
    current = pointer.document
    require(
        set(document) == TERMINAL_COMPLETION_FIELDS and
        set(current) == TERMINAL_COMPLETION_POINTER_FIELDS and
        document.get("status") == "TERMINAL_ACK_CHECKPOINTED" and
        current.get("status") == "TERMINAL_OWNER_RELEASE_AUTHORIZED" and
        document.get("boot_id") == current.get("boot_id") == read_boot_id()
        and document.get("recovery_id") ==
            request["expected_recovery_id"] == current.get("recovery_id") and
        document.get("finalization_id") ==
            request["expected_finalization_id"] ==
                current.get("finalization_id") and
        document.get("campaign_id") == request["expected_campaign_id"] ==
            current.get("campaign_id") and
        document.get("cycle_id") == request["expected_cycle_id"] ==
            current.get("cycle_id") and
        document.get("terminalizing_latch_sha256") == hpt1_sha256 and
        document.get("terminal_external_halt_latch_sha256") == hpw1_sha256 and
        document.get("terminal_evidence_file_sha256") == sha256(hpe1) and
        current.get("terminal_completion_path") == str(completion.path) and
        current.get("terminal_completion_file_sha256") ==
            completion.file_sha256 and
        current.get("terminal_completion_body_sha256") ==
            document.get("body_sha256") and
        document.get("owner_removal_required_after_commit") is True and
        document.get("terminal_replay_verified") is True and
        current.get("owner_removal_authorized") is True and
        all(document.get(field) is False for field in BOUNDARY_FIELDS) and
        all(current.get(field) is False for field in BOUNDARY_FIELDS), reason)
    if owner_payload is not None:
        owner_document = strict_json(
            owner_payload, "TERMINAL_WITNESS_OWNER_INVALID")
        validate_seal(
            owner_document, CHALLENGE_SCHEMA,
            "TERMINAL_WITNESS_OWNER_INVALID")
        require(
            document.get("terminal_challenge_file_sha256") ==
                sha256(owner_payload) and
            document.get("terminal_challenge_body_sha256") ==
                owner_document.get("body_sha256"), reason)


def checkpoint_and_release_host_owner(
        *, directory: int, owner_payload: bytes, request: Mapping[str, Any],
        stable: Mapping[str, str], hpt1_sha256: str, hpw1_sha256: str,
        hpe1: bytes, ack: Mapping[str, Any], boundary: BoundaryObservation,
) -> tuple[Artifact, Artifact]:
    reason = "TERMINAL_WITNESS_COMPLETION_INVALID"
    require(ack.get("terminal_replay") is True, reason)
    terminal_receipt = ack.get("finalization_receipt")
    terminal_receipt_sha = ack.get("finalization_receipt_sha256")
    require(isinstance(terminal_receipt, str) and
            isinstance(terminal_receipt_sha, str), reason)
    owner_document = strict_json(owner_payload, "TERMINAL_WITNESS_OWNER_INVALID")
    validate_seal(
        owner_document, CHALLENGE_SCHEMA, "TERMINAL_WITNESS_OWNER_INVALID")
    completion_path, pointer_path = terminal_completion_paths(
        str(request["expected_finalization_id"]))
    completion_document, completion_payload = sealed_document({
        "schema": TERMINAL_COMPLETION_SCHEMA, "version": 1,
        "status": "TERMINAL_ACK_CHECKPOINTED",
        "completed_at_ms": time.time_ns() // 1_000_000,
        "boot_id": boundary.boot_id,
        "recovery_id": request["expected_recovery_id"],
        "finalization_id": request["expected_finalization_id"],
        "campaign_id": request["expected_campaign_id"],
        "cycle_id": request["expected_cycle_id"],
        "expected_owner_set_sha256": stable["expected_owner_set_sha256"],
        "expected_owner_count": int(stable["expected_owner_count"]),
        "preliminary_finalization_receipt_sha256":
            stable["preliminary_finalization_receipt_sha256"],
        "terminal_challenge_file_sha256": sha256(owner_payload),
        "terminal_challenge_body_sha256": owner_document["body_sha256"],
        "terminalizing_latch_sha256": hpt1_sha256,
        "terminal_external_halt_latch_sha256": hpw1_sha256,
        "terminal_evidence_file_sha256": sha256(hpe1),
        "terminal_evidence_body_sha256":
            parse_lines(hpe1, "HPE1", HPE1_KEYS, reason)[0][
                "evidence_body_sha256"],
        "terminal_ack_receipt_sha256": terminal_receipt_sha,
        "terminal_ack_receipt": terminal_receipt,
        "egress_publisher_pid": boundary.publisher_pid,
        "egress_publisher_start_ticks": boundary.publisher_start_ticks,
        "egress_policy_generation": boundary.generation,
        "egress_policy_sha256": boundary.state_sha256,
        "owner_removal_required_after_commit": True,
        "terminal_replay_verified": True,
        **{field: False for field in BOUNDARY_FIELDS},
    })
    require(set(completion_document) == TERMINAL_COMPLETION_FIELDS, reason)
    try:
        completion = read_json_artifact(
            completion_path, TERMINAL_COMPLETION_SCHEMA, reason)
    except VerifierError:
        try:
            os.lstat(completion_path)
        except FileNotFoundError:
            publish_no_replace(
                completion_path, completion_payload, MAX_JSON_BYTES,
                mode=0o600)
            completion = read_json_artifact(
                completion_path, TERMINAL_COMPLETION_SCHEMA, reason)
        else:
            raise
    if completion.payload != completion_payload:
        # A replay retains the original completion timestamp and exact bytes.
        stable_existing = dict(completion.document)
        stable_new = dict(completion_document)
        for value in (stable_existing, stable_new):
            value.pop("completed_at_ms", None)
            value.pop("body_sha256", None)
        require(stable_existing == stable_new, reason)
    pointer_document, pointer_payload = sealed_document({
        "schema": TERMINAL_COMPLETION_POINTER_SCHEMA, "version": 1,
        # This pointer is the durable authorization for the exact unlink that
        # follows.  It deliberately does not claim the unlink is already
        # committed: a crash may leave the old owner present, while a crash
        # immediately after unlink leaves absence backed by both records.
        "status": "TERMINAL_OWNER_RELEASE_AUTHORIZED",
        "committed_at_ms": completion.document["completed_at_ms"],
        "boot_id": completion.document["boot_id"],
        "recovery_id": request["expected_recovery_id"],
        "finalization_id": request["expected_finalization_id"],
        "campaign_id": request["expected_campaign_id"],
        "cycle_id": request["expected_cycle_id"],
        "terminal_completion_path": str(completion_path),
        "terminal_completion_file_sha256": completion.file_sha256,
        "terminal_completion_body_sha256":
            completion.document["body_sha256"],
        "owner_removal_authorized": True,
        **{field: False for field in BOUNDARY_FIELDS},
    })
    require(set(pointer_document) == TERMINAL_COMPLETION_POINTER_FIELDS, reason)
    publish_no_replace(pointer_path, pointer_payload, MAX_JSON_BYTES, mode=0o600)
    pointer = read_json_artifact(
        pointer_path, TERMINAL_COMPLETION_POINTER_SCHEMA, reason)
    _validate_completion_documents(
        completion=completion, pointer=pointer, request=request,
        hpt1_sha256=hpt1_sha256, hpw1_sha256=hpw1_sha256, hpe1=hpe1,
        owner_payload=owner_payload)
    require(secure_read(
        HOST_AUTHORITY_OWNER_PATH, expected_uid=ROOT_UID,
        expected_gid=ROOT_GID, modes=frozenset({0o600}),
        maximum=MAX_JSON_BYTES,
        reason="TERMINAL_WITNESS_OWNER_INVALID") == owner_payload, reason)
    try:
        os.unlink(HOST_AUTHORITY_OWNER_PATH.name, dir_fd=directory)
        os.fsync(directory)
    except OSError as error:
        raise VerifierError("TERMINAL_WITNESS_OWNER_REMOVE_FAILED") from error
    try:
        os.stat(
            HOST_AUTHORITY_OWNER_PATH.name, dir_fd=directory,
            follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError as error:
        raise VerifierError("TERMINAL_WITNESS_OWNER_REMOVE_FAILED") from error
    else:
        raise VerifierError("TERMINAL_WITNESS_OWNER_REMOVE_FAILED")
    return completion, pointer


def replay_released_host_owner(
        request: Mapping[str, Any], *, command: CommandRunner,
        observer: Any | None) -> dict[str, Any] | None:
    """Replay a completed ACK after the exact host owner was removed.

    Bare owner absence is never evidence.  This path exists only when both
    durable completion artifacts are present and every current latch, HPE1,
    DENY_ALL observation, and supervisor ACK replay still matches them.
    """
    reason = "TERMINAL_WITNESS_COMPLETION_INVALID"
    completion_path, pointer_path = terminal_completion_paths(
        str(request["expected_finalization_id"]))
    present: list[bool] = []
    for path in (completion_path, pointer_path):
        try:
            os.lstat(path)
        except FileNotFoundError:
            present.append(False)
        except OSError as error:
            raise VerifierError(reason) from error
        else:
            present.append(True)
    if present == [False, False]:
        return None
    require(present == [True, True], reason)
    directory, lease, owner = acquire_host_lease(require_owner=None)
    try:
        if owner is not None:
            return None
        completion = read_json_artifact(
            completion_path, TERMINAL_COMPLETION_SCHEMA, reason)
        pointer = read_json_artifact(
            pointer_path, TERMINAL_COMPLETION_POINTER_SCHEMA, reason)
        hpt1_payload = secure_read(
            HPT1_PATH, expected_uid=EXEC_UID, expected_gid=EXEC_GID,
            modes=frozenset({0o600}), maximum=16 * 1024,
            reason="TERMINAL_WITNESS_HPT1_INVALID")
        hpt1 = validate_hpt1(hpt1_payload)
        hpt1_sha = sha256(hpt1_payload)
        capsule = secure_read(
            CAPSULE_OUTPUT, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o400}), maximum=MAX_LINE_FILE_BYTES,
            reason="TERMINAL_WITNESS_CAPSULE_INVALID")
        hpw1_payload = secure_read(
            HPW1_PATH, expected_uid=EXEC_UID, expected_gid=EXEC_GID,
            modes=frozenset({0o600}), maximum=8192,
            reason="TERMINAL_WITNESS_HPW1_INVALID")
        hpe1 = secure_read(
            EVIDENCE_OUTPUT, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o400}), maximum=MAX_HPE1_BYTES,
            reason="TERMINAL_WITNESS_HPE1_INVALID")
        stable, hpe_prefix = parse_lines(
            hpe1, "HPE1", HPE1_KEYS, "TERMINAL_WITNESS_HPE1_INVALID")
        require(stable["evidence_body_sha256"] == sha256(hpe_prefix) and
                stable["recovery_id"] == request["expected_recovery_id"] and
                stable["finalization_id"] ==
                    request["expected_finalization_id"] and
                stable["campaign_id"] == request["expected_campaign_id"] and
                stable["cycle_id"] == request["expected_cycle_id"] and
                hpt1["finalization_id"] ==
                    request["expected_finalization_id"], reason)
        hpw1_sha = sha256(hpw1_payload)
        validate_hpw1(
            hpw1_payload, stable=stable, capsule=capsule,
            hpt1_sha256=hpt1_sha)
        _validate_completion_documents(
            completion=completion, pointer=pointer, request=request,
            hpt1_sha256=hpt1_sha, hpw1_sha256=hpw1_sha, hpe1=hpe1)
        current_observer = observer or ProductionBoundaryObserver(
            command=command)
        first = current_observer.observe()
        validate_boundary(
            first, expected_boot=stable["host_boot_id"],
            expected_generation=int(stable["egress_policy_generation"]),
            expected_sha256=stable["egress_policy_sha256"])
        second = current_observer.observe()
        validate_boundary(
            second, expected_boot=first.boot_id,
            expected_generation=first.generation,
            expected_sha256=first.state_sha256)
        require(
            first.stable_identity == second.stable_identity and
            first.publisher_pid == int(stable["egress_publisher_pid"]) ==
                completion.document["egress_publisher_pid"] and
            first.publisher_start_ticks ==
                int(stable["egress_publisher_start_ticks"]) ==
                completion.document["egress_publisher_start_ticks"] and
            first.generation == completion.document[
                "egress_policy_generation"] and
            first.state_sha256 == completion.document[
                "egress_policy_sha256"], reason)
        replay = validate_ack(
            command(_ack_arguments(request, stable, hpe1), 120),
            hpe1=hpe1, hpt1_sha256=hpt1_sha,
            hpw1_sha256=hpw1_sha, stable=stable)
        require(
            replay.get("terminal_replay") is True and
            replay.get("finalization_receipt_sha256") ==
                completion.document["terminal_ack_receipt_sha256"] and
            replay.get("finalization_receipt") ==
                completion.document["terminal_ack_receipt"], reason)
        try:
            os.stat(
                HOST_AUTHORITY_OWNER_PATH.name, dir_fd=directory,
                follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise VerifierError(reason) from error
        else:
            raise VerifierError(reason)
        return replay
    finally:
        release_host_lease(directory, lease)


def run_verifier(
        request: Mapping[str, Any], *, command: CommandRunner = run_command,
        observer: Any | None = None) -> dict[str, Any]:
    attest_bundle(request, command)
    cutoff = read_json_artifact(
        Path(request["transport_cutoff_receipt"]), CUTOFF_SCHEMA,
        "TERMINAL_WITNESS_CUTOFF_INVALID")
    trust = read_json_artifact(
        Path(request["provider_trust_policy"]), TRUST_SCHEMA,
        "TERMINAL_WITNESS_TRUST_INVALID")
    challenge = read_json_artifact(
        Path(request["challenge"]), CHALLENGE_SCHEMA,
        "TERMINAL_WITNESS_CHALLENGE_INVALID")
    evidence_payload = secure_read(
        Path(request["signed_account_evidence"]), expected_uid=ROOT_UID,
        expected_gid=ROOT_GID, modes=frozenset({0o400, 0o600}),
        maximum=MAX_JSON_BYTES, reason="TERMINAL_WITNESS_EVIDENCE_INVALID")
    evidence_document = strict_json(
        evidence_payload, "TERMINAL_WITNESS_EVIDENCE_INVALID")
    evidence = Artifact(
        Path(request["signed_account_evidence"]), evidence_payload,
        evidence_document)
    witness = read_json_artifact(
        Path(request["terminal_witness"]), WITNESS_SCHEMA,
        "TERMINAL_WITNESS_WITNESS_INVALID")
    provider_request_path = Path(request["provider_request"])
    provider_response_path = Path(request["provider_response"])
    provider_request = secure_read(
        provider_request_path, expected_uid=ROOT_UID,
        expected_gid=ROOT_GID, modes=frozenset({0o400, 0o600}),
        maximum=MAX_JSON_BYTES, reason="TERMINAL_WITNESS_PROVIDER_INVALID")
    provider_response = secure_read(
        provider_response_path, expected_uid=ROOT_UID,
        expected_gid=ROOT_GID, modes=frozenset({0o400, 0o600}),
        maximum=MAX_JSON_BYTES, reason="TERMINAL_WITNESS_PROVIDER_INVALID")
    hpt1_payload = secure_read(
        HPT1_PATH, expected_uid=EXEC_UID, expected_gid=EXEC_GID,
        modes=frozenset({0o600}), maximum=16 * 1024,
        reason="TERMINAL_WITNESS_HPT1_INVALID")
    hpt1 = validate_hpt1(hpt1_payload)
    hpt1_sha = sha256(hpt1_payload)

    directory, lease, owner = acquire_host_lease()
    try:
        require(owner == challenge.payload,
                "TERMINAL_WITNESS_OWNER_INVALID")
        # Reopen every authority-bearing input only after the exclusive lease
        # is held. The prior independent attestation cannot be spliced across
        # an activation attempt.
        for artifact in (cutoff, trust, challenge, evidence, witness):
            current = secure_read(
                artifact.path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
                modes=frozenset({0o400, 0o600}), maximum=MAX_JSON_BYTES,
                reason="TERMINAL_WITNESS_INPUT_DRIFT")
            require(current == artifact.payload,
                    "TERMINAL_WITNESS_INPUT_DRIFT")
        require(
            secure_read(
                provider_request_path, expected_uid=ROOT_UID,
                expected_gid=ROOT_GID, modes=frozenset({0o400, 0o600}),
                maximum=MAX_JSON_BYTES,
                reason="TERMINAL_WITNESS_PROVIDER_INVALID") ==
                    provider_request and
            secure_read(
                provider_response_path, expected_uid=ROOT_UID,
                expected_gid=ROOT_GID, modes=frozenset({0o400, 0o600}),
                maximum=MAX_JSON_BYTES,
                reason="TERMINAL_WITNESS_PROVIDER_INVALID") ==
                    provider_response,
            "TERMINAL_WITNESS_INPUT_DRIFT")
        require(secure_read(
            HPT1_PATH, expected_uid=EXEC_UID, expected_gid=EXEC_GID,
            modes=frozenset({0o600}), maximum=16 * 1024,
            reason="TERMINAL_WITNESS_HPT1_INVALID") == hpt1_payload,
            "TERMINAL_WITNESS_HPT1_DRIFT")
        current_observer = observer or ProductionBoundaryObserver(
            command=command)
        first = current_observer.observe()
        validate_boundary(
            first, expected_boot=str(witness.document["boot_id"]),
            expected_generation=int(witness.document[
                "egress_policy_generation"]),
            expected_sha256=str(witness.document["egress_policy_sha256"]))
        second = current_observer.observe()
        validate_boundary(
            second, expected_boot=first.boot_id,
            expected_generation=first.generation,
            expected_sha256=first.state_sha256)
        require(first.stable_identity == second.stable_identity,
                "TERMINAL_WITNESS_CURRENT_BOUNDARY_DRIFT")
        stable = stable_values(
            request=request, cutoff=cutoff, trust=trust, challenge=challenge,
            evidence=evidence, witness=witness, hpt1=hpt1,
            hpt1_sha256=hpt1_sha, boundary=second)
        capsule = build_lines(
            "HPC1", HPC1_KEYS, stable, "capsule_body_sha256")
        require(len(capsule) <= MAX_LINE_FILE_BYTES,
                "TERMINAL_WITNESS_CAPSULE_TOO_LARGE")
        publish_no_replace(CAPSULE_OUTPUT, capsule, MAX_LINE_FILE_BYTES)
        commit = command((
            "/usr/bin/systemctl", "start", COMMITTER_UNIT), 45)
        require(commit.returncode == 0 and commit.stderr == b"",
                "TERMINAL_WITNESS_COMMITTER_FAILED")
        hpw1_payload = secure_read(
            HPW1_PATH, expected_uid=EXEC_UID, expected_gid=EXEC_GID,
            modes=frozenset({0o600}), maximum=8192,
            reason="TERMINAL_WITNESS_HPW1_INVALID")
        validate_hpw1(
            hpw1_payload, stable=stable, capsule=capsule,
            hpt1_sha256=hpt1_sha)
        require(secure_read(
            HPT1_PATH, expected_uid=EXEC_UID, expected_gid=EXEC_GID,
            modes=frozenset({0o600}), maximum=16 * 1024,
            reason="TERMINAL_WITNESS_HPT1_INVALID") == hpt1_payload,
            "TERMINAL_WITNESS_HPT1_DRIFT")
        third = current_observer.observe()
        validate_boundary(
            third, expected_boot=first.boot_id,
            expected_generation=first.generation,
            expected_sha256=first.state_sha256)
        require(third.stable_identity == first.stable_identity,
                "TERMINAL_WITNESS_CURRENT_BOUNDARY_DRIFT")
        hpw1_sha = sha256(hpw1_payload)
        hpe1 = build_hpe1(stable, hpw1_sha)
        require(len(hpe1) <= MAX_HPE1_BYTES,
                "TERMINAL_WITNESS_HPE1_TOO_LARGE")
        evidence_replay = publish_no_replace(
            EVIDENCE_OUTPUT, hpe1, MAX_HPE1_BYTES)
        final_boundary = current_observer.observe()
        validate_boundary(
            final_boundary, expected_boot=first.boot_id,
            expected_generation=first.generation,
            expected_sha256=first.state_sha256)
        require(final_boundary.stable_identity == first.stable_identity,
                "TERMINAL_WITNESS_CURRENT_BOUNDARY_DRIFT")
        # Exact replay artifacts are reopened immediately before the ACK. No
        # volatile observation time is embedded in HPE1.
        require(secure_read(
            HOST_AUTHORITY_OWNER_PATH, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o600}),
            maximum=MAX_JSON_BYTES,
            reason="TERMINAL_WITNESS_OWNER_INVALID") == owner and
            secure_read(
                HPT1_PATH, expected_uid=EXEC_UID, expected_gid=EXEC_GID,
                modes=frozenset({0o600}), maximum=16 * 1024,
                reason="TERMINAL_WITNESS_HPT1_INVALID") == hpt1_payload and
            secure_read(
                HPW1_PATH, expected_uid=EXEC_UID, expected_gid=EXEC_GID,
                modes=frozenset({0o600}), maximum=8192,
                reason="TERMINAL_WITNESS_HPW1_INVALID") == hpw1_payload and
            secure_read(
                EVIDENCE_OUTPUT, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
                modes=frozenset({0o400}), maximum=MAX_HPE1_BYTES,
                reason="TERMINAL_WITNESS_HPE1_INVALID") == hpe1 and
            secure_read(
                provider_request_path, expected_uid=ROOT_UID,
                expected_gid=ROOT_GID, modes=frozenset({0o400, 0o600}),
                maximum=MAX_JSON_BYTES,
                reason="TERMINAL_WITNESS_PROVIDER_INVALID") ==
                    provider_request and
            secure_read(
                provider_response_path, expected_uid=ROOT_UID,
                expected_gid=ROOT_GID, modes=frozenset({0o400, 0o600}),
                maximum=MAX_JSON_BYTES,
                reason="TERMINAL_WITNESS_PROVIDER_INVALID") ==
                    provider_response,
            "TERMINAL_WITNESS_REPLAY_ARTIFACT_DRIFT")
        # HPE1 publication is intentionally non-authorizing.  The outer
        # repair/finalizer owns the HSL8 ACK and its durable completion.  A
        # separate post-completion operation replays that ACK under this same
        # host lock domain before releasing the terminal challenge owner.
        return {
            "paper_finalization_state": "TERMINAL_WITNESS_COMMITTED",
            "terminal_replay": evidence_replay,
        }
    finally:
        release_host_lease(directory, lease)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    operation = value.add_mutually_exclusive_group(required=True)
    operation.add_argument("--run", action="store_true")
    operation.add_argument("--record-cutoff", action="store_true")
    value.add_argument("--request", type=Path, required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parsed = parser().parse_args(argv)
    try:
        if os.geteuid() != ROOT_UID or os.getegid() != ROOT_GID:
            raise VerifierError("TERMINAL_WITNESS_ROOT_REQUIRED")
        if parsed.record_cutoff:
            request = validate_cutoff_request(parsed.request)
            cutoff = record_transport_cutoff(request)
            result = {
                "paper_finalization_state": cutoff["status"],
                "terminal_replay": cutoff["_terminal_replay"],
            }
        else:
            request = validate_request(parsed.request)
            result = run_verifier(request)
    except VerifierError as error:
        print("hepta_p1_paper_terminal_witness_verifier: FAIL " + error.reason,
              file=sys.stderr)
        return 4
    print("STATUS=" + str(result["paper_finalization_state"]))
    print("TERMINAL_REPLAY=" + str(result["terminal_replay"]).lower())
    print("PAPER_AUTHORIZED=false")
    print("LIVE_AUTHORIZED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
