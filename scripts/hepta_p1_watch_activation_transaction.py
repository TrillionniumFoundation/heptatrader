#!/usr/bin/env -S /usr/bin/python3.12 -I -S

"""Crash-closed round114 WATCH Gateway activation transaction.

This helper has one fixed domain, consumes one fixed offline profile receipt,
and requires the exact round95 success and terminal failure as predecessors.
It never provisions WATCH authority and never starts PAPER/LIVE.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence


ROOT_UID = 0
ROOT_GID = 0
WATCH_UID = 2104
WATCH_GID = 2104
PAPER_CONTROL_GID = 2121
ROUND = 114
PREDECESSOR_ROUND = 95
ANCESTOR_ROUND = 86
DOMAIN = "alpha"

PROFILE_PATH = Path("/etc/heptatrader/trust-domains/alpha.env")
PROFILE_RECEIPT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/round114-generation22.json")
PROFILE_RECEIPT_STAGING_PATH = PROFILE_RECEIPT_PATH.with_name(
    ".round114-generation22.json.hepta-p1-round114.tmp")
PROFILE_TRANSITION_RECEIPT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/"
    "round114-dormant-paper-to-watch.json")
PROFILE_TRANSITION_RECEIPT_SCHEMA = (
    "hepta.p1-watch-profile-dormant-paper-transition-receipt.v2")
PROFILE_TRANSITION_RECEIPT_VERSION = 2
PROFILE_TRANSITION_RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain", "transition_token",
    "started_at_ms", "finished_at_ms", "target_path", "backup_path",
    "retained_target_path", "receipt_staging_path", "target_before",
    "target_after", "target_final", "backup", "retained_target",
    "preimage_evidence", "predecessor_profile_receipt", "preflight_before",
    "preflight_after", "preflight_final", "profile_content_changed",
    "target_written", "target_replaced", "services_started",
    "services_stopped", "services_restarted", "campaign_launched",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "shadow_install_evidence", "body_sha256",
})
PROFILE_TRANSITION_PREIMAGE_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-backups/"
    "round114-dormant-paper-to-watch/preimage-evidence.json")
PROFILE_TRANSITION_PREIMAGE_SCHEMA = (
    "hepta.p1-watch-profile-transition-preimage-evidence.v1")
PROFILE_TRANSITION_PREIMAGE_VERSION = 1
PROFILE_TRANSITION_PREIMAGE_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain", "transition_token",
    "created_at_ms", "target_before", "backup", "predecessor_profile_receipt",
    "preflight", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "shadow_install_evidence",
    "body_sha256",
})
LEGACY_PROFILE_RECEIPT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/round86.json")
LEGACY_PROFILE_BACKUP_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-backups/round86/alpha.env")
LEGACY_PROFILE_RETAINED_TARGET_PATH = PROFILE_PATH.with_name(
    ".alpha.env.hepta-p1-round86.tmp")
SHADOW_INSTALL_LOCK_PATH = Path("/var/lib/hepta/.shadow-runtime-install.lock")
SHADOW_CURRENT_INSTALL_POINTER_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-state/current-install-v1.json")
SHADOW_INSTALL_RECEIPT_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-receipts/"
    "hepta-p1-round114-generation22-passive.json")
SHADOW_INSTALL_MANIFEST_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-artifacts/"
    "hepta-p1-round114-generation22-shadow-runtime.manifest.json")
SHADOW_INSTALL_BACKUP_ROOT = Path(
    "/var/lib/hepta/shadow-runtime-backups/hepta-p1-round114-generation22-passive")
EXPECTED_SHADOW_INSTALL_GENERATION = 22
EXPECTED_PREDECESSOR_SHADOW_INSTALL_GENERATION = 21
EXPECTED_PREDECESSOR_CURRENT_INSTALL_POINTER_FILE_SHA256 = (
    "sha256:2beeb507fcafbbfc2c93d2e4756fddf0b27e9872733ff97d28af47006461d406")
ACTIVATION_RECEIPT_PATH = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-round114-receipt-v4.json")
LEGACY_ACTIVATION_RECEIPT_PATH = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-receipt-v1.json")
LEGACY_ACTIVATION_RECEIPT_V2_PATH = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-receipt-v2.json")
PREDECESSOR_ACTIVATION_RECEIPT_PATH = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-round95-receipt-v3.json")
PREDECESSOR_ACTIVATION_RECEIPT_FILE_SHA256 = (
    "sha256:c4b92e92bcdd55792e32fbe7f28a5399617352f7469e6661a09148efe6bdd5f3")
PREDECESSOR_ACTIVATION_RECEIPT_BODY_SHA256 = (
    "sha256:2d433239397a9820af0080628f424f5b6985d01ed9b5748a2064f903e1a2ed80")
FAILED_RECEIPT_PATH = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-round114-failed-receipt-v3.json")
FAILED_RECEIPT_REPLACEMENT_PATH = FAILED_RECEIPT_PATH.with_name(
    ".p1-watch-activation-round114-failed-receipt-v3.replacement")
FAILED_RECEIPT_PENDING_ARCHIVE_PATH = FAILED_RECEIPT_PATH.with_name(
    "p1-watch-activation-round114-pending-receipt-v3.json")
PREDECESSOR_FAILED_RECEIPT_PATH = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-round95-failed-receipt-v2.json")
PREDECESSOR_FAILED_RECEIPT_FILE_SHA256 = (
    "sha256:860cf9ab2005ebcc2f6d5a83e931ebe18e6a5764f502a503aa305fb009bff55d")
PREDECESSOR_FAILED_RECEIPT_BODY_SHA256 = (
    "sha256:a3097ec265d66cb6ad99db8555b777c3fd0009cbe7f85e453a1d7a8f126174ed")
PREDECESSOR_JOURNAL_ROOT = Path(
    "/var/lib/heptatrader/p1-watch-activation/round95/journal")
PREDECESSOR_JOURNAL_SHA256 = (
    "sha256:7d18a341a2e6ae322acd1b477f6287686af090e4a35716dc496bb8ab0f1a698e")
ANCESTOR_FAILED_RECEIPT_PATH = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-failed-receipt-v1.json")
ANCESTOR_FAILED_RECEIPT_FILE_SHA256 = (
    "sha256:957559d6a0ae12433c3ec59aee5bc4707c4c8dda2af74a0babed8da65d7dba15")
ANCESTOR_FAILED_RECEIPT_BODY_SHA256 = (
    "sha256:22abc6d6316e9a0576e782957c886033acc50c1e97ba97d5a7a417b8274d03f7")
ANCESTOR_JOURNAL_ROOT = Path(
    "/var/lib/heptatrader/p1-watch-activation/round86/journal")
ANCESTOR_JOURNAL_SHA256 = (
    "sha256:9b20db0e816e10dab879411ee9b255adae7d6760e159c6fbfb38b61447c8ffa6")
STATE_ROOT = Path("/var/lib/heptatrader/p1-watch-activation/round114")
JOURNAL_ROOT = STATE_ROOT / "journal"
PREPARED_RECEIPT_PATH = STATE_ROOT / ".activation-receipt.prepared"
LOCK_PATH = Path("/var/lib/heptatrader/.p1-watch-activation-round114.lock")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
KILL_SWITCH_PATH = Path("/run/hepta/ib-paper-control-alpha/kill-switch")
PAPER_POLICY_ROOT = Path("/etc/heptatrader/paper-campaigns")
WATCH_EXPORT = Path("/run/hepta-shadow-watch-export-alpha")
WATCH_SESSIONS = Path("/run/hepta-agent-alpha/sessions")
WATCH_PRIVATE = Path("/var/lib/hepta-shadow-watch-alpha/private")
WATCH_CUSTODIAN_TRANSACTION = Path(
    "/var/lib/hepta-shadow-watch-custodian/alpha/transaction.json")
BROKER_HELPER = Path("/usr/libexec/hepta-broker-egress-policy")
PROFILE_DEPLOYER = Path("/usr/libexec/hepta-p1-watch-profile-deployer")
SHADOW_INSTALLER = Path("/usr/libexec/hepta-shadow-host-installer")
GATEWAY_EXECUTABLE = Path("/usr/libexec/hepta-tool-gatewayd")
DOMAIN_CONFIG = Path("/etc/heptatrader/trust-domains/alpha.json")
PYTHON = Path("/usr/bin/python3.12")
FILESYSTEM_ROOT = Path("/")
STALE_QUARANTINE_ROOT = Path(
    "/var/lib/hepta/p1-admission/quarantine/"
    "activation-round114")
PERSISTENT_SYSTEMD_ROOT = Path("/etc/systemd/system")
RUNTIME_SYSTEMD_ROOT = Path("/run/systemd/system")
MASK_TARGET = "/dev/null"

PROFILE_PAYLOAD = (
    b"HEPTA_EXECUTION_REMOTE_MODE=SIMULATOR\n"
    b"HEPTA_EXECUTION_SOCKET=/run/hepta-execution-alpha/execution.sock\n"
    b"HEPTA_EXECUTION_EVENT_SOCKET=/run/hepta-execution-alpha/events.sock\n"
    b"HEPTA_EXECUTION_SERVICE_UID=2111\n"
    b"HEPTA_EXECUTION_IO_TIMEOUT_MS=2500\n"
    b"HEPTA_EXECUTION_MAX_RESPONSE_BYTES=32768\n"
    b"HEPTA_TOOL_ACCOUNT=SIM\n"
    b"HEPTA_EXECUTION_DOMAIN_ID=SIM:alpha\n"
    b"HEPTA_TOOL_ALLOW_TRADE=0\n"
    b"HEPTA_TOOL_SESSION_TEMPLATES=watch\n"
    b"HEPTA_TOOL_CONTRACT_BINDINGS=EUR.USD|EUR|CASH|IDEALPRO|USD\n"
    b"HEPTA_TOOL_AGENT_UID=2104\n"
    b"HEPTA_TOOL_SUPERVISOR_UID=0\n"
    b"HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC=86400\n"
    b"HEPTA_TOOL_SERVER_WORKERS=4\n"
    b"HEPTA_TOOL_SERVER_MAX_PENDING=32\n"
    b"HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER=1\n"
    b"HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER=8\n"
    b"HEPTA_TOOL_SERVER_INGRESS_WORKERS=2\n"
)
PROFILE_SHA256 = hashlib.sha256(PROFILE_PAYLOAD).hexdigest()
LEGACY_PROFILE_SHA256 = (
    "2397f4c86156adaa9dca0e929e727b827080312fd57ede3ffd1597d1bdc37ea1")
LEGACY_PROFILE_BYTES = 677
LEGACY_PROFILE_RECEIPT_FILE_SHA256 = (
    "sha256:3904f17a444fb7a6a482b187c081c9a8eba854d39dd476ff948477eb7b9376aa")
LEGACY_PROFILE_RECEIPT_BODY_SHA256 = (
    "sha256:17fcaee75ce5a3bc67f944b3d0fc5bc63512a39f4d85dc6e2b04f71af81da4ff")
LEGACY_PROFILE_RECEIPT_BYTES = 33103
PREDECESSOR_PROFILE_RECEIPT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/round95-generation20.json")
PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256 = (
    "sha256:c1557c1fe0bbab68bfc0c85148f2dcb3b32a2c8b75da7b229296d1b99daebd67")
PREDECESSOR_PROFILE_RECEIPT_BODY_SHA256 = (
    "sha256:e09712acbfed117a47ad5e86c63bbfe638ec38d89d7579e85b47409b57728fb2")
PREDECESSOR_PROFILE_RECEIPT_BYTES = 58196
PROFILE_ITEMS = tuple(
    line.decode("ascii").split("=", 1)
    for line in PROFILE_PAYLOAD.splitlines())

GATEWAY_SERVICE = "hepta-tool-gateway@alpha.service"
GATEWAY_SOCKET = "hepta-tool-gateway@alpha.socket"
SUPERVISOR_SOCKET = "hepta-tool-session-supervisor@alpha.socket"
BROKER_UNIT = "hepta-broker-egress-policy.service"
RECONCILE_TIMER = "hepta-p1-watch-activation-reconcile.timer"
GATEWAY_UNITS = (GATEWAY_SERVICE, GATEWAY_SOCKET, SUPERVISOR_SOCKET)
PAPER_UNITS = (
    "hepta-execution-ib-paper.service",
    "hepta-execution-ib-paper.socket",
    "hepta-execution-events-ib-paper.socket",
    "hepta-execution-ib-paper@alpha.service",
    "hepta-execution-ib-paper@alpha.socket",
    "hepta-execution-events-ib-paper@alpha.socket",
    "hepta-ib-paper-domain-preflight@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.socket",
)
WATCH_BOUNDARY_UNITS = (
    "hepta-shadow-watch-custodian@alpha.service",
    "hepta-shadow-watch-custodian-reconcile@alpha.service",
    "hepta-shadow-watch-custodian-reconcile@alpha.timer",
    "hepta-shadow-watch-collector@alpha.service",
    "hepta-shadow-watch-collector@alpha.timer",
    "hepta-shadow-watch-export@alpha.service",
)
STALE_UNITS = (
    "hepta-p1-shadow-admission-round110.service",
    "hepta-p1-shadow-reader-round109.service",
    "hepta-p1-shadow-host-round109.service",
    "hepta-p1-shadow-reader-round110.service",
    "hepta-p1-shadow-host-round110.service",
    "hepta-p1-shadow-admission-round112.service",
    "hepta-p1-shadow-reader-round111.service",
    "hepta-p1-shadow-host-round111.service",
    "hepta-p1-shadow-reader-round112.service",
    "hepta-p1-shadow-host-round112.service",
)

SYSTEMCTL = "/usr/bin/systemctl"
ACTIVATION_CREDENTIAL_NAME = "hepta-p1-watch-activation-transaction.py"
BROKER_CREDENTIAL_NAME = "hepta-broker-egress-policy.py"
PROFILE_DEPLOYER_CREDENTIAL_NAME = "hepta-p1-watch-profile-deployer.py"
SHADOW_INSTALLER_CREDENTIAL_NAME = "hepta-shadow-host-installer.py"
CREDENTIALS_DIRECTORY = os.environ.get("CREDENTIALS_DIRECTORY", "")
REQUIRE_CREDENTIALS = (
    os.environ.get("HEPTA_ACTIVATION_REQUIRE_CREDENTIALS") == "1")
BROKER_RUNTIME_HELPER = (
    Path(CREDENTIALS_DIRECTORY) / BROKER_CREDENTIAL_NAME
    if CREDENTIALS_DIRECTORY else BROKER_HELPER)
PROFILE_DEPLOYER_RUNTIME_SOURCE = (
    Path(CREDENTIALS_DIRECTORY) / PROFILE_DEPLOYER_CREDENTIAL_NAME
    if CREDENTIALS_DIRECTORY else PROFILE_DEPLOYER)
ACTIVATION_RUNTIME_SOURCE = (
    Path(CREDENTIALS_DIRECTORY) / ACTIVATION_CREDENTIAL_NAME
    if CREDENTIALS_DIRECTORY else Path(__file__))
SHADOW_INSTALLER_RUNTIME_SOURCE = (
    Path(CREDENTIALS_DIRECTORY) / SHADOW_INSTALLER_CREDENTIAL_NAME
    if CREDENTIALS_DIRECTORY else SHADOW_INSTALLER)
SANITIZED_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "PYTHONNOUSERSITE": "1",
}
MUTATION_ALLOWLIST = frozenset({
    (SYSTEMCTL, "enable", "--now", RECONCILE_TIMER),
    (SYSTEMCTL, "daemon-reload"),
    (SYSTEMCTL, "stop", BROKER_UNIT),
    (SYSTEMCTL, "start", BROKER_UNIT),
    (SYSTEMCTL, "unmask", *GATEWAY_UNITS),
    (SYSTEMCTL, "unmask", "--runtime", *GATEWAY_UNITS),
    (SYSTEMCTL, "start", GATEWAY_SERVICE),
    (SYSTEMCTL, "stop", *GATEWAY_UNITS),
    (SYSTEMCTL, "mask", *GATEWAY_UNITS),
    (SYSTEMCTL, "mask", "--runtime", *GATEWAY_UNITS),
})
BROKER_CHECK = (
    str(PYTHON), "-I", "-S", str(BROKER_RUNTIME_HELPER),
    "--check-deny-all")
BROKER_TIGHTEN = (
    str(PYTHON), "-I", "-S", str(BROKER_RUNTIME_HELPER),
    "--tighten-deny-all")
DENY_ALL_PASS = re.compile(
    r"\Ahepta_broker_egress_policy: PASS "
    r"policy_sha256=(?P<sha>[0-9a-f]{64}) "
    r"authorized_connectors=0 authorized_uids= protected_ports=4\n\Z")

ACTIVATION_PHASES = (
    "PREPARED", "TIMER_ENABLE_INTENT", "TIMER_ARMED",
    "STALE_QUARANTINE_INTENT", "STALE_CLEAN",
    "DAEMON_RELOAD_INTENT",
    "MANAGER_RELOADED", "BROKER_STOP_INTENT",
    "BROKER_STOPPED_DENY_ALL", "BROKER_START_INTENT",
    "BROKER_ACTIVE_DENY_ALL_ATTESTED", "GATEWAY_UNMASK_INTENT",
    "GATEWAY_UNMASKED_RELOADED", "GATEWAY_START_INTENT",
    "GATEWAY_ACTIVE_ATTESTED", "COMMIT_INTENT",
)
QUARANTINE_PHASES = (
    "QUARANTINE_INTENT", "GATEWAY_MASKED_STOPPED", "BROKER_DENY_ALL",
    "AUTHORITY_EMPTY", "FAILED_CLOSED",
)
RECEIPT_SCHEMA = "hepta.p1-watch-activation-receipt.v4"
RECEIPT_VERSION = 4
PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain",
    "started_at_ms", "completed_at_ms", "boot_id",
    "profile_deployment_receipt_path",
    "profile_deployment_receipt_file_sha256",
    "profile_deployment_receipt_body_sha256", "profile_sha256",
    "profile_bytes", "journal_sha256", "broker_before", "broker_after",
    "gateway_after", "reconcile_timer", "paper_units", "kill_switch_engaged",
    "watch_boundary", "stale_bundles", "systemctl_mutations",
    "fresh_activation_transaction", "gateway_activated",
    "gateway_profile_loaded",
    "gateway_contract_binding_loaded", "broker_loaded_source_attested",
    "broker_deny_all_continuity_attested", "watch_authority_provisioned",
    "campaign_launched", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access",
    "admission_prerequisite_satisfied", "paper_prerequisite_satisfied",
    "shadow_install_evidence", "predecessor_activation_failure",
    "body_sha256",
})
RECEIPT_FIELDS = frozenset({
    *PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_FIELDS,
    "predecessor_activation_success",
})
PREDECESSOR_ACTIVATION_SUCCESS_FIELDS = frozenset({
    "receipt_path", "receipt_file_sha256", "receipt_body_sha256",
    "receipt_schema", "receipt_version", "receipt_status", "receipt_round",
    "receipt_domain", "receipt_device", "receipt_inode", "receipt_mode",
    "receipt_nlink", "receipt_uid", "receipt_gid", "receipt_bytes",
    "receipt_mtime_ns", "receipt_ctime_ns",
})
PREDECESSOR_ACTIVATION_FAILURE_FIELDS = frozenset({
    "receipt_path", "receipt_file_sha256", "receipt_body_sha256",
    "receipt_schema", "receipt_version", "receipt_revision",
    "receipt_status", "receipt_round", "receipt_domain", "receipt_reason",
    "receipt_device", "receipt_inode", "receipt_mode", "receipt_nlink",
    "receipt_uid", "receipt_gid", "receipt_bytes", "receipt_mtime_ns",
    "receipt_ctime_ns", "journal_path", "journal_sha256",
    "journal_record_count", "journal_terminal_phase",
})
PREDECESSOR_FAILED_RECEIPT_DOCUMENT_FIELDS = frozenset({
    "schema", "version", "revision", "status", "round", "domain",
    "reason", "completed_at_ms", "quarantine", "previous_failed_receipt",
    "predecessor_activation_failure",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
ANCESTOR_FAILED_RECEIPT_DOCUMENT_FIELDS = frozenset({
    "schema", "version", "revision", "status", "round", "domain",
    "reason", "completed_at_ms", "quarantine", "previous_failed_receipt",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
ANCESTOR_PREVIOUS_FAILED_RECEIPT_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "device", "inode", "mode",
    "nlink", "uid", "gid", "bytes", "mtime_ns", "ctime_ns",
})
SHADOW_INSTALL_FILE_COUNT = 128
SHADOW_DEFAULT_DENY_IDENTITY_SHA256 = (
    "sha256:4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435")
SHADOW_INSTALLER_MEMBER = "usr/libexec/hepta-shadow-host-installer"
ACTIVATION_MEMBER = "usr/libexec/hepta-p1-watch-activation-transaction"
PROFILE_DEPLOYER_MEMBER = "usr/libexec/hepta-p1-watch-profile-deployer"
BROKER_HELPER_MEMBER = "usr/libexec/hepta-broker-egress-policy"
SHADOW_INSTALL_EVIDENCE_FIELDS = frozenset({
    "schema", "version", "receipt_path", "receipt_file_sha256",
    "receipt_body_sha256", "manifest_path", "manifest_file_sha256",
    "archive_sha256", "source_baseline_sha256", "installer_sha256",
    "installed_file_count", "installed_paths_sha256", "closure_sha256",
    "transaction_lock", "default_deny_identity_sha256", "lock_mode",
    "verified_under_lock", "domain", "backup_root", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "current_install_pointer_path", "current_install_pointer_file_sha256",
    "install_generation", "predecessor_install_generation",
    "predecessor_current_install_pointer_file_sha256",
})
BROKER_AFTER_FIELDS = frozenset({
    "unit", "active_state", "sub_state", "main_pid", "invocation_id",
    "exec_main_start_timestamp_monotonic_us", "process_starttime_ticks",
    "interpreter_path", "interpreter_sha256", "credential_source_path",
    "credential_source_sha256", "installed_source_path",
    "installed_source_sha256", "cmdline_sha256", "status_text",
    "tasks_current", "deny_all_policy_sha256", "authorized_connectors",
    "authorized_uids", "protected_ports", "unit_contract_sha256",
})
GATEWAY_AFTER_FIELDS = frozenset({
    "unit", "active_state", "sub_state", "gateway_main_pid",
    "gateway_invocation_id", "gateway_exec_main_start_timestamp_monotonic_us",
    "process_starttime_ticks", "gateway_executable_path",
    "gateway_executable_sha256", "domain_config_sha256",
    "gateway_profile_path", "gateway_profile_sha256",
    "gateway_process_profile_sha256", "execution_remote_mode",
    "tool_account", "execution_domain_id", "tool_allow_trade",
    "session_templates", "contract_bindings", "gateway_socket_path",
    "gateway_socket_device", "gateway_socket_inode",
    "supervisor_socket_path", "supervisor_socket_device",
    "supervisor_socket_inode", "unit_contract_sha256",
})
WATCH_BOUNDARY_FIELDS = frozenset({
    "export_absent", "sessions_authority_count", "private_authority_count",
    "custodian_transaction_absent", "session_bootstrap_idle_lock_observed",
})
RECONCILE_TIMER_FIELDS = frozenset({
    "unit", "load_state", "active_state", "sub_state", "job",
    "unit_file_state", "unit_contract_sha256",
})
QUARANTINE_GATEWAY_FIELDS = frozenset({
    "manager_units", "masks", "unit_contract_sha256",
})
QUARANTINE_MASK_FIELDS = frozenset({
    "path", "target", "device", "inode", "mode", "nlink", "uid", "gid",
    "bytes", "mtime_ns", "ctime_ns",
})
MAXIMUM_JSON_BYTES = 4 * 1024 * 1024
MAXIMUM_COMMAND_BYTES = 1024 * 1024
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW | CLOEXEC
READ_FLAGS = os.O_RDONLY | NOFOLLOW | CLOEXEC | getattr(os, "O_NONBLOCK", 0)
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC
PATH_FLAGS = getattr(os, "O_PATH", 0) | NOFOLLOW | CLOEXEC
RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2
AT_FDCWD = -100
LIBC = ctypes.CDLL(None, use_errno=True)
FAILURE_REPLACEMENT_SEAM_HOOK: Callable[[str], None] | None = None
QUARANTINE_ATTESTATION_SEAM_HOOK: Callable[[str], None] | None = None


class ActivationError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ShadowInstallBinding:
    consumer: Any
    verified: Any
    expected_evidence: dict[str, Any]
    installer_payload: bytes
    activation_payload: bytes
    profile_deployer_payload: bytes
    broker_helper_payload: bytes


@dataclass(frozen=True)
class ProfileArtifactBinding:
    module: Any
    document: dict[str, Any]
    artifacts: Any
    receipt_payload: bytes
    receipt_metadata: os.stat_result
    expected_shadow_install_evidence: dict[str, Any]


@dataclass(frozen=True)
class ShadowInstallQuarantineGuard:
    parent_path: Path
    parent_descriptor: int
    descriptor: int
    name: str
    identity: tuple[int, ...]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ActivationError(reason)


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ActivationError("ACTIVATION_JSON_INVALID") from error


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def seal(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "body_sha256": digest_bytes(canonical_bytes(body))}


def strict_document(payload: bytes, reason: str) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ActivationError(reason)
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("ascii"), object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ActivationError(reason)))
    except (UnicodeError, json.JSONDecodeError, ActivationError) as error:
        raise ActivationError(reason) from error
    _require(isinstance(document, dict), reason)
    _require(canonical_bytes(document) == payload, reason)
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    _require(claimed == digest_bytes(canonical_bytes(body)), reason)
    return document


def strict_canonical_object(payload: bytes, reason: str) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ActivationError(reason)
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("ascii"), object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ActivationError(reason)))
    except (UnicodeError, json.JSONDecodeError, ActivationError) as error:
        raise ActivationError(reason) from error
    _require(
        isinstance(document, dict) and canonical_bytes(document) == payload,
        reason)
    return document


def validate_shadow_install_evidence(value: Any) -> dict[str, Any]:
    reason = "ACTIVATION_SHADOW_INSTALL_EVIDENCE_INVALID"
    _require(
        isinstance(value, dict) and
        set(value) == SHADOW_INSTALL_EVIDENCE_FIELDS and
        value.get("schema") ==
            "hepta.shadow-runtime-install-consumption-evidence.v3" and
        type(value.get("version")) is int and value["version"] == 3 and
        value.get("receipt_path") == str(SHADOW_INSTALL_RECEIPT_PATH) and
        value.get("manifest_path") == str(SHADOW_INSTALL_MANIFEST_PATH) and
        value.get("domain") == DOMAIN and
        value.get("backup_root") == str(SHADOW_INSTALL_BACKUP_ROOT) and
        value.get("current_install_pointer_path") ==
            str(SHADOW_CURRENT_INSTALL_POINTER_PATH) and
        type(value.get("install_generation")) is int and
        value["install_generation"] == EXPECTED_SHADOW_INSTALL_GENERATION and
        type(value.get("predecessor_install_generation")) is int and
        value["predecessor_install_generation"] ==
            EXPECTED_PREDECESSOR_SHADOW_INSTALL_GENERATION and
        value.get("predecessor_current_install_pointer_file_sha256") ==
            EXPECTED_PREDECESSOR_CURRENT_INSTALL_POINTER_FILE_SHA256 and
        type(value.get("installed_file_count")) is int and
        value["installed_file_count"] == SHADOW_INSTALL_FILE_COUNT and
        value.get("default_deny_identity_sha256") ==
            SHADOW_DEFAULT_DENY_IDENTITY_SHA256 and
        value.get("lock_mode") == "exclusive" and
        value.get("verified_under_lock") is True and
        value.get("paper_authorized") is False and
        value.get("live_authorized") is False and
        value.get("mutation_attempted") is False and
        value.get("direct_broker_access") is False,
        reason)
    for field in (
            "receipt_file_sha256", "receipt_body_sha256",
            "manifest_file_sha256", "archive_sha256",
            "source_baseline_sha256", "installer_sha256",
            "installed_paths_sha256", "closure_sha256",
            "default_deny_identity_sha256",
            "current_install_pointer_file_sha256",
            "predecessor_current_install_pointer_file_sha256"):
        _require(
            type(value.get(field)) is str and
            re.fullmatch(r"sha256:[0-9a-f]{64}", value[field]) is not None,
            reason)
    lock = value.get("transaction_lock")
    _require(
        isinstance(lock, dict) and set(lock) == {
            "path", "device", "inode", "nlink", "uid", "gid", "mode",
            "size", "mtime_ns", "ctime_ns", "created_during_transaction",
            "persistent", "held_during_transaction"} and
        lock.get("path") == str(SHADOW_INSTALL_LOCK_PATH) and
        type(lock.get("device")) is int and lock["device"] >= 0 and
        type(lock.get("inode")) is int and lock["inode"] > 0 and
        type(lock.get("nlink")) is int and lock["nlink"] == 1 and
        type(lock.get("uid")) is int and lock["uid"] == ROOT_UID and
        type(lock.get("gid")) is int and lock["gid"] == ROOT_GID and
        lock.get("mode") == "0600" and
        type(lock.get("size")) is int and lock["size"] == 0 and
        type(lock.get("mtime_ns")) is int and lock["mtime_ns"] >= 0 and
        type(lock.get("ctime_ns")) is int and lock["ctime_ns"] >= 0 and
        type(lock.get("created_during_transaction")) is bool and
        lock.get("persistent") is True and
        lock.get("held_during_transaction") is True,
        reason)
    return value


def stable_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def rename_identity(metadata: os.stat_result) -> tuple[int, ...]:
    # A rename changes ctime on Linux; every other field remains bound.
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns,
    )


def procfs_identity(metadata: os.stat_result) -> tuple[int, ...]:
    # procfs directory nlink is the live process count and may change.
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_uid, metadata.st_gid, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _absolute_parts(path: Path) -> tuple[str, ...]:
    _require(path.is_absolute(), "ACTIVATION_INTERNAL_PATH_INVALID")
    parts = path.parts[1:]
    _require(
        all(part not in {"", ".", ".."} and "/" not in part
            for part in parts),
        "ACTIVATION_INTERNAL_PATH_INVALID")
    return parts


def _validate_directory(
    metadata: os.stat_result,
    policy: tuple[int, int, int] | None = None,
) -> None:
    if policy is None:
        _require(
            stat.S_ISDIR(metadata.st_mode) and
            metadata.st_uid in {ROOT_UID, 0} and metadata.st_gid in {ROOT_GID, 0}
            and stat.S_IMODE(metadata.st_mode) & 0o022 == 0,
            "ACTIVATION_ANCHORED_DIRECTORY_INVALID")
        return
    uid, gid, mode = policy
    _require(
        stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == uid and
        metadata.st_gid == gid and stat.S_IMODE(metadata.st_mode) == mode,
        "ACTIVATION_ANCHORED_DIRECTORY_INVALID")


def open_anchored_directory(
    path: Path,
    *,
    leaf_policy: tuple[int, int, int] | None = None,
    procfs: bool = False,
) -> int:
    """Open an absolute directory component-by-component without symlinks."""

    parts = _absolute_parts(path)
    _require(
        not procfs or (parts and parts[0] == "proc"),
        "ACTIVATION_INTERNAL_PATH_INVALID")
    try:
        descriptor = os.open(FILESYSTEM_ROOT, DIRECTORY_FLAGS)
    except OSError as error:
        raise ActivationError("ACTIVATION_ANCHOR_ROOT_INVALID") from error
    try:
        _validate_directory(os.fstat(descriptor))
        for index, part in enumerate(parts):
            try:
                child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
                child_meta = os.fstat(child)
                entry_meta = os.stat(
                    part, dir_fd=descriptor, follow_symlinks=False)
            except OSError as error:
                raise ActivationError(
                    "ACTIVATION_ANCHORED_DIRECTORY_INVALID") from error
            try:
                if leaf_policy is not None and index == len(parts) - 1:
                    _validate_directory(child_meta, leaf_policy)
                else:
                    _validate_directory(child_meta)
                identity = procfs_identity if procfs else stable_identity
                _require(
                    identity(child_meta) == identity(entry_meta),
                    "ACTIVATION_ANCHORED_DIRECTORY_REBOUND")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def canonical_rebind_directory(
    path: Path,
    descriptor: int,
    *,
    leaf_policy: tuple[int, int, int] | None = None,
    procfs: bool = False,
) -> None:
    current = os.fstat(descriptor)
    _validate_directory(current, leaf_policy)
    identity = procfs_identity if procfs else stable_identity
    rebound = open_anchored_directory(
        path, leaf_policy=leaf_policy, procfs=procfs)
    try:
        _require(
            identity(current) == identity(os.fstat(rebound)),
            "ACTIVATION_ANCHORED_DIRECTORY_REBOUND")
    finally:
        os.close(rebound)


def _ensure_owned_directory(path: Path, mode: int = 0o700) -> None:
    """Create only one transaction-owned leaf below an existing anchor."""

    parent = open_anchored_directory(path.parent)
    try:
        canonical_rebind_directory(path.parent, parent)
        try:
            os.mkdir(path.name, mode, dir_fd=parent)
            child = os.open(path.name, DIRECTORY_FLAGS, dir_fd=parent)
            try:
                os.fchown(child, ROOT_UID, ROOT_GID)
                os.fchmod(child, mode)
                os.fsync(child)
            finally:
                os.close(child)
            os.fsync(parent)
        except FileExistsError:
            pass
        except OSError as error:
            raise ActivationError("ACTIVATION_DIRECTORY_CREATE_FAILED") from error
        child = os.open(path.name, DIRECTORY_FLAGS, dir_fd=parent)
        try:
            _validate_directory(os.fstat(child), (ROOT_UID, ROOT_GID, mode))
            entry = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            _require(
                stable_identity(os.fstat(child)) == stable_identity(entry),
                "ACTIVATION_ANCHORED_DIRECTORY_REBOUND")
        finally:
            os.close(child)
        canonical_rebind_directory(path.parent, parent)
    finally:
        os.close(parent)


def _anchored_exists(path: Path) -> bool:
    parts = _absolute_parts(path)
    _require(bool(parts), "ACTIVATION_INTERNAL_PATH_INVALID")
    try:
        parent = os.open(FILESYSTEM_ROOT, DIRECTORY_FLAGS)
    except OSError as error:
        raise ActivationError("ACTIVATION_ANCHOR_ROOT_INVALID") from error
    try:
        _validate_directory(os.fstat(parent))
        for part in parts[:-1]:
            try:
                child = os.open(part, DIRECTORY_FLAGS, dir_fd=parent)
                child_meta = os.fstat(child)
                entry_meta = os.stat(
                    part, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return False
            except OSError as error:
                raise ActivationError("ACTIVATION_PATH_INVALID") from error
            try:
                _validate_directory(child_meta)
                _require(stable_identity(child_meta) == stable_identity(entry_meta),
                         "ACTIVATION_ANCHORED_DIRECTORY_REBOUND")
            except BaseException:
                os.close(child)
                raise
            os.close(parent)
            parent = child
        try:
            os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as error:
            raise ActivationError("ACTIVATION_PATH_INVALID") from error
        canonical_rebind_directory(path.parent, parent)
        return True
    finally:
        os.close(parent)


def _read_at(
    parent: int,
    name: str,
    reason: str,
    *,
    expected_uid: int | None,
    expected_gid: int | None,
    modes: frozenset[int] | None,
    maximum: int,
) -> tuple[bytes, os.stat_result]:
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(name, READ_FLAGS, dir_fd=parent)
    except OSError as error:
        raise ActivationError(reason) from error
    try:
        opened = os.fstat(descriptor)
        _require(
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            (expected_uid is None or opened.st_uid == expected_uid) and
            (expected_gid is None or opened.st_gid == expected_gid) and
            (modes is None or stat.S_IMODE(opened.st_mode) in modes) and
            0 <= opened.st_size <= maximum and
            stable_identity(before) == stable_identity(opened), reason)
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(
                descriptor, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        final = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _require(
            len(payload) <= maximum and
            stable_identity(opened) == stable_identity(after) ==
            stable_identity(final), reason)
        return bytes(payload), opened
    except OSError as error:
        raise ActivationError(reason) from error
    finally:
        os.close(descriptor)


def secure_read(
    path: Path, reason: str, *, expected_uid: int | None = None,
    expected_gid: int | None = None,
    modes: frozenset[int] = frozenset({0o600}),
    maximum: int = MAXIMUM_JSON_BYTES,
    parent_leaf_policy: tuple[int, int, int] | None = None,
    procfs_parent: bool = False,
) -> tuple[bytes, os.stat_result]:
    if expected_uid is None:
        expected_uid = ROOT_UID
    if expected_gid is None:
        expected_gid = ROOT_GID
    parent = open_anchored_directory(
        path.parent, leaf_policy=parent_leaf_policy, procfs=procfs_parent)
    try:
        canonical_rebind_directory(
            path.parent, parent, leaf_policy=parent_leaf_policy,
            procfs=procfs_parent)
        result = _read_at(
            parent, path.name, reason, expected_uid=expected_uid,
            expected_gid=expected_gid, modes=modes, maximum=maximum)
        canonical_rebind_directory(
            path.parent, parent, leaf_policy=parent_leaf_policy,
            procfs=procfs_parent)
        return result
    finally:
        os.close(parent)


def _bound_runtime_source(
    installed_path: Path,
    runtime_path: Path,
    reason: str,
) -> bytes:
    installed, _ = secure_read(
        installed_path, reason, modes=frozenset({0o755}),
        maximum=16 * 1024 * 1024)
    if CREDENTIALS_DIRECTORY:
        runtime, _ = secure_read(
            runtime_path, reason, modes=frozenset({0o400}),
            maximum=16 * 1024 * 1024)
    else:
        _require(not REQUIRE_CREDENTIALS, "ACTIVATION_CREDENTIALS_REQUIRED")
        runtime, _ = secure_read(
            runtime_path, reason, modes=frozenset({0o755}),
            maximum=16 * 1024 * 1024)
    _require(runtime == installed, reason)
    return runtime


def _bootstrap_validate_shadow_installer(
    installer_payload: bytes,
    expected_manifest_sha256: str,
) -> None:
    reason = "ACTIVATION_SHADOW_INSTALLER_SOURCE_INVALID"
    _require(
        type(expected_manifest_sha256) is str and
        re.fullmatch(
            r"sha256:[0-9a-f]{64}", expected_manifest_sha256) is not None,
        reason)
    manifest_payload, _ = secure_read(
        SHADOW_INSTALL_MANIFEST_PATH, reason,
        modes=frozenset({0o600}), maximum=2 * 1024 * 1024)
    _require(
        digest_bytes(manifest_payload) == expected_manifest_sha256,
        reason)
    document = strict_canonical_object(manifest_payload, reason)
    _require(
        set(document) == {
            "schema", "version", "archive_sha256",
            "source_baseline_sha256", "installer_sha256", "files",
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access"} and
        document.get("schema") ==
            "hepta.shadow-runtime-install-manifest.v2" and
        type(document.get("version")) is int and document["version"] == 2 and
        type(document.get("files")) is list and
        all(document.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")),
        reason)
    matches = [
        record for record in document["files"]
        if isinstance(record, dict) and
        record.get("path") == SHADOW_INSTALLER_MEMBER]
    installer_sha256 = digest_bytes(installer_payload)
    _require(
        len(matches) == 1 and set(matches[0]) == {
            "path", "mode", "size", "sha256"} and
        matches[0].get("mode") == "0755" and
        matches[0].get("size") == len(installer_payload) and
        matches[0].get("sha256") == installer_sha256 and
        document.get("installer_sha256") == installer_sha256,
        reason)


def _load_shadow_install_consumer(
    expected_evidence: dict[str, Any],
) -> tuple[Any, bytes]:
    payload = _bound_runtime_source(
        SHADOW_INSTALLER, SHADOW_INSTALLER_RUNTIME_SOURCE,
        "ACTIVATION_SHADOW_INSTALLER_SOURCE_INVALID")
    _bootstrap_validate_shadow_installer(
        payload, expected_evidence["manifest_file_sha256"])
    name = "_hepta_shadow_install_consumer_for_activation"
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(name, loader=None))
    module.__file__ = str(SHADOW_INSTALLER_RUNTIME_SOURCE)
    sys.modules[name] = module
    try:
        exec(compile(
            payload, str(SHADOW_INSTALLER_RUNTIME_SOURCE), "exec"),
            module.__dict__)
        _require(
            module.RECEIPT_SCHEMA ==
                "hepta.shadow-runtime-install-receipt.v4" and
            module.MANIFEST_SCHEMA ==
                "hepta.shadow-runtime-install-manifest.v2" and
            module.CURRENT_INSTALL_POINTER_SCHEMA ==
                "hepta.shadow-runtime-current-install.v1" and
            module.EXPECTED_SHADOW_FILE_COUNT == SHADOW_INSTALL_FILE_COUNT,
            "ACTIVATION_SHADOW_INSTALLER_SOURCE_INVALID")
        return module, payload
    except ActivationError:
        raise
    except Exception as error:
        raise ActivationError(
            "ACTIVATION_SHADOW_INSTALLER_SOURCE_INVALID") from error
    finally:
        sys.modules.pop(name, None)


def acquire_shadow_install_binding(
    expected_evidence: dict[str, Any],
) -> ShadowInstallBinding:
    expected = validate_shadow_install_evidence(expected_evidence)
    consumer, installer_payload = _load_shadow_install_consumer(expected)
    verified = None
    try:
        verified = consumer.acquire_verified_installation(
            receipt_path=SHADOW_INSTALL_RECEIPT_PATH,
            manifest_path=SHADOW_INSTALL_MANIFEST_PATH,
            expected_domain=DOMAIN,
            expected_backup_root=SHADOW_INSTALL_BACKUP_ROOT,
            expected_manifest_sha256=expected["manifest_file_sha256"],
            expected_receipt_sha256=expected["receipt_file_sha256"],
            lock_path=SHADOW_INSTALL_LOCK_PATH,
            expected_file_count=SHADOW_INSTALL_FILE_COUNT)
        activation_payload = _bound_runtime_source(
            Path("/usr/libexec/hepta-p1-watch-activation-transaction"),
            ACTIVATION_RUNTIME_SOURCE,
            "ACTIVATION_SOURCE_INVALID")
        profile_payload = _bound_runtime_source(
            PROFILE_DEPLOYER, PROFILE_DEPLOYER_RUNTIME_SOURCE,
            "ACTIVATION_PROFILE_DEPLOYER_SOURCE_INVALID")
        broker_payload = _bound_runtime_source(
            BROKER_HELPER, BROKER_RUNTIME_HELPER,
            "ACTIVATION_BROKER_SOURCE_INVALID")
        for archive_path, payload in (
                (SHADOW_INSTALLER_MEMBER, installer_payload),
                (ACTIVATION_MEMBER, activation_payload),
                (PROFILE_DEPLOYER_MEMBER, profile_payload),
                (BROKER_HELPER_MEMBER, broker_payload)):
            consumer.require_verified_runtime_member(
                verified, archive_path, payload)
        current = consumer.validate_verified_installation(verified)
        _require(current == expected, "ACTIVATION_SHADOW_INSTALL_REBOUND")
        return ShadowInstallBinding(
            consumer=consumer, verified=verified,
            expected_evidence=dict(expected),
            installer_payload=installer_payload,
            activation_payload=activation_payload,
            profile_deployer_payload=profile_payload,
            broker_helper_payload=broker_payload)
    except Exception as error:
        if verified is not None:
            try:
                consumer.release_verified_installation(verified)
            except Exception:
                pass
        if isinstance(error, ActivationError):
            raise
        raise ActivationError("ACTIVATION_SHADOW_INSTALL_INVALID") from error


def validate_shadow_install_binding(
    binding: ShadowInstallBinding,
) -> dict[str, Any]:
    try:
        current = binding.consumer.validate_verified_installation(
            binding.verified)
        for archive_path, payload in (
                (SHADOW_INSTALLER_MEMBER, binding.installer_payload),
                (ACTIVATION_MEMBER, binding.activation_payload),
                (PROFILE_DEPLOYER_MEMBER, binding.profile_deployer_payload),
                (BROKER_HELPER_MEMBER, binding.broker_helper_payload)):
            binding.consumer.require_verified_runtime_member(
                binding.verified, archive_path, payload)
        _require(
            current == binding.expected_evidence,
            "ACTIVATION_SHADOW_INSTALL_REBOUND")
        return current
    except ActivationError:
        raise
    except Exception as error:
        raise ActivationError("ACTIVATION_SHADOW_INSTALL_REBOUND") from error


def release_shadow_install_binding(binding: ShadowInstallBinding) -> None:
    try:
        binding.consumer.release_verified_installation(binding.verified)
    except Exception as error:
        raise ActivationError(
            "ACTIVATION_SHADOW_INSTALL_RELEASE_FAILED") from error


def acquire_shadow_install_quarantine_guard(
) -> ShadowInstallQuarantineGuard:
    """Acquire the existing install lock without trusting install payloads.

    This path is used precisely when manifest/receipt/closure provenance was
    rejected.  It therefore implements the minimal anchored lock consumer in
    this independently bound activation source and never imports or executes
    the rejected installer credential.
    """

    reason = "ACTIVATION_SHADOW_QUARANTINE_GUARD_INVALID"
    absolute = Path(os.path.normpath(os.path.abspath(
        os.fspath(SHADOW_INSTALL_LOCK_PATH))))
    _require(
        absolute.is_absolute() and absolute == SHADOW_INSTALL_LOCK_PATH,
        reason)
    parent = -1
    descriptor = -1
    locked = False
    try:
        parent = open_anchored_directory(absolute.parent)
        canonical_rebind_directory(absolute.parent, parent)
        before = os.stat(
            absolute.name, dir_fd=parent, follow_symlinks=False)
        _validate_shadow_install_quarantine_lock_metadata(before, reason)
        descriptor = os.open(absolute.name, READ_FLAGS, dir_fd=parent)
        opened = os.fstat(descriptor)
        _validate_shadow_install_quarantine_lock_metadata(opened, reason)
        _require(stable_identity(before) == stable_identity(opened), reason)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except (BlockingIOError, OSError) as error:
            raise ActivationError(reason) from error
        final_opened = os.fstat(descriptor)
        final_named = os.stat(
            absolute.name, dir_fd=parent, follow_symlinks=False)
        for metadata in (final_opened, final_named):
            _validate_shadow_install_quarantine_lock_metadata(metadata, reason)
        identity = stable_identity(opened)
        _require(
            identity == stable_identity(final_opened) ==
                stable_identity(final_named),
            reason)
        canonical_rebind_directory(absolute.parent, parent)
        guard = ShadowInstallQuarantineGuard(
            parent_path=absolute.parent,
            parent_descriptor=parent,
            descriptor=descriptor,
            name=absolute.name,
            identity=identity)
        parent = -1
        descriptor = -1
        locked = False
        validate_shadow_install_quarantine_guard(guard)
        return guard
    except ActivationError as error:
        if error.reason == reason:
            raise
        raise ActivationError(reason) from error
    except Exception as error:
        raise ActivationError(reason) from error
    finally:
        if descriptor >= 0:
            if locked:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)


def _validate_shadow_install_quarantine_lock_metadata(
    metadata: os.stat_result,
    reason: str,
) -> None:
    _require(
        stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
        metadata.st_uid == ROOT_UID and metadata.st_gid == ROOT_GID and
        stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_size == 0,
        reason)


def validate_shadow_install_quarantine_guard(
    guard: ShadowInstallQuarantineGuard,
) -> None:
    reason = "ACTIVATION_SHADOW_QUARANTINE_GUARD_REBOUND"
    try:
        _require(
            isinstance(guard, ShadowInstallQuarantineGuard) and
            guard.parent_path == SHADOW_INSTALL_LOCK_PATH.parent and
            guard.name == SHADOW_INSTALL_LOCK_PATH.name,
            reason)
        opened = os.fstat(guard.descriptor)
        named = os.stat(
            guard.name, dir_fd=guard.parent_descriptor,
            follow_symlinks=False)
        for metadata in (opened, named):
            _validate_shadow_install_quarantine_lock_metadata(metadata, reason)
        _require(
            stable_identity(opened) == guard.identity ==
                stable_identity(named),
            reason)
        canonical_rebind_directory(
            guard.parent_path, guard.parent_descriptor)
    except ActivationError as error:
        if error.reason == reason:
            raise
        raise ActivationError(reason) from error
    except Exception as error:
        raise ActivationError(reason) from error


def release_shadow_install_quarantine_guard(
    guard: ShadowInstallQuarantineGuard,
) -> None:
    error: Exception | None = None
    try:
        validate_shadow_install_quarantine_guard(guard)
    except Exception as caught:
        error = caught
    try:
        fcntl.flock(guard.descriptor, fcntl.LOCK_UN)
    except Exception as caught:
        if error is None:
            error = caught
    try:
        os.close(guard.descriptor)
    except Exception as caught:
        if error is None:
            error = caught
    try:
        os.close(guard.parent_descriptor)
    except Exception as caught:
        if error is None:
            error = caught
    if error is not None:
        raise ActivationError(
            "ACTIVATION_SHADOW_QUARANTINE_GUARD_RELEASE_FAILED") from error


def _write_exclusive(path: Path, payload: bytes, mode: int = 0o600) -> None:
    parent = open_anchored_directory(path.parent)
    try:
        canonical_rebind_directory(path.parent, parent)
        descriptor = os.open(path.name, CREATE_FLAGS, mode, dir_fd=parent)
        try:
            os.fchown(descriptor, ROOT_UID, ROOT_GID)
            os.fchmod(descriptor, mode)
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
            metadata = os.fstat(descriptor)
            _require(
                stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
                stat.S_IMODE(metadata.st_mode) == mode and
                metadata.st_size == len(payload),
                "ACTIVATION_WRITE_FAILED")
        finally:
            os.close(descriptor)
        final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _require(
            stat.S_ISREG(final.st_mode) and final.st_uid == ROOT_UID and
            final.st_gid == ROOT_GID and final.st_nlink == 1 and
            stat.S_IMODE(final.st_mode) == mode and
            final.st_size == len(payload), "ACTIVATION_WRITE_FAILED")
        os.fsync(parent)
        canonical_rebind_directory(path.parent, parent)
    except FileExistsError as error:
        raise ActivationError("ACTIVATION_FILE_EXISTS") from error
    except OSError as error:
        raise ActivationError("ACTIVATION_WRITE_FAILED") from error
    finally:
        os.close(parent)


def _validate_gateway_mask_metadata(metadata: os.stat_result) -> None:
    _require(
        stat.S_ISLNK(metadata.st_mode) and
        stat.S_IMODE(metadata.st_mode) == 0o777 and
        metadata.st_nlink == 1 and metadata.st_uid == ROOT_UID and
        metadata.st_gid == ROOT_GID and
        metadata.st_size == len(MASK_TARGET),
        "ACTIVATION_GATEWAY_QUARANTINE_INVALID")


def _read_gateway_mask(
    path: Path,
    scope: str,
    unit: str,
) -> tuple[dict[str, Any], tuple[int, ...]]:
    reason = "ACTIVATION_GATEWAY_QUARANTINE_INVALID"
    expected_root = (
        PERSISTENT_SYSTEMD_ROOT if scope == "persistent" else
        RUNTIME_SYSTEMD_ROOT)
    _require(
        scope in {"persistent", "runtime"} and unit in GATEWAY_UNITS and
        path == expected_root / unit and getattr(os, "O_PATH", 0) != 0,
        reason)
    parent = open_anchored_directory(path.parent)
    descriptor = -1
    try:
        canonical_rebind_directory(path.parent, parent)
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _validate_gateway_mask_metadata(before)
        descriptor = os.open(path.name, PATH_FLAGS, dir_fd=parent)
        opened = os.fstat(descriptor)
        _validate_gateway_mask_metadata(opened)
        _require(stable_identity(before) == stable_identity(opened), reason)
        target = os.readlink("", dir_fd=descriptor)
        after_readlink = os.fstat(descriptor)
        entry_after_readlink = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False)
        _validate_gateway_mask_metadata(after_readlink)
        _validate_gateway_mask_metadata(entry_after_readlink)
        _require(
            target == MASK_TARGET and
            stable_identity(opened) == stable_identity(after_readlink) ==
                stable_identity(entry_after_readlink), reason)
        final_opened = os.fstat(descriptor)
        final_entry = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False)
        _validate_gateway_mask_metadata(final_opened)
        _validate_gateway_mask_metadata(final_entry)
        _require(
            stable_identity(entry_after_readlink) ==
                stable_identity(final_opened) == stable_identity(final_entry),
            reason)
        canonical_rebind_directory(path.parent, parent)
        return ({
            "path": str(path), "target": target,
            "device": final_opened.st_dev, "inode": final_opened.st_ino,
            "mode": final_opened.st_mode, "nlink": final_opened.st_nlink,
            "uid": final_opened.st_uid, "gid": final_opened.st_gid,
            "bytes": final_opened.st_size,
            "mtime_ns": final_opened.st_mtime_ns,
            "ctime_ns": final_opened.st_ctime_ns,
        }, stable_identity(final_opened))
    except OSError as error:
        raise ActivationError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _gateway_masks_state() -> tuple[
    dict[str, dict[str, dict[str, Any]]],
    dict[str, dict[str, tuple[int, ...]]],
]:
    evidence: dict[str, dict[str, dict[str, Any]]] = {}
    identities: dict[str, dict[str, tuple[int, ...]]] = {}
    for unit in GATEWAY_UNITS:
        persistent, persistent_identity = _read_gateway_mask(
            PERSISTENT_SYSTEMD_ROOT / unit, "persistent", unit)
        runtime, runtime_identity = _read_gateway_mask(
            RUNTIME_SYSTEMD_ROOT / unit, "runtime", unit)
        evidence[unit] = {
            "persistent": persistent, "runtime": runtime}
        identities[unit] = {
            "persistent": persistent_identity, "runtime": runtime_identity}
    return evidence, identities


def _valid_gateway_quarantine(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != QUARANTINE_GATEWAY_FIELDS:
        return False
    manager = value.get("manager_units")
    masks = value.get("masks")
    if (not isinstance(manager, dict) or set(manager) != set(GATEWAY_UNITS) or
            not isinstance(masks, dict) or set(masks) != set(GATEWAY_UNITS)):
        return False
    expected_manager = {
        "LoadState": "masked", "ActiveState": "inactive",
        "SubState": "dead", "Job": "", "UnitFileState": "masked",
    }
    if any(member != expected_manager for member in manager.values()):
        return False
    for unit in GATEWAY_UNITS:
        if (not isinstance(masks[unit], dict) or
                set(masks[unit]) != {"persistent", "runtime"}):
            return False
        for scope, root in (
                ("persistent", PERSISTENT_SYSTEMD_ROOT),
                ("runtime", RUNTIME_SYSTEMD_ROOT)):
            member = masks[unit][scope]
            if (not isinstance(member, dict) or
                    set(member) != QUARANTINE_MASK_FIELDS or
                    member.get("path") != str(root / unit) or
                    member.get("target") != MASK_TARGET or
                    not all(type(member.get(field)) is int for field in (
                        "device", "inode", "mode", "nlink", "uid", "gid",
                        "bytes", "mtime_ns", "ctime_ns")) or
                    not stat.S_ISLNK(member["mode"]) or
                    stat.S_IMODE(member["mode"]) != 0o777 or
                    member["nlink"] != 1 or member["uid"] != ROOT_UID or
                    member["gid"] != ROOT_GID or
                    member["bytes"] != len(MASK_TARGET) or
                    member["device"] < 0 or member["inode"] <= 0 or
                    member["mtime_ns"] < 0 or member["ctime_ns"] < 0):
                return False
    body = {"manager_units": manager, "masks": masks}
    return value.get("unit_contract_sha256") == digest_bytes(
        canonical_bytes(body))


PROFILE_RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain",
    "started_at_ms", "finished_at_ms", "target_path",
    "receipt_staging_path", "target_before", "target_after",
    "target_final", "legacy_receipt", "legacy_backup",
    "legacy_retained_target", "preflight_before", "preflight_after",
    "preflight_final", "profile_content_changed", "target_written",
    "target_replaced",
    "services_started", "services_stopped", "services_restarted",
    "campaign_launched", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "body_sha256",
    "activation_receipt_eligible", "preflight_reusable_for_activation",
    "broker_loaded_source_attested",
    "broker_deny_all_continuity_attested",
    "fresh_activation_transaction_required",
    "shadow_install_evidence", "predecessor_profile_receipt",
    "dormant_paper_to_watch_transition_receipt",
})
PROFILE_FILE_EVIDENCE_FIELDS = frozenset({
    "path", "sha256", "bytes", "device", "inode", "mode", "nlink",
    "uid", "gid", "mtime_ns", "ctime_ns",
})
PROFILE_LEGACY_RECEIPT_EVIDENCE_FIELDS = frozenset({
    *PROFILE_FILE_EVIDENCE_FIELDS, "body_sha256",
})
PREDECESSOR_PROFILE_RECEIPT_EVIDENCE_FIELDS = frozenset({
    *PROFILE_FILE_EVIDENCE_FIELDS, "body_sha256",
})
PROFILE_TRANSITION_RECEIPT_EVIDENCE_FIELDS = frozenset({
    *PROFILE_FILE_EVIDENCE_FIELDS, "body_sha256",
})
PROFILE_PREFLIGHT_FIELDS = frozenset({
    "gateway_units", "gateway_masks", "gateway_unit_closure",
    "systemd_manager", "manager_unit_contracts", "broker_egress_unit",
    "broker_egress_check", "paper_units", "campaign_policy_count",
    "kill_switch_engaged", "watch_boundary",
    "broker_egress_deny_all_observed",
})


def _validate_profile_preflight(value: Any) -> None:
    reason = "ACTIVATION_PROFILE_RECEIPT_INVALID"
    _require(isinstance(value, dict) and set(value) == PROFILE_PREFLIGHT_FIELDS,
             reason)
    gateway = value["gateway_units"]
    masks = value["gateway_masks"]
    manager_contracts = value["manager_unit_contracts"]
    _require(
        isinstance(gateway, dict) and set(gateway) == set(GATEWAY_UNITS) and
        isinstance(masks, dict) and set(masks) == set(GATEWAY_UNITS) and
        isinstance(manager_contracts, dict) and
        set(manager_contracts) == set(GATEWAY_UNITS) and
        isinstance(value["gateway_unit_closure"], dict) and
        isinstance(value["systemd_manager"], dict), reason)
    for unit in GATEWAY_UNITS:
        member = gateway[unit]
        _require(
            isinstance(member, dict) and
            member.get("LoadState") == "masked" and
            member.get("ActiveState") == "inactive" and
            member.get("SubState") == "dead" and member.get("Job") == "" and
            member.get("UnitFileState") == "masked" and
            masks[unit] == {
                "persistent": {
                    "path": f"/etc/systemd/system/{unit}",
                    "target": "/dev/null"},
                "runtime": {
                    "path": f"/run/systemd/system/{unit}",
                    "target": "/dev/null"},
            }, reason)
    broker = value["broker_egress_unit"]
    broker_fields = {
        "Id", "Names", "LoadState", "ActiveState", "SubState",
        "UnitFileState", "FragmentPath", "SourcePath", "DropInPaths",
        "NeedDaemonReload", "Job", "MainPID", "ExecMainPID", "ControlPID",
    }
    _require(
        isinstance(broker, dict) and set(broker) == broker_fields and
        broker["Id"] == BROKER_UNIT and broker["Names"] == BROKER_UNIT and
        broker["LoadState"] == "loaded" and
        (broker["ActiveState"], broker["SubState"]) in {
            ("failed", "failed"), ("inactive", "dead")} and
        broker["UnitFileState"] == "enabled" and
        broker["FragmentPath"] ==
            "/usr/lib/systemd/system/hepta-broker-egress-policy.service" and
        broker["SourcePath"] == "" and broker["DropInPaths"] == "" and
        broker["NeedDaemonReload"] == "yes" and broker["Job"] == "" and
        broker["MainPID"] == 0 and type(broker["ExecMainPID"]) is int and
        (broker["ExecMainPID"] == 0 or
         1 < broker["ExecMainPID"] <= 2**31 - 1) and
        broker["ControlPID"] == 0, reason)
    check = value["broker_egress_check"]
    _require(
        isinstance(check, dict) and check.get("argv") == ["--check-deny-all"] and
        check.get("authorized_connectors") == 0 and
        check.get("authorized_uids") == [] and
        check.get("protected_ports") == 4 and check.get("status") == "PASS" and
        check.get("helper_path") == str(BROKER_HELPER) and
        isinstance(check.get("helper_sha256"), str) and
        isinstance(check.get("helper_bytes"), int) and
        isinstance(check.get("policy_sha256"), str), reason)
    paper = value["paper_units"]
    _require(
        isinstance(paper, dict) and set(paper) == set(PAPER_UNITS) and
        all(member == {
            "LoadState": "loaded", "ActiveState": "inactive",
            "SubState": "dead", "Job": ""} for member in paper.values()) and
        value["campaign_policy_count"] == 0 and
        value["kill_switch_engaged"] is True and
        value["broker_egress_deny_all_observed"] is True and
        isinstance(value["watch_boundary"], dict), reason)


def _validate_profile_file_evidence(
    value: Any,
    *,
    path: Path,
    sha256: str,
    size: int,
    mode: int,
    legacy_receipt: bool = False,
) -> dict[str, Any]:
    reason = "ACTIVATION_PROFILE_RECEIPT_INVALID"
    fields = (
        PROFILE_LEGACY_RECEIPT_EVIDENCE_FIELDS
        if legacy_receipt else PROFILE_FILE_EVIDENCE_FIELDS)
    _require(
        isinstance(value, dict) and set(value) == fields and
        value.get("path") == str(path) and
        value.get("sha256") == sha256 and
        value.get("bytes") == size and
        value.get("mode") == stat.S_IFREG | mode and
        value.get("nlink") == 1 and value.get("uid") == ROOT_UID and
        value.get("gid") == ROOT_GID and
        (not legacy_receipt or value.get("body_sha256") ==
         LEGACY_PROFILE_RECEIPT_BODY_SHA256),
        reason)
    for field in (
            "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
            "mtime_ns", "ctime_ns"):
        _require(type(value.get(field)) is int and value[field] >= 0, reason)
    _require(value["device"] > 0 and value["inode"] > 0, reason)
    return value


def _validate_predecessor_profile_receipt_evidence(value: Any) -> None:
    reason = "ACTIVATION_PROFILE_RECEIPT_INVALID"
    _require(
        isinstance(value, dict) and
        set(value) == PREDECESSOR_PROFILE_RECEIPT_EVIDENCE_FIELDS and
        value.get("path") == str(PREDECESSOR_PROFILE_RECEIPT_PATH) and
        value.get("sha256") == PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256 and
        value.get("body_sha256") ==
            PREDECESSOR_PROFILE_RECEIPT_BODY_SHA256 and
        value.get("bytes") == PREDECESSOR_PROFILE_RECEIPT_BYTES and
        all(type(value.get(field)) is int for field in (
            "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
            "mtime_ns", "ctime_ns")) and
        value["device"] >= 0 and value["inode"] > 0 and
        stat.S_ISREG(value["mode"]) and stat.S_IMODE(value["mode"]) == 0o600 and
        value["nlink"] == 1 and value["uid"] == ROOT_UID and
        value["gid"] == ROOT_GID and value["mtime_ns"] >= 0 and
        value["ctime_ns"] >= 0,
        reason)


def _validate_profile_transition_receipt_evidence(value: Any) -> None:
    """Validate the dynamic Round114 transition receipt's exact envelope."""

    reason = "ACTIVATION_PROFILE_RECEIPT_INVALID"
    _require(
        isinstance(value, dict) and
        set(value) == PROFILE_TRANSITION_RECEIPT_EVIDENCE_FIELDS and
        value.get("path") == str(PROFILE_TRANSITION_RECEIPT_PATH) and
        isinstance(value.get("sha256"), str) and
        re.fullmatch(r"sha256:[0-9a-f]{64}", value["sha256"]) is not None and
        isinstance(value.get("body_sha256"), str) and
        re.fullmatch(
            r"sha256:[0-9a-f]{64}", value["body_sha256"]) is not None and
        all(type(value.get(field)) is int for field in (
            "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
            "mtime_ns", "ctime_ns")) and
        value["device"] > 0 and value["inode"] > 0 and
        value["mode"] == stat.S_IFREG | 0o600 and value["nlink"] == 1 and
        value["uid"] == ROOT_UID and value["gid"] == ROOT_GID and
        0 < value["bytes"] <= MAXIMUM_JSON_BYTES and
        value["mtime_ns"] >= 0 and value["ctime_ns"] >= 0,
        reason)


