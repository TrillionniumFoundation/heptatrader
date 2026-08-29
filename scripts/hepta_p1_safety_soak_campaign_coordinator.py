#!/usr/bin/env python3
"""Root-only, restartable coordinator for the frozen P1 SHADOW safety soak.

The coordinator is deliberately an orchestration boundary, not an authority
boundary.  It can start only the pinned SHADOW launcher, observer/recorder
workers, fault-pin producer, fault injector, and auditor.  It never accepts a
PAPER/LIVE mode, credential, broker address, order, position, or mutation
request.  Every durable transition is an immutable, canonical journal entry;
an ambiguous launcher transition is failed closed and is never replayed.

Production is available only from the fixed installed image, as real root,
with ``--run`` and a root-owned canonical launch contract.  The core accepts
an execution adapter so the lifecycle can be tested against disposable fake
systemd without contacting a broker or the host service manager.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Protocol, Sequence


VERSION = 1
ROOT_UID = 0
ROOT_GID = 0
INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-campaign-coordinator")
PRODUCTION_MODE = "PRODUCTION_ROOT_COORDINATOR"
CONTRACT_SCHEMA = "hepta.p1-safety-soak-coordinator-launch-contract.v1"
RUNTIME_SCHEMA = "hepta.p1-safety-soak-campaign-runtime.v1"
FREEZE_SCHEMA = "hepta.p1-safety-soak-freeze-bundle-receipt.v1"
SPEC_SCHEMA = "hepta.p1-safety-soak-campaign-spec.v1"
PLAN_SCHEMA = "hepta.p1-safety-soak-fault-plan.v1"
POLICY_SCHEMA = "hepta.strategy-shadow-observation-policy.v1"
JOURNAL_SCHEMA = "hepta.p1-safety-soak-coordinator-journal-entry.v1"
REQUEST_SCHEMA = "hepta.p1-safety-soak-worker-request.v1"
ACK_SCHEMA = "hepta.p1-safety-soak-worker-ack.v1"
TERMINAL_SCHEMA = "hepta.p1-safety-soak-coordinator-terminal-receipt.v1"
AUDIT_SCHEMA = "hepta.p1-safety-soak-audit-receipt.v1"

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}")
FORMAL_ID = re.compile(
    r"hepta-p1-shadow-soak-round([1-9][0-9]*)-([0-9]{8})")
NUMBERED_JSON = re.compile(r"[0-9]{8}\.json")
UNIT_NAME = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,190}\."
    r"(?:service|socket|target|timer)")

MAXIMUM_DOCUMENT_BYTES = 16 * 1024 * 1024
MAXIMUM_COMMAND_BYTES = 1024 * 1024
MINIMUM_ELIGIBLE_DECISIONS = 200
MINIMUM_TRADING_DAYS = 10
MAXIMUM_TRADING_DAYS = 20
MINIMUM_BOOTTIME_DURATION_NS = 72 * 60 * 60 * 1_000_000_000
MINIMUM_COMPLETE_PPM = 990_001
MAXIMUM_CADENCE_MS = 15 * 60 * 1000
MAXIMUM_LAUNCH_LATENESS_MS = 15 * 60 * 1000
SYSTEMD_READY_TIMEOUT_SECONDS = 30
LAUNCHER_WARMUP_MS = 210 * 60 * 1000
LAUNCHER_EARLY_START_LEAD_MS = 20 * 60 * 1000
LAUNCHER_DISPATCH_TOLERANCE_MS = 60 * 1000
POLICY_SLOT_INTERVAL_MS = 2 * 60 * 1000
POLICY_MAXIMUM_ITERATIONS = 241
LAUNCHER_MINIMUM_EXEC_MARGIN_MS = 10 * 1000
POST_FORMAL_PROJECTION_GUARD_MS = 20 * 60 * 1000
POST_FORMAL_TEARDOWN_GUARD_MS = 30 * 60 * 1000
CONTINUITY_PROJECTION_GUARD_MS = 4 * 60 * 1000
COMMAND_WATCHDOG_PULSE_SECONDS = 10.0

SYSTEMCTL = "/usr/bin/systemctl"
SYSTEMD_RUN = "/usr/bin/systemd-run"
RECORDER = "/usr/libexec/hepta-p1-safety-soak-evidence-recorder"
FREEZER = "/usr/libexec/hepta-p1-safety-soak-campaign-freezer"
OBSERVER = "/usr/libexec/hepta-p1-safety-soak-independent-observer"
INJECTOR = "/usr/libexec/hepta-p1-safety-soak-root-fault-injector"
PIN_PRODUCER = "/usr/libexec/hepta-p1-safety-soak-fault-pin-producer"
AUDITOR = "/usr/libexec/hepta-p1-safety-soak-auditor"
LAUNCHER = "/usr/libexec/hepta-p1-shadow-admission-launcher"
HANDOFF = "/usr/libexec/hepta-p1-watch-to-paper-handoff"
OBSERVER_WORKER = "/usr/libexec/hepta-p1-safety-soak-observer-worker"
RECORDER_WORKER = "/usr/libexec/hepta-p1-safety-soak-recorder-worker"
POLICY_PLANNER = "/usr/libexec/hepta-p1-safety-soak-policy-planner"
OBSERVATION_POLICY_BUILDER = "/usr/libexec/build-hepta-p1-observation-policy"
BROKER_EGRESS_POLICY = "/usr/libexec/hepta-broker-egress-policy"
CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA = (
    "hepta.p1-safety-soak-independent-campaign-continuity-observation.v1")
CONTINUITY_CHECKPOINT_SCHEMA = (
    "hepta.p1-safety-soak-continuity-checkpoint.v1")
CAMPAIGN_RUNTIME_SCHEMA = "hepta.p1-safety-soak-campaign-runtime.v1"

BOUNDARY_FIELDS = (
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access",
)
REFERENCE_FIELDS = frozenset({"path", "file_sha256", "body_sha256"})
CAMPAIGN_RUNTIME_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "schema",
})
CONTINUITY_OBSERVATION_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "campaign_id", "observer_id", "observation_complete", "clock_id",
    "boot_id", "observed_boottime_ns", "source_manifest_sha256",
    "policy_sha256", "strategy_sha256", "freeze_bundle",
    "campaign_runtime", "continuity_slot_index",
    "continuity_scheduled_at_ms", "continuity_origin_ms",
    "continuity_end_ms", "continuity_cadence_ms",
    "continuity_final_slot", "continuity_is_final", "catch_up",
    "activation_receipt", "activation_receipt_document", "lease_receipt",
    "lease_receipt_document", "lease_generation",
    "previous_lease_generation", "previous_lease_receipt_body_sha256",
    "gateway_identity", "gateway_process_identity",
    "gateway_executable_identity", "gateway_profile_identity",
    "gateway_domain_config_identity", "supervisor_socket_identity",
    "custodian_identity", "collector_timer_identity",
    "activation_reconcile_timer_identity", "tool_socket_identity",
    "transition_fault_id", "persistent_stack_ok", "lease_chain_ok",
    "connector_count", "authorized_uids", "paper_unit_active_count",
    "campaign_socket_present", "kill_switch_engaged", "zero_exposure",
    "producer", "production_mode", "observation_evidence",
    *BOUNDARY_FIELDS, "body_sha256",
})
CONTINUITY_CHECKPOINT_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "clock_id", "boot_id",
    "observed_boottime_ns", "freeze_bundle", "campaign_runtime",
    "continuity_slot_index", "continuity_scheduled_at_ms",
    "continuity_origin_ms", "continuity_end_ms", "continuity_cadence_ms",
    "continuity_final_slot", "continuity_is_final", "catch_up",
    "activation_receipt", "activation_receipt_document", "lease_receipt",
    "lease_receipt_document", "lease_generation", "previous_lease_generation",
    "previous_lease_receipt_body_sha256", "gateway_identity",
    "gateway_process_identity", "gateway_executable_identity",
    "gateway_profile_identity", "gateway_domain_config_identity",
    "supervisor_socket_identity", "custodian_identity",
    "collector_timer_identity", "activation_reconcile_timer_identity",
    "tool_socket_identity", "transition_fault_id", "persistent_stack_ok",
    "lease_chain_ok", "connector_count", "authorized_uids",
    "paper_unit_active_count", "campaign_socket_present",
    "kill_switch_engaged", "zero_exposure", "source_manifest_sha256",
    "policy_sha256", "strategy_sha256", "previous_checkpoint_body_sha256",
    "observer_receipt", *BOUNDARY_FIELDS, "body_sha256",
})
CONTINUITY_CHECKPOINT_COPY_FIELDS = (
    CONTINUITY_CHECKPOINT_FIELDS - {
        "schema", "version", "campaign_id", "sequence",
        "previous_checkpoint_body_sha256", "observer_receipt", "body_sha256",
    }
)
FAULT_RESULT_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "fault_id",
    "fault_type", "target_id", "injection_boottime_ns",
    "recovered_boottime_ns", "recovery_verified", "cleanup_verified",
    "evidence_sha256", "observer_receipt", "previous_result_body_sha256",
    "authority_failure", "audit_failure", "cleanup_failure",
    *BOUNDARY_FIELDS, "body_sha256",
})
EXECUTABLE_FIELDS = frozenset({"path", "file_sha256"})
SOURCE_PIN_FIELDS = frozenset({
    "role", "source_path", "installed_path", "file_sha256",
})
FORMAL_RUNTIME_FIELDS = frozenset({
    "formal_campaign_id", "probe_campaign_id", "launcher_start_ms",
    "launcher_dispatch_at_ms", "valid_after_ms", "slot_interval_ms",
    "maximum_iterations",
    "expires_at_ms", "launcher_completion_deadline_ms",
    "projection_deadline_ms", "teardown_deadline_ms", "policy",
    "launcher_receipt_path",
    "verified_closure_path", "artifact_root",
})
CONTRACT_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "freeze_bundle",
    "state_root", "pin_formal_campaign_id", "observer_cadence_ms",
    "maximum_slot_lateness_ms", "poll_interval_ms", "issued_at_ms",
    "expires_at_ms", *BOUNDARY_FIELDS, "body_sha256",
})
RUNTIME_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "round", "boot_id",
    "issued_at_ms", "expires_at_ms", "freeze_bundle", "campaign_spec",
    "fault_plan", "pin_formal_campaign_id", "formal_campaigns",
    "observer_cadence_ms", "maximum_slot_lateness_ms", "state_root",
    "raw_observation_directory", "recorder_root",
    "injector_journal_directory", "injector_output_directory",
    "control_directory", "executables", *BOUNDARY_FIELDS, "body_sha256",
})
JOURNAL_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "event", "status",
    "recorded_at_ms", "recorded_boottime_ns", "details",
    "previous_body_sha256", *BOUNDARY_FIELDS, "body_sha256",
})
REQUEST_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "request_id",
    "target", "action", "created_at_ms", "deadline_ms", "arguments",
    "previous_body_sha256", *BOUNDARY_FIELDS, "body_sha256",
})
ACK_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "request_id",
    "worker", "action", "status", "completed_at_ms", "outputs",
    "previous_body_sha256", *BOUNDARY_FIELDS, "body_sha256",
})

ROLE_PATHS = {
    "campaign_freezer": FREEZER,
    "campaign_coordinator": str(INSTALLED_EXECUTABLE),
    "observer_worker": OBSERVER_WORKER,
    "recorder_worker": RECORDER_WORKER,
    "evidence_recorder": RECORDER,
    "independent_observer": OBSERVER,
    "root_fault_injector": INJECTOR,
    "fault_pin_producer": PIN_PRODUCER,
    "auditor": AUDITOR,
    "shadow_admission_launcher": LAUNCHER,
    "watch_to_paper_handoff": HANDOFF,
    "policy_planner": POLICY_PLANNER,
    "observation_policy_builder": OBSERVATION_POLICY_BUILDER,
    "broker_egress_policy": BROKER_EGRESS_POLICY,
}

FORBIDDEN_EXECUTION_UNITS = (
    "hepta-execution-ib-paper.service",
    "hepta-execution-ib-paper.socket",
    "hepta-execution-events-ib-paper.socket",
    "hepta-execution-ib-paper@alpha.service",
    "hepta-execution-ib-paper@alpha.socket",
    "hepta-execution-events-ib-paper@alpha.socket",
    "hepta-ib-paper-domain-preflight@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.socket",
    "hepta-execution-ib-live.service",
    "hepta-execution-ib-live.socket",
    "hepta-execution-events-ib-live.socket",
    "hepta-execution-ib-live@alpha.service",
    "hepta-execution-ib-live@alpha.socket",
    "hepta-execution-events-ib-live@alpha.socket",
    "hepta-ib-live-domain-preflight@alpha.service",
    "hepta-ib-live-campaign-operator@alpha.service",
    "hepta-ib-live-campaign-operator@alpha.socket",
)

AUDIT_RECEIPT_FIELDS = frozenset({
    "schema", "version", "phase", "verdict", "campaign_id", "domain_id",
    "independent_auditor_id", "audited_at_ms",
    "campaign_spec_file_sha256", "campaign_spec_body_sha256",
    "campaign_runtime",
    "freeze_bundle", "producer", "production_mode",
    "source_manifest_sha256", "policy_sha256", "strategy_sha256",
    "evaluated_interval", "counts", "completeness", "checked_artifacts",
    "failed_invariants", "exposure_summary", "cleanup_status",
    "p1_safety_soak_gate_satisfied", "paper_test_admission_candidate",
    "safest_allowed_next_action", *BOUNDARY_FIELDS, "body_sha256",
})
AUDIT_INTERVAL_FIELDS = frozenset({
    "clock_id", "boot_id", "start_boottime_ns", "end_boottime_ns",
    "duration_ns", "maximum_checkpoint_gap_ns", "consecutive",
    "continuity_origin_ms", "continuity_end_ms", "continuity_final_slot",
})
AUDIT_COUNTS_FIELDS = frozenset({
    "launcher_receipts", "verified_closures", "continuity_checkpoints",
    "declared_trading_days", "observed_trading_days", "scheduled_decisions",
    "decision_receipts", "eligible_decisions", "complete_eligible_decisions",
    "incomplete_eligible_decisions", "catch_up_decisions", "planned_faults",
    "fault_results", "authority_snapshots", "cleanup_snapshots",
})
AUDIT_COMPLETENESS_FIELDS = frozenset({
    "numerator", "denominator", "ppm", "strictly_greater_than_99_percent",
})
AUDIT_EXPOSURE_FIELDS = frozenset({
    "evidence_present", "maximum_connector_count",
    "maximum_authorized_uid_count", "maximum_paper_unit_active_count",
    "campaign_socket_ever_present", "kill_switch_continuously_engaged",
    "local_boundary_uncertain", "scope",
    "authoritative_account_state_observed",
})
AUDIT_CLEANUP_FIELDS = frozenset({
    "required_subject_count", "verified_subject_count", "complete",
})

WORKER_PROPERTIES = (
    "Type=notify", "NotifyAccess=main", "User=root", "Group=root",
    "Restart=on-failure", "RestartSec=1s", "WatchdogSec=30s",
    "TimeoutStartSec=45s", "TimeoutStopSec=30s", "KillMode=mixed",
    "UMask=0077", "NoNewPrivileges=yes", "PrivateTmp=yes",
    "ProtectSystem=strict", "ProtectHome=yes", "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes", "ProtectControlGroups=yes",
    "ProtectKernelLogs=yes", "ProtectClock=yes",
    "RestrictSUIDSGID=yes", "LockPersonality=yes",
    "RestrictRealtime=yes",
    "PrivateDevices=yes", "ProtectHostname=yes", "RestrictNamespaces=yes",
    "KeyringMode=private", "RemoveIPC=yes", "MemoryDenyWriteExecute=yes",
    "AmbientCapabilities=",
    "IPAddressDeny=any", "SystemCallArchitectures=native",
)

TRANSIENT_HARDENING_PROPERTIES = (
    "NoNewPrivileges=yes", "PrivateTmp=yes", "PrivateDevices=yes",
    "ProtectSystem=strict", "ProtectHome=yes", "ProtectHostname=yes",
    "ProtectKernelTunables=yes", "ProtectKernelModules=yes",
    "ProtectKernelLogs=yes", "ProtectControlGroups=yes", "ProtectClock=yes",
    "RestrictNamespaces=yes", "RestrictSUIDSGID=yes",
    "RestrictRealtime=yes", "LockPersonality=yes",
    "MemoryDenyWriteExecute=yes", "KeyringMode=private", "RemoveIPC=yes",
    "IPAddressDeny=any", "SystemCallArchitectures=native",
    "AmbientCapabilities=",
)


class CoordinatorError(RuntimeError):
    """Stable fail-closed campaign error."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class CoordinatorSignal(CoordinatorError):
    """A latched terminal signal routed through durable fail-closed cleanup."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"P1_COORDINATOR_SIGNAL_{signum}")
        self.signum = signum


def launcher_start_for_policy(
    valid_after_ms: int, slot_interval_ms: int,
) -> int:
    """Invert the launcher's frozen warm-up/alignment calculation exactly."""

    reason = "P1_COORDINATOR_LAUNCH_ANCHOR_INVALID"
    _require(type(valid_after_ms) is int and valid_after_ms > 0 and
             type(slot_interval_ms) is int and slot_interval_ms > 0, reason)
    start_ms = valid_after_ms - LAUNCHER_WARMUP_MS
    aligned = (
        (start_ms + LAUNCHER_WARMUP_MS + slot_interval_ms - 1) //
        slot_interval_ms
    ) * slot_interval_ms
    _require(start_ms > 0 and aligned == valid_after_ms, reason)
    return start_ms


def required_valid_after_after_teardown(
    previous_teardown_ms: int, slot_interval_ms: int,
) -> int:
    reason = "P1_COORDINATOR_FORMAL_ADJACENCY_INVALID"
    _require(type(previous_teardown_ms) is int and previous_teardown_ms >= 0 and
             type(slot_interval_ms) is int and slot_interval_ms > 0, reason)
    return (
        (previous_teardown_ms + LAUNCHER_WARMUP_MS +
         LAUNCHER_EARLY_START_LEAD_MS) // slot_interval_ms + 1
    ) * slot_interval_ms


