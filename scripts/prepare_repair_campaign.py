#!/usr/bin/env python3
"""Create one short PAPER-only repair campaign and its automatic stop timer."""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable
import fcntl
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import time
from typing import NamedTuple
import uuid


POLICY_PATH = Path("/etc/heptatrader/paper-campaigns/alpha.json")
AGENT_ENV_PATH = Path("/etc/heptatrader/local-ai-paper-agent.env")
STRATEGY_PATH = Path(
    "/usr/share/heptatrader/hepta-local-ai-paper-strategy-v3.json")
LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH = Path(
    "/etc/heptatrader/local-ai-paper-deployment-v1.json")
LOCAL_PAPER_DEPLOYMENT_EVIDENCE_SCHEMA = (
    "hepta.local-ai-paper-deployment-evidence.v1")
LOCAL_PAPER_DEPLOYMENT_EVIDENCE_FIELDS = {
    "schema", "version", "source_freeze_commit", "source_freeze_tree",
    "source_manifest_sha256", "source_baseline_sha256",
    "install_transaction_id", "installed_at_ms", "generated_at_ms", "files",
    "certified_install_closure_file_sha256",
    "certified_install_closure_body_sha256",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "body_sha256",
}
LOCAL_PAPER_DEPLOYMENT_FILE_FIELDS = {"path", "sha256", "mode"}
CERTIFIED_INSTALL_CLOSURE_SCHEMA = (
    "hepta.local-paper-certified-install-closure.v1")
CERTIFIED_INSTALL_CLOSURE_PATH = Path(
    "/etc/heptatrader/local-ai-paper-certified-install-closure-v1.json")
CERTIFIED_INSTALL_CLOSURE_FIELDS = {
    "schema", "version", "source_freeze_commit", "source_freeze_tree",
    "source_manifest_sha256", "source_baseline_sha256",
    "install_transaction_id", "installed_at_ms", "files", "body_sha256",
}
LOCAL_PAPER_DEPLOYMENT_BINDING_SCHEMA = (
    "hepta.local-paper-deployment-binding.v1")
LOCAL_PAPER_DEPLOYMENT_BINDING_FIELDS = {
    "schema", "version", "evidence_path", "evidence_file_sha256",
    "evidence_body_sha256", "source_freeze_commit", "source_freeze_tree",
    "source_manifest_sha256", "source_baseline_sha256",
    "certified_install_closure_file_sha256",
    "certified_install_closure_body_sha256",
    "install_transaction_id", "installed_at_ms", "generated_at_ms", "files",
}
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
GIT_OBJECT_PATTERN = re.compile(r"[0-9a-f]{40}")
INSTALL_TRANSACTION_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:@+\-]{7,127}")
ZERO_DIGEST = "sha256:" + "0" * 64
ZERO_GIT_OBJECT = "0" * 40
UNBOUND_DEPLOYMENT_TRANSACTION_ID = (
    "replace-with-certified-install-transaction")
MAX_DEPLOYED_FILE_BYTES = 256 * 1024 * 1024
# This is the exact installed runtime closure which can create, transport, or
# recover local IB PAPER authority.  It includes both legacy and domain-scoped
# IB sockets, their egress-policy drop-ins, the Agent-OS transport used by the
# local campaign, and the tmpfiles/policy inputs consumed at boot.  The
# independent deployment transaction must attest every member; an arbitrary
# or policy-selected subset is not accepted.
LOCAL_PAPER_DEPLOYMENT_FILES = tuple(sorted((
    (Path("/usr/bin/hepta-campaignctl"), 0o755),
    (Path("/usr/bin/hepta-sessionctl"), 0o755),
    (Path("/usr/bin/heptactl"), 0o755),
    (Path("/usr/libexec/hepta-agent-mcp-launcher"), 0o755),
    (Path("/usr/libexec/hepta-agent-session-bootstrap"), 0o755),
    (Path("/usr/libexec/hepta_agent_trust_domain.py"), 0o755),
    (Path("/usr/libexec/hepta-broker-egress-policy"), 0o755),
    (Path("/usr/libexec/hepta-ib-executiond"), 0o755),
    (Path("/usr/libexec/hepta-ib-paper-campaign-operator"), 0o755),
    (Path("/usr/libexec/hepta-ib-paper-domain-authority"), 0o755),
    (Path("/usr/libexec/hepta-local-ai-paper-agent"), 0o755),
    (Path("/usr/libexec/hepta-local-paper-control"), 0o755),
    (Path("/usr/libexec/hepta-p1-paper-canary-finalizer"), 0o755),
    (Path("/usr/libexec/hepta-p1-paper-terminal-witness-verifier"), 0o755),
    (Path("/usr/libexec/hepta-paper-terminal-latch-committer"), 0o755),
    (Path("/usr/libexec/hepta-local-paper-repair"), 0o755),
    (Path("/usr/libexec/hepta-local-paper-safe-recover"), 0o755),
    (Path("/usr/libexec/hepta-local-paper-safe-recover-guard"), 0o755),
    (Path("/usr/libexec/hepta-local-paper-session-renew"), 0o755),
    (Path("/usr/libexec/hepta-local-paper-supervisor"), 0o755),
    (Path("/usr/libexec/hepta-mcp-server"), 0o755),
    (Path("/usr/libexec/hepta-prepare-paper-campaign"), 0o755),
    (Path("/usr/libexec/hepta-tool-gatewayd"), 0o755),
    (Path("/usr/lib/systemd/system/hepta-broker-egress-policy.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-events-ib-paper.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-events-ib-paper@.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-ib-paper.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-ib-paper.service.d/"
          "10-hepta-broker-egress-policy.conf"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-ib-paper.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-ib-paper@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-ib-paper@.service.d/"
          "10-hepta-broker-egress-policy.conf"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-ib-paper@.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-ib-paper-campaign-operator@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-ib-paper-campaign-operator@.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-ib-paper-domain-preflight@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-ai-paper-agent.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-authority@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-p1-paper-canary-finalizer.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-p1-paper-canary-finalizer@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-fail-close@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-p1-paper-terminal-cutoff@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-p1-paper-terminal-witness-verifier@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-paper-terminal-latch-committer@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-safe-recover.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-safe-recover.timer"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-session-renew.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-session-renew.timer"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-supervisor.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-supervisor.timer"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-gateway.service"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-gateway.service.d/"
          "10-hepta-broker-egress-policy.conf"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-gateway@.service"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-gateway@.service.d/"
          "10-hepta-broker-egress-policy.conf"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-gateway.socket"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-gateway@.socket"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-session-supervisor.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-session-supervisor@.socket"),
     0o644),
    (Path("/usr/lib/tmpfiles.d/heptatrader-agent-os.conf"), 0o644),
    (Path("/usr/lib/tmpfiles.d/heptatrader-ib-paper.conf"), 0o644),
    (Path("/usr/share/heptatrader/hepta-broker-network-policy-v1.json"),
     0o644),
    (Path("/usr/share/heptatrader/hepta-local-ai-paper-strategy-v2.json"),
     0o644),
    (Path("/usr/share/heptatrader/hepta-local-ai-paper-strategy-v3.json"),
     0o644),
    (Path("/usr/share/heptatrader/hepta-service-identities-v1.json"), 0o644),
), key=lambda item: str(item[0])))
LOCAL_PAPER_DEPLOYMENT_FILE_COUNT = 63
if len(LOCAL_PAPER_DEPLOYMENT_FILES) != LOCAL_PAPER_DEPLOYMENT_FILE_COUNT:
    raise RuntimeError(
        "local PAPER certified deployment closure must contain exactly "
        "63 files")
STOP_UNIT = "hepta-local-ai-paper-repair-stop"
PERSISTENT_STOP_UNIT = "hepta-local-ai-paper-24h-stop"
RETRY_TIMER_UNIT = "hepta-local-ai-paper-end-flat-retry"
SAFE_RECOVERY_TIMER_UNIT = "hepta-local-paper-safe-recover.timer"
SESSION_RENEW_TIMER_UNIT = "hepta-local-paper-session-renew.timer"
SUPERVISOR_TIMER_UNIT = "hepta-local-paper-supervisor.timer"
AGENT_SERVICE_UNIT = "hepta-local-ai-paper-agent.service"
SYSTEMD_ROOT = Path("/etc/systemd/system")
STRATEGY_ID = "hepta-local-ai-paper-strategy-v3"
STRATEGY_VERSION = "3"
STATE_ROOT = Path("/var/lib/hepta-local-ai-paper-agent")
SESSION_AUTHORITY_ROOT = STATE_ROOT / "session-authority"
SESSION_ROOT = Path("/run/hepta-agent-alpha/sessions")
SUPERVISOR_LEASE_STORE = Path(
    "/var/lib/hepta-tool-gateway-alpha/session-leases.hsl2")
SUPERVISOR_LEASE_KEY = Path(
    "/etc/heptatrader/credentials/trust-domains/alpha/"
    "hepta-supervisor-lease.key")
SUPERVISOR_LEASE_CLEANUP_LOCK = Path(
    "/run/hepta-agent/session-lease-terminal-cleanup.lock")
SUPERVISOR_LEASE_BACKUP = (
    STATE_ROOT / "legacy-hsl5-paper-lease-store.backup.hsl2")
SUPERVISOR_LEASE_CLEANUP_INTENT = (
    STATE_ROOT / "legacy-hsl5-paper-cleanup.intent.json")
SUPERVISOR_LEASE_CLEANUP_RECEIPT = (
    STATE_ROOT / "legacy-hsl5-paper-cleanup.receipt.json")
SUPERVISOR_LEASE_CLEANUP_INTENT_SCHEMA = (
    "hepta.local-paper-legacy-hsl5-cleanup-intent.v1")
SUPERVISOR_LEASE_CLEANUP_RECEIPT_SCHEMA = (
    "hepta.local-paper-legacy-hsl5-cleanup-receipt.v1")
SUPERVISOR_LEASE_CLEANUP_INTENT_FIELDS = frozenset({
    "schema", "version", "migration_id", "campaign_id",
    "policy_file_sha256", "terminal_receipt_path",
    "terminal_receipt_file_sha256", "lease_store_path", "lease_key_path",
    "lease_key_file_sha256", "lease_lock_path", "pre_store_sha256",
    "backup_path",
    "expected_issuer", "expected_agent_id", "expected_peer_uid",
    "expected_key_uid", "expected_key_gid", "expected_key_mode",
    "expected_source_uid", "expected_source_gid", "expected_source_mode",
    "created_at_ms", "paper_only", "live_authorized", "body_sha256",
})
SUPERVISOR_LEASE_CLEANUP_RECEIPT_FIELDS = frozenset(
    (SUPERVISOR_LEASE_CLEANUP_INTENT_FIELDS - {
        "schema", "version", "body_sha256",
    }) | {
        "schema", "version", "migration_intent_body_sha256",
        "post_store_sha256", "backup_store_sha256", "retired_records",
        "helper_already_migrated", "completed_at_ms", "mutation_authorized",
        "body_sha256",
    })
SESSIONCTL = "/usr/bin/hepta-sessionctl"
PREPARE_TRANSACTION_PATH = STATE_ROOT / "prepare-campaign-transaction.json"
DEPLOYMENT_EVIDENCE_TRANSACTION_PATH = (
    STATE_ROOT / "deployment-evidence-transaction.json")
DEPLOYMENT_EVIDENCE_TRANSACTION_SCHEMA = (
    "hepta.local-paper-deployment-evidence-transaction.v1")
PREPARE_TRANSACTION_SCHEMA_V1 = "hepta.local-paper-prepare-transaction.v1"
PREPARE_TRANSACTION_SCHEMA_V2 = "hepta.local-paper-prepare-transaction.v2"
PREPARE_TRANSACTION_MAX_BYTES = 2 * 1024 * 1024
PREPARE_SNAPSHOT_MAX_BYTES = 512 * 1024
PAPER_POLICY_V5_SCHEMA = "hepta.ib-paper-campaign-policy.v5"
PAPER_POLICY_V1_FIELDS = {
    "schema", "version", "campaign_id", "domain_id", "enabled",
    "mutations_authorized", "paper_only", "live_authorized",
    "strategy_id", "strategy_version", "strategy_sha256",
    "valid_after_ms", "expires_at_ms", "allowed_instruments",
    "max_cycles", "max_quantity", "min_cycle_interval_ms",
    "operator_ttl_seconds", "max_intent_horizon_ms", "max_holding_ms",
    "max_active_orders", "order_type", "tif", "end_flat_required",
}
PAPER_POLICY_V5_LOCAL_FIELDS = PAPER_POLICY_V1_FIELDS | {
    "source_baseline_sha256", "admission_mode",
    "deployment_evidence_file_sha256",
    "deployment_evidence_body_sha256",
    "deployment_install_transaction_id",
}
PAPER_POLICY_V4_SCHEMA = "hepta.ib-paper-campaign-policy.v4"
PAPER_POLICY_V4_FIELDS = PAPER_POLICY_V5_LOCAL_FIELDS
PAPER_POLICY_DEPLOYMENT_BINDING_FIELDS = frozenset({
    "deployment_evidence_file_sha256",
    "deployment_evidence_body_sha256",
    "deployment_install_transaction_id",
})
PAPER_POLICY_V4_LEGACY_CLEANUP_FIELDS = (
    PAPER_POLICY_V4_FIELDS - PAPER_POLICY_DEPLOYMENT_BINDING_FIELDS)
PAPER_POLICY_V4_MAX_CYCLES = 25_000
PAPER_POLICY_V5_EXTERNAL_FIELDS = PAPER_POLICY_V1_FIELDS | {
    "source_baseline_sha256", "admission_receipt_name",
    "admission_receipt_file_sha256", "admission_receipt_body_sha256",
    "admission_finalization_current_pointer_path",
    "admission_finalization_current_pointer_file_sha256",
    "admission_finalization_current_pointer_body_sha256",
    "admission_finalization_tombstone_path",
    "admission_finalization_tombstone_file_sha256",
    "admission_finalization_tombstone_body_sha256",
    "admission_mode", "deployment_evidence_file_sha256",
    "deployment_evidence_body_sha256",
    "deployment_install_transaction_id",
    "p1_audit_receipt_path", "p1_audit_receipt_file_sha256",
    "p1_audit_receipt_body_sha256", "watch_handoff_receipt_path",
    "watch_handoff_receipt_file_sha256",
    "watch_handoff_receipt_body_sha256",
}
# Preserve the public constant used by the external-P1 fixtures and callers.
PAPER_POLICY_V5_FIELDS = PAPER_POLICY_V5_EXTERNAL_FIELDS
PAPER_POLICY_V5_MAX_DURATION_MS = 24 * 60 * 60 * 1000
PAPER_POLICY_V5_EXTERNAL_DURATION_MS = 5 * 60 * 1000
PAPER_POLICY_V5_MAX_CYCLES = 720
PAPER_POLICY_IDENTIFIER = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}")
PAPER_POLICY_STRATEGY = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
PAPER_POLICY_DOMAIN = re.compile(r"[a-z][a-z0-9-]{0,31}")
PAPER_POLICY_SAFE_JSON_NAME = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json")
PAPER_POLICY_FINALIZATION_TOMBSTONE = re.compile(
    r"finalized\.zero-exposure-[0-9a-f]{48}\.v1\.json")
PAPER_POLICY_V5_LOCAL_WAL_BINDING_FIELDS = {
    "schema", "version", "campaign_id", "domain_id", "strategy_id",
    "strategy_version", "strategy_sha256", "valid_after_ms",
    "expires_at_ms", "max_cycles", "source_baseline_sha256",
    "admission_mode", "deployment_evidence_file_sha256",
    "deployment_evidence_body_sha256",
    "deployment_install_transaction_id",
}
PAPER_POLICY_V5_WAL_BINDING_FIELDS = {
    "schema", "version", "campaign_id", "domain_id", "strategy_id",
    "strategy_version", "strategy_sha256", "valid_after_ms",
    "expires_at_ms", "max_cycles", "source_baseline_sha256",
    "admission_receipt_name", "admission_receipt_file_sha256",
    "admission_receipt_body_sha256",
    "admission_finalization_current_pointer_path",
    "admission_finalization_current_pointer_file_sha256",
    "admission_finalization_current_pointer_body_sha256",
    "admission_finalization_tombstone_path",
    "admission_finalization_tombstone_file_sha256",
    "admission_finalization_tombstone_body_sha256", "admission_mode",
    "deployment_evidence_file_sha256",
    "deployment_evidence_body_sha256",
    "deployment_install_transaction_id",
    "p1_audit_receipt_path", "p1_audit_receipt_file_sha256",
    "p1_audit_receipt_body_sha256", "watch_handoff_receipt_path",
    "watch_handoff_receipt_file_sha256",
    "watch_handoff_receipt_body_sha256",
}
PREPARE_TRANSACTION_PHASES = (
    "SNAPSHOT_READY",
    "BACKGROUND_TIMERS_STOPPED",
    "OLD_STOP_UNITS_DISARMED",
    "TARGET_ENV_INSTALLED",
    "TARGET_TIMERS_VERIFIED",
    "POLICY_COMMITTED",
    "ROLLBACK_REQUIRED",
)
CAMPAIGN_LOCK_PATHS = (
    STATE_ROOT / "safe-recovery-guard.lock",
    STATE_ROOT / "risk-recovery.lock",
    STATE_ROOT / "end-flat.lock",
)
FRESH_CAMPAIGN_RUNTIME_UNITS = (
    AGENT_SERVICE_UNIT,
    "hepta-ib-paper-campaign-operator@alpha.socket",
    "hepta-execution-ib-paper@alpha.service",
    "hepta-execution-ib-paper@alpha.socket",
    "hepta-execution-events-ib-paper@alpha.socket",
    "hepta-ib-paper-domain-preflight@alpha.service",
    "hepta-tool-gateway@alpha.service",
    "hepta-tool-gateway@alpha.socket",
    "hepta-tool-session-supervisor@alpha.socket",
    SAFE_RECOVERY_TIMER_UNIT,
    SESSION_RENEW_TIMER_UNIT,
    SUPERVISOR_TIMER_UNIT,
    PERSISTENT_STOP_UNIT + ".timer",
    PERSISTENT_STOP_UNIT + ".service",
    RETRY_TIMER_UNIT + ".timer",
)


class UnitFileSnapshot(NamedTuple):
    payload: bytes | None
    mode: int | None


class SystemdUnitSnapshot(NamedTuple):
    load_state: str
    unit_file_state: str
    active_state: str


class DeploymentEvidenceSnapshot(NamedTuple):
    payload: bytes
    document: dict[str, object]
    evidence_identity: tuple[int, ...]
    installed_identities: tuple[tuple[str, tuple[int, ...]], ...]


class CampaignLifecycleLocks:
    def __init__(self) -> None:
        self.descriptors: list[int] = []

    def __enter__(self) -> "CampaignLifecycleLocks":
        try:
            for path in CAMPAIGN_LOCK_PATHS:
                descriptor = os.open(
                    path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
                    getattr(os, "O_NOFOLLOW", 0), 0o600)
                self.descriptors.append(descriptor)
                metadata = os.fstat(descriptor)
                if (not stat.S_ISREG(metadata.st_mode) or
                        metadata.st_nlink != 1 or metadata.st_uid != 0 or
                        metadata.st_gid != 0 or
                        stat.S_IMODE(metadata.st_mode) != 0o600):
                    raise RuntimeError("REPAIR_PREPARE_LOCK_UNSAFE")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_unused: object) -> None:
        while self.descriptors:
            descriptor = self.descriptors.pop()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def campaign_lifecycle_locks() -> CampaignLifecycleLocks:
    return CampaignLifecycleLocks()


def stop_runtime_units() -> tuple[str, ...]:
    return (
        STOP_UNIT + ".timer",
        STOP_UNIT + ".service",
        PERSISTENT_STOP_UNIT + ".timer",
        PERSISTENT_STOP_UNIT + ".service",
        RETRY_TIMER_UNIT + ".timer",
    )


def background_timer_units() -> tuple[str, ...]:
    return (
        SAFE_RECOVERY_TIMER_UNIT,
        SESSION_RENEW_TIMER_UNIT,
        SUPERVISOR_TIMER_UNIT,
    )