def _validate_with_frozen_profile_deployer(
    payload: bytes,
    metadata: os.stat_result,
    expected_shadow_install_evidence: dict[str, Any],
) -> None:
    """Reuse the credential-frozen producer's complete round114 validator."""

    if not CREDENTIALS_DIRECTORY:
        _require(not REQUIRE_CREDENTIALS, "ACTIVATION_CREDENTIALS_REQUIRED")
        return
    installed, _ = secure_read(
        PROFILE_DEPLOYER, "ACTIVATION_PROFILE_DEPLOYER_SOURCE_INVALID",
        modes=frozenset({0o755}), maximum=16 * 1024 * 1024)
    credential, _ = secure_read(
        PROFILE_DEPLOYER_RUNTIME_SOURCE,
        "ACTIVATION_PROFILE_DEPLOYER_SOURCE_INVALID",
        modes=frozenset({0o400}), maximum=16 * 1024 * 1024)
    _require(credential == installed,
             "ACTIVATION_PROFILE_DEPLOYER_SOURCE_INVALID")
    name = "_hepta_frozen_profile_deployer_validator"
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(name, loader=None))
    module.__file__ = str(PROFILE_DEPLOYER_RUNTIME_SOURCE)
    sys.modules[name] = module
    try:
        exec(compile(
            credential, str(PROFILE_DEPLOYER_RUNTIME_SOURCE), "exec"),
            module.__dict__)
        _require(
            module.ROUND114_RECEIPT_SCHEMA ==
                "hepta.p1-watch-profile-deployment-receipt.v8" and
            module.ROUND114_RECEIPT_VERSION == 8 and
            module.ROUND114_RECEIPT_PATH == PROFILE_RECEIPT_PATH and
            module.ROUND114_TRANSITION_RECEIPT_SCHEMA ==
                PROFILE_TRANSITION_RECEIPT_SCHEMA and
            module.ROUND114_TRANSITION_RECEIPT_VERSION ==
                PROFILE_TRANSITION_RECEIPT_VERSION and
            module.ROUND114_TRANSITION_RECEIPT_PATH ==
                PROFILE_TRANSITION_RECEIPT_PATH and
            module.ROUND114_TRANSITION_RECEIPT_FIELDS ==
                PROFILE_TRANSITION_RECEIPT_FIELDS and
            "preimage_evidence" in
                module.ROUND114_TRANSITION_RECEIPT_FIELDS and
            module.ROUND114_TRANSITION_PREIMAGE_SCHEMA ==
                PROFILE_TRANSITION_PREIMAGE_SCHEMA and
            module.ROUND114_TRANSITION_PREIMAGE_VERSION ==
                PROFILE_TRANSITION_PREIMAGE_VERSION and
            module.ROUND114_TRANSITION_PREIMAGE_PATH ==
                PROFILE_TRANSITION_PREIMAGE_PATH and
            module.ROUND114_TRANSITION_PREIMAGE_FIELDS ==
                PROFILE_TRANSITION_PREIMAGE_FIELDS and
            module.ROUND114_RECEIPT_FIELDS == PROFILE_RECEIPT_FIELDS and
            "dormant_paper_to_watch_transition_receipt" in
                module.ROUND114_RECEIPT_FIELDS and
            callable(getattr(module, "validate_round114_receipt", None)) and
            callable(getattr(
                module, "validate_round114_receipt_state_binding", None)),
            "ACTIVATION_PROFILE_RECEIPT_INVALID")
        snapshot = module.FileSnapshot(payload, metadata)
        validator = getattr(module, "validate_round114_receipt", None)
        _require(callable(validator), "ACTIVATION_PROFILE_RECEIPT_INVALID")
        document, _file_sha = validator(
            snapshot, expected_shadow_install_evidence)
        _require(isinstance(document, dict),
                 "ACTIVATION_PROFILE_RECEIPT_INVALID")
    except ActivationError:
        raise
    except Exception as error:
        raise ActivationError("ACTIVATION_PROFILE_RECEIPT_INVALID") from error
    finally:
        sys.modules.pop(name, None)


