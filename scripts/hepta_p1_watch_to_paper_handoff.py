#!/usr/bin/env -S /usr/bin/python3.12 -I -S

"""Crash-closed WATCH retirement handoff for PAPER admission.

This root-only transaction retires the fixed round114 alpha WATCH service
surface.  It never starts PAPER, grants trading authority, opens a broker
connection, or submits an order.  A successful receipt is only a passive
handoff prerequisite consumed by the independent PAPER admission verifier.

An interrupted success prefix is deliberately not resumed.  Reconciliation
can only prove an already-published terminal receipt or drive the host into a
non-authorizing FAILED_CLOSED terminal state.
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


ROOT_UID = 0
ROOT_GID = 0
PAPER_CONTROL_GID = 2121
ROUND = 114
DOMAIN = "alpha"
MINIMUM_TRADING_DAYS = 10
MAXIMUM_TRADING_DAYS = 20
MINIMUM_ELIGIBLE_DECISIONS = 200
MINIMUM_COMPLETE_PPM = 990_001
MINIMUM_BOOTTIME_DURATION_NS = 72 * 60 * 60 * 1_000_000_000
VALIDITY_MS = 5 * 60 * 1000
MAXIMUM_JSON_BYTES = 16 * 1024 * 1024
MAXIMUM_COMMAND_BYTES = 1024 * 1024

STATE_ROOT = Path("/var/lib/heptatrader/p1-watch-to-paper-handoff/round114")
JOURNAL_ROOT = STATE_ROOT / "journal"
LOCK_PATH = Path(
    "/var/lib/heptatrader/p1-watch-to-paper-handoff/.round114.lock")
DEFAULT_ACTIVATION_RECEIPT = Path(
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-round114-receipt-v4.json")
DEFAULT_P1_AUDIT_RECEIPT = Path(
    "/var/lib/hepta/p1-admission/p1-safety-soak-audit-receipt-v1.json")
DEFAULT_OUTPUT_RECEIPT = Path(
    "/var/lib/hepta/p1-admission/"
    "p1-watch-to-paper-handoff-receipt-v2.json")

SYSTEMCTL = "/usr/bin/systemctl"
PYTHON = "/usr/bin/python3.12"
BROKER_HELPER = Path("/usr/libexec/hepta-broker-egress-policy")
INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-watch-to-paper-handoff")
P1_AUDITOR_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-auditor")
P1_AUDITOR_PRODUCTION_MODE = "PRODUCTION_ROOT_AUDIT"
KILL_SWITCH_PATH = Path("/run/hepta/ib-paper-control-alpha/kill-switch")
GLOBAL_KILL_SWITCH_PATH = Path("/run/hepta/ib-paper-control/kill-switch")
GLOBAL_PAPER_CONTROL_GID = 2003
IDENTITY_MANIFEST_PATH = Path(
    "/etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json")
DISABLED_IDENTITY_MANIFEST_SHA256 = (
    "sha256:4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435")
PROFILE_TARGET_PATH = Path("/etc/heptatrader/trust-domains/alpha.env")
PROFILE_CANDIDATE_PATH = PROFILE_TARGET_PATH.with_name(
    ".alpha.env.hepta-p1-round114-watch-to-paper.candidate")
PROFILE_DORMANT_BACKUP_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-backups/"
    "round114-dormant-paper-to-watch/alpha.env")
PROFILE_FORWARD_RETAINED_PATH = PROFILE_TARGET_PATH.with_name(
    ".alpha.env.hepta-p1-round114-dormant-paper-to-watch.retained")
PROFILE_FORWARD_PREIMAGE_PATH = PROFILE_DORMANT_BACKUP_PATH.with_name(
    "preimage-evidence.json")
PROFILE_FORWARD_TRANSITION_RECEIPT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/"
    "round114-dormant-paper-to-watch.json")
PROFILE_DEPLOYMENT_RECEIPT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/"
    "round114-generation22.json")
PROFILE_RETIRED_WATCH_PATH = STATE_ROOT / "retired-watch-profile.env"
PAPER_RUNTIME_PROFILE_PATH = Path(
    "/etc/heptatrader/trust-domains/alpha.ib-paper.env")
PAPER_RUNTIME_PROFILE_CANDIDATE_PATH = PAPER_RUNTIME_PROFILE_PATH.with_name(
    ".alpha.ib-paper.env.hepta-p1-round114-runtime-harden.candidate")
PAPER_RUNTIME_PROFILE_BACKUP_PATH = (
    STATE_ROOT / "legacy-paper-runtime-profile-backup.env")
PAPER_RUNTIME_PROFILE_RETAINED_PATH = (
    STATE_ROOT / "retained-legacy-paper-runtime-profile.env")
LEGACY_PAPER_RUNTIME_PROFILE_BYTES = 776
LEGACY_PAPER_RUNTIME_PROFILE_SHA256 = (
    "sha256:2537f50ffe51f74e975f452e570d2c8ddaa82e1757955443014f5f28c9170f03")
HARDENED_PAPER_RUNTIME_PROFILE_BYTES = 767
HARDENED_PAPER_RUNTIME_PROFILE_SHA256 = (
    "sha256:99dd8ab1cd612989906a972abcaad0dd4234d908ea4ce295c0c01a9059604ee4")
PAPER_RUNTIME_PROFILE_KEYS = (
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
    "HEPTA_IB_EXECUTION_DOMAIN_ID",
    "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES",
    "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS",
    "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS",
    "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS",
)
PAPER_RUNTIME_PROFILE_FIXED_VALUES = {
    "HEPTA_IB_EXECUTION_MODE": "PAPER",
    "HEPTA_IB_PAPER_HOST": "127.0.0.1",
    "HEPTA_IB_PAPER_PORT": "4002",
    "HEPTA_IB_PAPER_CLIENT_ID": "701",
    "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL": "35000",
    "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE": "1",
    "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS": "1",
    "HEPTA_IB_PAPER_QUOTE_CONTRACTS": "EUR.USD|EUR|CASH|IDEALPRO|USD",
    "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT": "EUR.USD",
    "HEPTA_IB_EXECUTION_GATEWAY_UID": "2101",
    "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID": "alpha",
    "HEPTA_IB_EXECUTION_DOMAIN_ID": "PAPER:alpha",
    "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES": "16384",
    "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS": "2500",
    "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS": "30000",
    "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS": "180000",
}
PAPER_RUNTIME_PROFILE_LEGACY_LIMITS = {
    "HEPTA_IB_PAPER_MAX_ORDER_QTY": "25000",
    "HEPTA_IB_PAPER_MAX_GROSS_POSITION": "25000",
    "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS": "30000",
}
PAPER_RUNTIME_PROFILE_HARDENED_LIMITS = {
    "HEPTA_IB_PAPER_MAX_ORDER_QTY": "1",
    "HEPTA_IB_PAPER_MAX_GROSS_POSITION": "1",
    "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS": "5000",
}
WATCH_PROFILE_BYTES = 736
WATCH_PROFILE_SHA256 = (
    "sha256:ffcde4c46237ecacb3c32603f3aca0ba1a51c5b353b4fd2e5ab2f42ca1470e3f")
DORMANT_PAPER_PROFILE_BYTES = 878
DORMANT_PAPER_PROFILE_SHA256 = (
    "sha256:e5866254918ebb23c39c3e3630b9281ab780ad82c2cdb8f63e68749b1f4e9012")
PROFILE_TRANSITION_SCHEMA = (
    "hepta.p1-watch-profile-dormant-paper-transition-receipt.v2")
PROFILE_TRANSITION_STATUS = (
    "OFFLINE_DORMANT_PAPER_TO_PASSIVE_WATCH_TRANSITIONED")
PROFILE_DEPLOYMENT_SCHEMA = "hepta.p1-watch-profile-deployment-receipt.v8"
PROFILE_DEPLOYMENT_STATUS = "OFFLINE_PASSIVE_WATCH_PROFILE_REATTESTED"
PROFILE_PREIMAGE_SCHEMA = (
    "hepta.p1-watch-profile-transition-preimage-evidence.v1")
PROFILE_PREIMAGE_STATUS = "DORMANT_PAPER_PREIMAGE_BOUND"
PRODUCTION_MODE = "PRODUCTION_ROOT_SYSTEMD"

WATCH_UNITS = (
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
)
WATCH_TIMER_UNITS = frozenset({
    "hepta-p1-watch-activation-reconcile.timer",
    "hepta-shadow-watch-collector@alpha.timer",
    "hepta-shadow-watch-custodian-reconcile@alpha.timer",
})
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
PAPER_INERT_UNIT_FILE_STATES = {
    ".service": frozenset({"disabled", "masked", "static"}),
    ".socket": frozenset({"disabled", "masked"}),
}
WATCH_SOCKET_PATHS = (
    Path("/run/hepta-agent-alpha/tools.sock"),
    Path("/run/hepta-tool-gateway-alpha/session-supervisor.sock"),
    Path("/run/hepta-execution-alpha/execution.sock"),
    Path("/run/hepta-execution-alpha/events.sock"),
)
WATCH_SESSIONS = Path("/run/hepta-agent-alpha/sessions")
WATCH_PRIVATE = Path("/var/lib/hepta-shadow-watch-alpha/private")
WATCH_EXPORT = Path("/run/hepta-shadow-watch-export-alpha")
WATCH_CUSTODIAN_TRANSACTION = Path(
    "/var/lib/hepta-shadow-watch-custodian/alpha/transaction.json")
PERSISTENT_SYSTEMD_ROOT = Path("/etc/systemd/system")
RUNTIME_SYSTEMD_ROOT = Path("/run/systemd/system")
MASK_TARGET = "/dev/null"

SANITIZED_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "PYTHONNOUSERSITE": "1",
}

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
UNIT_TOKEN = re.compile(r"[A-Za-z0-9@_.:-]{1,128}")
TRANSIENT_UNIT = re.compile(
    r"hepta-p1-shadow-(?:admission|host|reader|observer)-"
    r"round[1-9][0-9]*\.(?:service|timer)")
MAXIMUM_TRANSIENT_UNITS = 256

NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW | CLOEXEC
READ_FLAGS = os.O_RDONLY | NOFOLLOW | CLOEXEC | getattr(os, "O_NONBLOCK", 0)
RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2
LIBC = ctypes.CDLL(None, use_errno=True)

RECEIPT_SCHEMA = "hepta.p1-watch-to-paper-handoff-receipt.v2"
RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "issued_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "producer", "production_mode",
    "activation_receipt", "p1_audit_receipt", "freeze_bundle",
    "watch_units_inactive",
    "watch_authority_count", "watch_socket_count", "watch_timer_count",
    "paper_units_inactive", "broker_deny_all", "kill_switch_engaged",
    "global_kill_switch_engaged", "identity_count",
    "identity_manifest_sha256", "paper_profile_restored",
    "paper_profile_restoration", "profile_candidate_absent",
    "paper_runtime_profile_hardened", "paper_runtime_profile_hardening",
    "paper_runtime_profile_candidate_absent",
    "crash_recovery_verified", "cleanup_residue_count",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized", "body_sha256",
})
REFERENCE_FIELDS = frozenset({"path", "file_sha256", "body_sha256"})
PRODUCER_FIELDS = frozenset({"path", "file_sha256"})
PROFILE_FILE_EVIDENCE_FIELDS = frozenset({
    "path", "file_sha256", "bytes", "mode", "uid", "gid", "nlink",
    "device", "inode", "mtime_ns", "ctime_ns",
})
PROFILE_SEALED_EVIDENCE_FIELDS = frozenset({
    *PROFILE_FILE_EVIDENCE_FIELDS, "body_sha256",
})
PROFILE_RESTORATION_FIELDS = frozenset({
    "schema", "version", "status", "target", "dormant_backup",
    "forward_retained_dormant", "retired_watch",
    "forward_transition_receipt", "profile_deployment_receipt",
    "forward_preimage_evidence", "candidate_path", "retired_watch_path",
    "exchange_method", "forward_only_after_exchange",
    "restore_intent_record_sha256", "restore_exchange_record_sha256",
})
PAPER_RUNTIME_PROFILE_HARDENING_FIELDS = frozenset({
    "schema", "version", "status", "target", "legacy_backup",
    "retained_legacy", "candidate_path", "retained_legacy_path",
    "exchange_method", "forward_only_after_exchange",
    "harden_intent_record_sha256", "harden_exchange_record_sha256",
})
PAPER_RUNTIME_PROFILE_STATE_FIELDS = frozenset({
    "state", "target", "legacy_backup", "candidate", "retained_legacy",
})
PROFILE_STATE_FIELDS = frozenset({
    "state", "target", "dormant_backup", "forward_retained_dormant",
    "candidate", "retired_watch", "forward_transition_receipt",
    "profile_deployment_receipt", "forward_preimage_evidence",
})
PROFILE_TRANSITION_FIELDS = frozenset({
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
PROFILE_PREIMAGE_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain", "transition_token",
    "created_at_ms", "target_before", "backup",
    "predecessor_profile_receipt", "preflight", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "shadow_install_evidence", "body_sha256",
})
PROFILE_DEPLOYMENT_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain", "started_at_ms",
    "finished_at_ms", "target_path", "receipt_staging_path",
    "target_before", "target_after", "target_final", "legacy_receipt",
    "legacy_backup", "legacy_retained_target", "preflight_before",
    "preflight_after", "preflight_final", "profile_content_changed",
    "target_written", "target_replaced", "services_started",
    "services_stopped", "services_restarted", "campaign_launched",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "activation_receipt_eligible",
    "preflight_reusable_for_activation", "broker_loaded_source_attested",
    "broker_deny_all_continuity_attested",
    "fresh_activation_transaction_required", "shadow_install_evidence",
    "predecessor_profile_receipt",
    "dormant_paper_to_watch_transition_receipt", "body_sha256",
})

ACTIVATION_FIELDS = frozenset({
    "schema", "version", "status", "round", "domain", "started_at_ms",
    "completed_at_ms", "boot_id", "profile_deployment_receipt_path",
    "profile_deployment_receipt_file_sha256",
    "profile_deployment_receipt_body_sha256", "profile_sha256",
    "profile_bytes", "journal_sha256", "broker_before", "broker_after",
    "gateway_after", "reconcile_timer", "paper_units",
    "kill_switch_engaged", "watch_boundary", "stale_bundles",
    "systemctl_mutations", "fresh_activation_transaction",
    "gateway_activated", "gateway_profile_loaded",
    "gateway_contract_binding_loaded", "broker_loaded_source_attested",
    "broker_deny_all_continuity_attested", "watch_authority_provisioned",
    "campaign_launched", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access",
    "admission_prerequisite_satisfied", "paper_prerequisite_satisfied",
    "shadow_install_evidence", "predecessor_activation_success",
    "predecessor_activation_failure",
    "body_sha256",
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
PREDECESSOR_ACTIVATION_SUCCESS_PATH = (
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-round95-receipt-v3.json")
PREDECESSOR_ACTIVATION_SUCCESS_FILE_SHA256 = (
    "sha256:c4b92e92bcdd55792e32fbe7f28a5399617352f7469e6661a09148efe6bdd5f3")
PREDECESSOR_ACTIVATION_SUCCESS_BODY_SHA256 = (
    "sha256:2d433239397a9820af0080628f424f5b6985d01ed9b5748a2064f903e1a2ed80")
PREDECESSOR_ACTIVATION_FAILURE_PATH = (
    "/var/lib/hepta/shadow-observation/"
    "p1-watch-activation-round95-failed-receipt-v2.json")
PREDECESSOR_ACTIVATION_FAILURE_FILE_SHA256 = (
    "sha256:860cf9ab2005ebcc2f6d5a83e931ebe18e6a5764f502a503aa305fb009bff55d")
PREDECESSOR_ACTIVATION_FAILURE_BODY_SHA256 = (
    "sha256:a3097ec265d66cb6ad99db8555b777c3fd0009cbe7f85e453a1d7a8f126174ed")
PREDECESSOR_ACTIVATION_FAILURE_JOURNAL_PATH = (
    "/var/lib/heptatrader/p1-watch-activation/round95/journal")
PREDECESSOR_ACTIVATION_FAILURE_JOURNAL_SHA256 = (
    "sha256:7d18a341a2e6ae322acd1b477f6287686af090e4a35716dc496bb8ab0f1a698e")
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
P1_AUDIT_FIELDS = frozenset({
    "schema", "version", "phase", "verdict", "campaign_id", "domain_id",
    "independent_auditor_id", "audited_at_ms", "campaign_spec_file_sha256",
    "campaign_spec_body_sha256", "freeze_bundle", "campaign_runtime",
    "producer",
    "production_mode", "source_manifest_sha256", "policy_sha256",
    "strategy_sha256", "evaluated_interval", "counts", "completeness",
    "checked_artifacts", "failed_invariants", "exposure_summary",
    "cleanup_status", "p1_safety_soak_gate_satisfied",
    "paper_test_admission_candidate", "safest_allowed_next_action",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
})
P1_CAMPAIGN_RUNTIME_SCHEMA = "hepta.p1-safety-soak-campaign-runtime.v1"
P1_CAMPAIGN_RUNTIME_REFERENCE_FIELDS = REFERENCE_FIELDS | frozenset({
    "schema",
})
P1_INTERVAL_FIELDS = frozenset({
    "clock_id", "boot_id", "start_boottime_ns", "end_boottime_ns",
    "duration_ns", "maximum_checkpoint_gap_ns", "consecutive",
    "continuity_origin_ms", "continuity_end_ms", "continuity_final_slot",
})
P1_COUNTS_FIELDS = frozenset({
    "launcher_receipts", "verified_closures", "continuity_checkpoints",
    "declared_trading_days", "observed_trading_days", "scheduled_decisions",
    "decision_receipts", "eligible_decisions", "complete_eligible_decisions",
    "incomplete_eligible_decisions", "catch_up_decisions", "planned_faults",
    "fault_results", "authority_snapshots", "cleanup_snapshots",
})
P1_COMPLETENESS_FIELDS = frozenset({
    "numerator", "denominator", "ppm", "strictly_greater_than_99_percent",
})
P1_CHECKED_ARTIFACT_FIELDS = frozenset({
    "role", "path", "file_sha256", "body_sha256",
})
P1_EXPOSURE_FIELDS = frozenset({
    "evidence_present", "maximum_connector_count",
    "maximum_authorized_uid_count", "maximum_paper_unit_active_count",
    "campaign_socket_ever_present", "kill_switch_continuously_engaged",
    "local_boundary_uncertain", "scope",
    "authoritative_account_state_observed",
})
P1_CLEANUP_FIELDS = frozenset({
    "required_subject_count", "verified_subject_count", "complete",
})

UNIT_STATE_FIELDS = frozenset({
    "load_state", "active_state", "sub_state", "job", "unit_file_state",
    "persistent_masked", "runtime_masked",
})
BROKER_STATE_FIELDS = frozenset({
    "policy_sha256", "authorized_connectors", "authorized_uids",
    "protected_ports",
})
SNAPSHOT_FIELDS = frozenset({
    "watch_units", "transient_units", "paper_units", "broker",
    "kill_switch_engaged", "global_kill_switch_engaged",
    "identity_count", "identity_manifest_sha256",
    "watch_authority_count", "watch_socket_count", "watch_timer_count",
    "cleanup_residue_count",
})
JOURNAL_FIELDS = frozenset({
    "schema", "version", "sequence", "phase", "recorded_at_ms",
    "previous_record_sha256", "evidence", "body_sha256",
})
CONTEXT_FIELDS = frozenset({
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "producer", "production_mode", "activation_receipt",
    "p1_audit_receipt", "freeze_bundle", "output_path",
})


def _unit_phase_token(unit: str) -> str:
    return unit.upper().replace("-", "_").replace(
        "@", "_AT_").replace(".", "_")


def _unit_success_phases(unit: str) -> tuple[str, ...]:
    token = _unit_phase_token(unit)
    return (
        f"DISABLE_{token}_INTENT", f"DISABLE_{token}_APPLIED",
        f"PERSISTENT_MASK_{token}_INTENT",
        f"PERSISTENT_MASK_{token}_APPLIED",
        f"RUNTIME_MASK_{token}_INTENT",
        f"RUNTIME_MASK_{token}_APPLIED",
    )


SUCCESS_PHASES = (
    "PREPARED",
    *(phase for unit in WATCH_UNITS for phase in _unit_success_phases(unit)),
    "DAEMON_RELOAD_INTENT", "DAEMON_RELOAD_APPLIED",
    "PROFILE_RESTORE_INTENT", "PROFILE_CANDIDATE_READY",
    "PROFILE_EXCHANGE_INTENT", "PROFILE_EXCHANGED",
    "PROFILE_RETIRED_WATCH_SEALED",
    "RUNTIME_PROFILE_HARDEN_INTENT",
    "RUNTIME_PROFILE_LEGACY_BACKUP_SEALED",
    "RUNTIME_PROFILE_CANDIDATE_READY",
    "RUNTIME_PROFILE_EXCHANGE_INTENT", "RUNTIME_PROFILE_EXCHANGED",
    "RUNTIME_PROFILE_RETAINED_LEGACY_SEALED",
    "FINAL_ATTESTATION", "COMMIT_INTENT", "COMPLETED",
)
FAILURE_PHASES = (
    "FAILURE_INTENT", "FAIL_CLOSE_ATTESTED", "FAILED_CLOSED",
)

MUTATION_SEAM_HOOK: Callable[[str, str | None], None] | None = None
PUBLISH_SEAM_HOOK: Callable[[str], None] | None = None


class HandoffError(RuntimeError):
    """Stable fail-closed transaction error."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise HandoffError(reason)


def canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                separators=(",", ":")) + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise HandoffError("HANDOFF_CANONICALIZATION_FAILED") from error


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _parse_paper_runtime_profile(
    payload: bytes, *, hardened: bool, reason: str,
) -> dict[str, str]:
    """Parse the fixed profile without embedding or emitting its account."""

    expected_size = (HARDENED_PAPER_RUNTIME_PROFILE_BYTES if hardened else
                     LEGACY_PAPER_RUNTIME_PROFILE_BYTES)
    expected_sha256 = (HARDENED_PAPER_RUNTIME_PROFILE_SHA256 if hardened else
                       LEGACY_PAPER_RUNTIME_PROFILE_SHA256)
    _require(
        len(payload) == expected_size and digest_bytes(payload) == expected_sha256
        and payload.endswith(b"\n") and b"\r" not in payload and
        b"\x00" not in payload,
        reason)
    try:
        lines = payload[:-1].decode("ascii", errors="strict").split("\n")
    except UnicodeError as error:
        raise HandoffError(reason) from error
    _require(len(lines) == len(PAPER_RUNTIME_PROFILE_KEYS), reason)
    pairs: list[tuple[str, str]] = []
    for line in lines:
        _require(line.count("=") == 1, reason)
        key, value = line.split("=", 1)
        _require(key and value and "\n" not in value, reason)
        pairs.append((key, value))
    _require(
        tuple(key for key, _value in pairs) == PAPER_RUNTIME_PROFILE_KEYS and
        len({key for key, _value in pairs}) == len(pairs), reason)
    values = dict(pairs)
    for key, expected in PAPER_RUNTIME_PROFILE_FIXED_VALUES.items():
        _require(values.get(key) == expected, reason)
    limits = (PAPER_RUNTIME_PROFILE_HARDENED_LIMITS if hardened else
              PAPER_RUNTIME_PROFILE_LEGACY_LIMITS)
    for key, expected in limits.items():
        _require(values.get(key) == expected, reason)
    account = values.get("HEPTA_IB_PAPER_ACCOUNT", "")
    _require(
        len(account) == 9 and
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{8}", account) is not None,
        reason)
    return values


def _harden_paper_runtime_profile(payload: bytes) -> bytes:
    reason = "HANDOFF_RUNTIME_PROFILE_TRANSFORMATION_INVALID"
    legacy = _parse_paper_runtime_profile(
        payload, hardened=False, reason=reason)
    hardened = dict(legacy)
    hardened.update(PAPER_RUNTIME_PROFILE_HARDENED_LIMITS)
    transformed = ("".join(
        f"{key}={hardened[key]}\n" for key in PAPER_RUNTIME_PROFILE_KEYS)
    ).encode("ascii")
    _parse_paper_runtime_profile(transformed, hardened=True, reason=reason)
    _require(
        {
            key for key in PAPER_RUNTIME_PROFILE_KEYS
            if legacy[key] != hardened[key]
        } == set(PAPER_RUNTIME_PROFILE_HARDENED_LIMITS), reason)
    return transformed


def seal(body: Mapping[str, Any]) -> dict[str, Any]:
    plain = dict(body)
    _require("body_sha256" not in plain, "HANDOFF_BODY_DIGEST_INVALID")
    return {**plain, "body_sha256": digest_bytes(canonical_bytes(plain))}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HandoffError("HANDOFF_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def strict_document(payload: bytes, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                HandoffError("HANDOFF_NON_FINITE_JSON")),
        )
    except HandoffError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise HandoffError(reason) from error
    _require(isinstance(value, dict), reason)
    _require(canonical_bytes(value) == payload, reason)
    body = dict(value)
    claimed = body.pop("body_sha256", None)
    _require(
        type(claimed) is str and DIGEST.fullmatch(claimed) is not None and
        claimed == digest_bytes(canonical_bytes(body)), reason)
    return value


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_uid, metadata.st_gid, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """Stable directory identity; child churn may change size/timestamps."""

    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_uid, metadata.st_gid,
    )


def _trusted_directory_identity(
    descriptor: int, reason: str,
) -> tuple[int, ...]:
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise HandoffError(reason) from error
    _require(
        stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == ROOT_UID and
        metadata.st_gid == ROOT_GID and
        stat.S_IMODE(metadata.st_mode) & 0o022 == 0,
        reason)
    return _directory_identity(metadata)


def _canonical_path(path: Path, reason: str) -> Path:
    _require(path.is_absolute(), reason)
    normalized = Path(os.path.normpath(os.fspath(path)))
    _require(normalized == path and path.name not in {"", ".", ".."}, reason)
    return normalized


def _open_anchored_directory(path: Path, reason: str) -> int:
    path = _canonical_path(path, reason)
    descriptor = -1
    try:
        descriptor = os.open("/", DIRECTORY_FLAGS)
        for component in path.parts[1:]:
            before = os.stat(component, dir_fd=descriptor,
                             follow_symlinks=False)
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            opened = os.fstat(child)
            _require(stat.S_ISDIR(opened.st_mode) and
                     _directory_identity(before) ==
                        _directory_identity(opened), reason)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except (OSError, HandoffError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        if isinstance(error, HandoffError):
            raise
        raise HandoffError(reason) from error


def _open_trusted_directory(path: Path, reason: str) -> int:
    """Open a path whose full ancestor chain is owner-controlled."""

    path = _canonical_path(path, reason)
    descriptor = -1
    allowed_uids = {0, ROOT_UID}
    try:
        descriptor = os.open("/", DIRECTORY_FLAGS)
        root = os.fstat(descriptor)
        _require(
            stat.S_ISDIR(root.st_mode) and root.st_uid in allowed_uids and
            stat.S_IMODE(root.st_mode) & 0o022 == 0, reason)
        for component in path.parts[1:]:
            before = os.stat(component, dir_fd=descriptor,
                             follow_symlinks=False)
            _require(
                stat.S_ISDIR(before.st_mode) and
                before.st_uid in allowed_uids and
                stat.S_IMODE(before.st_mode) & 0o022 == 0, reason)
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            opened = os.fstat(child)
            _require(_directory_identity(before) ==
                     _directory_identity(opened), reason)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except (OSError, HandoffError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        if isinstance(error, HandoffError):
            raise
        raise HandoffError(reason) from error


def secure_read(
    path: Path, reason: str, *, expected_uid: int | None = None,
    expected_gid: int | None = None,
    modes: frozenset[int] = frozenset({0o400, 0o440, 0o600, 0o640, 0o644}),
    maximum: int = MAXIMUM_JSON_BYTES, trusted_parent: bool = False,
) -> tuple[bytes, os.stat_result]:
    path = _canonical_path(path, reason)
    uid = ROOT_UID if expected_uid is None else expected_uid
    parent = (_open_trusted_directory(path.parent, reason) if trusted_parent
              else _open_anchored_directory(path.parent, reason))
    descriptor = -1
    try:
        parent_identity = _directory_identity(os.fstat(parent))
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        opened = os.fstat(descriptor)
        _require(
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            opened.st_uid == uid and
            (expected_gid is None or opened.st_gid == expected_gid) and
            stat.S_IMODE(opened.st_mode) in modes and
            0 < opened.st_size <= maximum and
            _identity(before) == _identity(opened), reason)
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _require(
            0 < len(payload) <= maximum and
            _identity(opened) == _identity(after) == _identity(final) and
            parent_identity == _directory_identity(os.fstat(parent)), reason)
        return payload, opened
    except (OSError, HandoffError) as error:
        if isinstance(error, HandoffError):
            raise
        raise HandoffError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _profile_file_evidence(
    path: Path, payload: bytes, metadata: os.stat_result,
) -> dict[str, Any]:
    return {
        "path": str(path), "file_sha256": digest_bytes(payload),
        "bytes": len(payload), "mode": metadata.st_mode,
        "uid": metadata.st_uid, "gid": metadata.st_gid,
        "nlink": metadata.st_nlink, "device": metadata.st_dev,
        "inode": metadata.st_ino, "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


def _read_profile_file(
    path: Path, reason: str, *, sha256: str, size: int, mode: int,
) -> tuple[bytes, os.stat_result, dict[str, Any]]:
    payload, metadata = secure_read(
        path, reason, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({mode}), maximum=max(size, 1), trusted_parent=True)
    evidence = _profile_file_evidence(path, payload, metadata)
    _require(
        evidence["file_sha256"] == sha256 and evidence["bytes"] == size and
        evidence["mode"] == stat.S_IFREG | mode and
        evidence["uid"] == ROOT_UID and evidence["gid"] == ROOT_GID and
        evidence["nlink"] == 1, reason)
    return payload, metadata, evidence


def _validate_profile_file_evidence(
    value: Any, reason: str, *, path: Path, sha256: str, size: int, mode: int,
) -> dict[str, Any]:
    _require(isinstance(value, dict) and
             set(value) == PROFILE_FILE_EVIDENCE_FIELDS, reason)
    _require(
        value.get("path") == str(path) and
        value.get("file_sha256") == sha256 and value.get("bytes") == size and
        value.get("mode") == stat.S_IFREG | mode and
        value.get("uid") == ROOT_UID and value.get("gid") == ROOT_GID and
        value.get("nlink") == 1 and
        all(type(value.get(field)) is int and value[field] >= 0 for field in (
            "device", "inode", "mtime_ns", "ctime_ns")) and
        value["inode"] > 0, reason)
    return value


def _validate_forward_profile_evidence(
    value: Any, reason: str, *, path: Path, sha256: str, size: int, mode: int,
) -> None:
    """Validate the profile deployer's larger fixed file-evidence shape."""

    _require(isinstance(value, dict), reason)
    _require(
        value.get("path") == str(path) and value.get("sha256") == sha256 and
        value.get("bytes") == size and
        value.get("mode") == stat.S_IFREG | mode and
        value.get("uid") == ROOT_UID and value.get("gid") == ROOT_GID and
        value.get("nlink") == 1 and
        all(type(value.get(field)) is int and value[field] >= 0 for field in (
            "device", "inode", "mtime_ns", "ctime_ns")) and
        value["inode"] > 0, reason)


def _read_profile_sealed_evidence(
    path: Path, reason: str, *, schema: str, version: int, status: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, metadata = secure_read(
        path, reason, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o600}), trusted_parent=True)
    document = strict_document(payload, reason)
    expected_fields = {
        PROFILE_TRANSITION_SCHEMA: PROFILE_TRANSITION_FIELDS,
        PROFILE_DEPLOYMENT_SCHEMA: PROFILE_DEPLOYMENT_FIELDS,
        PROFILE_PREIMAGE_SCHEMA: PROFILE_PREIMAGE_FIELDS,
    }.get(schema)
    _require(
        expected_fields is not None and set(document) == expected_fields and
        document.get("schema") == schema and document.get("version") == version
        and document.get("status") == status and document.get("round") == ROUND
        and document.get("domain") == DOMAIN, reason)
    evidence = {
        **_profile_file_evidence(path, payload, metadata),
        "body_sha256": document["body_sha256"],
    }
    return document, evidence


def _validate_profile_sealed_evidence(
    value: Any, reason: str, *, path: Path,
) -> dict[str, Any]:
    _require(isinstance(value, dict) and
             set(value) == PROFILE_SEALED_EVIDENCE_FIELDS, reason)
    _require(
        value.get("path") == str(path) and
        DIGEST.fullmatch(str(value.get("file_sha256"))) is not None and
        DIGEST.fullmatch(str(value.get("body_sha256"))) is not None and
        type(value.get("bytes")) is int and value["bytes"] > 0 and
        value.get("mode") == stat.S_IFREG | 0o600 and
        value.get("uid") == ROOT_UID and value.get("gid") == ROOT_GID and
        value.get("nlink") == 1 and
        all(type(value.get(field)) is int and value[field] >= 0 for field in (
            "device", "inode", "mtime_ns", "ctime_ns")) and
        value["inode"] > 0, reason)
    return value


def _validate_profile_reference_documents(
    transition: Mapping[str, Any], deployment: Mapping[str, Any],
    preimage: Mapping[str, Any], reason: str, *,
    transition_evidence: Mapping[str, Any],
    preimage_evidence: Mapping[str, Any],
) -> None:
    _require(
        transition.get("target_path") == str(PROFILE_TARGET_PATH) and
        transition.get("backup_path") == str(PROFILE_DORMANT_BACKUP_PATH) and
        transition.get("retained_target_path") ==
            str(PROFILE_FORWARD_RETAINED_PATH) and
        transition.get("profile_content_changed") is True and
        transition.get("target_written") is True and
        transition.get("target_replaced") is True and
        _all_false(transition, (
            "services_started", "services_stopped", "services_restarted",
            "campaign_launched", "paper_authorized", "live_authorized",
            "mutation_attempted", "direct_broker_access")), reason)
    _validate_forward_profile_evidence(
        transition.get("target_after"), reason, path=PROFILE_TARGET_PATH,
        sha256=WATCH_PROFILE_SHA256, size=WATCH_PROFILE_BYTES, mode=0o644)
    _validate_forward_profile_evidence(
        transition.get("target_final"), reason, path=PROFILE_TARGET_PATH,
        sha256=WATCH_PROFILE_SHA256, size=WATCH_PROFILE_BYTES, mode=0o644)
    _validate_forward_profile_evidence(
        transition.get("backup"), reason, path=PROFILE_DORMANT_BACKUP_PATH,
        sha256=DORMANT_PAPER_PROFILE_SHA256,
        size=DORMANT_PAPER_PROFILE_BYTES, mode=0o600)
    _validate_forward_profile_evidence(
        transition.get("retained_target"), reason,
        path=PROFILE_FORWARD_RETAINED_PATH,
        sha256=DORMANT_PAPER_PROFILE_SHA256,
        size=DORMANT_PAPER_PROFILE_BYTES, mode=0o600)
    def legacy(value: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "path": value["path"], "sha256": value["file_sha256"],
            "body_sha256": value["body_sha256"], "bytes": value["bytes"],
            "device": value["device"], "inode": value["inode"],
            "mode": value["mode"], "nlink": value["nlink"],
            "uid": value["uid"], "gid": value["gid"],
            "mtime_ns": value["mtime_ns"], "ctime_ns": value["ctime_ns"],
        }
    _require(transition.get("preimage_evidence") ==
             legacy(preimage_evidence), reason)
    _require(
        deployment.get("target_path") == str(PROFILE_TARGET_PATH) and
        deployment.get("fresh_activation_transaction_required") is True and
        _all_false(deployment, (
            "profile_content_changed", "target_written", "target_replaced",
            "services_started", "services_stopped", "services_restarted",
            "campaign_launched", "paper_authorized", "live_authorized",
            "mutation_attempted", "direct_broker_access",
            "activation_receipt_eligible",
            "preflight_reusable_for_activation",
            "broker_loaded_source_attested",
            "broker_deny_all_continuity_attested")), reason)
    for field in ("target_before", "target_after", "target_final"):
        _validate_forward_profile_evidence(
            deployment.get(field), reason, path=PROFILE_TARGET_PATH,
            sha256=WATCH_PROFILE_SHA256, size=WATCH_PROFILE_BYTES, mode=0o644)
    _require(deployment.get("dormant_paper_to_watch_transition_receipt") ==
             legacy(transition_evidence), reason)
    _validate_forward_profile_evidence(
        preimage.get("target_before"), reason, path=PROFILE_TARGET_PATH,
        sha256=DORMANT_PAPER_PROFILE_SHA256,
        size=DORMANT_PAPER_PROFILE_BYTES, mode=0o644)
    _validate_forward_profile_evidence(
        preimage.get("backup"), reason, path=PROFILE_DORMANT_BACKUP_PATH,
        sha256=DORMANT_PAPER_PROFILE_SHA256,
        size=DORMANT_PAPER_PROFILE_BYTES, mode=0o600)
    _require(_all_false(preimage, (
        "paper_authorized", "live_authorized", "mutation_attempted",
        "direct_broker_access")), reason)


def _validate_profile_state(value: Any) -> dict[str, Any]:
    reason = "HANDOFF_PROFILE_RESTORATION_STATE_INVALID"
    _require(isinstance(value, dict) and set(value) == PROFILE_STATE_FIELDS,
             reason)
    state = value.get("state")
    _require(state in {"PRE", "PRE_CANDIDATE", "POST_CANDIDATE", "RESTORED"},
             reason)
    target_sha = (WATCH_PROFILE_SHA256 if state in {"PRE", "PRE_CANDIDATE"}
                  else DORMANT_PAPER_PROFILE_SHA256)
    target_size = (WATCH_PROFILE_BYTES if state in {"PRE", "PRE_CANDIDATE"}
                   else DORMANT_PAPER_PROFILE_BYTES)
    _validate_profile_file_evidence(
        value.get("target"), reason, path=PROFILE_TARGET_PATH,
        sha256=target_sha, size=target_size, mode=0o644)
    _validate_profile_file_evidence(
        value.get("dormant_backup"), reason,
        path=PROFILE_DORMANT_BACKUP_PATH,
        sha256=DORMANT_PAPER_PROFILE_SHA256,
        size=DORMANT_PAPER_PROFILE_BYTES, mode=0o600)
    _validate_profile_file_evidence(
        value.get("forward_retained_dormant"), reason,
        path=PROFILE_FORWARD_RETAINED_PATH,
        sha256=DORMANT_PAPER_PROFILE_SHA256,
        size=DORMANT_PAPER_PROFILE_BYTES, mode=0o600)
    _validate_profile_sealed_evidence(
        value.get("forward_transition_receipt"), reason,
        path=PROFILE_FORWARD_TRANSITION_RECEIPT_PATH)
    _validate_profile_sealed_evidence(
        value.get("profile_deployment_receipt"), reason,
        path=PROFILE_DEPLOYMENT_RECEIPT_PATH)
    _validate_profile_sealed_evidence(
        value.get("forward_preimage_evidence"), reason,
        path=PROFILE_FORWARD_PREIMAGE_PATH)
    candidate = value.get("candidate")
    retired = value.get("retired_watch")
    if state == "PRE":
        _require(candidate is None and retired is None, reason)
    elif state == "PRE_CANDIDATE":
        _validate_profile_file_evidence(
            candidate, reason, path=PROFILE_CANDIDATE_PATH,
            sha256=DORMANT_PAPER_PROFILE_SHA256,
            size=DORMANT_PAPER_PROFILE_BYTES, mode=0o644)
        _require(retired is None, reason)
    elif state == "POST_CANDIDATE":
        _require(isinstance(candidate, dict), reason)
        candidate_mode = stat.S_IMODE(candidate.get("mode", -1))
        _require(candidate_mode in {0o600, 0o644}, reason)
        _validate_profile_file_evidence(
            candidate, reason, path=PROFILE_CANDIDATE_PATH,
            sha256=WATCH_PROFILE_SHA256, size=WATCH_PROFILE_BYTES,
            mode=candidate_mode)
        _require(retired is None, reason)
    else:
        _require(candidate is None, reason)
        _validate_profile_file_evidence(
            retired, reason, path=PROFILE_RETIRED_WATCH_PATH,
            sha256=WATCH_PROFILE_SHA256, size=WATCH_PROFILE_BYTES, mode=0o600)
    return value


def _profile_restoration_evidence(
    state: Mapping[str, Any], journal: "Journal",
) -> dict[str, Any]:
    state = _validate_profile_state(dict(state))
    _require(state["state"] == "RESTORED",
             "HANDOFF_PROFILE_RESTORATION_NOT_COMPLETE")
    records = {record.phase: record for record in journal.load()}
    _require(
        "PROFILE_RESTORE_INTENT" in records and
        "PROFILE_EXCHANGED" in records,
        "HANDOFF_PROFILE_RESTORATION_JOURNAL_INVALID")
    return {
        "schema": "hepta.p1-watch-to-paper-profile-restoration.v1",
        "version": 1, "status": "DORMANT_PAPER_PROFILE_RESTORED",
        "target": state["target"],
        "dormant_backup": state["dormant_backup"],
        "forward_retained_dormant": state["forward_retained_dormant"],
        "retired_watch": state["retired_watch"],
        "forward_transition_receipt": state["forward_transition_receipt"],
        "profile_deployment_receipt": state["profile_deployment_receipt"],
        "forward_preimage_evidence": state["forward_preimage_evidence"],
        "candidate_path": str(PROFILE_CANDIDATE_PATH),
        "retired_watch_path": str(PROFILE_RETIRED_WATCH_PATH),
        "exchange_method": "RENAME_EXCHANGE",
        "forward_only_after_exchange": True,
        "restore_intent_record_sha256":
            records["PROFILE_RESTORE_INTENT"].file_sha256,
        "restore_exchange_record_sha256":
            records["PROFILE_EXCHANGED"].file_sha256,
    }


def _validate_profile_restoration(value: Any) -> dict[str, Any]:
    reason = "HANDOFF_PROFILE_RESTORATION_EVIDENCE_INVALID"
    _require(isinstance(value, dict) and
             set(value) == PROFILE_RESTORATION_FIELDS, reason)
    _require(
        value.get("schema") ==
            "hepta.p1-watch-to-paper-profile-restoration.v1" and
        value.get("version") == 1 and
        value.get("status") == "DORMANT_PAPER_PROFILE_RESTORED" and
        value.get("candidate_path") == str(PROFILE_CANDIDATE_PATH) and
        value.get("retired_watch_path") == str(PROFILE_RETIRED_WATCH_PATH) and
        value.get("exchange_method") == "RENAME_EXCHANGE" and
        value.get("forward_only_after_exchange") is True, reason)
    _validate_profile_file_evidence(
        value.get("target"), reason, path=PROFILE_TARGET_PATH,
        sha256=DORMANT_PAPER_PROFILE_SHA256,
        size=DORMANT_PAPER_PROFILE_BYTES, mode=0o644)
    _validate_profile_file_evidence(
        value.get("dormant_backup"), reason,
        path=PROFILE_DORMANT_BACKUP_PATH,
        sha256=DORMANT_PAPER_PROFILE_SHA256,
        size=DORMANT_PAPER_PROFILE_BYTES, mode=0o600)
    _validate_profile_file_evidence(
        value.get("forward_retained_dormant"), reason,
        path=PROFILE_FORWARD_RETAINED_PATH,
        sha256=DORMANT_PAPER_PROFILE_SHA256,
        size=DORMANT_PAPER_PROFILE_BYTES, mode=0o600)
    _validate_profile_file_evidence(
        value.get("retired_watch"), reason, path=PROFILE_RETIRED_WATCH_PATH,
        sha256=WATCH_PROFILE_SHA256, size=WATCH_PROFILE_BYTES, mode=0o600)
    _validate_profile_sealed_evidence(
        value.get("forward_transition_receipt"), reason,
        path=PROFILE_FORWARD_TRANSITION_RECEIPT_PATH)
    _validate_profile_sealed_evidence(
        value.get("profile_deployment_receipt"), reason,
        path=PROFILE_DEPLOYMENT_RECEIPT_PATH)
    _validate_profile_sealed_evidence(
        value.get("forward_preimage_evidence"), reason,
        path=PROFILE_FORWARD_PREIMAGE_PATH)
    _digest(value.get("restore_intent_record_sha256"), reason)
    _digest(value.get("restore_exchange_record_sha256"), reason)
    return value


def _validate_paper_runtime_profile_state(value: Any) -> dict[str, Any]:
    reason = "HANDOFF_RUNTIME_PROFILE_HARDENING_STATE_INVALID"
    _require(isinstance(value, dict) and
             set(value) == PAPER_RUNTIME_PROFILE_STATE_FIELDS, reason)
    state = value.get("state")
    _require(state in {
        "LEGACY", "LEGACY_BACKED_UP", "LEGACY_CANDIDATE",
        "HARDENED_CANDIDATE", "HARDENED",
    }, reason)
    hardened_live = state in {"HARDENED_CANDIDATE", "HARDENED"}
    _validate_profile_file_evidence(
        value.get("target"), reason, path=PAPER_RUNTIME_PROFILE_PATH,
        sha256=(HARDENED_PAPER_RUNTIME_PROFILE_SHA256 if hardened_live else
                LEGACY_PAPER_RUNTIME_PROFILE_SHA256),
        size=(HARDENED_PAPER_RUNTIME_PROFILE_BYTES if hardened_live else
              LEGACY_PAPER_RUNTIME_PROFILE_BYTES), mode=0o644)
    backup = value.get("legacy_backup")
    if state == "LEGACY":
        _require(backup is None, reason)
    else:
        _validate_profile_file_evidence(
            backup, reason, path=PAPER_RUNTIME_PROFILE_BACKUP_PATH,
            sha256=LEGACY_PAPER_RUNTIME_PROFILE_SHA256,
            size=LEGACY_PAPER_RUNTIME_PROFILE_BYTES, mode=0o600)
    candidate = value.get("candidate")
    retained = value.get("retained_legacy")
    if state in {"LEGACY", "LEGACY_BACKED_UP"}:
        _require(candidate is None and retained is None, reason)
    elif state == "LEGACY_CANDIDATE":
        _validate_profile_file_evidence(
            candidate, reason, path=PAPER_RUNTIME_PROFILE_CANDIDATE_PATH,
            sha256=HARDENED_PAPER_RUNTIME_PROFILE_SHA256,
            size=HARDENED_PAPER_RUNTIME_PROFILE_BYTES, mode=0o644)
        _require(retained is None, reason)
    elif state == "HARDENED_CANDIDATE":
        _require(isinstance(candidate, dict), reason)
        candidate_mode = stat.S_IMODE(candidate.get("mode", -1))
        _require(candidate_mode in {0o600, 0o644}, reason)
        _validate_profile_file_evidence(
            candidate, reason, path=PAPER_RUNTIME_PROFILE_CANDIDATE_PATH,
            sha256=LEGACY_PAPER_RUNTIME_PROFILE_SHA256,
            size=LEGACY_PAPER_RUNTIME_PROFILE_BYTES, mode=candidate_mode)
        _require(retained is None, reason)
    else:
        _require(candidate is None, reason)
        _validate_profile_file_evidence(
            retained, reason, path=PAPER_RUNTIME_PROFILE_RETAINED_PATH,
            sha256=LEGACY_PAPER_RUNTIME_PROFILE_SHA256,
            size=LEGACY_PAPER_RUNTIME_PROFILE_BYTES, mode=0o600)
    return value


def _paper_runtime_profile_hardening_evidence(
    state: Mapping[str, Any], journal: "Journal",
) -> dict[str, Any]:
    state = _validate_paper_runtime_profile_state(dict(state))
    _require(state["state"] == "HARDENED",
             "HANDOFF_RUNTIME_PROFILE_HARDENING_NOT_COMPLETE")
    records = {record.phase: record for record in journal.load()}
    _require(
        "RUNTIME_PROFILE_HARDEN_INTENT" in records and
        "RUNTIME_PROFILE_EXCHANGED" in records,
        "HANDOFF_RUNTIME_PROFILE_HARDENING_JOURNAL_INVALID")
    return {
        "schema": "hepta.p1-watch-to-paper-runtime-profile-hardening.v1",
        "version": 1, "status": "PAPER_RUNTIME_PROFILE_HARDENED",
        "target": state["target"],
        "legacy_backup": state["legacy_backup"],
        "retained_legacy": state["retained_legacy"],
        "candidate_path": str(PAPER_RUNTIME_PROFILE_CANDIDATE_PATH),
        "retained_legacy_path": str(PAPER_RUNTIME_PROFILE_RETAINED_PATH),
        "exchange_method": "RENAME_EXCHANGE",
        "forward_only_after_exchange": True,
        "harden_intent_record_sha256":
            records["RUNTIME_PROFILE_HARDEN_INTENT"].file_sha256,
        "harden_exchange_record_sha256":
            records["RUNTIME_PROFILE_EXCHANGED"].file_sha256,
    }


def _validate_paper_runtime_profile_hardening(
    value: Any,
) -> dict[str, Any]:
    reason = "HANDOFF_RUNTIME_PROFILE_HARDENING_EVIDENCE_INVALID"
    _require(isinstance(value, dict) and
             set(value) == PAPER_RUNTIME_PROFILE_HARDENING_FIELDS, reason)
    _require(
        value.get("schema") ==
            "hepta.p1-watch-to-paper-runtime-profile-hardening.v1" and
        value.get("version") == 1 and
        value.get("status") == "PAPER_RUNTIME_PROFILE_HARDENED" and
        value.get("candidate_path") ==
            str(PAPER_RUNTIME_PROFILE_CANDIDATE_PATH) and
        value.get("retained_legacy_path") ==
            str(PAPER_RUNTIME_PROFILE_RETAINED_PATH) and
        value.get("exchange_method") == "RENAME_EXCHANGE" and
        value.get("forward_only_after_exchange") is True, reason)
    _validate_profile_file_evidence(
        value.get("target"), reason, path=PAPER_RUNTIME_PROFILE_PATH,
        sha256=HARDENED_PAPER_RUNTIME_PROFILE_SHA256,
        size=HARDENED_PAPER_RUNTIME_PROFILE_BYTES, mode=0o644)
    for field, path in (
        ("legacy_backup", PAPER_RUNTIME_PROFILE_BACKUP_PATH),
        ("retained_legacy", PAPER_RUNTIME_PROFILE_RETAINED_PATH),
    ):
        _validate_profile_file_evidence(
            value.get(field), reason, path=path,
            sha256=LEGACY_PAPER_RUNTIME_PROFILE_SHA256,
            size=LEGACY_PAPER_RUNTIME_PROFILE_BYTES, mode=0o600)
    _digest(value.get("harden_intent_record_sha256"), reason)
    _digest(value.get("harden_exchange_record_sha256"), reason)
    return value


@dataclass(frozen=True)
class InputBinding:
    path: Path
    payload: bytes
    metadata_identity: tuple[int, ...]
    parent_identity: tuple[int, ...]
    document: dict[str, Any]

    @property
    def reference(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "file_sha256": digest_bytes(self.payload),
            "body_sha256": self.document["body_sha256"],
        }

    def reopen(self, reason: str) -> None:
        payload, metadata = secure_read(
            self.path, reason, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o600}), trusted_parent=True)
        parent = _open_trusted_directory(self.path.parent, reason)
        try:
            parent_identity = _directory_identity(os.fstat(parent))
        finally:
            os.close(parent)
        _require(
            payload == self.payload and _identity(metadata) ==
                self.metadata_identity and
            parent_identity == self.parent_identity and
            strict_document(payload, reason) == self.document, reason)