def generated_stop_unit_paths() -> tuple[Path, ...]:
    return (
        SYSTEMD_ROOT / (PERSISTENT_STOP_UNIT + ".service"),
        SYSTEMD_ROOT / (PERSISTENT_STOP_UNIT + ".timer"),
        SYSTEMD_ROOT / (RETRY_TIMER_UNIT + ".timer"),
    )


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def _validate_v5_prepare_policy(
        document: object, *, raw: bytes | None = None,
        require_disabled: bool = False,
) -> dict[str, object]:
    failure = "REPAIR_SOURCE_POLICY_BOUNDARY_INVALID"
    if not isinstance(document, dict):
        raise RuntimeError(failure)
    admission_mode = document.get("admission_mode")
    local_only = admission_mode == "local-only"
    expected_fields = (
        PAPER_POLICY_V5_LOCAL_FIELDS if local_only else
        PAPER_POLICY_V5_EXTERNAL_FIELDS)
    if (not isinstance(document, dict) or
            set(document) != expected_fields or
            document.get("schema") != PAPER_POLICY_V5_SCHEMA or
            document.get("version") != 5 or
            admission_mode not in {"external-p1-finalized", "local-only"} or
            document.get("paper_only") is not True or
            document.get("live_authorized") is not False or
            type(document.get("enabled")) is not bool or
            type(document.get("mutations_authorized")) is not bool or
            document.get("enabled") != document.get("mutations_authorized") or
            (require_disabled and document.get("enabled") is not False) or
            document.get("order_type") != ("MKT" if local_only else "LMT") or
            document.get("tif") != "DAY" or
            document.get("allowed_instruments") != ["EUR.USD"] or
            document.get("max_quantity") != (25_000 if local_only else 1) or
            document.get("max_active_orders") != 1 or
            document.get("end_flat_required") is not True):
        raise RuntimeError(failure)
    if raw is not None and raw != canonical(document):
        raise RuntimeError("REPAIR_SOURCE_POLICY_NON_CANONICAL")
    for field, pattern in (
            ("campaign_id", PAPER_POLICY_IDENTIFIER),
            ("domain_id", PAPER_POLICY_DOMAIN),
            ("strategy_id", PAPER_POLICY_IDENTIFIER),
            ("strategy_version", PAPER_POLICY_IDENTIFIER)):
        value = document.get(field)
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise RuntimeError(failure)
    if document.get("domain_id") != "alpha":
        raise RuntimeError(failure)
    common_digests = (
        "strategy_sha256", "source_baseline_sha256",
        "deployment_evidence_file_sha256",
        "deployment_evidence_body_sha256",
    )
    external_digests = (
        "admission_receipt_file_sha256",
        "admission_receipt_body_sha256",
        "admission_finalization_current_pointer_file_sha256",
        "admission_finalization_current_pointer_body_sha256",
        "admission_finalization_tombstone_file_sha256",
        "admission_finalization_tombstone_body_sha256",
        "p1_audit_receipt_file_sha256",
        "p1_audit_receipt_body_sha256",
        "watch_handoff_receipt_file_sha256",
        "watch_handoff_receipt_body_sha256",
    )
    for field in common_digests + (() if local_only else external_digests):
        value = document.get(field)
        if (not isinstance(value, str) or
                DIGEST_PATTERN.fullmatch(value) is None):
            raise RuntimeError(failure)
        if (value == ZERO_DIGEST and
                (not local_only or document.get("enabled") is not False)):
            raise RuntimeError(failure)
    install_transaction_id = document.get(
        "deployment_install_transaction_id")
    if (not isinstance(install_transaction_id, str) or
            INSTALL_TRANSACTION_PATTERN.fullmatch(install_transaction_id)
                is None):
        raise RuntimeError(failure)
    if not local_only:
        receipt_name = document.get("admission_receipt_name")
        if (not isinstance(receipt_name, str) or
                PAPER_POLICY_SAFE_JSON_NAME.fullmatch(receipt_name) is None):
            raise RuntimeError(failure)
        pointer = document.get("admission_finalization_current_pointer_path")
        tombstone = document.get("admission_finalization_tombstone_path")
        if (not isinstance(pointer, str) or not isinstance(tombstone, str) or
                not Path(pointer).is_absolute() or
                not Path(tombstone).is_absolute() or
                os.path.normpath(pointer) != pointer or
                os.path.normpath(tombstone) != tombstone or
                Path(pointer).name != "finalization-current.v1.json" or
                PAPER_POLICY_FINALIZATION_TOMBSTONE.fullmatch(
                    Path(tombstone).name) is None or
                Path(pointer).parent != Path(tombstone).parent):
            raise RuntimeError(failure)
        for field in ("p1_audit_receipt_path", "watch_handoff_receipt_path"):
            value = document.get(field)
            if (not isinstance(value, str) or not Path(value).is_absolute() or
                    os.path.normpath(value) != value):
                raise RuntimeError(failure)
    for field, minimum, maximum in (
            ("max_cycles", 2 if local_only else 1,
             PAPER_POLICY_V5_MAX_CYCLES if local_only else 1),
            ("min_cycle_interval_ms", 1_000, 60 * 60 * 1000),
            ("operator_ttl_seconds", 5, 20),
            ("max_intent_horizon_ms", 2_000, 60_000),
            ("max_holding_ms", 0, 60 * 60 * 1000)):
        value = document.get(field)
        if (type(value) is not int or value < minimum or value > maximum):
            raise RuntimeError(failure)
    valid_after_ms = document.get("valid_after_ms")
    expires_at_ms = document.get("expires_at_ms")
    if (type(valid_after_ms) is not int or type(expires_at_ms) is not int or
            valid_after_ms < 0 or expires_at_ms < 0 or
            valid_after_ms > 2**63 - 1 or expires_at_ms > 2**63 - 1):
        raise RuntimeError(failure)
    unbound_local_seed = (
        local_only and document.get("enabled") is False and
        valid_after_ms == 0 and expires_at_ms == 0)
    if (not unbound_local_seed and
            (valid_after_ms >= expires_at_ms or
             expires_at_ms - valid_after_ms >
                PAPER_POLICY_V5_MAX_DURATION_MS)):
        raise RuntimeError(failure)
    if (not local_only and
            expires_at_ms - valid_after_ms !=
                PAPER_POLICY_V5_EXTERNAL_DURATION_MS):
        raise RuntimeError(failure)
    return document


def _validate_disabled_v4_cleanup_policy(
        document: object, raw: bytes,
) -> dict[str, object]:
    """Validate the exact legacy policy whose terminal leases may be retired."""
    failure = "REPAIR_LEGACY_LEASE_CLEANUP_POLICY_INVALID"
    if not isinstance(document, dict):
        raise RuntimeError(failure)
    try:
        expected_raw = canonical(document)
    except (TypeError, ValueError) as error:
        raise RuntimeError(failure) from error
    document_fields = frozenset(document)
    legacy_unbound_policy = (
        document_fields == PAPER_POLICY_V4_LEGACY_CLEANUP_FIELDS)
    if (document_fields not in {
                frozenset(PAPER_POLICY_V4_FIELDS),
                frozenset(PAPER_POLICY_V4_LEGACY_CLEANUP_FIELDS),
            } or
            document.get("schema") != PAPER_POLICY_V4_SCHEMA or
            type(document.get("version")) is not int or
            document.get("version") != 4 or
            document.get("domain_id") != "alpha" or
            document.get("admission_mode") != "local-only" or
            document.get("enabled") is not False or
            document.get("mutations_authorized") is not False or
            document.get("paper_only") is not True or
            document.get("live_authorized") is not False or
            document.get("order_type") != "MKT" or
            document.get("tif") != "DAY" or
            document.get("allowed_instruments") != ["EUR.USD"] or
            type(document.get("max_active_orders")) is not int or
            document.get("max_active_orders") != 1 or
            document.get("end_flat_required") is not True or
            raw != expected_raw):
        raise RuntimeError(failure)
    for field, pattern in (
            ("campaign_id", PAPER_POLICY_IDENTIFIER),
            ("domain_id", PAPER_POLICY_DOMAIN),
            ("strategy_id", PAPER_POLICY_STRATEGY),
            ("strategy_version", PAPER_POLICY_STRATEGY)):
        value = document.get(field)
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise RuntimeError(failure)
    for field in ("strategy_sha256", "source_baseline_sha256"):
        value = document.get(field)
        if (not isinstance(value, str) or
                DIGEST_PATTERN.fullmatch(value) is None or
                (field != "strategy_sha256" and value == ZERO_DIGEST)):
            raise RuntimeError(failure)
    if not legacy_unbound_policy:
        for field in (
                "deployment_evidence_file_sha256",
                "deployment_evidence_body_sha256"):
            value = document.get(field)
            if (not isinstance(value, str) or
                    DIGEST_PATTERN.fullmatch(value) is None or
                    value == ZERO_DIGEST):
                raise RuntimeError(failure)
        transaction_id = document.get("deployment_install_transaction_id")
        if (not isinstance(transaction_id, str) or
                INSTALL_TRANSACTION_PATTERN.fullmatch(transaction_id) is None):
            raise RuntimeError(failure)
    for field, minimum, maximum in (
            ("max_cycles", 1, PAPER_POLICY_V4_MAX_CYCLES),
            ("max_quantity", 1, 25_000),
            ("min_cycle_interval_ms", 1_000, 60 * 60 * 1000),
            ("operator_ttl_seconds", 5, 20),
            ("max_intent_horizon_ms", 2_000, 60_000),
            ("max_holding_ms", 0, 60 * 60 * 1000)):
        value = document.get(field)
        if type(value) is not int or value < minimum or value > maximum:
            raise RuntimeError(failure)
    if 0 < document["max_holding_ms"] < 1_000:
        raise RuntimeError(failure)
    for field in ("valid_after_ms", "expires_at_ms"):
        value = document.get(field)
        if type(value) is not int or value < 0 or value > 2**63 - 1:
            raise RuntimeError(failure)
    return document


def _v5_policy_binding_record(
        document: dict[str, object]) -> dict[str, object]:
    _validate_v5_prepare_policy(document)
    fields = (
        PAPER_POLICY_V5_LOCAL_WAL_BINDING_FIELDS
        if document["admission_mode"] == "local-only" else
        PAPER_POLICY_V5_WAL_BINDING_FIELDS)
    return {
        field: document[field]
        for field in sorted(fields)
    }


def _require_p1_bound_prepare_policy(document: object) -> None:
    """Quarantine only the legacy v4 authority shape.

    V5 has two explicit shapes: independently finalized external P1, and the
    bounded local MKT path.  The latter retains deployment-byte binding and
    crash-closed/end-flat controls without claiming external P1 evidence.
    """
    if (isinstance(document, dict) and
            (document.get("schema") ==
                 "hepta.ib-paper-campaign-policy.v4" or
             document.get("version") == 4)):
        raise RuntimeError("REPAIR_P1_ADMISSION_REQUIRED")


def atomic_write(path: Path, payload: bytes) -> None:
    metadata = os.lstat(path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or
            metadata.st_gid != 0 or metadata.st_nlink != 1):
        raise RuntimeError("REPAIR_CONFIG_PATH_UNSAFE")
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, stat.S_IMODE(metadata.st_mode))
    try:
        try:
            os.fchmod(descriptor, stat.S_IMODE(metadata.st_mode))
            os.fchown(descriptor, 0, 0)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise RuntimeError("REPAIR_ATOMIC_WRITE_FAILED")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        directory = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def atomic_install(path: Path, payload: bytes, mode: int = 0o644) -> None:
    parent = os.lstat(path.parent)
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or
            parent.st_gid != 0 or stat.S_IMODE(parent.st_mode) & 0o022):
        raise RuntimeError("REPAIR_INSTALL_DIRECTORY_UNSAFE")
    if path.exists() or path.is_symlink():
        metadata = os.lstat(path)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or
                metadata.st_gid != 0 or metadata.st_nlink != 1):
            raise RuntimeError("REPAIR_INSTALL_PATH_UNSAFE")
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, mode)
    try:
        try:
            os.fchmod(descriptor, mode)
            os.fchown(descriptor, 0, 0)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise RuntimeError("REPAIR_ATOMIC_INSTALL_FAILED")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        directory = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_stable_root_file(path: Path, failure: str) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != 0 or before.st_gid != 0 or
                stat.S_IMODE(before.st_mode) & 0o022 or
                before.st_size < 1 or
                before.st_size > PREPARE_SNAPSHOT_MAX_BYTES):
            raise RuntimeError(failure)
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                raise RuntimeError(failure)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RuntimeError(failure)
        after = os.fstat(descriptor)
        current = os.lstat(path)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (any(getattr(before, key) != getattr(after, key) for key in identity) or
                before.st_dev != current.st_dev or
                before.st_ino != current.st_ino or
                not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or
                current.st_uid != 0 or current.st_gid != 0 or
                stat.S_IMODE(current.st_mode) != stat.S_IMODE(before.st_mode)):
            raise RuntimeError(failure)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _read_stable_owned_file(
        path: Path, failure: str, *, uid: int, gid: int, mode: int,
        minimum_bytes: int = 1,
        maximum_bytes: int = PREPARE_SNAPSHOT_MAX_BYTES,
) -> bytes:
    """Read one exact private file without following or racing its path."""
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != uid or before.st_gid != gid or
                stat.S_IMODE(before.st_mode) != mode or
                before.st_size < minimum_bytes or
                before.st_size > maximum_bytes):
            raise RuntimeError(failure)
        payload = bytearray()
        while len(payload) < before.st_size:
            chunk = os.read(descriptor, before.st_size - len(payload))
            if not chunk:
                raise RuntimeError(failure)
            payload.extend(chunk)
        if os.read(descriptor, 1):
            raise RuntimeError(failure)
        after = os.fstat(descriptor)
        try:
            current = os.lstat(path)
        except FileNotFoundError as error:
            raise RuntimeError(failure) from error
        identity = (
            "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
            "st_uid", "st_gid", "st_mode", "st_nlink",
        )
        if (any(getattr(before, key) != getattr(after, key)
                for key in identity) or
                any(getattr(before, key) != getattr(current, key)
                    for key in identity)):
            raise RuntimeError(failure)
        return bytes(payload)
    finally:
        os.close(descriptor)


def _metadata_identity(metadata: object) -> tuple[int, ...]:
    return tuple(int(getattr(metadata, name)) for name in (
        "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
        "st_uid", "st_gid", "st_mode", "st_nlink",
    ))


class SupervisorLeaseCleanupExclusiveLock:
    """Exclude every lease-store runtime until cleanup evidence is durable."""

    def __init__(self) -> None:
        self.descriptor = -1
        self.parent_descriptor = -1

    def __enter__(self) -> "SupervisorLeaseCleanupExclusiveLock":
        failure = "REPAIR_LEGACY_LEASE_CLEANUP_LOCK_UNSAFE"
        path = SUPERVISOR_LEASE_CLEANUP_LOCK
        try:
            parent_before = os.lstat(path.parent)
            if (not stat.S_ISDIR(parent_before.st_mode) or
                    parent_before.st_uid != 0 or parent_before.st_gid != 0 or
                    stat.S_IMODE(parent_before.st_mode) != 0o711):
                raise RuntimeError(failure)
            self.parent_descriptor = os.open(
                path.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0))
            parent_opened = os.fstat(self.parent_descriptor)
            if (parent_before.st_dev != parent_opened.st_dev or
                    parent_before.st_ino != parent_opened.st_ino or
                    parent_opened.st_uid != 0 or parent_opened.st_gid != 0 or
                    stat.S_IMODE(parent_opened.st_mode) != 0o711):
                raise RuntimeError(failure)
            before = os.stat(
                path.name, dir_fd=self.parent_descriptor,
                follow_symlinks=False)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    before.st_uid != 0 or before.st_gid != 0 or
                    stat.S_IMODE(before.st_mode) != 0o644 or
                    before.st_size != 0):
                raise RuntimeError(failure)
            self.descriptor = os.open(
                path.name, os.O_RDWR | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self.parent_descriptor)
            opened = os.fstat(self.descriptor)
            if _metadata_identity(before) != _metadata_identity(opened):
                raise RuntimeError(failure)
            try:
                fcntl.flock(
                    self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    "REPAIR_LEGACY_LEASE_CLEANUP_IN_PROGRESS") from error
            descriptor_after = os.fstat(self.descriptor)
            path_after = os.stat(
                path.name, dir_fd=self.parent_descriptor,
                follow_symlinks=False)
            if (_metadata_identity(opened) !=
                    _metadata_identity(descriptor_after) or
                    _metadata_identity(opened) !=
                    _metadata_identity(path_after)):
                raise RuntimeError(failure)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_unused: object) -> None:
        try:
            if self.descriptor >= 0:
                descriptor = self.descriptor
                self.descriptor = -1
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
        finally:
            if self.parent_descriptor >= 0:
                parent_descriptor = self.parent_descriptor
                self.parent_descriptor = -1
                os.close(parent_descriptor)


def supervisor_lease_cleanup_exclusive_lock(
) -> SupervisorLeaseCleanupExclusiveLock:
    return SupervisorLeaseCleanupExclusiveLock()


def _validate_deployment_evidence_document(
        value: object, *, now_ms: int | None = None,
) -> dict[str, object]:
    failure = "REPAIR_DEPLOYMENT_EVIDENCE_INVALID"
    if not isinstance(value, dict) or set(value) != (
            LOCAL_PAPER_DEPLOYMENT_EVIDENCE_FIELDS):
        raise RuntimeError(failure)
    document = value
    if (document["schema"] != LOCAL_PAPER_DEPLOYMENT_EVIDENCE_SCHEMA or
            document["version"] != 1 or
            document["paper_authorized"] is not False or
            document["live_authorized"] is not False or
            document["mutation_authorized"] is not False):
        raise RuntimeError(failure)
    for field in ("source_freeze_commit", "source_freeze_tree"):
        item = document[field]
        if (not isinstance(item, str) or
                GIT_OBJECT_PATTERN.fullmatch(item) is None or
                item == ZERO_GIT_OBJECT):
            raise RuntimeError(failure)
    for field in (
            "source_manifest_sha256", "source_baseline_sha256",
            "certified_install_closure_file_sha256",
            "certified_install_closure_body_sha256",
            "body_sha256"):
        item = document[field]
        if (not isinstance(item, str) or
                DIGEST_PATTERN.fullmatch(item) is None or
                item == ZERO_DIGEST):
            raise RuntimeError(failure)
    transaction_id = document["install_transaction_id"]
    if (not isinstance(transaction_id, str) or
            INSTALL_TRANSACTION_PATTERN.fullmatch(transaction_id) is None or
            transaction_id == UNBOUND_DEPLOYMENT_TRANSACTION_ID):
        raise RuntimeError(failure)
    installed_at_ms = document["installed_at_ms"]
    generated_at_ms = document["generated_at_ms"]
    current_ms = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    if (type(installed_at_ms) is not int or
            type(generated_at_ms) is not int or
            installed_at_ms < 0 or generated_at_ms < installed_at_ms or
            generated_at_ms > current_ms):
        raise RuntimeError(failure)
    files = document["files"]
    if (not isinstance(files, list) or
            len(files) != len(LOCAL_PAPER_DEPLOYMENT_FILES)):
        raise RuntimeError(failure)
    for record, (expected_path, expected_mode) in zip(
            files, LOCAL_PAPER_DEPLOYMENT_FILES, strict=True):
        if (not isinstance(record, dict) or
                set(record) != LOCAL_PAPER_DEPLOYMENT_FILE_FIELDS or
                record["path"] != str(expected_path) or
                type(record["mode"]) is not int or
                record["mode"] != expected_mode or
                not isinstance(record["sha256"], str) or
                DIGEST_PATTERN.fullmatch(record["sha256"]) is None or
                record["sha256"] == ZERO_DIGEST):
            raise RuntimeError(failure)
    body = dict(document)
    expected_body_digest = body.pop("body_sha256")
    actual_body_digest = "sha256:" + hashlib.sha256(canonical(body)).hexdigest()
    if expected_body_digest != actual_body_digest:
        raise RuntimeError(failure)
    return document


def _snapshot_deployed_file(
        path: Path, expected_mode: int,
) -> tuple[str, tuple[int, ...]]:
    failure = "REPAIR_DEPLOYED_FILE_INVALID"
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != 0 or before.st_gid != 0 or
                stat.S_IMODE(before.st_mode) != expected_mode or
                before.st_size < 1 or
                before.st_size > MAX_DEPLOYED_FILE_BYTES):
            raise RuntimeError(failure)
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError(failure)
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RuntimeError(failure)
        after = os.fstat(descriptor)
        current = os.lstat(path)
        before_identity = _metadata_identity(before)
        if (before_identity != _metadata_identity(after) or
                before_identity != _metadata_identity(current)):
            raise RuntimeError(failure)
        return "sha256:" + digest.hexdigest(), before_identity
    finally:
        os.close(descriptor)


def _stable_deployed_file_identity(
        path: Path, expected_sha256: str, expected_mode: int,
) -> tuple[int, ...]:
    observed_sha256, identity = _snapshot_deployed_file(path, expected_mode)
    if observed_sha256 != expected_sha256:
        raise RuntimeError("REPAIR_DEPLOYED_FILE_INVALID")
    return identity


def _load_local_paper_deployment_evidence_artifact(
) -> DeploymentEvidenceSnapshot:
    """Load the sealed evidence artifact without trusting old runtime bytes.

    A disabled local seed may be upgraded only after an independent installer
    has replaced the runtime named by the next certified closure.  At that
    point the previous evidence must remain structurally and cryptographically
    valid, but its file hashes are expected not to match the new generation.
    Runtime admission continues to use the strict loader below.
    """
    failure = "REPAIR_DEPLOYMENT_EVIDENCE_PATH_UNSAFE"
    before = os.lstat(LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            before.st_uid != 0 or before.st_gid != 0 or
            stat.S_IMODE(before.st_mode) != 0o600):
        raise RuntimeError(failure)
    before_identity = _metadata_identity(before)
    payload = _read_stable_root_file(
        LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH, failure)
    current = os.lstat(LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH)
    if before_identity != _metadata_identity(current):
        raise RuntimeError(failure)
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_INVALID") from error
    if canonical(document) != payload:
        raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_NON_CANONICAL")
    validated = _validate_deployment_evidence_document(document)
    final = os.lstat(LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH)
    if before_identity != _metadata_identity(final):
        raise RuntimeError(failure)
    return DeploymentEvidenceSnapshot(
        payload, validated, before_identity, ())