def validate_profile_receipt(
    payload: bytes,
    metadata: os.stat_result | None = None,
    expected_shadow_install_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document = strict_document(payload, "ACTIVATION_PROFILE_RECEIPT_INVALID")
    _require(
        set(document) == PROFILE_RECEIPT_FIELDS and
        document.get("schema") ==
            "hepta.p1-watch-profile-deployment-receipt.v8" and
        document.get("version") == 8 and
        document.get("status") ==
            "OFFLINE_PASSIVE_WATCH_PROFILE_REATTESTED",
        "ACTIVATION_PROFILE_RECEIPT_INVALID")
    fixed = {
        "round": ROUND, "domain": DOMAIN,
        "target_path": str(PROFILE_PATH),
        "receipt_staging_path": str(PROFILE_RECEIPT_STAGING_PATH),
        "profile_content_changed": False,
        "target_written": False,
        "target_replaced": False,
        "activation_receipt_eligible": False,
        "preflight_reusable_for_activation": False,
        "broker_loaded_source_attested": False,
        "broker_deny_all_continuity_attested": False,
        "fresh_activation_transaction_required": True,
        "paper_authorized": False, "live_authorized": False,
        "campaign_launched": False, "mutation_attempted": False,
        "direct_broker_access": False,
        "services_started": False, "services_stopped": False,
        "services_restarted": False,
    }
    _require(all(document.get(k) == v for k, v in fixed.items()),
             "ACTIVATION_PROFILE_RECEIPT_INVALID")
    _require(
        type(document.get("started_at_ms")) is int and
        type(document.get("finished_at_ms")) is int and
        0 <= document["started_at_ms"] <= document["finished_at_ms"],
        "ACTIVATION_PROFILE_RECEIPT_INVALID")
    target_before = _validate_profile_file_evidence(
        document.get("target_before"), path=PROFILE_PATH,
        sha256="sha256:" + PROFILE_SHA256, size=len(PROFILE_PAYLOAD),
        mode=0o644)
    target_after = _validate_profile_file_evidence(
        document.get("target_after"), path=PROFILE_PATH,
        sha256="sha256:" + PROFILE_SHA256, size=len(PROFILE_PAYLOAD),
        mode=0o644)
    target_final = _validate_profile_file_evidence(
        document.get("target_final"), path=PROFILE_PATH,
        sha256="sha256:" + PROFILE_SHA256, size=len(PROFILE_PAYLOAD),
        mode=0o644)
    _require(target_before == target_after == target_final,
             "ACTIVATION_PROFILE_RECEIPT_INVALID")
    _validate_profile_file_evidence(
        document.get("legacy_receipt"), path=LEGACY_PROFILE_RECEIPT_PATH,
        sha256=LEGACY_PROFILE_RECEIPT_FILE_SHA256,
        size=LEGACY_PROFILE_RECEIPT_BYTES, mode=0o600,
        legacy_receipt=True)
    _validate_profile_file_evidence(
        document.get("legacy_backup"), path=LEGACY_PROFILE_BACKUP_PATH,
        sha256="sha256:" + LEGACY_PROFILE_SHA256,
        size=LEGACY_PROFILE_BYTES, mode=0o600)
    _validate_profile_file_evidence(
        document.get("legacy_retained_target"),
        path=LEGACY_PROFILE_RETAINED_TARGET_PATH,
        sha256="sha256:" + LEGACY_PROFILE_SHA256,
        size=LEGACY_PROFILE_BYTES, mode=0o644)
    before = document.get("preflight_before")
    after = document.get("preflight_after")
    final = document.get("preflight_final")
    _validate_profile_preflight(before)
    _validate_profile_preflight(after)
    _validate_profile_preflight(final)
    _require(before == after == final,
             "ACTIVATION_PROFILE_RECEIPT_INVALID")
    _validate_predecessor_profile_receipt_evidence(
        document.get("predecessor_profile_receipt"))
    _validate_profile_transition_receipt_evidence(
        document.get("dormant_paper_to_watch_transition_receipt"))
    shadow_install_evidence = validate_shadow_install_evidence(
        document.get("shadow_install_evidence"))
    if expected_shadow_install_evidence is not None:
        _require(
            shadow_install_evidence == expected_shadow_install_evidence,
            "ACTIVATION_PROFILE_RECEIPT_INVALID")
    if metadata is not None:
        _validate_with_frozen_profile_deployer(
            payload, metadata, shadow_install_evidence)
    return document


def _load_verified_profile_deployer(
    shadow_install_binding: ShadowInstallBinding,
) -> Any:
    """Execute only the profile producer payload sealed by the install lock."""

    reason = "ACTIVATION_PROFILE_DEPLOYER_SOURCE_INVALID"
    validate_shadow_install_binding(shadow_install_binding)
    payload = shadow_install_binding.profile_deployer_payload
    name = "_hepta_verified_profile_deployer_for_activation"
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(name, loader=None))
    module.__file__ = str(PROFILE_DEPLOYER_RUNTIME_SOURCE)
    sys.modules[name] = module
    try:
        exec(compile(
            payload, str(PROFILE_DEPLOYER_RUNTIME_SOURCE), "exec"),
            module.__dict__)
        _require(
            module.ROUND114_RECEIPT_SCHEMA ==
                "hepta.p1-watch-profile-deployment-receipt.v8" and
            module.ROUND114_RECEIPT_VERSION == 8 and
            module.ROUND114_RECEIPT_PATH == PROFILE_RECEIPT_PATH and
            module.ROUND114_TRANSITION_RECEIPT_SCHEMA ==
                PROFILE_TRANSITION_RECEIPT_SCHEMA and
            module.ROUND114_TRANSITION_RECEIPT_VERSION ==
                PROFILE_TRANSITION_RECEIPT_VERSION and
            module.ROUND114_TRANSITION_RECEIPT_PATH ==
                PROFILE_TRANSITION_RECEIPT_PATH and
            module.ROUND114_TRANSITION_RECEIPT_FIELDS ==
                PROFILE_TRANSITION_RECEIPT_FIELDS and
            "preimage_evidence" in
                module.ROUND114_TRANSITION_RECEIPT_FIELDS and
            module.ROUND114_TRANSITION_PREIMAGE_SCHEMA ==
                PROFILE_TRANSITION_PREIMAGE_SCHEMA and
            module.ROUND114_TRANSITION_PREIMAGE_VERSION ==
                PROFILE_TRANSITION_PREIMAGE_VERSION and
            module.ROUND114_TRANSITION_PREIMAGE_PATH ==
                PROFILE_TRANSITION_PREIMAGE_PATH and
            module.ROUND114_TRANSITION_PREIMAGE_FIELDS ==
                PROFILE_TRANSITION_PREIMAGE_FIELDS and
            module.ROUND114_RECEIPT_FIELDS == PROFILE_RECEIPT_FIELDS and
            "dormant_paper_to_watch_transition_receipt" in
                module.ROUND114_RECEIPT_FIELDS and
            module.ROUND95_RECEIPT_PATH ==
                PREDECESSOR_PROFILE_RECEIPT_PATH and
            module.ROUND95_RECEIPT_FILE_SHA256 ==
                PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256 and
            module.LEGACY_RECEIPT_FILE_SHA256 ==
                LEGACY_PROFILE_RECEIPT_FILE_SHA256 and
            callable(getattr(module, "validate_round114_receipt", None)) and
            callable(getattr(
                module, "validate_round114_receipt_state_binding", None)),
            reason)
        validate_shadow_install_binding(shadow_install_binding)
        return module
    except ActivationError:
        raise
    except Exception as error:
        raise ActivationError(reason) from error
    finally:
        sys.modules.pop(name, None)


def _bind_profile_artifacts_with_module(
    module: Any,
    receipt_payload: bytes,
    receipt_metadata: os.stat_result,
    expected_shadow_install_evidence: dict[str, Any],
) -> ProfileArtifactBinding:
    """Bind a validated v8 receipt to every actual profile transaction file."""

    try:
        snapshot = module.FileSnapshot(receipt_payload, receipt_metadata)
        validator = getattr(module, "validate_round114_receipt", None)
        state_validator = getattr(
            module, "validate_round114_receipt_state_binding", None)
        _require(
            callable(validator) and callable(state_validator),
            "ACTIVATION_PROFILE_ARTIFACT_INVALID")
        document, _receipt_sha256 = validator(
            snapshot, expected_shadow_install_evidence)
        artifacts = module.read_rebind_artifacts(
            PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256)
        state_validator(document, artifacts)
        artifacts = module.require_rebind_artifacts_unchanged(
            artifacts, PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256)
        state_validator(document, artifacts)
        return ProfileArtifactBinding(
            module=module, document=document, artifacts=artifacts,
            receipt_payload=receipt_payload,
            receipt_metadata=receipt_metadata,
            expected_shadow_install_evidence=dict(
                expected_shadow_install_evidence))
    except ActivationError:
        raise
    except Exception as error:
        raise ActivationError("ACTIVATION_PROFILE_ARTIFACT_INVALID") from error


def acquire_profile_artifact_binding(
    receipt_payload: bytes,
    receipt_metadata: os.stat_result,
    shadow_install_binding: ShadowInstallBinding,
) -> ProfileArtifactBinding:
    evidence = validate_shadow_install_binding(shadow_install_binding)
    module = _load_verified_profile_deployer(shadow_install_binding)
    binding = _bind_profile_artifacts_with_module(
        module, receipt_payload, receipt_metadata, evidence)
    validate_shadow_install_binding(shadow_install_binding)
    validate_profile_artifact_binding(binding, shadow_install_binding)
    return binding


def validate_profile_artifact_binding(
    binding: ProfileArtifactBinding,
    shadow_install_binding: ShadowInstallBinding,
) -> None:
    reason = "ACTIVATION_PROFILE_ARTIFACT_REBOUND"
    try:
        current_evidence = validate_shadow_install_binding(
            shadow_install_binding)
        _require(
            current_evidence == binding.expected_shadow_install_evidence,
            reason)
        receipt_payload, receipt_metadata = secure_read(
            PROFILE_RECEIPT_PATH, reason, modes=frozenset({0o600}))
        _require(
            receipt_payload == binding.receipt_payload and
            stable_identity(receipt_metadata) ==
                stable_identity(binding.receipt_metadata),
            reason)
        snapshot = binding.module.FileSnapshot(
            receipt_payload, receipt_metadata)
        validator = getattr(
            binding.module, "validate_round114_receipt", None)
        state_validator = getattr(
            binding.module, "validate_round114_receipt_state_binding", None)
        _require(callable(validator) and callable(state_validator), reason)
        document, _receipt_sha256 = validator(
            snapshot, current_evidence)
        _require(document == binding.document, reason)
        artifacts = binding.module.require_rebind_artifacts_unchanged(
            binding.artifacts, PREDECESSOR_PROFILE_RECEIPT_FILE_SHA256)
        state_validator(document, artifacts)
        validate_shadow_install_binding(shadow_install_binding)
    except ActivationError:
        raise
    except Exception as error:
        raise ActivationError(reason) from error