@dataclass(frozen=True)
class ProducerBinding:
    path: Path
    payload: bytes
    metadata_identity: tuple[int, ...]
    parent_identity: tuple[int, ...]

    @property
    def reference(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "file_sha256": digest_bytes(self.payload),
        }

    def reopen(self) -> None:
        reason = "HANDOFF_EXECUTING_IMAGE_DRIFT"
        lexical = Path(__file__).absolute()
        try:
            lexical_metadata = os.lstat(lexical)
            resolved = lexical.resolve(strict=True)
            installed = INSTALLED_EXECUTABLE.resolve(strict=True)
            same = os.path.samefile(resolved, installed)
        except OSError as error:
            raise HandoffError(reason) from error
        _require(
            not stat.S_ISLNK(lexical_metadata.st_mode) and
            lexical == self.path and resolved == installed == self.path and
            same, reason)
        parent = _open_trusted_directory(self.path.parent, reason)
        try:
            _require(
                _trusted_directory_identity(parent, reason) ==
                    self.parent_identity,
                reason)
        finally:
            os.close(parent)
        payload, metadata = secure_read(
            self.path, reason, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o500, 0o555, 0o700, 0o755}),
            trusted_parent=True)
        final = os.lstat(lexical)
        _require(
            payload == self.payload and
            _identity(metadata) == self.metadata_identity ==
                _identity(lexical_metadata) == _identity(final),
            reason)


def bind_executing_image() -> ProducerBinding:
    reason = "HANDOFF_EXECUTING_IMAGE_NOT_INSTALLED"
    installed_path = _canonical_path(INSTALLED_EXECUTABLE, reason)
    lexical = Path(__file__).absolute()
    try:
        lexical_metadata = os.lstat(lexical)
        resolved = lexical.resolve(strict=True)
        installed = installed_path.resolve(strict=True)
        same = os.path.samefile(resolved, installed)
    except OSError as error:
        raise HandoffError(reason) from error
    _require(
        not stat.S_ISLNK(lexical_metadata.st_mode) and
        lexical == installed_path and resolved == installed == installed_path
        and same,
        "HANDOFF_EXECUTING_IMAGE_DRIFT")
    parent = _open_trusted_directory(
        installed_path.parent, "HANDOFF_EXECUTING_IMAGE_PARENT_UNTRUSTED")
    try:
        parent_identity = _trusted_directory_identity(
            parent, "HANDOFF_EXECUTING_IMAGE_PARENT_UNTRUSTED")
    finally:
        os.close(parent)
    payload, metadata = secure_read(
        installed_path, "HANDOFF_EXECUTING_IMAGE_INVALID",
        expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o500, 0o555, 0o700, 0o755}), trusted_parent=True)
    final = os.lstat(lexical)
    _require(
        _identity(lexical_metadata) == _identity(metadata) == _identity(final),
        "HANDOFF_EXECUTING_IMAGE_DRIFT")
    binding = ProducerBinding(
        installed_path, payload, _identity(metadata), parent_identity)
    binding.reopen()
    return binding


def _digest(value: Any, reason: str) -> str:
    _require(type(value) is str and DIGEST.fullmatch(value) is not None, reason)
    return value


def _integer(value: Any, reason: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, reason)
    return value


def _all_false(document: Mapping[str, Any], fields: Sequence[str]) -> bool:
    return all(document.get(field) is False for field in fields)


def _validate_activation_predecessor_lineage(
    success_value: Any, failure_value: Any, reason: str,
) -> None:
    """Bind v4 to the exact Round95 success/failure and Round86 lineage.

    The two pinned whole-file/body digests commit the canonical Round95
    receipts, including their embedded exact Round86 failed-v1 ancestor.
    """
    success = success_value if isinstance(success_value, dict) else {}
    failure = failure_value if isinstance(failure_value, dict) else {}
    _require(
        set(success) == PREDECESSOR_ACTIVATION_SUCCESS_FIELDS and
        success.get("receipt_path") == PREDECESSOR_ACTIVATION_SUCCESS_PATH and
        success.get("receipt_file_sha256") ==
            PREDECESSOR_ACTIVATION_SUCCESS_FILE_SHA256 and
        success.get("receipt_body_sha256") ==
            PREDECESSOR_ACTIVATION_SUCCESS_BODY_SHA256 and
        success.get("receipt_schema") ==
            "hepta.p1-watch-activation-receipt.v3" and
        success.get("receipt_version") == 3 and
        success.get("receipt_status") == "WATCH_GATEWAY_ACTIVATED" and
        success.get("receipt_round") == 95 and
        success.get("receipt_domain") == DOMAIN and
        all(type(success.get(field)) is int for field in (
            "receipt_device", "receipt_inode", "receipt_mode",
            "receipt_nlink", "receipt_uid", "receipt_gid", "receipt_bytes",
            "receipt_mtime_ns", "receipt_ctime_ns")) and
        success["receipt_device"] >= 0 and success["receipt_inode"] > 0 and
        stat.S_ISREG(success["receipt_mode"]) and
        stat.S_IMODE(success["receipt_mode"]) == 0o600 and
        success["receipt_nlink"] == 1 and success["receipt_uid"] == 0 and
        success["receipt_gid"] == 0 and
        0 < success["receipt_bytes"] <= MAXIMUM_JSON_BYTES and
        success["receipt_mtime_ns"] >= 0 and success["receipt_ctime_ns"] >= 0,
        reason)
    _require(
        set(failure) == PREDECESSOR_ACTIVATION_FAILURE_FIELDS and
        failure.get("receipt_path") == PREDECESSOR_ACTIVATION_FAILURE_PATH and
        failure.get("receipt_file_sha256") ==
            PREDECESSOR_ACTIVATION_FAILURE_FILE_SHA256 and
        failure.get("receipt_body_sha256") ==
            PREDECESSOR_ACTIVATION_FAILURE_BODY_SHA256 and
        failure.get("receipt_schema") ==
            "hepta.p1-watch-activation-failed-receipt.v2" and
        failure.get("receipt_version") == 2 and
        failure.get("receipt_revision") == 1 and
        failure.get("receipt_status") == "FAILED_CLOSED" and
        failure.get("receipt_round") == 95 and
        failure.get("receipt_domain") == DOMAIN and
        isinstance(failure.get("receipt_reason"), str) and
        re.fullmatch(r"[A-Z][A-Z0-9_]{0,255}",
                     failure["receipt_reason"]) is not None and
        all(type(failure.get(field)) is int for field in (
            "receipt_device", "receipt_inode", "receipt_mode",
            "receipt_nlink", "receipt_uid", "receipt_gid", "receipt_bytes",
            "receipt_mtime_ns", "receipt_ctime_ns", "journal_record_count")) and
        failure["receipt_device"] >= 0 and failure["receipt_inode"] > 0 and
        stat.S_ISREG(failure["receipt_mode"]) and
        stat.S_IMODE(failure["receipt_mode"]) == 0o600 and
        failure["receipt_nlink"] == 1 and failure["receipt_uid"] == 0 and
        failure["receipt_gid"] == 0 and
        0 < failure["receipt_bytes"] <= MAXIMUM_JSON_BYTES and
        failure["receipt_mtime_ns"] >= 0 and failure["receipt_ctime_ns"] >= 0 and
        failure.get("journal_path") ==
            PREDECESSOR_ACTIVATION_FAILURE_JOURNAL_PATH and
        failure.get("journal_sha256") ==
            PREDECESSOR_ACTIVATION_FAILURE_JOURNAL_SHA256 and
        failure["journal_record_count"] == 21 and
        failure.get("journal_terminal_phase") == "FAILED_CLOSED", reason)