def _load_local_paper_deployment_evidence() -> DeploymentEvidenceSnapshot:
    artifact = _load_local_paper_deployment_evidence_artifact()
    installed_identities: list[tuple[str, tuple[int, ...]]] = []
    records = artifact.document["files"]
    assert isinstance(records, list)
    for record, (path, mode) in zip(
            records, LOCAL_PAPER_DEPLOYMENT_FILES, strict=True):
        assert isinstance(record, dict)
        installed_identities.append((
            str(path),
            _stable_deployed_file_identity(path, str(record["sha256"]), mode),
        ))
    # Detect evidence replacement while the installed closure was hashed.
    final = os.lstat(LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH)
    if artifact.evidence_identity != _metadata_identity(final):
        raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_PATH_UNSAFE")
    return DeploymentEvidenceSnapshot(
        artifact.payload, artifact.document, artifact.evidence_identity,
        tuple(installed_identities))


def _deployment_binding_record(
        snapshot: DeploymentEvidenceSnapshot,
) -> dict[str, object]:
    document = snapshot.document
    files = document["files"]
    assert isinstance(files, list)
    return {
        "schema": LOCAL_PAPER_DEPLOYMENT_BINDING_SCHEMA,
        "version": 1,
        "evidence_path": str(LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH),
        "evidence_file_sha256":
            "sha256:" + hashlib.sha256(snapshot.payload).hexdigest(),
        "evidence_body_sha256": document["body_sha256"],
        "source_freeze_commit": document["source_freeze_commit"],
        "source_freeze_tree": document["source_freeze_tree"],
        "source_manifest_sha256": document["source_manifest_sha256"],
        "source_baseline_sha256": document["source_baseline_sha256"],
        "certified_install_closure_file_sha256":
            document["certified_install_closure_file_sha256"],
        "certified_install_closure_body_sha256":
            document["certified_install_closure_body_sha256"],
        "install_transaction_id": document["install_transaction_id"],
        "installed_at_ms": document["installed_at_ms"],
        "generated_at_ms": document["generated_at_ms"],
        "files": [dict(record) for record in files],
    }


def _validate_deployment_binding(value: object) -> dict[str, object]:
    failure = "REPAIR_PREPARE_DEPLOYMENT_BINDING_INVALID"
    if (not isinstance(value, dict) or
            set(value) != LOCAL_PAPER_DEPLOYMENT_BINDING_FIELDS or
            value.get("schema") != LOCAL_PAPER_DEPLOYMENT_BINDING_SCHEMA or
            value.get("version") != 1 or
            value.get("evidence_path") !=
                str(LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH)):
        raise RuntimeError(failure)
    for field in (
            "evidence_file_sha256", "evidence_body_sha256",
            "source_manifest_sha256", "source_baseline_sha256",
            "certified_install_closure_file_sha256",
            "certified_install_closure_body_sha256"):
        item = value.get(field)
        if (not isinstance(item, str) or
                DIGEST_PATTERN.fullmatch(item) is None or
                item == ZERO_DIGEST):
            raise RuntimeError(failure)
    for field in ("source_freeze_commit", "source_freeze_tree"):
        item = value.get(field)
        if (not isinstance(item, str) or
                GIT_OBJECT_PATTERN.fullmatch(item) is None or
                item == ZERO_GIT_OBJECT):
            raise RuntimeError(failure)
    transaction_id = value.get("install_transaction_id")
    if (not isinstance(transaction_id, str) or
            INSTALL_TRANSACTION_PATTERN.fullmatch(transaction_id) is None):
        raise RuntimeError(failure)
    installed_at_ms = value.get("installed_at_ms")
    generated_at_ms = value.get("generated_at_ms")
    if (type(installed_at_ms) is not int or
            type(generated_at_ms) is not int or
            installed_at_ms < 0 or generated_at_ms < installed_at_ms):
        raise RuntimeError(failure)
    files = value.get("files")
    if (not isinstance(files, list) or
            len(files) != len(LOCAL_PAPER_DEPLOYMENT_FILES)):
        raise RuntimeError(failure)
    for record, (path, mode) in zip(
            files, LOCAL_PAPER_DEPLOYMENT_FILES, strict=True):
        if (not isinstance(record, dict) or
                set(record) != LOCAL_PAPER_DEPLOYMENT_FILE_FIELDS or
                record.get("path") != str(path) or
                record.get("mode") != mode or
                type(record.get("mode")) is not int or
                not isinstance(record.get("sha256"), str) or
                DIGEST_PATTERN.fullmatch(str(record.get("sha256"))) is None or
                record.get("sha256") == ZERO_DIGEST):
            raise RuntimeError(failure)
    return value


def _require_deployment_snapshot_unchanged(
        expected: DeploymentEvidenceSnapshot,
) -> None:
    try:
        current = _load_local_paper_deployment_evidence()
    except (OSError, RuntimeError) as error:
        raise RuntimeError(
            "REPAIR_DEPLOYMENT_EVIDENCE_DRIFTED") from error
    if current != expected:
        raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_DRIFTED")


def _require_deployment_artifact_unchanged(
        expected: DeploymentEvidenceSnapshot,
) -> None:
    try:
        current = _load_local_paper_deployment_evidence_artifact()
    except (OSError, RuntimeError) as error:
        raise RuntimeError(
            "REPAIR_DEPLOYMENT_EVIDENCE_DRIFTED") from error
    if current != expected:
        raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_DRIFTED")


def _local_v5_disabled_seed_from_deployment(
        deployment: DeploymentEvidenceSnapshot, strategy_sha256: str,
) -> dict[str, object]:
    if (DIGEST_PATTERN.fullmatch(strategy_sha256) is None or
            strategy_sha256 == ZERO_DIGEST):
        raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_INVALID")
    binding = _deployment_binding_record(deployment)
    install_transaction_id = str(binding["install_transaction_id"])
    campaign_suffix = hashlib.sha256(
        install_transaction_id.encode("ascii")).hexdigest()[:16]
    document: dict[str, object] = {
        "schema": PAPER_POLICY_V5_SCHEMA,
        "version": 5,
        "campaign_id": "local-paper-v5-disabled-seed-" + campaign_suffix,
        "domain_id": "alpha",
        "enabled": False,
        "mutations_authorized": False,
        "paper_only": True,
        "live_authorized": False,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "strategy_sha256": strategy_sha256,
        "valid_after_ms": 0,
        "expires_at_ms": 0,
        "allowed_instruments": ["EUR.USD"],
        "max_cycles": PAPER_POLICY_V5_MAX_CYCLES,
        "max_quantity": 25_000,
        "min_cycle_interval_ms": 120_000,
        "operator_ttl_seconds": 20,
        "max_intent_horizon_ms": 60_000,
        "max_holding_ms": 0,
        "max_active_orders": 1,
        "order_type": "MKT",
        "tif": "DAY",
        "end_flat_required": True,
        "source_baseline_sha256": binding["source_baseline_sha256"],
        "admission_mode": "local-only",
        "deployment_evidence_file_sha256":
            binding["evidence_file_sha256"],
        "deployment_evidence_body_sha256":
            binding["evidence_body_sha256"],
        "deployment_install_transaction_id": install_transaction_id,
    }
    return _validate_v5_prepare_policy(document, require_disabled=True)


def _deployment_strategy_sha256(
        deployment: DeploymentEvidenceSnapshot,
) -> str:
    records = deployment.document.get("files")
    if not isinstance(records, list):
        raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_INVALID")
    matches = [
        record.get("sha256") for record in records
        if isinstance(record, dict) and
        record.get("path") == str(STRATEGY_PATH)
    ]
    if (len(matches) != 1 or not isinstance(matches[0], str) or
            DIGEST_PATTERN.fullmatch(matches[0]) is None or
            matches[0] == ZERO_DIGEST):
        raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_INVALID")
    return matches[0]


def _prior_v5_local_deployment_artifact(
        policy: dict[str, object],
) -> tuple[DeploymentEvidenceSnapshot | None, bool]:
    """Validate the exact old evidence binding for one disabled upgrade."""
    unbound_policy: dict[str, object] = {
        "schema": PAPER_POLICY_V5_SCHEMA,
        "version": 5,
        "campaign_id": "local-paper-v5-disabled-seed",
        "domain_id": "alpha",
        "enabled": False,
        "mutations_authorized": False,
        "paper_only": True,
        "live_authorized": False,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "strategy_sha256": ZERO_DIGEST,
        "valid_after_ms": 0,
        "expires_at_ms": 0,
        "allowed_instruments": ["EUR.USD"],
        "max_cycles": PAPER_POLICY_V5_MAX_CYCLES,
        "max_quantity": 25_000,
        "min_cycle_interval_ms": 120_000,
        "operator_ttl_seconds": 20,
        "max_intent_horizon_ms": 60_000,
        "max_holding_ms": 0,
        "max_active_orders": 1,
        "order_type": "MKT",
        "tif": "DAY",
        "end_flat_required": True,
        "source_baseline_sha256": ZERO_DIGEST,
        "admission_mode": "local-only",
        "deployment_evidence_file_sha256": ZERO_DIGEST,
        "deployment_evidence_body_sha256": ZERO_DIGEST,
        "deployment_install_transaction_id":
            UNBOUND_DEPLOYMENT_TRANSACTION_ID,
    }
    digest_fields = (
        "source_baseline_sha256",
        "deployment_evidence_file_sha256",
        "deployment_evidence_body_sha256",
    )
    if policy == unbound_policy:
        try:
            os.lstat(LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH)
        except FileNotFoundError:
            return None, True
        raise RuntimeError("REPAIR_SOURCE_POLICY_DEPLOYMENT_MISMATCH")
    if (any(policy.get(field) == ZERO_DIGEST for field in digest_fields) or
            policy.get("deployment_install_transaction_id") ==
                UNBOUND_DEPLOYMENT_TRANSACTION_ID):
        raise RuntimeError("REPAIR_SOURCE_POLICY_DEPLOYMENT_MISMATCH")
    try:
        artifact = _load_local_paper_deployment_evidence_artifact()
    except (OSError, RuntimeError) as error:
        raise RuntimeError(
            "REPAIR_SOURCE_POLICY_DEPLOYMENT_MISMATCH") from error
    if not _v5_policy_matches_deployment_artifact(
            policy, artifact.payload, artifact.document):
        raise RuntimeError("REPAIR_SOURCE_POLICY_DEPLOYMENT_MISMATCH")
    expected_seed = _local_v5_disabled_seed_from_deployment(
        artifact, _deployment_strategy_sha256(artifact))
    return artifact, policy == expected_seed


def _bound_deployment_source_baseline(
        source_policy: dict[str, object],
        deployment: DeploymentEvidenceSnapshot,
) -> str:
    claimed = source_policy.get("source_baseline_sha256")
    if (not isinstance(claimed, str) or
            DIGEST_PATTERN.fullmatch(claimed) is None or
            (claimed == ZERO_DIGEST and
             (source_policy.get("enabled") is not False or
              source_policy.get("mutations_authorized") is not False))):
        raise RuntimeError("REPAIR_SOURCE_POLICY_BASELINE_INVALID")
    deployed = deployment.document.get("source_baseline_sha256")
    if (not isinstance(deployed, str) or
            DIGEST_PATTERN.fullmatch(deployed) is None or
            deployed == ZERO_DIGEST):
        raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_INVALID")
    if claimed != ZERO_DIGEST and claimed != deployed:
        raise RuntimeError("REPAIR_SOURCE_POLICY_DEPLOYMENT_MISMATCH")
    return deployed