@dataclass(frozen=True)
class JournalRecord:
    phase: str
    file_sha256: str
    document: dict[str, Any]


class Journal:
    def __init__(self, root: Path | None = None):
        self.root = JOURNAL_ROOT if root is None else root

    def load(self) -> list[JournalRecord]:
        if not _anchored_exists(self.root):
            return []
        descriptor = open_anchored_directory(
            self.root, leaf_policy=(ROOT_UID, ROOT_GID, 0o700))
        records: list[JournalRecord] = []
        previous: str | None = None
        try:
            try:
                names = sorted(os.listdir(descriptor))
            except OSError as error:
                raise ActivationError("ACTIVATION_JOURNAL_INVALID") from error
            for index, name in enumerate(names):
                match = re.fullmatch(r"([0-9]{4})-([A-Z_]+)\.json", name)
                _require(match is not None and int(match.group(1)) == index,
                         "ACTIVATION_JOURNAL_INVALID")
                payload, _ = _read_at(
                    descriptor, name, "ACTIVATION_JOURNAL_INVALID",
                    expected_uid=ROOT_UID, expected_gid=ROOT_GID,
                    modes=frozenset({0o600}), maximum=MAXIMUM_JSON_BYTES)
                document = strict_document(
                    payload, "ACTIVATION_JOURNAL_INVALID")
                _require(set(document) == {
                    "schema", "version", "sequence", "phase",
                    "recorded_at_ms", "previous_record_sha256", "evidence",
                    "body_sha256"}, "ACTIVATION_JOURNAL_INVALID")
                _require(
                    document["schema"] ==
                        "hepta.p1-watch-activation-journal.v1" and
                    document["version"] == 1 and
                    document["sequence"] == index and
                    document["phase"] == match.group(2) and
                    type(document["recorded_at_ms"]) is int and
                    document["recorded_at_ms"] >= 0 and
                    document["previous_record_sha256"] == previous and
                    isinstance(document["evidence"], dict),
                    "ACTIVATION_JOURNAL_INVALID")
                file_sha = digest_bytes(payload)
                records.append(JournalRecord(
                    document["phase"], file_sha, document))
                previous = file_sha
            phases = [item.phase for item in records]
            if any(phase in QUARANTINE_PHASES for phase in phases):
                first = min(
                    index for index, phase in enumerate(phases)
                    if phase in QUARANTINE_PHASES)
                _require(
                    phases[:first] == list(ACTIVATION_PHASES[:first]) and
                    phases[first:] == list(QUARANTINE_PHASES[:len(phases) - first]),
                    "ACTIVATION_JOURNAL_INVALID")
            else:
                _require(phases == list(ACTIVATION_PHASES[:len(phases)]),
                         "ACTIVATION_JOURNAL_INVALID")
            canonical_rebind_directory(
                self.root, descriptor,
                leaf_policy=(ROOT_UID, ROOT_GID, 0o700))
            return records
        finally:
            os.close(descriptor)

    def append(self, phase: str, evidence: dict[str, Any]) -> JournalRecord:
        _require(phase in set(ACTIVATION_PHASES) | set(QUARANTINE_PHASES),
                 "ACTIVATION_PHASE_INVALID")
        records = self.load()
        body = {
            "schema": "hepta.p1-watch-activation-journal.v1", "version": 1,
            "sequence": len(records), "phase": phase,
            "recorded_at_ms": time.time_ns() // 1_000_000,
            "previous_record_sha256": (
                records[-1].file_sha256 if records else None),
            "evidence": evidence,
        }
        document = seal(body)
        payload = canonical_bytes(document)
        path = self.root / f"{len(records):04d}-{phase}.json"
        _write_exclusive(path, payload)
        return JournalRecord(phase, digest_bytes(payload), document)

    def digest(self) -> str:
        return digest_bytes(canonical_bytes([
            item.file_sha256 for item in self.load()]))


def _validate_ancestor_previous_failed_receipt(value: Any) -> None:
    reason = "ACTIVATION_ANCESTOR_FAILED_RECEIPT_INVALID"
    _require(
        isinstance(value, dict) and
        set(value) == ANCESTOR_PREVIOUS_FAILED_RECEIPT_FIELDS and
        value.get("path") == str(ANCESTOR_FAILED_RECEIPT_PATH) and
        re.fullmatch(r"sha256:[0-9a-f]{64}",
                     value.get("file_sha256", "")) is not None and
        re.fullmatch(r"sha256:[0-9a-f]{64}",
                     value.get("body_sha256", "")) is not None and
        all(type(value.get(field)) is int for field in (
            "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
            "mtime_ns", "ctime_ns")) and
        value["device"] >= 0 and value["inode"] > 0 and
        stat.S_ISREG(value["mode"]) and
        stat.S_IMODE(value["mode"]) == 0o600 and
        value["nlink"] == 1 and value["uid"] == ROOT_UID and
        value["gid"] == ROOT_GID and
        0 < value["bytes"] <= MAXIMUM_JSON_BYTES and
        value["mtime_ns"] >= 0 and value["ctime_ns"] >= 0,
        reason)


def _validate_ancestor_failed_receipt_document(
    document: dict[str, Any],
) -> None:
    reason = "ACTIVATION_ANCESTOR_FAILED_RECEIPT_INVALID"
    quarantine = document.get("quarantine")
    _require(
        set(document) == ANCESTOR_FAILED_RECEIPT_DOCUMENT_FIELDS and
        document.get("schema") ==
            "hepta.p1-watch-activation-failed-receipt.v1" and
        document.get("version") == 1 and
        document.get("revision") == 1 and
        document.get("status") == "FAILED_CLOSED" and
        document.get("round") == ANCESTOR_ROUND and
        document.get("domain") == DOMAIN and
        type(document.get("completed_at_ms")) is int and
        document["completed_at_ms"] >= 0 and
        isinstance(document.get("reason"), str) and
        re.fullmatch(r"[A-Z][A-Z0-9_]{0,255}",
                     document["reason"]) is not None and
        all(document.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")) and
        isinstance(quarantine, dict) and
        set(quarantine) == {"errors", "deny_all", "complete"} and
        quarantine.get("errors") == [] and
        quarantine.get("complete") is True and
        _valid_deny_all(quarantine.get("deny_all")) and
        document.get("body_sha256") ==
            ANCESTOR_FAILED_RECEIPT_BODY_SHA256 and
        document.get("previous_failed_receipt") is None,
        reason)


def _validate_ancestor_activation_failure_evidence(value: Any) -> None:
    reason = "ACTIVATION_ANCESTOR_EVIDENCE_INVALID"
    _require(
        isinstance(value, dict) and
        set(value) == PREDECESSOR_ACTIVATION_FAILURE_FIELDS and
        value.get("receipt_path") == str(ANCESTOR_FAILED_RECEIPT_PATH) and
        value.get("receipt_file_sha256") ==
            ANCESTOR_FAILED_RECEIPT_FILE_SHA256 and
        value.get("receipt_body_sha256") ==
            ANCESTOR_FAILED_RECEIPT_BODY_SHA256 and
        value.get("receipt_schema") ==
            "hepta.p1-watch-activation-failed-receipt.v1" and
        value.get("receipt_version") == 1 and
        value.get("receipt_revision") == 1 and
        value.get("receipt_status") == "FAILED_CLOSED" and
        value.get("receipt_round") == ANCESTOR_ROUND and
        value.get("receipt_domain") == DOMAIN and
        isinstance(value.get("receipt_reason"), str) and
        re.fullmatch(r"[A-Z][A-Z0-9_]{0,255}",
                     value["receipt_reason"]) is not None and
        all(type(value.get(field)) is int for field in (
            "receipt_device", "receipt_inode", "receipt_mode",
            "receipt_nlink", "receipt_uid", "receipt_gid", "receipt_bytes",
            "receipt_mtime_ns", "receipt_ctime_ns", "journal_record_count")) and
        value["receipt_device"] >= 0 and value["receipt_inode"] > 0 and
        stat.S_ISREG(value["receipt_mode"]) and
        stat.S_IMODE(value["receipt_mode"]) == 0o600 and
        value["receipt_nlink"] == 1 and value["receipt_uid"] == ROOT_UID and
        value["receipt_gid"] == ROOT_GID and
        0 < value["receipt_bytes"] <= MAXIMUM_JSON_BYTES and
        value["receipt_mtime_ns"] >= 0 and value["receipt_ctime_ns"] >= 0 and
        value.get("journal_path") == str(ANCESTOR_JOURNAL_ROOT) and
        value.get("journal_sha256") == ANCESTOR_JOURNAL_SHA256 and
        value["journal_record_count"] > 0 and
        value.get("journal_terminal_phase") == "FAILED_CLOSED",
        reason)


def ancestor_activation_failure_evidence(
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reason = "ACTIVATION_ANCESTOR_FAILED_RECEIPT_INVALID"
    payload, metadata = secure_read(
        ANCESTOR_FAILED_RECEIPT_PATH, reason,
        expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o600}), maximum=MAXIMUM_JSON_BYTES)
    document = strict_document(payload, reason)
    _require(
        digest_bytes(payload) == ANCESTOR_FAILED_RECEIPT_FILE_SHA256,
        reason)
    _validate_ancestor_failed_receipt_document(document)
    records = Journal(ANCESTOR_JOURNAL_ROOT).load()
    prefix_length, journal_reason = _validate_quarantine_journal_prefix(
        records, document["reason"])
    journal_sha256 = digest_bytes(canonical_bytes([
        record.file_sha256 for record in records]))
    _require(
        prefix_length == len(QUARANTINE_PHASES) and
        journal_reason == document["reason"] and
        records[-1].phase == "FAILED_CLOSED" and
        journal_sha256 == ANCESTOR_JOURNAL_SHA256,
        "ACTIVATION_ANCESTOR_JOURNAL_INVALID")
    evidence = {
        "receipt_path": str(ANCESTOR_FAILED_RECEIPT_PATH),
        "receipt_file_sha256": digest_bytes(payload),
        "receipt_body_sha256": document["body_sha256"],
        "receipt_schema": document["schema"],
        "receipt_version": document["version"],
        "receipt_revision": document["revision"],
        "receipt_status": document["status"],
        "receipt_round": document["round"],
        "receipt_domain": document["domain"],
        "receipt_reason": document["reason"],
        "receipt_device": metadata.st_dev,
        "receipt_inode": metadata.st_ino,
        "receipt_mode": metadata.st_mode,
        "receipt_nlink": metadata.st_nlink,
        "receipt_uid": metadata.st_uid,
        "receipt_gid": metadata.st_gid,
        "receipt_bytes": metadata.st_size,
        "receipt_mtime_ns": metadata.st_mtime_ns,
        "receipt_ctime_ns": metadata.st_ctime_ns,
        "journal_path": str(ANCESTOR_JOURNAL_ROOT),
        "journal_sha256": journal_sha256,
        "journal_record_count": len(records),
        "journal_terminal_phase": records[-1].phase,
    }
    _validate_ancestor_activation_failure_evidence(evidence)
    if expected is not None:
        _validate_ancestor_activation_failure_evidence(expected)
        _require(evidence == expected, "ACTIVATION_ANCESTOR_REBOUND")
    return evidence


def validate_predecessor_activation_success_evidence(value: Any) -> None:
    reason = "ACTIVATION_PREDECESSOR_SUCCESS_EVIDENCE_INVALID"
    _require(
        isinstance(value, dict) and
        set(value) == PREDECESSOR_ACTIVATION_SUCCESS_FIELDS and
        value.get("receipt_path") == str(PREDECESSOR_ACTIVATION_RECEIPT_PATH) and
        value.get("receipt_file_sha256") ==
            PREDECESSOR_ACTIVATION_RECEIPT_FILE_SHA256 and
        value.get("receipt_body_sha256") ==
            PREDECESSOR_ACTIVATION_RECEIPT_BODY_SHA256 and
        value.get("receipt_schema") ==
            "hepta.p1-watch-activation-receipt.v3" and
        value.get("receipt_version") == 3 and
        value.get("receipt_status") == "WATCH_GATEWAY_ACTIVATED" and
        value.get("receipt_round") == PREDECESSOR_ROUND and
        value.get("receipt_domain") == DOMAIN and
        all(type(value.get(field)) is int for field in (
            "receipt_device", "receipt_inode", "receipt_mode",
            "receipt_nlink", "receipt_uid", "receipt_gid", "receipt_bytes",
            "receipt_mtime_ns", "receipt_ctime_ns")) and
        value["receipt_device"] >= 0 and value["receipt_inode"] > 0 and
        stat.S_ISREG(value["receipt_mode"]) and
        stat.S_IMODE(value["receipt_mode"]) == 0o600 and
        value["receipt_nlink"] == 1 and value["receipt_uid"] == ROOT_UID and
        value["receipt_gid"] == ROOT_GID and
        0 < value["receipt_bytes"] <= MAXIMUM_JSON_BYTES and
        value["receipt_mtime_ns"] >= 0 and value["receipt_ctime_ns"] >= 0,
        reason)


def _validate_predecessor_activation_success_document(
    document: dict[str, Any],
) -> None:
    reason = "ACTIVATION_PREDECESSOR_SUCCESS_RECEIPT_INVALID"
    _require(
        set(document) == PREDECESSOR_ACTIVATION_SUCCESS_RECEIPT_FIELDS and
        document.get("schema") ==
            "hepta.p1-watch-activation-receipt.v3" and
        document.get("version") == 3 and
        document.get("status") == "WATCH_GATEWAY_ACTIVATED" and
        document.get("round") == PREDECESSOR_ROUND and
        document.get("domain") == DOMAIN and
        document.get("body_sha256") ==
            PREDECESSOR_ACTIVATION_RECEIPT_BODY_SHA256 and
        all(document.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access", "watch_authority_provisioned",
            "campaign_launched", "paper_prerequisite_satisfied")) and
        document.get("admission_prerequisite_satisfied") is True and
        document.get("fresh_activation_transaction") is True and
        document.get("gateway_activated") is True and
        document.get("gateway_profile_loaded") is True and
        document.get("gateway_contract_binding_loaded") is True,
        reason)
    _validate_ancestor_activation_failure_evidence(
        document.get("predecessor_activation_failure"))


def predecessor_activation_success_evidence(
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reason = "ACTIVATION_PREDECESSOR_SUCCESS_RECEIPT_INVALID"
    payload, metadata = secure_read(
        PREDECESSOR_ACTIVATION_RECEIPT_PATH, reason,
        expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o600}), maximum=MAXIMUM_JSON_BYTES)
    _require(
        digest_bytes(payload) == PREDECESSOR_ACTIVATION_RECEIPT_FILE_SHA256,
        reason)
    document = strict_document(payload, reason)
    _validate_predecessor_activation_success_document(document)
    ancestor_activation_failure_evidence(
        document["predecessor_activation_failure"])
    evidence = {
        "receipt_path": str(PREDECESSOR_ACTIVATION_RECEIPT_PATH),
        "receipt_file_sha256": digest_bytes(payload),
        "receipt_body_sha256": document["body_sha256"],
        "receipt_schema": document["schema"],
        "receipt_version": document["version"],
        "receipt_status": document["status"],
        "receipt_round": document["round"],
        "receipt_domain": document["domain"],
        "receipt_device": metadata.st_dev,
        "receipt_inode": metadata.st_ino,
        "receipt_mode": metadata.st_mode,
        "receipt_nlink": metadata.st_nlink,
        "receipt_uid": metadata.st_uid,
        "receipt_gid": metadata.st_gid,
        "receipt_bytes": metadata.st_size,
        "receipt_mtime_ns": metadata.st_mtime_ns,
        "receipt_ctime_ns": metadata.st_ctime_ns,
    }
    validate_predecessor_activation_success_evidence(evidence)
    if expected is not None:
        validate_predecessor_activation_success_evidence(expected)
        _require(evidence == expected, "ACTIVATION_PREDECESSOR_SUCCESS_REBOUND")
    return evidence


def _validate_predecessor_failed_receipt_document(
    document: dict[str, Any],
) -> None:
    reason = "ACTIVATION_PREDECESSOR_FAILED_RECEIPT_INVALID"
    quarantine = document.get("quarantine")
    _require(
        set(document) == PREDECESSOR_FAILED_RECEIPT_DOCUMENT_FIELDS and
        document.get("schema") ==
            "hepta.p1-watch-activation-failed-receipt.v2" and
        document.get("version") == 2 and document.get("revision") == 1 and
        document.get("status") == "FAILED_CLOSED" and
        document.get("round") == PREDECESSOR_ROUND and
        document.get("domain") == DOMAIN and
        type(document.get("completed_at_ms")) is int and
        document["completed_at_ms"] >= 0 and
        isinstance(document.get("reason"), str) and
        re.fullmatch(r"[A-Z][A-Z0-9_]{0,255}",
                     document["reason"]) is not None and
        document.get("previous_failed_receipt") is None and
        all(document.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")) and
        isinstance(quarantine, dict) and
        set(quarantine) == {"errors", "deny_all", "complete"} and
        quarantine.get("errors") == [] and quarantine.get("complete") is True and
        _valid_deny_all(quarantine.get("deny_all")) and
        document.get("body_sha256") ==
            PREDECESSOR_FAILED_RECEIPT_BODY_SHA256,
        reason)
    _validate_ancestor_activation_failure_evidence(
        document.get("predecessor_activation_failure"))


def validate_predecessor_activation_failure_evidence(value: Any) -> None:
    reason = "ACTIVATION_PREDECESSOR_EVIDENCE_INVALID"
    _require(
        isinstance(value, dict) and
        set(value) == PREDECESSOR_ACTIVATION_FAILURE_FIELDS and
        value.get("receipt_path") == str(PREDECESSOR_FAILED_RECEIPT_PATH) and
        value.get("receipt_file_sha256") ==
            PREDECESSOR_FAILED_RECEIPT_FILE_SHA256 and
        value.get("receipt_body_sha256") ==
            PREDECESSOR_FAILED_RECEIPT_BODY_SHA256 and
        value.get("receipt_schema") ==
            "hepta.p1-watch-activation-failed-receipt.v2" and
        value.get("receipt_version") == 2 and
        value.get("receipt_revision") == 1 and
        value.get("receipt_status") == "FAILED_CLOSED" and
        value.get("receipt_round") == PREDECESSOR_ROUND and
        value.get("receipt_domain") == DOMAIN and
        isinstance(value.get("receipt_reason"), str) and
        re.fullmatch(r"[A-Z][A-Z0-9_]{0,255}",
                     value["receipt_reason"]) is not None and
        all(type(value.get(field)) is int for field in (
            "receipt_device", "receipt_inode", "receipt_mode",
            "receipt_nlink", "receipt_uid", "receipt_gid", "receipt_bytes",
            "receipt_mtime_ns", "receipt_ctime_ns", "journal_record_count")) and
        value["receipt_device"] >= 0 and value["receipt_inode"] > 0 and
        stat.S_ISREG(value["receipt_mode"]) and
        stat.S_IMODE(value["receipt_mode"]) == 0o600 and
        value["receipt_nlink"] == 1 and value["receipt_uid"] == ROOT_UID and
        value["receipt_gid"] == ROOT_GID and
        0 < value["receipt_bytes"] <= MAXIMUM_JSON_BYTES and
        value["receipt_mtime_ns"] >= 0 and value["receipt_ctime_ns"] >= 0 and
        value.get("journal_path") == str(PREDECESSOR_JOURNAL_ROOT) and
        value.get("journal_sha256") == PREDECESSOR_JOURNAL_SHA256 and
        value.get("journal_record_count") == 21 and
        value.get("journal_terminal_phase") == "FAILED_CLOSED",
        reason)


def predecessor_activation_failure_evidence(
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reason = "ACTIVATION_PREDECESSOR_FAILED_RECEIPT_INVALID"
    payload, metadata = secure_read(
        PREDECESSOR_FAILED_RECEIPT_PATH, reason,
        expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o600}), maximum=MAXIMUM_JSON_BYTES)
    document = strict_document(payload, reason)
    _require(
        digest_bytes(payload) == PREDECESSOR_FAILED_RECEIPT_FILE_SHA256,
        reason)
    _validate_predecessor_failed_receipt_document(document)
    ancestor_activation_failure_evidence(
        document["predecessor_activation_failure"])
    records = Journal(PREDECESSOR_JOURNAL_ROOT).load()
    prefix_length, journal_reason = _validate_quarantine_journal_prefix(
        records, document["reason"])
    journal_sha256 = digest_bytes(canonical_bytes([
        record.file_sha256 for record in records]))
    _require(
        len(records) == 21 and
        prefix_length == len(QUARANTINE_PHASES) and
        journal_reason == document["reason"] and
        records[-1].phase == "FAILED_CLOSED" and
        journal_sha256 == PREDECESSOR_JOURNAL_SHA256,
        "ACTIVATION_PREDECESSOR_JOURNAL_INVALID")
    evidence = {
        "receipt_path": str(PREDECESSOR_FAILED_RECEIPT_PATH),
        "receipt_file_sha256": digest_bytes(payload),
        "receipt_body_sha256": document["body_sha256"],
        "receipt_schema": document["schema"],
        "receipt_version": document["version"],
        "receipt_revision": document["revision"],
        "receipt_status": document["status"],
        "receipt_round": document["round"],
        "receipt_domain": document["domain"],
        "receipt_reason": document["reason"],
        "receipt_device": metadata.st_dev,
        "receipt_inode": metadata.st_ino,
        "receipt_mode": metadata.st_mode,
        "receipt_nlink": metadata.st_nlink,
        "receipt_uid": metadata.st_uid,
        "receipt_gid": metadata.st_gid,
        "receipt_bytes": metadata.st_size,
        "receipt_mtime_ns": metadata.st_mtime_ns,
        "receipt_ctime_ns": metadata.st_ctime_ns,
        "journal_path": str(PREDECESSOR_JOURNAL_ROOT),
        "journal_sha256": journal_sha256,
        "journal_record_count": len(records),
        "journal_terminal_phase": records[-1].phase,
    }
    validate_predecessor_activation_failure_evidence(evidence)
    if expected is not None:
        validate_predecessor_activation_failure_evidence(expected)
        _require(evidence == expected, "ACTIVATION_PREDECESSOR_REBOUND")
    return evidence