def validate_activation(document: dict[str, Any]) -> tuple[str, str]:
    reason = "HANDOFF_ACTIVATION_RECEIPT_INVALID"
    _require(set(document) == ACTIVATION_FIELDS, reason)
    _require(
        document.get("schema") == "hepta.p1-watch-activation-receipt.v4" and
        document.get("version") == 4 and document.get("status") ==
            "WATCH_GATEWAY_ACTIVATED" and document.get("round") == ROUND and
        document.get("domain") == DOMAIN, reason)
    evidence = document.get("shadow_install_evidence")
    _require(isinstance(evidence, dict) and
             set(evidence) == SHADOW_INSTALL_EVIDENCE_FIELDS, reason)
    _validate_activation_predecessor_lineage(
        document.get("predecessor_activation_success"),
        document.get("predecessor_activation_failure"), reason)
    source = _digest(evidence.get("source_baseline_sha256"), reason)
    _require(
        evidence.get("schema") ==
            "hepta.shadow-runtime-install-consumption-evidence.v3" and
        evidence.get("version") == 3 and evidence.get("domain") == DOMAIN and
        evidence.get("receipt_path") ==
            "/var/lib/hepta/shadow-runtime-install-receipts/"
            "hepta-p1-round114-generation22-passive.json" and
        evidence.get("manifest_path") ==
            "/var/lib/hepta/shadow-runtime-install-artifacts/"
            "hepta-p1-round114-generation22-shadow-runtime.manifest.json" and
        evidence.get("backup_root") ==
            "/var/lib/hepta/shadow-runtime-backups/"
            "hepta-p1-round114-generation22-passive" and
        evidence.get("installed_file_count") == 128 and
        evidence.get("install_generation") == 22 and
        evidence.get("predecessor_install_generation") == 21 and
        evidence.get("predecessor_current_install_pointer_file_sha256") ==
            "sha256:2beeb507fcafbbfc2c93d2e4756fddf0b27e9872733ff97d28af47006461d406" and
        evidence.get("verified_under_lock") is True and
        evidence.get("lock_mode") == "exclusive" and
        _all_false(evidence, (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")), reason)
    _integer(document.get("started_at_ms"), reason)
    _integer(document.get("completed_at_ms"), reason)
    _require(document["started_at_ms"] <= document["completed_at_ms"], reason)
    _require(
        document.get("fresh_activation_transaction") is True and
        document.get("gateway_activated") is True and
        document.get("gateway_profile_loaded") is True and
        document.get("gateway_contract_binding_loaded") is True and
        document.get("broker_loaded_source_attested") is True and
        document.get("broker_deny_all_continuity_attested") is True and
        document.get("watch_authority_provisioned") is False and
        document.get("campaign_launched") is False and
        document.get("admission_prerequisite_satisfied") is True and
        document.get("paper_prerequisite_satisfied") is False and
        document.get("kill_switch_engaged") is True and
        _all_false(document, (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")), reason)
    broker = document.get("broker_after")
    _require(isinstance(broker, dict) and
             broker.get("authorized_connectors") == 0 and
             broker.get("authorized_uids") == [], reason)
    return source, DOMAIN


def validate_p1_audit(document: dict[str, Any]) -> tuple[str, str, str]:
    reason = "HANDOFF_P1_AUDIT_RECEIPT_INVALID"
    _require(set(document) == P1_AUDIT_FIELDS, reason)
    _require(
        document.get("schema") == "hepta.p1-safety-soak-audit-receipt.v1" and
        document.get("version") == 1 and document.get("phase") == "P1_SHADOW"
        and document.get("verdict") == "GO" and
        document.get("domain_id") == DOMAIN and
        type(document.get("campaign_id")) is str and
        TOKEN.fullmatch(document["campaign_id"]) is not None and
        document.get("p1_safety_soak_gate_satisfied") is True and
        document.get("paper_test_admission_candidate") is False and
        document.get("production_mode") == P1_AUDITOR_PRODUCTION_MODE and
        document.get("safest_allowed_next_action") ==
            "CONTINUE_REMAINING_PAPER_ADMISSION_GATES" and
        document.get("failed_invariants") == [] and
        _all_false(document, (
            "paper_authorized", "live_authorized", "mutation_authorized",
            "direct_broker_access")), reason)
    source = _digest(document.get("source_manifest_sha256"), reason)
    _integer(document.get("audited_at_ms"), reason)
    _require(type(document.get("independent_auditor_id")) is str and
             TOKEN.fullmatch(document["independent_auditor_id"]) is not None,
             reason)
    _reference(document.get("freeze_bundle"), reason)
    campaign_runtime = document.get("campaign_runtime")
    _require(
        isinstance(campaign_runtime, dict) and
        set(campaign_runtime) == P1_CAMPAIGN_RUNTIME_REFERENCE_FIELDS and
        campaign_runtime.get("schema") == P1_CAMPAIGN_RUNTIME_SCHEMA,
        reason)
    _reference({
        field: campaign_runtime[field] for field in REFERENCE_FIELDS
    }, reason)
    audit_producer = document.get("producer")
    _require(isinstance(audit_producer, dict) and
             set(audit_producer) == PRODUCER_FIELDS and
             audit_producer.get("path") == str(P1_AUDITOR_EXECUTABLE), reason)
    _digest(audit_producer.get("file_sha256"), reason)
    for field in (
        "campaign_spec_file_sha256", "campaign_spec_body_sha256",
        "policy_sha256", "strategy_sha256",
    ):
        _digest(document.get(field), reason)
    interval = document.get("evaluated_interval")
    _require(isinstance(interval, dict) and
             set(interval) == P1_INTERVAL_FIELDS, reason)
    for field in (
        "start_boottime_ns", "end_boottime_ns", "duration_ns",
        "maximum_checkpoint_gap_ns", "continuity_origin_ms",
        "continuity_end_ms", "continuity_final_slot",
    ):
        _integer(interval.get(field), reason)
    _require(
        interval.get("clock_id") == "CLOCK_BOOTTIME" and
        type(interval.get("boot_id")) is str and bool(interval["boot_id"]) and
        interval.get("consecutive") is True and
        interval["start_boottime_ns"] < interval["end_boottime_ns"] and
        interval["duration_ns"] ==
            interval["end_boottime_ns"] - interval["start_boottime_ns"] and
        interval["duration_ns"] >= MINIMUM_BOOTTIME_DURATION_NS and
        interval["maximum_checkpoint_gap_ns"] <=
            15 * 60 * 1_000_000_000 and
        interval["continuity_origin_ms"] <
            interval["continuity_end_ms"] and
        interval["continuity_final_slot"] >= 1, reason)
    counts = document.get("counts")
    _require(isinstance(counts, dict) and set(counts) == P1_COUNTS_FIELDS,
             reason)
    for field in P1_COUNTS_FIELDS:
        _integer(counts.get(field), reason)
    _require(
        MINIMUM_TRADING_DAYS <= counts["declared_trading_days"] <=
            MAXIMUM_TRADING_DAYS and
        counts["observed_trading_days"] ==
            counts["declared_trading_days"] and
        counts["launcher_receipts"] > 0 and
        counts["verified_closures"] == counts["launcher_receipts"] and
        counts["continuity_checkpoints"] ==
            interval["continuity_final_slot"] + 1 and
        counts["decision_receipts"] == counts["scheduled_decisions"] and
        counts["eligible_decisions"] >= MINIMUM_ELIGIBLE_DECISIONS and
        counts["complete_eligible_decisions"] +
            counts["incomplete_eligible_decisions"] ==
                counts["eligible_decisions"] and
        counts["catch_up_decisions"] == 0 and
        counts["planned_faults"] > 0 and
        counts["fault_results"] == counts["planned_faults"] and
        counts["authority_snapshots"] > 0 and
        counts["cleanup_snapshots"] > 0, reason)
    completeness = document.get("completeness")
    _require(isinstance(completeness, dict) and
             set(completeness) == P1_COMPLETENESS_FIELDS and
             completeness.get("numerator") ==
                counts["complete_eligible_decisions"] and
             completeness.get("denominator") == counts["eligible_decisions"]
             and _integer(completeness.get("ppm"), reason) ==
                completeness["numerator"] * 1_000_000 //
                    completeness["denominator"] and
             completeness["numerator"] * 100 >
                completeness["denominator"] * 99 and
             completeness["ppm"] >= MINIMUM_COMPLETE_PPM and
             completeness.get("strictly_greater_than_99_percent") is True,
             reason)
    artifacts = document.get("checked_artifacts")
    _require(isinstance(artifacts, list) and bool(artifacts), reason)
    order: list[tuple[str, str]] = []
    for artifact in artifacts:
        _require(isinstance(artifact, dict) and
                 set(artifact) == P1_CHECKED_ARTIFACT_FIELDS, reason)
        role = artifact.get("role")
        _require(type(role) is str and TOKEN.fullmatch(role) is not None,
                 reason)
        path = _canonical_path(Path(artifact.get("path", "")), reason)
        _digest(artifact.get("file_sha256"), reason)
        _digest(artifact.get("body_sha256"), reason)
        order.append((role, str(path)))
    _require(order == sorted(set(order)) and
             any(role == "launcher_receipt" for role, _path in order), reason)
    exposure = document.get("exposure_summary")
    _require(isinstance(exposure, dict) and
             set(exposure) == P1_EXPOSURE_FIELDS and
             exposure.get("evidence_present") is True and
             exposure.get("campaign_socket_ever_present") is False and
             exposure.get("kill_switch_continuously_engaged") is True and
             exposure.get("local_boundary_uncertain") is False and
             exposure.get("scope") == "LOCAL_HOST_BOUNDARY_ONLY" and
             exposure.get("authoritative_account_state_observed") is False and
             all(_integer(exposure.get(field), reason) == 0 for field in (
                "maximum_connector_count", "maximum_authorized_uid_count",
                "maximum_paper_unit_active_count")), reason)
    cleanup = document.get("cleanup_status")
    _require(isinstance(cleanup, dict) and set(cleanup) == P1_CLEANUP_FIELDS,
             reason)
    _integer(cleanup.get("required_subject_count"), reason, 1)
    _integer(cleanup.get("verified_subject_count"), reason)
    _require(cleanup.get("verified_subject_count") ==
             cleanup["required_subject_count"] and
             cleanup.get("complete") is True, reason)
    return source, DOMAIN, document["campaign_id"]


def bind_inputs(
    activation_path: Path, p1_audit_path: Path,
) -> tuple[InputBinding, InputBinding, str, str]:
    activation_path = _canonical_path(
        activation_path, "HANDOFF_ACTIVATION_RECEIPT_INVALID")
    p1_audit_path = _canonical_path(
        p1_audit_path, "HANDOFF_P1_AUDIT_RECEIPT_INVALID")
    activation_payload, activation_metadata = secure_read(
        activation_path, "HANDOFF_ACTIVATION_RECEIPT_INVALID",
        expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o600}), trusted_parent=True)
    activation = strict_document(
        activation_payload, "HANDOFF_ACTIVATION_RECEIPT_INVALID")
    activation_source, activation_domain = validate_activation(activation)
    audit_payload, audit_metadata = secure_read(
        p1_audit_path, "HANDOFF_P1_AUDIT_RECEIPT_INVALID",
        expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o600}), trusted_parent=True)
    audit = strict_document(audit_payload, "HANDOFF_P1_AUDIT_RECEIPT_INVALID")
    audit_source, audit_domain, campaign = validate_p1_audit(audit)
    _require(activation_source == audit_source,
             "HANDOFF_SOURCE_LINEAGE_MISMATCH")
    _require(activation_domain == audit_domain == DOMAIN,
             "HANDOFF_DOMAIN_LINEAGE_MISMATCH")
    parent_identities: list[tuple[int, ...]] = []
    for path, reason in (
        (activation_path, "HANDOFF_ACTIVATION_RECEIPT_INVALID"),
        (p1_audit_path, "HANDOFF_P1_AUDIT_RECEIPT_INVALID"),
    ):
        parent = _open_trusted_directory(path.parent, reason)
        try:
            parent_identities.append(_directory_identity(os.fstat(parent)))
        finally:
            os.close(parent)
    return (
        InputBinding(
            activation_path, activation_payload,
            _identity(activation_metadata), parent_identities[0], activation),
        InputBinding(
            p1_audit_path, audit_payload, _identity(audit_metadata),
            parent_identities[1], audit), activation_source, campaign,
    )


def _ensure_owned_directory(path: Path, mode: int = 0o700) -> None:
    """Create one fixed state directory and re-open its exact inode."""

    path = _canonical_path(path, "HANDOFF_STATE_DIRECTORY_INVALID")
    try:
        path.mkdir(mode=mode, parents=False, exist_ok=True)
        metadata = os.lstat(path)
        _require(
            stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == ROOT_UID and metadata.st_gid == ROOT_GID and
            stat.S_IMODE(metadata.st_mode) == mode,
            "HANDOFF_STATE_DIRECTORY_INVALID")
        descriptor = _open_anchored_directory(
            path, "HANDOFF_STATE_DIRECTORY_INVALID")
        try:
            _require(_directory_identity(os.fstat(descriptor)) ==
                     _directory_identity(metadata),
                     "HANDOFF_STATE_DIRECTORY_INVALID")
        finally:
            os.close(descriptor)
    except (OSError, HandoffError) as error:
        if isinstance(error, HandoffError):
            raise
        raise HandoffError("HANDOFF_STATE_DIRECTORY_INVALID") from error


def prepare_state_directories() -> None:
    _ensure_owned_directory(STATE_ROOT.parent)
    _ensure_owned_directory(STATE_ROOT)
    _ensure_owned_directory(JOURNAL_ROOT)


def acquire_lock() -> int:
    parent = _open_anchored_directory(
        LOCK_PATH.parent, "HANDOFF_LOCK_INVALID")
    descriptor = -1
    try:
        try:
            before = os.stat(
                LOCK_PATH.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            descriptor = os.open(
                LOCK_PATH.name, os.O_RDWR | os.O_CREAT | NOFOLLOW | CLOEXEC,
                0o600, dir_fd=parent)
        else:
            _require(
                stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                before.st_uid == ROOT_UID and before.st_gid == ROOT_GID and
                stat.S_IMODE(before.st_mode) == 0o600 and before.st_size == 0,
                "HANDOFF_LOCK_INVALID")
            descriptor = os.open(
                LOCK_PATH.name, os.O_RDWR | NOFOLLOW | CLOEXEC, dir_fd=parent)
        opened = os.fstat(descriptor)
        final = os.stat(
            LOCK_PATH.name, dir_fd=parent, follow_symlinks=False)
        _require(
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            opened.st_uid == ROOT_UID and opened.st_gid == ROOT_GID and
            stat.S_IMODE(opened.st_mode) == 0o600 and opened.st_size == 0 and
            _identity(opened) == _identity(final), "HANDOFF_LOCK_INVALID")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise HandoffError("HANDOFF_LOCK_BUSY") from error
        os.fsync(descriptor)
        os.fsync(parent)
        return descriptor
    except (OSError, HandoffError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        if isinstance(error, HandoffError):
            raise
        raise HandoffError("HANDOFF_LOCK_INVALID") from error
    finally:
        os.close(parent)


def release_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _rename_noreplace(
    source_parent: int, source_name: str,
    destination_parent: int, destination_name: str,
    reason: str,
) -> None:
    ctypes.set_errno(0)
    result = LIBC.renameat2(
        source_parent, os.fsencode(source_name), destination_parent,
        os.fsencode(destination_name), RENAME_NOREPLACE)
    if result != 0:
        code = ctypes.get_errno()
        raise HandoffError(reason) from OSError(code, os.strerror(code))


def _rename_exchange(
    first_parent: int, first_name: str,
    second_parent: int, second_name: str, reason: str,
) -> None:
    ctypes.set_errno(0)
    result = LIBC.renameat2(
        first_parent, os.fsencode(first_name), second_parent,
        os.fsencode(second_name), RENAME_EXCHANGE)
    if result != 0:
        code = ctypes.get_errno()
        raise HandoffError(reason) from OSError(code, os.strerror(code))


def _publish_payload(
    destination: Path, payload: bytes, *, reason: str,
    validate: Callable[[bytes], dict[str, Any]],
    invoke_publish_seams: bool = False,
) -> dict[str, Any]:
    destination = _canonical_path(destination, reason)
    _require(0 < len(payload) <= MAXIMUM_JSON_BYTES, reason)
    parent = _open_anchored_directory(destination.parent, reason)
    staging = "." + destination.name + ".handoff.tmp"
    descriptor = -1
    renamed = False
    try:
        parent_metadata = os.fstat(parent)
        _require(
            parent_metadata.st_uid == ROOT_UID and
            parent_metadata.st_gid == ROOT_GID and
            stat.S_IMODE(parent_metadata.st_mode) & 0o022 == 0,
            reason)
        try:
            os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise HandoffError("HANDOFF_RECEIPT_ALREADY_EXISTS")
        try:
            os.stat(staging, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise HandoffError("HANDOFF_RECEIPT_STAGING_EXISTS")
        descriptor = os.open(
            staging, os.O_RDWR | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC,
            0o600, dir_fd=parent)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        if invoke_publish_seams and PUBLISH_SEAM_HOOK is not None:
            PUBLISH_SEAM_HOOK("AFTER_TEMP_FSYNC")
        opened = os.fstat(descriptor)
        _require(
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            opened.st_uid == ROOT_UID and opened.st_gid == ROOT_GID and
            stat.S_IMODE(opened.st_mode) == 0o600 and
            opened.st_size == len(payload), reason)
        os.lseek(descriptor, 0, os.SEEK_SET)
        staged = b""
        while len(staged) < len(payload):
            chunk = os.read(descriptor, len(payload) - len(staged))
            if not chunk:
                break
            staged += chunk
        _require(staged == payload, reason)
        validate(staged)
        os.close(descriptor)
        descriptor = -1
        _rename_noreplace(parent, staging, parent, destination.name, reason)
        renamed = True
        if invoke_publish_seams and PUBLISH_SEAM_HOOK is not None:
            PUBLISH_SEAM_HOOK("AFTER_RENAME")
        os.fsync(parent)
        if invoke_publish_seams and PUBLISH_SEAM_HOOK is not None:
            PUBLISH_SEAM_HOOK("AFTER_PARENT_FSYNC")
        reopened, metadata = secure_read(
            destination, reason, modes=frozenset({0o600}))
        _require(reopened == payload and metadata.st_uid == ROOT_UID, reason)
        document = validate(reopened)
        if invoke_publish_seams and PUBLISH_SEAM_HOOK is not None:
            PUBLISH_SEAM_HOOK("AFTER_REOPEN")
        return document
    except (OSError, HandoffError) as error:
        if isinstance(error, HandoffError):
            raise
        raise HandoffError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not renamed:
            try:
                os.unlink(staging, dir_fd=parent)
                os.fsync(parent)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        os.close(parent)


def _remove_staging(destination: Path) -> None:
    """Remove only this transaction's fixed, unpublished staging inode."""

    destination = _canonical_path(destination, "HANDOFF_STAGING_INVALID")
    parent = _open_anchored_directory(
        destination.parent, "HANDOFF_STAGING_INVALID")
    staging = "." + destination.name + ".handoff.tmp"
    try:
        try:
            metadata = os.stat(
                staging, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == ROOT_UID and metadata.st_gid == ROOT_GID and
            stat.S_IMODE(metadata.st_mode) == 0o600 and
            metadata.st_size <= MAXIMUM_JSON_BYTES,
            "HANDOFF_STAGING_INVALID")
        os.unlink(staging, dir_fd=parent)
        os.fsync(parent)
    except (OSError, HandoffError) as error:
        if isinstance(error, HandoffError):
            raise
        raise HandoffError("HANDOFF_STAGING_INVALID") from error
    finally:
        os.close(parent)


def _valid_phase_sequence(phases: list[str]) -> bool:
    if not phases:
        return True
    failure_indexes = [
        index for index, phase in enumerate(phases) if phase in FAILURE_PHASES]
    if not failure_indexes:
        return phases == list(SUCCESS_PHASES[:len(phases)])
    first = failure_indexes[0]
    success = phases[:first]
    failure = phases[first:]
    return (
        success == list(SUCCESS_PHASES[:len(success)]) and
        "COMPLETED" not in success and
        failure == list(FAILURE_PHASES[:len(failure)])
    )


@dataclass(frozen=True)
class JournalRecord:
    phase: str
    file_sha256: str
    document: dict[str, Any]


class Journal:
    def __init__(self, root: Path | None = None):
        self.root = JOURNAL_ROOT if root is None else root

    def load(self) -> list[JournalRecord]:
        try:
            metadata = os.lstat(self.root)
        except FileNotFoundError:
            return []
        _require(
            stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == ROOT_UID and metadata.st_gid == ROOT_GID and
            stat.S_IMODE(metadata.st_mode) == 0o700,
            "HANDOFF_JOURNAL_INVALID")
        parent = _open_anchored_directory(
            self.root, "HANDOFF_JOURNAL_INVALID")
        records: list[JournalRecord] = []
        previous: str | None = None
        try:
            names = sorted(os.listdir(parent))
            _require(not any(name.startswith(".pending-") for name in names),
                     "HANDOFF_JOURNAL_INVALID")
            for sequence, name in enumerate(names):
                match = re.fullmatch(r"([0-9]{4})-([A-Z0-9_]+)\.json", name)
                _require(match is not None and int(match.group(1)) == sequence,
                         "HANDOFF_JOURNAL_INVALID")
                payload, metadata = secure_read(
                    self.root / name, "HANDOFF_JOURNAL_INVALID",
                    modes=frozenset({0o600}))
                _require(metadata.st_uid == ROOT_UID, "HANDOFF_JOURNAL_INVALID")
                document = strict_document(payload, "HANDOFF_JOURNAL_INVALID")
                _require(set(document) == JOURNAL_FIELDS and
                         document.get("schema") ==
                            "hepta.p1-watch-to-paper-handoff-journal.v1" and
                         document.get("version") == 1 and
                         document.get("sequence") == sequence and
                         document.get("phase") == match.group(2) and
                         type(document.get("recorded_at_ms")) is int and
                         document["recorded_at_ms"] >= 0 and
                         document.get("previous_record_sha256") == previous and
                         isinstance(document.get("evidence"), dict),
                         "HANDOFF_JOURNAL_INVALID")
                file_sha = digest_bytes(payload)
                records.append(JournalRecord(
                    document["phase"], file_sha, document))
                previous = file_sha
            _require(_valid_phase_sequence([item.phase for item in records]),
                     "HANDOFF_JOURNAL_INVALID")
            return records
        except (OSError, HandoffError) as error:
            if isinstance(error, HandoffError):
                raise
            raise HandoffError("HANDOFF_JOURNAL_INVALID") from error
        finally:
            os.close(parent)

    def append(self, phase: str, evidence: Mapping[str, Any]) -> JournalRecord:
        records = self.load()
        phases = [item.phase for item in records] + [phase]
        _require(_valid_phase_sequence(phases), "HANDOFF_JOURNAL_PHASE_INVALID")
        sequence = len(records)
        body = {
            "schema": "hepta.p1-watch-to-paper-handoff-journal.v1",
            "version": 1, "sequence": sequence, "phase": phase,
            "recorded_at_ms": time.time_ns() // 1_000_000,
            "previous_record_sha256":
                records[-1].file_sha256 if records else None,
            "evidence": dict(evidence),
        }
        document = seal(body)
        payload = canonical_bytes(document)
        destination = self.root / f"{sequence:04d}-{phase}.json"
        result = _publish_payload(
            destination, payload, reason="HANDOFF_JOURNAL_WRITE_FAILED",
            validate=lambda value: strict_document(
                value, "HANDOFF_JOURNAL_INVALID"))
        record = JournalRecord(phase, digest_bytes(payload), result)
        _require(self.load()[-1] == record, "HANDOFF_JOURNAL_REBOUND")
        return record

    def digest(self) -> str:
        return digest_bytes(canonical_bytes([
            item.file_sha256 for item in self.load()]))


def _systemd_phase_token(unit: str) -> str:
    _require(UNIT_TOKEN.fullmatch(unit) is not None,
             "HANDOFF_UNIT_INVALID")
    return _unit_phase_token(unit)


def _path_exists_nofollow(path: Path) -> bool:
    try:
        os.lstat(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as error:
        raise HandoffError("HANDOFF_BOUNDARY_INVALID") from error


def _directory_entries(path: Path) -> list[str]:
    if not _path_exists_nofollow(path):
        return []
    descriptor = _open_anchored_directory(path, "HANDOFF_BOUNDARY_INVALID")
    try:
        return sorted(os.listdir(descriptor))
    except OSError as error:
        raise HandoffError("HANDOFF_BOUNDARY_INVALID") from error
    finally:
        os.close(descriptor)


def _session_authority_count() -> int:
    """Count authority entries and prove the bootstrap lock is idle."""

    names = _directory_entries(WATCH_SESSIONS)
    if not names:
        return 0
    authority = [name for name in names if name != ".session-bootstrap.lock"]
    if ".session-bootstrap.lock" not in names:
        return len(authority)
    parent = _open_anchored_directory(
        WATCH_SESSIONS, "HANDOFF_AUTHORITY_BOUNDARY_INVALID")
    descriptor = -1
    try:
        before = os.stat(
            ".session-bootstrap.lock", dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            ".session-bootstrap.lock", os.O_RDWR | NOFOLLOW | CLOEXEC,
            dir_fd=parent)
        opened = os.fstat(descriptor)
        final = os.stat(
            ".session-bootstrap.lock", dir_fd=parent,
            follow_symlinks=False)
        _require(
            _identity(before) == _identity(opened) == _identity(final) and
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            opened.st_uid == ROOT_UID and opened.st_gid == ROOT_GID and
            stat.S_IMODE(opened.st_mode) == 0o600 and opened.st_size == 0,
            "HANDOFF_AUTHORITY_BOUNDARY_INVALID")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return len(authority) + 1
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return len(authority)
    except (OSError, HandoffError) as error:
        if isinstance(error, HandoffError):
            raise
        raise HandoffError("HANDOFF_AUTHORITY_BOUNDARY_INVALID") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


class ProductionExecutor:
    """Fixed, credential-free systemd retirement executor."""

    __slots__ = ("_producer_binding",)

    def __init__(self) -> None:
        _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
                 "HANDOFF_ROOT_REQUIRED")
        self._producer_binding = bind_executing_image()

    def attest_producer(self) -> ProducerBinding:
        self._producer_binding.reopen()
        return self._producer_binding

    def _run(self, arguments: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        allowed = (
            arguments == (SYSTEMCTL, "daemon-reload") or
            len(arguments) == 4 and arguments[:3] ==
                (SYSTEMCTL, "disable", "--now") and
                arguments[3] in WATCH_UNITS or
            len(arguments) == 3 and arguments[:2] ==
                (SYSTEMCTL, "mask") and arguments[2] in WATCH_UNITS or
            len(arguments) == 4 and arguments[:3] ==
                (SYSTEMCTL, "mask", "--runtime") and
                arguments[3] in WATCH_UNITS
        )
        _require(allowed, "HANDOFF_SYSTEMCTL_ARGUMENT_INVALID")
        try:
            result = subprocess.run(
                arguments, check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=SANITIZED_ENVIRONMENT, cwd="/", timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            raise HandoffError("HANDOFF_SYSTEMCTL_FAILED") from error
        _require(
            result.returncode == 0 and
            len(result.stdout) <= MAXIMUM_COMMAND_BYTES and
            len(result.stderr) <= MAXIMUM_COMMAND_BYTES,
            "HANDOFF_SYSTEMCTL_FAILED")
        return result

    def disable_and_stop(self, unit: str) -> None:
        self._run((SYSTEMCTL, "disable", "--now", unit))

    def mask_persistent(self, unit: str) -> None:
        self._run((SYSTEMCTL, "mask", unit))

    def mask_runtime(self, unit: str) -> None:
        self._run((SYSTEMCTL, "mask", "--runtime", unit))

    def daemon_reload(self) -> None:
        self._run((SYSTEMCTL, "daemon-reload"))

    @staticmethod
    def _show(unit: str) -> dict[str, str]:
        names = (
            "LoadState", "ActiveState", "SubState", "Job", "UnitFileState")
        arguments = (
            SYSTEMCTL, "show", "--no-pager", "--property=" + ",".join(names),
            unit)
        try:
            result = subprocess.run(
                arguments, check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=SANITIZED_ENVIRONMENT, cwd="/", timeout=15)
        except (OSError, subprocess.SubprocessError) as error:
            raise HandoffError("HANDOFF_SYSTEMD_ATTESTATION_FAILED") from error
        _require(result.returncode == 0 and not result.stderr and
                 len(result.stdout) <= 65536,
                 "HANDOFF_SYSTEMD_ATTESTATION_FAILED")
        fields: dict[str, str] = {}
        try:
            for raw in result.stdout.decode("utf-8", errors="strict").splitlines():
                key, value = raw.split("=", 1)
                _require(key in names and key not in fields,
                         "HANDOFF_SYSTEMD_ATTESTATION_FAILED")
                fields[key] = value
        except (UnicodeError, ValueError) as error:
            raise HandoffError(
                "HANDOFF_SYSTEMD_ATTESTATION_FAILED") from error
        _require(set(fields) == set(names),
                 "HANDOFF_SYSTEMD_ATTESTATION_FAILED")
        return {
            "load_state": fields["LoadState"],
            "active_state": fields["ActiveState"],
            "sub_state": fields["SubState"], "job": fields["Job"],
            "unit_file_state": fields["UnitFileState"],
        }

    @staticmethod
    def _transient_units() -> tuple[str, ...]:
        arguments = (
            SYSTEMCTL, "list-units", "--all", "--plain", "--no-legend",
            "--no-pager", "hepta-p1-shadow-*.service",
            "hepta-p1-shadow-*.timer")
        try:
            result = subprocess.run(
                arguments, check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=SANITIZED_ENVIRONMENT, cwd="/", timeout=15)
        except (OSError, subprocess.SubprocessError) as error:
            raise HandoffError(
                "HANDOFF_TRANSIENT_INVENTORY_INVALID") from error
        _require(
            result.returncode == 0 and not result.stderr and
            len(result.stdout) <= MAXIMUM_COMMAND_BYTES,
            "HANDOFF_TRANSIENT_INVENTORY_INVALID")
        try:
            lines = result.stdout.decode(
                "utf-8", errors="strict").splitlines()
        except UnicodeError as error:
            raise HandoffError(
                "HANDOFF_TRANSIENT_INVENTORY_INVALID") from error
        names: list[str] = []
        for line in lines:
            parts = line.split(None, 4)
            _require(len(parts) >= 4 and
                     TRANSIENT_UNIT.fullmatch(parts[0]) is not None,
                     "HANDOFF_TRANSIENT_INVENTORY_INVALID")
            names.append(parts[0])
        _require(
            len(names) <= MAXIMUM_TRANSIENT_UNITS and
            len(names) == len(set(names)),
            "HANDOFF_TRANSIENT_INVENTORY_INVALID")
        return tuple(sorted(names))

    @staticmethod
    def _mask_present(root: Path, unit: str) -> bool:
        path = root / unit
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            return False
        except OSError as error:
            raise HandoffError("HANDOFF_SYSTEMD_MASK_INVALID") from error
        _require(stat.S_ISLNK(metadata.st_mode) and metadata.st_uid == ROOT_UID
                 and metadata.st_gid == ROOT_GID,
                 "HANDOFF_SYSTEMD_MASK_INVALID")
        try:
            return os.readlink(path) == MASK_TARGET
        except OSError as error:
            raise HandoffError("HANDOFF_SYSTEMD_MASK_INVALID") from error

    @staticmethod
    def _broker(tighten: bool) -> dict[str, Any]:
        action = "--tighten-deny-all" if tighten else "--check-deny-all"
        try:
            result = subprocess.run(
                (PYTHON, "-I", "-S", str(BROKER_HELPER), action),
                check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=SANITIZED_ENVIRONMENT, cwd="/", timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            raise HandoffError("HANDOFF_BROKER_DENY_ALL_INVALID") from error
        _require(result.returncode == 0 and not result.stderr and
                 len(result.stdout) <= 4096,
                 "HANDOFF_BROKER_DENY_ALL_INVALID")
        match = re.fullmatch(
            rb"hepta_broker_egress_policy: PASS policy_sha256="
            rb"(?P<sha>[0-9a-f]{64}) authorized_connectors=0 "
            rb"authorized_uids= protected_ports=4\n", result.stdout)
        _require(match is not None, "HANDOFF_BROKER_DENY_ALL_INVALID")
        assert match is not None
        return {
            "policy_sha256": "sha256:" +
                match.group("sha").decode("ascii"),
            "authorized_connectors": 0, "authorized_uids": [],
            "protected_ports": 4,
        }

    @staticmethod
    def _kill_switch(path: Path, gid: int) -> bool:
        payload, _ = secure_read(
            path, "HANDOFF_KILL_SWITCH_INVALID", expected_gid=gid,
            modes=frozenset({0o440}), maximum=8)
        _require(payload == b"engaged", "HANDOFF_KILL_SWITCH_INVALID")
        return True

    @staticmethod
    def _identity_manifest() -> tuple[int, str]:
        reason = "HANDOFF_IDENTITY_MANIFEST_INVALID"
        payload, _ = secure_read(
            IDENTITY_MANIFEST_PATH, reason, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o600}), maximum=4096,
            trusted_parent=True)
        observed = digest_bytes(payload)
        _require(observed == DISABLED_IDENTITY_MANIFEST_SHA256, reason)
        try:
            value = json.loads(
                payload.decode("utf-8", errors="strict"),
                object_pairs_hook=_unique_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    HandoffError(reason)))
        except HandoffError:
            raise
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise HandoffError(reason) from error
        _require(
            isinstance(value, dict) and value.get("schema") ==
                "hepta.agent-trust-domain-paper-identities.v1" and
            value.get("version") == 1 and value.get("identities") == [] and
            value.get("paper_authorized") is False and
            value.get("live_authorized") is False, reason)
        return 0, observed

    def profile_restoration_state(self) -> dict[str, Any]:
        reason = "HANDOFF_PROFILE_RESTORATION_STATE_INVALID"
        target_payload, _target_meta = secure_read(
            PROFILE_TARGET_PATH, reason, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o644}),
            maximum=DORMANT_PAPER_PROFILE_BYTES, trusted_parent=True)
        if (len(target_payload), digest_bytes(target_payload)) == (
                WATCH_PROFILE_BYTES, WATCH_PROFILE_SHA256):
            target_kind = "WATCH"
        elif (len(target_payload), digest_bytes(target_payload)) == (
                DORMANT_PAPER_PROFILE_BYTES, DORMANT_PAPER_PROFILE_SHA256):
            target_kind = "DORMANT"
        else:
            raise HandoffError(reason)
        target_payload, _target_meta, target = _read_profile_file(
            PROFILE_TARGET_PATH, reason,
            sha256=(WATCH_PROFILE_SHA256 if target_kind == "WATCH" else
                    DORMANT_PAPER_PROFILE_SHA256),
            size=(WATCH_PROFILE_BYTES if target_kind == "WATCH" else
                  DORMANT_PAPER_PROFILE_BYTES), mode=0o644)
        _backup_payload, _backup_meta, backup = _read_profile_file(
            PROFILE_DORMANT_BACKUP_PATH, reason,
            sha256=DORMANT_PAPER_PROFILE_SHA256,
            size=DORMANT_PAPER_PROFILE_BYTES, mode=0o600)
        _retained_payload, _retained_meta, retained = _read_profile_file(
            PROFILE_FORWARD_RETAINED_PATH, reason,
            sha256=DORMANT_PAPER_PROFILE_SHA256,
            size=DORMANT_PAPER_PROFILE_BYTES, mode=0o600)
        transition_document, transition = _read_profile_sealed_evidence(
            PROFILE_FORWARD_TRANSITION_RECEIPT_PATH, reason,
            schema=PROFILE_TRANSITION_SCHEMA, version=2,
            status=PROFILE_TRANSITION_STATUS)
        deployment_document, deployment = _read_profile_sealed_evidence(
            PROFILE_DEPLOYMENT_RECEIPT_PATH, reason,
            schema=PROFILE_DEPLOYMENT_SCHEMA, version=8,
            status=PROFILE_DEPLOYMENT_STATUS)
        preimage_document, preimage = _read_profile_sealed_evidence(
            PROFILE_FORWARD_PREIMAGE_PATH, reason,
            schema=PROFILE_PREIMAGE_SCHEMA, version=1,
            status=PROFILE_PREIMAGE_STATUS)
        _validate_profile_reference_documents(
            transition_document, deployment_document, preimage_document,
            reason, transition_evidence=transition,
            preimage_evidence=preimage)

        candidate: dict[str, Any] | None = None
        if _path_exists_nofollow(PROFILE_CANDIDATE_PATH):
            payload, metadata = secure_read(
                PROFILE_CANDIDATE_PATH, reason, expected_uid=ROOT_UID,
                expected_gid=ROOT_GID, modes=frozenset({0o600, 0o644}),
                maximum=DORMANT_PAPER_PROFILE_BYTES, trusted_parent=True)
            candidate = _profile_file_evidence(
                PROFILE_CANDIDATE_PATH, payload, metadata)
        retired_watch: dict[str, Any] | None = None
        if _path_exists_nofollow(PROFILE_RETIRED_WATCH_PATH):
            _payload, _metadata, retired_watch = _read_profile_file(
                PROFILE_RETIRED_WATCH_PATH, reason,
                sha256=WATCH_PROFILE_SHA256, size=WATCH_PROFILE_BYTES,
                mode=0o600)
        if target_kind == "WATCH" and candidate is None and retired_watch is None:
            state = "PRE"
        elif target_kind == "WATCH" and candidate is not None and retired_watch is None:
            _validate_profile_file_evidence(
                candidate, reason, path=PROFILE_CANDIDATE_PATH,
                sha256=DORMANT_PAPER_PROFILE_SHA256,
                size=DORMANT_PAPER_PROFILE_BYTES, mode=0o644)
            state = "PRE_CANDIDATE"
        elif target_kind == "DORMANT" and candidate is not None and retired_watch is None:
            _require(
                candidate["file_sha256"] == WATCH_PROFILE_SHA256 and
                candidate["bytes"] == WATCH_PROFILE_BYTES and
                stat.S_IMODE(candidate["mode"]) in {0o600, 0o644}, reason)
            state = "POST_CANDIDATE"
        elif target_kind == "DORMANT" and candidate is None and retired_watch is not None:
            state = "RESTORED"
        else:
            raise HandoffError(reason)
        return _validate_profile_state({
            "state": state, "target": target, "dormant_backup": backup,
            "forward_retained_dormant": retained, "candidate": candidate,
            "retired_watch": retired_watch,
            "forward_transition_receipt": transition,
            "profile_deployment_receipt": deployment,
            "forward_preimage_evidence": preimage,
        })

    def prepare_profile_candidate(self) -> None:
        reason = "HANDOFF_PROFILE_CANDIDATE_FAILED"
        state = self.profile_restoration_state()
        if state["state"] == "PRE_CANDIDATE":
            return
        _require(state["state"] == "PRE", reason)
        payload, _metadata, _evidence = _read_profile_file(
            PROFILE_DORMANT_BACKUP_PATH, reason,
            sha256=DORMANT_PAPER_PROFILE_SHA256,
            size=DORMANT_PAPER_PROFILE_BYTES, mode=0o600)
        parent = _open_trusted_directory(PROFILE_TARGET_PATH.parent, reason)
        descriptor = -1
        try:
            descriptor = os.open(
                PROFILE_CANDIDATE_PATH.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC,
                0o644, dir_fd=parent)
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fchmod(descriptor, 0o644)
            os.fchown(descriptor, ROOT_UID, ROOT_GID)
            os.fsync(descriptor)
            os.fsync(parent)
        except (OSError, HandoffError) as error:
            if isinstance(error, HandoffError):
                raise
            raise HandoffError(reason) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)
        _require(self.profile_restoration_state()["state"] == "PRE_CANDIDATE",
                 reason)

    def exchange_profile_candidate(self) -> None:
        reason = "HANDOFF_PROFILE_EXCHANGE_FAILED"
        state = self.profile_restoration_state()
        if state["state"] in {"POST_CANDIDATE", "RESTORED"}:
            return
        _require(state["state"] == "PRE_CANDIDATE", reason)
        parent = _open_trusted_directory(PROFILE_TARGET_PATH.parent, reason)
        try:
            _rename_exchange(
                parent, PROFILE_TARGET_PATH.name,
                parent, PROFILE_CANDIDATE_PATH.name, reason)
            os.fsync(parent)
        except OSError as error:
            raise HandoffError(reason) from error
        finally:
            os.close(parent)
        _require(self.profile_restoration_state()["state"] == "POST_CANDIDATE",
                 reason)

    def remove_preexchange_profile_candidate(self) -> None:
        reason = "HANDOFF_PROFILE_CANDIDATE_CLEANUP_FAILED"
        state = self.profile_restoration_state()
        if state["state"] == "PRE":
            return
        _require(state["state"] == "PRE_CANDIDATE", reason)
        expected = state["candidate"]
        parent = _open_trusted_directory(PROFILE_CANDIDATE_PATH.parent, reason)
        descriptor = -1
        try:
            before = os.stat(
                PROFILE_CANDIDATE_PATH.name, dir_fd=parent,
                follow_symlinks=False)
            descriptor = os.open(
                PROFILE_CANDIDATE_PATH.name,
                os.O_RDONLY | NOFOLLOW | CLOEXEC, dir_fd=parent)
            opened = os.fstat(descriptor)
            _require(
                _identity(before) == _identity(opened) and
                _profile_file_evidence(
                    PROFILE_CANDIDATE_PATH, os.read(
                        descriptor, DORMANT_PAPER_PROFILE_BYTES + 1), opened) ==
                    expected,
                reason)
            os.unlink(PROFILE_CANDIDATE_PATH.name, dir_fd=parent)
            os.fsync(parent)
            try:
                os.stat(PROFILE_CANDIDATE_PATH.name, dir_fd=parent,
                        follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise HandoffError(reason)
        except (OSError, HandoffError) as error:
            if isinstance(error, HandoffError):
                raise
            raise HandoffError(reason) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)
        _require(self.profile_restoration_state()["state"] == "PRE", reason)

    @staticmethod
    def profile_candidate_absent() -> bool:
        reason = "HANDOFF_PROFILE_CANDIDATE_ABSENCE_INVALID"
        parent = _open_trusted_directory(PROFILE_CANDIDATE_PATH.parent, reason)
        try:
            identity = _trusted_directory_identity(parent, reason)
            for _attempt in range(2):
                try:
                    os.stat(PROFILE_CANDIDATE_PATH.name, dir_fd=parent,
                            follow_symlinks=False)
                except FileNotFoundError:
                    pass
                except OSError as error:
                    raise HandoffError(reason) from error
                else:
                    raise HandoffError(reason)
            _require(identity == _trusted_directory_identity(parent, reason),
                     reason)
            return True
        finally:
            os.close(parent)

    def seal_retired_watch(self) -> None:
        reason = "HANDOFF_PROFILE_RETIRED_WATCH_FAILED"
        state = self.profile_restoration_state()
        if state["state"] == "RESTORED":
            return
        _require(state["state"] == "POST_CANDIDATE", reason)
        candidate_parent = _open_trusted_directory(
            PROFILE_CANDIDATE_PATH.parent, reason)
        retired_parent = _open_trusted_directory(
            PROFILE_RETIRED_WATCH_PATH.parent, reason)
        descriptor = -1
        try:
            descriptor = os.open(
                PROFILE_CANDIDATE_PATH.name, os.O_RDWR | NOFOLLOW | CLOEXEC,
                dir_fd=candidate_parent)
            os.fchmod(descriptor, 0o600)
            os.fchown(descriptor, ROOT_UID, ROOT_GID)
            os.fsync(descriptor)
            os.fsync(candidate_parent)
            _rename_noreplace(
                candidate_parent, PROFILE_CANDIDATE_PATH.name,
                retired_parent, PROFILE_RETIRED_WATCH_PATH.name, reason)
            os.fsync(candidate_parent)
            os.fsync(retired_parent)
        except (OSError, HandoffError) as error:
            if isinstance(error, HandoffError):
                raise
            raise HandoffError(reason) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(candidate_parent)
            os.close(retired_parent)
        _require(self.profile_restoration_state()["state"] == "RESTORED",
                 reason)

    def paper_runtime_profile_hardening_state(self) -> dict[str, Any]:
        reason = "HANDOFF_RUNTIME_PROFILE_HARDENING_STATE_INVALID"
        target_payload, target_metadata = secure_read(
            PAPER_RUNTIME_PROFILE_PATH, reason, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o644}),
            maximum=LEGACY_PAPER_RUNTIME_PROFILE_BYTES, trusted_parent=True)
        target_digest = digest_bytes(target_payload)
        if (len(target_payload), target_digest) == (
                LEGACY_PAPER_RUNTIME_PROFILE_BYTES,
                LEGACY_PAPER_RUNTIME_PROFILE_SHA256):
            _parse_paper_runtime_profile(
                target_payload, hardened=False, reason=reason)
            target_kind = "LEGACY"
        elif (len(target_payload), target_digest) == (
                HARDENED_PAPER_RUNTIME_PROFILE_BYTES,
                HARDENED_PAPER_RUNTIME_PROFILE_SHA256):
            _parse_paper_runtime_profile(
                target_payload, hardened=True, reason=reason)
            target_kind = "HARDENED"
        else:
            raise HandoffError(reason)
        target = _profile_file_evidence(
            PAPER_RUNTIME_PROFILE_PATH, target_payload, target_metadata)

        backup: dict[str, Any] | None = None
        if _path_exists_nofollow(PAPER_RUNTIME_PROFILE_BACKUP_PATH):
            payload, metadata = secure_read(
                PAPER_RUNTIME_PROFILE_BACKUP_PATH, reason,
                expected_uid=ROOT_UID, expected_gid=ROOT_GID,
                modes=frozenset({0o600}),
                maximum=LEGACY_PAPER_RUNTIME_PROFILE_BYTES,
                trusted_parent=True)
            _parse_paper_runtime_profile(payload, hardened=False, reason=reason)
            backup = _profile_file_evidence(
                PAPER_RUNTIME_PROFILE_BACKUP_PATH, payload, metadata)

        candidate: dict[str, Any] | None = None
        candidate_kind: str | None = None
        if _path_exists_nofollow(PAPER_RUNTIME_PROFILE_CANDIDATE_PATH):
            payload, metadata = secure_read(
                PAPER_RUNTIME_PROFILE_CANDIDATE_PATH, reason,
                expected_uid=ROOT_UID, expected_gid=ROOT_GID,
                modes=frozenset({0o600, 0o644}),
                maximum=LEGACY_PAPER_RUNTIME_PROFILE_BYTES,
                trusted_parent=True)
            observed = (len(payload), digest_bytes(payload))
            if observed == (
                    HARDENED_PAPER_RUNTIME_PROFILE_BYTES,
                    HARDENED_PAPER_RUNTIME_PROFILE_SHA256):
                _parse_paper_runtime_profile(
                    payload, hardened=True, reason=reason)
                candidate_kind = "HARDENED"
            elif observed == (
                    LEGACY_PAPER_RUNTIME_PROFILE_BYTES,
                    LEGACY_PAPER_RUNTIME_PROFILE_SHA256):
                _parse_paper_runtime_profile(
                    payload, hardened=False, reason=reason)
                candidate_kind = "LEGACY"
            else:
                raise HandoffError(reason)
            candidate = _profile_file_evidence(
                PAPER_RUNTIME_PROFILE_CANDIDATE_PATH, payload, metadata)

        retained: dict[str, Any] | None = None
        if _path_exists_nofollow(PAPER_RUNTIME_PROFILE_RETAINED_PATH):
            payload, metadata = secure_read(
                PAPER_RUNTIME_PROFILE_RETAINED_PATH, reason,
                expected_uid=ROOT_UID, expected_gid=ROOT_GID,
                modes=frozenset({0o600}),
                maximum=LEGACY_PAPER_RUNTIME_PROFILE_BYTES,
                trusted_parent=True)
            _parse_paper_runtime_profile(payload, hardened=False, reason=reason)
            retained = _profile_file_evidence(
                PAPER_RUNTIME_PROFILE_RETAINED_PATH, payload, metadata)

        if (target_kind, backup, candidate_kind, retained) == (
                "LEGACY", None, None, None):
            state = "LEGACY"
        elif (target_kind == "LEGACY" and backup is not None and
              candidate is None and retained is None):
            state = "LEGACY_BACKED_UP"
        elif (target_kind == "LEGACY" and backup is not None and
              candidate_kind == "HARDENED" and retained is None):
            state = "LEGACY_CANDIDATE"
        elif (target_kind == "HARDENED" and backup is not None and
              candidate_kind == "LEGACY" and retained is None):
            state = "HARDENED_CANDIDATE"
        elif (target_kind == "HARDENED" and backup is not None and
              candidate is None and retained is not None):
            state = "HARDENED"
        else:
            raise HandoffError(reason)
        return _validate_paper_runtime_profile_state({
            "state": state, "target": target, "legacy_backup": backup,
            "candidate": candidate, "retained_legacy": retained,
        })

    def backup_legacy_paper_runtime_profile(self) -> None:
        reason = "HANDOFF_RUNTIME_PROFILE_BACKUP_FAILED"
        state = self.paper_runtime_profile_hardening_state()
        if state["state"] != "LEGACY":
            _require(state["state"] in {
                "LEGACY_BACKED_UP", "LEGACY_CANDIDATE",
                "HARDENED_CANDIDATE", "HARDENED",
            }, reason)
            return
        payload, metadata = secure_read(
            PAPER_RUNTIME_PROFILE_PATH, reason, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o644}),
            maximum=LEGACY_PAPER_RUNTIME_PROFILE_BYTES, trusted_parent=True)
        _parse_paper_runtime_profile(payload, hardened=False, reason=reason)
        _require(
            _profile_file_evidence(
                PAPER_RUNTIME_PROFILE_PATH, payload, metadata) ==
            state["target"], reason)
        parent = _open_anchored_directory(
            PAPER_RUNTIME_PROFILE_BACKUP_PATH.parent, reason)
        descriptor = -1
        try:
            descriptor = os.open(
                PAPER_RUNTIME_PROFILE_BACKUP_PATH.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC,
                0o600, dir_fd=parent)
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fchmod(descriptor, 0o600)
            os.fchown(descriptor, ROOT_UID, ROOT_GID)
            os.fsync(descriptor)
            os.fsync(parent)
        except (OSError, HandoffError) as error:
            if isinstance(error, HandoffError):
                raise
            raise HandoffError(reason) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)
        _require(
            self.paper_runtime_profile_hardening_state()["state"] ==
                "LEGACY_BACKED_UP", reason)

    def prepare_paper_runtime_profile_candidate(self) -> None:
        reason = "HANDOFF_RUNTIME_PROFILE_CANDIDATE_FAILED"
        state = self.paper_runtime_profile_hardening_state()
        if state["state"] == "LEGACY_CANDIDATE":
            return
        _require(state["state"] == "LEGACY_BACKED_UP", reason)
        payload, metadata = secure_read(
            PAPER_RUNTIME_PROFILE_BACKUP_PATH, reason,
            expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o600}),
            maximum=LEGACY_PAPER_RUNTIME_PROFILE_BYTES, trusted_parent=True)
        _parse_paper_runtime_profile(payload, hardened=False, reason=reason)
        _require(
            _profile_file_evidence(
                PAPER_RUNTIME_PROFILE_BACKUP_PATH, payload, metadata) ==
            state["legacy_backup"], reason)
        hardened = _harden_paper_runtime_profile(payload)
        parent = _open_trusted_directory(PAPER_RUNTIME_PROFILE_PATH.parent,
                                         reason)
        descriptor = -1
        try:
            descriptor = os.open(
                PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC,
                0o644, dir_fd=parent)
            offset = 0
            while offset < len(hardened):
                offset += os.write(descriptor, hardened[offset:])
            os.fchmod(descriptor, 0o644)
            os.fchown(descriptor, ROOT_UID, ROOT_GID)
            os.fsync(descriptor)
            os.fsync(parent)
        except (OSError, HandoffError) as error:
            if isinstance(error, HandoffError):
                raise
            raise HandoffError(reason) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)
        _require(
            self.paper_runtime_profile_hardening_state()["state"] ==
                "LEGACY_CANDIDATE", reason)

    def exchange_paper_runtime_profile_candidate(self) -> None:
        reason = "HANDOFF_RUNTIME_PROFILE_EXCHANGE_FAILED"
        state = self.paper_runtime_profile_hardening_state()
        if state["state"] in {"HARDENED_CANDIDATE", "HARDENED"}:
            return
        _require(state["state"] == "LEGACY_CANDIDATE", reason)
        parent = _open_trusted_directory(PAPER_RUNTIME_PROFILE_PATH.parent,
                                         reason)
        try:
            _rename_exchange(
                parent, PAPER_RUNTIME_PROFILE_PATH.name, parent,
                PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.name, reason)
            os.fsync(parent)
        except OSError as error:
            raise HandoffError(reason) from error
        finally:
            os.close(parent)
        _require(
            self.paper_runtime_profile_hardening_state()["state"] ==
                "HARDENED_CANDIDATE", reason)

    def remove_preexchange_paper_runtime_profile_candidate(self) -> None:
        reason = "HANDOFF_RUNTIME_PROFILE_CANDIDATE_CLEANUP_FAILED"
        state = self.paper_runtime_profile_hardening_state()
        if state["state"] in {"LEGACY", "LEGACY_BACKED_UP"}:
            return
        _require(state["state"] == "LEGACY_CANDIDATE", reason)
        expected = state["candidate"]
        parent = _open_trusted_directory(
            PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.parent, reason)
        descriptor = -1
        try:
            before = os.stat(
                PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.name, dir_fd=parent,
                follow_symlinks=False)
            descriptor = os.open(
                PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.name,
                os.O_RDONLY | NOFOLLOW | CLOEXEC, dir_fd=parent)
            opened = os.fstat(descriptor)
            payload = os.read(
                descriptor, LEGACY_PAPER_RUNTIME_PROFILE_BYTES + 1)
            _parse_paper_runtime_profile(payload, hardened=True, reason=reason)
            _require(
                _identity(before) == _identity(opened) and
                _profile_file_evidence(
                    PAPER_RUNTIME_PROFILE_CANDIDATE_PATH,
                    payload, opened) == expected, reason)
            os.unlink(PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.name, dir_fd=parent)
            os.fsync(parent)
        except (OSError, HandoffError) as error:
            if isinstance(error, HandoffError):
                raise
            raise HandoffError(reason) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)
        _require(
            self.paper_runtime_profile_hardening_state()["state"] ==
                "LEGACY_BACKED_UP", reason)

    @staticmethod
    def paper_runtime_profile_candidate_absent() -> bool:
        reason = "HANDOFF_RUNTIME_PROFILE_CANDIDATE_ABSENCE_INVALID"
        parent = _open_trusted_directory(
            PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.parent, reason)
        try:
            identity = _trusted_directory_identity(parent, reason)
            for _attempt in range(2):
                try:
                    os.stat(
                        PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.name,
                        dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                except OSError as error:
                    raise HandoffError(reason) from error
                else:
                    raise HandoffError(reason)
            _require(identity == _trusted_directory_identity(parent, reason),
                     reason)
            return True
        finally:
            os.close(parent)

    def seal_retained_legacy_paper_runtime_profile(self) -> None:
        reason = "HANDOFF_RUNTIME_PROFILE_RETAIN_LEGACY_FAILED"
        state = self.paper_runtime_profile_hardening_state()
        if state["state"] == "HARDENED":
            return
        _require(state["state"] == "HARDENED_CANDIDATE", reason)
        candidate_parent = _open_trusted_directory(
            PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.parent, reason)
        retained_parent = _open_anchored_directory(
            PAPER_RUNTIME_PROFILE_RETAINED_PATH.parent, reason)
        descriptor = -1
        try:
            descriptor = os.open(
                PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.name,
                os.O_RDWR | NOFOLLOW | CLOEXEC, dir_fd=candidate_parent)
            os.fchmod(descriptor, 0o600)
            os.fchown(descriptor, ROOT_UID, ROOT_GID)
            os.fsync(descriptor)
            os.fsync(candidate_parent)
            _rename_noreplace(
                candidate_parent, PAPER_RUNTIME_PROFILE_CANDIDATE_PATH.name,
                retained_parent, PAPER_RUNTIME_PROFILE_RETAINED_PATH.name,
                reason)
            os.fsync(candidate_parent)
            os.fsync(retained_parent)
        except (OSError, HandoffError) as error:
            if isinstance(error, HandoffError):
                raise
            raise HandoffError(reason) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(candidate_parent)
            os.close(retained_parent)
        _require(
            self.paper_runtime_profile_hardening_state()["state"] ==
                "HARDENED", reason)

    def snapshot(self, *, tighten: bool = False) -> dict[str, Any]:
        watch: dict[str, dict[str, Any]] = {}
        for unit in WATCH_UNITS:
            watch[unit] = {
                **self._show(unit),
                "persistent_masked": self._mask_present(
                    PERSISTENT_SYSTEMD_ROOT, unit),
                "runtime_masked": self._mask_present(
                    RUNTIME_SYSTEMD_ROOT, unit),
            }
        transient = {
            unit: self._show(unit) for unit in self._transient_units()}
        paper = {unit: self._show(unit) for unit in PAPER_UNITS}
        session_authority_count = _session_authority_count()
        private = _directory_entries(WATCH_PRIVATE)
        export_present = _path_exists_nofollow(WATCH_EXPORT)
        transaction_present = _path_exists_nofollow(
            WATCH_CUSTODIAN_TRANSACTION)
        socket_count = sum(
            1 for path in WATCH_SOCKET_PATHS if _path_exists_nofollow(path))
        timer_count = sum(
            1 for unit in WATCH_TIMER_UNITS
            if watch[unit]["active_state"] != "inactive" or
               bool(watch[unit]["job"])) + sum(
            1 for unit, state in transient.items()
            if unit.endswith(".timer") and
            (state["active_state"] != "inactive" or bool(state["job"])))
        authority_count = (
            session_authority_count + len(private) + int(export_present) +
            int(transaction_present))
        transient_residue = sum(
            1 for state in transient.values()
            if state["active_state"] != "inactive" or bool(state["job"]))
        cleanup_count = authority_count + socket_count + transient_residue
        identity_count, identity_sha256 = self._identity_manifest()
        return validate_snapshot({
            "watch_units": watch, "transient_units": transient,
            "paper_units": paper,
            "broker": self._broker(tighten),
            "kill_switch_engaged": self._kill_switch(
                KILL_SWITCH_PATH, PAPER_CONTROL_GID),
            "global_kill_switch_engaged": self._kill_switch(
                GLOBAL_KILL_SWITCH_PATH, GLOBAL_PAPER_CONTROL_GID),
            "identity_count": identity_count,
            "identity_manifest_sha256": identity_sha256,
            "watch_authority_count": authority_count,
            "watch_socket_count": socket_count,
            "watch_timer_count": timer_count,
            "cleanup_residue_count": cleanup_count,
        })


def validate_snapshot(value: Any) -> dict[str, Any]:
    reason = "HANDOFF_BOUNDARY_INVALID"
    _require(isinstance(value, dict) and set(value) == SNAPSHOT_FIELDS, reason)
    watch = value.get("watch_units")
    _require(isinstance(watch, dict) and set(watch) == set(WATCH_UNITS), reason)
    for unit, state_value in watch.items():
        _require(UNIT_TOKEN.fullmatch(unit) is not None and
                 isinstance(state_value, dict) and
                 set(state_value) == UNIT_STATE_FIELDS, reason)
        for field in ("load_state", "active_state", "sub_state", "job",
                      "unit_file_state"):
            _require(type(state_value.get(field)) is str, reason)
        _require(type(state_value.get("persistent_masked")) is bool and
                 type(state_value.get("runtime_masked")) is bool, reason)
    transient = value.get("transient_units")
    _require(isinstance(transient, dict) and
             len(transient) <= MAXIMUM_TRANSIENT_UNITS, reason)
    for unit, state_value in transient.items():
        _require(
            type(unit) is str and TRANSIENT_UNIT.fullmatch(unit) is not None and
            isinstance(state_value, dict) and set(state_value) == {
                "load_state", "active_state", "sub_state", "job",
                "unit_file_state"} and all(
                type(state_value.get(field)) is str for field in (
                    "load_state", "active_state", "sub_state", "job",
                    "unit_file_state")), reason)
    paper = value.get("paper_units")
    _require(isinstance(paper, dict) and set(paper) == set(PAPER_UNITS), reason)
    for unit, state_value in paper.items():
        _require(UNIT_TOKEN.fullmatch(unit) is not None and
                 isinstance(state_value, dict), reason)
        for field in ("load_state", "active_state", "sub_state", "job",
                      "unit_file_state"):
            _require(type(state_value.get(field)) is str, reason)
    broker = value.get("broker")
    _require(isinstance(broker, dict) and set(broker) == BROKER_STATE_FIELDS and
             DIGEST.fullmatch(str(broker.get("policy_sha256"))) is not None and
             type(broker.get("authorized_connectors")) is int and
             isinstance(broker.get("authorized_uids"), list) and
             all(type(item) is int and item >= 0
                 for item in broker["authorized_uids"]) and
             broker.get("protected_ports") == 4, reason)
    for field in (
        "watch_authority_count", "watch_socket_count", "watch_timer_count",
        "cleanup_residue_count", "identity_count",
    ):
        _integer(value.get(field), reason)
    _require(
        type(value.get("kill_switch_engaged")) is bool and
        type(value.get("global_kill_switch_engaged")) is bool and
        type(value.get("identity_manifest_sha256")) is str and
        DIGEST.fullmatch(value["identity_manifest_sha256"]) is not None,
        reason)
    return value


def _paper_inactive(snapshot: Mapping[str, Any]) -> bool:
    for unit, state in snapshot["paper_units"].items():
        suffix = ".socket" if unit.endswith(".socket") else ".service"
        if not (
            state.get("active_state") == "inactive" and
            not state.get("job") and state.get("unit_file_state") in
                PAPER_INERT_UNIT_FILE_STATES[suffix]
        ):
            return False
    return True


def _broker_deny_all(snapshot: Mapping[str, Any]) -> bool:
    broker = snapshot["broker"]
    return (broker.get("authorized_connectors") == 0 and
            broker.get("authorized_uids") == [])


def _safe_boundary(snapshot: Mapping[str, Any]) -> bool:
    return (
        _paper_inactive(snapshot) and _broker_deny_all(snapshot) and
        snapshot.get("kill_switch_engaged") is True and
        snapshot.get("global_kill_switch_engaged") is True and
        snapshot.get("identity_count") == 0 and
        snapshot.get("identity_manifest_sha256") ==
            DISABLED_IDENTITY_MANIFEST_SHA256
    )


def _watch_complete(snapshot: Mapping[str, Any]) -> bool:
    return (
        _safe_boundary(snapshot) and
        all(
            state.get("active_state") == "inactive" and
            not state.get("job") and
            state.get("persistent_masked") is True and
            state.get("runtime_masked") is True
            for state in snapshot["watch_units"].values()) and
        all(
            state.get("active_state") == "inactive" and not state.get("job")
            for state in snapshot["transient_units"].values()) and
        snapshot.get("watch_authority_count") == 0 and
        snapshot.get("watch_socket_count") == 0 and
        snapshot.get("watch_timer_count") == 0 and
        snapshot.get("cleanup_residue_count") == 0
    )


def _reference(value: Any, reason: str) -> dict[str, str]:
    _require(isinstance(value, dict) and set(value) == REFERENCE_FIELDS, reason)
    path = _canonical_path(Path(value.get("path", "")), reason)
    return {
        "path": str(path),
        "file_sha256": _digest(value.get("file_sha256"), reason),
        "body_sha256": _digest(value.get("body_sha256"), reason),
    }


def _producer_reference(value: Any, reason: str) -> dict[str, str]:
    _require(isinstance(value, dict) and set(value) == PRODUCER_FIELDS, reason)
    path = _canonical_path(Path(value.get("path", "")), reason)
    file_sha256 = _digest(value.get("file_sha256"), reason)
    _require(path == INSTALLED_EXECUTABLE and
             file_sha256 != "sha256:" + "0" * 64, reason)
    return {"path": str(path), "file_sha256": file_sha256}


def validate_context(value: Any) -> dict[str, Any]:
    reason = "HANDOFF_CONTEXT_INVALID"
    _require(isinstance(value, dict) and set(value) == CONTEXT_FIELDS, reason)
    _require(value.get("round") == ROUND and value.get("domain") == DOMAIN and
             type(value.get("campaign_id")) is str and
             TOKEN.fullmatch(value["campaign_id"]) is not None, reason)
    _digest(value.get("source_baseline_sha256"), reason)
    _producer_reference(value.get("producer"), reason)
    _require(value.get("production_mode") == PRODUCTION_MODE, reason)
    _reference(value.get("activation_receipt"), reason)
    _reference(value.get("p1_audit_receipt"), reason)
    _reference(value.get("freeze_bundle"), reason)
    _canonical_path(Path(value.get("output_path", "")), reason)
    return value


def _make_context(
    activation: InputBinding, audit: InputBinding,
    producer: ProducerBinding, source: str, campaign: str, output_path: Path,
) -> dict[str, Any]:
    output_path = _canonical_path(output_path, "HANDOFF_OUTPUT_PATH_INVALID")
    _require(
        len({activation.path, audit.path, output_path}) == 3,
        "HANDOFF_OUTPUT_PATH_INVALID")
    return validate_context({
        "round": ROUND, "domain": DOMAIN, "campaign_id": campaign,
        "source_baseline_sha256": source,
        "producer": producer.reference, "production_mode": PRODUCTION_MODE,
        "activation_receipt": activation.reference,
        "p1_audit_receipt": audit.reference,
        "freeze_bundle": _reference(
            audit.document["freeze_bundle"], "HANDOFF_CONTEXT_INVALID"),
        "output_path": str(output_path),
    })


def _fallback_snapshot() -> dict[str, Any]:
    unknown_watch = {
        unit: {
            "load_state": "unknown", "active_state": "unknown",
            "sub_state": "unknown", "job": "unknown",
            "unit_file_state": "unknown", "persistent_masked": False,
            "runtime_masked": False,
        }
        for unit in WATCH_UNITS
    }
    unknown_paper = {
        unit: {
            "load_state": "unknown", "active_state": "unknown",
            "sub_state": "unknown", "job": "unknown",
            "unit_file_state": "unknown",
        }
        for unit in PAPER_UNITS
    }
    return validate_snapshot({
        "watch_units": unknown_watch, "transient_units": {
            "hepta-p1-shadow-admission-round1.service": {
                "load_state": "unknown", "active_state": "unknown",
                "sub_state": "unknown", "job": "unknown",
                "unit_file_state": "unknown",
            }},
        "paper_units": unknown_paper,
        "broker": {
            "policy_sha256": "sha256:" + "0" * 64,
            "authorized_connectors": 1, "authorized_uids": [0],
            "protected_ports": 4,
        },
        "kill_switch_engaged": False, "global_kill_switch_engaged": False,
        "identity_count": 1,
        "identity_manifest_sha256": "sha256:" + "0" * 64,
        "watch_authority_count": 1,
        "watch_socket_count": 1, "watch_timer_count": 1,
        "cleanup_residue_count": 1,
    })


def _receipt_body(
    context: Mapping[str, Any], snapshot: Mapping[str, Any],
    status: str, *, crash_recovery_verified: bool,
    profile_restoration: Mapping[str, Any] | None = None,
    profile_candidate_absent: bool,
    paper_runtime_profile_hardening: Mapping[str, Any] | None = None,
    paper_runtime_profile_candidate_absent: bool,
    issued_at_ms: int | None = None,
) -> dict[str, Any]:
    context = validate_context(dict(context))
    snapshot = validate_snapshot(dict(snapshot))
    _require(status in {"WATCH_RETIRED_HANDOFF_COMPLETE", "FAILED_CLOSED"},
             "HANDOFF_RECEIPT_INVALID")
    issued = time.time_ns() // 1_000_000 if issued_at_ms is None else issued_at_ms
    _integer(issued, "HANDOFF_RECEIPT_INVALID")
    watch_inactive = all(
        state.get("active_state") == "inactive" and not state.get("job")
        for state in snapshot["watch_units"].values())
    restoration = (None if profile_restoration is None else
                   _validate_profile_restoration(dict(profile_restoration)))
    hardening = (
        None if paper_runtime_profile_hardening is None else
        _validate_paper_runtime_profile_hardening(
            dict(paper_runtime_profile_hardening)))
    body = {
        "schema": RECEIPT_SCHEMA, "version": 2, "status": status,
        "issued_at_ms": issued, "expires_at_ms": issued + VALIDITY_MS,
        "round": ROUND, "domain": DOMAIN,
        "campaign_id": context["campaign_id"],
        "source_baseline_sha256": context["source_baseline_sha256"],
        "producer": context["producer"],
        "production_mode": context["production_mode"],
        "activation_receipt": context["activation_receipt"],
        "p1_audit_receipt": context["p1_audit_receipt"],
        "freeze_bundle": context["freeze_bundle"],
        "watch_units_inactive": watch_inactive,
        "watch_authority_count": snapshot["watch_authority_count"],
        "watch_socket_count": snapshot["watch_socket_count"],
        "watch_timer_count": snapshot["watch_timer_count"],
        "paper_units_inactive": _paper_inactive(snapshot),
        "broker_deny_all": _broker_deny_all(snapshot),
        "kill_switch_engaged": snapshot["kill_switch_engaged"],
        "global_kill_switch_engaged":
            snapshot["global_kill_switch_engaged"],
        "identity_count": snapshot["identity_count"],
        "identity_manifest_sha256": snapshot["identity_manifest_sha256"],
        "paper_profile_restored": restoration is not None,
        "paper_profile_restoration": restoration,
        "profile_candidate_absent": profile_candidate_absent,
        "paper_runtime_profile_hardened": hardening is not None,
        "paper_runtime_profile_hardening": hardening,
        "paper_runtime_profile_candidate_absent":
            paper_runtime_profile_candidate_absent,
        "crash_recovery_verified": crash_recovery_verified,
        "cleanup_residue_count": snapshot["cleanup_residue_count"],
        "paper_authorized": False, "live_authorized": False,
        "mutation_authorized": False, "direct_broker_access": False,
        "order_submission_authorized": False,
    }
    if status == "WATCH_RETIRED_HANDOFF_COMPLETE":
        _require(_watch_complete(snapshot) and crash_recovery_verified and
                 restoration is not None and profile_candidate_absent and
                 hardening is not None and
                 paper_runtime_profile_candidate_absent,
                 "HANDOFF_COMPLETION_NOT_PROVEN")
    return seal(body)


def validate_receipt(payload: bytes) -> dict[str, Any]:
    reason = "HANDOFF_RECEIPT_INVALID"
    document = strict_document(payload, reason)
    _require(set(document) == RECEIPT_FIELDS and
             document.get("schema") == RECEIPT_SCHEMA and
             document.get("version") == 2 and document.get("round") == ROUND
             and document.get("domain") == DOMAIN and
             document.get("status") in {
                "WATCH_RETIRED_HANDOFF_COMPLETE", "FAILED_CLOSED"}, reason)
    issued = _integer(document.get("issued_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    _require(issued < expires and expires - issued == VALIDITY_MS, reason)
    _require(type(document.get("campaign_id")) is str and
             TOKEN.fullmatch(document["campaign_id"]) is not None, reason)
    _digest(document.get("source_baseline_sha256"), reason)
    _producer_reference(document.get("producer"), reason)
    _require(document.get("production_mode") == PRODUCTION_MODE, reason)
    _reference(document.get("activation_receipt"), reason)
    _reference(document.get("p1_audit_receipt"), reason)
    _reference(document.get("freeze_bundle"), reason)
    for field in (
        "watch_authority_count", "watch_socket_count", "watch_timer_count",
        "cleanup_residue_count", "identity_count",
    ):
        _integer(document.get(field), reason)
    for field in (
        "watch_units_inactive", "paper_units_inactive", "broker_deny_all",
        "kill_switch_engaged", "global_kill_switch_engaged",
        "paper_profile_restored", "profile_candidate_absent",
        "paper_runtime_profile_hardened",
        "paper_runtime_profile_candidate_absent",
        "crash_recovery_verified",
        "paper_authorized", "live_authorized", "mutation_authorized",
        "direct_broker_access", "order_submission_authorized",
    ):
        _require(type(document.get(field)) is bool, reason)
    _require(_all_false(document, (
        "paper_authorized", "live_authorized", "mutation_authorized",
        "direct_broker_access", "order_submission_authorized")), reason)
    _require(document["profile_candidate_absent"] is True, reason)
    _require(
        document["paper_runtime_profile_candidate_absent"] is True, reason)
    _digest(document.get("identity_manifest_sha256"), reason)
    restoration = document.get("paper_profile_restoration")
    if document["paper_profile_restored"]:
        _validate_profile_restoration(restoration)
    else:
        _require(restoration is None, reason)
    hardening = document.get("paper_runtime_profile_hardening")
    if document["paper_runtime_profile_hardened"]:
        _validate_paper_runtime_profile_hardening(hardening)
    else:
        _require(hardening is None, reason)
    if document["status"] == "WATCH_RETIRED_HANDOFF_COMPLETE":
        _require(
            document["watch_units_inactive"] is True and
            document["watch_authority_count"] == 0 and
            document["watch_socket_count"] == 0 and
            document["watch_timer_count"] == 0 and
            document["paper_units_inactive"] is True and
            document["broker_deny_all"] is True and
            document["kill_switch_engaged"] is True and
            document["global_kill_switch_engaged"] is True and
            document["identity_count"] == 0 and
            document["identity_manifest_sha256"] ==
                DISABLED_IDENTITY_MANIFEST_SHA256 and
            document["paper_profile_restored"] is True and
            document["profile_candidate_absent"] is True and
            document["paper_runtime_profile_hardened"] is True and
            document["paper_runtime_profile_candidate_absent"] is True and
            document["crash_recovery_verified"] is True and
            document["cleanup_residue_count"] == 0, reason)
    return document


def _receipt_matches_context(
    receipt: Mapping[str, Any], context: Mapping[str, Any],
) -> bool:
    return all(receipt.get(field) == context.get(field) for field in (
        "round", "domain", "campaign_id", "source_baseline_sha256",
        "producer", "production_mode", "activation_receipt",
        "p1_audit_receipt", "freeze_bundle"))


def _read_receipt_if_present(path: Path) -> dict[str, Any] | None:
    if not _path_exists_nofollow(path):
        return None
    payload, _ = secure_read(
        path, "HANDOFF_RECEIPT_INVALID", modes=frozenset({0o600}))
    return validate_receipt(payload)


def _publish_receipt(
    context: Mapping[str, Any], snapshot: Mapping[str, Any], status: str,
    *, crash_recovery_verified: bool,
    profile_restoration: Mapping[str, Any] | None = None,
    profile_candidate_absent: bool,
    paper_runtime_profile_hardening: Mapping[str, Any] | None = None,
    paper_runtime_profile_candidate_absent: bool,
) -> dict[str, Any]:
    output = Path(context["output_path"])
    document = _receipt_body(
        context, snapshot, status,
        crash_recovery_verified=crash_recovery_verified,
        profile_restoration=profile_restoration,
        profile_candidate_absent=profile_candidate_absent,
        paper_runtime_profile_hardening=paper_runtime_profile_hardening,
        paper_runtime_profile_candidate_absent=
            paper_runtime_profile_candidate_absent)
    payload = canonical_bytes(document)
    return _publish_payload(
        output, payload, reason="HANDOFF_RECEIPT_PUBLISH_FAILED",
        validate=validate_receipt, invoke_publish_seams=True)


def _reopen_inputs(
    activation: InputBinding, audit: InputBinding,
    producer: ProducerBinding,
) -> None:
    activation.reopen("HANDOFF_ACTIVATION_RECEIPT_REBOUND")
    audit.reopen("HANDOFF_P1_AUDIT_RECEIPT_REBOUND")
    audit_producer = audit.document["producer"]
    payload, _metadata = secure_read(
        P1_AUDITOR_EXECUTABLE, "HANDOFF_P1_AUDITOR_IMAGE_REBOUND",
        expected_uid=ROOT_UID, expected_gid=ROOT_GID,
        modes=frozenset({0o500, 0o555, 0o700, 0o755}),
        trusted_parent=True)
    _require(
        audit_producer == {
            "path": str(P1_AUDITOR_EXECUTABLE),
            "file_sha256": digest_bytes(payload),
        }, "HANDOFF_P1_AUDITOR_IMAGE_REBOUND")
    producer.reopen()


def _mutation_seam(phase: str, unit: str | None = None) -> None:
    if MUTATION_SEAM_HOOK is not None:
        MUTATION_SEAM_HOOK(phase, unit)


def _success_mutation(
    journal: Journal, executor: Any,
    activation: InputBinding, audit: InputBinding,
    producer: ProducerBinding,
    *, operation: str, unit: str,
) -> dict[str, Any]:
    token = _systemd_phase_token(unit)
    if operation == "DISABLE":
        method = executor.disable_and_stop
        intent = f"DISABLE_{token}_INTENT"
        applied = f"DISABLE_{token}_APPLIED"
    elif operation == "PERSISTENT_MASK":
        method = executor.mask_persistent
        intent = f"PERSISTENT_MASK_{token}_INTENT"
        applied = f"PERSISTENT_MASK_{token}_APPLIED"
    elif operation == "RUNTIME_MASK":
        method = executor.mask_runtime
        intent = f"RUNTIME_MASK_{token}_INTENT"
        applied = f"RUNTIME_MASK_{token}_APPLIED"
    else:
        raise HandoffError("HANDOFF_MUTATION_INVALID")
    journal.append(intent, {"unit": unit, "operation": operation})
    _reopen_inputs(activation, audit, producer)
    _mutation_seam("BEFORE_" + operation, unit)
    method(unit)
    _mutation_seam("AFTER_" + operation, unit)
    _reopen_inputs(activation, audit, producer)
    snapshot = validate_snapshot(executor.snapshot())
    _require(_safe_boundary(snapshot), "HANDOFF_SAFETY_BOUNDARY_LOST")
    journal.append(applied, {
        "unit": unit, "operation": operation, "snapshot": snapshot})
    return snapshot


def _profile_intent_evidence(state: Mapping[str, Any]) -> dict[str, Any]:
    state = _validate_profile_state(dict(state))
    _require(state["state"] == "PRE",
             "HANDOFF_PROFILE_RESTORATION_STATE_INVALID")
    return {
        "target_before": state["target"],
        "dormant_backup": state["dormant_backup"],
        "forward_retained_dormant": state["forward_retained_dormant"],
        "forward_transition_receipt": state["forward_transition_receipt"],
        "profile_deployment_receipt": state["profile_deployment_receipt"],
        "forward_preimage_evidence": state["forward_preimage_evidence"],
        "candidate_path": str(PROFILE_CANDIDATE_PATH),
        "retired_watch_path": str(PROFILE_RETIRED_WATCH_PATH),
        "exchange_method": "RENAME_EXCHANGE",
        "forward_only_after_exchange": True,
    }


def _validate_profile_intent_rebound(
    journal: Journal, state: Mapping[str, Any],
) -> None:
    records = {record.phase: record for record in journal.load()}
    record = records.get("PROFILE_RESTORE_INTENT")
    _require(record is not None,
             "HANDOFF_PROFILE_RESTORATION_JOURNAL_INVALID")
    evidence = record.document["evidence"]
    _require(isinstance(evidence, dict),
             "HANDOFF_PROFILE_RESTORATION_JOURNAL_INVALID")
    for field in (
        "dormant_backup", "forward_retained_dormant",
        "forward_transition_receipt", "profile_deployment_receipt",
        "forward_preimage_evidence",
    ):
        _require(evidence.get(field) == state.get(field),
                 "HANDOFF_PROFILE_RESTORATION_REBOUND")
    _require(
        evidence.get("candidate_path") == str(PROFILE_CANDIDATE_PATH) and
        evidence.get("retired_watch_path") == str(PROFILE_RETIRED_WATCH_PATH)
        and evidence.get("exchange_method") == "RENAME_EXCHANGE" and
        evidence.get("forward_only_after_exchange") is True,
        "HANDOFF_PROFILE_RESTORATION_REBOUND")


def _append_profile_prefix_for_state(
    journal: Journal, state: Mapping[str, Any],
) -> None:
    """Journal recovered mutation facts in the only legal success order."""

    current = [record.phase for record in journal.load()]
    target = state["state"]
    desired = ["PROFILE_RESTORE_INTENT"]
    if target in {"PRE_CANDIDATE", "POST_CANDIDATE", "RESTORED"}:
        desired.append("PROFILE_CANDIDATE_READY")
    if target in {"POST_CANDIDATE", "RESTORED"}:
        desired.extend(("PROFILE_EXCHANGE_INTENT", "PROFILE_EXCHANGED"))
    if target == "RESTORED":
        desired.append("PROFILE_RETIRED_WATCH_SEALED")
    for phase in desired:
        if phase in current:
            continue
        _require(SUCCESS_PHASES[len(current)] == phase,
                 "HANDOFF_PROFILE_RESTORATION_JOURNAL_INVALID")
        journal.append(phase, {
            "profile_state": dict(state), "recovered_state_observation": True,
        })
        current.append(phase)


def _perform_profile_restoration(
    journal: Journal, executor: Any, activation: InputBinding,
    audit: InputBinding, producer: ProducerBinding,
) -> dict[str, Any]:
    snapshot = validate_snapshot(executor.snapshot())
    _require(_watch_complete(snapshot), "HANDOFF_PROFILE_PREFLIGHT_FAILED")
    state = _validate_profile_state(executor.profile_restoration_state())
    phases = [record.phase for record in journal.load()]
    if "PROFILE_RESTORE_INTENT" not in phases:
        _require(state["state"] == "PRE",
                 "HANDOFF_PROFILE_RESTORATION_STATE_INVALID")
        journal.append("PROFILE_RESTORE_INTENT", _profile_intent_evidence(state))
    else:
        _validate_profile_intent_rebound(journal, state)
    _reopen_inputs(activation, audit, producer)

    if state["state"] == "PRE":
        _mutation_seam("BEFORE_PROFILE_CANDIDATE")
        executor.prepare_profile_candidate()
        _mutation_seam("AFTER_PROFILE_CANDIDATE")
        state = _validate_profile_state(executor.profile_restoration_state())
        _require(state["state"] == "PRE_CANDIDATE",
                 "HANDOFF_PROFILE_CANDIDATE_FAILED")
    _append_profile_prefix_for_state(journal, state)
    _validate_profile_intent_rebound(journal, state)
    _reopen_inputs(activation, audit, producer)
    _require(_watch_complete(validate_snapshot(executor.snapshot())),
             "HANDOFF_SAFETY_BOUNDARY_LOST")

    if state["state"] == "PRE_CANDIDATE":
        phases = [record.phase for record in journal.load()]
        if "PROFILE_EXCHANGE_INTENT" not in phases:
            journal.append("PROFILE_EXCHANGE_INTENT", {
                "profile_state": state, "exchange_method": "RENAME_EXCHANGE",
                "forward_only_after_exchange": True,
            })
        _reopen_inputs(activation, audit, producer)
        _mutation_seam("BEFORE_PROFILE_EXCHANGE")
        executor.exchange_profile_candidate()
        _mutation_seam("AFTER_PROFILE_EXCHANGE")
        state = _validate_profile_state(executor.profile_restoration_state())
        _require(state["state"] == "POST_CANDIDATE",
                 "HANDOFF_PROFILE_EXCHANGE_FAILED")
    _append_profile_prefix_for_state(journal, state)
    _validate_profile_intent_rebound(journal, state)
    _reopen_inputs(activation, audit, producer)
    _require(_watch_complete(validate_snapshot(executor.snapshot())),
             "HANDOFF_SAFETY_BOUNDARY_LOST")

    if state["state"] == "POST_CANDIDATE":
        _mutation_seam("BEFORE_PROFILE_RETIRE_WATCH")
        executor.seal_retired_watch()
        _mutation_seam("AFTER_PROFILE_RETIRE_WATCH")
        state = _validate_profile_state(executor.profile_restoration_state())
        _require(state["state"] == "RESTORED",
                 "HANDOFF_PROFILE_RETIRED_WATCH_FAILED")
    _append_profile_prefix_for_state(journal, state)
    _validate_profile_intent_rebound(journal, state)
    _reopen_inputs(activation, audit, producer)
    _require(_watch_complete(validate_snapshot(executor.snapshot())),
             "HANDOFF_SAFETY_BOUNDARY_LOST")
    return _profile_restoration_evidence(state, journal)


def _paper_runtime_profile_intent_evidence(
    state: Mapping[str, Any],
) -> dict[str, Any]:
    state = _validate_paper_runtime_profile_state(dict(state))
    _require(state["state"] == "LEGACY",
             "HANDOFF_RUNTIME_PROFILE_HARDENING_STATE_INVALID")
    return {
        "target_before": state["target"],
        "legacy_backup_path": str(PAPER_RUNTIME_PROFILE_BACKUP_PATH),
        "candidate_path": str(PAPER_RUNTIME_PROFILE_CANDIDATE_PATH),
        "retained_legacy_path": str(PAPER_RUNTIME_PROFILE_RETAINED_PATH),
        "legacy_file_sha256": LEGACY_PAPER_RUNTIME_PROFILE_SHA256,
        "hardened_file_sha256": HARDENED_PAPER_RUNTIME_PROFILE_SHA256,
        "changed_keys": sorted(PAPER_RUNTIME_PROFILE_HARDENED_LIMITS),
        "exchange_method": "RENAME_EXCHANGE",
        "forward_only_after_exchange": True,
    }


def _validate_paper_runtime_profile_intent_rebound(
    journal: Journal, state: Mapping[str, Any],
) -> None:
    state = _validate_paper_runtime_profile_state(dict(state))
    records = {record.phase: record for record in journal.load()}
    record = records.get("RUNTIME_PROFILE_HARDEN_INTENT")
    _require(record is not None,
             "HANDOFF_RUNTIME_PROFILE_HARDENING_JOURNAL_INVALID")
    evidence = record.document.get("evidence")
    _require(isinstance(evidence, dict),
             "HANDOFF_RUNTIME_PROFILE_HARDENING_JOURNAL_INVALID")
    before = evidence.get("target_before")
    _validate_profile_file_evidence(
        before, "HANDOFF_RUNTIME_PROFILE_HARDENING_REBOUND",
        path=PAPER_RUNTIME_PROFILE_PATH,
        sha256=LEGACY_PAPER_RUNTIME_PROFILE_SHA256,
        size=LEGACY_PAPER_RUNTIME_PROFILE_BYTES, mode=0o644)
    _require(
        evidence.get("legacy_backup_path") ==
            str(PAPER_RUNTIME_PROFILE_BACKUP_PATH) and
        evidence.get("candidate_path") ==
            str(PAPER_RUNTIME_PROFILE_CANDIDATE_PATH) and
        evidence.get("retained_legacy_path") ==
            str(PAPER_RUNTIME_PROFILE_RETAINED_PATH) and
        evidence.get("legacy_file_sha256") ==
            LEGACY_PAPER_RUNTIME_PROFILE_SHA256 and
        evidence.get("hardened_file_sha256") ==
            HARDENED_PAPER_RUNTIME_PROFILE_SHA256 and
        evidence.get("changed_keys") ==
            sorted(PAPER_RUNTIME_PROFILE_HARDENED_LIMITS) and
        evidence.get("exchange_method") == "RENAME_EXCHANGE" and
        evidence.get("forward_only_after_exchange") is True,
        "HANDOFF_RUNTIME_PROFILE_HARDENING_REBOUND")
    if state["state"] in {
            "LEGACY", "LEGACY_BACKED_UP", "LEGACY_CANDIDATE"}:
        _require(state["target"] == before,
                 "HANDOFF_RUNTIME_PROFILE_HARDENING_REBOUND")
    else:
        displaced = (state["candidate"] if
                     state["state"] == "HARDENED_CANDIDATE" else
                     state["retained_legacy"])
        _require(
            isinstance(displaced, dict) and
            displaced.get("device") == before.get("device") and
            displaced.get("inode") == before.get("inode") and
            displaced.get("file_sha256") ==
                LEGACY_PAPER_RUNTIME_PROFILE_SHA256 and
            displaced.get("bytes") == LEGACY_PAPER_RUNTIME_PROFILE_BYTES,
            "HANDOFF_RUNTIME_PROFILE_HARDENING_REBOUND")


def _append_paper_runtime_profile_prefix_for_state(
    journal: Journal, state: Mapping[str, Any],
) -> None:
    current = [record.phase for record in journal.load()]
    target = state["state"]
    desired = ["RUNTIME_PROFILE_HARDEN_INTENT"]
    if target in {
            "LEGACY_BACKED_UP", "LEGACY_CANDIDATE",
            "HARDENED_CANDIDATE", "HARDENED"}:
        desired.append("RUNTIME_PROFILE_LEGACY_BACKUP_SEALED")
    if target in {"LEGACY_CANDIDATE", "HARDENED_CANDIDATE", "HARDENED"}:
        desired.append("RUNTIME_PROFILE_CANDIDATE_READY")
    if target in {"HARDENED_CANDIDATE", "HARDENED"}:
        desired.extend((
            "RUNTIME_PROFILE_EXCHANGE_INTENT", "RUNTIME_PROFILE_EXCHANGED"))
    if target == "HARDENED":
        desired.append("RUNTIME_PROFILE_RETAINED_LEGACY_SEALED")
    for phase in desired:
        if phase in current:
            continue
        _require(SUCCESS_PHASES[len(current)] == phase,
                 "HANDOFF_RUNTIME_PROFILE_HARDENING_JOURNAL_INVALID")
        journal.append(phase, {
            "paper_runtime_profile_state": dict(state),
            "recovered_state_observation": True,
        })
        current.append(phase)


def _perform_paper_runtime_profile_hardening(
    journal: Journal, executor: Any, activation: InputBinding,
    audit: InputBinding, producer: ProducerBinding,
) -> dict[str, Any]:
    _require(_watch_complete(validate_snapshot(executor.snapshot())),
             "HANDOFF_RUNTIME_PROFILE_PREFLIGHT_FAILED")
    state = _validate_paper_runtime_profile_state(
        executor.paper_runtime_profile_hardening_state())
    phases = [record.phase for record in journal.load()]
    if "RUNTIME_PROFILE_HARDEN_INTENT" not in phases:
        _require(state["state"] == "LEGACY",
                 "HANDOFF_RUNTIME_PROFILE_HARDENING_STATE_INVALID")
        journal.append(
            "RUNTIME_PROFILE_HARDEN_INTENT",
            _paper_runtime_profile_intent_evidence(state))
    else:
        _validate_paper_runtime_profile_intent_rebound(journal, state)
    _reopen_inputs(activation, audit, producer)

    if state["state"] == "LEGACY":
        _mutation_seam("BEFORE_RUNTIME_PROFILE_BACKUP")
        executor.backup_legacy_paper_runtime_profile()
        _mutation_seam("AFTER_RUNTIME_PROFILE_BACKUP")
        state = _validate_paper_runtime_profile_state(
            executor.paper_runtime_profile_hardening_state())
        _require(state["state"] == "LEGACY_BACKED_UP",
                 "HANDOFF_RUNTIME_PROFILE_BACKUP_FAILED")
    _append_paper_runtime_profile_prefix_for_state(journal, state)
    _validate_paper_runtime_profile_intent_rebound(journal, state)
    _reopen_inputs(activation, audit, producer)
    _require(_watch_complete(validate_snapshot(executor.snapshot())),
             "HANDOFF_SAFETY_BOUNDARY_LOST")

    if state["state"] == "LEGACY_BACKED_UP":
        _mutation_seam("BEFORE_RUNTIME_PROFILE_CANDIDATE")
        executor.prepare_paper_runtime_profile_candidate()
        _mutation_seam("AFTER_RUNTIME_PROFILE_CANDIDATE")
        state = _validate_paper_runtime_profile_state(
            executor.paper_runtime_profile_hardening_state())
        _require(state["state"] == "LEGACY_CANDIDATE",
                 "HANDOFF_RUNTIME_PROFILE_CANDIDATE_FAILED")
    _append_paper_runtime_profile_prefix_for_state(journal, state)
    _validate_paper_runtime_profile_intent_rebound(journal, state)
    _reopen_inputs(activation, audit, producer)
    _require(_watch_complete(validate_snapshot(executor.snapshot())),
             "HANDOFF_SAFETY_BOUNDARY_LOST")

    if state["state"] == "LEGACY_CANDIDATE":
        phases = [record.phase for record in journal.load()]
        if "RUNTIME_PROFILE_EXCHANGE_INTENT" not in phases:
            journal.append("RUNTIME_PROFILE_EXCHANGE_INTENT", {
                "paper_runtime_profile_state": state,
                "exchange_method": "RENAME_EXCHANGE",
                "forward_only_after_exchange": True,
            })
        _reopen_inputs(activation, audit, producer)
        _mutation_seam("BEFORE_RUNTIME_PROFILE_EXCHANGE")
        executor.exchange_paper_runtime_profile_candidate()
        _mutation_seam("AFTER_RUNTIME_PROFILE_EXCHANGE")
        state = _validate_paper_runtime_profile_state(
            executor.paper_runtime_profile_hardening_state())
        _require(state["state"] == "HARDENED_CANDIDATE",
                 "HANDOFF_RUNTIME_PROFILE_EXCHANGE_FAILED")
    _append_paper_runtime_profile_prefix_for_state(journal, state)
    _validate_paper_runtime_profile_intent_rebound(journal, state)
    _reopen_inputs(activation, audit, producer)
    _require(_watch_complete(validate_snapshot(executor.snapshot())),
             "HANDOFF_SAFETY_BOUNDARY_LOST")

    if state["state"] == "HARDENED_CANDIDATE":
        _mutation_seam("BEFORE_RUNTIME_PROFILE_RETAIN_LEGACY")
        executor.seal_retained_legacy_paper_runtime_profile()
        _mutation_seam("AFTER_RUNTIME_PROFILE_RETAIN_LEGACY")
        state = _validate_paper_runtime_profile_state(
            executor.paper_runtime_profile_hardening_state())
        _require(state["state"] == "HARDENED",
                 "HANDOFF_RUNTIME_PROFILE_RETAIN_LEGACY_FAILED")
    _append_paper_runtime_profile_prefix_for_state(journal, state)
    _validate_paper_runtime_profile_intent_rebound(journal, state)
    _reopen_inputs(activation, audit, producer)
    _require(_watch_complete(validate_snapshot(executor.snapshot())),
             "HANDOFF_SAFETY_BOUNDARY_LOST")
    return _paper_runtime_profile_hardening_evidence(state, journal)


def _complete_after_reload(
    journal: Journal, executor: Any, activation: InputBinding,
    audit: InputBinding, producer: ProducerBinding,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    restoration = _perform_profile_restoration(
        journal, executor, activation, audit, producer)
    hardening = _perform_paper_runtime_profile_hardening(
        journal, executor, activation, audit, producer)
    final = validate_snapshot(executor.snapshot())
    _require(_watch_complete(final), "HANDOFF_FINAL_ATTESTATION_FAILED")
    _reopen_inputs(activation, audit, producer)
    profile_candidate_absent = executor.profile_candidate_absent()
    runtime_candidate_absent = (
        executor.paper_runtime_profile_candidate_absent())
    _require(profile_candidate_absent,
             "HANDOFF_PROFILE_CANDIDATE_ABSENCE_INVALID")
    _require(runtime_candidate_absent,
             "HANDOFF_RUNTIME_PROFILE_CANDIDATE_ABSENCE_INVALID")
    journal.append("FINAL_ATTESTATION", {
        "snapshot": final, "paper_profile_restoration": restoration,
        "paper_runtime_profile_hardening": hardening,
        "profile_candidate_absent": profile_candidate_absent,
        "paper_runtime_profile_candidate_absent": runtime_candidate_absent,
    })
    journal.append("COMMIT_INTENT", {
        "snapshot_sha256": digest_bytes(canonical_bytes(final)),
        "journal_sha256": journal.digest(),
        "paper_profile_restoration": restoration,
        "paper_runtime_profile_hardening": hardening,
    })
    _reopen_inputs(activation, audit, producer)
    profile_candidate_absent = executor.profile_candidate_absent()
    runtime_candidate_absent = (
        executor.paper_runtime_profile_candidate_absent())
    _require(profile_candidate_absent,
             "HANDOFF_PROFILE_CANDIDATE_ABSENCE_INVALID")
    _require(runtime_candidate_absent,
             "HANDOFF_RUNTIME_PROFILE_CANDIDATE_ABSENCE_INVALID")
    receipt = _publish_receipt(
        context, final, "WATCH_RETIRED_HANDOFF_COMPLETE",
        crash_recovery_verified=True, profile_restoration=restoration,
        profile_candidate_absent=profile_candidate_absent,
        paper_runtime_profile_hardening=hardening,
        paper_runtime_profile_candidate_absent=runtime_candidate_absent)
    journal.append("COMPLETED", {
        "receipt": {
            "path": context["output_path"],
            "file_sha256": digest_bytes(canonical_bytes(receipt)),
            "body_sha256": receipt["body_sha256"],
        }
    })
    return receipt


def _perform_success(
    journal: Journal, executor: Any, activation: InputBinding,
    audit: InputBinding, producer: ProducerBinding,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    preflight = validate_snapshot(executor.snapshot())
    _require(_safe_boundary(preflight), "HANDOFF_PREFLIGHT_FAILED")
    _reopen_inputs(activation, audit, producer)
    journal.append("PREPARED", {
        "context": dict(context), "preflight": preflight})
    for unit in WATCH_UNITS:
        _success_mutation(
            journal, executor, activation, audit, producer,
            operation="DISABLE", unit=unit)
        _success_mutation(
            journal, executor, activation, audit, producer,
            operation="PERSISTENT_MASK", unit=unit)
        _success_mutation(
            journal, executor, activation, audit, producer,
            operation="RUNTIME_MASK", unit=unit)
    journal.append("DAEMON_RELOAD_INTENT", {})
    _reopen_inputs(activation, audit, producer)
    _mutation_seam("BEFORE_DAEMON_RELOAD")
    executor.daemon_reload()
    _mutation_seam("AFTER_DAEMON_RELOAD")
    _reopen_inputs(activation, audit, producer)
    after_reload = validate_snapshot(executor.snapshot())
    _require(_safe_boundary(after_reload), "HANDOFF_SAFETY_BOUNDARY_LOST")
    journal.append("DAEMON_RELOAD_APPLIED", {"snapshot": after_reload})
    return _complete_after_reload(
        journal, executor, activation, audit, producer, context)


def _best_effort_fail_close(executor: Any) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    for unit in WATCH_UNITS:
        for name, method in (
            ("DISABLE", executor.disable_and_stop),
            ("PERSISTENT_MASK", executor.mask_persistent),
            ("RUNTIME_MASK", executor.mask_runtime),
        ):
            try:
                _mutation_seam("FAIL_CLOSE_BEFORE_" + name, unit)
                method(unit)
                _mutation_seam("FAIL_CLOSE_AFTER_" + name, unit)
            except HandoffError as error:
                errors.append(f"{name}:{unit}:{error.reason}")
    try:
        _mutation_seam("FAIL_CLOSE_BEFORE_DAEMON_RELOAD")
        executor.daemon_reload()
        _mutation_seam("FAIL_CLOSE_AFTER_DAEMON_RELOAD")
    except HandoffError as error:
        errors.append("DAEMON_RELOAD:" + error.reason)
    try:
        snapshot = validate_snapshot(executor.snapshot(tighten=True))
    except HandoffError as error:
        errors.append("ATTESTATION:" + error.reason)
        snapshot = _fallback_snapshot()
    return snapshot, errors


def _context_from_records(records: Sequence[JournalRecord]) -> dict[str, Any]:
    _require(bool(records) and records[0].phase == "PREPARED",
             "HANDOFF_CONTEXT_INVALID")
    evidence = records[0].document["evidence"]
    _require(isinstance(evidence, dict) and "context" in evidence,
             "HANDOFF_CONTEXT_INVALID")
    return validate_context(evidence["context"])


def _append_failure_phase_if_needed(
    journal: Journal, phase: str, evidence: Mapping[str, Any],
) -> None:
    phases = [record.phase for record in journal.load()]
    if phase in phases:
        return
    expected_index = FAILURE_PHASES.index(phase)
    present = [item for item in phases if item in FAILURE_PHASES]
    _require(present == list(FAILURE_PHASES[:expected_index]),
             "HANDOFF_FAILURE_JOURNAL_INVALID")
    journal.append(phase, evidence)


def _forward_close_profile_after_exchange(
    journal: Journal, executor: Any,
) -> dict[str, Any] | None:
    phases = [record.phase for record in journal.load()]
    if "PROFILE_RESTORE_INTENT" not in phases:
        return None
    state = _validate_profile_state(executor.profile_restoration_state())
    _validate_profile_intent_rebound(journal, state)
    if state["state"] == "PRE_CANDIDATE":
        executor.remove_preexchange_profile_candidate()
        state = _validate_profile_state(executor.profile_restoration_state())
        _require(state["state"] == "PRE",
                 "HANDOFF_PROFILE_CANDIDATE_CLEANUP_FAILED")
    if state["state"] == "PRE":
        return None
    _append_profile_prefix_for_state(journal, state)
    if state["state"] == "POST_CANDIDATE":
        executor.seal_retired_watch()
        state = _validate_profile_state(executor.profile_restoration_state())
        _require(state["state"] == "RESTORED",
                 "HANDOFF_PROFILE_FORWARD_CLOSE_FAILED")
        _append_profile_prefix_for_state(journal, state)
    _require(state["state"] == "RESTORED",
             "HANDOFF_PROFILE_FORWARD_CLOSE_FAILED")
    return _profile_restoration_evidence(state, journal)


def _forward_close_paper_runtime_profile_after_exchange(
    journal: Journal, executor: Any,
) -> dict[str, Any] | None:
    phases = [record.phase for record in journal.load()]
    if "RUNTIME_PROFILE_HARDEN_INTENT" not in phases:
        return None
    state = _validate_paper_runtime_profile_state(
        executor.paper_runtime_profile_hardening_state())
    _validate_paper_runtime_profile_intent_rebound(journal, state)
    if state["state"] == "LEGACY_CANDIDATE":
        executor.remove_preexchange_paper_runtime_profile_candidate()
        state = _validate_paper_runtime_profile_state(
            executor.paper_runtime_profile_hardening_state())
        _require(state["state"] == "LEGACY_BACKED_UP",
                 "HANDOFF_RUNTIME_PROFILE_CANDIDATE_CLEANUP_FAILED")
    if state["state"] in {"LEGACY", "LEGACY_BACKED_UP"}:
        return None
    _append_paper_runtime_profile_prefix_for_state(journal, state)
    if state["state"] == "HARDENED_CANDIDATE":
        executor.seal_retained_legacy_paper_runtime_profile()
        state = _validate_paper_runtime_profile_state(
            executor.paper_runtime_profile_hardening_state())
        _require(state["state"] == "HARDENED",
                 "HANDOFF_RUNTIME_PROFILE_FORWARD_CLOSE_FAILED")
        _append_paper_runtime_profile_prefix_for_state(journal, state)
    _require(state["state"] == "HARDENED",
             "HANDOFF_RUNTIME_PROFILE_FORWARD_CLOSE_FAILED")
    _validate_paper_runtime_profile_intent_rebound(journal, state)
    return _paper_runtime_profile_hardening_evidence(state, journal)


def _fail_close(
    journal: Journal, executor: Any, context: Mapping[str, Any], reason: str,
) -> dict[str, Any]:
    output = Path(context["output_path"])
    existing = _read_receipt_if_present(output)
    if existing is not None:
        _require(_receipt_matches_context(existing, context),
                 "HANDOFF_RECEIPT_CONTEXT_MISMATCH")
        return existing
    restoration = _forward_close_profile_after_exchange(journal, executor)
    hardening = _forward_close_paper_runtime_profile_after_exchange(
        journal, executor)
    _append_failure_phase_if_needed(
        journal, "FAILURE_INTENT", {"reason": reason})
    snapshot, errors = _best_effort_fail_close(executor)
    candidate_absent = executor.profile_candidate_absent()
    _require(candidate_absent, "HANDOFF_PROFILE_CANDIDATE_ABSENCE_INVALID")
    runtime_candidate_absent = (
        executor.paper_runtime_profile_candidate_absent())
    _require(runtime_candidate_absent,
             "HANDOFF_RUNTIME_PROFILE_CANDIDATE_ABSENCE_INVALID")
    _append_failure_phase_if_needed(journal, "FAIL_CLOSE_ATTESTED", {
        "reason": reason, "errors": errors, "snapshot": snapshot})
    _append_failure_phase_if_needed(journal, "FAILED_CLOSED", {
        "reason": reason, "cleanup_complete": _watch_complete(snapshot),
        "errors": errors,
    })
    _remove_staging(output)
    return _publish_receipt(
        context, snapshot, "FAILED_CLOSED",
        crash_recovery_verified=not errors and _watch_complete(snapshot),
        profile_restoration=restoration,
        profile_candidate_absent=candidate_absent,
        paper_runtime_profile_hardening=hardening,
        paper_runtime_profile_candidate_absent=runtime_candidate_absent)


def _assert_expected_lineage(
    source: str, campaign: str,
    expected_source: str, expected_campaign: str,
) -> None:
    _require(DIGEST.fullmatch(expected_source) is not None and
             source == expected_source, "HANDOFF_EXPECTED_SOURCE_MISMATCH")
    _require(TOKEN.fullmatch(expected_campaign) is not None and
             campaign == expected_campaign,
             "HANDOFF_EXPECTED_CAMPAIGN_MISMATCH")


def _select_production_executor(
    executor: Any | None, production_mode: str | None,
) -> tuple[ProductionExecutor, ProducerBinding]:
    _require(production_mode == PRODUCTION_MODE,
             "HANDOFF_EXPLICIT_PRODUCTION_INTENT_REQUIRED")
    _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
             "HANDOFF_ROOT_REQUIRED")
    if executor is not None:
        _require(type(executor) is ProductionExecutor,
                 "HANDOFF_PRODUCTION_EXECUTOR_REQUIRED")
        selected = executor
    else:
        selected = ProductionExecutor()
    producer = ProductionExecutor.attest_producer(selected)
    _require(producer.reference == {
        "path": str(INSTALLED_EXECUTABLE),
        "file_sha256": digest_bytes(producer.payload),
    }, "HANDOFF_EXECUTING_IMAGE_DRIFT")
    return selected, producer


def handoff(
    activation_receipt: Path, p1_audit_receipt: Path, output: Path,
    *, expected_source_baseline_sha256: str, expected_campaign_id: str,
    production_mode: str | None = None, executor: Any | None = None,
) -> dict[str, Any]:
    selected, producer = _select_production_executor(
        executor, production_mode)
    activation, audit, source, campaign = bind_inputs(
        activation_receipt, p1_audit_receipt)
    _assert_expected_lineage(
        source, campaign, expected_source_baseline_sha256,
        expected_campaign_id)
    context = _make_context(
        activation, audit, producer, source, campaign, output)
    initial_preflight = validate_snapshot(selected.snapshot())
    _require(_safe_boundary(initial_preflight), "HANDOFF_PREFLIGHT_FAILED")
    initial_profile = _validate_profile_state(
        selected.profile_restoration_state())
    _require(initial_profile["state"] == "PRE",
             "HANDOFF_PROFILE_PREFLIGHT_FAILED")
    initial_runtime_profile = _validate_paper_runtime_profile_state(
        selected.paper_runtime_profile_hardening_state())
    _require(initial_runtime_profile["state"] == "LEGACY",
             "HANDOFF_RUNTIME_PROFILE_PREFLIGHT_FAILED")
    _reopen_inputs(activation, audit, producer)
    prepare_state_directories()
    lock = acquire_lock()
    try:
        journal = Journal()
        _require(not journal.load(), "HANDOFF_TRANSACTION_ALREADY_EXISTS")
        _require(_read_receipt_if_present(Path(context["output_path"])) is None,
                 "HANDOFF_RECEIPT_ALREADY_EXISTS")
        try:
            return _perform_success(
                journal, selected, activation, audit, producer, context)
        except HandoffError as error:
            if not journal.load():
                raise
            return _fail_close(journal, selected, context, error.reason)
    finally:
        release_lock(lock)


def _context_matches_arguments(
    context: Mapping[str, Any], activation_receipt: Path | None,
    p1_audit_receipt: Path | None, output: Path | None,
    expected_source: str | None, expected_campaign: str | None,
) -> None:
    if activation_receipt is not None:
        _require(str(_canonical_path(
            activation_receipt, "HANDOFF_CONTEXT_ARGUMENT_MISMATCH")) ==
            context["activation_receipt"]["path"],
            "HANDOFF_CONTEXT_ARGUMENT_MISMATCH")
    if p1_audit_receipt is not None:
        _require(str(_canonical_path(
            p1_audit_receipt, "HANDOFF_CONTEXT_ARGUMENT_MISMATCH")) ==
            context["p1_audit_receipt"]["path"],
            "HANDOFF_CONTEXT_ARGUMENT_MISMATCH")
    if output is not None:
        _require(str(_canonical_path(
            output, "HANDOFF_CONTEXT_ARGUMENT_MISMATCH")) ==
            context["output_path"], "HANDOFF_CONTEXT_ARGUMENT_MISMATCH")
    if expected_source is not None:
        _require(expected_source == context["source_baseline_sha256"],
                 "HANDOFF_CONTEXT_ARGUMENT_MISMATCH")
    if expected_campaign is not None:
        _require(expected_campaign == context["campaign_id"],
                 "HANDOFF_CONTEXT_ARGUMENT_MISMATCH")


def _rebind_context_inputs(
    context: Mapping[str, Any], producer: ProducerBinding,
) -> tuple[InputBinding, InputBinding]:
    activation, audit, source, campaign = bind_inputs(
        Path(context["activation_receipt"]["path"]),
        Path(context["p1_audit_receipt"]["path"]))
    _require(
        activation.reference == context["activation_receipt"] and
        audit.reference == context["p1_audit_receipt"] and
        source == context["source_baseline_sha256"] and
        campaign == context["campaign_id"] and
        producer.reference == context["producer"],
        "HANDOFF_CONTEXT_REBOUND")
    _reopen_inputs(activation, audit, producer)
    return activation, audit


def reconcile(
    *, activation_receipt: Path | None = None,
    p1_audit_receipt: Path | None = None, output: Path | None = None,
    expected_source_baseline_sha256: str | None = None,
    expected_campaign_id: str | None = None,
    production_mode: str | None = None, executor: Any | None = None,
) -> str:
    selected, producer = _select_production_executor(
        executor, production_mode)
    if not _path_exists_nofollow(JOURNAL_ROOT):
        return "NO_TRANSACTION"
    lock = acquire_lock()
    try:
        journal = Journal()
        records = journal.load()
        if not records:
            return "NO_TRANSACTION"
        context = _context_from_records(records)
        _require(
            context["producer"] == producer.reference and
            context["production_mode"] == PRODUCTION_MODE,
            "HANDOFF_EXECUTING_IMAGE_CONTEXT_MISMATCH")
        producer.reopen()
        _context_matches_arguments(
            context, activation_receipt, p1_audit_receipt, output,
            expected_source_baseline_sha256, expected_campaign_id)
        output_path = Path(context["output_path"])
        existing = _read_receipt_if_present(output_path)
        phases = [record.phase for record in records]
        if existing is not None:
            _require(_receipt_matches_context(existing, context),
                     "HANDOFF_RECEIPT_CONTEXT_MISMATCH")
            if existing["status"] == "FAILED_CLOSED":
                _require(any(phase in FAILURE_PHASES for phase in phases),
                         "HANDOFF_FAILED_RECEIPT_WITHOUT_FAILURE_JOURNAL")
                return "FAILED_CLOSED"
            _require(phases[-1] in {"COMMIT_INTENT", "COMPLETED"},
                     "HANDOFF_COMPLETE_RECEIPT_WITHOUT_COMMIT")
            snapshot = validate_snapshot(selected.snapshot())
            _require(_watch_complete(snapshot),
                     "HANDOFF_COMPLETE_RUNTIME_DRIFT")
            state = _validate_profile_state(
                selected.profile_restoration_state())
            runtime_state = _validate_paper_runtime_profile_state(
                selected.paper_runtime_profile_hardening_state())
            _require(
                state["state"] == "RESTORED" and
                selected.profile_candidate_absent() is True and
                existing["paper_profile_restored"] is True and
                _profile_restoration_evidence(state, journal) ==
                    existing["paper_profile_restoration"] and
                runtime_state["state"] == "HARDENED" and
                selected.paper_runtime_profile_candidate_absent() is True and
                existing["paper_runtime_profile_hardened"] is True and
                _paper_runtime_profile_hardening_evidence(
                    runtime_state, journal) ==
                    existing["paper_runtime_profile_hardening"],
                "HANDOFF_COMPLETE_PROFILE_DRIFT")
            if phases[-1] == "COMMIT_INTENT":
                journal.append("COMPLETED", {
                    "receipt": {
                        "path": context["output_path"],
                        "file_sha256": digest_bytes(
                            canonical_bytes(existing)),
                        "body_sha256": existing["body_sha256"],
                    },
                    "recovered_after_publish": True,
                })
            return "WATCH_RETIRED_HANDOFF_COMPLETE"
        if (
            "DAEMON_RELOAD_APPLIED" in phases and
            not any(phase in FAILURE_PHASES for phase in phases)
        ):
            activation, audit = _rebind_context_inputs(context, producer)
            try:
                completed = _complete_after_reload(
                    journal, selected, activation, audit, producer, context)
            except HandoffError as error:
                failed = _fail_close(
                    journal, selected, context, error.reason)
                _require(failed["status"] == "FAILED_CLOSED",
                         "HANDOFF_FAILURE_PUBLICATION_INVALID")
                return "FAILED_CLOSED"
            _require(completed["status"] ==
                     "WATCH_RETIRED_HANDOFF_COMPLETE",
                     "HANDOFF_COMPLETION_PUBLICATION_INVALID")
            return "WATCH_RETIRED_HANDOFF_COMPLETE"
        failed = _fail_close(
            journal, selected, context, "HANDOFF_INCOMPLETE_TRANSACTION")
        _require(failed["status"] == "FAILED_CLOSED",
                 "HANDOFF_FAILURE_PUBLICATION_INVALID")
        return "FAILED_CLOSED"
    finally:
        release_lock(lock)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Retire fixed round114 alpha WATCH surfaces without authorizing "
            "PAPER"))
    parser.add_argument("action", choices=("handoff", "reconcile"))
    parser.add_argument(
        "--activation-receipt", type=Path,
        default=DEFAULT_ACTIVATION_RECEIPT)
    parser.add_argument(
        "--p1-audit-receipt", type=Path,
        default=DEFAULT_P1_AUDIT_RECEIPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_RECEIPT)
    parser.add_argument("--expected-source-baseline-sha256")
    parser.add_argument("--expected-campaign-id")
    parser.add_argument("--run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _require(arguments.run, "HANDOFF_EXPLICIT_PRODUCTION_INTENT_REQUIRED")
        if arguments.action == "handoff":
            _require(arguments.expected_source_baseline_sha256 is not None,
                     "HANDOFF_EXPECTED_SOURCE_REQUIRED")
            _require(arguments.expected_campaign_id is not None,
                     "HANDOFF_EXPECTED_CAMPAIGN_REQUIRED")
            receipt = handoff(
                arguments.activation_receipt, arguments.p1_audit_receipt,
                arguments.output,
                expected_source_baseline_sha256=
                    arguments.expected_source_baseline_sha256,
                expected_campaign_id=arguments.expected_campaign_id,
                production_mode=PRODUCTION_MODE)
            sys.stdout.buffer.write(canonical_bytes(receipt))
            return 0 if receipt["status"] == \
                "WATCH_RETIRED_HANDOFF_COMPLETE" else 1
        status = reconcile(
            activation_receipt=arguments.activation_receipt,
            p1_audit_receipt=arguments.p1_audit_receipt,
            output=arguments.output,
            expected_source_baseline_sha256=
                arguments.expected_source_baseline_sha256,
            expected_campaign_id=arguments.expected_campaign_id,
            production_mode=PRODUCTION_MODE)
        sys.stdout.write(status + "\n")
        return 0 if status in {
            "NO_TRANSACTION", "WATCH_RETIRED_HANDOFF_COMPLETE"} else 1
    except HandoffError as error:
        sys.stderr.write(error.reason + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