def _v5_local_seed_from_deployment(
        deployment: DeploymentEvidenceSnapshot,
) -> dict[str, object]:
    strategy_raw = _read_stable_root_file(
        STRATEGY_PATH, "REPAIR_STRATEGY_PATH_UNSAFE")
    try:
        strategy = json.loads(strategy_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("REPAIR_STRATEGY_BOUNDARY_INVALID") from error
    if (not isinstance(strategy, dict) or
            strategy.get("schema") != "hepta.local-ai-paper-strategy.v3" or
            strategy.get("version") != 3 or
            strategy.get("paper_only") is not True or
            strategy.get("live_authorized") is not False or
            strategy.get("order_type") != "MKT" or
            strategy.get("max_order_quantity") != 25_000 or
            strategy.get("max_holding_seconds") != 0 or
            strategy.get("exit_mode") != "MODEL_REVERSAL" or
            strategy.get("rate_limit_fail_closed") is not True or
            strategy.get("emergency_reduce_only_recovery") is not True or
            strategy.get("auth_rearm_required_after_rate_limit") is not True or
            strategy.get("campaign_end_flat_required") is not True):
        raise RuntimeError("REPAIR_STRATEGY_BOUNDARY_INVALID")
    return _local_v5_disabled_seed_from_deployment(
        deployment, "sha256:" + hashlib.sha256(strategy_raw).hexdigest())


def _load_certified_install_closure(
        path: Path, expected_file_sha256: str,
) -> tuple[bytes, dict[str, object], tuple[int, ...]]:
    failure = "REPAIR_CERTIFIED_INSTALL_CLOSURE_INVALID"
    if (path != CERTIFIED_INSTALL_CLOSURE_PATH or
            path == LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH or
            DIGEST_PATTERN.fullmatch(expected_file_sha256) is None or
            expected_file_sha256 == ZERO_DIGEST):
        raise RuntimeError(failure)
    metadata = os.lstat(path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("REPAIR_CERTIFIED_INSTALL_CLOSURE_PATH_UNSAFE")
    identity = _metadata_identity(metadata)
    payload = _read_stable_root_file(
        path, "REPAIR_CERTIFIED_INSTALL_CLOSURE_PATH_UNSAFE")
    if "sha256:" + hashlib.sha256(payload).hexdigest() != expected_file_sha256:
        raise RuntimeError(failure)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(failure) from error
    if not isinstance(value, dict) or canonical(value) != payload:
        raise RuntimeError(failure)
    document = value
    if (set(document) != CERTIFIED_INSTALL_CLOSURE_FIELDS or
            document.get("schema") != CERTIFIED_INSTALL_CLOSURE_SCHEMA or
            document.get("version") != 1):
        raise RuntimeError(failure)
    for field in ("source_freeze_commit", "source_freeze_tree"):
        item = document.get(field)
        if (not isinstance(item, str) or
                GIT_OBJECT_PATTERN.fullmatch(item) is None or
                item == ZERO_GIT_OBJECT):
            raise RuntimeError(failure)
    for field in (
            "source_manifest_sha256", "source_baseline_sha256",
            "body_sha256"):
        item = document.get(field)
        if (not isinstance(item, str) or
                DIGEST_PATTERN.fullmatch(item) is None or
                item == ZERO_DIGEST):
            raise RuntimeError(failure)
    transaction_id = document.get("install_transaction_id")
    installed_at_ms = document.get("installed_at_ms")
    if (not isinstance(transaction_id, str) or
            INSTALL_TRANSACTION_PATTERN.fullmatch(transaction_id) is None or
            transaction_id == UNBOUND_DEPLOYMENT_TRANSACTION_ID or
            type(installed_at_ms) is not int or installed_at_ms <= 0):
        raise RuntimeError(failure)
    files = document.get("files")
    if (not isinstance(files, list) or
            len(files) != len(LOCAL_PAPER_DEPLOYMENT_FILES)):
        raise RuntimeError(failure)
    for record, (expected_path, expected_mode) in zip(
            files, LOCAL_PAPER_DEPLOYMENT_FILES, strict=True):
        if (not isinstance(record, dict) or
                set(record) != LOCAL_PAPER_DEPLOYMENT_FILE_FIELDS or
                record.get("path") != str(expected_path) or
                type(record.get("mode")) is not int or
                record.get("mode") != expected_mode or
                not isinstance(record.get("sha256"), str) or
                DIGEST_PATTERN.fullmatch(str(record.get("sha256"))) is None or
                record.get("sha256") == ZERO_DIGEST):
            raise RuntimeError(failure)
    body = dict(document)
    expected_body = body.pop("body_sha256")
    if "sha256:" + hashlib.sha256(canonical(body)).hexdigest() != expected_body:
        raise RuntimeError(failure)
    if identity != _metadata_identity(os.lstat(path)):
        raise RuntimeError("REPAIR_CERTIFIED_INSTALL_CLOSURE_PATH_UNSAFE")
    return payload, document, identity


def _v5_policy_matches_deployment_artifact(
        policy: dict[str, object], payload: bytes,
        document: dict[str, object],
) -> bool:
    return all((
        policy.get("source_baseline_sha256") ==
            document.get("source_baseline_sha256"),
        policy.get("deployment_evidence_file_sha256") ==
            "sha256:" + hashlib.sha256(payload).hexdigest(),
        policy.get("deployment_evidence_body_sha256") ==
            document.get("body_sha256"),
        policy.get("deployment_install_transaction_id") ==
            document.get("install_transaction_id"),
    ))


def _deployment_evidence_transaction_record(
        *, previous_policy: bytes, previous_evidence: bytes | None,
        target_policy: bytes, target_evidence: bytes,
        certified_closure_file_sha256: str,
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema": DEPLOYMENT_EVIDENCE_TRANSACTION_SCHEMA,
        "version": 1,
        "certified_install_closure_path":
            str(CERTIFIED_INSTALL_CLOSURE_PATH),
        "certified_install_closure_file_sha256":
            certified_closure_file_sha256,
        "previous_policy_sha256":
            "sha256:" + hashlib.sha256(previous_policy).hexdigest(),
        "previous_evidence_sha256":
            ("sha256:" + hashlib.sha256(previous_evidence).hexdigest()
             if previous_evidence is not None else ZERO_DIGEST),
        "previous_evidence_present": previous_evidence is not None,
        "target_policy": _payload_record(target_policy),
        "target_evidence": _payload_record(target_evidence),
        "created_at_ms": time.time_ns() // 1_000_000,
    }
    return {
        **body,
        "body_sha256":
            "sha256:" + hashlib.sha256(canonical(body)).hexdigest(),
    }


def _validate_deployment_evidence_transaction(
        value: object, *, raw: bytes | None = None,
) -> dict[str, object]:
    failure = "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_INVALID"
    fields = {
        "schema", "version", "certified_install_closure_path",
        "certified_install_closure_file_sha256",
        "previous_policy_sha256", "previous_evidence_sha256",
        "previous_evidence_present",
        "target_policy", "target_evidence", "created_at_ms", "body_sha256",
    }
    if (not isinstance(value, dict) or set(value) != fields or
            value.get("schema") != DEPLOYMENT_EVIDENCE_TRANSACTION_SCHEMA or
            value.get("version") != 1 or
            value.get("certified_install_closure_path") !=
                str(CERTIFIED_INSTALL_CLOSURE_PATH) or
            type(value.get("previous_evidence_present")) is not bool or
            type(value.get("created_at_ms")) is not int or
            not 0 < int(value.get("created_at_ms", 0)) <= 0x7FFFFFFFFFFFFFFF):
        raise RuntimeError(failure)
    for field in (
            "certified_install_closure_file_sha256",
            "previous_policy_sha256", "body_sha256"):
        item = value.get(field)
        if (not isinstance(item, str) or
                DIGEST_PATTERN.fullmatch(item) is None or
                item == ZERO_DIGEST):
            raise RuntimeError(failure)
    previous_evidence_sha256 = value.get("previous_evidence_sha256")
    if (not isinstance(previous_evidence_sha256, str) or
            DIGEST_PATTERN.fullmatch(previous_evidence_sha256) is None or
            (value["previous_evidence_present"] is True) ==
                (previous_evidence_sha256 == ZERO_DIGEST)):
        raise RuntimeError(failure)
    body = dict(value)
    body_sha256 = body.pop("body_sha256")
    if (body_sha256 !=
            "sha256:" + hashlib.sha256(canonical(body)).hexdigest() or
            (raw is not None and raw != canonical(value))):
        raise RuntimeError(failure)
    try:
        target_policy_raw = _decode_payload_record(value["target_policy"])
        target_evidence_raw = _decode_payload_record(value["target_evidence"])
        target_policy = json.loads(target_policy_raw)
        target_evidence = json.loads(target_evidence_raw)
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError,
            RuntimeError) as error:
        raise RuntimeError(failure) from error
    if (canonical(target_policy) != target_policy_raw or
            canonical(target_evidence) != target_evidence_raw):
        raise RuntimeError(failure)
    try:
        _validate_v5_prepare_policy(
            target_policy, raw=target_policy_raw, require_disabled=True)
        _validate_deployment_evidence_document(target_evidence)
    except RuntimeError as error:
        raise RuntimeError(failure) from error
    assert isinstance(target_policy, dict)
    assert isinstance(target_evidence, dict)
    if (target_policy.get("admission_mode") != "local-only" or
            not _v5_policy_matches_deployment_artifact(
                target_policy, target_evidence_raw, target_evidence) or
            target_evidence.get(
                "certified_install_closure_file_sha256") !=
                    value["certified_install_closure_file_sha256"]):
        raise RuntimeError(failure)
    return value


def _load_deployment_evidence_transaction(
) -> dict[str, object] | None:
    try:
        payload = _read_stable_owned_file(
            DEPLOYMENT_EVIDENCE_TRANSACTION_PATH,
            "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_PATH_UNSAFE",
            uid=0, gid=0, mode=0o600,
            maximum_bytes=3 * PREPARE_SNAPSHOT_MAX_BYTES)
    except FileNotFoundError:
        return None
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_INVALID") from error
    return _validate_deployment_evidence_transaction(value, raw=payload)


def _install_deployment_evidence_transaction(
        record: dict[str, object],
) -> None:
    payload = canonical(_validate_deployment_evidence_transaction(record))
    try:
        os.lstat(DEPLOYMENT_EVIDENCE_TRANSACTION_PATH)
    except FileNotFoundError:
        atomic_install(
            DEPLOYMENT_EVIDENCE_TRANSACTION_PATH, payload, 0o600)
        return
    raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_RESIDUE")


def _remove_deployment_evidence_transaction() -> None:
    path = DEPLOYMENT_EVIDENCE_TRANSACTION_PATH
    metadata = os.lstat(path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError(
            "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_PATH_UNSAFE")
    os.unlink(path)
    directory = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _resume_deployment_evidence_transaction(
        record: dict[str, object], *, certified_closure_path: Path,
        certified_closure_file_sha256: str,
) -> DeploymentEvidenceSnapshot:
    record = _validate_deployment_evidence_transaction(record)
    if (certified_closure_path != CERTIFIED_INSTALL_CLOSURE_PATH or
            certified_closure_file_sha256 !=
                record["certified_install_closure_file_sha256"]):
        raise RuntimeError(
            "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_ARGUMENT_MISMATCH")
    certified_payload, certified, certified_identity = (
        _load_certified_install_closure(
            certified_closure_path, certified_closure_file_sha256))
    target_policy_raw = _decode_payload_record(record["target_policy"])
    target_evidence_raw = _decode_payload_record(record["target_evidence"])
    target_policy = json.loads(target_policy_raw)
    target_evidence = json.loads(target_evidence_raw)
    assert isinstance(target_policy, dict)
    assert isinstance(target_evidence, dict)
    certified_files = certified["files"]
    target_files = target_evidence["files"]
    if (not isinstance(certified_files, list) or
            not isinstance(target_files, list) or
            target_evidence.get("source_freeze_commit") !=
                certified.get("source_freeze_commit") or
            target_evidence.get("source_freeze_tree") !=
                certified.get("source_freeze_tree") or
            target_evidence.get("source_manifest_sha256") !=
                certified.get("source_manifest_sha256") or
            target_evidence.get("source_baseline_sha256") !=
                certified.get("source_baseline_sha256") or
            target_evidence.get("install_transaction_id") !=
                certified.get("install_transaction_id") or
            target_evidence.get("installed_at_ms") !=
                certified.get("installed_at_ms") or
            target_evidence.get(
                "certified_install_closure_body_sha256") !=
                    certified.get("body_sha256") or
            target_files != certified_files):
        raise RuntimeError(
            "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_INVALID")
    installed_identities: list[tuple[str, tuple[int, ...]]] = []
    for record_file, (path, mode) in zip(
            target_files, LOCAL_PAPER_DEPLOYMENT_FILES, strict=True):
        assert isinstance(record_file, dict)
        digest, identity = _snapshot_deployed_file(path, mode)
        if digest != record_file["sha256"]:
            raise RuntimeError(
                "REPAIR_DEPLOYED_FILE_CERTIFICATION_MISMATCH")
        installed_identities.append((str(path), identity))
    candidate = DeploymentEvidenceSnapshot(
        target_evidence_raw, target_evidence, (),
        tuple(installed_identities))
    if canonical(_v5_local_seed_from_deployment(candidate)) != target_policy_raw:
        raise RuntimeError(
            "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_INVALID")
    policy_raw = _read_stable_owned_file(
        POLICY_PATH, "REPAIR_SOURCE_POLICY_PATH_UNSAFE",
        uid=0, gid=0, mode=0o600)
    try:
        evidence_raw = _read_stable_owned_file(
            LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH,
            "REPAIR_DEPLOYMENT_EVIDENCE_PATH_UNSAFE",
            uid=0, gid=0, mode=0o600)
    except FileNotFoundError:
        evidence_raw = None
    policy_sha256 = "sha256:" + hashlib.sha256(policy_raw).hexdigest()
    evidence_sha256 = (
        "sha256:" + hashlib.sha256(evidence_raw).hexdigest()
        if evidence_raw is not None else None)
    allowed_policy = {
        record["previous_policy_sha256"],
        str(record["target_policy"]["sha256"]),
    }
    allowed_evidence = {
        str(record["target_evidence"]["sha256"]),
    }
    if record["previous_evidence_present"] is True:
        allowed_evidence.add(str(record["previous_evidence_sha256"]))
    else:
        allowed_evidence.add(None)
    if (policy_sha256 not in allowed_policy or
            evidence_sha256 not in allowed_evidence):
        raise RuntimeError(
            "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_UNRECOVERABLE")
    if evidence_sha256 != record["target_evidence"]["sha256"]:
        if record["previous_evidence_present"] is True:
            current = _read_stable_owned_file(
                LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH,
                "REPAIR_DEPLOYMENT_EVIDENCE_PATH_UNSAFE",
                uid=0, gid=0, mode=0o600)
            if ("sha256:" + hashlib.sha256(current).hexdigest() !=
                    record["previous_evidence_sha256"]):
                raise RuntimeError(
                    "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_UNRECOVERABLE")
            atomic_write(
                LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH, target_evidence_raw)
        else:
            try:
                os.lstat(LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH)
            except FileNotFoundError:
                atomic_install(
                    LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH,
                    target_evidence_raw, 0o600)
            else:
                raise RuntimeError(
                    "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_UNRECOVERABLE")
    sealed = _load_local_paper_deployment_evidence()
    if sealed.payload != target_evidence_raw or sealed.document != target_evidence:
        raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_DRIFTED")
    current_policy = _read_stable_owned_file(
        POLICY_PATH, "REPAIR_SOURCE_POLICY_PATH_UNSAFE",
        uid=0, gid=0, mode=0o600)
    if current_policy != target_policy_raw:
        if ("sha256:" + hashlib.sha256(current_policy).hexdigest() !=
                record["previous_policy_sha256"]):
            raise RuntimeError(
                "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_UNRECOVERABLE")
        atomic_write(POLICY_PATH, target_policy_raw)
    if (_read_stable_owned_file(
            POLICY_PATH, "REPAIR_SOURCE_POLICY_PATH_UNSAFE",
            uid=0, gid=0, mode=0o600) != target_policy_raw):
        raise RuntimeError("REPAIR_SOURCE_POLICY_DRIFTED")
    _require_deployment_snapshot_unchanged(sealed)
    certified_after = _load_certified_install_closure(
        certified_closure_path, certified_closure_file_sha256)
    if certified_after != (certified_payload, certified, certified_identity):
        raise RuntimeError("REPAIR_CERTIFIED_INSTALL_CLOSURE_PATH_UNSAFE")
    _remove_deployment_evidence_transaction()
    return sealed


def record_local_paper_deployment_evidence(
        *, certified_closure_path: Path,
        certified_closure_file_sha256: str,
) -> DeploymentEvidenceSnapshot:
    """Seal the root-owned receipt produced by an offline install transaction.

    This mode is deliberately non-authorizing.  Caller strings and the current
    policy are never source proof: an external certified closure, file-digest
    pinned by the deployment transaction, supplies every frozen-source and
    expected installed-file identity.  Actual installed bytes must match it.
    """
    with campaign_lifecycle_locks():
        if _load_prepare_transaction() is not None:
            raise RuntimeError("REPAIR_PREPARE_TRANSACTION_RESIDUE")
        deployment_transaction = _load_deployment_evidence_transaction()
        if deployment_transaction is not None:
            return _resume_deployment_evidence_transaction(
                deployment_transaction,
                certified_closure_path=certified_closure_path,
                certified_closure_file_sha256=
                    certified_closure_file_sha256)
        certified_payload, certified, certified_identity = (
            _load_certified_install_closure(
            certified_closure_path, certified_closure_file_sha256)
        )
        policy_raw = _read_stable_root_file(
            POLICY_PATH, "REPAIR_SOURCE_POLICY_PATH_UNSAFE")
        try:
            policy = json.loads(policy_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "REPAIR_SOURCE_POLICY_BOUNDARY_INVALID") from error
        v4_seed = (
            isinstance(policy, dict) and
            policy.get("schema") == "hepta.ib-paper-campaign-policy.v4" and
            policy.get("admission_mode") == "local-only")
        v5_seed = (
            isinstance(policy, dict) and
            policy.get("schema") == PAPER_POLICY_V5_SCHEMA and
            policy.get("admission_mode") == "local-only")
        if (not isinstance(policy, dict) or not (v4_seed or v5_seed) or
                policy.get("paper_only") is not True or
                policy.get("live_authorized") is not False or
                policy.get("enabled") is not False or
                policy.get("mutations_authorized") is not False):
            raise RuntimeError("REPAIR_SOURCE_POLICY_BOUNDARY_INVALID")
        if v5_seed:
            exact_policy_raw = _read_stable_owned_file(
                POLICY_PATH, "REPAIR_SOURCE_POLICY_PATH_UNSAFE",
                uid=0, gid=0, mode=0o600)
            if exact_policy_raw != policy_raw:
                raise RuntimeError("REPAIR_SOURCE_POLICY_DRIFTED")
            policy_raw = exact_policy_raw
            _validate_v5_prepare_policy(
                policy, raw=policy_raw, require_disabled=True)
        prior_deployment_artifact: DeploymentEvidenceSnapshot | None = None
        verified_local_disabled_seed = False
        if v5_seed:
            (prior_deployment_artifact,
             verified_local_disabled_seed) = (
                _prior_v5_local_deployment_artifact(policy))
        claimed = policy.get("source_baseline_sha256")
        if (not isinstance(claimed, str) or
                DIGEST_PATTERN.fullmatch(claimed) is None):
            raise RuntimeError("REPAIR_SOURCE_POLICY_DEPLOYMENT_MISMATCH")
        require_fresh_campaign_admission(
            policy,
            verified_local_disabled_seed=verified_local_disabled_seed)
        generated_at_ms = time.time_ns() // 1_000_000
        installed_at_ms = certified["installed_at_ms"]
        assert isinstance(installed_at_ms, int)
        if installed_at_ms > generated_at_ms:
            raise RuntimeError("REPAIR_CERTIFIED_INSTALL_CLOSURE_INVALID")
        files: list[dict[str, object]] = []
        installed_identities: list[tuple[str, tuple[int, ...]]] = []
        certified_files = certified["files"]
        assert isinstance(certified_files, list)
        for certified_record, (path, mode) in zip(
                certified_files, LOCAL_PAPER_DEPLOYMENT_FILES, strict=True):
            assert isinstance(certified_record, dict)
            digest, identity = _snapshot_deployed_file(path, mode)
            if digest != certified_record["sha256"]:
                raise RuntimeError("REPAIR_DEPLOYED_FILE_CERTIFICATION_MISMATCH")
            files.append({
                "path": str(path), "sha256": digest, "mode": mode,
            })
            installed_identities.append((str(path), identity))
        body: dict[str, object] = {
            "schema": LOCAL_PAPER_DEPLOYMENT_EVIDENCE_SCHEMA,
            "version": 1,
            "source_freeze_commit": certified["source_freeze_commit"],
            "source_freeze_tree": certified["source_freeze_tree"],
            "source_manifest_sha256": certified["source_manifest_sha256"],
            "source_baseline_sha256": certified["source_baseline_sha256"],
            "install_transaction_id": certified["install_transaction_id"],
            "installed_at_ms": installed_at_ms,
            "generated_at_ms": generated_at_ms,
            "files": files,
            "certified_install_closure_file_sha256":
                certified_closure_file_sha256,
            "certified_install_closure_body_sha256":
                certified["body_sha256"],
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
        }
        document = {
            **body,
            "body_sha256":
                "sha256:" + hashlib.sha256(canonical(body)).hexdigest(),
        }
        _validate_deployment_evidence_document(
            document, now_ms=generated_at_ms)
        payload = canonical(document)
        candidate = DeploymentEvidenceSnapshot(
            payload, document, (), tuple(installed_identities))
        migrated_policy = _v5_local_seed_from_deployment(candidate)
        migrated_policy_raw = canonical(migrated_policy)
        current_policy_raw = (
            _read_stable_owned_file(
                POLICY_PATH, "REPAIR_SOURCE_POLICY_PATH_UNSAFE",
                uid=0, gid=0, mode=0o600)
            if v5_seed else
            _read_stable_root_file(
                POLICY_PATH, "REPAIR_SOURCE_POLICY_PATH_UNSAFE"))
        if current_policy_raw != policy_raw:
            raise RuntimeError("REPAIR_SOURCE_POLICY_DRIFTED")
        if v5_seed:
            if prior_deployment_artifact is not None:
                _require_deployment_artifact_unchanged(
                    prior_deployment_artifact)
            else:
                try:
                    current_prior, current_verified = (
                        _prior_v5_local_deployment_artifact(policy))
                except (OSError, RuntimeError) as error:
                    raise RuntimeError(
                        "REPAIR_DEPLOYMENT_EVIDENCE_DRIFTED") from error
                if (current_prior is not None or
                        current_verified is not
                            verified_local_disabled_seed):
                    raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_DRIFTED")
        if v5_seed:
            transaction = _deployment_evidence_transaction_record(
                previous_policy=policy_raw,
                previous_evidence=(
                    prior_deployment_artifact.payload
                    if prior_deployment_artifact is not None else None),
                target_policy=migrated_policy_raw,
                target_evidence=payload,
                certified_closure_file_sha256=
                    certified_closure_file_sha256)
            _install_deployment_evidence_transaction(transaction)
            return _resume_deployment_evidence_transaction(
                transaction,
                certified_closure_path=certified_closure_path,
                certified_closure_file_sha256=
                    certified_closure_file_sha256)
        try:
            metadata = os.lstat(LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH)
        except FileNotFoundError:
            atomic_install(
                LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH, payload, 0o600)
        else:
            if (not stat.S_ISREG(metadata.st_mode) or
                    metadata.st_nlink != 1 or metadata.st_uid != 0 or
                    metadata.st_gid != 0 or
                    stat.S_IMODE(metadata.st_mode) != 0o600):
                raise RuntimeError(
                    "REPAIR_DEPLOYMENT_EVIDENCE_PATH_UNSAFE")
            atomic_write(LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH, payload)
        sealed = _load_local_paper_deployment_evidence()
        if (sealed.payload != payload or sealed.document != document or
                sealed.installed_identities != tuple(installed_identities)):
            raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_DRIFTED")
        atomic_write(POLICY_PATH, migrated_policy_raw)
        _require_deployment_snapshot_unchanged(sealed)
        if (_read_stable_root_file(
                    POLICY_PATH, "REPAIR_SOURCE_POLICY_PATH_UNSAFE") !=
                migrated_policy_raw or
                _read_stable_root_file(
                    certified_closure_path,
                    "REPAIR_CERTIFIED_INSTALL_CLOSURE_PATH_UNSAFE") !=
                certified_payload or
                _metadata_identity(os.lstat(certified_closure_path)) !=
                certified_identity):
            raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_DRIFTED")
        return sealed


def render_agent_env(
        campaign_id: str, strategy_sha256: str,
        auth_generation: str | None,
        auth_profile_id: str | None, source: bytes, *,
        strategy_id: str = STRATEGY_ID,
        strategy_version: str = STRATEGY_VERSION) -> bytes:
    try:
        lines = source.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise RuntimeError("REPAIR_AGENT_CAMPAIGN_ENV_INVALID") from error
    rendered: list[str] = []
    campaign_matches = 0
    strategy_id_matches = 0
    strategy_version_matches = 0
    strategy_matches = 0
    auth_matches = 0
    profile_matches = 0
    for line in lines:
        key, separator, _ = line.partition("=")
        if separator and key == "HEPTA_LOCAL_AI_CAMPAIGN_ID":
            campaign_matches += 1
            rendered.append(f"HEPTA_LOCAL_AI_CAMPAIGN_ID={campaign_id}")
        elif separator and key == "HEPTA_LOCAL_AI_STRATEGY_ID":
            strategy_id_matches += 1
            rendered.append(f"HEPTA_LOCAL_AI_STRATEGY_ID={strategy_id}")
        elif separator and key == "HEPTA_LOCAL_AI_STRATEGY_VERSION":
            strategy_version_matches += 1
            rendered.append(
                f"HEPTA_LOCAL_AI_STRATEGY_VERSION={strategy_version}")
        elif separator and key == "HEPTA_LOCAL_AI_STRATEGY_SHA256":
            strategy_matches += 1
            rendered.append(
                f"HEPTA_LOCAL_AI_STRATEGY_SHA256={strategy_sha256}")
        elif separator and key == "HEPTA_LOCAL_AI_AUTH_GENERATION":
            auth_matches += 1
            rendered.append(
                "HEPTA_LOCAL_AI_AUTH_GENERATION=" +
                (auth_generation if auth_generation is not None else _))
        elif separator and key == "HEPTA_LOCAL_AI_AUTH_PROFILE_ID":
            profile_matches += 1
            rendered.append(
                "HEPTA_LOCAL_AI_AUTH_PROFILE_ID=" +
                (auth_profile_id if auth_profile_id is not None else _))
        else:
            rendered.append(line)
    if auth_generation is not None and auth_matches == 0:
        rendered.append(
            f"HEPTA_LOCAL_AI_AUTH_GENERATION={auth_generation}")
        auth_matches = 1
    if auth_profile_id is not None and profile_matches == 0:
        rendered.append(
            f"HEPTA_LOCAL_AI_AUTH_PROFILE_ID={auth_profile_id}")
        profile_matches = 1
    if strategy_id_matches == 0:
        rendered.append(f"HEPTA_LOCAL_AI_STRATEGY_ID={strategy_id}")
        strategy_id_matches = 1
    if strategy_version_matches == 0:
        rendered.append(
            f"HEPTA_LOCAL_AI_STRATEGY_VERSION={strategy_version}")
        strategy_version_matches = 1
    if (campaign_matches != 1 or strategy_id_matches != 1 or
            strategy_version_matches != 1 or strategy_matches != 1 or
            auth_matches != 1 or profile_matches != 1):
        raise RuntimeError("REPAIR_AGENT_CAMPAIGN_ENV_INVALID")
    return ("\n".join(rendered) + "\n").encode("ascii")


def update_agent_env(
        campaign_id: str, strategy_sha256: str,
        auth_generation: str | None,
        auth_profile_id: str | None) -> None:
    rendered = render_agent_env(
        campaign_id, strategy_sha256, auth_generation, auth_profile_id,
        AGENT_ENV_PATH.read_bytes())
    atomic_write(AGENT_ENV_PATH, rendered)


def _systemctl(*arguments: str, timeout: int = 30) -> str:
    completed = subprocess.run(
        ["/usr/bin/systemctl", *arguments], text=True,
        capture_output=True, timeout=timeout, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "REPAIR_SYSTEMD_FAILED: " +
            (completed.stderr.strip() or completed.stdout.strip()))
    return completed.stdout


def _systemd_unit_dbus_path(unit: str) -> str:
    """Return systemd's object-path encoding for one unit name.

    ``systemctl show`` renders realtime timestamps in the host's local
    timezone, which is not a stable machine-readable representation.  The
    systemd D-Bus object path uses the unit-name byte encoding below (for
    example ``-`` becomes ``_2d`` and ``.`` becomes ``_2e``).
    """
    if not isinstance(unit, str) or not unit:
        raise RuntimeError("REPAIR_STOP_TIMER_VERIFICATION_FAILED")
    encoded: list[str] = []
    for index, value in enumerate(unit.encode("utf-8")):
        if ((ord("a") <= value <= ord("z")) or
                (ord("A") <= value <= ord("Z")) or
                (index > 0 and ord("0") <= value <= ord("9"))):
            encoded.append(chr(value))
        else:
            encoded.append(f"_{value:02x}")
    return "/org/freedesktop/systemd1/unit/" + "".join(encoded)


def _systemd_timer_realtime_usec(unit: str) -> int:
    """Read a timer's raw realtime deadline from systemd's D-Bus API.

    The D-Bus property is an unsigned microsecond value.  Keeping this
    separate from the human-oriented ``systemctl show`` output avoids
    interpreting timezone abbreviations (for example ``CST``) ourselves.
    """
    expected_units = {
        PERSISTENT_STOP_UNIT + ".timer",
        RETRY_TIMER_UNIT + ".timer",
    }
    if unit not in expected_units:
        raise RuntimeError("REPAIR_STOP_TIMER_VERIFICATION_FAILED")
    try:
        completed = subprocess.run(
            [
                "/usr/bin/busctl", "--system", "get-property",
                "org.freedesktop.systemd1", _systemd_unit_dbus_path(unit),
                "org.freedesktop.systemd1.Timer", "NextElapseUSecRealtime",
            ],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            "REPAIR_STOP_TIMER_VERIFICATION_FAILED") from error
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeError("REPAIR_STOP_TIMER_VERIFICATION_FAILED")
    fields = completed.stdout.strip().split()
    if (len(fields) != 2 or fields[0] != "t" or
            not re.fullmatch(r"[0-9]+", fields[1])):
        raise RuntimeError("REPAIR_STOP_TIMER_VERIFICATION_FAILED")
    value = int(fields[1])
    # UINT64_MAX is systemd's ``USEC_INFINITY`` sentinel for an unscheduled
    # timer, not a real deadline.
    if not 0 < value < ((1 << 64) - 1):
        raise RuntimeError("REPAIR_STOP_TIMER_VERIFICATION_FAILED")
    return value


def snapshot_unit_file(path: Path) -> UnitFileSnapshot:
    flags = os.O_RDONLY | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return UnitFileSnapshot(payload=None, mode=None)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or
                metadata.st_gid != 0 or metadata.st_nlink != 1 or
                stat.S_IMODE(metadata.st_mode) & 0o022):
            raise RuntimeError("REPAIR_STOP_UNIT_PATH_UNSAFE")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return UnitFileSnapshot(
            payload=b"".join(chunks), mode=stat.S_IMODE(metadata.st_mode))
    finally:
        os.close(descriptor)


def snapshot_stop_unit_files() -> dict[Path, UnitFileSnapshot]:
    return {path: snapshot_unit_file(path)
            for path in generated_stop_unit_paths()}


def _remove_installed_unit_file(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    parent = os.lstat(path.parent)
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or
            parent.st_gid != 0 or stat.S_IMODE(parent.st_mode) & 0o022 or
            not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or
            metadata.st_gid != 0 or metadata.st_nlink != 1):
        raise RuntimeError("REPAIR_STOP_UNIT_REMOVE_UNSAFE")
    os.unlink(path)
    directory = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def restore_unit_file(path: Path, snapshot: UnitFileSnapshot) -> None:
    if snapshot.payload is None:
        if snapshot.mode is not None:
            raise RuntimeError("REPAIR_STOP_UNIT_SNAPSHOT_INVALID")
        _remove_installed_unit_file(path)
        return
    if snapshot.mode is None:
        raise RuntimeError("REPAIR_STOP_UNIT_SNAPSHOT_INVALID")
    atomic_install(path, snapshot.payload, snapshot.mode)


def _systemd_properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in properties:
            raise RuntimeError("REPAIR_SYSTEMD_STATE_INVALID")
        properties[key] = value
    required = {"LoadState", "UnitFileState", "ActiveState"}
    if set(properties) != required:
        raise RuntimeError("REPAIR_SYSTEMD_STATE_INVALID")
    return properties


def read_systemd_unit_state(
        unit: str, *, validate: bool = True) -> SystemdUnitSnapshot:
    properties = _systemd_properties(_systemctl(
        "show", unit, "-p", "LoadState", "-p", "UnitFileState",
        "-p", "ActiveState"))
    snapshot = SystemdUnitSnapshot(
        load_state=properties["LoadState"],
        unit_file_state=properties["UnitFileState"],
        active_state=properties["ActiveState"])
    if not validate:
        return snapshot
    if snapshot.load_state == "not-found":
        if snapshot.unit_file_state or snapshot.active_state != "inactive":
            raise RuntimeError("REPAIR_SYSTEMD_STATE_UNSUPPORTED")
        return snapshot
    if (snapshot.load_state != "loaded" or
            snapshot.active_state not in {"active", "inactive"} or
            snapshot.unit_file_state not in {
                "enabled", "enabled-runtime", "disabled", "static",
                "indirect", "generated", "transient", "alias"}):
        raise RuntimeError("REPAIR_SYSTEMD_STATE_UNSUPPORTED")
    return snapshot


def _reset_failed_error_is_only_not_loaded(
        error: RuntimeError, units: tuple[str, ...]) -> bool:
    """Recognize systemd's benign inactive-unit reset-failed response.

    ``systemctl reset-failed`` exits non-zero for an inactive unit which has
    already been garbage-collected, even though ``systemctl show`` may still
    report ``LoadState=loaded`` from the preceding snapshot.  Only the exact
    per-unit ``not loaded`` diagnostics are eligible for the race handling;
    permission, transport, and other systemd failures must remain fatal.
    """
    prefix = "REPAIR_SYSTEMD_FAILED: "
    message = str(error)
    if not message.startswith(prefix):
        return False
    lines = tuple(line.strip() for line in message[len(prefix):].splitlines()
                  if line.strip())
    expected = frozenset(
        f"Failed to reset failed state of unit {unit}: "
        f"Unit {unit} not loaded."
        for unit in units)
    return bool(lines) and frozenset(lines).issubset(expected)


def _reset_failed_stop_units(units: tuple[str, ...]) -> None:
    """Clear old stop-unit failure state without masking a real failure.

    A stop can unload a generated unit between the snapshot and
    ``reset-failed``.  In that case systemd reports ``not loaded`` for an
    otherwise harmless inactive/not-found unit and returns 1.  Re-read every
    unit before accepting that result; an active, malformed, or unsupported
    state still fails closed.
    """
    if not units:
        return
    try:
        _systemctl("reset-failed", *units)
        return
    except RuntimeError as error:
        if not _reset_failed_error_is_only_not_loaded(error, units):
            raise
    current = {
        unit: read_systemd_unit_state(unit, validate=False)
        for unit in units
    }
    if any(
            state.active_state != "inactive" or
            state.load_state not in {"loaded", "not-found"}
            for state in current.values()):
        raise RuntimeError("REPAIR_SYSTEMD_RESET_FAILED_STATE_DRIFTED")


def snapshot_systemd_unit_states() -> dict[str, SystemdUnitSnapshot]:
    return {
        unit: read_systemd_unit_state(unit)
        for unit in stop_runtime_units() + background_timer_units()
    }


def require_agent_inactive() -> None:
    state = read_systemd_unit_state(AGENT_SERVICE_UNIT)
    if state.load_state != "loaded" or state.active_state != "inactive":
        raise RuntimeError("REPAIR_PREPARE_REQUIRES_AGENT_STOPPED")


def _command_json(command: list[str], failure: str) -> dict[str, object]:
    completed = subprocess.run(
        command, text=True, capture_output=True, timeout=30, check=False)
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(failure) from error
    if completed.returncode != 0 or not isinstance(value, dict):
        raise RuntimeError(failure)
    return value


def _require_terminal_end_flat(policy: dict[str, object]) -> None:
    if (policy.get("enabled") is not False or
            policy.get("mutations_authorized") is not False):
        raise RuntimeError("REPAIR_PREPARE_PRIOR_POLICY_NOT_TERMINAL")
    campaign_id = policy.get("campaign_id")
    if campaign_id is None:
        return
    if (not isinstance(campaign_id, str) or not campaign_id or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,255}",
                         campaign_id) is None):
        raise RuntimeError("REPAIR_PREPARE_PRIOR_CAMPAIGN_INVALID")
    path = STATE_ROOT / ("end-flat-" + campaign_id + ".receipt.json")
    metadata = os.lstat(path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("REPAIR_PREPARE_PRIOR_RECEIPT_UNSAFE")
    try:
        receipt = json.loads(path.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("REPAIR_PREPARE_PRIOR_RECEIPT_INVALID") from error
    sessions = receipt.get("revoked_sessions") \
        if isinstance(receipt, dict) else None
    legacy_v4_local_migration = (
        policy.get("schema") == "hepta.ib-paper-campaign-policy.v4" and
        policy.get("version") == 4 and
        policy.get("admission_mode") == "local-only" and
        policy.get("paper_only") is True and
        policy.get("live_authorized") is False and
        policy.get("enabled") is False and
        policy.get("mutations_authorized") is False)
    legacy_minimal_fields = {
        "active_orders", "campaign_enabled", "campaign_id",
        "campaign_policy_sha256", "cancelled_order_ids", "completed_at_ms",
        "deny_all_verified", "first_fx_cash_generation",
        "first_position_generation", "gross_absolute_position",
        "halt_result", "identity_manifest_sha256", "live_authorized",
        "local_paper_authorized", "mutations_authorized", "paper_only",
        "position", "reboot_durable", "schema",
        "second_fx_cash_generation", "second_position_generation",
    }
    legacy_minimal_receipt = (
        legacy_v4_local_migration and isinstance(receipt, dict) and
        set(receipt) == legacy_minimal_fields)
    strict_current_receipt = (
        isinstance(receipt, dict) and
        receipt.get("authorized_connector_count") == 0 and
        receipt.get("authorized_uids") == [] and
        receipt.get("identity_count") == 0 and
        receipt.get("known_campaign_sessions_revoked") is True and
        isinstance(sessions, list) and bool(sessions) and
        all(isinstance(item, dict) and item.get("revoked") is True
            for item in sessions) and
        receipt.get("tool_gateway_stopped") is True and
        receipt.get("execution_runtime_stopped") is True and
        receipt.get("start_permits_cleared") is True)
    if (not isinstance(receipt, dict) or
            receipt.get("schema") !=
                "hepta.local-ai-paper-end-flat-receipt.v1" or
            receipt.get("campaign_id") != campaign_id or
            receipt.get("campaign_policy_sha256") !=
                "sha256:" + hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest() or
            receipt.get("position") != 0 or
            receipt.get("active_orders") != 0 or
            receipt.get("gross_absolute_position") != 0 or
            receipt.get("campaign_enabled") is not False or
            receipt.get("mutations_authorized") is not False or
            receipt.get("local_paper_authorized") is not False or
            receipt.get("deny_all_verified") is not True or
            receipt.get("reboot_durable") is not True or
            receipt.get("paper_only") is not True or
            receipt.get("live_authorized") is not False or
            not (strict_current_receipt or legacy_minimal_receipt)):
        raise RuntimeError("REPAIR_PREPARE_PRIOR_RECEIPT_INVALID")


def _require_deny_all() -> None:
    status = _command_json([
        "/usr/libexec/hepta-local-paper-control", "status",
        "--domain", "alpha",
    ], "REPAIR_PREPARE_CONTROL_STATUS_INVALID")
    if (status.get("mode") != "DENY_ALL" or
            status.get("paper_authorized") is not False or
            status.get("live_authorized") is not False or
            status.get("identity_count") != 0):
        raise RuntimeError("REPAIR_PREPARE_CONTROL_NOT_DENY_ALL")
    completed = subprocess.run([
        "/usr/libexec/hepta-broker-egress-policy", "--policy",
        "/usr/share/heptatrader/hepta-broker-network-policy-v1.json",
        "--identity-manifest",
        "/usr/share/heptatrader/hepta-service-identities-v1.json",
        "--check-deny-all",
    ], text=True, capture_output=True, timeout=30, check=False)
    if (completed.returncode != 0 or re.fullmatch(
            r"hepta_broker_egress_policy: PASS policy_sha256=[0-9a-f]{64} "
            r"authorized_connectors=0 authorized_uids= "
            r"protected_ports=4\s*", completed.stdout) is None):
        raise RuntimeError("REPAIR_PREPARE_BROKER_NOT_DENY_ALL")


def _require_external_p1_boundary(policy: dict[str, object]) -> None:
    if (
            policy.get("schema") != PAPER_POLICY_V5_SCHEMA or
            policy.get("version") != 5 or
            policy.get("admission_mode") != "external-p1-finalized"):
        raise RuntimeError("REPAIR_EXTERNAL_P1_POLICY_INVALID")
    value = _command_json([
        "/usr/libexec/hepta-local-paper-control", "verify-external-p1",
        "--domain", "alpha",
        "--watch-handoff-receipt", str(policy["watch_handoff_receipt_path"]),
        "--watch-handoff-receipt-file-sha256",
        str(policy["watch_handoff_receipt_file_sha256"]),
        "--watch-handoff-receipt-body-sha256",
        str(policy["watch_handoff_receipt_body_sha256"]),
        "--campaign-id", str(policy["campaign_id"]),
        "--source-baseline-sha256", str(policy["source_baseline_sha256"]),
    ], "REPAIR_EXTERNAL_P1_BOUNDARY_INVALID")
    if (
            value.get("mode") != "DENY_ALL" or
            value.get("admission_mode") != "external-p1-finalized" or
            value.get("campaign_id") != policy["campaign_id"] or
            value.get("domain") != "alpha" or
            value.get("paper_authorized") is not False or
            value.get("live_authorized") is not False or
            value.get("identity_count") != 0 or
            value.get("watch_handoff_receipt_file_sha256") !=
                policy["watch_handoff_receipt_file_sha256"] or
            value.get("watch_handoff_receipt_body_sha256") !=
                policy["watch_handoff_receipt_body_sha256"] or
            value.get("source_baseline_sha256") !=
                policy["source_baseline_sha256"]):
        raise RuntimeError("REPAIR_EXTERNAL_P1_BOUNDARY_INVALID")


def _require_no_session_or_permit_residue() -> None:
    managed_session = re.compile(
        r"(?:local-paper|(?:risk-recovery|end-flat)-[0-9a-f]{24})"
        r"\.token(?:\.lease\.json)?")
    try:
        session_children = list(SESSION_ROOT.iterdir())
    except FileNotFoundError:
        session_children = []
    if any(managed_session.fullmatch(path.name) for path in session_children):
        raise RuntimeError("REPAIR_PREPARE_SESSION_RESIDUE")
    managed_authority = re.compile(
        r"(?:local-paper|(?:risk-recovery|end-flat)-[0-9a-f]{24})"
        r"\.token\.(?:authority\.json|revoke-token)")
    try:
        authority_children = list(SESSION_AUTHORITY_ROOT.iterdir())
    except FileNotFoundError:
        authority_children = []
    if any(managed_authority.fullmatch(path.name)
           for path in authority_children):
        raise RuntimeError("REPAIR_PREPARE_SESSION_AUTHORITY_RESIDUE")
    for name in (
            "start-permit.pending.json", "start-permit.claimed.json",
            "start-permit.consumed.json"):
        path = STATE_ROOT / name
        if path.exists() or path.is_symlink():
            raise RuntimeError("REPAIR_PREPARE_START_PERMIT_RESIDUE")


def _require_runtime_inactive() -> None:
    for unit in FRESH_CAMPAIGN_RUNTIME_UNITS:
        properties: dict[str, str] = {}
        for line in _systemctl(
                "show", unit, "-p", "LoadState", "-p", "ActiveState",
                "-p", "Job").splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in properties:
                raise RuntimeError("REPAIR_PREPARE_RUNTIME_STATE_INVALID")
            properties[key] = value
        if (set(properties) != {"LoadState", "ActiveState", "Job"} or
                properties["LoadState"] not in {"loaded", "not-found"} or
                properties["ActiveState"] != "inactive" or
                properties["Job"] != ""):
            raise RuntimeError("REPAIR_PREPARE_RUNTIME_NOT_INACTIVE:" + unit)
    for path in (
            Path("/run/hepta-agent-alpha/tools.sock"),
            Path("/run/hepta-tool-gateway-alpha/session-supervisor.sock")):
        if path.exists() or path.is_symlink():
            raise RuntimeError("REPAIR_PREPARE_RUNTIME_SOCKET_RESIDUE")


def require_fresh_campaign_admission(
        policy: dict[str, object], *,
        verified_local_disabled_seed: bool = False,
) -> None:
    require_agent_inactive()
    external_v5 = (
        policy.get("schema") == PAPER_POLICY_V5_SCHEMA and
        policy.get("admission_mode") == "external-p1-finalized")
    unbound_local_v5_seed = (
        verified_local_disabled_seed and
        policy.get("schema") == PAPER_POLICY_V5_SCHEMA and
        policy.get("admission_mode") == "local-only" and
        policy.get("enabled") is False and
        policy.get("mutations_authorized") is False and
        policy.get("valid_after_ms") == 0 and
        policy.get("expires_at_ms") == 0)
    if not external_v5 and not unbound_local_v5_seed:
        _require_terminal_end_flat(policy)
    _require_deny_all()
    _require_no_session_or_permit_residue()
    _require_runtime_inactive()
    if external_v5:
        _require_external_p1_boundary(policy)


def _sealed_cleanup_document(body: dict[str, object]) -> dict[str, object]:
    return {
        **body,
        "body_sha256": "sha256:" + hashlib.sha256(canonical(body)).hexdigest(),
    }


def _load_cleanup_document(
        path: Path, schema: str,
) -> dict[str, object] | None:
    try:
        payload = _read_stable_owned_file(
            path, "REPAIR_LEGACY_LEASE_CLEANUP_ARTIFACT_UNSAFE",
            uid=0, gid=0, mode=0o600)
    except FileNotFoundError:
        return None
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "REPAIR_LEGACY_LEASE_CLEANUP_ARTIFACT_INVALID") from error
    expected_fields = {
        SUPERVISOR_LEASE_CLEANUP_INTENT_SCHEMA:
            SUPERVISOR_LEASE_CLEANUP_INTENT_FIELDS,
        SUPERVISOR_LEASE_CLEANUP_RECEIPT_SCHEMA:
            SUPERVISOR_LEASE_CLEANUP_RECEIPT_FIELDS,
    }.get(schema)
    if expected_fields is None:
        raise RuntimeError("REPAIR_LEGACY_LEASE_CLEANUP_SCHEMA_INVALID")
    digest_fields = {
        "policy_file_sha256", "terminal_receipt_file_sha256",
        "lease_key_file_sha256", "pre_store_sha256", "body_sha256",
    }
    if schema == SUPERVISOR_LEASE_CLEANUP_RECEIPT_SCHEMA:
        digest_fields |= {
            "migration_intent_body_sha256", "post_store_sha256",
            "backup_store_sha256",
        }
    absolute_path_fields = {
        "terminal_receipt_path", "lease_store_path", "lease_key_path",
        "lease_lock_path", "backup_path",
    }
    if (not isinstance(document, dict) or set(document) != expected_fields or
            document.get("schema") != schema or
            type(document.get("version")) is not int or
            document.get("version") != 1 or
            document.get("paper_only") is not True or
            document.get("live_authorized") is not False or
            not isinstance(document.get("migration_id"), str) or
            re.fullmatch(r"[0-9a-f]{32}", document["migration_id"]) is None or
            not isinstance(document.get("campaign_id"), str) or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,255}",
                         document["campaign_id"]) is None or
            any(not isinstance(document.get(field), str) or
                DIGEST_PATTERN.fullmatch(document[field]) is None
                for field in digest_fields) or
            any(not isinstance(document.get(field), str) or
                not document[field].startswith("/") or
                "\x00" in document[field]
                for field in absolute_path_fields) or
            not isinstance(document.get("expected_issuer"), str) or
            re.fullmatch(r"[\x21-\x7e]{1,128}",
                         document["expected_issuer"]) is None or
            not isinstance(document.get("expected_agent_id"), str) or
            re.fullmatch(r"[\x21-\x7e]{1,128}",
                         document["expected_agent_id"]) is None or
            type(document.get("expected_peer_uid")) is not int or
            not 0 <= document["expected_peer_uid"] <= 0xFFFFFFFF or
            type(document.get("expected_key_uid")) is not int or
            document["expected_key_uid"] != 0 or
            type(document.get("expected_key_gid")) is not int or
            document["expected_key_gid"] != 0 or
            type(document.get("expected_key_mode")) is not int or
            document["expected_key_mode"] != 0o400 or
            type(document.get("expected_source_uid")) is not int or
            not 0 <= document["expected_source_uid"] <= 0xFFFFFFFF or
            type(document.get("expected_source_gid")) is not int or
            not 0 <= document["expected_source_gid"] <= 0xFFFFFFFF or
            type(document.get("expected_source_mode")) is not int or
            document["expected_source_mode"] != 0o600 or
            type(document.get("created_at_ms")) is not int or
            not 0 < document["created_at_ms"] <= 0x7FFFFFFFFFFFFFFF):
        raise RuntimeError("REPAIR_LEGACY_LEASE_CLEANUP_ARTIFACT_INVALID")
    if schema == SUPERVISOR_LEASE_CLEANUP_RECEIPT_SCHEMA:
        if (document.get("post_store_sha256") ==
                document.get("pre_store_sha256") or
                document.get("backup_store_sha256") !=
                document.get("pre_store_sha256") or
                type(document.get("retired_records")) is not int or
                document["retired_records"] < 1 or
                type(document.get("helper_already_migrated")) is not bool or
                type(document.get("completed_at_ms")) is not int or
                not (document["created_at_ms"] <=
                     document["completed_at_ms"] <= 0x7FFFFFFFFFFFFFFF) or
                document.get("mutation_authorized") is not False):
            raise RuntimeError(
                "REPAIR_LEGACY_LEASE_CLEANUP_ARTIFACT_INVALID")
        intent_body = {
            "schema": SUPERVISOR_LEASE_CLEANUP_INTENT_SCHEMA,
            "version": 1,
            **{
                field: document[field]
                for field in SUPERVISOR_LEASE_CLEANUP_INTENT_FIELDS
                if field not in {"schema", "version", "body_sha256"}
            },
        }
        expected_intent_sha256 = (
            "sha256:" + hashlib.sha256(canonical(intent_body)).hexdigest())
        if (document.get("migration_intent_body_sha256") !=
                expected_intent_sha256):
            raise RuntimeError(
                "REPAIR_LEGACY_LEASE_CLEANUP_ARTIFACT_INVALID")
    body = dict(document)
    body_sha256 = body.pop("body_sha256")
    if ("sha256:" + hashlib.sha256(canonical(body)).hexdigest() !=
            body_sha256 or canonical(document) != payload):
        raise RuntimeError("REPAIR_LEGACY_LEASE_CLEANUP_ARTIFACT_INVALID")
    return document