class ProductionExecutor:
    def __init__(self) -> None:
        self.mutations: list[list[str]] = []

    @staticmethod
    def _run(arguments: Sequence[str], timeout: float = 30) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                list(arguments), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="strict", cwd="/",
                env=SANITIZED_ENVIRONMENT, close_fds=True, timeout=timeout,
                check=False)
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            raise ActivationError("ACTIVATION_COMMAND_FAILED") from error
        _require(
            len(result.stdout.encode()) <= MAXIMUM_COMMAND_BYTES and
            len(result.stderr.encode()) <= MAXIMUM_COMMAND_BYTES,
            "ACTIVATION_COMMAND_OUTPUT_TOO_LARGE")
        return result

    def mutate(self, arguments: Sequence[str]) -> None:
        argv = tuple(arguments)
        _require(argv in MUTATION_ALLOWLIST, "ACTIVATION_SYSTEMCTL_NOT_ALLOWED")
        result = self._run(argv, 90)
        _require(result.returncode == 0, "ACTIVATION_SYSTEMCTL_FAILED")
        self.mutations.append(list(argv))

    def _show(self, unit: str, fields: Sequence[str]) -> dict[str, str]:
        argv = [SYSTEMCTL, "show", "--no-pager"]
        argv.extend(f"--property={field}" for field in fields)
        argv.append(unit)
        result = self._run(argv, 10)
        _require(result.returncode == 0 and result.stderr == "",
                 "ACTIVATION_SYSTEMD_STATE_INVALID")
        parsed: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            _require(separator == "=" and key in fields and key not in parsed,
                     "ACTIVATION_SYSTEMD_STATE_INVALID")
            parsed[key] = value
        _require(set(parsed) == set(fields), "ACTIVATION_SYSTEMD_STATE_INVALID")
        return parsed

    def deny_all(self, *, tighten: bool = False) -> dict[str, Any]:
        argv = BROKER_TIGHTEN if tighten else BROKER_CHECK
        result = self._run(argv, 20)
        _require(result.returncode == 0 and result.stderr == "",
                 "ACTIVATION_BROKER_NOT_DENY_ALL")
        match = DENY_ALL_PASS.fullmatch(result.stdout)
        _require(match is not None, "ACTIVATION_BROKER_NOT_DENY_ALL")
        return {
            "policy_sha256": "sha256:" + match.group("sha"),
            "authorized_connectors": 0, "authorized_uids": [],
            "protected_ports": 4,
        }

    def preflight(self) -> dict[str, Any]:
        gateway: dict[str, Any] = {}
        for unit in GATEWAY_UNITS:
            state = self._show(unit, (
                "LoadState", "ActiveState", "SubState", "Job",
                "UnitFileState"))
            _require(
                state == {"LoadState": "masked", "ActiveState": "inactive",
                          "SubState": "dead", "Job": "",
                          "UnitFileState": "masked"},
                "ACTIVATION_GATEWAY_NOT_OFFLINE")
            gateway[unit] = state
        paper = self.attest_paper_inactive()
        for unit in STALE_UNITS:
            state = self._show(unit, ("LoadState", "ActiveState", "SubState"))
            _require(state == {
                "LoadState": "not-found", "ActiveState": "inactive",
                "SubState": "dead"}, "ACTIVATION_STALE_UNIT_PRESENT")
        return {"gateway_units": gateway, "paper_units": paper,
                "deny_all": self.deny_all()}

    def attest_paper_inactive(self) -> dict[str, Any]:
        paper: dict[str, Any] = {}
        for unit in PAPER_UNITS:
            names = ("ActiveState", "SubState", "Job")
            state = self._show(unit, names)
            self._require_manager_rebind(
                state, self._show(unit, names), "ACTIVATION_PAPER_REBOUND")
            _require(state == {
                "ActiveState": "inactive", "SubState": "dead", "Job": ""},
                "ACTIVATION_PAPER_ACTIVE")
            paper[unit] = state
        return paper

    def attest_reconcile_timer(self) -> dict[str, str]:
        names = (
            "LoadState", "ActiveState", "SubState", "Job", "UnitFileState")
        fields = self._show(RECONCILE_TIMER, names)
        self._require_manager_rebind(
            fields, self._show(RECONCILE_TIMER, names),
            "ACTIVATION_RECONCILE_TIMER_REBOUND")
        expected = {
            "LoadState": "loaded", "ActiveState": "active",
            "Job": "", "UnitFileState": "enabled",
        }
        _require(
            {key: fields[key] for key in expected} == expected and
            fields["SubState"] in {"waiting", "running"},
            "ACTIVATION_RECONCILE_TIMER_NOT_ARMED")
        return {
            "unit": RECONCILE_TIMER, "load_state": fields["LoadState"],
            "active_state": fields["ActiveState"],
            "sub_state": fields["SubState"], "job": fields["Job"],
            "unit_file_state": fields["UnitFileState"],
            "unit_contract_sha256": self._unit_contract_sha(fields),
        }

    def stop_broker(self) -> None:
        state = self._show(BROKER_UNIT, ("ActiveState", "SubState"))
        if state["ActiveState"] != "inactive":
            self.mutate((SYSTEMCTL, "stop", BROKER_UNIT))

    @staticmethod
    def _unit_contract_sha(fields: dict[str, str]) -> str:
        return digest_bytes(canonical_bytes(fields))

    @staticmethod
    def _require_manager_rebind(
        before: dict[str, str],
        after: dict[str, str],
        reason: str,
    ) -> None:
        _require(after == before, reason)

    def attest_broker(self) -> dict[str, Any]:
        names = (
            "LoadState", "ActiveState", "SubState", "MainPID", "InvocationID",
            "ExecMainStartTimestampMonotonic", "Type", "NotifyAccess",
            "StatusText", "TasksCurrent", "NRestarts", "ExecStart",
            "LoadCredential")
        fields: dict[str, str] = {}
        for attempt in range(8):
            fields = self._show(BROKER_UNIT, names)
            if fields.get("TasksCurrent") == "1":
                break
            if attempt < 7:
                time.sleep(0.01)
        _require(
            fields["LoadState"] == "loaded" and
            fields["ActiveState"] == "active" and
            fields["SubState"] == "running" and fields["Type"] == "notify" and
            fields["NotifyAccess"] == "main" and
            fields["StatusText"] == "HeptaTrader broker boundary exact deny-all" and
            fields["TasksCurrent"] == "1" and fields["NRestarts"] == "0" and
            "--supervise-deny-all" in fields["ExecStart"] and
            ("hepta-broker-egress-policy.py" in fields["LoadCredential"] or
             fields["LoadCredential"] == "[unprintable]"),
            "ACTIVATION_BROKER_RUNTIME_INVALID")
        _require(re.fullmatch(r"[0-9a-f]{32}", fields["InvocationID"]) is not None,
                 "ACTIVATION_BROKER_RUNTIME_INVALID")
        pid = int(fields["MainPID"])
        _require(pid > 1, "ACTIVATION_BROKER_RUNTIME_INVALID")
        installed, _ = secure_read(
            BROKER_HELPER, "ACTIVATION_BROKER_SOURCE_INVALID", modes=frozenset({0o755}),
            maximum=64 * 1024 * 1024)
        interpreter, _ = secure_read(
            PYTHON, "ACTIVATION_BROKER_INTERPRETER_INVALID",
            modes=frozenset({0o755}), maximum=64 * 1024 * 1024)
        proc_payloads, process_starttime = _proc_payload_snapshot(
            pid, ("cmdline", "environ"))
        cmdline = proc_payloads["cmdline"]
        environ = proc_payloads["environ"]
        env = dict(
            entry.split(b"=", 1) for entry in environ.split(b"\0")
            if b"=" in entry)
        credentials_raw = env.get(b"CREDENTIALS_DIRECTORY")
        _require(credentials_raw is not None, "ACTIVATION_BROKER_SOURCE_INVALID")
        credentials = credentials_raw.decode("ascii", errors="strict")
        _require(credentials.startswith("/run/credentials/"),
                 "ACTIVATION_BROKER_SOURCE_INVALID")
        credential = _proc_credential_snapshot(
            pid, credentials, "hepta-broker-egress-policy.py",
            process_starttime)
        _require(credential == installed, "ACTIVATION_BROKER_SOURCE_INVALID")
        expected_parts = [
            str(PYTHON), "-I", "-S",
            f"{credentials}/hepta-broker-egress-policy.py",
            "--supervise-deny-all", "--paper-identities",
            "/etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json",
        ]
        _require(cmdline == b"\0".join(
            item.encode("ascii") for item in expected_parts) + b"\0",
            "ACTIVATION_BROKER_RUNTIME_INVALID")
        deny = self.deny_all()
        self._require_manager_rebind(
            fields, self._show(BROKER_UNIT, names),
            "ACTIVATION_BROKER_RUNTIME_REBOUND")
        return {
            "unit": BROKER_UNIT, "active_state": "active",
            "sub_state": "running", "main_pid": pid,
            "invocation_id": fields["InvocationID"],
            "exec_main_start_timestamp_monotonic_us": int(
                fields["ExecMainStartTimestampMonotonic"]),
            "process_starttime_ticks": process_starttime,
            "interpreter_path": str(PYTHON),
            "interpreter_sha256": digest_bytes(interpreter),
            "credential_source_path": credentials +
                "/hepta-broker-egress-policy.py",
            "credential_source_sha256": digest_bytes(credential),
            "installed_source_path": str(BROKER_HELPER),
            "installed_source_sha256": digest_bytes(installed),
            "cmdline_sha256": digest_bytes(cmdline),
            "status_text": fields["StatusText"], "tasks_current": 1,
            "deny_all_policy_sha256": deny["policy_sha256"],
            "authorized_connectors": 0, "authorized_uids": [],
            "protected_ports": 4,
            "unit_contract_sha256": self._unit_contract_sha(fields),
        }

    def attest_gateway(self) -> dict[str, Any]:
        names = (
            "LoadState", "ActiveState", "SubState", "MainPID", "InvocationID",
            "ExecMainStartTimestampMonotonic", "ExecStart", "EnvironmentFiles",
            "BindsTo", "After")
        fields = self._show(GATEWAY_SERVICE, names)
        _require(
            fields["LoadState"] == "loaded" and
            fields["ActiveState"] == "active" and
            fields["SubState"] == "running" and BROKER_UNIT in fields["BindsTo"] and
            str(PROFILE_PATH) in fields["EnvironmentFiles"],
            "ACTIVATION_GATEWAY_RUNTIME_INVALID")
        pid = int(fields["MainPID"])
        _require(pid > 1 and re.fullmatch(
            r"[0-9a-f]{32}", fields["InvocationID"]) is not None,
            "ACTIVATION_GATEWAY_RUNTIME_INVALID")
        expected = dict(PROFILE_ITEMS)
        expected.update({
            "HEPTA_TOOL_SOCKET": "/run/hepta-agent-alpha/tools.sock",
            "HEPTA_TOOL_AGENT_ID": "alpha",
            "HEPTA_TOOL_SUPERVISOR_LEASE_STORE":
                "/var/lib/hepta-tool-gateway-alpha/session-leases.hsl2",
            "HEPTA_TOOL_SUPERVISOR_AUDIT_JOURNAL":
                "/var/lib/hepta-tool-gateway-alpha/session-audit.jsonl",
        })
        # ``Type=simple`` may report the service active after fork but before
        # the systemd child has completed its final credential transition and
        # exec.  Both the root pre-exec child and the capability-empty,
        # non-dumpable gateway expose root-owned procfs projections, so wait
        # for the complete frozen environment projection on the same
        # manager-bound PID instead of treating procfs ownership as readiness.
        proc_payloads: dict[str, bytes] | None = None
        process_starttime = -1
        values: dict[str, str] = {}
        for attempt in range(21):
            proc_payloads = None
            last_error: ActivationError | None = None
            for owner_uid, owner_gid in (
                (ROOT_UID, ROOT_GID), (2101, 2101),
            ):
                try:
                    proc_payloads, process_starttime = _proc_payload_snapshot(
                        pid, ("environ",), uid=owner_uid, gid=owner_gid)
                    break
                except ActivationError as error:
                    if error.reason != "ACTIVATION_ANCHORED_DIRECTORY_INVALID":
                        raise
                    last_error = error
            if proc_payloads is not None:
                values = {}
                for entry in proc_payloads["environ"].split(b"\0"):
                    if entry.startswith(b"HEPTA_") and b"=" in entry:
                        key, value = entry.split(b"=", 1)
                        values[key.decode("ascii")] = value.decode("ascii")
                if values == expected:
                    break
            if attempt == 20:
                if proc_payloads is None and last_error is not None:
                    raise last_error
                break
            time.sleep(0.05)
        _require(proc_payloads is not None,
                 "ACTIVATION_GATEWAY_RUNTIME_INVALID")
        _require(values == expected, "ACTIVATION_GATEWAY_PROFILE_NOT_LOADED")
        executable, _ = secure_read(
            GATEWAY_EXECUTABLE, "ACTIVATION_GATEWAY_EXECUTABLE_INVALID",
            modes=frozenset({0o755}), maximum=64 * 1024 * 1024)
        profile, _ = secure_read(
            PROFILE_PATH, "ACTIVATION_PROFILE_INVALID",
            modes=frozenset({0o644}), maximum=65536)
        domain_config, _ = secure_read(
            DOMAIN_CONFIG, "ACTIVATION_DOMAIN_CONFIG_INVALID",
            modes=frozenset({0o600}), maximum=1024 * 1024)
        tool = _socket_identity(Path("/run/hepta-agent-alpha/tools.sock"), 2104, 2104)
        supervisor = _socket_identity(
            Path("/run/hepta-tool-gateway-alpha/session-supervisor.sock"), 2101, 2101)
        process_projection = digest_bytes(canonical_bytes(values))
        self._require_manager_rebind(
            fields, self._show(GATEWAY_SERVICE, names),
            "ACTIVATION_GATEWAY_RUNTIME_REBOUND")
        return {
            "unit": GATEWAY_SERVICE, "active_state": "active",
            "sub_state": "running", "gateway_main_pid": pid,
            "gateway_invocation_id": fields["InvocationID"],
            "gateway_exec_main_start_timestamp_monotonic_us": int(
                fields["ExecMainStartTimestampMonotonic"]),
            "process_starttime_ticks": process_starttime,
            "gateway_executable_path": str(GATEWAY_EXECUTABLE),
            "gateway_executable_sha256": digest_bytes(executable),
            "domain_config_sha256": digest_bytes(domain_config),
            "gateway_profile_path": str(PROFILE_PATH),
            "gateway_profile_sha256": digest_bytes(profile),
            "gateway_process_profile_sha256": process_projection,
            "execution_remote_mode": values["HEPTA_EXECUTION_REMOTE_MODE"],
            "tool_account": values["HEPTA_TOOL_ACCOUNT"],
            "execution_domain_id": values["HEPTA_EXECUTION_DOMAIN_ID"],
            "tool_allow_trade": values["HEPTA_TOOL_ALLOW_TRADE"],
            "session_templates": values["HEPTA_TOOL_SESSION_TEMPLATES"],
            "contract_bindings": values["HEPTA_TOOL_CONTRACT_BINDINGS"],
            "gateway_socket_path": "/run/hepta-agent-alpha/tools.sock",
            "gateway_socket_device": tool[0], "gateway_socket_inode": tool[1],
            "supervisor_socket_path":
                "/run/hepta-tool-gateway-alpha/session-supervisor.sock",
            "supervisor_socket_device": supervisor[0],
            "supervisor_socket_inode": supervisor[1],
            "unit_contract_sha256": self._unit_contract_sha(fields),
        }

    def attest_gateway_quarantined(self) -> dict[str, Any]:
        names = (
            "LoadState", "ActiveState", "SubState", "Job",
            "UnitFileState")
        manager_before = {
            unit: self._show(unit, names) for unit in GATEWAY_UNITS}
        if QUARANTINE_ATTESTATION_SEAM_HOOK is not None:
            QUARANTINE_ATTESTATION_SEAM_HOOK("AFTER_MANAGER_BEFORE")
        masks_before, identities_before = _gateway_masks_state()
        if QUARANTINE_ATTESTATION_SEAM_HOOK is not None:
            QUARANTINE_ATTESTATION_SEAM_HOOK("BETWEEN_MASK_SNAPSHOTS")
        masks_middle, identities_middle = _gateway_masks_state()
        if QUARANTINE_ATTESTATION_SEAM_HOOK is not None:
            QUARANTINE_ATTESTATION_SEAM_HOOK("BEFORE_MANAGER_AFTER")
        manager_after = {
            unit: self._show(unit, names) for unit in GATEWAY_UNITS}
        masks_after, identities_after = _gateway_masks_state()
        expected = {
            "LoadState": "masked", "ActiveState": "inactive",
            "SubState": "dead", "Job": "", "UnitFileState": "masked",
        }
        _require(
            manager_before == manager_after and
            all(state == expected for state in manager_after.values()),
            "ACTIVATION_GATEWAY_QUARANTINE_INVALID")
        _require(
            masks_before == masks_middle == masks_after and
            identities_before == identities_middle == identities_after,
            "ACTIVATION_GATEWAY_QUARANTINE_INVALID")
        body = {"manager_units": manager_after, "masks": masks_after}
        evidence = {
            **body,
            "unit_contract_sha256": digest_bytes(canonical_bytes(body)),
        }
        _require(_valid_gateway_quarantine(evidence),
                 "ACTIVATION_GATEWAY_QUARANTINE_INVALID")
        return evidence

    def quarantine(self) -> dict[str, Any]:
        errors: list[str] = []
        for argv in (
            (SYSTEMCTL, "stop", *GATEWAY_UNITS),
            (SYSTEMCTL, "mask", *GATEWAY_UNITS),
            (SYSTEMCTL, "mask", "--runtime", *GATEWAY_UNITS),
            (SYSTEMCTL, "daemon-reload"),
        ):
            try:
                self.mutate(argv)
            except ActivationError as error:
                errors.append(error.reason)
        gateway: dict[str, Any] | None = None
        try:
            gateway = self.attest_gateway_quarantined()
        except ActivationError as error:
            errors.append(error.reason)
        deny: dict[str, Any] | None = None
        try:
            deny = self.deny_all(tighten=True)
            deny = self.deny_all()
        except ActivationError as error:
            errors.append(error.reason)
        return {
            "errors": errors, "gateway_masked_stopped": gateway,
            "deny_all": deny,
            "complete": (
                not errors and _valid_gateway_quarantine(gateway) and
                _valid_deny_all(deny)),
        }


def _parse_proc_starttime(payload: bytes) -> int:
    try:
        contents = payload.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise ActivationError("ACTIVATION_PROCESS_INVALID") from error
    close = contents.rfind(") ")
    _require(close > 0, "ACTIVATION_PROCESS_INVALID")
    fields = contents[close + 2:].split()
    _require(len(fields) > 19 and fields[19].isdigit(),
             "ACTIVATION_PROCESS_INVALID")
    return int(fields[19])


def _proc_payload_snapshot(
    pid: int,
    names: tuple[str, ...],
    *,
    uid: int = ROOT_UID,
    gid: int = ROOT_GID,
) -> tuple[dict[str, bytes], int]:
    _require(pid > 1 and all(re.fullmatch(r"[a-z]+", name) for name in names),
             "ACTIVATION_PROCESS_INVALID")
    path = Path(f"/proc/{pid}")
    descriptor = open_anchored_directory(
        path, leaf_policy=(uid, gid, 0o555), procfs=True)
    try:
        before_payload, _ = _read_at(
            descriptor, "stat", "ACTIVATION_PROCESS_INVALID",
            expected_uid=uid, expected_gid=gid, modes=None,
            maximum=65536)
        before = _parse_proc_starttime(before_payload)
        result: dict[str, bytes] = {}
        for name in names:
            result[name], _ = _read_at(
                descriptor, name, "ACTIVATION_PROCESS_INVALID",
                expected_uid=uid, expected_gid=gid, modes=None,
                maximum=MAXIMUM_COMMAND_BYTES)
        after_payload, _ = _read_at(
            descriptor, "stat", "ACTIVATION_PROCESS_INVALID",
            expected_uid=uid, expected_gid=gid, modes=None,
            maximum=65536)
        after = _parse_proc_starttime(after_payload)
        _require(before == after, "ACTIVATION_PROCESS_REBOUND")
        canonical_rebind_directory(
            path, descriptor, leaf_policy=(uid, gid, 0o555),
            procfs=True)
        return result, before
    finally:
        os.close(descriptor)


def _proc_credential_snapshot(
    pid: int,
    credentials_directory: str,
    name: str,
    expected_starttime: int,
) -> bytes:
    _require(
        re.fullmatch(r"/run/credentials/[A-Za-z0-9_.@:-]+",
                     credentials_directory) is not None and
        re.fullmatch(r"[A-Za-z0-9_.-]+", name) is not None,
        "ACTIVATION_BROKER_SOURCE_INVALID")
    proc_path = Path(f"/proc/{pid}")
    process = open_anchored_directory(
        proc_path, leaf_policy=(ROOT_UID, ROOT_GID, 0o555), procfs=True)
    root = -1
    current = -1
    try:
        stat_before, _ = _read_at(
            process, "stat", "ACTIVATION_PROCESS_INVALID",
            expected_uid=ROOT_UID, expected_gid=ROOT_GID, modes=None,
            maximum=65536)
        _require(_parse_proc_starttime(stat_before) == expected_starttime,
                 "ACTIVATION_PROCESS_REBOUND")
        _require(os.readlink("root", dir_fd=process) == "/",
                 "ACTIVATION_PROCESS_ROOT_INVALID")
        # /proc/<pid>/root is a kernel-controlled procfs magic link.  Follow it
        # once into an fd, then traverse every real component no-follow.
        root = os.open(
            "root", os.O_RDONLY | os.O_DIRECTORY | CLOEXEC, dir_fd=process)
        _validate_directory(os.fstat(root))
        current = os.dup(root)
        for part in ("run", "credentials", credentials_directory.rsplit("/", 1)[1]):
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=current)
            try:
                opened = os.fstat(child)
                entry = os.stat(part, dir_fd=current, follow_symlinks=False)
                _validate_directory(opened)
                _require(stable_identity(opened) == stable_identity(entry),
                         "ACTIVATION_BROKER_SOURCE_INVALID")
            except BaseException:
                os.close(child)
                raise
            os.close(current)
            current = child
        payload, _ = _read_at(
            current, name, "ACTIVATION_BROKER_SOURCE_INVALID",
            expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o400}), maximum=64 * 1024 * 1024)
        stat_after, _ = _read_at(
            process, "stat", "ACTIVATION_PROCESS_INVALID",
            expected_uid=ROOT_UID, expected_gid=ROOT_GID, modes=None,
            maximum=65536)
        _require(_parse_proc_starttime(stat_after) == expected_starttime,
                 "ACTIVATION_PROCESS_REBOUND")
        canonical_rebind_directory(
            proc_path, process, leaf_policy=(ROOT_UID, ROOT_GID, 0o555),
            procfs=True)
        return payload
    except OSError as error:
        raise ActivationError("ACTIVATION_BROKER_SOURCE_INVALID") from error
    finally:
        if current >= 0:
            os.close(current)
        if root >= 0:
            os.close(root)
        os.close(process)


def _socket_identity(path: Path, uid: int, gid: int) -> tuple[int, int]:
    parent_policy = (
        (2101, 2101, 0o700)
        if path.parent == Path("/run/hepta-tool-gateway-alpha") else None)
    parent = open_anchored_directory(
        path.parent, leaf_policy=parent_policy)
    descriptor = -1
    try:
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(path.name, PATH_FLAGS, dir_fd=parent)
        opened = os.fstat(descriptor)
        final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _require(
            stable_identity(before) == stable_identity(opened) ==
            stable_identity(final) and stat.S_ISSOCK(opened.st_mode) and
            opened.st_uid == uid and opened.st_gid == gid and
            stat.S_IMODE(opened.st_mode) == 0o600 and opened.st_nlink == 1,
            "ACTIVATION_SOCKET_INVALID")
        canonical_rebind_directory(
            path.parent, parent, leaf_policy=parent_policy)
        return opened.st_dev, opened.st_ino
    except OSError as error:
        raise ActivationError("ACTIVATION_SOCKET_INVALID") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _validate_watch_private_directory() -> None:
    """Prove the fixed service-owned WATCH snapshot namespace is empty.

    systemd's ``StateDirectory=hepta-shadow-watch-alpha`` intentionally owns
    both the state directory and its ``private`` child as the WATCH service
    identity.  Keep that exception local to this fixed two-level namespace:
    every ancestor through ``/var/lib`` still uses the ordinary root-owned
    anchored traversal, while both service-owned directories are bound across
    their opened descriptors and a canonical reopen.

    The parent inventory is part of the proof.  Otherwise the service identity
    could rename a residue-bearing ``private`` directory aside and publish a
    new empty directory under the expected name.
    """

    reason = "ACTIVATION_AUTHORITY_RESIDUE"
    parent = -1
    private = -1
    rebound_parent = -1
    rebound_private = -1
    try:
        _require(
            WATCH_PRIVATE.is_absolute() and WATCH_PRIVATE.name == "private",
            reason)
        policy = (WATCH_UID, WATCH_GID, 0o700)
        parent = open_anchored_directory(
            WATCH_PRIVATE.parent, leaf_policy=policy)
        parent_opened = os.fstat(parent)
        _require(
            stat.S_ISDIR(parent_opened.st_mode) and
            parent_opened.st_uid == WATCH_UID and
            parent_opened.st_gid == WATCH_GID and
            stat.S_IMODE(parent_opened.st_mode) == 0o700 and
            sorted(os.listdir(parent)) == [WATCH_PRIVATE.name],
            reason)

        private_before = os.stat(
            WATCH_PRIVATE.name, dir_fd=parent, follow_symlinks=False)
        private = os.open(
            WATCH_PRIVATE.name, DIRECTORY_FLAGS, dir_fd=parent)
        private_opened = os.fstat(private)
        _require(
            stable_identity(private_before) ==
                stable_identity(private_opened) and
            stat.S_ISDIR(private_opened.st_mode) and
            private_opened.st_uid == WATCH_UID and
            private_opened.st_gid == WATCH_GID and
            stat.S_IMODE(private_opened.st_mode) == 0o700 and
            os.listdir(private) == [],
            reason)

        private_final = os.fstat(private)
        private_entry_final = os.stat(
            WATCH_PRIVATE.name, dir_fd=parent, follow_symlinks=False)
        parent_final = os.fstat(parent)
        _require(
            stable_identity(parent_opened) == stable_identity(parent_final) and
            stable_identity(private_opened) ==
                stable_identity(private_final) ==
                stable_identity(private_entry_final) and
            sorted(os.listdir(parent)) == [WATCH_PRIVATE.name] and
            os.listdir(private) == [],
            reason)

        canonical_rebind_directory(
            WATCH_PRIVATE.parent, parent, leaf_policy=policy)
        rebound_parent = open_anchored_directory(
            WATCH_PRIVATE.parent, leaf_policy=policy)
        rebound_private_before = os.stat(
            WATCH_PRIVATE.name,
            dir_fd=rebound_parent,
            follow_symlinks=False)
        rebound_private = os.open(
            WATCH_PRIVATE.name, DIRECTORY_FLAGS, dir_fd=rebound_parent)
        rebound_private_opened = os.fstat(rebound_private)
        rebound_parent_names = sorted(os.listdir(rebound_parent))
        rebound_private_names = os.listdir(rebound_private)
        rebound_parent_final = os.fstat(rebound_parent)
        rebound_private_final = os.fstat(rebound_private)
        rebound_private_entry = os.stat(
            WATCH_PRIVATE.name,
            dir_fd=rebound_parent,
            follow_symlinks=False)
        _require(
            stable_identity(parent_opened) ==
                stable_identity(rebound_parent_final) and
            stable_identity(private_opened) ==
                stable_identity(rebound_private_before) ==
                stable_identity(rebound_private_opened) ==
                stable_identity(rebound_private_final) ==
                stable_identity(rebound_private_entry) and
            rebound_parent_names == [WATCH_PRIVATE.name] and
            rebound_private_names == [] and
            sorted(os.listdir(rebound_parent)) == [WATCH_PRIVATE.name] and
            os.listdir(rebound_private) == [] and
            stable_identity(rebound_parent_final) ==
                stable_identity(os.fstat(rebound_parent)) and
            stable_identity(rebound_private_final) ==
                stable_identity(os.fstat(rebound_private)) ==
                stable_identity(os.stat(
                    WATCH_PRIVATE.name,
                    dir_fd=rebound_parent,
                    follow_symlinks=False)),
            reason)
    except ActivationError as error:
        if error.reason == reason:
            raise
        raise ActivationError(reason) from error
    except OSError as error:
        raise ActivationError(reason) from error
    finally:
        if rebound_private >= 0:
            os.close(rebound_private)
        if rebound_parent >= 0:
            os.close(rebound_parent)
        if private >= 0:
            os.close(private)
        if parent >= 0:
            os.close(parent)