class InjectedCrash(RuntimeError):
    """Test-only crash at a durable transition seam."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise CoordinatorError(reason)


def _stable_failure_reason(error: BaseException, *, setup: bool = False) -> str:
    if isinstance(error, CoordinatorError):
        return error.reason
    prefix = "P1_COORDINATOR_UNEXPECTED_SETUP_" if setup else \
        "P1_COORDINATOR_UNEXPECTED_"
    name = re.sub(r"[^A-Z0-9_]", "_", type(error).__name__.upper())
    return prefix + name


def fail_closed_after_unexpected(
    coordinator: "CampaignCoordinator", error: BaseException,
) -> str:
    """Production orchestration catch boundary, exposed for fault-seam tests."""

    reason = _stable_failure_reason(error)
    coordinator.fail_closed(reason)
    return reason


def boundary() -> dict[str, bool]:
    return {field: False for field in BOUNDARY_FIELDS}


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise CoordinatorError("P1_COORDINATOR_CANONICALIZATION_FAILED") \
            from error


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def seal(body: Mapping[str, Any]) -> dict[str, Any]:
    document = dict(body)
    document["body_sha256"] = digest_bytes(canonical_bytes(document))
    return document


def _exact(value: Any, fields: frozenset[str], reason: str) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields, reason)
    return value


def _validate_seal(document: Mapping[str, Any], reason: str) -> None:
    claimed = document.get("body_sha256")
    body = dict(document)
    body.pop("body_sha256", None)
    _require(
        isinstance(claimed, str) and DIGEST.fullmatch(claimed) is not None and
        claimed == digest_bytes(canonical_bytes(body)), reason)


def _reject_authority(document: Mapping[str, Any], reason: str) -> None:
    _require(all(document.get(field) is False for field in BOUNDARY_FIELDS),
             reason)
    forbidden = {
        "paper", "live", "broker", "credential", "secret", "token",
        "order", "position", "mutation",
    }
    # Paths and fixed role labels may contain descriptive words, but no input
    # value may introduce an authority switch.  Explicit boundary booleans are
    # the only accepted authority-shaped fields.
    for key, value in document.items():
        lowered = str(key).lower()
        if key not in BOUNDARY_FIELDS and any(
                word in lowered for word in forbidden):
            _require(
                not isinstance(value, bool) or value is False,
                reason)


def _identifier(value: Any, reason: str) -> str:
    _require(isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None,
             reason)
    return value


def _integer(value: Any, reason: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, reason)
    return value


def _digest(value: Any, reason: str) -> str:
    _require(isinstance(value, str) and DIGEST.fullmatch(value) is not None,
             reason)
    return value


def _absolute(value: Any, reason: str) -> Path:
    _require(type(value) is str and bool(value), reason)
    path = Path(value)
    _require(path.is_absolute() and ".." not in path.parts, reason)
    return path


@dataclass(frozen=True)
class Snapshot:
    path: Path
    payload: bytes
    document: dict[str, Any]
    file_sha256: str
    body_sha256: str
    metadata: os.stat_result


def _secure_read(
    path: Path, *, expected_uid: int, expected_gid: int,
    modes: frozenset[int], sealed: bool = True,
) -> Snapshot:
    reason = "P1_COORDINATOR_INPUT_UNTRUSTED"
    _require(path.is_absolute(), reason)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CoordinatorError(reason) from error
    try:
        before = os.fstat(descriptor)
        _require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
            before.st_uid == expected_uid and before.st_gid == expected_gid and
            stat.S_IMODE(before.st_mode) in modes and
            0 < before.st_size <= MAXIMUM_DOCUMENT_BYTES, reason)
        chunks: list[bytes] = []
        remaining = MAXIMUM_DOCUMENT_BYTES + 1
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        _require(
            len(payload) <= MAXIMUM_DOCUMENT_BYTES and
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
             before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
             after.st_ctime_ns), reason)
    finally:
        os.close(descriptor)
    try:
        document = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CoordinatorError(reason) from error
    _require(isinstance(document, dict) and canonical_bytes(document) == payload,
             reason)
    if sealed:
        _validate_seal(document, reason)
        body_sha = str(document["body_sha256"])
    else:
        body_sha = digest_bytes(payload)
    return Snapshot(
        path=path, payload=payload, document=document,
        file_sha256=digest_bytes(payload), body_sha256=body_sha,
        metadata=before)


def _secure_executable(
    path: Path, expected_sha256: str, *, expected_uid: int = ROOT_UID,
    expected_gid: int = ROOT_GID,
) -> bytes:
    reason = "P1_COORDINATOR_PINNED_EXECUTABLE_INVALID"
    _digest(expected_sha256, reason)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CoordinatorError(reason) from error
    try:
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == expected_uid and metadata.st_gid == expected_gid and
            stat.S_IMODE(metadata.st_mode) in {0o500, 0o550, 0o555, 0o700,
                                               0o750, 0o755} and
            0 < metadata.st_size <= MAXIMUM_DOCUMENT_BYTES, reason)
        payload = b""
        while len(payload) <= MAXIMUM_DOCUMENT_BYTES:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            payload += block
        after = os.fstat(descriptor)
        _require(
            len(payload) <= MAXIMUM_DOCUMENT_BYTES and
            digest_bytes(payload) == expected_sha256 and
            (metadata.st_dev, metadata.st_ino, metadata.st_size,
             metadata.st_mtime_ns, metadata.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size,
             after.st_mtime_ns, after.st_ctime_ns), reason)
        return payload
    finally:
        os.close(descriptor)


def _secure_static_file(
    path: Path, expected_sha256: str, *, expected_uid: int, expected_gid: int,
) -> bytes:
    reason = "P1_COORDINATOR_STATIC_FRAGMENT_INVALID"
    _digest(expected_sha256, reason)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CoordinatorError(reason) from error
    try:
        before = os.fstat(descriptor)
        _require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
            before.st_uid == expected_uid and before.st_gid == expected_gid and
            stat.S_IMODE(before.st_mode) in {0o400, 0o440, 0o444, 0o600,
                                               0o640, 0o644} and
            0 < before.st_size <= MAXIMUM_DOCUMENT_BYTES, reason)
        payload = os.read(descriptor, MAXIMUM_DOCUMENT_BYTES + 1)
        after = os.fstat(descriptor)
        _require(
            len(payload) <= MAXIMUM_DOCUMENT_BYTES and
            digest_bytes(payload) == expected_sha256 and
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
             before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
             after.st_ctime_ns), reason)
        return payload
    finally:
        os.close(descriptor)


def _unit_sections(payload: bytes) -> dict[str, dict[str, list[str]]]:
    reason = "P1_COORDINATOR_STATIC_FRAGMENT_SYNTAX_INVALID"
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise CoordinatorError(reason) from error
    sections: dict[str, dict[str, list[str]]] = {}
    section: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            _require(section and section not in sections, reason)
            sections[section] = {}
            continue
        _require(section is not None and "=" in raw and not raw.endswith("\\"),
                 reason)
        key, value = raw.split("=", 1)
        key = key.strip()
        _require(bool(key) and key.isascii(), reason)
        sections[section].setdefault(key, []).append(value.strip())
    return sections


def _trusted_directory(
    path: Path, *, expected_uid: int, expected_gid: int,
    create: bool = False,
) -> None:
    reason = "P1_COORDINATOR_DIRECTORY_UNTRUSTED"
    if create:
        try:
            path.mkdir(mode=0o700, parents=False, exist_ok=True)
        except OSError as error:
            raise CoordinatorError(reason) from error
    try:
        metadata = path.lstat()
    except OSError as error:
        raise CoordinatorError(reason) from error
    _require(
        stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode) and
        metadata.st_uid == expected_uid and metadata.st_gid == expected_gid and
        stat.S_IMODE(metadata.st_mode) == 0o700, reason)


_LIBC = ctypes.CDLL(None, use_errno=True)
RENAME_NOREPLACE = 1


def _rename_noreplace(source: Path, destination: Path) -> None:
    function = getattr(_LIBC, "renameat2", None)
    _require(function is not None, "P1_COORDINATOR_RENAMEAT2_UNAVAILABLE")
    function.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint)
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    if function(-100, os.fsencode(source), -100, os.fsencode(destination),
                RENAME_NOREPLACE) != 0:
        number = ctypes.get_errno()
        if number == errno.EEXIST:
            raise CoordinatorError("P1_COORDINATOR_OUTPUT_EXISTS")
        raise CoordinatorError("P1_COORDINATOR_RENAME_FAILED")


def publish_noreplace(
    path: Path, document: Mapping[str, Any], *, expected_uid: int,
    expected_gid: int,
) -> Snapshot:
    payload = canonical_bytes(document)
    _require(path.is_absolute() and len(payload) <= MAXIMUM_DOCUMENT_BYTES,
             "P1_COORDINATOR_OUTPUT_INVALID")
    _trusted_directory(
        path.parent, expected_uid=expected_uid, expected_gid=expected_gid)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_NOFOLLOW", 0) | os.O_CLOEXEC, 0o600)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, expected_uid, expected_gid)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        _rename_noreplace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return _secure_read(
        path, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({0o600}), sealed="body_sha256" in document)


def publish_json_array(
    path: Path, values: Sequence[str], *, expected_uid: int,
    expected_gid: int,
) -> str:
    _require(
        isinstance(values, Sequence) and bool(values) and
        all(type(item) is str and bool(item) for item in values),
        "P1_COORDINATOR_ARGV_INVALID")
    payload = canonical_bytes(list(values))
    _trusted_directory(
        path.parent, expected_uid=expected_uid, expected_gid=expected_gid)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
        getattr(os, "O_NOFOLLOW", 0) | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, expected_uid, expected_gid)
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        _rename_noreplace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return digest_bytes(payload)


def _argv_file_sha256(
    path: Path, *, expected_uid: int, expected_gid: int,
) -> str:
    """Securely validate and hash one canonical argv array."""

    reason = "P1_COORDINATOR_ARGV_FILE_INVALID"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CoordinatorError(reason) from error
    try:
        before = os.fstat(descriptor)
        _require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
            before.st_uid == expected_uid and before.st_gid == expected_gid and
            stat.S_IMODE(before.st_mode) == 0o600 and
            0 < before.st_size <= MAXIMUM_COMMAND_BYTES, reason)
        payload = os.read(descriptor, MAXIMUM_COMMAND_BYTES + 1)
        after = os.fstat(descriptor)
        _require(
            len(payload) <= MAXIMUM_COMMAND_BYTES and
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
             before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
             after.st_ctime_ns), reason)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CoordinatorError(reason) from error
    _require(
        isinstance(value, list) and bool(value) and
        all(type(item) is str and bool(item) and "\x00" not in item
            for item in value) and canonical_bytes(value) == payload, reason)
    return digest_bytes(payload)


class Journal:
    """Gap-free immutable coordinator predecessor chain."""

    def __init__(
        self, directory: Path, campaign_id: str, *, expected_uid: int,
        expected_gid: int, wall_clock: callable = lambda: time.time_ns() // 1_000_000,
        boot_clock: callable = time.monotonic_ns,
    ) -> None:
        self.directory = directory
        self.campaign_id = campaign_id
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid
        self.wall_clock = wall_clock
        self.boot_clock = boot_clock
        _trusted_directory(
            directory, expected_uid=expected_uid, expected_gid=expected_gid)
        self.entries = self._load()

    def _load(self) -> list[Snapshot]:
        names = sorted(item.name for item in self.directory.iterdir()
                       if not item.name.startswith("."))
        _require(
            all(NUMBERED_JSON.fullmatch(name) is not None for name in names) and
            names == [f"{index:08d}.json" for index in range(len(names))],
            "P1_COORDINATOR_JOURNAL_GAP")
        entries: list[Snapshot] = []
        previous: str | None = None
        terminal_seen = False
        for index, name in enumerate(names):
            snapshot = _secure_read(
                self.directory / name, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid, modes=frozenset({0o600}))
            value = self._validate_entry(
                snapshot, index=index, previous=previous,
                terminal_seen=terminal_seen)
            terminal_seen = (
                value["status"] == "FAILED_CLOSED" or
                (value["event"] == "TERMINAL" and
                 value["status"] == "COMMITTED"))
            previous = snapshot.body_sha256
            entries.append(snapshot)
        return entries

    def _validate_entry(
        self, snapshot: Snapshot, *, index: int, previous: str | None,
        terminal_seen: bool,
    ) -> dict[str, Any]:
        value = _exact(
            snapshot.document, JOURNAL_FIELDS,
            "P1_COORDINATOR_JOURNAL_INVALID")
        _validate_seal(value, "P1_COORDINATOR_JOURNAL_INVALID")
        _reject_authority(value, "P1_COORDINATOR_JOURNAL_AUTHORITY")
        _require(
            value.get("schema") == JOURNAL_SCHEMA and
            value.get("version") == VERSION and
            value.get("campaign_id") == self.campaign_id and
            value.get("sequence") == index and
            value.get("previous_body_sha256") == previous and
            isinstance(value.get("event"), str) and value["event"] and
            value.get("status") in {
                "INTENT", "COMMITTED", "MISSED", "FAILED_CLOSED",
            } and isinstance(value.get("details"), dict) and
            not terminal_seen,
            "P1_COORDINATOR_JOURNAL_CHAIN_INVALID")
        _integer(value.get("recorded_at_ms"),
                 "P1_COORDINATOR_JOURNAL_INVALID")
        _integer(value.get("recorded_boottime_ns"),
                 "P1_COORDINATOR_JOURNAL_INVALID")
        return value

    def refresh(self) -> None:
        """Validate only immutable entries appended since the last refresh."""

        if self.failed or self.complete:
            return
        previous = None if not self.entries else self.entries[-1].body_sha256
        while True:
            index = len(self.entries)
            path = self.directory / f"{index:08d}.json"
            try:
                path.lstat()
            except FileNotFoundError:
                # Detect the immediately observable gap without rescanning the
                # complete multi-day chain on every coordinator poll.
                _require(
                    not (self.directory / f"{index + 1:08d}.json").exists(),
                    "P1_COORDINATOR_JOURNAL_GAP")
                return
            snapshot = _secure_read(
                path, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid, modes=frozenset({0o600}))
            value = self._validate_entry(
                snapshot, index=index, previous=previous,
                terminal_seen=False)
            self.entries.append(snapshot)
            previous = snapshot.body_sha256
            if (value["status"] == "FAILED_CLOSED" or
                    (value["event"] == "TERMINAL" and
                     value["status"] == "COMMITTED")):
                return

    def append(
        self, event: str, status_value: str, details: Mapping[str, Any],
    ) -> dict[str, Any]:
        _require(
            type(event) is str and bool(event) and status_value in {
                "INTENT", "COMMITTED", "MISSED", "FAILED_CLOSED",
            } and isinstance(details, Mapping),
            "P1_COORDINATOR_JOURNAL_APPEND_INVALID")
        _require(
            not self.entries or
            (self.entries[-1].document["status"] != "FAILED_CLOSED" and
             not (self.entries[-1].document["event"] == "TERMINAL" and
                  self.entries[-1].document["status"] == "COMMITTED")),
            "P1_COORDINATOR_ALREADY_FAILED_CLOSED")
        document = seal({
            "schema": JOURNAL_SCHEMA, "version": VERSION,
            "campaign_id": self.campaign_id,
            "sequence": len(self.entries), "event": event,
            "status": status_value,
            "recorded_at_ms": int(self.wall_clock()),
            "recorded_boottime_ns": int(self.boot_clock()),
            "details": dict(details),
            "previous_body_sha256": (
                None if not self.entries else self.entries[-1].body_sha256),
            **boundary(),
        })
        snapshot = publish_noreplace(
            self.directory / f"{len(self.entries):08d}.json", document,
            expected_uid=self.expected_uid, expected_gid=self.expected_gid)
        self.entries.append(snapshot)
        return document

    def matching(self, event: str, **details: Any) -> list[dict[str, Any]]:
        return [
            item.document for item in self.entries
            if item.document["event"] == event and all(
                item.document["details"].get(key) == value
                for key, value in details.items())
        ]

    @property
    def failed(self) -> bool:
        return bool(self.entries and
                    self.entries[-1].document["status"] == "FAILED_CLOSED")

    @property
    def complete(self) -> bool:
        return bool(
            self.entries and self.entries[-1].document["event"] == "TERMINAL" and
            self.entries[-1].document["status"] == "COMMITTED")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


class ExecutionAdapter(Protocol):
    def run(self, argv: Sequence[str], timeout_seconds: int) -> CommandResult: ...
    def show_unit(self, unit: str) -> Mapping[str, str]: ...
    def start_transient(
        self, unit: str, argv: Sequence[str], properties: Sequence[str],
        environment: Mapping[str, str],
    ) -> None: ...
    def start_unit(self, unit: str) -> None: ...
    def stop_unit(self, unit: str) -> None: ...
    def unit_enabled_state(self, unit: str) -> str: ...
    def unit_contract_state(self, unit: str) -> Mapping[str, str]: ...
    def sleep(self, seconds: float) -> None: ...


class ProductionAdapter:
    """Fixed argv-only subprocess and systemd boundary."""

    UNIT_PROPERTIES = (
        "LoadState", "ActiveState", "SubState", "MainPID", "InvocationID",
        "ExecMainStartTimestampMonotonic", "NRestarts", "FragmentPath",
        "Result", "ExecMainStatus", "UnitFileState", "Job",
        "NoNewPrivileges", "PrivateTmp", "PrivateDevices", "ProtectSystem",
        "ProtectHome", "ProtectHostname", "ProtectKernelTunables",
        "ProtectKernelModules", "ProtectKernelLogs", "ProtectControlGroups",
        "ProtectClock", "RestrictNamespaces", "RestrictSUIDSGID",
        "RestrictRealtime", "LockPersonality", "MemoryDenyWriteExecute",
        "KeyringMode", "RemoveIPC", "RestrictAddressFamilies",
        "IPAddressDeny", "SystemCallArchitectures",
        "CapabilityBoundingSet", "AmbientCapabilities", "PartOf", "BindsTo",
        "After", "ReadWritePaths",
    )

    @staticmethod
    def _command(argv: Sequence[str], timeout_seconds: int) -> CommandResult:
        _require(
            isinstance(argv, Sequence) and bool(argv) and
            all(type(item) is str and bool(item) for item in argv) and
            argv[0].startswith("/") and type(timeout_seconds) is int and
            timeout_seconds > 0, "P1_COORDINATOR_COMMAND_INVALID")
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, close_fds=True, env={
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "LANG": "C", "LC_ALL": "C",
                })
            deadline = time.monotonic() + timeout_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(list(argv), timeout_seconds)
                try:
                    stdout, stderr = process.communicate(
                        timeout=min(COMMAND_WATCHDOG_PULSE_SECONDS, remaining))
                    break
                except subprocess.TimeoutExpired:
                    _sd_notify(
                        "WATCHDOG=1\nSTATUS=waiting for fixed child command")
            result = CommandResult(process.returncode, stdout, stderr)
        except (OSError, subprocess.SubprocessError) as error:
            if process is not None and process.poll() is None:
                process.kill()
                try:
                    process.communicate(timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass
            raise CoordinatorError("P1_COORDINATOR_COMMAND_FAILED") from error
        _require(
            len(result.stdout) <= MAXIMUM_COMMAND_BYTES and
            len(result.stderr) <= MAXIMUM_COMMAND_BYTES,
            "P1_COORDINATOR_COMMAND_OUTPUT_TOO_LARGE")
        return result

    def run(self, argv: Sequence[str], timeout_seconds: int) -> CommandResult:
        return self._command(argv, timeout_seconds)

    def show_unit(self, unit: str) -> Mapping[str, str]:
        _require(UNIT_NAME.fullmatch(unit) is not None,
                 "P1_COORDINATOR_UNIT_INVALID")
        result = self._command((
            SYSTEMCTL, "show", "--no-pager",
            *(f"--property={item}" for item in self.UNIT_PROPERTIES), unit,
        ), 10)
        _require(result.returncode == 0,
                 "P1_COORDINATOR_SYSTEMD_SHOW_FAILED")
        fields: dict[str, str] = {}
        for line in result.stdout.decode("utf-8", errors="strict").splitlines():
            key, separator, value = line.partition("=")
            _require(
                separator == "=" and key in self.UNIT_PROPERTIES and
                key not in fields, "P1_COORDINATOR_SYSTEMD_SHOW_INVALID")
            fields[key] = value
        _require(set(fields) == set(self.UNIT_PROPERTIES),
                 "P1_COORDINATOR_SYSTEMD_SHOW_INVALID")
        return fields

    def start_transient(
        self, unit: str, argv: Sequence[str], properties: Sequence[str],
        environment: Mapping[str, str],
    ) -> None:
        _require(UNIT_NAME.fullmatch(unit) is not None,
                 "P1_COORDINATOR_UNIT_INVALID")
        command = [SYSTEMD_RUN, "--quiet", f"--unit={unit}"]
        command.extend(f"--property={item}" for item in properties)
        for key, value in sorted(environment.items()):
            _require(
                re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key) is not None and
                re.fullmatch(r"[A-Za-z0-9._:/@+-]{1,256}", value) is not None,
                "P1_COORDINATOR_ENVIRONMENT_INVALID")
            command.append(f"--setenv={key}={value}")
        command.append("--")
        command.extend(argv)
        result = self._command(command, 20)
        _require(result.returncode == 0,
                 "P1_COORDINATOR_TRANSIENT_START_FAILED")

    def start_unit(self, unit: str) -> None:
        result = self._command((SYSTEMCTL, "start", unit), 30)
        _require(result.returncode == 0, "P1_COORDINATOR_UNIT_START_FAILED")

    def stop_unit(self, unit: str) -> None:
        result = self._command((SYSTEMCTL, "stop", unit), 45)
        _require(result.returncode == 0, "P1_COORDINATOR_UNIT_STOP_FAILED")

    def unit_enabled_state(self, unit: str) -> str:
        _require(UNIT_NAME.fullmatch(unit) is not None,
                 "P1_COORDINATOR_UNIT_INVALID")
        result = self._command((SYSTEMCTL, "is-enabled", unit), 10)
        _require(result.returncode in {0, 1, 3, 4} and
                 len(result.stdout.splitlines()) == 1,
                 "P1_COORDINATOR_IS_ENABLED_FAILED")
        try:
            value = result.stdout.decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise CoordinatorError(
                "P1_COORDINATOR_IS_ENABLED_INVALID") from error
        _require(value in {
            "enabled", "enabled-runtime", "linked", "linked-runtime",
            "alias", "static", "indirect", "disabled", "generated",
            "transient", "masked", "masked-runtime", "not-found",
        }, "P1_COORDINATOR_IS_ENABLED_INVALID")
        return value

    def unit_contract_state(self, unit: str) -> Mapping[str, str]:
        names = ("LoadState", "UnitFileState", "FragmentPath", "DropInPaths")
        result = self._command((
            SYSTEMCTL, "show", "--no-pager",
            *(f"--property={item}" for item in names), unit,
        ), 10)
        _require(result.returncode == 0,
                 "P1_COORDINATOR_STATIC_UNIT_SHOW_FAILED")
        fields: dict[str, str] = {}
        for line in result.stdout.decode("utf-8", errors="strict").splitlines():
            key, separator, value = line.partition("=")
            _require(separator == "=" and key in names and key not in fields,
                     "P1_COORDINATOR_STATIC_UNIT_SHOW_INVALID")
            fields[key] = value
        _require(set(fields) == set(names),
                 "P1_COORDINATOR_STATIC_UNIT_SHOW_INVALID")
        return fields

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)


def _reference(snapshot: Snapshot) -> dict[str, str]:
    return {
        "path": str(snapshot.path), "file_sha256": snapshot.file_sha256,
        "body_sha256": snapshot.body_sha256,
    }


def _validate_reference(value: Any, reason: str) -> dict[str, str]:
    reference = _exact(value, REFERENCE_FIELDS, reason)
    _absolute(reference.get("path"), reason)
    _digest(reference.get("file_sha256"), reason)
    _digest(reference.get("body_sha256"), reason)
    return reference


def _open_reference(
    value: Mapping[str, Any], *, expected_uid: int, expected_gid: int,
    reason: str,
) -> Snapshot:
    reference = _validate_reference(value, reason)
    snapshot = _secure_read(
        Path(reference["path"]), expected_uid=expected_uid,
        expected_gid=expected_gid, modes=frozenset({0o600}))
    _require(
        snapshot.file_sha256 == reference["file_sha256"] and
        snapshot.body_sha256 == reference["body_sha256"], reason)
    return snapshot


def validate_launch_contract(
    document: dict[str, Any], now_ms: int, *, require_current: bool = True,
) \
        -> dict[str, Any]:
    reason = "P1_COORDINATOR_LAUNCH_CONTRACT_INVALID"
    _exact(document, CONTRACT_FIELDS, reason)
    _validate_seal(document, reason)
    _reject_authority(document, reason)
    campaign = _identifier(document.get("campaign_id"), reason)
    _require(
        document.get("schema") == CONTRACT_SCHEMA and
        document.get("version") == VERSION and
        document.get("status") == "FROZEN", reason)
    _validate_reference(document.get("freeze_bundle"), reason)
    state_root = _absolute(document.get("state_root"), reason)
    _require(
        state_root == Path("/var/lib/hepta/p1-safety-soak") / campaign,
        reason)
    formal = document.get("pin_formal_campaign_id")
    _require(type(formal) is str and FORMAL_ID.fullmatch(formal) is not None,
             reason)
    cadence = _integer(document.get("observer_cadence_ms"), reason, 1000)
    lateness = _integer(document.get("maximum_slot_lateness_ms"), reason, 0)
    poll = _integer(document.get("poll_interval_ms"), reason, 100)
    issued = _integer(document.get("issued_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason, issued + 1)
    _require(
        cadence <= MAXIMUM_CADENCE_MS and lateness <= cadence and
        lateness <= MAXIMUM_LAUNCH_LATENESS_MS and poll <= 60_000 and
        poll <= (LAUNCHER_EARLY_START_LEAD_MS -
                 LAUNCHER_MINIMUM_EXEC_MARGIN_MS) and
        (not require_current or issued <= now_ms < expires), reason)
    return document


def validate_freeze_bundle(
    document: dict[str, Any], now_ms: int, *, require_current: bool = True,
) \
        -> dict[str, Any]:
    reason = "P1_COORDINATOR_FREEZE_BUNDLE_INVALID"
    _validate_seal(document, reason)
    _reject_authority(document, reason)
    _require(
        document.get("schema") == FREEZE_SCHEMA and
        document.get("version") == VERSION and
        document.get("status") == "FROZEN" and
        document.get("production_mode") == "PRODUCTION_ROOT_PREFLIGHT" and
        type(document.get("round")) is int and document["round"] > 0 and
        BOOT_ID.fullmatch(str(document.get("boot_id"))) is not None and
        type(document.get("issued_at_ms")) is int and
        type(document.get("expires_at_ms")) is int and
        document["issued_at_ms"] < document["expires_at_ms"] and
        (not require_current or
         document["issued_at_ms"] <= now_ms < document["expires_at_ms"]),
        reason)
    _identifier(document.get("campaign_id"), reason)
    source_sha = _digest(document.get("source_manifest_sha256"), reason)
    del source_sha
    days = document.get("declared_trading_days")
    slots = document.get("eligible_scheduled_at_ms")
    _require(
        isinstance(days, list) and
        MINIMUM_TRADING_DAYS <= len(days) <= MAXIMUM_TRADING_DAYS and
        days == sorted(set(days)) and
        isinstance(slots, list) and len(slots) >= MINIMUM_ELIGIBLE_DECISIONS and
        slots == sorted(set(slots)) and
        all(type(item) is int and item >= 0 for item in slots), reason)
    formal = document.get("formal_policies")
    _require(isinstance(formal, list) and bool(formal), reason)
    previous_teardown_deadline = 0
    formal_ids: set[str] = set()
    for item in formal:
        item = _exact(item, frozenset({
            "campaign_id", "path", "file_sha256", "body_sha256",
            "launcher_start_ms", "launcher_dispatch_at_ms",
            "valid_after_ms", "expires_at_ms", "slot_interval_ms",
            "maximum_iterations", "launcher_completion_deadline_ms",
            "projection_deadline_ms", "teardown_deadline_ms",
        }), reason)
        campaign_id = item.get("campaign_id")
        _require(
            type(campaign_id) is str and FORMAL_ID.fullmatch(campaign_id) and
            campaign_id not in formal_ids, reason)
        _absolute(item.get("path"), reason)
        _digest(item.get("file_sha256"), reason)
        _digest(item.get("body_sha256"), reason)
        start = _integer(item.get("valid_after_ms"), reason, 1)
        expiry = _integer(item.get("expires_at_ms"), reason, start + 1)
        interval = _integer(item.get("slot_interval_ms"), reason, 1)
        maximum = _integer(item.get("maximum_iterations"), reason, 1)
        launcher_start = _integer(item.get("launcher_start_ms"), reason, 1)
        launcher_dispatch = _integer(
            item.get("launcher_dispatch_at_ms"), reason, 1)
        launcher_completion = _integer(
            item.get("launcher_completion_deadline_ms"), reason, expiry)
        projection_deadline = _integer(
            item.get("projection_deadline_ms"), reason,
            launcher_completion)
        teardown_deadline = _integer(
            item.get("teardown_deadline_ms"), reason,
            projection_deadline)
        _require(
            launcher_start == launcher_start_for_policy(start, interval) and
            launcher_dispatch ==
                launcher_start - LAUNCHER_EARLY_START_LEAD_MS and
            launcher_dispatch >= document["issued_at_ms"] and
            interval == POLICY_SLOT_INTERVAL_MS and
            maximum == POLICY_MAXIMUM_ITERATIONS and
            (previous_teardown_deadline == 0 or
             start == required_valid_after_after_teardown(
                 previous_teardown_deadline, interval)) and
            expiry == start + interval * maximum and
            launcher_completion == expiry + MAXIMUM_LAUNCH_LATENESS_MS and
            projection_deadline ==
                expiry + POST_FORMAL_PROJECTION_GUARD_MS and
            teardown_deadline ==
                expiry + POST_FORMAL_TEARDOWN_GUARD_MS, reason)
        previous_teardown_deadline = teardown_deadline
        formal_ids.add(campaign_id)
    _require(document["expires_at_ms"] > previous_teardown_deadline, reason)
    planned = document.get("planned_faults")
    _require(isinstance(planned, list) and len(planned) == 7, reason)
    roles: set[str] = set()
    pins = document.get("source_producer_pins")
    _require(isinstance(pins, list), reason)
    for raw in pins:
        pin = _exact(raw, SOURCE_PIN_FIELDS, reason)
        role = _identifier(pin.get("role"), reason)
        _require(role not in roles, reason)
        _absolute(pin.get("installed_path"), reason)
        _digest(pin.get("file_sha256"), reason)
        _require(type(pin.get("source_path")) is str and pin["source_path"],
                 reason)
        if role in ROLE_PATHS:
            _require(pin["installed_path"] == ROLE_PATHS[role], reason)
        roles.add(role)
    _require(set(ROLE_PATHS).issubset(roles), reason)
    anchors = document.get("anchors")
    _require(isinstance(anchors, dict) and set(anchors).issuperset({
        "source_anchor", "policy_anchor", "strategy_anchor",
        "frozen_schedule", "frozen_fault_schedule",
    }), reason)
    for value in anchors.values():
        _validate_reference(value, reason)
    return document


def _validate_runtime(document: dict[str, Any]) -> dict[str, Any]:
    reason = "P1_COORDINATOR_RUNTIME_INVALID"
    _exact(document, RUNTIME_FIELDS, reason)
    _validate_seal(document, reason)
    _reject_authority(document, reason)
    _require(
        document.get("schema") == RUNTIME_SCHEMA and
        document.get("version") == VERSION and
        document.get("status") == "FROZEN" and
        BOOT_ID.fullmatch(str(document.get("boot_id"))) is not None,
        reason)
    _identifier(document.get("campaign_id"), reason)
    _integer(document.get("round"), reason, 1)
    issued_at_ms = _integer(document.get("issued_at_ms"), reason)
    runtime_expires_at_ms = _integer(
        document.get("expires_at_ms"), reason, issued_at_ms + 1)
    for field in ("freeze_bundle", "campaign_spec", "fault_plan"):
        _validate_reference(document.get(field), reason)
    formal = document.get("formal_campaigns")
    _require(isinstance(formal, list) and bool(formal), reason)
    previous_teardown_deadline = 0
    for value in formal:
        item = _exact(value, FORMAL_RUNTIME_FIELDS, reason)
        _require(FORMAL_ID.fullmatch(str(item.get("formal_campaign_id"))), reason)
        _require(type(item.get("probe_campaign_id")) is str, reason)
        valid_after = _integer(item.get("valid_after_ms"), reason, 1)
        launcher_start = _integer(item.get("launcher_start_ms"), reason, 1)
        launcher_dispatch = _integer(
            item.get("launcher_dispatch_at_ms"), reason, 1)
        expiry = _integer(item.get("expires_at_ms"), reason, valid_after + 1)
        completion_deadline = _integer(
            item.get("launcher_completion_deadline_ms"), reason, expiry)
        projection_deadline = _integer(
            item.get("projection_deadline_ms"), reason, completion_deadline)
        teardown_deadline = _integer(
            item.get("teardown_deadline_ms"), reason, projection_deadline)
        interval = _integer(item.get("slot_interval_ms"), reason, 1)
        maximum = _integer(item.get("maximum_iterations"), reason, 1)
        _require(
            launcher_start == launcher_start_for_policy(
                valid_after, interval) and
            launcher_dispatch ==
                launcher_start - LAUNCHER_EARLY_START_LEAD_MS and
            interval == POLICY_SLOT_INTERVAL_MS and
            maximum == POLICY_MAXIMUM_ITERATIONS and
            expiry == valid_after + interval * maximum and
            completion_deadline == expiry + MAXIMUM_LAUNCH_LATENESS_MS and
            projection_deadline ==
                expiry + POST_FORMAL_PROJECTION_GUARD_MS and
            teardown_deadline == expiry + POST_FORMAL_TEARDOWN_GUARD_MS and
            (previous_teardown_deadline == 0 or
             valid_after == required_valid_after_after_teardown(
                 previous_teardown_deadline, interval)), reason)
        previous_teardown_deadline = teardown_deadline
        _validate_reference(item.get("policy"), reason)
        for field in ("launcher_receipt_path", "verified_closure_path",
                      "artifact_root"):
            _absolute(item.get(field), reason)
    _require(runtime_expires_at_ms > previous_teardown_deadline, reason)
    executables = document.get("executables")
    _require(isinstance(executables, dict) and
             set(executables) == set(ROLE_PATHS), reason)
    for role, raw in executables.items():
        item = _exact(raw, EXECUTABLE_FIELDS, reason)
        _require(item.get("path") == ROLE_PATHS[role], reason)
        _digest(item.get("file_sha256"), reason)
    return document


def validate_final_audit(
    snapshot: Snapshot, runtime_snapshot: Snapshot,
    bundle_snapshot: Snapshot, spec_snapshot: Snapshot,
) -> dict[str, Any]:
    """Exact local consumer validation for the certifying GO receipt."""

    reason = "P1_COORDINATOR_FINAL_AUDIT_INVALID"
    value = _exact(snapshot.document, AUDIT_RECEIPT_FIELDS, reason)
    _validate_seal(value, reason)
    _reject_authority(value, reason)
    runtime = runtime_snapshot.document
    runtime_reference = {
        **_reference(runtime_snapshot), "schema": CAMPAIGN_RUNTIME_SCHEMA,
    }
    _require(
        _reference(spec_snapshot) == runtime["campaign_spec"] and
        value.get("campaign_runtime") == runtime_reference, reason)
    spec = spec_snapshot.document
    minimum_duration_ns = _integer(
        spec.get("minimum_boottime_duration_ns"), reason,
        MINIMUM_BOOTTIME_DURATION_NS)
    maximum_gap_ns = _integer(
        spec.get("maximum_checkpoint_gap_ns"), reason, 1)
    scheduled_decisions = _integer(
        spec.get("scheduled_decision_count"), reason,
        MINIMUM_ELIGIBLE_DECISIONS)
    minimum_eligible = _integer(
        spec.get("minimum_eligible_decisions"), reason,
        MINIMUM_ELIGIBLE_DECISIONS)
    minimum_complete_ppm = _integer(
        spec.get("minimum_complete_ppm"), reason, MINIMUM_COMPLETE_PPM)
    eligible_schedule = spec.get("eligible_scheduled_at_ms")
    declared_days = spec.get("declared_trading_days")
    _require(
        isinstance(declared_days, list) and
        len(declared_days) == len(set(declared_days)) and
        isinstance(eligible_schedule, list) and
        eligible_schedule == sorted(set(eligible_schedule)) and
        all(type(item) is int and item >= 0 for item in eligible_schedule) and
        minimum_eligible <= len(eligible_schedule) <= scheduled_decisions and
        MINIMUM_COMPLETE_PPM <= minimum_complete_ppm <= 1_000_000,
        reason)
    _require(
        value.get("schema") == AUDIT_SCHEMA and
        value.get("version") == VERSION and value.get("phase") == "P1_SHADOW" and
        value.get("verdict") == "GO" and
        value.get("campaign_id") == runtime["campaign_id"] and
        value.get("domain_id") == spec.get("domain_id") and
        value.get("independent_auditor_id") ==
            spec.get("independent_auditor_id") and
        type(value.get("audited_at_ms")) is int and
        value["audited_at_ms"] >= 0 and
        value.get("campaign_spec_file_sha256") ==
            spec_snapshot.file_sha256 and
        value.get("campaign_spec_body_sha256") ==
            spec_snapshot.body_sha256 and
        value.get("freeze_bundle") == _reference(bundle_snapshot) and
        value.get("production_mode") == "PRODUCTION_ROOT_AUDIT" and
        value.get("producer") == runtime["executables"]["auditor"] and
        value.get("source_manifest_sha256") ==
            spec.get("source_manifest_sha256") and
        value.get("policy_sha256") == spec.get("policy_sha256") and
        value.get("strategy_sha256") == spec.get("strategy_sha256") and
        value.get("p1_safety_soak_gate_satisfied") is True and
        value.get("paper_test_admission_candidate") is False and
        value.get("safest_allowed_next_action") ==
            "CONTINUE_REMAINING_PAPER_ADMISSION_GATES" and
        value.get("failed_invariants") == [], reason)
    interval = _exact(value.get("evaluated_interval"),
                      AUDIT_INTERVAL_FIELDS, reason)
    formals = runtime["formal_campaigns"]
    continuity_origin_ms = formals[0]["launcher_dispatch_at_ms"]
    continuity_end_ms = formals[-1]["teardown_deadline_ms"]
    continuity_cadence_ms = _integer(
        runtime.get("observer_cadence_ms"), reason, 1)
    continuity_final_slot = (
        continuity_end_ms - continuity_origin_ms + continuity_cadence_ms - 1
    ) // continuity_cadence_ms
    _require(
        interval.get("clock_id") == "CLOCK_BOOTTIME" and
        interval.get("boot_id") == runtime["boot_id"] and
        interval.get("consecutive") is True and
        type(interval.get("start_boottime_ns")) is int and
        type(interval.get("end_boottime_ns")) is int and
        0 <= interval["start_boottime_ns"] <=
            interval["end_boottime_ns"] and
        type(interval.get("duration_ns")) is int and
        interval["duration_ns"] ==
            interval["end_boottime_ns"] -
            interval["start_boottime_ns"] and
        interval["duration_ns"] >= MINIMUM_BOOTTIME_DURATION_NS and
        interval["duration_ns"] >= minimum_duration_ns and
        type(interval.get("maximum_checkpoint_gap_ns")) is int and
        interval["maximum_checkpoint_gap_ns"] >= 0 and
        interval["maximum_checkpoint_gap_ns"] <= maximum_gap_ns and
        interval["maximum_checkpoint_gap_ns"] <= MAXIMUM_CADENCE_MS * 1_000_000,
        reason)
    _require(
        interval.get("continuity_origin_ms") == continuity_origin_ms and
        interval.get("continuity_end_ms") == continuity_end_ms and
        interval.get("continuity_final_slot") == continuity_final_slot,
        reason)
    counts = _exact(value.get("counts"), AUDIT_COUNTS_FIELDS, reason)
    _require(
        all(type(item) is int and item >= 0 for item in counts.values()) and
        counts["launcher_receipts"] == len(runtime["formal_campaigns"]) and
        counts["verified_closures"] == len(runtime["formal_campaigns"]) and
        counts["continuity_checkpoints"] >= 2 and
        counts["declared_trading_days"] ==
            len(declared_days) and
        MINIMUM_TRADING_DAYS <= counts["declared_trading_days"] <=
            MAXIMUM_TRADING_DAYS and
        counts["observed_trading_days"] ==
            counts["declared_trading_days"] and
        counts["scheduled_decisions"] == scheduled_decisions and
        counts["decision_receipts"] == counts["scheduled_decisions"] and
        counts["eligible_decisions"] == len(eligible_schedule) and
        counts["eligible_decisions"] >= minimum_eligible and
        counts["complete_eligible_decisions"] +
            counts["incomplete_eligible_decisions"] ==
            counts["eligible_decisions"] and
        counts["complete_eligible_decisions"] * 100 >
            counts["eligible_decisions"] * 99 and
        counts["catch_up_decisions"] == 0 and
        counts["planned_faults"] == 7 and counts["fault_results"] == 7 and
        counts["authority_snapshots"] > 0,
        reason)
    completeness = _exact(
        value.get("completeness"), AUDIT_COMPLETENESS_FIELDS, reason)
    _require(
        completeness.get("numerator") == counts["complete_eligible_decisions"] and
        completeness.get("denominator") == counts["eligible_decisions"] and
        type(completeness.get("ppm")) is int and
        completeness["ppm"] == (
            counts["complete_eligible_decisions"] * 1_000_000 //
            counts["eligible_decisions"]) and
        minimum_complete_ppm <= completeness["ppm"] <= 1_000_000 and
        completeness.get("strictly_greater_than_99_percent") is True, reason)
    exposure = _exact(value.get("exposure_summary"),
                      AUDIT_EXPOSURE_FIELDS, reason)
    _require(
        exposure.get("evidence_present") is True and
        exposure.get("campaign_socket_ever_present") is False and
        exposure.get("kill_switch_continuously_engaged") is True and
        exposure.get("local_boundary_uncertain") is False and
        exposure.get("scope") == "LOCAL_HOST_BOUNDARY_ONLY" and
        exposure.get("authoritative_account_state_observed") is False and
        all(exposure.get(field) == 0 for field in (
            "maximum_connector_count", "maximum_authorized_uid_count",
            "maximum_paper_unit_active_count")), reason)
    cleanup = _exact(value.get("cleanup_status"), AUDIT_CLEANUP_FIELDS, reason)
    _require(
        cleanup.get("complete") is True and
        type(cleanup.get("required_subject_count")) is int and
        cleanup.get("verified_subject_count") ==
            cleanup.get("required_subject_count") and
        cleanup["required_subject_count"] ==
            len(runtime["formal_campaigns"]) + 8,
        reason)
    checked = value.get("checked_artifacts")
    _require(isinstance(checked, list) and bool(checked), reason)
    previous: tuple[str, str] | None = None
    artifact_keys: set[tuple[str, str, str, str]] = set()
    for raw in checked:
        item = _exact(raw, frozenset({
            "role", "path", "file_sha256", "body_sha256"}), reason)
        key = (item.get("role"), item.get("path"))
        _require(
            type(key[0]) is str and type(key[1]) is str and
            Path(key[1]).is_absolute() and _digest(item.get("file_sha256"), reason) and
            _digest(item.get("body_sha256"), reason) and
            (previous is None or key > previous), reason)
        previous = key
        artifact_keys.add((item["role"], item["path"],
                           item["file_sha256"], item["body_sha256"]))
    for role, reference in (
        ("campaign_spec", _reference(spec_snapshot)),
        ("freeze_bundle", _reference(bundle_snapshot)),
        ("fault_plan", runtime["fault_plan"]),
    ):
        _require((role, reference["path"], reference["file_sha256"],
                  reference["body_sha256"]) in artifact_keys, reason)
    return value


def _make_runtime(
    contract: Mapping[str, Any], bundle_snapshot: Snapshot,
    spec_snapshot: Snapshot, plan_snapshot: Snapshot,
) -> dict[str, Any]:
    bundle = bundle_snapshot.document
    campaign_id = str(bundle["campaign_id"])
    decision_lateness_ms = _integer(
        spec_snapshot.document.get("maximum_decision_lateness_ms"),
        "P1_COORDINATOR_SPEC_DECISION_LATENESS_INVALID")
    formal_values: list[dict[str, Any]] = []
    for raw in bundle["formal_policies"]:
        match = FORMAL_ID.fullmatch(raw["campaign_id"])
        _require(match is not None, "P1_COORDINATOR_FORMAL_ID_INVALID")
        _require(
            contract["observer_cadence_ms"] <=
                raw["slot_interval_ms"] - decision_lateness_ms,
            "P1_COORDINATOR_POST_DECISION_CLOCK_CADENCE_INVALID")
        round_number, date_value = match.groups()
        formal_values.append({
            "formal_campaign_id": raw["campaign_id"],
            "probe_campaign_id": (
                f"hepta-p1-shadow-load-probe-round{int(round_number) - 1}-"
                f"{date_value}"),
            "launcher_start_ms": raw["launcher_start_ms"],
            "launcher_dispatch_at_ms": raw["launcher_dispatch_at_ms"],
            "valid_after_ms": raw["valid_after_ms"],
            "expires_at_ms": raw["expires_at_ms"],
            "launcher_completion_deadline_ms":
                raw["launcher_completion_deadline_ms"],
            "projection_deadline_ms": raw["projection_deadline_ms"],
            "teardown_deadline_ms": raw["teardown_deadline_ms"],
            "slot_interval_ms": raw["slot_interval_ms"],
            "maximum_iterations": raw["maximum_iterations"],
            "policy": {
                "path": raw["path"], "file_sha256": raw["file_sha256"],
                "body_sha256": raw["body_sha256"],
            },
            "launcher_receipt_path": (
                f"/var/lib/hepta/p1-admission/private/round{round_number}/"
                "launcher-receipt.json"),
            "verified_closure_path": (
                f"/var/lib/hepta/p1-admission/private/round{round_number}/"
                "formal-verified-closure.json"),
            "artifact_root": (
                "/var/lib/hepta/p1-admission/readers/" +
                raw["campaign_id"] + "/observer"),
        })
    pin_by_role = {item["role"]: item
                   for item in bundle["source_producer_pins"]}
    executables = {
        role: {
            "path": path, "file_sha256": pin_by_role[role]["file_sha256"],
        } for role, path in ROLE_PATHS.items()
    }
    state_root = Path(str(contract["state_root"]))
    return seal({
        "schema": RUNTIME_SCHEMA, "version": VERSION, "status": "FROZEN",
        "campaign_id": campaign_id, "round": bundle["round"],
        "boot_id": bundle["boot_id"], "issued_at_ms": bundle["issued_at_ms"],
        "expires_at_ms": bundle["expires_at_ms"],
        "freeze_bundle": _reference(bundle_snapshot),
        "campaign_spec": _reference(spec_snapshot),
        "fault_plan": _reference(plan_snapshot),
        "pin_formal_campaign_id": contract["pin_formal_campaign_id"],
        "formal_campaigns": formal_values,
        "observer_cadence_ms": contract["observer_cadence_ms"],
        "maximum_slot_lateness_ms": contract["maximum_slot_lateness_ms"],
        "state_root": str(state_root),
        "raw_observation_directory": str(state_root / "raw-observations"),
        "recorder_root": str(state_root / "recorder"),
        "injector_journal_directory": str(state_root / "injector-journal"),
        "injector_output_directory": str(state_root / "injection-receipts"),
        "control_directory": str(state_root / "control"),
        "executables": executables, **boundary(),
    })


def _sd_notify(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
            channel.connect(address)
            channel.sendall(message.encode("utf-8"))
    except OSError:
        pass


class ControlQueue:
    def __init__(
        self, root: Path, campaign_id: str, *, expected_uid: int,
        expected_gid: int,
    ) -> None:
        self.root = root
        self.campaign_id = campaign_id
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid

    def _directory(self, target: str, kind: str) -> Path:
        _require(target in {"observer", "recorder"} and
                 kind in {"requests", "acks"},
                 "P1_COORDINATOR_CONTROL_TARGET_INVALID")
        return self.root / f"{target}-{kind}"

    def _load_chain(self, target: str, kind: str) -> list[Snapshot]:
        directory = self._directory(target, kind)
        _trusted_directory(
            directory, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        names = sorted(item.name for item in directory.iterdir()
                       if not item.name.startswith("."))
        _require(names == [f"{index:08d}.json" for index in range(len(names))],
                 "P1_COORDINATOR_CONTROL_CHAIN_GAP")
        fields = REQUEST_FIELDS if kind == "requests" else ACK_FIELDS
        schema = REQUEST_SCHEMA if kind == "requests" else ACK_SCHEMA
        previous: str | None = None
        result: list[Snapshot] = []
        for index, name in enumerate(names):
            snapshot = _secure_read(
                directory / name, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid, modes=frozenset({0o600}))
            value = _exact(snapshot.document, fields,
                           "P1_COORDINATOR_CONTROL_INVALID")
            _validate_seal(value, "P1_COORDINATOR_CONTROL_INVALID")
            _reject_authority(value, "P1_COORDINATOR_CONTROL_AUTHORITY")
            _require(
                value.get("schema") == schema and
                value.get("campaign_id") == self.campaign_id and
                value.get("sequence") == index and
                value.get("previous_body_sha256") == previous,
                "P1_COORDINATOR_CONTROL_CHAIN_INVALID")
            previous = snapshot.body_sha256
            result.append(snapshot)
        return result

    def publish(
        self, target: str, action: str, arguments: Mapping[str, Any],
        *, now_ms: int, deadline_ms: int,
    ) -> dict[str, Any]:
        entries = self._load_chain(target, "requests")
        request_id = f"{target}-{len(entries):08d}-{action.lower()}"
        document = seal({
            "schema": REQUEST_SCHEMA, "version": VERSION,
            "campaign_id": self.campaign_id, "sequence": len(entries),
            "request_id": request_id, "target": target, "action": action,
            "created_at_ms": now_ms, "deadline_ms": deadline_ms,
            "arguments": dict(arguments),
            "previous_body_sha256": (
                None if not entries else entries[-1].body_sha256),
            **boundary(),
        })
        publish_noreplace(
            self._directory(target, "requests") /
            f"{len(entries):08d}.json", document,
            expected_uid=self.expected_uid, expected_gid=self.expected_gid)
        return document

    def ack(self, target: str, request_id: str) -> dict[str, Any] | None:
        for item in self._load_chain(target, "acks"):
            if item.document["request_id"] == request_id:
                return item.document
        return None


class CampaignCoordinator:
    """Durable state machine shared by production and fake-systemd tests."""

    def __init__(
        self, contract_snapshot: Snapshot, bundle_snapshot: Snapshot,
        runtime_snapshot: Snapshot, adapter: ExecutionAdapter, *,
        expected_uid: int, expected_gid: int,
        wall_clock: callable = lambda: time.time_ns() // 1_000_000,
        boot_clock: callable = time.monotonic_ns,
        crash_after_event: str | None = None,
    ) -> None:
        self.contract_snapshot = contract_snapshot
        self.contract = contract_snapshot.document
        self.bundle_snapshot = bundle_snapshot
        self.bundle = bundle_snapshot.document
        self.runtime_snapshot = runtime_snapshot
        self.runtime = runtime_snapshot.document
        self.adapter = adapter
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid
        self.wall_clock = wall_clock
        self.boot_clock = boot_clock
        self.crash_after_event = crash_after_event
        self.root = Path(self.runtime["state_root"])
        self.journal = Journal(
            self.root / "coordinator-journal", self.runtime["campaign_id"],
            expected_uid=expected_uid, expected_gid=expected_gid,
            wall_clock=wall_clock, boot_clock=boot_clock)
        self.control = ControlQueue(
            Path(self.runtime["control_directory"]),
            self.runtime["campaign_id"], expected_uid=expected_uid,
            expected_gid=expected_gid)
        self.executables = self.runtime["executables"]
        self._worker_journals: dict[str, Journal] = {}

    def _append(
        self, event: str, status_value: str, details: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = self.journal.append(event, status_value, details)
        _sd_notify("WATCHDOG=1\nSTATUS=" + event + ":" + status_value)
        if self.crash_after_event == f"{event}:{status_value}":
            raise InjectedCrash(self.crash_after_event)
        return result

    def _events(self, event: str, **details: Any) -> list[dict[str, Any]]:
        return self.journal.matching(event, **details)

    def _has_commit(self, event: str, **details: Any) -> bool:
        return any(item["status"] == "COMMITTED"
                   for item in self._events(event, **details))

    def _has_intent(self, event: str, **details: Any) -> bool:
        return any(item["status"] == "INTENT"
                   for item in self._events(event, **details))

    def _verify_executable(self, role: str) -> None:
        value = self.executables[role]
        _secure_executable(
            Path(value["path"]), value["file_sha256"],
            expected_uid=self.expected_uid, expected_gid=self.expected_gid)

    def _run_pinned(
        self, role: str, argv: Sequence[str], timeout_seconds: int,
    ) -> CommandResult:
        _require(argv[0] == self.executables[role]["path"],
                 "P1_COORDINATOR_EXECUTABLE_ROLE_DRIFT")
        self._verify_executable(role)
        result = self.adapter.run(argv, timeout_seconds)
        self._verify_executable(role)
        return result

    def _unit_names(self) -> dict[str, str]:
        formal = self.runtime["pin_formal_campaign_id"]
        match = FORMAL_ID.fullmatch(formal)
        _require(match is not None, "P1_COORDINATOR_PIN_FORMAL_INVALID")
        round_number = int(match.group(1))
        return {
            "observer": (
                "hepta-p1-shadow-independent-observer-"
                f"round{round_number}.service"),
            "recorder": (
                "hepta-p1-shadow-evidence-recorder-"
                f"round{round_number}.service"),
            "injector": (
                "hepta-p1-safety-soak-root-fault-injector-"
                f"round{round_number}.service"),
        }

    def _coordinator_unit(self) -> str:
        return (
            "hepta-p1-safety-soak-campaign@" +
            self.runtime["campaign_id"] + ".service")

    def assert_formal_deadline(
        self, formal: Mapping[str, Any], stage: str,
    ) -> None:
        fields = {
            "LAUNCHER": ("launcher_completion_deadline_ms",
                         "P1_COORDINATOR_LAUNCHER_COMPLETION_DEADLINE"),
            "PROJECTION": ("projection_deadline_ms",
                           "P1_COORDINATOR_PROJECTION_DEADLINE"),
            "CLEANUP": ("teardown_deadline_ms",
                        "P1_COORDINATOR_LAUNCHER_CLEANUP_DEADLINE"),
        }
        _require(stage in fields, "P1_COORDINATOR_DEADLINE_STAGE_INVALID")
        field, reason = fields[stage]
        _require(int(self.wall_clock()) <= int(formal[field]), reason)

    def _ownership_properties(self) -> list[str]:
        owner = self._coordinator_unit()
        return [f"PartOf={owner}", f"BindsTo={owner}", f"After={owner}"]

    def _assert_transient_contract(
        self, unit: str, *, capability_names: frozenset[str],
        address_families: frozenset[str], read_write_paths: frozenset[str],
    ) -> None:
        value = self.adapter.show_unit(unit)
        reason = "P1_COORDINATOR_TRANSIENT_HARDENING_DRIFT"
        for field in (
            "NoNewPrivileges", "PrivateTmp", "PrivateDevices", "ProtectHome",
            "ProtectHostname", "ProtectKernelTunables",
            "ProtectKernelModules", "ProtectKernelLogs",
            "ProtectControlGroups", "ProtectClock", "RestrictNamespaces",
            "RestrictSUIDSGID", "RestrictRealtime", "LockPersonality",
            "MemoryDenyWriteExecute", "RemoveIPC",
        ):
            _require(value.get(field) == "yes", reason)
        owner = self._coordinator_unit()
        capability_tokens = [
            item.lower() for item in value.get(
                "CapabilityBoundingSet", "").split() if item]
        normalized_caps = set(capability_tokens)
        family_tokens = value.get("RestrictAddressFamilies", "").split()
        _require(
            value.get("ProtectSystem") == "strict" and
            value.get("KeyringMode") == "private" and
            value.get("SystemCallArchitectures") == "native" and
            value.get("AmbientCapabilities") == "" and
            len(capability_tokens) == len(normalized_caps) and
            normalized_caps == {item.lower() for item in capability_names} and
            len(family_tokens) == len(set(family_tokens)) and
            set(family_tokens) == set(address_families) and
            not {"AF_INET", "AF_INET6"}.intersection(family_tokens) and
            set(value.get("IPAddressDeny", "").split()) == {
                "0.0.0.0/0", "::/0"} and
            owner in value.get("PartOf", "").split() and
            owner in value.get("BindsTo", "").split() and
            owner in value.get("After", "").split() and
            read_write_paths.issubset(set(
                value.get("ReadWritePaths", "").split())), reason)

    def assert_forbidden_units_inert(self, phase: str) -> None:
        """Prove every exact PAPER/LIVE surface disabled and inactive."""

        _require(phase in {"PREFLIGHT", "TERMINAL"},
                 "P1_COORDINATOR_BOUNDARY_PHASE_INVALID")
        event = "ASSERT_EXECUTION_BOUNDARY_" + phase
        states: list[dict[str, Any]] = []
        allowed_file_states = {
            "disabled", "static", "indirect", "masked", "masked-runtime",
            "not-found",
        }
        for unit in FORBIDDEN_EXECUTION_UNITS:
            shown = self.adapter.show_unit(unit)
            enabled = self.adapter.unit_enabled_state(unit)
            _require(
                enabled in allowed_file_states and
                shown.get("UnitFileState", enabled) in
                    allowed_file_states | {""} and
                shown.get("ActiveState") in {"inactive", "failed"} and
                shown.get("SubState") not in {
                    "running", "listening", "start", "start-pre",
                } and shown.get("Job", "") == "" and
                int(shown.get("MainPID", "0") or "0") == 0,
                "P1_COORDINATOR_PAPER_OR_LIVE_UNIT_NOT_INERT")
            states.append({
                "unit": unit, "is_enabled": enabled,
                "unit_file_state": shown.get("UnitFileState", ""),
                "load_state": shown.get("LoadState", ""),
                "active_state": shown.get("ActiveState", ""),
                "sub_state": shown.get("SubState", ""),
                "main_pid": int(shown.get("MainPID", "0") or "0"),
                "job": shown.get("Job", ""),
            })
        digest = digest_bytes(canonical_bytes(states))
        if not self._has_commit(event):
            self._append(event, "COMMITTED", {
                "states": states, "states_sha256": digest,
            })
        else:
            prior = next(item for item in self._events(event)
                         if item["status"] == "COMMITTED")
            _require(prior["details"]["states_sha256"] == digest,
                     "P1_COORDINATOR_EXECUTION_BOUNDARY_DRIFT")

    def assert_static_unit_contracts(self, phase: str) -> None:
        """Bind installed static fragments to the frozen source manifest."""

        _require(phase in {"PREFLIGHT", "TERMINAL"},
                 "P1_COORDINATOR_STATIC_UNIT_PHASE_INVALID")
        reason = "P1_COORDINATOR_STATIC_UNIT_CONTRACT_INVALID"
        reference = _validate_reference(
            self.bundle.get("source_baseline"), reason)
        baseline = _secure_read(
            Path(reference["path"]), expected_uid=self.expected_uid,
            expected_gid=self.expected_gid,
            modes=frozenset({0o400, 0o600, 0o644}), sealed=False)
        _require(
            baseline.file_sha256 == reference["file_sha256"] and
            baseline.body_sha256 == reference["body_sha256"] and
            baseline.document.get("schema") ==
                "hepta.versioned-source-baseline.v1", reason)
        manifest = baseline.document.get("source_manifest")
        _require(isinstance(manifest, dict) and
                 isinstance(manifest.get("files"), list), reason)
        by_source: dict[str, str] = {}
        for raw in manifest["files"]:
            _require(isinstance(raw, dict) and
                     type(raw.get("path")) is str and
                     type(raw.get("sha256")) is str, reason)
            by_source[raw["path"]] = _digest(raw["sha256"], reason)

        contracts = (
            (
                "systemd/hepta-p1-safety-soak-campaign@.service",
                "hepta-p1-safety-soak-campaign@.service",
                self._coordinator_unit(), "coordinator"),
            (
                "systemd/hepta-p1-safety-soak-observer-worker@.service",
                "hepta-p1-safety-soak-observer-worker@.service",
                "hepta-p1-safety-soak-observer-worker@.service", "observer"),
            (
                "systemd/hepta-p1-safety-soak-recorder-worker@.service",
                "hepta-p1-safety-soak-recorder-worker@.service",
                "hepta-p1-safety-soak-recorder-worker@.service", "recorder"),
            (
                "systemd/hepta-p1-safety-soak@.target",
                "hepta-p1-safety-soak@.target",
                "hepta-p1-safety-soak@.target", "target"),
        )
        results: list[dict[str, Any]] = []
        for source_path, installed_name, manager_unit, role in contracts:
            _require(source_path in by_source, reason)
            installed_path = Path("/usr/lib/systemd/system") / installed_name
            payload = _secure_static_file(
                installed_path, by_source[source_path],
                expected_uid=self.expected_uid, expected_gid=self.expected_gid)
            manager = self.adapter.unit_contract_state(manager_unit)
            enabled = self.adapter.unit_enabled_state(manager_unit)
            _require(
                manager.get("LoadState") == "loaded" and
                manager.get("UnitFileState") == "static" and
                enabled == "static" and manager.get("DropInPaths") == "" and
                manager.get("FragmentPath") == str(installed_path), reason)
            sections = _unit_sections(payload)
            _require("Install" not in sections, reason)
            if role == "target":
                unit = sections.get("Unit", {})
                _require(
                    unit.get("Requires") == [
                        "hepta-p1-safety-soak-campaign@%i.service"] and
                    unit.get("StopWhenUnneeded") == ["yes"] and
                    set(word for value in unit.get("Conflicts", [])
                        for word in value.split()) ==
                        set(FORBIDDEN_EXECUTION_UNITS), reason)
            else:
                service = sections.get("Service", {})
                expected_exec = {
                    "coordinator": (
                        "/usr/libexec/hepta-p1-safety-soak-campaign-coordinator "
                        "--run --launch-contract "
                        "/etc/heptatrader/p1-safety-soak/%i.json"),
                    "observer": (
                        "/usr/libexec/hepta-p1-safety-soak-observer-worker "
                        "--run --runtime-manifest "
                        "/var/lib/hepta/p1-safety-soak/%i/runtime-manifest.json "
                        "--expected-runtime-manifest-file-sha256 "
                        "${HEPTA_P1_RUNTIME_FILE_SHA256}"),
                    "recorder": (
                        "/usr/libexec/hepta-p1-safety-soak-recorder-worker "
                        "--run --runtime-manifest "
                        "/var/lib/hepta/p1-safety-soak/%i/runtime-manifest.json "
                        "--expected-runtime-manifest-file-sha256 "
                        "${HEPTA_P1_RUNTIME_FILE_SHA256}"),
                }[role]
                _require(
                    service.get("Type") == ["notify"] and
                    service.get("NotifyAccess") == ["main"] and
                    service.get("User") == ["root"] and
                    service.get("Group") == ["root"] and
                    service.get("ExecStart") == [expected_exec] and
                    service.get("Restart") == ["on-failure"] and
                    service.get("RestartSec") ==
                        (["5s"] if role == "coordinator" else ["1s"]) and
                    service.get("WatchdogSec") ==
                        (["45s"] if role == "coordinator" else ["30s"]) and
                    service.get("NoNewPrivileges") == ["yes"] and
                    service.get("PrivateDevices") == ["yes"] and
                    service.get("PrivateTmp") == ["yes"] and
                    service.get("ProtectSystem") == ["strict"] and
                    service.get("ProtectHome") == ["yes"] and
                    service.get("ProtectHostname") == ["yes"] and
                    service.get("RestrictNamespaces") == ["yes"] and
                    service.get("ProtectKernelTunables") == ["yes"] and
                    service.get("ProtectKernelModules") == ["yes"] and
                    service.get("ProtectControlGroups") == ["yes"] and
                    service.get("RestrictSUIDSGID") == ["yes"] and
                    service.get("LockPersonality") == ["yes"] and
                    service.get("KeyringMode") == ["private"] and
                    service.get("RemoveIPC") == ["yes"] and
                    service.get("MemoryDenyWriteExecute") == ["yes"] and
                    service.get("AmbientCapabilities") == [""] and
                    service.get("RestrictAddressFamilies") == ([
                        "AF_UNIX AF_NETLINK"] if role == "observer" else [
                        "AF_UNIX"]) and
                    service.get("IPAddressDeny") == ["any"] and
                    service.get("SystemCallArchitectures") == ["native"],
                    reason)
                expected_caps = {
                    "coordinator": "",
                    "observer": (
                        "CAP_DAC_READ_SEARCH CAP_SYS_PTRACE CAP_NET_ADMIN"),
                    "recorder": "CAP_DAC_READ_SEARCH",
                }[role]
                _require(service.get("CapabilityBoundingSet") ==
                         [expected_caps], reason)
                if role == "coordinator":
                    _require(
                        service.get("StateDirectory") ==
                            ["hepta/p1-safety-soak"] and
                        service.get("StateDirectoryMode") == ["0700"] and
                        set(word for value in sections["Unit"].get(
                            "Conflicts", []) for word in value.split()) ==
                            set(FORBIDDEN_EXECUTION_UNITS), reason)
                else:
                    _require(
                        sections["Unit"].get("PartOf") == [
                            "hepta-p1-safety-soak@%i.target"] and
                        service.get("EnvironmentFile") == [
                            "/run/hepta-p1-safety-soak/%i-worker.env"], reason)
            results.append({
                "role": role, "source_path": source_path,
                "installed_path": str(installed_path),
                "fragment_file_sha256": digest_bytes(payload),
                "fragment_path": manager["FragmentPath"],
                "drop_in_paths": manager["DropInPaths"],
                "unit_file_state": manager["UnitFileState"],
                "is_enabled": enabled,
            })
        event = "ASSERT_STATIC_UNIT_CONTRACTS_" + phase
        details = {
            "units": results,
            "units_sha256": digest_bytes(canonical_bytes(results)),
            "source_baseline": _reference(baseline),
        }
        committed = next((item for item in self._events(event)
                          if item["status"] == "COMMITTED"), None)
        if committed is None:
            self._append(event, "COMMITTED", details)
        else:
            _require(committed["details"] == details,
                     "P1_COORDINATOR_STATIC_UNIT_CONTRACT_DRIFT")

    def owned_units(self) -> list[str]:
        units = list(self._unit_names().values())
        units.extend(self._launcher_unit(item["formal_campaign_id"])
                     for item in self.runtime["formal_campaigns"])
        return sorted(set(units))

    def close_owned_units(self) -> dict[str, Any]:
        """Stop and prove absence of every coordinator-owned process/job."""

        event = "CLOSE_OWNED_UNITS"
        if not self._has_intent(event):
            self._append(event, "INTENT", {"units": self.owned_units()})
        states: list[dict[str, Any]] = []
        for unit in self.owned_units():
            before = self.adapter.show_unit(unit)
            if before.get("ActiveState") not in {"inactive", "failed"}:
                self.adapter.stop_unit(unit)
            after = self.adapter.show_unit(unit)
            _require(
                after.get("ActiveState") in {"inactive", "failed"} and
                after.get("SubState") not in {
                    "running", "listening", "start", "start-pre",
                } and int(after.get("MainPID", "0") or "0") == 0 and
                after.get("Job", "") == "",
                "P1_COORDINATOR_OWNED_UNIT_RESIDUE")
            fragment = after.get("FragmentPath", "")
            _require(
                after.get("LoadState") == "not-found" or
                Path(fragment).parent == Path("/run/systemd/transient"),
                "P1_COORDINATOR_OWNED_FRAGMENT_DRIFT")
            states.append({
                "unit": unit, "load_state": after.get("LoadState", ""),
                "active_state": after.get("ActiveState", ""),
                "sub_state": after.get("SubState", ""),
                "main_pid": int(after.get("MainPID", "0") or "0"),
                "job": after.get("Job", ""), "fragment_path": fragment,
            })
        details = {
            "states": states,
            "states_sha256": digest_bytes(canonical_bytes(states)),
            "all_inactive": True, "residue_absent": True,
        }
        existing = next((item for item in self._events(event)
                         if item["status"] == "COMMITTED"), None)
        if existing is None:
            return self._append(event, "COMMITTED", details)
        _require(existing["details"] == details,
                 "P1_COORDINATOR_OWNED_UNIT_CLOSURE_DRIFT")
        return existing

    @staticmethod
    def _unit_running(value: Mapping[str, str]) -> bool:
        try:
            pid = int(value.get("MainPID", "0"))
        except ValueError:
            return False
        return (
            value.get("LoadState") == "loaded" and
            value.get("ActiveState") == "active" and
            value.get("SubState") == "running" and pid > 0 and
            bool(value.get("InvocationID")) and
            Path(value.get("FragmentPath", "")).parent ==
                Path("/run/systemd/transient")
        )

    def _wait_running(self, unit: str) -> Mapping[str, str]:
        for _unused in range(SYSTEMD_READY_TIMEOUT_SECONDS * 5):
            value = self.adapter.show_unit(unit)
            if self._unit_running(value):
                return value
            if value.get("ActiveState") == "failed":
                raise CoordinatorError("P1_COORDINATOR_WORKER_FAILED")
            self.adapter.sleep(0.2)
        raise CoordinatorError("P1_COORDINATOR_WORKER_READY_TIMEOUT")

    def ensure_worker(self, worker: str, argv: Sequence[str]) -> None:
        _require(worker in {"observer", "recorder"},
                 "P1_COORDINATOR_WORKER_INVALID")
        unit = self._unit_names()[worker]
        event = "START_WORKER"
        argv_sha = digest_bytes(canonical_bytes(list(argv)))
        commits = self._events(event, worker=worker)
        if commits and any(item["status"] == "COMMITTED" for item in commits):
            state = self.adapter.show_unit(unit)
            _require(self._unit_running(state),
                     "P1_COORDINATOR_PINNED_WORKER_NOT_RUNNING")
            self._assert_transient_contract(
                unit, capability_names=(
                    frozenset({
                        "CAP_DAC_READ_SEARCH", "CAP_SYS_PTRACE",
                        "CAP_NET_ADMIN",
                    })
                    if worker == "observer" else
                    frozenset({"CAP_DAC_READ_SEARCH"})),
                address_families=(
                    frozenset({"AF_UNIX", "AF_NETLINK"})
                    if worker == "observer" else
                    frozenset({"AF_UNIX"})),
                read_write_paths=frozenset({str(self.root)}))
            return
        state = self.adapter.show_unit(unit)
        if not self._has_intent(event, worker=worker):
            self._append(event, "INTENT", {
                "worker": worker, "unit": unit, "exec_argv_sha256": argv_sha,
            })
        if state.get("LoadState") == "not-found":
            role = f"{worker}_worker"
            self._verify_executable(role)
            properties = list(WORKER_PROPERTIES)
            properties.extend(self._ownership_properties())
            properties.append(
                "CapabilityBoundingSet=CAP_DAC_READ_SEARCH CAP_SYS_PTRACE CAP_NET_ADMIN"
                if worker == "observer" else
                "CapabilityBoundingSet=CAP_DAC_READ_SEARCH")
            properties.append(
                "RestrictAddressFamilies=AF_UNIX AF_NETLINK"
                if worker == "observer" else
                "RestrictAddressFamilies=AF_UNIX")
            properties.append(f"ReadWritePaths={self.root}")
            self.adapter.start_transient(
                unit, argv, properties,
                {"HEPTA_P1_CAMPAIGN_ID": self.runtime["campaign_id"]})
        elif not self._unit_running(state):
            _require(
                state.get("FragmentPath") ==
                    f"/run/systemd/transient/{unit}",
                "P1_COORDINATOR_WORKER_FRAGMENT_DRIFT")
            self.adapter.start_unit(unit)
        state = self._wait_running(unit)
        self._assert_transient_contract(
            unit, capability_names=(
                frozenset({
                    "CAP_DAC_READ_SEARCH", "CAP_SYS_PTRACE",
                    "CAP_NET_ADMIN",
                })
                if worker == "observer" else
                frozenset({"CAP_DAC_READ_SEARCH"})),
            address_families=(
                frozenset({"AF_UNIX", "AF_NETLINK"})
                if worker == "observer" else frozenset({"AF_UNIX"})),
            read_write_paths=frozenset({str(self.root)}))
        self._append(event, "COMMITTED", {
            "worker": worker, "unit": unit, "exec_argv_sha256": argv_sha,
            "fragment_path": state["FragmentPath"],
            "invocation_id": state["InvocationID"],
            "main_pid": int(state["MainPID"]),
            "n_restarts": int(state.get("NRestarts", "0") or "0"),
        })

    def assert_workers_healthy(self) -> None:
        """Re-prove worker liveness and reject a durable worker terminal."""

        units = self._unit_names()
        for worker in ("observer", "recorder"):
            state = self.adapter.show_unit(units[worker])
            _require(self._unit_running(state),
                     "P1_COORDINATOR_PINNED_WORKER_NOT_RUNNING")
            self._assert_transient_contract(
                units[worker], capability_names=(
                    frozenset({
                        "CAP_DAC_READ_SEARCH", "CAP_SYS_PTRACE",
                        "CAP_NET_ADMIN",
                    })
                    if worker == "observer" else
                    frozenset({"CAP_DAC_READ_SEARCH"})),
                address_families=(
                    frozenset({"AF_UNIX", "AF_NETLINK"})
                    if worker == "observer" else
                    frozenset({"AF_UNIX"})),
                read_write_paths=frozenset({str(self.root)}))
            worker_journal = self._worker_journals.get(worker)
            if worker_journal is None:
                worker_journal = Journal(
                    self.root / f"{worker}-worker-journal",
                    self.runtime["campaign_id"],
                    expected_uid=self.expected_uid,
                    expected_gid=self.expected_gid,
                    wall_clock=self.wall_clock, boot_clock=self.boot_clock)
                self._worker_journals[worker] = worker_journal
            else:
                worker_journal.refresh()
            _require(not worker_journal.failed,
                     "P1_COORDINATOR_WORKER_FAILED_CLOSED")

    def campaign_continuity_complete(self) -> bool:
        """Cross-bind every frozen heartbeat to exactly one checkpoint."""

        reason = "P1_COORDINATOR_CAMPAIGN_CONTINUITY_INCOMPLETE"
        formals = self.runtime["formal_campaigns"]
        origin = int(formals[0]["launcher_dispatch_at_ms"])
        end = int(formals[-1]["teardown_deadline_ms"])
        cadence = int(self.runtime["observer_cadence_ms"])
        final_slot = (end - origin + cadence - 1) // cadence
        observer_journal = self._worker_journals.get("observer")
        recorder_journal = self._worker_journals.get("recorder")
        _require(observer_journal is not None and recorder_journal is not None,
                 reason)
        continuity_entries = [
            item.document for item in observer_journal.entries
            if item.document["event"] == "CAMPAIGN_CONTINUITY_SLOT"
        ]
        _require(
            not any(item["status"] == "MISSED"
                    for item in continuity_entries), reason)
        now_ms = int(self.wall_clock())
        commits = [item for item in continuity_entries
                   if item["status"] == "COMMITTED"]
        if len(commits) < final_slot + 1:
            _require(
                now_ms <= end + self.runtime["maximum_slot_lateness_ms"],
                reason)
            return False
        _require(len(commits) == final_slot + 1, reason)
        runtime_reference = {
            **_reference(self.runtime_snapshot),
            "schema": CAMPAIGN_RUNTIME_SCHEMA,
        }
        raw_snapshots: list[Snapshot] = []
        raw_references: list[dict[str, str]] = []
        transitions: set[str] = set()
        dynamic_fields = (
            "gateway_identity", "gateway_process_identity",
            "tool_socket_identity", "supervisor_socket_identity",
        )
        frozen_fields = (
            "freeze_bundle", "campaign_runtime", "activation_receipt",
            "activation_receipt_document", "gateway_executable_identity",
            "gateway_profile_identity", "gateway_domain_config_identity",
            "custodian_identity", "collector_timer_identity",
            "activation_reconcile_timer_identity", "source_manifest_sha256",
            "policy_sha256", "strategy_sha256",
        )
        previous_raw: dict[str, Any] | None = None
        for expected_slot, item in enumerate(commits):
            details = item["details"]
            scheduled = min(origin + expected_slot * cadence, end)
            _require(
                details.get("first_slot") == expected_slot and
                details.get("last_slot") == expected_slot and
                details.get("scheduled_at_ms") == scheduled and
                details.get("origin_ms") == origin and
                details.get("end_ms") == end and
                details.get("cadence_ms") == cadence and
                details.get("maximum_slot_lateness_ms") ==
                    self.runtime["maximum_slot_lateness_ms"] and
                details.get("final_slot") == final_slot and
                details.get("catch_up") is False,
                reason)
            transition = details.get("transition_fault_id")
            _require(transition is None or
                     (type(transition) is str and
                      IDENTIFIER.fullmatch(transition) is not None), reason)
            raw_reference = _validate_reference(
                details.get("continuity_observation"), reason)
            expected_raw_path = (
                Path(self.runtime["raw_observation_directory"]) /
                "continuity" / f"{expected_slot:08d}.json")
            _require(raw_reference["path"] == str(expected_raw_path), reason)
            raw = _open_reference(
                raw_reference, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid, reason=reason)
            value = _exact(raw.document, CONTINUITY_OBSERVATION_FIELDS, reason)
            _require(
                value.get("schema") ==
                    CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA and
                value.get("version") == VERSION and
                value.get("status") == "COMPLETE" and
                value.get("campaign_id") == self.runtime["campaign_id"] and
                value.get("boot_id") == self.runtime["boot_id"] and
                value.get("clock_id") == "CLOCK_BOOTTIME" and
                value.get("observation_complete") is True and
                value.get("production_mode") ==
                    "PRODUCTION_ROOT_OBSERVER" and
                type(value.get("observed_at_ms")) is int and
                scheduled <= value["observed_at_ms"] <= scheduled +
                    self.runtime["maximum_slot_lateness_ms"] and
                value.get("campaign_runtime") == runtime_reference and
                value.get("continuity_slot_index") == expected_slot and
                value.get("continuity_scheduled_at_ms") == scheduled and
                value.get("continuity_origin_ms") == origin and
                value.get("continuity_end_ms") == end and
                value.get("continuity_cadence_ms") == cadence and
                value.get("continuity_final_slot") == final_slot and
                value.get("continuity_is_final") is
                    (expected_slot == final_slot) and
                value.get("catch_up") is False and
                value.get("transition_fault_id") == transition and
                value.get("persistent_stack_ok") is True and
                value.get("lease_chain_ok") is True and
                value.get("connector_count") == 0 and
                value.get("authorized_uids") == [] and
                value.get("paper_unit_active_count") == 0 and
                value.get("campaign_socket_present") is False and
                value.get("kill_switch_engaged") is True and
                value.get("zero_exposure") is True and
                all(value.get(field) is False for field in BOUNDARY_FIELDS),
                reason)
            if previous_raw is None:
                _require(transition is None, reason)
            else:
                _require(
                    all(value[field] == previous_raw[field]
                        for field in frozen_fields) and
                    type(value.get("observed_boottime_ns")) is int and
                    value["observed_boottime_ns"] >
                        previous_raw["observed_boottime_ns"], reason)
                changed = any(
                    value[field] != previous_raw[field]
                    for field in dynamic_fields)
                _require(changed is (transition is not None), reason)
                if transition is not None:
                    _require(transition not in transitions, reason)
                    transitions.add(transition)
            previous_raw = value
            raw_snapshots.append(raw)
            raw_references.append(raw_reference)

        if transitions:
            fault_results: dict[str, dict[str, Any]] = {}
            for path in self._collect(
                    Path(self.runtime["recorder_root"]) / "fault-results"):
                snapshot = _secure_read(
                    path, expected_uid=self.expected_uid,
                    expected_gid=self.expected_gid,
                    modes=frozenset({0o600}))
                value = _exact(snapshot.document, FAULT_RESULT_FIELDS, reason)
                fault_id = value.get("fault_id")
                _require(
                    value.get("schema") ==
                        "hepta.p1-safety-soak-fault-result.v1" and
                    value.get("version") == VERSION and
                    value.get("campaign_id") == self.runtime["campaign_id"] and
                    type(value.get("sequence")) is int and
                    type(fault_id) is str and
                    IDENTIFIER.fullmatch(fault_id) is not None and
                    fault_id not in fault_results and
                    all(value.get(field) is False
                        for field in BOUNDARY_FIELDS), reason)
                fault_results[fault_id] = value
            for index, raw in enumerate(raw_snapshots):
                transition = raw.document["transition_fault_id"]
                if transition is None:
                    continue
                result = fault_results.get(transition)
                _require(
                    index > 0 and result is not None and
                    result.get("fault_type") == "SERVICE_RESTART" and
                    result.get("target_id") == "watch-execution-gateway" and
                    result.get("recovery_verified") is True and
                    result.get("cleanup_verified") is True and
                    result.get("authority_failure") is False and
                    result.get("audit_failure") is False and
                    result.get("cleanup_failure") is False and
                    type(result.get("injection_boottime_ns")) is int and
                    type(result.get("recovered_boottime_ns")) is int and
                    raw_snapshots[index - 1].document[
                        "observed_boottime_ns"] <=
                        result.get("injection_boottime_ns") <=
                        result.get("recovered_boottime_ns") <=
                        raw.document["observed_boottime_ns"], reason)

        projections = [
            item.document for item in recorder_journal.entries
            if item.document["event"] == "PROJECT_OBSERVATION" and
            item.document["status"] == "COMMITTED" and
            item.document["details"].get("kind") == "continuity"
        ]
        previous_checkpoint_body: str | None = None
        for expected_slot, (raw, raw_reference) in enumerate(zip(
                raw_snapshots, raw_references)):
            matches = [
                item for item in projections
                if item["details"].get("input_file_sha256") ==
                    raw.file_sha256
            ]
            if not matches:
                _require(
                    now_ms <= end + self.runtime["maximum_slot_lateness_ms"] +
                        CONTINUITY_PROJECTION_GUARD_MS,
                    reason)
                return False
            _require(len(matches) == 1, reason)
            outputs = matches[0]["details"].get("outputs")
            _require(isinstance(outputs, list) and len(outputs) == 1, reason)
            output = outputs[0]
            _require(
                isinstance(output, dict) and
                output.get("role") == "continuity_checkpoint" and
                output.get("schema") == CONTINUITY_CHECKPOINT_SCHEMA and
                output.get("sealed") is True,
                reason)
            checkpoint_reference = {
                field: output.get(field) for field in REFERENCE_FIELDS
            }
            checkpoint = _open_reference(
                checkpoint_reference, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid, reason=reason)
            expected_checkpoint_path = (
                Path(self.runtime["recorder_root"]) / "checkpoints" /
                f"{expected_slot:08d}.json")
            value = _exact(
                checkpoint.document, CONTINUITY_CHECKPOINT_FIELDS, reason)
            expected_observer_reference = {
                **raw_reference,
                "schema": CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA,
            }
            _require(
                checkpoint.path == expected_checkpoint_path and
                output.get("path") == str(expected_checkpoint_path) and
                output.get("file_sha256") == checkpoint.file_sha256 and
                output.get("body_sha256") == checkpoint.body_sha256 and
                value.get("schema") == CONTINUITY_CHECKPOINT_SCHEMA and
                value.get("version") == VERSION and
                value.get("campaign_id") == self.runtime["campaign_id"] and
                value.get("sequence") == expected_slot and
                value.get("previous_checkpoint_body_sha256") ==
                    previous_checkpoint_body and
                value.get("observer_receipt") ==
                    expected_observer_reference and
                all(value[field] == raw.document[field]
                    for field in CONTINUITY_CHECKPOINT_COPY_FIELDS),
                reason)
            previous_checkpoint_body = checkpoint.body_sha256

        _require(
            len(projections) == final_slot + 1 and
            raw_snapshots[0].document["continuity_scheduled_at_ms"] == origin and
            raw_snapshots[-1].document["continuity_scheduled_at_ms"] == end and
            raw_snapshots[-1].document["continuity_is_final"] is True,
            reason)
        return True

    def produce_pins(self, observer_argv_path: Path,
                     recorder_argv_path: Path) -> Path:
        output = self.root / "fault-injector-pins.json"
        event = "PRODUCE_FAULT_PINS"
        if self._has_commit(event):
            _require(output.exists(), "P1_COORDINATOR_FAULT_PINS_MISSING")
            return output
        if self._has_intent(event):
            if output.exists():
                snapshot = _secure_read(
                    output, expected_uid=self.expected_uid,
                    expected_gid=self.expected_gid,
                    modes=frozenset({0o600}))
                self._append(event, "COMMITTED", {
                    "path": str(output), "file_sha256": snapshot.file_sha256,
                    "body_sha256": snapshot.body_sha256,
                })
                return output
            raise CoordinatorError("P1_COORDINATOR_PIN_PUBLICATION_AMBIGUOUS")
        spec = _open_reference(
            self.runtime["campaign_spec"], expected_uid=self.expected_uid,
            expected_gid=self.expected_gid,
            reason="P1_COORDINATOR_SPEC_DRIFT")
        plan = _open_reference(
            self.runtime["fault_plan"], expected_uid=self.expected_uid,
            expected_gid=self.expected_gid,
            reason="P1_COORDINATOR_PLAN_DRIFT")
        observer_argv_file_sha = _argv_file_sha256(
            observer_argv_path, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        recorder_argv_file_sha = _argv_file_sha256(
            recorder_argv_path, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        units = self._unit_names()
        argv = [
            PIN_PRODUCER, "--run",
            "--campaign-spec", str(spec.path),
            "--fault-plan", str(plan.path),
            "--freeze-bundle", str(self.bundle_snapshot.path),
            "--runtime-manifest", str(self.runtime_snapshot.path),
            "--formal-campaign-id", self.runtime["pin_formal_campaign_id"],
            "--observer-unit", units["observer"],
            "--recorder-unit", units["recorder"],
            "--observer-exec-argv-json", str(observer_argv_path),
            "--recorder-exec-argv-json", str(recorder_argv_path),
            "--expected-observer-argv-file-sha256",
            observer_argv_file_sha,
            "--expected-recorder-argv-file-sha256",
            recorder_argv_file_sha,
            "--expected-spec-file-sha256", spec.file_sha256,
            "--expected-spec-body-sha256", spec.body_sha256,
            "--expected-plan-file-sha256", plan.file_sha256,
            "--expected-plan-body-sha256", plan.body_sha256,
            "--expected-freeze-file-sha256", self.bundle_snapshot.file_sha256,
            "--expected-freeze-body-sha256", self.bundle_snapshot.body_sha256,
            "--expected-runtime-file-sha256", self.runtime_snapshot.file_sha256,
            "--expected-runtime-body-sha256", self.runtime_snapshot.body_sha256,
            "--expected-source-manifest-sha256",
            spec.document["source_manifest_sha256"],
            "--boot-id", self.runtime["boot_id"],
            "--output", str(output),
        ]
        self._append(event, "INTENT", {
            "output": str(output), "argv_sha256":
                digest_bytes(canonical_bytes(argv)),
        })
        result = self._run_pinned("fault_pin_producer", argv, 60)
        _require(result.returncode == 0 and output.exists(),
                 "P1_COORDINATOR_PIN_PRODUCER_FAILED")
        snapshot = _secure_read(
            output, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid, modes=frozenset({0o600}))
        self._append(event, "COMMITTED", {
            "path": str(output), "file_sha256": snapshot.file_sha256,
            "body_sha256": snapshot.body_sha256,
        })
        return output

    def ensure_injector(self, pins_path: Path) -> None:
        event = "START_FAULT_INJECTOR"
        unit = self._unit_names()["injector"]
        spec = _open_reference(
            self.runtime["campaign_spec"], expected_uid=self.expected_uid,
            expected_gid=self.expected_gid,
            reason="P1_COORDINATOR_SPEC_DRIFT")
        plan = _open_reference(
            self.runtime["fault_plan"], expected_uid=self.expected_uid,
            expected_gid=self.expected_gid,
            reason="P1_COORDINATOR_PLAN_DRIFT")
        argv = [
            INJECTOR, "--run", "--campaign-spec", str(spec.path),
            "--fault-plan", str(plan.path), "--pins", str(pins_path),
            "--campaign-id", self.runtime["campaign_id"],
            "--formal-campaign-id", self.runtime["pin_formal_campaign_id"],
            "--boot-id", self.runtime["boot_id"],
            "--source-manifest-sha256",
            spec.document["source_manifest_sha256"],
            "--campaign-spec-body-sha256", spec.body_sha256,
            "--fault-plan-body-sha256", plan.body_sha256,
        ]
        if self._has_commit(event):
            state = self.adapter.show_unit(unit)
            _require(
                state.get("LoadState") == "loaded" and
                state.get("ActiveState") in {"active", "inactive"},
                "P1_COORDINATOR_INJECTOR_IDENTITY_LOST")
            self._assert_transient_contract(
                unit, capability_names=frozenset({
                    "CAP_KILL", "CAP_DAC_READ_SEARCH", "CAP_SYS_PTRACE",
                    "CAP_NET_ADMIN"}),
                address_families=frozenset({"AF_UNIX", "AF_NETLINK"}),
                read_write_paths=frozenset({
                    str(self.root), "/run/hepta-p1-fault-fixture"}))
            return
        state = self.adapter.show_unit(unit)
        if not self._has_intent(event):
            self._append(event, "INTENT", {
                "unit": unit,
                "exec_argv_sha256": digest_bytes(canonical_bytes(argv)),
            })
        if state.get("LoadState") == "not-found":
            self._verify_executable("root_fault_injector")
            properties = [
                "Type=exec", "User=root", "Group=root", "Restart=no",
                "TimeoutStartSec=infinity", "TimeoutStopSec=360s",
                "KillMode=mixed", "UMask=0077",
                *TRANSIENT_HARDENING_PROPERTIES,
                "RestrictAddressFamilies=AF_UNIX AF_NETLINK",
                "CapabilityBoundingSet=CAP_KILL CAP_DAC_READ_SEARCH CAP_SYS_PTRACE CAP_NET_ADMIN",
                f"ReadWritePaths={self.root} /run/hepta-p1-fault-fixture",
                *self._ownership_properties(),
            ]
            self.adapter.start_transient(unit, argv, properties, {})
        elif state.get("ActiveState") == "failed":
            raise CoordinatorError("P1_COORDINATOR_INJECTOR_FAILED")
        state = self.adapter.show_unit(unit)
        _require(
            state.get("LoadState") == "loaded" and
            state.get("ActiveState") in {"active", "inactive"},
            "P1_COORDINATOR_INJECTOR_START_FAILED")
        self._assert_transient_contract(
            unit, capability_names=frozenset({
                "CAP_KILL", "CAP_DAC_READ_SEARCH", "CAP_SYS_PTRACE",
                "CAP_NET_ADMIN"}),
            address_families=frozenset({"AF_UNIX", "AF_NETLINK"}),
            read_write_paths=frozenset({
                str(self.root), "/run/hepta-p1-fault-fixture"}))
        self._append(event, "COMMITTED", {
            "unit": unit,
            "exec_argv_sha256": digest_bytes(canonical_bytes(argv)),
            "fragment_path": state.get("FragmentPath"),
            "invocation_id": state.get("InvocationID"),
        })

    def _launcher_unit(self, formal_id: str) -> str:
        match = FORMAL_ID.fullmatch(formal_id)
        _require(match is not None, "P1_COORDINATOR_FORMAL_ID_INVALID")
        return f"hepta-p1-safety-soak-launcher-round{match.group(1)}.service"

    def launch_formal_if_due(self, formal: Mapping[str, Any]) -> str:
        formal_id = str(formal["formal_campaign_id"])
        event = "LAUNCH_FORMAL"
        commits = self._events(event, formal_campaign_id=formal_id)
        if any(item["status"] == "COMMITTED" for item in commits):
            return "COMPLETE"
        now_ms = int(self.wall_clock())
        start_ms = int(formal["launcher_start_ms"])
        dispatch_at_ms = int(formal["launcher_dispatch_at_ms"])
        latest_dispatch_ms = (
            dispatch_at_ms + LAUNCHER_DISPATCH_TOLERANCE_MS)
        if not self._has_intent(event, formal_campaign_id=formal_id):
            if now_ms < dispatch_at_ms:
                return "WAITING"
            if now_ms > latest_dispatch_ms:
                self._append(event, "MISSED", {
                    "formal_campaign_id": formal_id,
                    "launcher_start_ms": start_ms,
                    "dispatch_at_ms": dispatch_at_ms,
                    "latest_dispatch_ms": latest_dispatch_ms,
                    "observed_at_ms": now_ms, "catch_up": False,
                })
                raise CoordinatorError("P1_COORDINATOR_LAUNCH_SLOT_MISSED")
            unit = self._launcher_unit(formal_id)
            argv = [
                LAUNCHER, "--probe-campaign-id", formal["probe_campaign_id"],
                "--formal-campaign-id", formal_id,
                "--formal-start-ms", str(start_ms),
            ]
            self._verify_executable("shadow_admission_launcher")
            self._append(event, "INTENT", {
                "formal_campaign_id": formal_id, "unit": unit,
                "launcher_start_ms": start_ms,
                "formal_valid_after_ms": formal["valid_after_ms"],
                "dispatch_at_ms": dispatch_at_ms,
                "latest_dispatch_ms": latest_dispatch_ms,
                "observed_at_ms": now_ms,
                "exec_argv_sha256": digest_bytes(canonical_bytes(argv)),
            })
            self.adapter.start_transient(unit, argv, [
                "Type=exec", "User=root", "Group=root", "Restart=no",
                "TimeoutStartSec=infinity", "TimeoutStopSec=360s",
                "KillMode=mixed", "UMask=0077",
                *TRANSIENT_HARDENING_PROPERTIES,
                "RestrictAddressFamilies=AF_UNIX AF_NETLINK",
                "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH CAP_FOWNER CAP_KILL CAP_SYS_PTRACE CAP_NET_ADMIN",
                "ReadWritePaths=/var/lib/hepta/p1-admission /var/lib/hepta/.shadow-runtime-install.lock",
                *self._ownership_properties(),
            ], {})
            self._assert_transient_contract(
                unit, capability_names=frozenset({
                    "CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_DAC_READ_SEARCH",
                    "CAP_FOWNER", "CAP_KILL", "CAP_SYS_PTRACE",
                    "CAP_NET_ADMIN"}),
                address_families=frozenset({"AF_UNIX", "AF_NETLINK"}),
                read_write_paths=frozenset({
                    "/var/lib/hepta/p1-admission",
                    "/var/lib/hepta/.shadow-runtime-install.lock"}))
            return "RUNNING"
        intent = self._events(event, formal_campaign_id=formal_id)[-1]
        unit = str(intent["details"]["unit"])
        state = self.adapter.show_unit(unit)
        if state.get("ActiveState") in {"active", "activating"}:
            self._assert_transient_contract(
                unit, capability_names=frozenset({
                    "CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_DAC_READ_SEARCH",
                    "CAP_FOWNER", "CAP_KILL", "CAP_SYS_PTRACE",
                    "CAP_NET_ADMIN"}),
                address_families=frozenset({"AF_UNIX", "AF_NETLINK"}),
                read_write_paths=frozenset({
                    "/var/lib/hepta/p1-admission",
                    "/var/lib/hepta/.shadow-runtime-install.lock"}))
            return "RUNNING"
        self._assert_transient_contract(
            unit, capability_names=frozenset({
                "CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_DAC_READ_SEARCH",
                "CAP_FOWNER", "CAP_KILL", "CAP_SYS_PTRACE",
                "CAP_NET_ADMIN"}),
            address_families=frozenset({"AF_UNIX", "AF_NETLINK"}),
            read_write_paths=frozenset({
                "/var/lib/hepta/p1-admission",
                "/var/lib/hepta/.shadow-runtime-install.lock"}))
        if (state.get("LoadState") != "loaded" or
                state.get("Result") not in {"success", ""} or
                state.get("ExecMainStatus") not in {"0", ""}):
            raise CoordinatorError("P1_COORDINATOR_LAUNCHER_FAILED")
        receipt = _secure_read(
            Path(formal["launcher_receipt_path"]),
            expected_uid=self.expected_uid, expected_gid=self.expected_gid,
            modes=frozenset({0o600}))
        closure = _secure_read(
            Path(formal["verified_closure_path"]),
            expected_uid=self.expected_uid, expected_gid=self.expected_gid,
            modes=frozenset({0o600}))
        _require(
            receipt.document.get("paper_authorized") is False and
            receipt.document.get("live_authorized") is False and
            closure.document.get("paper_authorized") is False and
            closure.document.get("live_authorized") is False,
            "P1_COORDINATOR_LAUNCHER_AUTHORITY_DRIFT")
        self._append(event, "COMMITTED", {
            "formal_campaign_id": formal_id, "unit": unit,
            "launcher_receipt": _reference(receipt),
            "verified_closure": _reference(closure),
        })
        return "COMPLETE"

    def request_projection(self, formal: Mapping[str, Any]) -> dict[str, Any]:
        formal_id = str(formal["formal_campaign_id"])
        event = "REQUEST_PROJECTION"
        commits = self._events(event, formal_campaign_id=formal_id)
        committed = next((item for item in commits
                          if item["status"] == "COMMITTED"), None)
        if committed is not None:
            request_id = committed["details"]["request_id"]
            ack = self.control.ack("recorder", request_id)
            _require(ack is not None and ack.get("status") == "COMPLETE",
                     "P1_COORDINATOR_PROJECTION_ACK_DRIFT")
            return ack
        closure = _secure_read(
            Path(formal["verified_closure_path"]),
            expected_uid=self.expected_uid, expected_gid=self.expected_gid,
            modes=frozenset({0o600}))
        request = self.control.publish(
            "recorder", "PROJECT_DECISIONS", {
                "formal_campaign_id": formal_id,
                "verified_closure": _reference(closure),
                "artifact_root": formal["artifact_root"],
            }, now_ms=int(self.wall_clock()),
            # The service receipt is historical clock evidence only; the
            # recorder must consume it before this frozen projection bound.
            deadline_ms=int(formal["projection_deadline_ms"]))
        self._append(event, "COMMITTED", {
            "formal_campaign_id": formal_id,
            "request_id": request["request_id"],
        })
        return request

    def projection_complete(self, formal: Mapping[str, Any]) -> bool:
        values = self._events(
            "REQUEST_PROJECTION",
            formal_campaign_id=formal["formal_campaign_id"])
        committed = next((item for item in values
                          if item["status"] == "COMMITTED"), None)
        if committed is None:
            return False
        ack = self.control.ack("recorder", committed["details"]["request_id"])
        if ack is None:
            return False
        _require(ack.get("status") == "COMPLETE",
                 "P1_COORDINATOR_PROJECTION_FAILED")
        return True

    def request_launcher_cleanup(self, formal: Mapping[str, Any]) -> str:
        formal_id = str(formal["formal_campaign_id"])
        event = "REQUEST_LAUNCHER_CLEANUP"
        committed = next((
            item for item in self._events(
                event, formal_campaign_id=formal_id)
            if item["status"] == "COMMITTED"), None)
        if committed is not None:
            return str(committed["details"]["request_id"])
        request = self.control.publish(
            "observer", "CLEANUP", {
                "subject_type": "LAUNCHER", "subject_id": formal_id,
                "formal_campaign_id": formal_id,
                "fault_injection_receipt": None,
            }, now_ms=int(self.wall_clock()),
            deadline_ms=int(formal["teardown_deadline_ms"]))
        self._append(event, "COMMITTED", {
            "formal_campaign_id": formal_id,
            "request_id": request["request_id"],
        })
        return str(request["request_id"])

    def launcher_cleanup_complete(self, formal: Mapping[str, Any]) -> bool:
        committed = next((
            item for item in self._events(
                "REQUEST_LAUNCHER_CLEANUP",
                formal_campaign_id=formal["formal_campaign_id"])
            if item["status"] == "COMMITTED"), None)
        if committed is None:
            return False
        ack = self.control.ack(
            "observer", committed["details"]["request_id"])
        if ack is None:
            return False
        _require(ack.get("status") == "COMPLETE",
                 "P1_COORDINATOR_LAUNCHER_CLEANUP_FAILED")
        return True

    def request_final_cleanup(self) -> str:
        event = "REQUEST_FINAL_CLEANUP"
        existing = self._events(event)
        committed = next((item for item in existing
                          if item["status"] == "COMMITTED"), None)
        if committed is not None:
            return str(committed["details"]["request_id"])
        request = self.control.publish(
            "observer", "CLEANUP", {
                "subject_type": "FINAL",
                "subject_id": self.runtime["campaign_id"],
                "formal_campaign_id": None,
                "fault_injection_receipt": None,
            }, now_ms=int(self.wall_clock()),
            deadline_ms=self.runtime["expires_at_ms"])
        self._append(event, "COMMITTED", {
            "request_id": request["request_id"]})
        return str(request["request_id"])

    def final_cleanup_complete(self, observer_request_id: str) -> bool:
        observer_ack = self.control.ack("observer", observer_request_id)
        if observer_ack is None:
            return False
        _require(observer_ack.get("status") == "COMPLETE",
                 "P1_COORDINATOR_FINAL_CLEANUP_OBSERVER_FAILED")
        event = "REQUEST_RECORDER_DRAIN"
        committed = next((item for item in self._events(event)
                          if item["status"] == "COMMITTED"), None)
        if committed is None:
            request = self.control.publish(
                "recorder", "DRAIN", {
                    "required_observer_request_id": observer_request_id,
                    "required_output": observer_ack["outputs"][0],
                }, now_ms=int(self.wall_clock()),
                deadline_ms=self.runtime["expires_at_ms"])
            self._append(event, "COMMITTED", {
                "request_id": request["request_id"]})
            return False
        ack = self.control.ack("recorder", committed["details"]["request_id"])
        if ack is None:
            return False
        _require(ack.get("status") == "COMPLETE",
                 "P1_COORDINATOR_FINAL_CLEANUP_RECORDER_FAILED")
        return True

    def injector_complete(self) -> bool:
        unit = self._unit_names()["injector"]
        state = self.adapter.show_unit(unit)
        if state.get("ActiveState") in {"active", "activating"}:
            return False
        _require(
            state.get("LoadState") == "loaded" and
            state.get("Result") in {"success", ""} and
            state.get("ExecMainStatus") in {"0", ""},
            "P1_COORDINATOR_INJECTOR_FAILED")
        output = Path(self.runtime["injector_output_directory"])
        names = sorted(item.name for item in output.iterdir()
                       if not item.name.startswith("."))
        _require(len(names) == 7,
                 "P1_COORDINATOR_INJECTOR_RECEIPT_SET_INCOMPLETE")
        return True

    def _collect(self, directory: Path) -> list[Path]:
        if not directory.exists():
            return []
        result: list[Path] = []
        for item in sorted(directory.iterdir()):
            metadata = item.lstat()
            _require(not stat.S_ISLNK(metadata.st_mode),
                     "P1_COORDINATOR_AUDIT_INPUT_UNTRUSTED")
            if stat.S_ISREG(metadata.st_mode) and item.suffix == ".json":
                result.append(item)
        return result

    def run_audit(self) -> Path:
        output = self.root / "final-audit-receipt.json"
        event = "RUN_FINAL_AUDIT"
        spec_snapshot = _open_reference(
            self.runtime["campaign_spec"], expected_uid=self.expected_uid,
            expected_gid=self.expected_gid,
            reason="P1_COORDINATOR_SPEC_DRIFT")
        if self._has_commit(event):
            _require(output.exists(), "P1_COORDINATOR_AUDIT_RECEIPT_MISSING")
            snapshot = _secure_read(
                output, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid, modes=frozenset({0o600}))
            validate_final_audit(
                snapshot, self.runtime_snapshot, self.bundle_snapshot,
                spec_snapshot)
            return output
        if self._has_intent(event):
            if output.exists():
                snapshot = _secure_read(
                    output, expected_uid=self.expected_uid,
                    expected_gid=self.expected_gid,
                    modes=frozenset({0o600}))
                validate_final_audit(
                    snapshot, self.runtime_snapshot, self.bundle_snapshot,
                    spec_snapshot)
                self._append(event, "COMMITTED", {
                    "receipt": _reference(snapshot)})
                return output
            raise CoordinatorError("P1_COORDINATOR_AUDIT_AMBIGUOUS")
        spec = spec_snapshot.path
        plan = Path(self.runtime["fault_plan"]["path"])
        argv = [
            AUDITOR, "--run", "--campaign-spec", str(spec),
            "--campaign-runtime", str(self.runtime_snapshot.path),
        ]
        for formal in self.runtime["formal_campaigns"]:
            argv += ["--launcher-receipt", formal["launcher_receipt_path"]]
            argv += ["--verified-closure", formal["verified_closure_path"]]
        recorder_root = Path(self.runtime["recorder_root"])
        for path in self._collect(recorder_root / "decisions"):
            argv += ["--decision-receipt", str(path)]
        for path in self._collect(recorder_root / "checkpoints"):
            argv += ["--continuity-checkpoint", str(path)]
        argv += ["--fault-plan", str(plan)]
        for path in self._collect(recorder_root / "fault-results"):
            argv += ["--fault-result", str(path)]
        for path in self._collect(recorder_root / "authority-snapshots"):
            argv += ["--authority-snapshot", str(path)]
        for path in self._collect(recorder_root / "cleanup-snapshots"):
            argv += ["--cleanup-snapshot", str(path)]
        raw = Path(self.runtime["raw_observation_directory"])
        for child in (
            "continuity", "service", "authority", "fault", "cleanup",
        ):
            for path in self._collect(raw / child):
                argv += ["--observer-receipt", str(path)]
        for path in self._collect(Path(self.runtime["injector_output_directory"])):
            argv += ["--fault-injection-receipt", str(path)]
        argv += ["--output", str(output)]
        self._append(event, "INTENT", {
            "output": str(output),
            "argv_sha256": digest_bytes(canonical_bytes(argv)),
        })
        result = self._run_pinned("auditor", argv, 1800)
        _require(result.returncode == 0 and output.exists(),
                 "P1_COORDINATOR_AUDITOR_FAILED")
        snapshot = _secure_read(
            output, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid, modes=frozenset({0o600}))
        validate_final_audit(
            snapshot, self.runtime_snapshot, self.bundle_snapshot,
            spec_snapshot)
        self._append(event, "COMMITTED", {"receipt": _reference(snapshot)})
        return output

    def terminal_receipt(
        self, audit_path: Path, owned_unit_closure: Mapping[str, Any],
    ) -> Path:
        output = self.root / "terminal-receipt.json"
        audit = _secure_read(
            audit_path, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid, modes=frozenset({0o600}))
        spec_snapshot = _open_reference(
            self.runtime["campaign_spec"], expected_uid=self.expected_uid,
            expected_gid=self.expected_gid,
            reason="P1_COORDINATOR_SPEC_DRIFT")
        validate_final_audit(
            audit, self.runtime_snapshot, self.bundle_snapshot, spec_snapshot)
        _require(
            owned_unit_closure.get("schema") == JOURNAL_SCHEMA and
            owned_unit_closure.get("event") == "CLOSE_OWNED_UNITS" and
            owned_unit_closure.get("status") == "COMMITTED" and
            owned_unit_closure.get("details", {}).get("all_inactive") is True and
            owned_unit_closure.get("details", {}).get("residue_absent") is True,
            "P1_COORDINATOR_OWNED_UNIT_CLOSURE_INVALID")
        terminal_boundary = next((
            item for item in reversed(self.journal.entries)
            if item.document["event"] == "ASSERT_EXECUTION_BOUNDARY_TERMINAL" and
            item.document["status"] == "COMMITTED"), None)
        _require(terminal_boundary is not None,
                 "P1_COORDINATOR_TERMINAL_BOUNDARY_MISSING")
        decisions = self._collect(Path(self.runtime["recorder_root"]) /
                                  "decisions")
        terminal_events = [
            item.document for item in self.journal.entries
            if item.document["event"] == "TERMINAL"
        ]
        _require(
            len(terminal_events) <= 2 and
            (not terminal_events or terminal_events[0]["status"] == "INTENT") and
            (len(terminal_events) < 2 or
             terminal_events[1]["status"] == "COMMITTED") and
            (not terminal_events or
             terminal_events[-1]["sequence"] ==
                self.journal.entries[-1].document["sequence"]),
            "P1_COORDINATOR_TERMINAL_JOURNAL_INVALID")
        completed_at_ms = (
            _integer(
                terminal_events[0]["details"].get("completed_at_ms"),
                "P1_COORDINATOR_TERMINAL_INTENT_DRIFT")
            if terminal_events else int(self.wall_clock()))
        document = seal({
            "schema": TERMINAL_SCHEMA, "version": VERSION,
            "status": "COMPLETE_NON_AUTHORIZING",
            "campaign_id": self.runtime["campaign_id"],
            "completed_at_ms": completed_at_ms,
            "freeze_bundle": _reference(self.bundle_snapshot),
            "audit_receipt": _reference(audit),
            "owned_unit_closure_body_sha256":
                owned_unit_closure["body_sha256"],
            "owned_unit_states_sha256":
                owned_unit_closure["details"]["states_sha256"],
            "terminal_execution_boundary_body_sha256":
                terminal_boundary.body_sha256,
            "eligible_decision_receipt_count": len(decisions),
            "fault_receipt_count": len(self._collect(
                Path(self.runtime["injector_output_directory"]))),
            "kill_switch_required_engaged": True,
            "paper_handoff_authorized": False,
            "live_handoff_authorized": False,
            "mutation_handoff_authorized": False,
            **boundary(),
        })
        _require(len(decisions) >= MINIMUM_ELIGIBLE_DECISIONS,
                 "P1_COORDINATOR_ELIGIBLE_DECISIONS_INCOMPLETE")
        intent_details = {
            "path": str(output), "body_sha256": document["body_sha256"],
            "completed_at_ms": completed_at_ms,
            "audit_receipt": _reference(audit),
            "owned_unit_closure_body_sha256":
                owned_unit_closure["body_sha256"],
            "terminal_execution_boundary_body_sha256":
                terminal_boundary.body_sha256,
            "handoff_authorized": False,
        }
        if not terminal_events:
            self._append("TERMINAL", "INTENT", intent_details)
            terminal_events = [self.journal.entries[-1].document]
        else:
            _require(terminal_events[0]["details"] == intent_details,
                     "P1_COORDINATOR_TERMINAL_INTENT_DRIFT")
        if output.exists():
            snapshot = _secure_read(
                output, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid, modes=frozenset({0o600}))
            _require(snapshot.payload == canonical_bytes(document),
                     "P1_COORDINATOR_TERMINAL_DRIFT")
        else:
            snapshot = publish_noreplace(
                output, document, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid)
        commit_details = {
            "path": str(output), "file_sha256": snapshot.file_sha256,
            "body_sha256": snapshot.body_sha256,
            "intent_body_sha256": self.journal.entries[
                terminal_events[0]["sequence"]].body_sha256,
            "handoff_authorized": False,
        }
        if len(terminal_events) == 1:
            self._append("TERMINAL", "COMMITTED", commit_details)
        else:
            _require(terminal_events[1]["details"] == commit_details,
                     "P1_COORDINATOR_TERMINAL_COMMIT_DRIFT")
        return output

    def recover_terminal_receipt(self) -> Path | None:
        """Finish or verify the non-authorizing terminal commit decision."""

        terminal = [item.document for item in self.journal.entries
                    if item.document["event"] == "TERMINAL"]
        if not terminal:
            return None
        _require(
            terminal[0]["status"] == "INTENT" and
            terminal[-1]["status"] in {"INTENT", "COMMITTED"} and
            terminal[-1]["sequence"] ==
                self.journal.entries[-1].document["sequence"],
            "P1_COORDINATOR_TERMINAL_RECOVERY_INVALID")
        closure = next((
            item.document for item in reversed(self.journal.entries)
            if item.document["event"] == "CLOSE_OWNED_UNITS" and
            item.document["status"] == "COMMITTED"
        ), None)
        _require(closure is not None,
                 "P1_COORDINATOR_TERMINAL_RECOVERY_CLOSURE_MISSING")
        audit = self.root / "final-audit-receipt.json"
        _require(audit.exists(),
                 "P1_COORDINATOR_TERMINAL_RECOVERY_AUDIT_MISSING")
        return self.terminal_receipt(audit, closure)

    def _pending_failure_reason(self) -> str | None:
        failures = self._events("CAMPAIGN_FAILURE")
        if not failures:
            return None
        _require(
            len(failures) == 1 and failures[0]["status"] == "INTENT" and
            set(failures[0]["details"]) == {
                "reason", "handoff_authorized"} and
            type(failures[0]["details"].get("reason")) is str and
            bool(failures[0]["details"]["reason"]) and
            failures[0]["details"].get("handoff_authorized") is False,
            "P1_COORDINATOR_FAILURE_INTENT_INVALID")
        return str(failures[0]["details"]["reason"])

    def recover_failure_intent(self) -> str | None:
        """Finish a durable failure decision before any normal transition."""

        reason = self._pending_failure_reason()
        if reason is None:
            return None
        self.fail_closed(reason)
        return reason

    def fail_closed(self, reason: str) -> None:
        cleanup_error: str | None = None
        closure_sha: str | None = None
        if not self.journal.failed:
            pending_reason = self._pending_failure_reason()
            if pending_reason is None:
                self._append("CAMPAIGN_FAILURE", "INTENT", {
                    "reason": reason, "handoff_authorized": False})
            else:
                _require(pending_reason == reason,
                         "P1_COORDINATOR_FAILURE_INTENT_DRIFT")
            try:
                closure = self.close_owned_units()
                closure_sha = closure["body_sha256"]
            except Exception as error:  # terminal failure must still be sealed
                cleanup_error = (
                    error.reason if isinstance(error, CoordinatorError) else
                    type(error).__name__)
            self._append("CAMPAIGN", "FAILED_CLOSED", {
                "reason": reason, "catch_up": False,
                "handoff_authorized": False,
                "owned_unit_closure_body_sha256": closure_sha,
                "cleanup_error": cleanup_error,
            })
            return
        # A restarted already-failed campaign still attempts bounded residue
        # removal, but can never append after or replace its terminal receipt.
        for unit in self.owned_units():
            try:
                state = self.adapter.show_unit(unit)
                if state.get("ActiveState") not in {"inactive", "failed"}:
                    self.adapter.stop_unit(unit)
            except Exception:
                continue


def _ensure_layout(root: Path, uid: int, gid: int) -> None:
    base = root.parent
    _trusted_directory(base, expected_uid=uid, expected_gid=gid)
    _trusted_directory(root, expected_uid=uid, expected_gid=gid, create=True)
    names = (
        "coordinator-journal", "recorder", "raw-observations",
        "injector-journal", "injection-receipts", "control",
    )
    for name in names:
        _trusted_directory(
            root / name, expected_uid=uid, expected_gid=gid, create=True)
    for name in ("continuity", "service", "authority", "fault", "cleanup"):
        _trusted_directory(
            root / "raw-observations" / name,
            expected_uid=uid, expected_gid=gid, create=True)
    for name in (
        "observer-requests", "observer-acks", "recorder-requests",
        "recorder-acks",
    ):
        _trusted_directory(
            root / "control" / name,
            expected_uid=uid, expected_gid=gid, create=True)


def _run_recorder_freeze(
    contract: Mapping[str, Any], bundle: Snapshot, adapter: ExecutionAdapter,
    executables: Mapping[str, Any], *, expected_uid: int, expected_gid: int,
) -> tuple[Snapshot, Snapshot]:
    root = Path(str(contract["state_root"])) / "recorder"
    spec_path = root / "campaign-spec.json"
    plan_path = root / "fault-plan.json"
    if spec_path.exists() and plan_path.exists():
        return (
            _secure_read(spec_path, expected_uid=expected_uid,
                         expected_gid=expected_gid, modes=frozenset({0o600})),
            _secure_read(plan_path, expected_uid=expected_uid,
                         expected_gid=expected_gid, modes=frozenset({0o600})),
        )
    value = bundle.document
    anchors = value["anchors"]
    argv = [RECORDER, "--run", "freeze", "--root", str(root)]
    for role, option in (
        ("source_anchor", "--source-anchor"),
        ("policy_anchor", "--policy-anchor"),
        ("strategy_anchor", "--strategy-anchor"),
    ):
        argv += [option, anchors[role]["path"]]
    for item in value["formal_policies"]:
        argv += ["--formal-policy", item["path"]]
    argv += [
        "--schedule", anchors["frozen_schedule"]["path"],
        "--fault-schedule", anchors["frozen_fault_schedule"]["path"],
        "--freeze-bundle", str(bundle.path),
    ]
    executable = executables["evidence_recorder"]
    _secure_executable(
        Path(executable["path"]), executable["file_sha256"],
        expected_uid=expected_uid, expected_gid=expected_gid)
    result = adapter.run(argv, 180)
    _require(result.returncode == 0 and spec_path.exists() and plan_path.exists(),
             "P1_COORDINATOR_RECORDER_FREEZE_FAILED")
    _secure_executable(
        Path(executable["path"]), executable["file_sha256"],
        expected_uid=expected_uid, expected_gid=expected_gid)
    spec = _secure_read(
        spec_path, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({0o600}))
    plan = _secure_read(
        plan_path, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({0o600}))
    _require(
        spec.document.get("schema") == SPEC_SCHEMA and
        plan.document.get("schema") == PLAN_SCHEMA and
        spec.document.get("campaign_id") == value["campaign_id"] and
        plan.document.get("campaign_id") == value["campaign_id"] and
        spec.document.get("fault_plan_body_sha256") == plan.body_sha256,
        "P1_COORDINATOR_RECORDER_FREEZE_INVALID")
    return spec, plan


def _publish_or_open_runtime(
    path: Path, document: dict[str, Any], *, expected_uid: int,
    expected_gid: int,
) -> Snapshot:
    if path.exists():
        snapshot = _secure_read(
            path, expected_uid=expected_uid, expected_gid=expected_gid,
            modes=frozenset({0o600}))
        _require(snapshot.payload == canonical_bytes(document),
                 "P1_COORDINATOR_RUNTIME_DRIFT")
        return snapshot
    return publish_noreplace(
        path, document, expected_uid=expected_uid, expected_gid=expected_gid)


def _open_runtime_for_terminal_recovery(
    path: Path,
    contract: Mapping[str, Any],
    bundle: Snapshot,
    *,
    expected_uid: int,
    expected_gid: int,
) -> Snapshot:
    """Reconstruct and bind a persisted runtime without invoking producers."""

    runtime = _secure_read(
        path, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({0o600}))
    _validate_runtime(runtime.document)
    spec = _open_reference(
        runtime.document["campaign_spec"], expected_uid=expected_uid,
        expected_gid=expected_gid,
        reason="P1_COORDINATOR_TERMINAL_RECOVERY_SPEC_DRIFT")
    plan = _open_reference(
        runtime.document["fault_plan"], expected_uid=expected_uid,
        expected_gid=expected_gid,
        reason="P1_COORDINATOR_TERMINAL_RECOVERY_PLAN_DRIFT")
    expected = _make_runtime(contract, bundle, spec, plan)
    _require(
        runtime.payload == canonical_bytes(expected),
        "P1_COORDINATOR_TERMINAL_RECOVERY_RUNTIME_DRIFT")
    return runtime


def _publish_or_open_argv(
    path: Path, argv: Sequence[str], *, expected_uid: int, expected_gid: int,
) -> None:
    expected = canonical_bytes(list(argv))
    if path.exists():
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_CLOEXEC
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            payload = os.read(descriptor, MAXIMUM_COMMAND_BYTES + 1)
        finally:
            os.close(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_uid == expected_uid and
            metadata.st_gid == expected_gid and
            stat.S_IMODE(metadata.st_mode) == 0o600 and payload == expected,
            "P1_COORDINATOR_ARGV_DRIFT")
        return
    publish_json_array(
        path, argv, expected_uid=expected_uid, expected_gid=expected_gid)


def _bind_installed_image(bundle: Mapping[str, Any]) -> None:
    reason = "P1_COORDINATOR_INSTALLED_IMAGE_REQUIRED"
    try:
        executing = Path(__file__).resolve(strict=True)
        _require(
            executing == INSTALLED_EXECUTABLE and
            os.path.samefile(executing, INSTALLED_EXECUTABLE), reason)
    except OSError as error:
        raise CoordinatorError(reason) from error
    pins = {item["role"]: item for item in bundle["source_producer_pins"]}
    _require("campaign_coordinator" in pins and
             pins["campaign_coordinator"]["installed_path"] ==
             str(INSTALLED_EXECUTABLE), reason)
    _secure_executable(
        INSTALLED_EXECUTABLE, pins["campaign_coordinator"]["file_sha256"])


def _read_boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii").strip()
    except OSError as error:
        raise CoordinatorError("P1_COORDINATOR_BOOT_ID_UNAVAILABLE") from error
    _require(BOOT_ID.fullmatch(value) is not None,
             "P1_COORDINATOR_BOOT_ID_INVALID")
    return value


def run_production(contract_path: Path) -> Path:
    now_ms = time.time_ns() // 1_000_000
    contract_snapshot = _secure_read(
        contract_path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o600}))
    contract = validate_launch_contract(
        contract_snapshot.document, now_ms, require_current=False)
    _require(
        contract_path == Path("/etc/heptatrader/p1-safety-soak") /
            f"{contract['campaign_id']}.json",
        "P1_COORDINATOR_LAUNCH_CONTRACT_PATH_INVALID")
    bundle = _open_reference(
        contract["freeze_bundle"], expected_uid=ROOT_UID,
        expected_gid=ROOT_GID, reason="P1_COORDINATOR_FREEZE_REFERENCE_DRIFT")
    validate_freeze_bundle(
        bundle.document, now_ms, require_current=False)
    _require(
        bundle.document["campaign_id"] == contract["campaign_id"] and
        contract["pin_formal_campaign_id"] in {
            item["campaign_id"] for item in bundle.document["formal_policies"]
        } and bundle.document["boot_id"] == _read_boot_id(),
        "P1_COORDINATOR_CAMPAIGN_OR_BOOT_DRIFT")
    _bind_installed_image(bundle.document)
    state_root = Path(contract["state_root"])
    _ensure_layout(state_root, ROOT_UID, ROOT_GID)
    pin_by_role = {item["role"]: item
                   for item in bundle.document["source_producer_pins"]}
    executables = {
        role: {"path": path, "file_sha256": pin_by_role[role]["file_sha256"]}
        for role, path in ROLE_PATHS.items()
    }
    adapter = ProductionAdapter()
    bootstrap = Journal(
        state_root / "coordinator-journal", contract["campaign_id"],
        expected_uid=ROOT_UID, expected_gid=ROOT_GID)
    failure_decision_seen = any(
        item.document["event"] == "CAMPAIGN_FAILURE"
        for item in bootstrap.entries)
    terminal_decision_seen = any(
        item.document["event"] == "TERMINAL"
        for item in bootstrap.entries)
    _require(
        not (failure_decision_seen and terminal_decision_seen),
        "P1_COORDINATOR_TERMINAL_DECISION_CONFLICT")
    if bootstrap.failed or failure_decision_seen or terminal_decision_seen:
        runtime_path = state_root / "runtime-manifest.json"
        if not runtime_path.exists():
            _require(
                bootstrap.failed and not failure_decision_seen and
                not terminal_decision_seen,
                "P1_COORDINATOR_TERMINAL_RECOVERY_RUNTIME_MISSING")
            raise CoordinatorError("P1_COORDINATOR_PREVIOUSLY_FAILED_CLOSED")
        runtime = _open_runtime_for_terminal_recovery(
            runtime_path, contract, bundle,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID)
        coordinator = CampaignCoordinator(
            contract_snapshot, bundle, runtime, adapter,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID)
        if coordinator.journal.failed:
            coordinator.fail_closed("P1_COORDINATOR_PREVIOUSLY_FAILED_CLOSED")
            raise CoordinatorError("P1_COORDINATOR_PREVIOUSLY_FAILED_CLOSED")
        recovered_failure = coordinator.recover_failure_intent()
        if recovered_failure is not None:
            raise CoordinatorError(recovered_failure)
        recovered_terminal = coordinator.recover_terminal_receipt()
        _require(
            terminal_decision_seen and recovered_terminal is not None,
            "P1_COORDINATOR_TERMINAL_RECOVERY_INVALID")
        return recovered_terminal
    try:
        validate_launch_contract(contract_snapshot.document, now_ms)
        validate_freeze_bundle(bundle.document, now_ms)
    except CoordinatorError as stale_error:
        runtime_path = state_root / "runtime-manifest.json"
        if runtime_path.exists():
            runtime = _open_runtime_for_terminal_recovery(
                runtime_path, contract, bundle,
                expected_uid=ROOT_UID, expected_gid=ROOT_GID)
            coordinator = CampaignCoordinator(
                contract_snapshot, bundle, runtime, adapter,
                expected_uid=ROOT_UID, expected_gid=ROOT_GID)
            try:
                coordinator.fail_closed(stale_error.reason)
            except Exception as close_error:
                raise CoordinatorError(
                    "P1_COORDINATOR_FAILED_CLOSED_DURABILITY_FAILURE") \
                    from close_error
        else:
            bootstrap.append("CAMPAIGN", "FAILED_CLOSED", {
                "reason": stale_error.reason, "catch_up": False,
                "handoff_authorized": False,
                "owned_unit_closure_body_sha256": None,
                "cleanup_error": None,
            })
        raise
    try:
        freeze_committed = next((
            item for item in bootstrap.entries
            if item.document["event"] == "RECORDER_FREEZE" and
            item.document["status"] == "COMMITTED"), None)
        if not any(
            item.document["event"] == "RECORDER_FREEZE" and
            item.document["status"] == "INTENT"
            for item in bootstrap.entries
        ):
            bootstrap.append("RECORDER_FREEZE", "INTENT", {
                "recorder_root": str(state_root / "recorder"),
                "freeze_bundle": _reference(bundle),
            })
        spec, plan = _run_recorder_freeze(
            contract, bundle, adapter, executables,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID)
        freeze_details = {
            "campaign_spec": _reference(spec),
            "fault_plan": _reference(plan),
        }
        if freeze_committed is None:
            bootstrap.append("RECORDER_FREEZE", "COMMITTED", freeze_details)
        else:
            _require(freeze_committed.document["details"] == freeze_details,
                     "P1_COORDINATOR_RECORDER_FREEZE_DRIFT")
        runtime_document = _make_runtime(contract, bundle, spec, plan)
        _validate_runtime(runtime_document)
        runtime_path = state_root / "runtime-manifest.json"
        runtime = _publish_or_open_runtime(
            runtime_path, runtime_document, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID)
        observer_argv = [
            OBSERVER_WORKER, "--run", "--runtime-manifest", str(runtime_path),
            "--expected-runtime-manifest-file-sha256", runtime.file_sha256,
        ]
        recorder_argv = [
            RECORDER_WORKER, "--run", "--runtime-manifest", str(runtime_path),
            "--expected-runtime-manifest-file-sha256", runtime.file_sha256,
        ]
        observer_argv_path = state_root / "observer-exec-argv.json"
        recorder_argv_path = state_root / "recorder-exec-argv.json"
        _publish_or_open_argv(
            observer_argv_path, observer_argv,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID)
        _publish_or_open_argv(
            recorder_argv_path, recorder_argv,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID)
        coordinator = CampaignCoordinator(
            contract_snapshot, bundle, runtime, adapter,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID)
    except Exception as error:
        reason = _stable_failure_reason(error, setup=True)
        if not bootstrap.failed:
            bootstrap.append("CAMPAIGN", "FAILED_CLOSED", {
                "reason": reason, "catch_up": False,
                "handoff_authorized": False,
                "owned_unit_closure_body_sha256": None,
                "cleanup_error": None,
            })
        if isinstance(error, CoordinatorError):
            raise
        raise CoordinatorError(reason) from error
    if coordinator.journal.failed:
        coordinator.fail_closed("P1_COORDINATOR_PREVIOUSLY_FAILED_CLOSED")
        raise CoordinatorError("P1_COORDINATOR_PREVIOUSLY_FAILED_CLOSED")
    recovered_failure = coordinator.recover_failure_intent()
    if recovered_failure is not None:
        raise CoordinatorError(recovered_failure)
    recovered_terminal = coordinator.recover_terminal_receipt()
    if recovered_terminal is not None:
        return recovered_terminal
    _sd_notify("READY=1\nSTATUS=validating frozen campaign")
    try:
        coordinator.assert_static_unit_contracts("PREFLIGHT")
        coordinator.assert_forbidden_units_inert("PREFLIGHT")
        coordinator.ensure_worker("observer", observer_argv)
        coordinator.ensure_worker("recorder", recorder_argv)
        coordinator.assert_workers_healthy()
        pins = coordinator.produce_pins(
            observer_argv_path, recorder_argv_path)
        coordinator.ensure_injector(pins)
        for formal in runtime.document["formal_campaigns"]:
            while True:
                coordinator.assert_workers_healthy()
                state = coordinator.launch_formal_if_due(formal)
                if state == "COMPLETE":
                    break
                coordinator.assert_formal_deadline(formal, "LAUNCHER")
                _sd_notify("WATCHDOG=1\nSTATUS=" + state)
                adapter.sleep(contract["poll_interval_ms"] / 1000)
            coordinator.request_projection(formal)
            coordinator.request_launcher_cleanup(formal)
            while not coordinator.projection_complete(formal):
                coordinator.assert_workers_healthy()
                coordinator.assert_formal_deadline(formal, "PROJECTION")
                _sd_notify("WATCHDOG=1\nSTATUS=waiting for decision projection")
                adapter.sleep(contract["poll_interval_ms"] / 1000)
            while not coordinator.launcher_cleanup_complete(formal):
                coordinator.assert_workers_healthy()
                coordinator.assert_formal_deadline(formal, "CLEANUP")
                _sd_notify("WATCHDOG=1\nSTATUS=waiting for launcher cleanup")
                adapter.sleep(contract["poll_interval_ms"] / 1000)
        fault_deadline_ns = max(
            int(item["inject_at_boottime_ns"]) +
            int(item["maximum_injection_lateness_ns"]) +
            int(item["maximum_recovery_ns"])
            for item in bundle.document["planned_faults"])
        while not coordinator.injector_complete():
            coordinator.assert_workers_healthy()
            _require(
                int(coordinator.boot_clock()) <= fault_deadline_ns,
                "P1_COORDINATOR_FAULT_CAMPAIGN_DEADLINE")
            _sd_notify("WATCHDOG=1\nSTATUS=waiting for seven fault receipts")
            adapter.sleep(contract["poll_interval_ms"] / 1000)
        cleanup_request = coordinator.request_final_cleanup()
        while not coordinator.final_cleanup_complete(cleanup_request):
            coordinator.assert_workers_healthy()
            _require(
                int(coordinator.wall_clock()) <=
                    runtime.document["expires_at_ms"],
                "P1_COORDINATOR_FINAL_CLEANUP_DEADLINE")
            _sd_notify("WATCHDOG=1\nSTATUS=waiting for final cleanup evidence")
            adapter.sleep(contract["poll_interval_ms"] / 1000)
        while True:
            coordinator.assert_workers_healthy()
            if coordinator.campaign_continuity_complete():
                break
            _sd_notify(
                "WATCHDOG=1\nSTATUS=waiting for final campaign continuity "
                "checkpoint")
            adapter.sleep(contract["poll_interval_ms"] / 1000)
        coordinator.assert_workers_healthy()
        audit = coordinator.run_audit()
        closure = coordinator.close_owned_units()
        coordinator.assert_static_unit_contracts("TERMINAL")
        coordinator.assert_forbidden_units_inert("TERMINAL")
        terminal = coordinator.terminal_receipt(audit, closure)
        _sd_notify("STOPPING=1\nSTATUS=complete non-authorizing handoff")
        return terminal
    except Exception as error:
        try:
            reason = fail_closed_after_unexpected(coordinator, error)
        except Exception as close_error:
            _sd_notify(
                "STOPPING=1\nSTATUS=failed-closed publication failure")
            raise CoordinatorError(
                "P1_COORDINATOR_FAILED_CLOSED_DURABILITY_FAILURE") \
                from close_error
        _sd_notify("STOPPING=1\nSTATUS=failed closed: " + reason)
        if isinstance(error, CoordinatorError):
            raise
        raise CoordinatorError(reason) from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--launch-contract", required=True, type=Path)
    return parser


def _install_signal_handlers() -> dict[int, Any]:
    previous: dict[int, Any] = {}
    observed = False

    def handler(signum: int, _frame: Any) -> None:
        nonlocal observed
        if observed:
            return
        observed = True
        raise CoordinatorSignal(signum)

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, handler)
    return previous


def _restore_signal_handlers(previous: Mapping[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    previous_handlers: dict[int, Any] = {}
    try:
        _require(arguments.run, "P1_COORDINATOR_EXPLICIT_RUN_REQUIRED")
        _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
                 "P1_COORDINATOR_ROOT_REQUIRED")
        previous_handlers = _install_signal_handlers()
        try:
            terminal = run_production(arguments.launch_contract)
        finally:
            _restore_signal_handlers(previous_handlers)
        print(
            "hepta_p1_safety_soak_campaign_coordinator: PASS "
            f"terminal={terminal}")
        return 0
    except CoordinatorSignal as error:
        print(
            "hepta_p1_safety_soak_campaign_coordinator: FAIL " + error.reason,
            file=sys.stderr)
        return 128 + error.signum
    except CoordinatorError as error:
        print(
            "hepta_p1_safety_soak_campaign_coordinator: FAIL " + error.reason,
            file=sys.stderr)
        return 4
    except Exception:
        print(
            "hepta_p1_safety_soak_campaign_coordinator: FAIL "
            "P1_COORDINATOR_UNEXPECTED_FAILURE", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