def _remove_cleanup_intent() -> None:
    metadata = os.lstat(SUPERVISOR_LEASE_CLEANUP_INTENT)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("REPAIR_LEGACY_LEASE_CLEANUP_INTENT_UNSAFE")
    os.unlink(SUPERVISOR_LEASE_CLEANUP_INTENT)
    descriptor = os.open(
        SUPERVISOR_LEASE_CLEANUP_INTENT.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_supervisor_lease_store() -> bytes:
    identity = pwd.getpwnam("hepta-gw-alpha")
    return _read_stable_owned_file(
        SUPERVISOR_LEASE_STORE,
        "REPAIR_LEGACY_LEASE_STORE_UNSAFE",
        uid=identity.pw_uid, gid=identity.pw_gid, mode=0o600,
        maximum_bytes=2 * 1024 * 1024)


def _read_supervisor_lease_backup() -> bytes:
    return _read_stable_owned_file(
        SUPERVISOR_LEASE_BACKUP,
        "REPAIR_LEGACY_LEASE_BACKUP_UNSAFE",
        uid=0, gid=0, mode=0o400,
        maximum_bytes=2 * 1024 * 1024)


def _read_supervisor_lease_key() -> bytes:
    payload = _read_stable_owned_file(
        SUPERVISOR_LEASE_KEY,
        "REPAIR_LEGACY_LEASE_KEY_UNSAFE",
        uid=0, gid=0, mode=0o400, maximum_bytes=65)
    stripped = payload.rstrip(b"\r\n")
    if not (len(stripped) == 32 or
            (len(stripped) == 64 and
             re.fullmatch(rb"[0-9a-f]{64}", stripped) is not None)):
        raise RuntimeError("REPAIR_LEGACY_LEASE_KEY_INVALID")
    return payload


def _read_supervisor_lease_lock() -> bytes:
    payload = _read_stable_owned_file(
        SUPERVISOR_LEASE_CLEANUP_LOCK,
        "REPAIR_LEGACY_LEASE_CLEANUP_LOCK_UNSAFE",
        uid=0, gid=0, mode=0o644, minimum_bytes=0, maximum_bytes=4096)
    if payload:
        raise RuntimeError("REPAIR_LEGACY_LEASE_CLEANUP_LOCK_INVALID")
    return payload


def _run_legacy_lease_cleanup_helper(
        expected_pre_store_sha256: str, expected_key_file_sha256: str,
        expected_peer_uid: int,
        expected_source_uid: int, expected_source_gid: int,
) -> dict[str, object]:
    completed = subprocess.run([
        SESSIONCTL, "terminal-cleanup-hsl5-paper",
        "--store", str(SUPERVISOR_LEASE_STORE),
        "--backup", str(SUPERVISOR_LEASE_BACKUP),
        "--key-file", str(SUPERVISOR_LEASE_KEY),
        "--lock-file", str(SUPERVISOR_LEASE_CLEANUP_LOCK),
        "--expected-key-uid", "0",
        "--expected-key-gid", "0",
        "--expected-key-mode", "0400",
        "--expected-key-file-sha256", expected_key_file_sha256,
        "--expected-issuer", "hepta.os.bootstrap",
        "--expected-agent-id", "alpha",
        "--expected-peer-uid", str(expected_peer_uid),
        "--expected-source-uid", str(expected_source_uid),
        "--expected-source-gid", str(expected_source_gid),
        "--expected-source-mode", "0600",
        "--expected-pre-store-sha256", expected_pre_store_sha256,
    ], text=True, capture_output=True, timeout=30, check=False)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "REPAIR_LEGACY_LEASE_CLEANUP_RESPONSE_INVALID") from error
    if (completed.returncode != 0 or not isinstance(result, dict) or
            set(result) != {
                "accepted", "reason_code", "retired_records",
                "pre_store_sha256", "post_store_sha256",
                "backup_store_sha256", "already_migrated",
            } or result.get("accepted") is not True or
            result.get("reason_code") != "OK" or
            not isinstance(result.get("retired_records"), int) or
            isinstance(result.get("retired_records"), bool) or
            result.get("retired_records", 0) < 1 or
            result.get("pre_store_sha256") != expected_pre_store_sha256 or
            result.get("backup_store_sha256") != expected_pre_store_sha256 or
            not isinstance(result.get("post_store_sha256"), str) or
            DIGEST_PATTERN.fullmatch(
                str(result.get("post_store_sha256"))) is None or
            result.get("post_store_sha256") == expected_pre_store_sha256 or
            not isinstance(result.get("already_migrated"), bool)):
        raise RuntimeError("REPAIR_LEGACY_LEASE_CLEANUP_RESPONSE_INVALID")
    return result