def validate_local_boundaries() -> dict[str, Any]:
    sessions = -1
    idle_lock = -1
    profile, _ = secure_read(
        PROFILE_PATH, "ACTIVATION_PROFILE_INVALID", modes=frozenset({0o644}),
        maximum=65536)
    _require(profile == PROFILE_PAYLOAD, "ACTIVATION_PROFILE_INVALID")
    try:
        _require(_anchored_exists(WATCH_SESSIONS),
                 "ACTIVATION_AUTHORITY_RESIDUE")
        sessions = open_anchored_directory(
            WATCH_SESSIONS, leaf_policy=(ROOT_UID, ROOT_GID, 0o711))
        names = sorted(os.listdir(sessions))
        _require(names == [".session-bootstrap.lock"],
                 "ACTIVATION_AUTHORITY_RESIDUE")
        before = os.stat(
            names[0], dir_fd=sessions, follow_symlinks=False)
        idle_lock = os.open(
            names[0], os.O_RDWR | NOFOLLOW | CLOEXEC, dir_fd=sessions)
        opened = os.fstat(idle_lock)
        _require(
            stable_identity(before) == stable_identity(opened) and
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            opened.st_uid == ROOT_UID and opened.st_gid == ROOT_GID and
            stat.S_IMODE(opened.st_mode) == 0o600 and opened.st_size == 0,
            "ACTIVATION_AUTHORITY_RESIDUE")
        try:
            fcntl.flock(idle_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ActivationError("ACTIVATION_SESSION_BOOTSTRAP_BUSY") from error
        marker, _ = secure_read(
            KILL_SWITCH_PATH, "ACTIVATION_KILL_SWITCH_INVALID",
            expected_gid=PAPER_CONTROL_GID, modes=frozenset({0o440}), maximum=8,
            parent_leaf_policy=(ROOT_UID, PAPER_CONTROL_GID, 0o750))
        _require(marker == b"engaged", "ACTIVATION_KILL_SWITCH_INVALID")
        if _anchored_exists(PAPER_POLICY_ROOT):
            policies = open_anchored_directory(PAPER_POLICY_ROOT)
            try:
                _require(os.listdir(policies) == [],
                         "ACTIVATION_PAPER_POLICY_PRESENT")
                canonical_rebind_directory(PAPER_POLICY_ROOT, policies)
            finally:
                os.close(policies)
        _require(not _anchored_exists(WATCH_EXPORT),
                 "ACTIVATION_AUTHORITY_RESIDUE")
        _require(not _anchored_exists(WATCH_CUSTODIAN_TRANSACTION),
                 "ACTIVATION_AUTHORITY_RESIDUE")
        _validate_watch_private_directory()
        final_opened = os.fstat(idle_lock)
        final_entry = os.stat(
            names[0], dir_fd=sessions, follow_symlinks=False)
        _require(
            stable_identity(opened) == stable_identity(final_opened) ==
            stable_identity(final_entry), "ACTIVATION_AUTHORITY_RESIDUE")
        canonical_rebind_directory(
            WATCH_SESSIONS, sessions,
            leaf_policy=(ROOT_UID, ROOT_GID, 0o711))
        return {"export_absent": True, "sessions_authority_count": 0,
                "private_authority_count": 0,
                "custodian_transaction_absent": True,
                "session_bootstrap_idle_lock_observed": True}
    except OSError as error:
        raise ActivationError("ACTIVATION_AUTHORITY_RESIDUE") from error
    finally:
        if idle_lock >= 0:
            try:
                fcntl.flock(idle_lock, fcntl.LOCK_UN)
            finally:
                os.close(idle_lock)
        if sessions >= 0:
            os.close(sessions)


def validate_post_activation_paper_boundary() -> dict[str, Any]:
    """Continuously attest PAPER safety without owning WATCH authority."""

    profile, _ = secure_read(
        PROFILE_PATH, "ACTIVATION_PROFILE_INVALID", modes=frozenset({0o644}),
        maximum=65536)
    _require(profile == PROFILE_PAYLOAD, "ACTIVATION_PROFILE_INVALID")
    marker, _ = secure_read(
        KILL_SWITCH_PATH, "ACTIVATION_KILL_SWITCH_INVALID",
        expected_gid=PAPER_CONTROL_GID, modes=frozenset({0o440}), maximum=8,
        parent_leaf_policy=(ROOT_UID, PAPER_CONTROL_GID, 0o750))
    _require(marker == b"engaged", "ACTIVATION_KILL_SWITCH_INVALID")
    if _anchored_exists(PAPER_POLICY_ROOT):
        policies = open_anchored_directory(PAPER_POLICY_ROOT)
        try:
            _require(os.listdir(policies) == [],
                     "ACTIVATION_PAPER_POLICY_PRESENT")
            canonical_rebind_directory(PAPER_POLICY_ROOT, policies)
        finally:
            os.close(policies)
    return {
        "profile_sha256": "sha256:" + PROFILE_SHA256,
        "kill_switch_engaged": True,
        "campaign_policy_count": 0,
    }


def stale_paths() -> dict[int, tuple[Path, ...]]:
    base = Path("/var/lib/hepta/p1-admission")
    return {
        110: (base / "private/round110", base / "public/round110",
              base / "readers/hepta-p1-shadow-load-probe-round109-20260801",
              base / "readers/hepta-p1-shadow-soak-round110-20260801"),
        112: (base / "private/round112", base / "public/round112",
              base / "readers/hepta-p1-shadow-load-probe-round111-20260801",
              base / "readers/hepta-p1-shadow-soak-round112-20260801"),
    }


def stale_quarantine_paths() -> dict[int, tuple[Path, ...]]:
    return {
        110: (
            STALE_QUARANTINE_ROOT / "round110/private-round110",
            STALE_QUARANTINE_ROOT / "round110/public-round110",
            STALE_QUARANTINE_ROOT /
                "round110/hepta-p1-shadow-load-probe-round109-20260801",
            STALE_QUARANTINE_ROOT /
                "round110/hepta-p1-shadow-soak-round110-20260801",
        ),
        112: (
            STALE_QUARANTINE_ROOT / "round112/private-round112",
            STALE_QUARANTINE_ROOT / "round112/public-round112",
            STALE_QUARANTINE_ROOT /
                "round112/hepta-p1-shadow-load-probe-round111-20260801",
            STALE_QUARANTINE_ROOT /
                "round112/hepta-p1-shadow-soak-round112-20260801",
        ),
    }


STALE_FILE_SHA256 = {
    110: {
        (0, "launcher-receipt.json"):
            "3fe92cd29c23b78166fc557be2f88c29df1a41aec716958a3061331b3a1e6a35",
        (0, "launcher-state.json"):
            "f058f2562397c3fc6f9de16f950fec95dd4e0742591bb4d59b5f6dc85e67497b",
        (1, "load-probe-authority-marker.json"):
            "7e1fc292ab7e61fae014f148199776a287ec8bbcdf9762cb4588484c9394970b",
        (1, "load-probe-policy.json"):
            "7495751c7b4b0f5a1fab814f53a3fd54bfdb486e1a40980de4799bdf323ddd4a",
        (2, "controller-status.json"):
            "e758f8ef497cf3006e0cf8104701a684819001c9711780b4ce6f1f7ad14af09c",
    },
    112: {
        (0, "launcher-receipt.json"):
            "a0c61e38581f8918d540d7940bea2ebfe49e9a8263a40b2a3a95130f59e5c24d",
        (0, "launcher-state.json"):
            "c9b3357ee48a5b721937bc7e7da1e639f40811388925925e14f8fc29d3c1cb26",
        (1, "load-probe-authority-marker.json"):
            "7d348dc31c753bcd876a6bb8555de41e2b3003c6843a2f45599f0ad3c98f544d",
        (1, "load-probe-policy.json"):
            "9b1d7765fba1f7494975998d9c55991c6a1e7f791dd5aa183610443354b61ccd",
        (2, "controller-status.json"):
            "117cc81ed32754f8b3c79ea325c82c06ede2ff7a4acf62df058cba69b689d63a",
    },
}
STALE_ROOT_POLICIES = (
    (ROOT_UID, ROOT_GID, 0o700),
    (ROOT_UID, ROOT_GID, 0o755),
    (1000, 1000, 0o700),
    (1000, 1000, 0o700),
)
STALE_ROOT_ENTRIES = (
    ("launcher-receipt.json", "launcher-state.json"),
    ("load-probe-authority-marker.json", "load-probe-policy.json"),
    ("controller-status.json", "observer"),
    ("observer",),
)


@dataclass(frozen=True)
class StaleBundleValidation:
    round_number: int
    location: str
    paths: tuple[Path, ...]
    identities: tuple[tuple[int, ...], ...]
    evidence: dict[str, Any]


def _directory_entries(
    path: Path,
    policy: tuple[int, int, int],
    expected: tuple[str, ...],
) -> tuple[tuple[int, ...], int]:
    descriptor = open_anchored_directory(path, leaf_policy=policy)
    try:
        metadata = os.fstat(descriptor)
        try:
            entries = tuple(sorted(os.listdir(descriptor)))
        except OSError as error:
            raise ActivationError("ACTIVATION_STALE_BUNDLE_INVALID") from error
        _require(entries == tuple(sorted(expected)),
                 "ACTIVATION_STALE_BUNDLE_INVALID")
        canonical_rebind_directory(path, descriptor, leaf_policy=policy)
        return stable_identity(metadata), metadata.st_nlink
    finally:
        os.close(descriptor)


def _child_directory_entries(
    parent_path: Path,
    parent_policy: tuple[int, int, int],
    name: str,
    child_policy: tuple[int, int, int],
    expected: tuple[str, ...],
) -> tuple[tuple[int, ...], int]:
    parent = open_anchored_directory(
        parent_path, leaf_policy=parent_policy)
    child = -1
    try:
        child = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
        opened = os.fstat(child)
        entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _validate_directory(opened, child_policy)
        _require(stable_identity(opened) == stable_identity(entry),
                 "ACTIVATION_STALE_BUNDLE_REBOUND")
        _require(tuple(sorted(os.listdir(child))) == tuple(sorted(expected)),
                 "ACTIVATION_STALE_BUNDLE_INVALID")
        final = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _require(stable_identity(opened) == stable_identity(final),
                 "ACTIVATION_STALE_BUNDLE_REBOUND")
        canonical_rebind_directory(
            parent_path, parent, leaf_policy=parent_policy)
        return stable_identity(opened), opened.st_nlink
    except OSError as error:
        raise ActivationError("ACTIVATION_STALE_BUNDLE_INVALID") from error
    finally:
        if child >= 0:
            os.close(child)
        os.close(parent)


def _stale_json_semantics(
    round_number: int,
    root_index: int,
    name: str,
    document: dict[str, Any],
) -> None:
    reason = "ACTIVATION_STALE_BUNDLE_INVALID"
    probe_round = round_number - 1
    probe_id = f"hepta-p1-shadow-load-probe-round{probe_round}-20260801"
    formal_id = f"hepta-p1-shadow-soak-round{round_number}-20260801"
    if (root_index, name) == (0, "launcher-receipt.json"):
        _require(
            document.get("schema") ==
                "hepta.p1-shadow-admission-launcher-receipt.v1" and
            document.get("version") == 1 and
            document.get("status") == "FAILED_CLOSED" and
            document.get("domain_id") == DOMAIN and
            document.get("probe_campaign_id") == probe_id and
            document.get("formal_campaign_id") == formal_id and
            document.get("reason") == "P1_LAUNCHER_COMMAND_REJECTED" and
            document.get("authority_residue") is False and
            document.get("export_residue") is False and
            document.get("cleanup_errors") == [] and
            all(document.get(field) is False for field in (
                "paper_authorized", "live_authorized",
                "mutation_authorized", "direct_broker_access")), reason)
    elif (root_index, name) == (0, "launcher-state.json"):
        _require(
            document.get("schema") ==
                "hepta.p1-shadow-admission-launcher-state.v1" and
            document.get("version") == 1 and document.get("status") == "STARTING" and
            document.get("domain_id") == DOMAIN and
            document.get("probe_campaign_id") == probe_id and
            document.get("formal_campaign_id") == formal_id and
            all(document.get(field) is False for field in (
                "paper_authorized", "live_authorized",
                "mutation_authorized", "direct_broker_access")), reason)
    elif (root_index, name) == (1, "load-probe-authority-marker.json"):
        _require(
            document.get("schema") ==
                "hepta.p1-shadow-load-probe-authority-marker.v1" and
            document.get("version") == 1 and document.get("status") == "ACTIVE" and
            document.get("scope") == "LOAD_PROBE" and
            document.get("mode") == "LOAD_PROBE" and
            document.get("campaign_id") == probe_id and
            document.get("execution_binding_status") ==
                "PENDING_FIRST_SNAPSHOT" and
            all(document.get(field) is False for field in (
                "paper_authorized", "live_authorized",
                "mutation_authorized", "direct_broker_access")), reason)
    elif (root_index, name) == (1, "load-probe-policy.json"):
        _require(
            document.get("schema") ==
                "hepta.strategy-shadow-observation-policy.v1" and
            document.get("version") == 1 and
            document.get("campaign_id") == probe_id and
            all(document.get(field) is False for field in (
                "paper_authorized", "live_authorized", "mutation_attempted",
                "direct_broker_access")), reason)
    elif (root_index, name) == (2, "controller-status.json"):
        expected_state = "FAILED" if round_number == 110 else "WAITING_FOR_EXPORT"
        expected_reason = (
            "P1_CONTROLLER_ENVIRONMENT_INVALID" if round_number == 110
            else None)
        _require(
            document.get("schema") ==
                "hepta.p1-shadow-observer-controller-status.v1" and
            document.get("version") == 1 and
            document.get("campaign_id") == probe_id and
            document.get("state") == expected_state and
            document.get("reason") == expected_reason and
            all(document.get(field) is False for field in (
                "paper_authorized", "live_authorized", "mutation_attempted",
                "direct_broker_access")), reason)
    else:
        raise ActivationError(reason)


def _validate_stale_location(
    round_number: int,
    paths: tuple[Path, ...],
    location: str,
) -> StaleBundleValidation:
    _require(len(paths) == 4, "ACTIVATION_STALE_BUNDLE_INVALID")
    identities: list[tuple[int, ...]] = []
    inventory: list[dict[str, Any]] = []
    terminal_sha: str | None = None
    for index, root in enumerate(paths):
        identity, nlink = _directory_entries(
            root, STALE_ROOT_POLICIES[index], STALE_ROOT_ENTRIES[index])
        identities.append(identity)
        inventory.append({
            "root_index": index, "kind": "directory", "relative_path": ".",
            "uid": STALE_ROOT_POLICIES[index][0],
            "gid": STALE_ROOT_POLICIES[index][1],
            "mode": STALE_ROOT_POLICIES[index][2], "nlink": nlink,
        })
        if "observer" in STALE_ROOT_ENTRIES[index]:
            observer_identity, observer_nlink = _child_directory_entries(
                root, STALE_ROOT_POLICIES[index], "observer",
                (1000, 1000, 0o700), ())
            _require(observer_identity[3] == observer_nlink,
                     "ACTIVATION_STALE_BUNDLE_INVALID")
            inventory.append({
                "root_index": index, "kind": "directory",
                "relative_path": "observer", "uid": 1000, "gid": 1000,
                "mode": 0o700, "nlink": observer_nlink,
            })
        for (file_root, name), expected_sha in sorted(
                STALE_FILE_SHA256[round_number].items()):
            if file_root != index:
                continue
            uid, gid, _root_mode = STALE_ROOT_POLICIES[index]
            mode = 0o644 if index == 1 else 0o600
            payload, metadata = secure_read(
                root / name, "ACTIVATION_STALE_BUNDLE_INVALID",
                expected_uid=uid, expected_gid=gid,
                modes=frozenset({mode}), maximum=MAXIMUM_JSON_BYTES,
                parent_leaf_policy=STALE_ROOT_POLICIES[index])
            _require(hashlib.sha256(payload).hexdigest() == expected_sha,
                     "ACTIVATION_STALE_BUNDLE_INVALID")
            document = strict_document(
                payload, "ACTIVATION_STALE_BUNDLE_INVALID")
            _stale_json_semantics(round_number, index, name, document)
            inventory.append({
                "root_index": index, "kind": "file", "relative_path": name,
                "uid": uid, "gid": gid, "mode": mode,
                "bytes": metadata.st_size,
                "sha256": "sha256:" + expected_sha,
            })
            if (index, name) == (0, "launcher-receipt.json"):
                terminal_sha = "sha256:" + expected_sha
        rebound = open_anchored_directory(
            root, leaf_policy=STALE_ROOT_POLICIES[index])
        try:
            _require(stable_identity(os.fstat(rebound)) == identity,
                     "ACTIVATION_STALE_BUNDLE_REBOUND")
        finally:
            os.close(rebound)
    evidence = {
        "round": round_number,
        "status": "QUARANTINED" if location == "QUARANTINE" else
            "VALIDATED_FAILED_CLOSED",
        "bundle_sha256": digest_bytes(canonical_bytes(inventory)),
        "terminal_receipt_sha256": terminal_sha,
        "quarantine_root": (
            str(STALE_QUARANTINE_ROOT / f"round{round_number}")
            if location == "QUARANTINE" else None),
    }
    return StaleBundleValidation(
        round_number, location, paths, tuple(identities), evidence)


def validate_stale_bundles() -> list[StaleBundleValidation]:
    result: list[StaleBundleValidation] = []
    sources = stale_paths()
    destinations = stale_quarantine_paths()
    _require(set(sources) == {110, 112} and set(destinations) == {110, 112},
             "ACTIVATION_STALE_BUNDLE_INVALID")
    for round_number in (110, 112):
        source_flags = [_anchored_exists(path) for path in sources[round_number]]
        destination_flags = [
            _anchored_exists(path) for path in destinations[round_number]]
        if not any(source_flags) and not any(destination_flags):
            result.append(StaleBundleValidation(
                round_number, "ABSENT", sources[round_number], (), {
                    "round": round_number, "status": "ABSENT",
                    "bundle_sha256": None, "terminal_receipt_sha256": None,
                    "quarantine_root": None,
                }))
        elif all(source_flags) and not any(destination_flags):
            result.append(_validate_stale_location(
                round_number, sources[round_number], "SOURCE"))
        elif not any(source_flags) and all(destination_flags):
            result.append(_validate_stale_location(
                round_number, destinations[round_number], "QUARANTINE"))
        else:
            raise ActivationError("ACTIVATION_STALE_BUNDLE_PARTIAL")
    return result


def _rename_noreplace(
    source: Path,
    destination: Path,
    expected_identity: tuple[int, ...],
    source_policy: tuple[int, int, int],
) -> None:
    source_parent = open_anchored_directory(source.parent)
    destination_parent = open_anchored_directory(
        destination.parent, leaf_policy=(ROOT_UID, ROOT_GID, 0o700))
    try:
        source_before = os.stat(
            source.name, dir_fd=source_parent, follow_symlinks=False)
        _validate_directory(source_before, source_policy)
        _require(stable_identity(source_before) == expected_identity,
                 "ACTIVATION_STALE_BUNDLE_REBOUND")
        try:
            os.stat(destination.name, dir_fd=destination_parent,
                    follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ActivationError("ACTIVATION_STALE_QUARANTINE_EXISTS")
        ctypes.set_errno(0)
        result = LIBC.renameat2(
            source_parent, os.fsencode(source.name), destination_parent,
            os.fsencode(destination.name), RENAME_NOREPLACE)
        if result != 0:
            code = ctypes.get_errno()
            raise ActivationError(
                "ACTIVATION_STALE_QUARANTINE_FAILED") from OSError(
                    code, os.strerror(code))
        try:
            os.stat(source.name, dir_fd=source_parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ActivationError("ACTIVATION_STALE_QUARANTINE_FAILED")
        moved = os.stat(
            destination.name, dir_fd=destination_parent,
            follow_symlinks=False)
        _require(rename_identity(moved) == rename_identity(source_before),
                 "ACTIVATION_STALE_QUARANTINE_FAILED")
        os.fsync(source_parent)
        os.fsync(destination_parent)
        canonical_rebind_directory(source.parent, source_parent)
        canonical_rebind_directory(
            destination.parent, destination_parent,
            leaf_policy=(ROOT_UID, ROOT_GID, 0o700))
    except OSError as error:
        raise ActivationError("ACTIVATION_STALE_QUARANTINE_FAILED") from error
    finally:
        os.close(source_parent)
        os.close(destination_parent)


def quarantine_stale_bundles(
    validations: list[StaleBundleValidation],
) -> list[dict[str, Any]]:
    if any(item.location == "SOURCE" for item in validations):
        _ensure_owned_directory(STALE_QUARANTINE_ROOT.parent, 0o700)
        _ensure_owned_directory(STALE_QUARANTINE_ROOT, 0o700)
    destinations = stale_quarantine_paths()
    for item in validations:
        if item.location != "SOURCE":
            continue
        round_root = STALE_QUARANTINE_ROOT / f"round{item.round_number}"
        _ensure_owned_directory(round_root, 0o700)
        for index, (source, destination) in enumerate(zip(
                item.paths, destinations[item.round_number], strict=True)):
            _rename_noreplace(
                source, destination, item.identities[index],
                STALE_ROOT_POLICIES[index])
    final = validate_stale_bundles()
    _require(
        all(item.location in {"ABSENT", "QUARANTINE"} for item in final),
        "ACTIVATION_STALE_QUARANTINE_FAILED")
    return [item.evidence for item in final]


def acquire_lock() -> int:
    parent = open_anchored_directory(LOCK_PATH.parent)
    descriptor = -1
    try:
        canonical_rebind_directory(LOCK_PATH.parent, parent)
        try:
            before = os.stat(
                LOCK_PATH.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    LOCK_PATH.name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC,
                    0o600, dir_fd=parent)
                os.fchown(descriptor, ROOT_UID, ROOT_GID)
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
                os.fsync(parent)
                before = os.stat(
                    LOCK_PATH.name, dir_fd=parent, follow_symlinks=False)
            except FileExistsError:
                before = os.stat(
                    LOCK_PATH.name, dir_fd=parent, follow_symlinks=False)
            except OSError as error:
                raise ActivationError("ACTIVATION_LOCK_INVALID") from error
        except OSError as error:
            raise ActivationError("ACTIVATION_LOCK_INVALID") from error
        _require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
            before.st_uid == ROOT_UID and before.st_gid == ROOT_GID and
            stat.S_IMODE(before.st_mode) == 0o600 and before.st_size == 0,
            "ACTIVATION_LOCK_INVALID")
        if descriptor < 0:
            descriptor = os.open(
                LOCK_PATH.name, os.O_RDWR | NOFOLLOW | CLOEXEC,
                dir_fd=parent)
        opened = os.fstat(descriptor)
        _require(stable_identity(before) == stable_identity(opened),
                 "ACTIVATION_LOCK_REBOUND")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ActivationError("ACTIVATION_LOCK_BUSY") from error
        final = os.stat(
            LOCK_PATH.name, dir_fd=parent, follow_symlinks=False)
        _require(stable_identity(opened) == stable_identity(final),
                 "ACTIVATION_LOCK_REBOUND")
        canonical_rebind_directory(LOCK_PATH.parent, parent)
        result = descriptor
        descriptor = -1
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def validate_held_lock(descriptor: int) -> None:
    parent = open_anchored_directory(LOCK_PATH.parent)
    try:
        opened = os.fstat(descriptor)
        entry = os.stat(
            LOCK_PATH.name, dir_fd=parent, follow_symlinks=False)
        _require(
            stable_identity(opened) == stable_identity(entry) and
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            opened.st_uid == ROOT_UID and opened.st_gid == ROOT_GID and
            stat.S_IMODE(opened.st_mode) == 0o600 and opened.st_size == 0,
            "ACTIVATION_LOCK_REBOUND")
        canonical_rebind_directory(LOCK_PATH.parent, parent)
    except OSError as error:
        raise ActivationError("ACTIVATION_LOCK_INVALID") from error
    finally:
        os.close(parent)


def prepare_state_directories() -> None:
    _ensure_owned_directory(
        Path("/var/lib/heptatrader/p1-watch-activation"), 0o700)
    _ensure_owned_directory(STATE_ROOT, 0o700)
    _ensure_owned_directory(JOURNAL_ROOT, 0o700)


def _build_receipt(
    started: int, boot_id: str, profile_receipt: dict[str, Any],
    profile_receipt_payload: bytes, journal: Journal,
    preflight: dict[str, Any], broker_before: dict[str, Any],
    broker_after: dict[str, Any], gateway_after: dict[str, Any],
    reconcile_timer: dict[str, Any],
    watch_boundary: dict[str, Any], stale: list[dict[str, Any]],
    mutations: list[list[str]],
    shadow_install_evidence: dict[str, Any],
    predecessor_activation_success: dict[str, Any],
    predecessor_activation_failure: dict[str, Any],
) -> dict[str, Any]:
    validate_shadow_install_evidence(shadow_install_evidence)
    validate_predecessor_activation_success_evidence(
        predecessor_activation_success)
    validate_predecessor_activation_failure_evidence(
        predecessor_activation_failure)
    _require(set(broker_after) == BROKER_AFTER_FIELDS and
             set(gateway_after) == GATEWAY_AFTER_FIELDS and
             _valid_reconcile_timer_evidence(reconcile_timer) and
             set(watch_boundary) == WATCH_BOUNDARY_FIELDS and
             watch_boundary == {
                 "export_absent": True, "sessions_authority_count": 0,
                 "private_authority_count": 0,
                 "custodian_transaction_absent": True,
                 "session_bootstrap_idle_lock_observed": True,
             },
             "ACTIVATION_EVIDENCE_INVALID")
    body = {
        "schema": RECEIPT_SCHEMA, "version": RECEIPT_VERSION,
        "status": "WATCH_GATEWAY_ACTIVATED",
        "round": ROUND, "domain": DOMAIN, "started_at_ms": started,
        "completed_at_ms": time.time_ns() // 1_000_000, "boot_id": boot_id,
        "profile_deployment_receipt_path": str(PROFILE_RECEIPT_PATH),
        "profile_deployment_receipt_file_sha256":
            digest_bytes(profile_receipt_payload),
        "profile_deployment_receipt_body_sha256":
            profile_receipt["body_sha256"],
        "profile_sha256": "sha256:" + PROFILE_SHA256,
        "profile_bytes": len(PROFILE_PAYLOAD),
        "journal_sha256": journal.digest(),
        "broker_before": broker_before, "broker_after": broker_after,
        "gateway_after": gateway_after, "reconcile_timer": reconcile_timer,
        "paper_units": preflight["paper_units"],
        "kill_switch_engaged": True, "watch_boundary": watch_boundary,
        "stale_bundles": stale, "systemctl_mutations": mutations,
        "fresh_activation_transaction": True,
        "gateway_activated": True, "gateway_profile_loaded": True,
        "gateway_contract_binding_loaded": True,
        "broker_loaded_source_attested": True,
        "broker_deny_all_continuity_attested": True,
        "watch_authority_provisioned": False, "campaign_launched": False,
        "paper_authorized": False, "live_authorized": False,
        "mutation_attempted": False, "direct_broker_access": False,
        "admission_prerequisite_satisfied": True,
        "paper_prerequisite_satisfied": False,
        "shadow_install_evidence": shadow_install_evidence,
        "predecessor_activation_success": predecessor_activation_success,
        "predecessor_activation_failure": predecessor_activation_failure,
    }
    receipt = seal(body)
    _require(set(receipt) == RECEIPT_FIELDS, "ACTIVATION_RECEIPT_INVALID")
    return receipt


def _reconcile_timer_contract_sha256(evidence: dict[str, Any]) -> str:
    return digest_bytes(canonical_bytes({
        "LoadState": evidence.get("load_state"),
        "ActiveState": evidence.get("active_state"),
        "SubState": evidence.get("sub_state"),
        "Job": evidence.get("job"),
        "UnitFileState": evidence.get("unit_file_state"),
    }))


def _valid_reconcile_timer_evidence(evidence: Any) -> bool:
    return (
        isinstance(evidence, dict) and
        set(evidence) == RECONCILE_TIMER_FIELDS and
        evidence.get("unit") == RECONCILE_TIMER and
        evidence.get("load_state") == "loaded" and
        evidence.get("active_state") == "active" and
        evidence.get("sub_state") in {"waiting", "running"} and
        evidence.get("job") == "" and
        evidence.get("unit_file_state") == "enabled" and
        evidence.get("unit_contract_sha256") ==
            _reconcile_timer_contract_sha256(evidence)
    )


def _reconcile_timer_evidence_matches(
    observed: Any,
    recorded: Any,
) -> bool:
    if not (
            _valid_reconcile_timer_evidence(observed) and
            _valid_reconcile_timer_evidence(recorded)):
        return False
    stable_fields = (
        "unit", "load_state", "active_state", "job", "unit_file_state")
    return all(observed[field] == recorded[field] for field in stable_fields)


def _broker_evidence_matches(observed: Any, recorded: Any) -> bool:
    if not (
            isinstance(observed, dict) and
            isinstance(recorded, dict) and
            set(observed) == BROKER_AFTER_FIELDS and
            set(recorded) == BROKER_AFTER_FIELDS and
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(observed.get("unit_contract_sha256"))) is not None and
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(recorded.get("unit_contract_sha256"))) is not None):
        return False
    return all(
        observed[field] == recorded[field]
        for field in BROKER_AFTER_FIELDS
        if field != "unit_contract_sha256")