def _cleanup_file_sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _require_cleanup_scope_binding(
        document: dict[str, object], *, campaign_id: str,
        policy_sha256: str, terminal_receipt_path: Path,
        terminal_receipt_sha256: str, lease_key_file_sha256: str,
        expected_peer_uid: int, expected_source_uid: int,
        expected_source_gid: int, failure: str,
) -> None:
    expected: dict[str, object] = {
        "campaign_id": campaign_id,
        "policy_file_sha256": policy_sha256,
        "terminal_receipt_path": str(terminal_receipt_path),
        "terminal_receipt_file_sha256": terminal_receipt_sha256,
        "lease_store_path": str(SUPERVISOR_LEASE_STORE),
        "lease_key_path": str(SUPERVISOR_LEASE_KEY),
        "lease_key_file_sha256": lease_key_file_sha256,
        "lease_lock_path": str(SUPERVISOR_LEASE_CLEANUP_LOCK),
        "backup_path": str(SUPERVISOR_LEASE_BACKUP),
        "expected_issuer": "hepta.os.bootstrap",
        "expected_agent_id": "alpha",
        "expected_peer_uid": expected_peer_uid,
        "expected_key_uid": 0,
        "expected_key_gid": 0,
        "expected_key_mode": 0o400,
        "expected_source_uid": expected_source_uid,
        "expected_source_gid": expected_source_gid,
        "expected_source_mode": 0o600,
        "paper_only": True,
        "live_authorized": False,
    }
    if any(document.get(field) != value
           for field, value in expected.items()):
        raise RuntimeError(failure)


def _require_cleanup_receipt_intent_binding(
        receipt: dict[str, object], intent: dict[str, object],
) -> None:
    shared_fields = SUPERVISOR_LEASE_CLEANUP_INTENT_FIELDS - {
        "schema", "version", "body_sha256",
    }
    if (receipt.get("migration_intent_body_sha256") !=
            intent.get("body_sha256") or
            any(receipt.get(field) != intent.get(field)
                for field in shared_fields)):
        raise RuntimeError("REPAIR_LEGACY_LEASE_CLEANUP_INTENT_DRIFTED")