def _gateway_evidence_matches(observed: Any, recorded: Any) -> bool:
    if not (
            isinstance(observed, dict) and
            isinstance(recorded, dict) and
            set(observed) == GATEWAY_AFTER_FIELDS and
            set(recorded) == GATEWAY_AFTER_FIELDS and
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(observed.get("unit_contract_sha256"))) is not None and
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(recorded.get("unit_contract_sha256"))) is not None):
        return False
    return all(
        observed[field] == recorded[field]
        for field in GATEWAY_AFTER_FIELDS
        if field != "unit_contract_sha256")


def _validate_reconcile_runtime_evidence(
    selected: ProductionExecutor,
    receipt: dict[str, Any],
) -> None:
    checks = (
        (
            "BROKER",
            selected.attest_broker,
            lambda observed: _broker_evidence_matches(
                observed, receipt["broker_after"]),
        ),
        (
            "GATEWAY",
            selected.attest_gateway,
            lambda observed: _gateway_evidence_matches(
                observed, receipt["gateway_after"]),
        ),
        (
            "RECONCILE_TIMER",
            selected.attest_reconcile_timer,
            lambda observed: _reconcile_timer_evidence_matches(
                observed, receipt["reconcile_timer"]),
        ),
        (
            "PAPER_UNITS",
            selected.attest_paper_inactive,
            lambda observed: observed == receipt["paper_units"],
        ),
        (
            "BROKER_DENY_ALL",
            selected.deny_all,
            lambda observed: observed["policy_sha256"] ==
                receipt["broker_after"]["deny_all_policy_sha256"],
        ),
    )
    for component, observe, matches in checks:
        reason = "ACTIVATION_RUNTIME_DRIFT_" + component
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                observed = observe()
                if matches(observed):
                    break
            except Exception as error:
                last_error = error
            if attempt < 2:
                time.sleep(0.05)
        else:
            raise ActivationError(reason) from last_error


def validate_activation_receipt(
    receipt: Any,
    *,
    shadow_install_evidence: dict[str, Any],
    profile_receipt: dict[str, Any],
    profile_receipt_payload: bytes,
    predecessor_activation_success: dict[str, Any],
    predecessor_activation_failure: dict[str, Any],
) -> dict[str, Any]:
    reason = "ACTIVATION_RECEIPT_INVALID"
    _require(
        isinstance(receipt, dict) and set(receipt) == RECEIPT_FIELDS and
        receipt.get("schema") == RECEIPT_SCHEMA and
        receipt.get("version") == RECEIPT_VERSION and
        receipt.get("status") == "WATCH_GATEWAY_ACTIVATED" and
        receipt.get("round") == ROUND and receipt.get("domain") == DOMAIN and
        receipt.get("profile_deployment_receipt_path") ==
            str(PROFILE_RECEIPT_PATH) and
        receipt.get("profile_deployment_receipt_file_sha256") ==
            digest_bytes(profile_receipt_payload) and
        receipt.get("profile_deployment_receipt_body_sha256") ==
            profile_receipt.get("body_sha256") and
        receipt.get("shadow_install_evidence") == shadow_install_evidence and
        receipt.get("predecessor_activation_success") ==
            predecessor_activation_success and
        receipt.get("predecessor_activation_failure") ==
            predecessor_activation_failure and
        receipt.get("fresh_activation_transaction") is True and
        receipt.get("gateway_activated") is True and
        receipt.get("gateway_profile_loaded") is True and
        receipt.get("gateway_contract_binding_loaded") is True and
        receipt.get("broker_loaded_source_attested") is True and
        receipt.get("broker_deny_all_continuity_attested") is True and
        receipt.get("watch_authority_provisioned") is False and
        receipt.get("campaign_launched") is False and
        receipt.get("paper_authorized") is False and
        receipt.get("live_authorized") is False and
        receipt.get("mutation_attempted") is False and
        receipt.get("direct_broker_access") is False and
        receipt.get("admission_prerequisite_satisfied") is True and
        receipt.get("paper_prerequisite_satisfied") is False,
        reason)
    validate_shadow_install_evidence(receipt["shadow_install_evidence"])
    validate_predecessor_activation_success_evidence(
        receipt["predecessor_activation_success"])
    validate_predecessor_activation_failure_evidence(
        receipt["predecessor_activation_failure"])
    return receipt


def _failure_receipt(
    reason: str,
    quarantine: dict[str, Any],
    *,
    revision: int = 1,
    previous_failed_receipt: dict[str, Any] | None = None,
    predecessor_activation_success: dict[str, Any],
    predecessor_activation_failure: dict[str, Any],
) -> dict[str, Any]:
    validate_predecessor_activation_success_evidence(
        predecessor_activation_success)
    validate_predecessor_activation_failure_evidence(
        predecessor_activation_failure)
    return seal({
        "schema": "hepta.p1-watch-activation-failed-receipt.v3",
        "version": 3,
        "revision": revision,
        "status": "FAILED_CLOSED" if quarantine.get("complete") else
            "PENDING_EXPIRY",
        "round": ROUND, "domain": DOMAIN, "reason": reason,
        "completed_at_ms": time.time_ns() // 1_000_000,
        "quarantine": quarantine,
        "previous_failed_receipt": previous_failed_receipt,
        "predecessor_activation_success": predecessor_activation_success,
        "predecessor_activation_failure": predecessor_activation_failure,
        "paper_authorized": False,
        "live_authorized": False, "mutation_attempted": False,
        "direct_broker_access": False,
    })


def _validate_activation_guards(
    shadow_install_binding: ShadowInstallBinding,
    descriptor: int,
    predecessor_activation_success: dict[str, Any],
    predecessor_activation_failure: dict[str, Any],
) -> dict[str, Any]:
    evidence = validate_shadow_install_binding(shadow_install_binding)
    validate_held_lock(descriptor)
    predecessor_activation_success_evidence(predecessor_activation_success)
    predecessor_activation_failure_evidence(predecessor_activation_failure)
    return evidence


def activate(executor: ProductionExecutor | None = None) -> dict[str, Any]:
    _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
             "ACTIVATION_ROOT_REQUIRED")
    selected = executor or ProductionExecutor()
    predecessor_activation_success = (
        predecessor_activation_success_evidence())
    predecessor_activation_failure = (
        predecessor_activation_failure_evidence())
    initial_profile_payload, initial_profile_metadata = secure_read(
        PROFILE_RECEIPT_PATH, "ACTIVATION_PROFILE_RECEIPT_INVALID",
        modes=frozenset({0o600}))
    initial_profile_receipt = validate_profile_receipt(
        initial_profile_payload)
    shadow_install_binding = acquire_shadow_install_binding(
        initial_profile_receipt["shadow_install_evidence"])
    profile_artifact_binding: ProfileArtifactBinding | None = None
    descriptor = -1
    try:
        shadow_install_evidence = validate_shadow_install_binding(
            shadow_install_binding)
        profile_payload, profile_metadata = secure_read(
            PROFILE_RECEIPT_PATH, "ACTIVATION_PROFILE_RECEIPT_INVALID",
            modes=frozenset({0o600}))
        _require(
            profile_payload == initial_profile_payload and
            stable_identity(profile_metadata) ==
                stable_identity(initial_profile_metadata),
            "ACTIVATION_PROFILE_RECEIPT_REBOUND")
        profile_receipt = validate_profile_receipt(
            profile_payload, profile_metadata, shadow_install_evidence)
        profile_artifact_binding = acquire_profile_artifact_binding(
            profile_payload, profile_metadata, shadow_install_binding)
        _require(
            profile_artifact_binding.document == profile_receipt,
            "ACTIVATION_PROFILE_ARTIFACT_INVALID")
        validate_shadow_install_binding(shadow_install_binding)
        validate_profile_artifact_binding(
            profile_artifact_binding, shadow_install_binding)
        prepare_state_directories()
        validate_shadow_install_binding(shadow_install_binding)
        validate_profile_artifact_binding(
            profile_artifact_binding, shadow_install_binding)
        descriptor = acquire_lock()
        journal = Journal()
        started = time.time_ns() // 1_000_000
        try:
            try:
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                validate_profile_artifact_binding(
                    profile_artifact_binding, shadow_install_binding)
                _require(not _anchored_exists(ACTIVATION_RECEIPT_PATH) and
                         not _anchored_exists(
                             LEGACY_ACTIVATION_RECEIPT_PATH) and
                         not _anchored_exists(
                             LEGACY_ACTIVATION_RECEIPT_V2_PATH) and
                         not _anchored_exists(FAILED_RECEIPT_PATH) and
                         not _anchored_exists(
                             FAILED_RECEIPT_REPLACEMENT_PATH) and
                         not _anchored_exists(
                             FAILED_RECEIPT_PENDING_ARCHIVE_PATH) and
                         not journal.load(),
                         "ACTIVATION_ALREADY_TERMINAL")
                watch_boundary = validate_local_boundaries()
                preflight = selected.preflight()
                boot_payload, _ = secure_read(
                    BOOT_ID_PATH, "ACTIVATION_BOOT_ID_INVALID",
                    modes=frozenset({0o444}), maximum=64,
                    procfs_parent=True)
                try:
                    boot_id = boot_payload.decode("ascii").strip()
                except UnicodeError as error:
                    raise ActivationError(
                        "ACTIVATION_BOOT_ID_INVALID") from error
                _require(re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    boot_id) is not None, "ACTIVATION_BOOT_ID_INVALID")
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                validate_profile_artifact_binding(
                    profile_artifact_binding, shadow_install_binding)
                journal.append("PREPARED", {
                    "boot_id": boot_id,
                    "profile_receipt_sha256": digest_bytes(profile_payload),
                    "preflight_sha256": digest_bytes(
                        canonical_bytes(preflight)),
                    "shadow_install_evidence_sha256": digest_bytes(
                        canonical_bytes(shadow_install_evidence)),
                    "predecessor_activation_success_sha256": digest_bytes(
                        canonical_bytes(predecessor_activation_success)),
                    "predecessor_activation_failure_sha256": digest_bytes(
                        canonical_bytes(predecessor_activation_failure)),
                })
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                journal.append("TIMER_ENABLE_INTENT", {})
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                selected.mutate((
                    SYSTEMCTL, "enable", "--now", RECONCILE_TIMER))
                timer_state = selected.attest_reconcile_timer()
                journal.append("TIMER_ARMED", timer_state)
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                stale_validations = validate_stale_bundles()
                journal.append("STALE_QUARANTINE_INTENT", {
                    "bundles": [item.evidence for item in stale_validations]})
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                stale = quarantine_stale_bundles(stale_validations)
                journal.append("STALE_CLEAN", {"bundles": stale})
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                journal.append("DAEMON_RELOAD_INTENT", {})
                selected.mutate((SYSTEMCTL, "daemon-reload"))
                journal.append("MANAGER_RELOADED", {})
                journal.append("BROKER_STOP_INTENT", {})
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                selected.stop_broker()
                stopped_deny = selected.deny_all()
                journal.append("BROKER_STOPPED_DENY_ALL", stopped_deny)
                journal.append("BROKER_START_INTENT", {})
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                selected.mutate((SYSTEMCTL, "start", BROKER_UNIT))
                broker_after = selected.attest_broker()
                journal.append(
                    "BROKER_ACTIVE_DENY_ALL_ATTESTED", broker_after)
                journal.append("GATEWAY_UNMASK_INTENT", {})
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                selected.mutate((SYSTEMCTL, "unmask", *GATEWAY_UNITS))
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                selected.mutate((
                    SYSTEMCTL, "unmask", "--runtime", *GATEWAY_UNITS))
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                selected.mutate((SYSTEMCTL, "daemon-reload"))
                journal.append("GATEWAY_UNMASKED_RELOADED", {})
                journal.append("GATEWAY_START_INTENT", {})
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                selected.mutate((SYSTEMCTL, "start", GATEWAY_SERVICE))
                gateway_after = selected.attest_gateway()
                final_deny = selected.deny_all()
                _require(final_deny["policy_sha256"] ==
                         broker_after["deny_all_policy_sha256"],
                         "ACTIVATION_DENY_ALL_CONTINUITY_INVALID")
                final_boundary = validate_local_boundaries()
                final_paper = selected.attest_paper_inactive()
                journal.append("GATEWAY_ACTIVE_ATTESTED", gateway_after)
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                validate_profile_artifact_binding(
                    profile_artifact_binding, shadow_install_binding)
                journal.append("COMMIT_INTENT", {
                    "broker_after_sha256": digest_bytes(
                        canonical_bytes(broker_after)),
                    "gateway_after_sha256": digest_bytes(
                        canonical_bytes(gateway_after)),
                    "stale_bundles_sha256": digest_bytes(
                        canonical_bytes(stale)),
                })
                receipt = _build_receipt(
                    started, boot_id, profile_receipt, profile_payload, journal,
                    {**preflight, "paper_units": final_paper},
                    preflight["deny_all"], broker_after, gateway_after,
                    timer_state, final_boundary, stale, selected.mutations,
                    shadow_install_evidence,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                payload = canonical_bytes(receipt)
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                validate_profile_artifact_binding(
                    profile_artifact_binding, shadow_install_binding)
                _write_exclusive(PREPARED_RECEIPT_PATH, payload)
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                validate_profile_artifact_binding(
                    profile_artifact_binding, shadow_install_binding)
                _write_exclusive(ACTIVATION_RECEIPT_PATH, payload)
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                validate_profile_artifact_binding(
                    profile_artifact_binding, shadow_install_binding)
                return receipt
            except Exception as caught:
                primary = (
                    caught if isinstance(caught, ActivationError)
                    else ActivationError("ACTIVATION_INTERNAL_ERROR"))
                _reconcile_quarantine(
                    selected, journal, primary.reason,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                if primary is caught:
                    raise primary
                raise primary from caught
        finally:
            try:
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                if profile_artifact_binding is not None:
                    validate_profile_artifact_binding(
                        profile_artifact_binding, shadow_install_binding)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
                descriptor = -1
    finally:
        try:
            predecessor_activation_success_evidence(
                predecessor_activation_success)
            predecessor_activation_failure_evidence(
                predecessor_activation_failure)
            validate_shadow_install_binding(shadow_install_binding)
            if profile_artifact_binding is not None:
                validate_profile_artifact_binding(
                    profile_artifact_binding, shadow_install_binding)
        finally:
            release_shadow_install_binding(shadow_install_binding)


FAILED_RECEIPT_FIELDS = frozenset({
    "schema", "version", "revision", "status", "round", "domain",
    "reason", "completed_at_ms", "quarantine",
    "previous_failed_receipt", "predecessor_activation_success",
    "predecessor_activation_failure",
    "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "body_sha256",
})
PREVIOUS_FAILED_RECEIPT_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "device", "inode", "mode",
    "nlink", "uid", "gid", "bytes", "mtime_ns", "ctime_ns",
})


def _valid_deny_all(value: Any) -> bool:
    return (
        isinstance(value, dict) and set(value) == {
            "policy_sha256", "authorized_connectors", "authorized_uids",
            "protected_ports"} and
        re.fullmatch(r"sha256:[0-9a-f]{64}",
                     value.get("policy_sha256", "")) is not None and
        value.get("authorized_connectors") == 0 and
        value.get("authorized_uids") == [] and
        value.get("protected_ports") == 4)


def _validate_previous_failed_receipt(value: Any) -> None:
    reason = "ACTIVATION_FAILED_RECEIPT_INVALID"
    _require(
        isinstance(value, dict) and
        set(value) == PREVIOUS_FAILED_RECEIPT_FIELDS and
        value.get("path") == str(FAILED_RECEIPT_PATH) and
        re.fullmatch(r"sha256:[0-9a-f]{64}",
                     value.get("file_sha256", "")) is not None and
        re.fullmatch(r"sha256:[0-9a-f]{64}",
                     value.get("body_sha256", "")) is not None and
        all(type(value.get(field)) is int for field in (
            "device", "inode", "mode", "nlink", "uid", "gid", "bytes",
            "mtime_ns", "ctime_ns")) and
        value["device"] >= 0 and value["inode"] > 0 and
        stat.S_ISREG(value["mode"]) and
        stat.S_IMODE(value["mode"]) == 0o600 and
        value["nlink"] == 1 and value["uid"] == ROOT_UID and
        value["gid"] == ROOT_GID and
        0 < value["bytes"] <= MAXIMUM_JSON_BYTES and
        value["mtime_ns"] >= 0 and value["ctime_ns"] >= 0,
        reason)


def validate_failed_receipt(payload: bytes) -> dict[str, Any]:
    document = strict_document(payload, "ACTIVATION_FAILED_RECEIPT_INVALID")
    quarantine = document.get("quarantine")
    errors = quarantine.get("errors") if isinstance(quarantine, dict) else None
    deny_all = (
        quarantine.get("deny_all") if isinstance(quarantine, dict) else None)
    complete = (
        quarantine.get("complete") if isinstance(quarantine, dict) else None)
    _require(
        set(document) == FAILED_RECEIPT_FIELDS and
        document.get("schema") ==
            "hepta.p1-watch-activation-failed-receipt.v3" and
        document.get("version") == 3 and
        type(document.get("revision")) is int and
        document["revision"] in {1, 2} and
        document.get("status") in {"PENDING_EXPIRY", "FAILED_CLOSED"} and
        document.get("round") == ROUND and document.get("domain") == DOMAIN and
        type(document.get("completed_at_ms")) is int and
        document["completed_at_ms"] >= 0 and
        isinstance(document.get("reason"), str) and
        re.fullmatch(r"[A-Z][A-Z0-9_]{0,255}", document["reason"]) is not None and
        all(document.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")) and
        isinstance(quarantine, dict) and set(quarantine) == {
            "errors", "deny_all", "complete"} and
        isinstance(errors, list) and
        all(isinstance(item, str) and
            re.fullmatch(r"[A-Z][A-Z0-9_]{0,255}", item) is not None
            for item in errors) and
        type(complete) is bool and
        (deny_all is None or _valid_deny_all(deny_all)),
        "ACTIVATION_FAILED_RECEIPT_INVALID")
    validate_predecessor_activation_success_evidence(
        document.get("predecessor_activation_success"))
    validate_predecessor_activation_failure_evidence(
        document.get("predecessor_activation_failure"))
    if document["status"] == "PENDING_EXPIRY":
        _require(
            document["revision"] == 1 and
            document.get("previous_failed_receipt") is None and
            complete is False,
            "ACTIVATION_FAILED_RECEIPT_INVALID")
    else:
        _require(
            complete is True and errors == [] and _valid_deny_all(deny_all),
            "ACTIVATION_FAILED_RECEIPT_INVALID")
        if document["revision"] == 1:
            _require(document.get("previous_failed_receipt") is None,
                     "ACTIVATION_FAILED_RECEIPT_INVALID")
        else:
            _validate_previous_failed_receipt(
                document.get("previous_failed_receipt"))
    return document


def _previous_failed_receipt_evidence(
    payload: bytes,
    document: dict[str, Any],
    metadata: os.stat_result,
) -> dict[str, Any]:
    _require(
        document.get("status") == "PENDING_EXPIRY" and
        document.get("revision") == 1,
        "ACTIVATION_FAILED_RECEIPT_INVALID")
    return {
        "path": str(FAILED_RECEIPT_PATH),
        "file_sha256": digest_bytes(payload),
        "body_sha256": document["body_sha256"],
        "device": metadata.st_dev, "inode": metadata.st_ino,
        "mode": metadata.st_mode, "nlink": metadata.st_nlink,
        "uid": metadata.st_uid, "gid": metadata.st_gid,
        "bytes": metadata.st_size, "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


def _require_previous_receipt_match(
    payload: bytes,
    document: dict[str, Any],
    metadata: os.stat_result,
    evidence: dict[str, Any],
    *,
    moved: bool,
) -> None:
    _validate_previous_failed_receipt(evidence)
    validate_failed_receipt(payload)
    expected = _previous_failed_receipt_evidence(payload, document, metadata)
    fields = set(PREVIOUS_FAILED_RECEIPT_FIELDS)
    if moved:
        # Linux updates ctime on each rename.  Every other inode and body
        # attribute remains bound to the pre-exchange evidence.
        fields.remove("ctime_ns")
        expected["ctime_ns"] = evidence["ctime_ns"]
    _require(
        document["status"] == "PENDING_EXPIRY" and
        document["revision"] == 1 and
        all(expected[field] == evidence[field] for field in fields) and
        (moved or expected["ctime_ns"] == evidence["ctime_ns"]),
        "ACTIVATION_FAILED_RECEIPT_INVALID")


def _read_failed_receipt_path(
    path: Path,
) -> tuple[bytes, dict[str, Any], os.stat_result]:
    payload, metadata = secure_read(
        path, "ACTIVATION_FAILED_RECEIPT_INVALID",
        modes=frozenset({0o600}))
    return payload, validate_failed_receipt(payload), metadata


def _atomic_exchange_failed_receipts(
    pending_payload: bytes,
    pending_metadata: os.stat_result,
    terminal_payload: bytes,
    terminal_metadata: os.stat_result,
) -> tuple[os.stat_result, os.stat_result]:
    reason = "ACTIVATION_FAILED_RECEIPT_REPLACE_FAILED"
    _require(
        FAILED_RECEIPT_PATH.parent == FAILED_RECEIPT_REPLACEMENT_PATH.parent and
        FAILED_RECEIPT_PATH.name != FAILED_RECEIPT_REPLACEMENT_PATH.name,
        reason)
    parent = open_anchored_directory(FAILED_RECEIPT_PATH.parent)
    try:
        main_payload, main_before = _read_at(
            parent, FAILED_RECEIPT_PATH.name, reason,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o600}), maximum=MAXIMUM_JSON_BYTES)
        replacement_payload, replacement_before = _read_at(
            parent, FAILED_RECEIPT_REPLACEMENT_PATH.name, reason,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o600}), maximum=MAXIMUM_JSON_BYTES)
        _require(
            main_payload == pending_payload and
            stable_identity(main_before) == stable_identity(pending_metadata) and
            replacement_payload == terminal_payload and
            stable_identity(replacement_before) ==
                stable_identity(terminal_metadata),
            reason)
        canonical_rebind_directory(FAILED_RECEIPT_PATH.parent, parent)
        ctypes.set_errno(0)
        result = LIBC.renameat2(
            parent, os.fsencode(FAILED_RECEIPT_PATH.name), parent,
            os.fsencode(FAILED_RECEIPT_REPLACEMENT_PATH.name),
            RENAME_EXCHANGE)
        if result != 0:
            code = ctypes.get_errno()
            raise ActivationError(reason) from OSError(
                code, os.strerror(code))
        new_main_payload, new_main = _read_at(
            parent, FAILED_RECEIPT_PATH.name, reason,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o600}), maximum=MAXIMUM_JSON_BYTES)
        old_pending_payload, old_pending = _read_at(
            parent, FAILED_RECEIPT_REPLACEMENT_PATH.name, reason,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o600}), maximum=MAXIMUM_JSON_BYTES)
        _require(
            new_main_payload == terminal_payload and
            rename_identity(new_main) == rename_identity(replacement_before) and
            old_pending_payload == pending_payload and
            rename_identity(old_pending) == rename_identity(main_before),
            reason)
        os.fsync(parent)
        canonical_rebind_directory(FAILED_RECEIPT_PATH.parent, parent)
        return new_main, old_pending
    except OSError as error:
        raise ActivationError(reason) from error
    finally:
        os.close(parent)


def _rename_failed_receipt_noreplace(
    source: Path,
    destination: Path,
    expected_payload: bytes,
    expected_metadata: os.stat_result,
) -> os.stat_result:
    reason = "ACTIVATION_FAILED_RECEIPT_REPLACE_FAILED"
    _require(
        source.parent == destination.parent and source.name != destination.name,
        reason)
    parent = open_anchored_directory(source.parent)
    try:
        payload, before = _read_at(
            parent, source.name, reason, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o600}),
            maximum=MAXIMUM_JSON_BYTES)
        _require(
            payload == expected_payload and
            stable_identity(before) == stable_identity(expected_metadata),
            reason)
        try:
            os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ActivationError(reason)
        canonical_rebind_directory(source.parent, parent)
        ctypes.set_errno(0)
        result = LIBC.renameat2(
            parent, os.fsencode(source.name), parent,
            os.fsencode(destination.name), RENAME_NOREPLACE)
        if result != 0:
            code = ctypes.get_errno()
            raise ActivationError(reason) from OSError(
                code, os.strerror(code))
        try:
            os.stat(source.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ActivationError(reason)
        final_payload, final = _read_at(
            parent, destination.name, reason, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o600}),
            maximum=MAXIMUM_JSON_BYTES)
        _require(
            final_payload == expected_payload and
            rename_identity(final) == rename_identity(before), reason)
        os.fsync(parent)
        canonical_rebind_directory(source.parent, parent)
        return final
    except OSError as error:
        raise ActivationError(reason) from error
    finally:
        os.close(parent)


def _validate_recovered_terminal_link(
    terminal: dict[str, Any],
    pending_payload: bytes,
    pending: dict[str, Any],
    pending_metadata: os.stat_result,
    *,
    moved: bool,
) -> None:
    evidence = terminal.get("previous_failed_receipt")
    _require(
        terminal.get("status") == "FAILED_CLOSED" and
        terminal.get("revision") == 2 and
        terminal.get("reason") == pending.get("reason") and
        terminal.get("predecessor_activation_success") ==
            pending.get("predecessor_activation_success") and
        terminal.get("predecessor_activation_failure") ==
            pending.get("predecessor_activation_failure") and
        terminal.get("completed_at_ms", -1) >= pending.get(
            "completed_at_ms", 0) and
        isinstance(evidence, dict),
        "ACTIVATION_FAILED_RECEIPT_INVALID")
    _require_previous_receipt_match(
        pending_payload, pending, pending_metadata, evidence, moved=moved)


def _finalize_recovered_failed_receipt(
    terminal_payload: bytes,
    terminal: dict[str, Any],
) -> dict[str, Any]:
    reason = "ACTIVATION_FAILED_RECEIPT_INVALID"
    replacement_exists = _anchored_exists(FAILED_RECEIPT_REPLACEMENT_PATH)
    archive_exists = _anchored_exists(FAILED_RECEIPT_PENDING_ARCHIVE_PATH)
    _require(replacement_exists != archive_exists, reason)
    pending_path = (
        FAILED_RECEIPT_REPLACEMENT_PATH if replacement_exists else
        FAILED_RECEIPT_PENDING_ARCHIVE_PATH)
    pending_payload, pending, pending_metadata = _read_failed_receipt_path(
        pending_path)
    _validate_recovered_terminal_link(
        terminal, pending_payload, pending, pending_metadata, moved=True)
    if replacement_exists:
        pending_metadata = _rename_failed_receipt_noreplace(
            FAILED_RECEIPT_REPLACEMENT_PATH,
            FAILED_RECEIPT_PENDING_ARCHIVE_PATH,
            pending_payload, pending_metadata)
    final_payload, final, _ = _read_failed_receipt_path(FAILED_RECEIPT_PATH)
    archived_payload, archived, archived_metadata = _read_failed_receipt_path(
        FAILED_RECEIPT_PENDING_ARCHIVE_PATH)
    _require(
        final_payload == terminal_payload and final == terminal and
        archived_payload == pending_payload and archived == pending and
        not _anchored_exists(FAILED_RECEIPT_REPLACEMENT_PATH), reason)
    _validate_recovered_terminal_link(
        final, archived_payload, archived, archived_metadata, moved=True)
    return final


def _replace_pending_failed_receipt(
    pending_payload: bytes,
    pending: dict[str, Any],
    pending_metadata: os.stat_result,
    quarantine: dict[str, Any],
) -> dict[str, Any]:
    reason = "ACTIVATION_FAILED_RECEIPT_INVALID"
    _require(
        pending.get("status") == "PENDING_EXPIRY" and
        pending.get("revision") == 1 and quarantine.get("complete") is True and
        not _anchored_exists(FAILED_RECEIPT_PENDING_ARCHIVE_PATH), reason)
    evidence = _previous_failed_receipt_evidence(
        pending_payload, pending, pending_metadata)
    replacement_exists = _anchored_exists(FAILED_RECEIPT_REPLACEMENT_PATH)
    if not replacement_exists:
        terminal = _failure_receipt(
            pending["reason"], quarantine, revision=2,
            previous_failed_receipt=evidence,
            predecessor_activation_success=
                pending["predecessor_activation_success"],
            predecessor_activation_failure=
                pending["predecessor_activation_failure"])
        terminal_payload = canonical_bytes(terminal)
        _write_exclusive(FAILED_RECEIPT_REPLACEMENT_PATH, terminal_payload)
        terminal_metadata = secure_read(
            FAILED_RECEIPT_REPLACEMENT_PATH, reason,
            modes=frozenset({0o600}))[1]
        if FAILURE_REPLACEMENT_SEAM_HOOK is not None:
            FAILURE_REPLACEMENT_SEAM_HOOK("AFTER_REPLACEMENT_WRITE")
    else:
        terminal_payload, terminal, terminal_metadata = (
            _read_failed_receipt_path(FAILED_RECEIPT_REPLACEMENT_PATH))
    _validate_recovered_terminal_link(
        terminal, pending_payload, pending, pending_metadata, moved=False)
    terminal_metadata, _old_pending_metadata = (
        _atomic_exchange_failed_receipts(
            pending_payload, pending_metadata,
            terminal_payload, terminal_metadata))
    if FAILURE_REPLACEMENT_SEAM_HOOK is not None:
        FAILURE_REPLACEMENT_SEAM_HOOK("AFTER_EXCHANGE")
    final_payload, final, _ = _read_failed_receipt_path(FAILED_RECEIPT_PATH)
    _require(final_payload == terminal_payload and final == terminal, reason)
    return _finalize_recovered_failed_receipt(final_payload, final)


def _validate_quarantine_journal_prefix(
    records: list[JournalRecord],
    requested_reason: str,
) -> tuple[int, str]:
    phases = [record.phase for record in records]
    indices = [
        index for index, phase in enumerate(phases)
        if phase in QUARANTINE_PHASES]
    if not indices:
        return 0, requested_reason
    first = indices[0]
    suffix = records[first:]
    _require(
        [record.phase for record in suffix] ==
            list(QUARANTINE_PHASES[:len(suffix)]),
        "ACTIVATION_JOURNAL_INVALID")
    intent = suffix[0].document["evidence"]
    _require(
        set(intent) == {"reason"} and
        isinstance(intent.get("reason"), str) and
        re.fullmatch(r"[A-Z][A-Z0-9_]{0,255}", intent["reason"]) is not None,
        "ACTIVATION_JOURNAL_INVALID")
    if len(suffix) >= 2:
        gateway = suffix[1].document["evidence"]
        _require(
            set(gateway) == {"evidence"} and
            _valid_gateway_quarantine(gateway.get("evidence")),
            "ACTIVATION_JOURNAL_INVALID")
    if len(suffix) >= 3:
        deny = suffix[2].document["evidence"]
        _require(
            set(deny) == {"evidence"} and
            _valid_deny_all(deny.get("evidence")),
            "ACTIVATION_JOURNAL_INVALID")
    if len(suffix) >= 4:
        boundary = suffix[3].document["evidence"]
        _require(
            set(boundary) == WATCH_BOUNDARY_FIELDS and boundary == {
                "export_absent": True, "sessions_authority_count": 0,
                "private_authority_count": 0,
                "custodian_transaction_absent": True,
                "session_bootstrap_idle_lock_observed": True,
            }, "ACTIVATION_JOURNAL_INVALID")
    if len(suffix) >= 5:
        terminal = suffix[4].document["evidence"]
        _require(terminal == {"complete": True},
                 "ACTIVATION_JOURNAL_INVALID")
    return len(suffix), intent["reason"]


def _reconcile_quarantine(
    selected: ProductionExecutor,
    journal: Journal,
    reason: str,
    predecessor_activation_success: dict[str, Any],
    predecessor_activation_failure: dict[str, Any],
) -> dict[str, Any]:
    validate_predecessor_activation_success_evidence(
        predecessor_activation_success)
    validate_predecessor_activation_failure_evidence(
        predecessor_activation_failure)
    prefix_length = 0
    journal_usable = True
    try:
        records = journal.load()
        prefix_length, reason = _validate_quarantine_journal_prefix(
            records, reason)
        if prefix_length == 0:
            journal.append("QUARANTINE_INTENT", {"reason": reason})
            prefix_length = 1
    except Exception:
        journal_usable = False
    try:
        observed = selected.quarantine()
        gateway = observed.get("gateway_masked_stopped")
        quarantine = {
            "errors": list(observed.get("errors", [])),
            "deny_all": observed.get("deny_all"),
            "complete": (
                observed.get("complete") is True and
                observed.get("errors") == [] and
                _valid_gateway_quarantine(gateway) and
                _valid_deny_all(observed.get("deny_all"))),
        }
    except Exception:
        gateway = None
        quarantine = {
            "errors": ["ACTIVATION_QUARANTINE_INTERNAL_ERROR"],
            "deny_all": None, "complete": False}
    boundary: dict[str, Any] | None = None
    try:
        boundary = validate_local_boundaries()
    except ActivationError as error:
        quarantine["errors"].append(error.reason)
        quarantine["complete"] = False
    except Exception:
        quarantine["errors"].append(
            "ACTIVATION_QUARANTINE_INTERNAL_ERROR")
        quarantine["complete"] = False
    if journal_usable:
        try:
            if prefix_length == 1 and _valid_gateway_quarantine(gateway):
                journal.append("GATEWAY_MASKED_STOPPED", {
                    "evidence": gateway})
                prefix_length = 2
            if (prefix_length == 2 and
                    _valid_gateway_quarantine(gateway) and
                    _valid_deny_all(quarantine["deny_all"])):
                journal.append("BROKER_DENY_ALL", {
                    "evidence": quarantine["deny_all"]})
                prefix_length = 3
            if (prefix_length == 3 and
                    _valid_gateway_quarantine(gateway) and
                    _valid_deny_all(quarantine["deny_all"]) and
                    boundary is not None):
                journal.append("AUTHORITY_EMPTY", boundary)
                prefix_length = 4
            if prefix_length == 4 and quarantine["complete"]:
                journal.append("FAILED_CLOSED", {"complete": True})
                prefix_length = 5
        except Exception:
            quarantine["errors"].append("ACTIVATION_JOURNAL_INVALID")
            quarantine["complete"] = False
        if prefix_length < len(QUARANTINE_PHASES):
            quarantine["complete"] = False
    else:
        quarantine["complete"] = bool(
            quarantine["complete"] and boundary is not None)
    try:
        if not _anchored_exists(FAILED_RECEIPT_PATH):
            _write_exclusive(
                FAILED_RECEIPT_PATH,
                canonical_bytes(_failure_receipt(
                    reason, quarantine,
                    predecessor_activation_success=
                        predecessor_activation_success,
                    predecessor_activation_failure=
                        predecessor_activation_failure)))
    except Exception:
        pass
    return quarantine


def _reconcile_locked(
    selected: ProductionExecutor,
    descriptor: int,
    journal: Journal,
    shadow_install_binding: ShadowInstallBinding,
    shadow_install_evidence: dict[str, Any],
    profile_receipt: dict[str, Any],
    profile_receipt_payload: bytes,
    profile_artifact_binding: ProfileArtifactBinding,
    predecessor_activation_success: dict[str, Any],
    predecessor_activation_failure: dict[str, Any],
) -> str:
    _validate_activation_guards(
        shadow_install_binding, descriptor, predecessor_activation_success,
        predecessor_activation_failure)
    validate_profile_artifact_binding(
        profile_artifact_binding, shadow_install_binding)
    try:
        try:
            records = journal.load()
            active_exists = _anchored_exists(ACTIVATION_RECEIPT_PATH)
            legacy_active_exists = _anchored_exists(
                LEGACY_ACTIVATION_RECEIPT_PATH) or _anchored_exists(
                    LEGACY_ACTIVATION_RECEIPT_V2_PATH)
            failed_exists = _anchored_exists(FAILED_RECEIPT_PATH)
            replacement_exists = _anchored_exists(
                FAILED_RECEIPT_REPLACEMENT_PATH)
            archive_exists = _anchored_exists(
                FAILED_RECEIPT_PENDING_ARCHIVE_PATH)
        except Exception as error:
            _reconcile_quarantine(
                selected, journal, "ACTIVATION_STATE_INVALID",
                predecessor_activation_success,
                predecessor_activation_failure)
            raise ActivationError("ACTIVATION_STATE_INVALID") from error
        if legacy_active_exists:
            _reconcile_quarantine(
                selected, journal, "ACTIVATION_LEGACY_RECEIPT_PRESENT",
                predecessor_activation_success,
                predecessor_activation_failure)
            raise ActivationError("ACTIVATION_LEGACY_RECEIPT_PRESENT")
        if not failed_exists and (replacement_exists or archive_exists):
            _reconcile_quarantine(
                selected, journal, "ACTIVATION_STATE_INVALID",
                predecessor_activation_success,
                predecessor_activation_failure)
            raise ActivationError("ACTIVATION_STATE_INVALID")
        if not records and not active_exists and not failed_exists:
            return "NO_TRANSACTION"
        if failed_exists:
            try:
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                payload, failed, metadata = _read_failed_receipt_path(
                    FAILED_RECEIPT_PATH)
                _require(
                    failed["predecessor_activation_success"] ==
                        predecessor_activation_success and
                    failed["predecessor_activation_failure"] ==
                        predecessor_activation_failure,
                    "ACTIVATION_FAILED_RECEIPT_INVALID")
                quarantine_prefix, journal_reason = (
                    _validate_quarantine_journal_prefix(
                        records, failed["reason"]))
                _require(
                    quarantine_prefix == 0 or
                    journal_reason == failed["reason"],
                    "ACTIVATION_FAILED_RECEIPT_INVALID")
                if failed["status"] == "PENDING_EXPIRY":
                    _require(not archive_exists,
                             "ACTIVATION_FAILED_RECEIPT_INVALID")
                    _validate_activation_guards(
                        shadow_install_binding, descriptor,
                        predecessor_activation_success,
                        predecessor_activation_failure)
                    quarantine = _reconcile_quarantine(
                        selected, journal, failed["reason"],
                        predecessor_activation_success,
                        predecessor_activation_failure)
                    _require(
                        quarantine["complete"],
                        "ACTIVATION_QUARANTINE_INCOMPLETE")
                    validate_held_lock(descriptor)
                    failed = _replace_pending_failed_receipt(
                        payload, failed, metadata, quarantine)
                elif failed["revision"] == 2:
                    _validate_activation_guards(
                        shadow_install_binding, descriptor,
                        predecessor_activation_success,
                        predecessor_activation_failure)
                    failed = _finalize_recovered_failed_receipt(
                        payload, failed)
                else:
                    _require(not replacement_exists and not archive_exists,
                             "ACTIVATION_FAILED_RECEIPT_INVALID")
            except Exception as error:
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                _reconcile_quarantine(
                    selected, journal, "ACTIVATION_FAILED_RECEIPT_INVALID",
                    predecessor_activation_success,
                    predecessor_activation_failure)
                raise ActivationError(
                    "ACTIVATION_FAILED_RECEIPT_INVALID") from error
            _validate_activation_guards(
                shadow_install_binding, descriptor,
                predecessor_activation_success,
                predecessor_activation_failure)
            quarantine = selected.quarantine()
            _require(
                quarantine.get("complete") is True and
                quarantine.get("errors") == [] and
                _valid_gateway_quarantine(
                    quarantine.get("gateway_masked_stopped")) and
                _valid_deny_all(quarantine.get("deny_all")),
                "ACTIVATION_QUARANTINE_INCOMPLETE")
            validate_local_boundaries()
            _validate_activation_guards(
                shadow_install_binding, descriptor,
                predecessor_activation_success,
                predecessor_activation_failure)
            return "FAILED_CLOSED"
        if records and records[-1].phase == "COMMIT_INTENT" and active_exists:
            diagnostic_reason = "ACTIVATION_RUNTIME_DRIFT_RECEIPT"
            try:
                payload, _ = secure_read(
                    ACTIVATION_RECEIPT_PATH, "ACTIVATION_RECEIPT_INVALID",
                    modes=frozenset({0o600}))
                receipt = strict_document(
                    payload, "ACTIVATION_RECEIPT_INVALID")
                validate_activation_receipt(
                    receipt,
                    shadow_install_evidence=shadow_install_evidence,
                    profile_receipt=profile_receipt,
                    profile_receipt_payload=profile_receipt_payload,
                    predecessor_activation_success=
                        predecessor_activation_success,
                    predecessor_activation_failure=
                        predecessor_activation_failure)
                boot_payload, _ = secure_read(
                    BOOT_ID_PATH, "ACTIVATION_BOOT_ID_INVALID",
                    modes=frozenset({0o444}), maximum=64,
                    procfs_parent=True)
                _require(
                         receipt["fresh_activation_transaction"] is True and
                         receipt["journal_sha256"] == journal.digest() and
                         receipt["boot_id"] ==
                            boot_payload.decode("ascii").strip(),
                         diagnostic_reason)
                _validate_reconcile_runtime_evidence(selected, receipt)
                diagnostic_reason = "ACTIVATION_RUNTIME_DRIFT_PAPER_BOUNDARY"
                validate_post_activation_paper_boundary()
                diagnostic_reason = "ACTIVATION_RUNTIME_DRIFT_GUARDS"
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                diagnostic_reason = "ACTIVATION_RUNTIME_DRIFT_PROFILE_ARTIFACT"
                validate_profile_artifact_binding(
                    profile_artifact_binding, shadow_install_binding)
                return "WATCH_GATEWAY_ACTIVATED"
            except Exception as error:
                if (
                    isinstance(error, ActivationError) and
                    error.reason.startswith("ACTIVATION_RUNTIME_DRIFT_")
                ):
                    diagnostic_reason = error.reason
                try:
                    _validate_activation_guards(
                        shadow_install_binding, descriptor,
                        predecessor_activation_success,
                        predecessor_activation_failure)
                except ActivationError:
                    pass
                _reconcile_quarantine(
                    selected, journal, "ACTIVATION_RUNTIME_DRIFT",
                    predecessor_activation_success,
                    predecessor_activation_failure)
                raise ActivationError(diagnostic_reason) from error
        _validate_activation_guards(
            shadow_install_binding, descriptor,
            predecessor_activation_success,
            predecessor_activation_failure)
        quarantine = _reconcile_quarantine(
            selected, journal, "ACTIVATION_INCOMPLETE_TRANSACTION",
            predecessor_activation_success,
            predecessor_activation_failure)
        _require(quarantine["complete"], "ACTIVATION_QUARANTINE_INCOMPLETE")
        failed_payload, _ = secure_read(
            FAILED_RECEIPT_PATH, "ACTIVATION_FAILED_RECEIPT_INVALID",
            modes=frozenset({0o600}))
        validate_failed_receipt(failed_payload)
        _validate_activation_guards(
            shadow_install_binding, descriptor,
            predecessor_activation_success,
            predecessor_activation_failure)
        return "FAILED_CLOSED"
    finally:
        _validate_activation_guards(
            shadow_install_binding, descriptor,
            predecessor_activation_success,
            predecessor_activation_failure)
        validate_profile_artifact_binding(
            profile_artifact_binding, shadow_install_binding)


def _reconcile_state_observed() -> bool:
    journal = Journal()
    try:
        if journal.load():
            return True
        return any(_anchored_exists(path) for path in (
            ACTIVATION_RECEIPT_PATH, FAILED_RECEIPT_PATH,
            FAILED_RECEIPT_REPLACEMENT_PATH,
            FAILED_RECEIPT_PENDING_ARCHIVE_PATH))
    except ActivationError:
        return True


def _quarantine_under_verified_shadow_install(
    selected: ProductionExecutor,
    shadow_install_binding: ShadowInstallBinding,
    reason: str,
    predecessor_activation_success: dict[str, Any],
    predecessor_activation_failure: dict[str, Any],
) -> None:
    descriptor = -1
    validate_shadow_install_binding(shadow_install_binding)
    prepare_state_directories()
    validate_shadow_install_binding(shadow_install_binding)
    descriptor = acquire_lock()
    try:
        _validate_activation_guards(
            shadow_install_binding, descriptor,
            predecessor_activation_success,
            predecessor_activation_failure)
        _reconcile_quarantine(
            selected, Journal(), reason, predecessor_activation_success,
            predecessor_activation_failure)
        _validate_activation_guards(
            shadow_install_binding, descriptor,
            predecessor_activation_success,
            predecessor_activation_failure)
    finally:
        try:
            _validate_activation_guards(
                shadow_install_binding, descriptor,
                predecessor_activation_success,
                predecessor_activation_failure)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _quarantine_after_shadow_install_rejection(
    selected: ProductionExecutor,
    reason: str,
    predecessor_activation_success: dict[str, Any],
    predecessor_activation_failure: dict[str, Any],
) -> None:
    predecessor_activation_success_evidence(
        predecessor_activation_success)
    predecessor_activation_failure_evidence(
        predecessor_activation_failure)
    guard = acquire_shadow_install_quarantine_guard()
    descriptor = -1
    try:
        validate_shadow_install_quarantine_guard(guard)
        prepare_state_directories()
        validate_shadow_install_quarantine_guard(guard)
        descriptor = acquire_lock()
        try:
            validate_shadow_install_quarantine_guard(guard)
            validate_held_lock(descriptor)
            _reconcile_quarantine(
                selected, Journal(), reason, predecessor_activation_success,
                predecessor_activation_failure)
            validate_shadow_install_quarantine_guard(guard)
            validate_held_lock(descriptor)
        finally:
            try:
                validate_shadow_install_quarantine_guard(guard)
                validate_held_lock(descriptor)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
                descriptor = -1
    finally:
        try:
            predecessor_activation_success_evidence(
                predecessor_activation_success)
            predecessor_activation_failure_evidence(
                predecessor_activation_failure)
            validate_shadow_install_quarantine_guard(guard)
        finally:
            release_shadow_install_quarantine_guard(guard)


def reconcile(executor: ProductionExecutor | None = None) -> str:
    _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
             "ACTIVATION_ROOT_REQUIRED")
    if not _reconcile_state_observed():
        return "NO_TRANSACTION"
    selected = executor or ProductionExecutor()
    predecessor_activation_success = (
        predecessor_activation_success_evidence())
    predecessor_activation_failure = (
        predecessor_activation_failure_evidence())
    try:
        initial_profile_payload, initial_profile_metadata = secure_read(
            PROFILE_RECEIPT_PATH, "ACTIVATION_PROFILE_RECEIPT_INVALID",
            modes=frozenset({0o600}))
        initial_profile_receipt = validate_profile_receipt(
            initial_profile_payload)
        shadow_install_binding = acquire_shadow_install_binding(
            initial_profile_receipt["shadow_install_evidence"])
    except Exception as caught:
        primary = (
            caught if isinstance(caught, ActivationError)
            else ActivationError("ACTIVATION_SHADOW_INSTALL_INVALID"))
        _quarantine_after_shadow_install_rejection(
            selected, primary.reason, predecessor_activation_success,
            predecessor_activation_failure)
        if primary is caught:
            raise primary
        raise primary from caught
    descriptor = -1
    profile_artifact_binding: ProfileArtifactBinding | None = None
    try:
        try:
            shadow_install_evidence = validate_shadow_install_binding(
                shadow_install_binding)
            profile_receipt_payload, profile_receipt_metadata = secure_read(
                PROFILE_RECEIPT_PATH, "ACTIVATION_PROFILE_RECEIPT_INVALID",
                modes=frozenset({0o600}))
            _require(
                profile_receipt_payload == initial_profile_payload and
                stable_identity(profile_receipt_metadata) ==
                    stable_identity(initial_profile_metadata),
                "ACTIVATION_PROFILE_RECEIPT_REBOUND")
            profile_receipt = validate_profile_receipt(
                profile_receipt_payload, profile_receipt_metadata,
                shadow_install_evidence)
            profile_artifact_binding = acquire_profile_artifact_binding(
                profile_receipt_payload, profile_receipt_metadata,
                shadow_install_binding)
            _require(
                profile_artifact_binding.document == profile_receipt,
                "ACTIVATION_PROFILE_ARTIFACT_INVALID")
            validate_shadow_install_binding(shadow_install_binding)
            validate_profile_artifact_binding(
                profile_artifact_binding, shadow_install_binding)
            prepare_state_directories()
            validate_shadow_install_binding(shadow_install_binding)
            validate_profile_artifact_binding(
                profile_artifact_binding, shadow_install_binding)
        except Exception as caught:
            primary = (
                caught if isinstance(caught, ActivationError)
                else ActivationError("ACTIVATION_SHADOW_INSTALL_INVALID"))
            _quarantine_under_verified_shadow_install(
                selected, shadow_install_binding, primary.reason,
                predecessor_activation_success,
                predecessor_activation_failure)
            if primary is caught:
                raise primary
            raise primary from caught
        descriptor = acquire_lock()
        journal = Journal()
        try:
            return _reconcile_locked(
                selected, descriptor, journal, shadow_install_binding,
                shadow_install_evidence, profile_receipt,
                profile_receipt_payload, profile_artifact_binding,
                predecessor_activation_success,
                predecessor_activation_failure)
        finally:
            try:
                _validate_activation_guards(
                    shadow_install_binding, descriptor,
                    predecessor_activation_success,
                    predecessor_activation_failure)
                if profile_artifact_binding is not None:
                    validate_profile_artifact_binding(
                        profile_artifact_binding, shadow_install_binding)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
                descriptor = -1
    finally:
        try:
            predecessor_activation_success_evidence(
                predecessor_activation_success)
            predecessor_activation_failure_evidence(
                predecessor_activation_failure)
            validate_shadow_install_binding(shadow_install_binding)
            if profile_artifact_binding is not None:
                validate_profile_artifact_binding(
                    profile_artifact_binding, shadow_install_binding)
        finally:
            release_shadow_install_binding(shadow_install_binding)


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Activate or reconcile the fixed round114 alpha WATCH profile")
    parser.add_argument("action", choices=("activate", "reconcile"))
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    options = parse_arguments(arguments)
    try:
        if options.action == "activate":
            receipt = activate()
            print("hepta-p1-watch-activation-transaction: PASS receipt=" +
                  digest_bytes(canonical_bytes(receipt)))
        else:
            print("hepta-p1-watch-activation-transaction: PASS status=" +
                  reconcile())
        return 0
    except ActivationError as error:
        print("hepta-p1-watch-activation-transaction: FAIL " + error.reason,
              file=sys.stderr)
        return 1
    except Exception:
        print("hepta-p1-watch-activation-transaction: FAIL "
              "ACTIVATION_INTERNAL_ERROR", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