def migrate_legacy_hsl5_paper_leases() -> dict[str, object]:
    """Retire only terminal ownerless HSL5 PAPER leases; grant no authority."""
    with campaign_lifecycle_locks():
        if _load_prepare_transaction() is not None:
            raise RuntimeError("REPAIR_PREPARE_TRANSACTION_RESIDUE")
        if _load_deployment_evidence_transaction() is not None:
            raise RuntimeError(
                "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_RESIDUE")
        policy_raw = _read_stable_root_file(
            POLICY_PATH, "REPAIR_SOURCE_POLICY_PATH_UNSAFE")
        try:
            policy = json.loads(policy_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("REPAIR_SOURCE_POLICY_BOUNDARY_INVALID") from error
        policy = _validate_disabled_v4_cleanup_policy(policy, policy_raw)
        campaign_id = policy["campaign_id"]
        assert isinstance(campaign_id, str)
        _require_terminal_end_flat(policy)
        terminal_receipt_path = STATE_ROOT / (
            "end-flat-" + campaign_id + ".receipt.json")
        terminal_receipt_raw = _read_stable_root_file(
            terminal_receipt_path,
            "REPAIR_LEGACY_LEASE_CLEANUP_RECEIPT_UNSAFE")
        policy_sha256 = "sha256:" + hashlib.sha256(policy_raw).hexdigest()
        terminal_receipt_sha256 = (
            "sha256:" + hashlib.sha256(terminal_receipt_raw).hexdigest())
        _require_deny_all()
        _require_no_session_or_permit_residue()
        _require_runtime_inactive()
        if (_read_stable_root_file(
                POLICY_PATH, "REPAIR_SOURCE_POLICY_PATH_UNSAFE") != policy_raw or
                _read_stable_root_file(
                    terminal_receipt_path,
                    "REPAIR_LEGACY_LEASE_CLEANUP_RECEIPT_UNSAFE") !=
                terminal_receipt_raw):
            raise RuntimeError("REPAIR_LEGACY_LEASE_CLEANUP_BINDING_DRIFTED")

        agent_identity = pwd.getpwnam("hepta-agent-alpha")
        gateway_identity = pwd.getpwnam("hepta-gw-alpha")
        key_snapshot = _read_supervisor_lease_key()
        lock_snapshot = _read_supervisor_lease_lock()
        lease_key_file_sha256 = _cleanup_file_sha256(key_snapshot)
        receipt = _load_cleanup_document(
            SUPERVISOR_LEASE_CLEANUP_RECEIPT,
            SUPERVISOR_LEASE_CLEANUP_RECEIPT_SCHEMA)
        if receipt is not None:
            with supervisor_lease_cleanup_exclusive_lock():
                intent = _load_cleanup_document(
                    SUPERVISOR_LEASE_CLEANUP_INTENT,
                    SUPERVISOR_LEASE_CLEANUP_INTENT_SCHEMA)
                current_store_sha256 = _cleanup_file_sha256(
                    _read_supervisor_lease_store())
                backup_store_sha256 = _cleanup_file_sha256(
                    _read_supervisor_lease_backup())
                _require_cleanup_scope_binding(
                    receipt, campaign_id=campaign_id,
                    policy_sha256=policy_sha256,
                    terminal_receipt_path=terminal_receipt_path,
                    terminal_receipt_sha256=terminal_receipt_sha256,
                    lease_key_file_sha256=lease_key_file_sha256,
                    expected_peer_uid=agent_identity.pw_uid,
                    expected_source_uid=gateway_identity.pw_uid,
                    expected_source_gid=gateway_identity.pw_gid,
                    failure="REPAIR_LEGACY_LEASE_CLEANUP_RECEIPT_DRIFTED")
                if (receipt.get("post_store_sha256") !=
                        current_store_sha256 or
                        receipt.get("pre_store_sha256") !=
                        backup_store_sha256 or
                        receipt.get("backup_store_sha256") !=
                            backup_store_sha256 or
                        _read_supervisor_lease_key() != key_snapshot or
                        _read_supervisor_lease_lock() != lock_snapshot):
                    raise RuntimeError(
                        "REPAIR_LEGACY_LEASE_CLEANUP_RECEIPT_DRIFTED")
                if intent is not None:
                    _require_cleanup_scope_binding(
                        intent, campaign_id=campaign_id,
                        policy_sha256=policy_sha256,
                        terminal_receipt_path=terminal_receipt_path,
                        terminal_receipt_sha256=terminal_receipt_sha256,
                        lease_key_file_sha256=lease_key_file_sha256,
                        expected_peer_uid=agent_identity.pw_uid,
                        expected_source_uid=gateway_identity.pw_uid,
                        expected_source_gid=gateway_identity.pw_gid,
                        failure=
                            "REPAIR_LEGACY_LEASE_CLEANUP_INTENT_DRIFTED")
                    _require_cleanup_receipt_intent_binding(receipt, intent)
                    _remove_cleanup_intent()
                return receipt

        intent = _load_cleanup_document(
            SUPERVISOR_LEASE_CLEANUP_INTENT,
            SUPERVISOR_LEASE_CLEANUP_INTENT_SCHEMA)
        if intent is None:
            pre_store_sha256 = "sha256:" + hashlib.sha256(
                _read_supervisor_lease_store()).hexdigest()
            intent_body: dict[str, object] = {
                "schema": SUPERVISOR_LEASE_CLEANUP_INTENT_SCHEMA,
                "version": 1,
                "migration_id": uuid.uuid4().hex,
                "campaign_id": campaign_id,
                "policy_file_sha256": policy_sha256,
                "terminal_receipt_path": str(terminal_receipt_path),
                "terminal_receipt_file_sha256": terminal_receipt_sha256,
                "lease_store_path": str(SUPERVISOR_LEASE_STORE),
                "lease_key_path": str(SUPERVISOR_LEASE_KEY),
                "lease_key_file_sha256": lease_key_file_sha256,
                "lease_lock_path": str(SUPERVISOR_LEASE_CLEANUP_LOCK),
                "pre_store_sha256": pre_store_sha256,
                "backup_path": str(SUPERVISOR_LEASE_BACKUP),
                "expected_issuer": "hepta.os.bootstrap",
                "expected_agent_id": "alpha",
                "expected_peer_uid": agent_identity.pw_uid,
                "expected_key_uid": 0,
                "expected_key_gid": 0,
                "expected_key_mode": 0o400,
                "expected_source_uid": gateway_identity.pw_uid,
                "expected_source_gid": gateway_identity.pw_gid,
                "expected_source_mode": 0o600,
                "created_at_ms": time.time_ns() // 1_000_000,
                "paper_only": True,
                "live_authorized": False,
            }
            intent = _sealed_cleanup_document(intent_body)
            atomic_install(
                SUPERVISOR_LEASE_CLEANUP_INTENT, canonical(intent), 0o600)
        else:
            pre_store_sha256 = intent.get("pre_store_sha256")
            _require_cleanup_scope_binding(
                intent, campaign_id=campaign_id,
                policy_sha256=policy_sha256,
                terminal_receipt_path=terminal_receipt_path,
                terminal_receipt_sha256=terminal_receipt_sha256,
                lease_key_file_sha256=lease_key_file_sha256,
                expected_peer_uid=agent_identity.pw_uid,
                expected_source_uid=gateway_identity.pw_uid,
                expected_source_gid=gateway_identity.pw_gid,
                failure="REPAIR_LEGACY_LEASE_CLEANUP_INTENT_DRIFTED")
            if (not isinstance(pre_store_sha256, str) or
                    DIGEST_PATTERN.fullmatch(pre_store_sha256) is None):
                raise RuntimeError("REPAIR_LEGACY_LEASE_CLEANUP_INTENT_DRIFTED")
        assert isinstance(pre_store_sha256, str)
        result = _run_legacy_lease_cleanup_helper(
            pre_store_sha256, lease_key_file_sha256,
            agent_identity.pw_uid,
            gateway_identity.pw_uid, gateway_identity.pw_gid)
        with supervisor_lease_cleanup_exclusive_lock():
            _require_deny_all()
            _require_no_session_or_permit_residue()
            _require_runtime_inactive()
            if (_read_stable_root_file(
                    POLICY_PATH,
                    "REPAIR_SOURCE_POLICY_PATH_UNSAFE") != policy_raw or
                    _read_stable_root_file(
                        terminal_receipt_path,
                        "REPAIR_LEGACY_LEASE_CLEANUP_RECEIPT_UNSAFE") !=
                    terminal_receipt_raw):
                raise RuntimeError(
                    "REPAIR_LEGACY_LEASE_CLEANUP_BINDING_DRIFTED")
            if _read_supervisor_lease_key() != key_snapshot:
                raise RuntimeError("REPAIR_LEGACY_LEASE_KEY_DRIFTED")
            if _read_supervisor_lease_lock() != lock_snapshot:
                raise RuntimeError("REPAIR_LEGACY_LEASE_CLEANUP_LOCK_DRIFTED")
            observed_post_sha256 = _cleanup_file_sha256(
                _read_supervisor_lease_store())
            observed_backup_sha256 = _cleanup_file_sha256(
                _read_supervisor_lease_backup())
            if (observed_post_sha256 != result["post_store_sha256"] or
                    observed_backup_sha256 != pre_store_sha256 or
                    result["backup_store_sha256"] != pre_store_sha256):
                raise RuntimeError("REPAIR_LEGACY_LEASE_CLEANUP_STORE_DRIFTED")
            receipt_body: dict[str, object] = {
                **{key: value for key, value in intent.items()
                   if key not in {"body_sha256", "schema", "version"}},
                "schema": SUPERVISOR_LEASE_CLEANUP_RECEIPT_SCHEMA,
                "version": 1,
                "migration_intent_body_sha256": intent["body_sha256"],
                "post_store_sha256": result["post_store_sha256"],
                "backup_store_sha256": result["backup_store_sha256"],
                "retired_records": result["retired_records"],
                "helper_already_migrated": result["already_migrated"],
                "completed_at_ms": time.time_ns() // 1_000_000,
                "mutation_authorized": False,
                "paper_only": True,
                "live_authorized": False,
            }
            receipt = _sealed_cleanup_document(receipt_body)
            atomic_install(
                SUPERVISOR_LEASE_CLEANUP_RECEIPT, canonical(receipt), 0o600)
            _remove_cleanup_intent()
            return receipt


def _disable_unit_enablement(
        units: list[str] | tuple[str, ...], *, stop: bool = False) -> None:
    if not units:
        return
    arguments = ["disable"]
    if stop:
        arguments.append("--now")
    _systemctl(*arguments, *units, timeout=60 if stop else 30)
    _systemctl("disable", "--runtime", *units)


def _quiesce_current_stop_units() -> None:
    states = {
        unit: read_systemd_unit_state(unit, validate=False)
        for unit in stop_runtime_units()
    }
    loaded = [unit for unit, snapshot in states.items()
              if snapshot.load_state != "not-found"]
    if loaded:
        _systemctl("stop", *loaded, timeout=330)
    enabled = [unit for unit, snapshot in states.items()
               if snapshot.load_state != "not-found" and
               snapshot.unit_file_state in {"enabled", "enabled-runtime"}]
    _disable_unit_enablement(enabled)


def restore_systemd_unit_states(
        snapshots: dict[str, SystemdUnitSnapshot]) -> None:
    enablement_mutable = [
        unit for unit, snapshot in snapshots.items()
        if snapshot.load_state != "not-found" and
        snapshot.unit_file_state in {
            "enabled", "enabled-runtime", "disabled"}
    ]
    _disable_unit_enablement(enablement_mutable)
    for unit, snapshot in snapshots.items():
        if snapshot.load_state == "not-found":
            continue
        if snapshot.unit_file_state == "enabled":
            _systemctl("enable", unit)
        elif snapshot.unit_file_state == "enabled-runtime":
            _systemctl("enable", "--runtime", unit)
    for unit, snapshot in snapshots.items():
        if snapshot.load_state == "not-found":
            continue
        if snapshot.active_state == "active":
            _systemctl("start", unit, timeout=330)
        else:
            _systemctl("stop", unit, timeout=330)
    restored = {
        unit: read_systemd_unit_state(unit)
        for unit in snapshots
    }
    if restored != snapshots:
        raise RuntimeError("REPAIR_SYSTEMD_STATE_RESTORE_FAILED")


def rollback_prepare(
        previous_policy: bytes, previous_env: bytes,
        unit_files: dict[Path, UnitFileSnapshot],
        unit_states: dict[str, SystemdUnitSnapshot]) -> None:
    failures: list[str] = []

    def attempt(label: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except BaseException as error:
            failures.append(f"{label}:{type(error).__name__}:{error}")

    attempt("quiesce", _quiesce_current_stop_units)
    for path, snapshot in unit_files.items():
        attempt(
            "restore-unit-" + path.name,
            lambda path=path, snapshot=snapshot:
                restore_unit_file(path, snapshot))
    attempt("restore-policy", lambda: atomic_write(
        POLICY_PATH, previous_policy))
    attempt("restore-env", lambda: atomic_write(
        AGENT_ENV_PATH, previous_env))
    attempt("daemon-reload", lambda: _systemctl("daemon-reload"))
    attempt("restore-unit-states", lambda: restore_systemd_unit_states(
        unit_states))
    if failures:
        raise RuntimeError(
            "REPAIR_PREPARE_ROLLBACK_FAILED: " + "; ".join(failures))


def disarm_old_stop_units(
        snapshots: dict[str, SystemdUnitSnapshot]) -> None:
    loaded = [unit for unit in stop_runtime_units()
              if snapshots[unit].load_state != "not-found"]
    if loaded:
        _systemctl("stop", *loaded, timeout=330)
    enabled = [unit for unit in loaded if
               snapshots[unit].unit_file_state in {
                   "enabled", "enabled-runtime"}]
    _disable_unit_enablement(enabled)
    if loaded:
        _reset_failed_stop_units(tuple(loaded))


def keep_background_timers_stopped() -> None:
    """Do not let a prepared campaign start before rearm/manual start."""
    _disable_unit_enablement(background_timer_units(), stop=True)


def activate_background_timers() -> None:
    """Enable recurring custodians only after the explicit manual start."""
    _systemctl(
        "enable", "--now", SAFE_RECOVERY_TIMER_UNIT,
        SESSION_RENEW_TIMER_UNIT, SUPERVISOR_TIMER_UNIT)


def stop_unit_payloads(deadline_seconds: int) -> dict[Path, bytes]:
    deadline = dt.datetime.fromtimestamp(
        deadline_seconds, tz=dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    service = """[Unit]
Description=Safely end and flatten the bounded local AI PAPER campaign
After=network-online.target hepta-tool-gateway@alpha.service hepta-execution-ib-paper@alpha.service
OnFailure=hepta-local-ai-paper-end-flat-retry.timer

[Service]
Type=oneshot
ExecCondition=/usr/libexec/hepta-local-paper-repair end-flat-condition
ExecStart=/usr/libexec/hepta-local-paper-repair end-flat
TimeoutStartSec=300s
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=read-only
ProtectSystem=strict
ReadWritePaths=/var/lib/hepta-local-ai-paper-agent /etc/heptatrader /etc/systemd/system/hepta-broker-egress-policy.service.d -/run/hepta-agent-alpha
RestrictSUIDSGID=yes
LockPersonality=yes
""".encode("ascii")
    timer = f"""[Unit]
Description=Absolute 24-hour local AI PAPER campaign deadline

[Timer]
OnCalendar={deadline}
Persistent=true
AccuracySec=1s
RandomizedDelaySec=0
RemainAfterElapse=no
Unit=hepta-local-ai-paper-24h-stop.service

[Install]
WantedBy=timers.target
""".encode("ascii")
    retry = """[Unit]
Description=Retry fail-closed PAPER end-flat after a transient failure

[Timer]
OnActiveSec=60s
OnUnitInactiveSec=60s
AccuracySec=1s
RandomizedDelaySec=0
RemainAfterElapse=no
Unit=hepta-local-ai-paper-24h-stop.service

[Install]
WantedBy=timers.target
""".encode("ascii")
    return {
        SYSTEMD_ROOT / (PERSISTENT_STOP_UNIT + ".service"): service,
        SYSTEMD_ROOT / (PERSISTENT_STOP_UNIT + ".timer"): timer,
        SYSTEMD_ROOT / (RETRY_TIMER_UNIT + ".timer"): retry,
    }


def _verify_stop_unit_files(payloads: dict[Path, bytes]) -> None:
    if set(payloads) != set(generated_stop_unit_paths()):
        raise RuntimeError("REPAIR_STOP_TIMER_PAYLOAD_INVALID")
    for path, payload in payloads.items():
        snapshot = snapshot_unit_file(path)
        if snapshot != UnitFileSnapshot(payload=payload, mode=0o644):
            raise RuntimeError("REPAIR_STOP_TIMER_FILE_VERIFICATION_FAILED")


def _verify_armed_stop_timers(deadline_seconds: int) -> None:
    if deadline_seconds <= time.time_ns() // 1_000_000_000:
        raise RuntimeError("REPAIR_STOP_TIMER_DEADLINE_ELAPSED")
    timers = (
        PERSISTENT_STOP_UNIT + ".timer", RETRY_TIMER_UNIT + ".timer")
    for unit in timers:
        state = _systemctl(
            "show", unit,
            "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
            "-p", "UnitFileState", "-p", "FragmentPath", "-p", "DropInPaths",
            "-p", "NextElapseUSecRealtime", "-p", "NextElapseUSecMonotonic",
            "-p", "Job")
        properties: dict[str, str] = {}
        for line in state.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in properties:
                raise RuntimeError("REPAIR_STOP_TIMER_VERIFICATION_FAILED")
            properties[key] = value
        if (set(properties) != {
                "LoadState", "ActiveState", "SubState", "UnitFileState",
                "FragmentPath", "DropInPaths",
                "NextElapseUSecRealtime", "NextElapseUSecMonotonic", "Job"} or
                properties["LoadState"] != "loaded" or
                properties["UnitFileState"] != "enabled" or
                properties["ActiveState"] != "active" or
                properties["SubState"] != "waiting" or
                properties["FragmentPath"] != str(SYSTEMD_ROOT / unit) or
                properties["DropInPaths"] or properties["Job"]):
            raise RuntimeError("REPAIR_STOP_TIMER_VERIFICATION_FAILED")
        if unit == PERSISTENT_STOP_UNIT + ".timer":
            # ``systemctl show`` localizes this field (the host currently
            # renders ``CST``), so its text must not be parsed as UTC.  Read
            # the raw D-Bus microsecond value and bind it to the exact
            # integer deadline instead.
            if not properties["NextElapseUSecRealtime"]:
                raise RuntimeError("REPAIR_STOP_TIMER_VERIFICATION_FAILED")
            observed_usec = _systemd_timer_realtime_usec(
                PERSISTENT_STOP_UNIT + ".timer")
            if observed_usec != deadline_seconds * 1_000_000:
                raise RuntimeError("REPAIR_STOP_TIMER_DEADLINE_MISMATCH")
        elif properties["NextElapseUSecMonotonic"] in {"", "0", "n/a"}:
            raise RuntimeError("REPAIR_STOP_TIMER_VERIFICATION_FAILED")
    service_unit = PERSISTENT_STOP_UNIT + ".service"
    service_output = _systemctl(
        "show", service_unit, "-p", "LoadState", "-p", "UnitFileState",
        "-p", "ActiveState", "-p", "FragmentPath", "-p", "DropInPaths",
        "-p", "Job")
    service: dict[str, str] = {}
    for line in service_output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in service:
            raise RuntimeError("REPAIR_STOP_SERVICE_VERIFICATION_FAILED")
        service[key] = value
    if (set(service) != {
            "LoadState", "UnitFileState", "ActiveState", "FragmentPath",
            "DropInPaths", "Job"} or service["LoadState"] != "loaded" or
            service["UnitFileState"] != "static" or
            service["ActiveState"] != "inactive" or
            service["FragmentPath"] != str(SYSTEMD_ROOT / service_unit) or
            service["DropInPaths"] or service["Job"]):
        raise RuntimeError("REPAIR_STOP_SERVICE_VERIFICATION_FAILED")


def arm_stop_timer(
        deadline_seconds: int,
        payloads: dict[Path, bytes] | None = None) -> None:
    expected = payloads or stop_unit_payloads(deadline_seconds)
    if set(expected) != set(generated_stop_unit_paths()):
        raise RuntimeError("REPAIR_STOP_TIMER_PAYLOAD_INVALID")
    for path, payload in expected.items():
        atomic_install(path, payload)
    _systemctl("daemon-reload")
    timers = (
        PERSISTENT_STOP_UNIT + ".timer", RETRY_TIMER_UNIT + ".timer")
    _systemctl("enable", *timers)
    _systemctl("start", *timers)
    _verify_stop_unit_files(expected)
    _verify_armed_stop_timers(deadline_seconds)


def _payload_record(payload: bytes) -> dict[str, object]:
    if len(payload) > PREPARE_SNAPSHOT_MAX_BYTES:
        raise RuntimeError("REPAIR_PREPARE_SNAPSHOT_TOO_LARGE")
    return {
        "base64": base64.b64encode(payload).decode("ascii"),
        "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }


def _decode_payload_record(value: object) -> bytes:
    if (not isinstance(value, dict) or
            set(value) != {"base64", "sha256", "size"} or
            not isinstance(value.get("base64"), str) or
            not isinstance(value.get("sha256"), str) or
            not isinstance(value.get("size"), int) or
            isinstance(value.get("size"), bool) or
            value.get("size", -1) < 0 or
            value.get("size", 0) > PREPARE_SNAPSHOT_MAX_BYTES):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    try:
        payload = base64.b64decode(
            str(value["base64"]).encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID") from error
    if (len(payload) != value["size"] or
            "sha256:" + hashlib.sha256(payload).hexdigest() !=
            value["sha256"] or
            base64.b64encode(payload).decode("ascii") != value["base64"]):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    return payload


def _unit_snapshot_record(snapshot: UnitFileSnapshot) -> dict[str, object]:
    if snapshot.payload is None:
        if snapshot.mode is not None:
            raise RuntimeError("REPAIR_STOP_UNIT_SNAPSHOT_INVALID")
        return {"mode": None, "payload": None}
    if (snapshot.mode is None or snapshot.mode < 0 or
            snapshot.mode > 0o7777 or snapshot.mode & 0o022):
        raise RuntimeError("REPAIR_STOP_UNIT_SNAPSHOT_INVALID")
    return {"mode": snapshot.mode, "payload": _payload_record(snapshot.payload)}


def _decode_unit_snapshot_record(value: object) -> UnitFileSnapshot:
    if (not isinstance(value, dict) or set(value) != {"mode", "payload"}):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    if value["payload"] is None:
        if value["mode"] is not None:
            raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
        return UnitFileSnapshot(None, None)
    if (not isinstance(value["mode"], int) or
            isinstance(value["mode"], bool) or
            value["mode"] < 0 or value["mode"] > 0o7777 or
            value["mode"] & 0o022):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    return UnitFileSnapshot(
        _decode_payload_record(value["payload"]), int(value["mode"]))


def _systemd_snapshot_record(
        snapshot: SystemdUnitSnapshot) -> dict[str, str]:
    return {
        "load_state": snapshot.load_state,
        "unit_file_state": snapshot.unit_file_state,
        "active_state": snapshot.active_state,
    }


def _decode_systemd_snapshot_record(value: object) -> SystemdUnitSnapshot:
    if (not isinstance(value, dict) or
            set(value) != {"load_state", "unit_file_state", "active_state"} or
            not all(isinstance(item, str) for item in value.values())):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    snapshot = SystemdUnitSnapshot(
        str(value["load_state"]), str(value["unit_file_state"]),
        str(value["active_state"]))
    if snapshot.load_state == "not-found":
        if snapshot.unit_file_state or snapshot.active_state != "inactive":
            raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    elif (snapshot.load_state != "loaded" or
            snapshot.active_state not in {"active", "inactive"} or
            snapshot.unit_file_state not in {
                "enabled", "enabled-runtime", "disabled", "static",
                "indirect", "generated", "transient", "alias"}):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    return snapshot


def _prepare_transaction_record(
        campaign_id: str, deadline_seconds: int, duration_seconds: int,
        previous_policy: bytes, previous_env: bytes,
        previous_unit_files: dict[Path, UnitFileSnapshot],
        previous_unit_states: dict[str, SystemdUnitSnapshot],
        target_policy: bytes, target_env: bytes,
        target_unit_files: dict[Path, bytes],
        deployment: DeploymentEvidenceSnapshot) -> dict[str, object]:
    expected_paths = set(generated_stop_unit_paths())
    expected_units = set(stop_runtime_units() + background_timer_units())
    if (set(previous_unit_files) != expected_paths or
            set(target_unit_files) != expected_paths or
            set(previous_unit_states) != expected_units):
        raise RuntimeError("REPAIR_PREPARE_SNAPSHOT_INVALID")
    try:
        target_document = json.loads(target_policy)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("REPAIR_PREPARE_SNAPSHOT_INVALID") from error
    target_is_v5 = (
        isinstance(target_document, dict) and
        target_document.get("schema") == PAPER_POLICY_V5_SCHEMA)
    created_at_ms = time.time_ns() // 1_000_000
    record: dict[str, object] = {
        "schema": (
            PREPARE_TRANSACTION_SCHEMA_V2 if target_is_v5 else
            PREPARE_TRANSACTION_SCHEMA_V1),
        "transaction_id": uuid.uuid4().hex,
        "campaign_id": campaign_id,
        "phase": "SNAPSHOT_READY",
        "created_at_ms": created_at_ms,
        "updated_at_ms": created_at_ms,
        "deadline_seconds": deadline_seconds,
        "duration_seconds": duration_seconds,
        "previous_policy": _payload_record(previous_policy),
        "previous_env": _payload_record(previous_env),
        "previous_unit_files": {
            str(path): _unit_snapshot_record(snapshot)
            for path, snapshot in previous_unit_files.items()
        },
        "previous_unit_states": {
            unit: _systemd_snapshot_record(snapshot)
            for unit, snapshot in previous_unit_states.items()
        },
        "target_policy": _payload_record(target_policy),
        "target_env": _payload_record(target_env),
        "target_unit_files": {
            str(path): _unit_snapshot_record(UnitFileSnapshot(payload, 0o644))
            for path, payload in target_unit_files.items()
        },
        "deployment_binding": _deployment_binding_record(deployment),
    }
    if target_is_v5:
        assert isinstance(target_document, dict)
        record["v5_policy_binding"] = _v5_policy_binding_record(
            target_document)
    return record


def _validate_prepare_transaction(value: object) -> dict[str, object]:
    required = {
        "schema", "transaction_id", "campaign_id", "phase",
        "created_at_ms", "updated_at_ms", "deadline_seconds",
        "duration_seconds", "previous_policy", "previous_env",
        "previous_unit_files", "previous_unit_states", "target_policy",
        "target_env", "target_unit_files", "deployment_binding",
    }
    if not isinstance(value, dict):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    schema = value.get("schema")
    expected_fields = (
        required if schema == PREPARE_TRANSACTION_SCHEMA_V1 else
        required | {"v5_policy_binding"}
        if schema == PREPARE_TRANSACTION_SCHEMA_V2 else None)
    if (expected_fields is None or set(value) != expected_fields or
            not isinstance(value.get("transaction_id"), str) or
            re.fullmatch(r"[0-9a-f]{32}", str(value.get("transaction_id")))
                is None or
            not isinstance(value.get("campaign_id"), str) or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,255}",
                         str(value.get("campaign_id"))) is None or
            value.get("phase") not in PREPARE_TRANSACTION_PHASES):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    for key in ("created_at_ms", "updated_at_ms", "deadline_seconds",
                "duration_seconds"):
        if (not isinstance(value.get(key), int) or
                isinstance(value.get(key), bool) or value.get(key, 0) <= 0):
            raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    if (value["updated_at_ms"] < value["created_at_ms"] or
            value["deadline_seconds"] * 1000 <= value["created_at_ms"]):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    previous_policy = _decode_payload_record(value["previous_policy"])
    target_policy = _decode_payload_record(value["target_policy"])
    _decode_payload_record(value["previous_env"])
    _decode_payload_record(value["target_env"])
    try:
        previous_document = json.loads(previous_policy)
        target_document = json.loads(target_policy)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID") from error
    if (not isinstance(previous_document, dict) or
            previous_document.get("paper_only") is not True or
            previous_document.get("live_authorized") is not False or
            previous_document.get("enabled") is not False or
            previous_document.get("mutations_authorized") is not False or
            not isinstance(target_document, dict) or
            target_document.get("campaign_id") != value["campaign_id"] or
            target_document.get("paper_only") is not True or
            target_document.get("live_authorized") is not False or
            target_document.get("enabled") is not True or
            target_document.get("mutations_authorized") is not True or
            target_document.get("expires_at_ms") !=
                value["deadline_seconds"] * 1000):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    target_is_v5 = target_document.get("schema") == PAPER_POLICY_V5_SCHEMA
    if target_is_v5 != (schema == PREPARE_TRANSACTION_SCHEMA_V2):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    if target_is_v5:
        try:
            _validate_v5_prepare_policy(
                previous_document, raw=previous_policy,
                require_disabled=True)
            _validate_v5_prepare_policy(target_document, raw=target_policy)
        except RuntimeError as error:
            raise RuntimeError(
                "REPAIR_PREPARE_TRANSACTION_INVALID") from error
        if target_document["admission_mode"] == "external-p1-finalized":
            expected_target = dict(previous_document)
            expected_target["enabled"] = True
            expected_target["mutations_authorized"] = True
            if (target_document != expected_target or
                    value["duration_seconds"] * 1000 !=
                        PAPER_POLICY_V5_EXTERNAL_DURATION_MS or
                    value["duration_seconds"] * 1000 !=
                        int(target_document["expires_at_ms"]) -
                        int(target_document["valid_after_ms"])):
                raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
        else:
            mutable_local_fields = {
                "campaign_id", "enabled", "mutations_authorized",
                "strategy_id", "strategy_version", "strategy_sha256",
                "valid_after_ms", "expires_at_ms", "max_cycles",
                "max_holding_ms", "source_baseline_sha256",
                "deployment_evidence_file_sha256",
                "deployment_evidence_body_sha256",
                "deployment_install_transaction_id",
            }
            for field in PAPER_POLICY_V5_LOCAL_FIELDS - mutable_local_fields:
                if target_document[field] != previous_document[field]:
                    raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
            created_at_ms = int(value["created_at_ms"])
            valid_after_ms = int(target_document["valid_after_ms"])
            if abs(created_at_ms - valid_after_ms) > 5_000:
                raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
        if (value["v5_policy_binding"] !=
                _v5_policy_binding_record(target_document)):
            raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    previous_units = value.get("previous_unit_files")
    target_units = value.get("target_unit_files")
    unit_states = value.get("previous_unit_states")
    expected_paths = {str(path) for path in generated_stop_unit_paths()}
    expected_units = set(stop_runtime_units() + background_timer_units())
    if (not isinstance(previous_units, dict) or
            set(previous_units) != expected_paths or
            not isinstance(target_units, dict) or
            set(target_units) != expected_paths or
            not isinstance(unit_states, dict) or
            set(unit_states) != expected_units):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    for raw in previous_units.values():
        _decode_unit_snapshot_record(raw)
    for raw in target_units.values():
        snapshot = _decode_unit_snapshot_record(raw)
        if snapshot.payload is None or snapshot.mode != 0o644:
            raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    for raw in unit_states.values():
        _decode_systemd_snapshot_record(raw)
    deployment_binding = _validate_deployment_binding(
        value["deployment_binding"])
    if (target_document.get("source_baseline_sha256") !=
            deployment_binding["source_baseline_sha256"] or
            target_document.get("deployment_evidence_file_sha256") !=
            deployment_binding["evidence_file_sha256"] or
            target_document.get("deployment_evidence_body_sha256") !=
            deployment_binding["evidence_body_sha256"] or
            target_document.get("deployment_install_transaction_id") !=
            deployment_binding["install_transaction_id"]):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    return value


def _persist_prepare_transaction(
        record: dict[str, object], *, create: bool = False) -> None:
    payload = canonical(_validate_prepare_transaction(record))
    if len(payload) > PREPARE_TRANSACTION_MAX_BYTES:
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_TOO_LARGE")
    if create:
        try:
            os.lstat(PREPARE_TRANSACTION_PATH)
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError("REPAIR_PREPARE_TRANSACTION_RESIDUE")
        atomic_install(PREPARE_TRANSACTION_PATH, payload, 0o600)
    else:
        atomic_write(PREPARE_TRANSACTION_PATH, payload)


def _load_prepare_transaction() -> dict[str, object] | None:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(PREPARE_TRANSACTION_PATH, flags)
    except FileNotFoundError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                metadata.st_size < 2 or
                metadata.st_size > PREPARE_TRANSACTION_MAX_BYTES):
            raise RuntimeError("REPAIR_PREPARE_TRANSACTION_PATH_UNSAFE")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                raise RuntimeError("REPAIR_PREPARE_TRANSACTION_TRUNCATED")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RuntimeError("REPAIR_PREPARE_TRANSACTION_GREW")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID") from error
    return _validate_prepare_transaction(value)


def _remove_prepare_transaction() -> None:
    metadata = os.lstat(PREPARE_TRANSACTION_PATH)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_PATH_UNSAFE")
    os.unlink(PREPARE_TRANSACTION_PATH)
    directory = os.open(
        PREPARE_TRANSACTION_PATH.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _advance_prepare_transaction(
        record: dict[str, object], phase: str) -> None:
    if phase not in PREPARE_TRANSACTION_PHASES:
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_PHASE_INVALID")
    current = str(record.get("phase"))
    if phase != "ROLLBACK_REQUIRED":
        ordered = PREPARE_TRANSACTION_PHASES[:-1]
        if (current not in ordered or phase not in ordered or
                ordered.index(phase) != ordered.index(current) + 1):
            raise RuntimeError("REPAIR_PREPARE_TRANSACTION_PHASE_INVALID")
    record["phase"] = phase
    record["updated_at_ms"] = max(
        int(record["created_at_ms"]), time.time_ns() // 1_000_000)
    _persist_prepare_transaction(record)


def _transaction_unit_files(
        record: dict[str, object], key: str) -> dict[Path, UnitFileSnapshot]:
    value = record[key]
    if not isinstance(value, dict):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    return {
        Path(path): _decode_unit_snapshot_record(snapshot)
        for path, snapshot in value.items()
    }


def _transaction_unit_states(
        record: dict[str, object]) -> dict[str, SystemdUnitSnapshot]:
    value = record["previous_unit_states"]
    if not isinstance(value, dict):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    return {
        unit: _decode_systemd_snapshot_record(snapshot)
        for unit, snapshot in value.items()
    }


def _verify_prepare_target(
        record: dict[str, object], *, require_policy: bool) -> None:
    target_policy = _decode_payload_record(record["target_policy"])
    try:
        target_policy_document = json.loads(target_policy)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "REPAIR_PREPARE_TRANSACTION_INVALID") from error
    _require_p1_bound_prepare_policy(target_policy_document)
    _validate_v5_prepare_policy(target_policy_document, raw=target_policy)
    if (record.get("v5_policy_binding") !=
            _v5_policy_binding_record(target_policy_document)):
        raise RuntimeError("REPAIR_PREPARE_TRANSACTION_INVALID")
    expected_deployment = _validate_deployment_binding(
        record["deployment_binding"])
    try:
        current_deployment = _deployment_binding_record(
            _load_local_paper_deployment_evidence())
    except (OSError, RuntimeError) as error:
        raise RuntimeError(
            "REPAIR_PREPARE_TARGET_DEPLOYMENT_DRIFTED") from error
    if current_deployment != expected_deployment:
        raise RuntimeError("REPAIR_PREPARE_TARGET_DEPLOYMENT_DRIFTED")
    target_env = _decode_payload_record(record["target_env"])
    if _read_stable_root_file(
            AGENT_ENV_PATH, "REPAIR_PREPARE_TARGET_ENV_UNSAFE") != target_env:
        raise RuntimeError("REPAIR_PREPARE_TARGET_ENV_DRIFTED")
    target_units = _transaction_unit_files(record, "target_unit_files")
    _verify_stop_unit_files({
        path: snapshot.payload
        for path, snapshot in target_units.items()
        if snapshot.payload is not None
    })
    _verify_armed_stop_timers(int(record["deadline_seconds"]))
    if (require_policy and _read_stable_root_file(
            POLICY_PATH, "REPAIR_PREPARE_TARGET_POLICY_UNSAFE") !=
            target_policy):
        raise RuntimeError("REPAIR_PREPARE_TARGET_POLICY_DRIFTED")


def _fence_current_prepare_policy() -> None:
    raw = _read_stable_root_file(
        POLICY_PATH, "REPAIR_PREPARE_RECOVERY_POLICY_UNSAFE")
    try:
        policy = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("REPAIR_PREPARE_RECOVERY_POLICY_INVALID") from error
    if (not isinstance(policy, dict) or
            policy.get("schema") not in {
                "hepta.ib-paper-campaign-policy.v4",
                PAPER_POLICY_V5_SCHEMA,
            } or
            policy.get("paper_only") is not True or
            policy.get("live_authorized") is not False):
        raise RuntimeError("REPAIR_PREPARE_RECOVERY_POLICY_INVALID")
    if (policy.get("enabled") is False and
            policy.get("mutations_authorized") is False):
        return
    policy["enabled"] = False
    policy["mutations_authorized"] = False
    sealed = canonical(policy)
    atomic_write(POLICY_PATH, sealed)
    if _read_stable_root_file(
            POLICY_PATH, "REPAIR_PREPARE_RECOVERY_POLICY_UNSAFE") != sealed:
        raise RuntimeError("REPAIR_PREPARE_RECOVERY_POLICY_FENCE_FAILED")


def _rollback_prepare_transaction(
        record: dict[str, object], *, require_safe_boundary: bool) -> None:
    _fence_current_prepare_policy()
    if require_safe_boundary:
        try:
            _require_deny_all()
            _require_no_session_or_permit_residue()
            _require_runtime_inactive()
        except BaseException as error:
            raise RuntimeError(
                "REPAIR_PREPARE_RECOVERY_BOUNDARY_UNSAFE") from error
    rollback_prepare(
        _decode_payload_record(record["previous_policy"]),
        _decode_payload_record(record["previous_env"]),
        _transaction_unit_files(record, "previous_unit_files"),
        _transaction_unit_states(record))
    if (_read_stable_root_file(
            POLICY_PATH, "REPAIR_PREPARE_RECOVERY_POLICY_UNSAFE") !=
            _decode_payload_record(record["previous_policy"]) or
            _read_stable_root_file(
                AGENT_ENV_PATH, "REPAIR_PREPARE_RECOVERY_ENV_UNSAFE") !=
            _decode_payload_record(record["previous_env"])):
        raise RuntimeError("REPAIR_PREPARE_RECOVERY_RESTORE_FAILED")
    _remove_prepare_transaction()


def reconcile_prepare_transaction_locked() -> str | None:
    try:
        record = _load_prepare_transaction()
    except BaseException as error:
        # A torn or tampered WAL cannot be trusted for restoration, but it can
        # never be allowed to leave entry authority enabled.
        _fence_current_prepare_policy()
        raise RuntimeError(
            "REPAIR_PREPARE_TRANSACTION_UNRECOVERABLE") from error
    if record is None:
        return None
    if record.get("schema") == PREPARE_TRANSACTION_SCHEMA_V2:
        # A reboot or process restart never resumes or ratifies PAPER entry
        # authority.  Even a byte-complete v5 target is fenced and restored;
        # an operator must begin a fresh, continuously observed transaction.
        _rollback_prepare_transaction(record, require_safe_boundary=True)
        return "ROLLED_BACK"
    target_policy = _decode_payload_record(record["target_policy"])
    if _read_stable_root_file(
            POLICY_PATH, "REPAIR_PREPARE_RECOVERY_POLICY_UNSAFE") == target_policy:
        try:
            _verify_prepare_target(record, require_policy=True)
        except BaseException:
            _rollback_prepare_transaction(record, require_safe_boundary=True)
            return "ROLLED_BACK"
        _remove_prepare_transaction()
        return "COMMITTED"
    _rollback_prepare_transaction(record, require_safe_boundary=True)
    return "ROLLED_BACK"


def commit_campaign(
        source_policy: dict[str, object], strategy_digest: str,
        deadline_seconds: int, duration_seconds: int, max_cycles: int,
        auth_generation: str | None, auth_profile_id: str | None,
) -> tuple[str, dict[str, object]]:
    with campaign_lifecycle_locks():
        if (not isinstance(max_cycles, int) or isinstance(max_cycles, bool) or
                not 1 <= max_cycles <= 25_000):
            raise RuntimeError("REPAIR_CAMPAIGN_CYCLE_LIMIT_INVALID")
        reconcile_prepare_transaction_locked()
        if _load_deployment_evidence_transaction() is not None:
            raise RuntimeError(
                "REPAIR_DEPLOYMENT_EVIDENCE_TRANSACTION_RESIDUE")
        current_policy_raw = _read_stable_root_file(
            POLICY_PATH, "REPAIR_SOURCE_POLICY_PATH_UNSAFE")
        current_policy = json.loads(current_policy_raw)
        if current_policy != source_policy:
            raise RuntimeError("REPAIR_SOURCE_POLICY_DRIFTED")
        _require_p1_bound_prepare_policy(current_policy)
        _validate_v5_prepare_policy(
            current_policy, raw=current_policy_raw, require_disabled=True)
        local_only = current_policy["admission_mode"] == "local-only"
        if ((duration_seconds < 300 or
             duration_seconds > 24 * 60 * 60) or
                (not local_only and
                 duration_seconds * 1000 !=
                    PAPER_POLICY_V5_EXTERNAL_DURATION_MS) or
                max_cycles < (2 if local_only else 1) or
                max_cycles > (PAPER_POLICY_V5_MAX_CYCLES
                              if local_only else 1)):
            raise RuntimeError("REPAIR_CAMPAIGN_POLICY_PIN_MISMATCH")
        if not local_only:
            pinned_valid_after_ms = int(current_policy["valid_after_ms"])
            pinned_expires_at_ms = int(current_policy["expires_at_ms"])
            if (deadline_seconds * 1000 != pinned_expires_at_ms or
                    duration_seconds * 1000 !=
                        pinned_expires_at_ms - pinned_valid_after_ms or
                    max_cycles != current_policy["max_cycles"]):
                raise RuntimeError("REPAIR_CAMPAIGN_POLICY_PIN_MISMATCH")
        current_strategy_digest = "sha256:" + hashlib.sha256(
            STRATEGY_PATH.read_bytes()).hexdigest()
        if (current_strategy_digest != strategy_digest or
                (not local_only and current_strategy_digest !=
                    current_policy["strategy_sha256"])):
            raise RuntimeError("REPAIR_STRATEGY_DRIFTED")
        deployment = _load_local_paper_deployment_evidence()
        deployed_source_baseline = _bound_deployment_source_baseline(
            current_policy, deployment)
        deployment_binding = _deployment_binding_record(deployment)
        if (not local_only and
                (deployed_source_baseline !=
                    current_policy["source_baseline_sha256"] or
                current_policy["deployment_evidence_file_sha256"] !=
                    deployment_binding["evidence_file_sha256"] or
                current_policy["deployment_evidence_body_sha256"] !=
                    deployment_binding["evidence_body_sha256"] or
                current_policy["deployment_install_transaction_id"] !=
                    deployment_binding["install_transaction_id"])):
            raise RuntimeError("REPAIR_SOURCE_POLICY_DEPLOYMENT_MISMATCH")
        remaining_seconds = (
            deadline_seconds -
            (time.time_ns() + 999_999_999) // 1_000_000_000)
        if (remaining_seconds > duration_seconds or
                (remaining_seconds < 300 if local_only else
                 remaining_seconds <= 0)):
            raise RuntimeError("REPAIR_CAMPAIGN_DEADLINE_DRIFTED")
        require_fresh_campaign_admission(
            current_policy,
            verified_local_disabled_seed=(
                local_only and current_policy ==
                    _v5_local_seed_from_deployment(deployment)))
        if local_only:
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            campaign_id = (
                "local-ai-paper-mkt-model-exit-" + stamp + "-" +
                uuid.uuid4().hex[:12])
        else:
            campaign_id = str(current_policy["campaign_id"])
        if any(STATE_ROOT.glob("*" + campaign_id + "*")):
            raise RuntimeError("REPAIR_CAMPAIGN_ID_ALREADY_USED")
        policy = dict(current_policy)
        if local_only:
            policy["campaign_id"] = campaign_id
            policy["order_type"] = "MKT"
            policy["tif"] = "DAY"
            policy["strategy_id"] = STRATEGY_ID
            policy["strategy_version"] = STRATEGY_VERSION
            policy["strategy_sha256"] = strategy_digest
            policy["source_baseline_sha256"] = deployed_source_baseline
            policy["deployment_evidence_file_sha256"] = (
                deployment_binding["evidence_file_sha256"])
            policy["deployment_evidence_body_sha256"] = (
                deployment_binding["evidence_body_sha256"])
            policy["deployment_install_transaction_id"] = (
                deployment_binding["install_transaction_id"])
            policy["valid_after_ms"] = (
                deadline_seconds - duration_seconds) * 1000
            policy["expires_at_ms"] = deadline_seconds * 1000
            policy["max_cycles"] = max_cycles
            policy["max_holding_ms"] = 0
        policy["enabled"] = True
        policy["mutations_authorized"] = True
        _validate_v5_prepare_policy(policy)
        previous_policy = current_policy_raw
        previous_env = _read_stable_root_file(
            AGENT_ENV_PATH, "REPAIR_AGENT_ENV_PATH_UNSAFE")
        previous_stop_unit_files = snapshot_stop_unit_files()
        previous_unit_states = snapshot_systemd_unit_states()
        target_policy = canonical(policy)
        target_env = render_agent_env(
            campaign_id, strategy_digest, auth_generation, auth_profile_id,
            previous_env, strategy_id=str(policy["strategy_id"]),
            strategy_version=str(policy["strategy_version"]))
        target_stop_unit_files = stop_unit_payloads(deadline_seconds)
        transaction = _prepare_transaction_record(
            campaign_id, deadline_seconds, duration_seconds,
            previous_policy, previous_env, previous_stop_unit_files,
            previous_unit_states, target_policy, target_env,
            target_stop_unit_files, deployment)
        # The first check guarded all admission computations. Reopen the
        # finalized handoff and restored profile once more at the last point
        # before the prepare WAL can exist. The WAL then binds the exact
        # target policy until the transaction commits or rolls back.
        if not local_only:
            _require_external_p1_boundary(current_policy)
        _persist_prepare_transaction(transaction, create=True)
        try:
            keep_background_timers_stopped()
            _advance_prepare_transaction(
                transaction, "BACKGROUND_TIMERS_STOPPED")
            disarm_old_stop_units(previous_unit_states)
            _advance_prepare_transaction(
                transaction, "OLD_STOP_UNITS_DISARMED")
            atomic_write(AGENT_ENV_PATH, target_env)
            _advance_prepare_transaction(transaction, "TARGET_ENV_INSTALLED")
            arm_stop_timer(deadline_seconds, target_stop_unit_files)
            _verify_prepare_target(transaction, require_policy=False)
            _advance_prepare_transaction(
                transaction, "TARGET_TIMERS_VERIFIED")
            # Re-hash both the independent evidence and every installed file
            # after staging, immediately before publishing entry authority.
            _require_deployment_snapshot_unchanged(deployment)
            # Entry authority is the commit marker. It is published only after
            # the campaign environment and both durable end-flat timers are
            # installed, active, waiting, and byte-for-byte verified.
            atomic_write(POLICY_PATH, target_policy)
            _verify_prepare_target(transaction, require_policy=True)
            _advance_prepare_transaction(transaction, "POLICY_COMMITTED")
            _remove_prepare_transaction()
        except Exception as error:
            try:
                try:
                    _advance_prepare_transaction(
                        transaction, "ROLLBACK_REQUIRED")
                except BaseException:
                    pass
                _rollback_prepare_transaction(
                    transaction, require_safe_boundary=False)
            except BaseException as rollback_error:
                raise rollback_error from error
            raise
        return campaign_id, policy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--migrate-legacy-hsl5-paper-leases", action="store_true",
        help=("retire exact terminal ownerless HSL5 PAPER leases while "
              "DENY_ALL and all campaign runtimes remain stopped"))
    parser.add_argument(
        "--record-deployment-evidence", action="store_true",
        help=("seal the non-authorizing root-owned deployment receipt after "
              "an offline install transaction; does not prepare a campaign"))
    parser.add_argument("--certified-install-closure", type=Path)
    parser.add_argument("--certified-install-closure-sha256")
    lifetime = parser.add_mutually_exclusive_group()
    lifetime.add_argument(
        "--duration-seconds", type=int,
        help="finite PAPER campaign lifetime (300 seconds to 24 hours)")
    lifetime.add_argument(
        "--expires-at-seconds", type=int,
        help="absolute UTC epoch deadline, preserving an existing test window")
    parser.add_argument(
        "--max-cycles", type=int, default=720,
        help=("total bounded cycle budget (local-only 2 to 720; "
              "external-p1-finalized exactly 1)"))
    parser.add_argument(
        "--auth-generation",
        help="non-secret generation label for an explicitly probed auth change")
    parser.add_argument(
        "--auth-profile-id",
        help="non-secret OpenClaw auth profile id pinned by the PAPER agent")
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("REPAIR_ROOT_REQUIRED")
    deployment_values = (
        arguments.certified_install_closure,
        arguments.certified_install_closure_sha256,
    )
    if arguments.migrate_legacy_hsl5_paper_leases:
        if (arguments.record_deployment_evidence or
                any(value is not None for value in deployment_values) or
                arguments.duration_seconds is not None or
                arguments.expires_at_seconds is not None or
                arguments.auth_generation is not None or
                arguments.auth_profile_id is not None or
                arguments.max_cycles != 720):
            raise RuntimeError(
                "REPAIR_LEGACY_LEASE_CLEANUP_ARGUMENTS_INVALID")
        receipt = migrate_legacy_hsl5_paper_leases()
        print(
            "REPAIR_LEGACY_HSL5_PAPER_LEASES_RETIRED "
            f"retired_records={receipt['retired_records']} "
            f"pre_store_sha256={receipt['pre_store_sha256']} "
            f"post_store_sha256={receipt['post_store_sha256']} "
            "paper_authorized=false live_authorized=false "
            "mutation_authorized=false",
            flush=True)
        return 0
    if arguments.record_deployment_evidence:
        if (any(value is None for value in deployment_values) or
                arguments.duration_seconds is not None or
                arguments.expires_at_seconds is not None or
                arguments.auth_generation is not None or
                arguments.auth_profile_id is not None or
                arguments.max_cycles != 720):
            raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_ARGUMENTS_INVALID")
        snapshot = record_local_paper_deployment_evidence(
            certified_closure_path=Path(
                arguments.certified_install_closure),
            certified_closure_file_sha256=str(
                arguments.certified_install_closure_sha256))
        binding = _deployment_binding_record(snapshot)
        print(
            "REPAIR_DEPLOYMENT_EVIDENCE_READY "
            f"evidence_file_sha256={binding['evidence_file_sha256']} "
            f"evidence_body_sha256={binding['evidence_body_sha256']} "
            f"source_baseline_sha256={binding['source_baseline_sha256']} "
            f"install_transaction_id={binding['install_transaction_id']} "
            "paper_authorized=false live_authorized=false "
            "mutation_authorized=false",
            flush=True)
        return 0
    if any(value is not None for value in deployment_values):
        raise RuntimeError("REPAIR_DEPLOYMENT_EVIDENCE_ARGUMENTS_INVALID")
    current_seconds = (time.time_ns() + 999_999_999) // 1_000_000_000
    if arguments.expires_at_seconds is not None:
        deadline_seconds = arguments.expires_at_seconds
        duration_seconds = deadline_seconds - current_seconds
    else:
        duration_seconds = arguments.duration_seconds or 86_400
        deadline_seconds = current_seconds + duration_seconds
    max_cycles = arguments.max_cycles
    auth_generation = arguments.auth_generation
    auth_profile_id = arguments.auth_profile_id
    if duration_seconds < 300:
        raise RuntimeError("REPAIR_CAMPAIGN_DURATION_INVALID")
    if max_cycles < 1:
        raise RuntimeError("REPAIR_CAMPAIGN_CYCLE_LIMIT_INVALID")
    if (auth_generation is not None and
            (len(auth_generation) < 8 or len(auth_generation) > 128 or
             not auth_generation[0].isalnum() or
             any(character not in
                 "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
                 for character in auth_generation))):
        raise RuntimeError("REPAIR_AUTH_GENERATION_INVALID")
    if ((auth_generation is None) != (auth_profile_id is None)):
        raise RuntimeError("REPAIR_AUTH_BINDING_PAIR_REQUIRED")
    if (auth_profile_id is not None and
            (len(auth_profile_id) < 3 or len(auth_profile_id) > 256 or
             not auth_profile_id[0].isalnum() or
             any(character not in
                 "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:@+-"
                 for character in auth_profile_id))):
        raise RuntimeError("REPAIR_AUTH_PROFILE_INVALID")
    # Reconcile before parsing the strategy or source policy. A reboot-torn
    # transaction must be fenced/restored even if either later input drifted.
    with campaign_lifecycle_locks():
        reconcile_prepare_transaction_locked()
    policy_raw = _read_stable_root_file(
        POLICY_PATH, "REPAIR_SOURCE_POLICY_PATH_UNSAFE")
    policy = json.loads(policy_raw)
    _require_p1_bound_prepare_policy(policy)
    _validate_v5_prepare_policy(
        policy, raw=policy_raw, require_disabled=True)
    local_only = policy["admission_mode"] == "local-only"
    if not local_only:
        pinned_deadline_seconds = int(policy["expires_at_ms"]) // 1000
        pinned_duration_seconds = (
            int(policy["expires_at_ms"]) -
            int(policy["valid_after_ms"])) // 1000
        if (int(policy["expires_at_ms"]) % 1000 != 0 or
                int(policy["valid_after_ms"]) % 1000 != 0):
            raise RuntimeError("REPAIR_CAMPAIGN_DURATION_INVALID")
        if arguments.expires_at_seconds is not None:
            if deadline_seconds != pinned_deadline_seconds:
                raise RuntimeError("REPAIR_CAMPAIGN_DURATION_INVALID")
        elif duration_seconds != pinned_duration_seconds:
            raise RuntimeError("REPAIR_CAMPAIGN_DURATION_INVALID")
        # External-P1 supplies a signed window; the CLI only asserts it.
        deadline_seconds = pinned_deadline_seconds
        duration_seconds = pinned_duration_seconds
    if (duration_seconds < 300 or duration_seconds > 24 * 60 * 60 or
            (not local_only and
             duration_seconds * 1000 !=
                PAPER_POLICY_V5_EXTERNAL_DURATION_MS)):
        raise RuntimeError("REPAIR_CAMPAIGN_DURATION_INVALID")
    if (max_cycles < (2 if local_only else 1) or
            max_cycles > (PAPER_POLICY_V5_MAX_CYCLES if local_only else 1) or
            max_cycles != policy["max_cycles"]):
        raise RuntimeError("REPAIR_CAMPAIGN_CYCLE_LIMIT_INVALID")
    strategy_raw = STRATEGY_PATH.read_bytes()
    strategy = json.loads(strategy_raw)
    if (not isinstance(strategy, dict) or
            strategy.get("schema") !=
                "hepta.local-ai-paper-strategy.v3" or
            strategy.get("version") != 3 or
            strategy.get("paper_only") is not True or
            strategy.get("live_authorized") is not False or
            strategy.get("order_type") != ("MKT" if local_only else "LMT") or
            strategy.get("max_order_quantity") != policy["max_quantity"] or
            strategy.get("max_holding_seconds") != 0 or
            strategy.get("exit_mode") != "MODEL_REVERSAL" or
            strategy.get("rate_limit_fail_closed") is not True or
            strategy.get("emergency_reduce_only_recovery") is not True or
            strategy.get("auth_rearm_required_after_rate_limit") is not True or
            strategy.get("campaign_end_flat_required") is not True):
        raise RuntimeError("REPAIR_STRATEGY_BOUNDARY_INVALID")
    strategy_digest = "sha256:" + hashlib.sha256(strategy_raw).hexdigest()
    if ((not local_only and strategy_digest != policy["strategy_sha256"]) or
            (not local_only and
             str(strategy["version"]) != policy["strategy_version"])):
        raise RuntimeError("REPAIR_STRATEGY_DRIFTED")
    campaign_id, policy = commit_campaign(
        policy, strategy_digest, deadline_seconds, duration_seconds,
        max_cycles, auth_generation, auth_profile_id)
    print(
        "REPAIR_CAMPAIGN_READY "
        f"campaign_id={campaign_id} expires_at_ms={policy['expires_at_ms']} "
        f"duration_seconds={duration_seconds} max_cycles={max_cycles} "
        f"source_baseline_sha256={policy['source_baseline_sha256']} "
        f"acceptance_cycles_reserved=1 strategy_cycle_budget={max_cycles - 1} "
        "acceptance_performance_included=false "
        f"paper_only=true live_authorized=false "
        f"order_type={policy['order_type']} tif=DAY "
        "background_timers_stopped=true manual_start_required=true",
        flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired,
            json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
