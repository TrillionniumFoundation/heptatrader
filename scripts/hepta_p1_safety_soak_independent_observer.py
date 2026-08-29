#!/usr/bin/env python3
"""Produce independent, read-only P1 safety-soak observations.

This root-only helper observes a fixed SHADOW/WATCH boundary.  It can inspect
allowlisted systemd properties, fixed files and ``/proc`` identities, and run
the broker policy's read-only ``--check-deny-all`` action.  It cannot start,
stop, restart, enable or otherwise mutate a service; it never reads session
token contents, broker credentials, account state, orders, or positions.

Fault observations consume a separately produced, root-owned injection
receipt.  This program verifies the declared pre/post identity and independently
re-opens the post state.  It never performs a fault injection itself.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from datetime import date, datetime, timezone
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


VERSION = 1
ROOT_UID = 0
ROOT_GID = 0
MAXIMUM_INPUT_BYTES = 16 * 1024 * 1024
MAXIMUM_OUTPUT_BYTES = 16 * 1024 * 1024
MAXIMUM_COMMAND_BYTES = 1024 * 1024
MAXIMUM_OBSERVATION_AGE_MS = 5 * 60 * 1000
MAXIMUM_CLOCK_SKEW_MS = 30 * 1000
MAXIMUM_BOOTTIME_SKEW_NS = 30 * 1_000_000_000
MAXIMUM_FAULT_INJECTION_LATENESS_NS = 30 * 1_000_000_000
MAXIMUM_FAULT_RECOVERY_NS = 5 * 60 * 1_000_000_000
LAUNCHER_WARMUP_MS = 210 * 60 * 1000
LAUNCHER_EARLY_START_LEAD_MS = 20 * 60 * 1000
POLICY_SLOT_INTERVAL_MS = 2 * 60 * 1000
POLICY_MAXIMUM_ITERATIONS = 241
MINIMUM_TRADING_DAYS = 10
MAXIMUM_TRADING_DAYS = 20
MINIMUM_ELIGIBLE_DECISIONS = 200
MINIMUM_COMPLETE_PPM = 990_001
MINIMUM_BOOTTIME_DURATION_NS = 72 * 60 * 60 * 1_000_000_000
MAXIMUM_CHECKPOINT_GAP_NS = 15 * 60 * 1_000_000_000
MAXIMUM_DECISION_LATENESS_MS = 15 * 60 * 1000
TRADING_TIMEZONE = "America/New_York"
MINIMUM_CLOCK_STEP_MS = 100
MAXIMUM_CLOCK_STEP_MS = 60 * 1000
OBSERVATION_LIFETIME_MS = 4 * 60 * 1000
INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-independent-observer")
PRODUCTION_MODE = "PRODUCTION_ROOT_OBSERVER"
REHEARSAL_MODE = "INJECTED_REHEARSAL"

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}")
UNIT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,191}")
INVOCATION_ID = re.compile(r"[0-9a-f]{32}")
EXPORT_GENERATION = re.compile(
    r"generation-([0-9]{20})-([A-Za-z0-9_-]{8,64})")

NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW | CLOEXEC
READ_FLAGS = os.O_RDONLY | NOFOLLOW | CLOEXEC | NONBLOCK
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC
RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)

SERVICE_SCHEMA = "hepta.p1-safety-soak-independent-service-observation.v1"
CAMPAIGN_CONTINUITY_SCHEMA = (
    "hepta.p1-safety-soak-independent-campaign-continuity-observation.v1")
FAULT_SCHEMA = "hepta.p1-safety-soak-independent-fault-observation.v1"
AUTHORITY_SCHEMA = (
    "hepta.p1-safety-soak-independent-authority-observation.v1")
CLEANUP_SCHEMA = "hepta.p1-safety-soak-independent-cleanup-observation.v1"
EVIDENCE_SCHEMA = (
    "hepta.p1-safety-soak-independent-observation-evidence.v1")
INJECTION_SCHEMA = (
    "hepta.p1-safety-soak-root-fault-injection-receipt.v1")
FAULT_IDENTITY_SCHEMA = "hepta.p1-safety-soak-fault-target-identity.v1"
SPEC_SCHEMA = "hepta.p1-safety-soak-campaign-spec.v1"
FAULT_PLAN_SCHEMA = "hepta.p1-safety-soak-fault-plan.v1"
CAMPAIGN_RUNTIME_SCHEMA = "hepta.p1-safety-soak-campaign-runtime.v1"

BOUNDARY_FIELDS = (
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access",
)
PERMISSION_FIELDS = frozenset({
    *BOUNDARY_FIELDS, "mutation_attempted", "order_submission_authorized",
})

FORMAL_CAMPAIGN_FIELDS = frozenset({
    "campaign_id", "campaign_sha256", "policy_body_sha256",
    "policy_file_sha256",
})
SPEC_FIELDS = frozenset({
    "schema", "version", "campaign_id", "domain_id",
    "source_manifest_sha256", "policy_sha256", "strategy_id",
    "strategy_version", "strategy_sha256", "formal_campaigns",
    "declared_trading_days", "trading_timezone",
    "trading_calendar_sha256", "eligible_scheduled_at_ms",
    "scheduled_decision_count", "minimum_eligible_decisions",
    "minimum_complete_ppm", "minimum_boottime_duration_ns",
    "maximum_checkpoint_gap_ns", "maximum_decision_lateness_ms",
    "fault_plan_body_sha256", "independent_auditor_id", "frozen_at_ms",
    "freeze_bundle",
    *BOUNDARY_FIELDS, "body_sha256",
})
PLANNED_FAULT_FIELDS = frozenset({
    "fault_id", "fault_type", "target_id", "formal_campaign_id",
    "inject_at_boottime_ns",
    "maximum_injection_lateness_ns", "maximum_recovery_ns",
})
FAULT_PLAN_FIELDS = frozenset({
    "schema", "version", "campaign_id", "source_manifest_sha256",
    "policy_sha256", "strategy_sha256", "planned_faults",
    *BOUNDARY_FIELDS, "body_sha256",
})

COMMON_OBSERVATION_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "campaign_id", "observer_id", "observation_complete", "clock_id",
    "boot_id", "source_manifest_sha256", "policy_sha256",
    "strategy_sha256", "producer", "production_mode",
    "observation_evidence", *BOUNDARY_FIELDS,
    "body_sha256",
})
PRODUCER_FIELDS = frozenset({"path", "file_sha256"})
FREEZE_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256",
})
RUNTIME_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "schema",
})
RUNTIME_EXECUTABLE_FIELDS = frozenset({"path", "file_sha256"})
FORMAL_RUNTIME_FIELDS = frozenset({
    "formal_campaign_id", "probe_campaign_id", "launcher_start_ms",
    "launcher_dispatch_at_ms", "valid_after_ms", "slot_interval_ms",
    "maximum_iterations", "expires_at_ms",
    "launcher_completion_deadline_ms", "projection_deadline_ms",
    "teardown_deadline_ms", "policy", "launcher_receipt_path",
    "verified_closure_path", "artifact_root",
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
SERVICE_FIELDS = COMMON_OBSERVATION_FIELDS | frozenset({
    "observed_boottime_ns", "service_epoch", "fencing_generation",
    "lease_generation", "transition_fault_id", "continuity_ok",
    "audit_ok", "cleanup_ok",
})
CAMPAIGN_CONTINUITY_FIELDS = COMMON_OBSERVATION_FIELDS | frozenset({
    "observed_boottime_ns", "freeze_bundle", "campaign_runtime",
    "continuity_slot_index", "continuity_scheduled_at_ms",
    "continuity_origin_ms", "continuity_end_ms", "continuity_cadence_ms",
    "continuity_final_slot", "continuity_is_final", "catch_up",
    "activation_receipt",
    "activation_receipt_document", "export_commit",
    "export_commit_document", "export_snapshot", "lease_receipt",
    "lease_receipt_document", "export_receipt", "lease_generation",
    "previous_lease_generation",
    "previous_lease_receipt_body_sha256", "gateway_identity",
    "gateway_process_identity", "gateway_executable_identity",
    "gateway_profile_identity", "gateway_domain_config_identity",
    "supervisor_socket_identity",
    "custodian_identity", "collector_timer_identity",
    "activation_reconcile_timer_identity", "tool_socket_identity",
    "transition_fault_id", "persistent_stack_ok", "lease_chain_ok", "connector_count",
    "authorized_uids", "paper_unit_active_count",
    "campaign_socket_present", "kill_switch_engaged", "zero_exposure",
})
FAULT_FIELDS = COMMON_OBSERVATION_FIELDS | frozenset({
    "fault_id", "fault_type", "target_id", "injection_boottime_ns",
    "recovered_boottime_ns", "recovery_verified", "cleanup_verified",
    "authority_failure", "audit_failure", "cleanup_failure",
})
AUTHORITY_FIELDS = COMMON_OBSERVATION_FIELDS | frozenset({
    "observed_boottime_ns", "connector_count", "authorized_uids",
    "paper_unit_active_count", "campaign_socket_present",
    "kill_switch_engaged", "local_boundary_safe",
    "local_boundary_uncertain", "observation_scope",
    "authoritative_account_state_observed",
})
CLEANUP_FIELDS = COMMON_OBSERVATION_FIELDS | frozenset({
    "observed_boottime_ns", "subject_type", "subject_id",
    "watch_authority_count", "export_residue_count",
    "session_authority_count", "paper_unit_active_count",
    "campaign_socket_present", "cleanup_complete", "cleanup_uncertain",
    "errors",
})

UNIT_FIELDS = frozenset({
    "unit", "load_state", "active_state", "sub_state", "unit_file_state",
    "main_pid", "invocation_id",
    "exec_main_start_timestamp_monotonic_us", "n_restarts",
    "state_sha256",
})
PROCESS_FIELDS = frozenset({
    "pid", "uid", "gid", "starttime_ticks", "exe_device", "exe_inode",
    "cgroup_sha256", "state_sha256",
})
PATH_FIELDS = frozenset({
    "path", "present", "parent_device", "parent_inode", "parent_uid",
    "parent_gid", "parent_mode", "parent_nlink", "file_type", "device",
    "inode", "uid", "gid", "mode", "nlink", "size", "mtime_ns",
    "ctime_ns", "content_file_sha256", "content_body_sha256",
    "state_sha256",
})
BROKER_FIELDS = frozenset({
    "helper_path", "helper_file_sha256", "policy_sha256",
    "authorized_connector_count", "authorized_uids",
    "protected_port_count", "deny_all", "checked_boottime_ns",
    "state_sha256",
})
ARTIFACT_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "schema",
})
EVIDENCE_FIELDS = frozenset({
    "schema", "version", "kind", "boot_id", "observed_boottime_ns",
    "systemd_units", "processes", "paths", "broker_deny_all",
    "fault_injection_receipt", "body_sha256",
})
FAULT_IDENTITY_FIELDS = frozenset({
    "schema", "version", "phase", "target_id", "boot_id",
    "observed_boottime_ns", "service_epoch", "fencing_generation",
    "lease_generation", "systemd_units", "processes", "paths",
    "broker_deny_all", "residue_count", "wall_clock_delta_ms",
    "fixture_generation", "fixture_expires_boottime_ns", "fixture_valid",
    "body_sha256",
})
INJECTION_FIELDS = frozenset({
    "schema", "version", "status", "issued_at_ms", "expires_at_ms",
    "campaign_id", "source_manifest_sha256", "policy_sha256",
    "strategy_sha256", "fault_id", "fault_type", "target_id", "clock_id",
    "boot_id", "planned_injection_boottime_ns",
    "actual_injection_boottime_ns", "recovered_boottime_ns",
    "maximum_recovery_ns", "injector_id", "injector_uid", "injector_gid",
    "injection_scope", "action_receipt_sha256", "pre_identity",
    "post_identity", "injection_performed", "recovery_complete",
    "cleanup_complete", "authority_failure", "audit_failure",
    "cleanup_failure", "producer", "production_mode", "pins_reference",
    "journal_predecessor_sequence", "journal_predecessor_body_sha256",
    *BOUNDARY_FIELDS, "body_sha256",
})

ALLOWED_FAULT_TYPES = frozenset({
    "PROCESS_KILL", "SERVICE_RESTART", "TOKEN_LOSS", "LEASE_EXPIRY",
    "NETWORK_DENY_RELOAD", "EVIDENCE_WRITER_CRASH", "CLOCK_STEP",
})
FAULT_TARGET_IDS = {
    "PROCESS_KILL": "p1-independent-observer-process",
    "SERVICE_RESTART": "watch-execution-gateway",
    "TOKEN_LOSS": "fault-fixture-watch-session-token",
    "LEASE_EXPIRY": "fault-fixture-watch-lease",
    "NETWORK_DENY_RELOAD": "broker-egress-deny-policy",
    "EVIDENCE_WRITER_CRASH": "p1-safety-soak-evidence-recorder",
    "CLOCK_STEP": "wall-clock-discontinuity-detector",
}

SYSTEMCTL = "/usr/bin/systemctl"
BROKER_HELPER = Path("/usr/libexec/hepta-broker-egress-policy")
TOKEN_FAULT_FIXTURE = Path(
    "/run/hepta-p1-fault-fixture/watch-session-token.json")
LEASE_FAULT_FIXTURE = Path(
    "/run/hepta-p1-fault-fixture/watch-lease.json")
BROKER_PASS = re.compile(
    rb"\Ahepta_broker_egress_policy: PASS "
    rb"policy_sha256=(sha256:[0-9a-f]{64}) "
    rb"authorized_connectors=([0-9]+) "
    rb"authorized_uids=([0-9,]*) protected_ports=([0-9]+)\n\Z")

GATEWAY_UNIT = "hepta-tool-gateway@alpha.service"
GATEWAY_EXECUTABLE = Path("/usr/libexec/hepta-tool-gatewayd")
GATEWAY_PROFILE = Path("/etc/heptatrader/trust-domains/alpha.env")
GATEWAY_DOMAIN_CONFIG = Path("/etc/heptatrader/trust-domains/alpha.json")
GATEWAY_TOOL_SOCKET = Path("/run/hepta-agent-alpha/tools.sock")
GATEWAY_SUPERVISOR_SOCKET = Path(
    "/run/hepta-tool-gateway-alpha/session-supervisor.sock")
BROKER_UNIT = "hepta-broker-egress-policy.service"
WATCH_SERVICE_UNITS = (
    GATEWAY_UNIT,
    "hepta-shadow-watch-custodian@alpha.service",
    "hepta-shadow-watch-collector@alpha.timer",
)
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
    GATEWAY_UNIT,
    "hepta-tool-gateway@alpha.socket",
    "hepta-tool-session-supervisor@alpha.socket",
    "hepta-execution-simulator@alpha.service",
    "hepta-execution-simulator@alpha.socket",
    "hepta-execution-events-simulator@alpha.socket",
)
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

FORMAL_MARKER_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "policy_path",
    "policy_file_sha256", "policy_body_sha256", "admission_receipt_path",
    "admission_receipt_file_sha256", "admission_receipt_body_sha256",
    "admitted_at_ms", "marker_created_at_ms", "expires_at_ms",
    "execution_service_epoch", "execution_service_fencing_generation",
    "environment", *BOUNDARY_FIELDS, "body_sha256",
})
MARKER_ENVIRONMENT_FIELDS = frozenset({
    "boot_id", "audit_journal_device", "audit_journal_inode",
    "collector_sha256", "exporter_sha256", "heptactl_sha256",
    "gateway_sha256", "custodian_sha256", "observer_sha256",
    "host_controller_sha256", "domain_config_sha256",
    "gateway_profile_sha256", "gateway_process_profile_sha256",
    "gateway_invocation_id", "gateway_main_pid",
    "gateway_exec_main_start_timestamp_monotonic_us",
    "gateway_socket_device", "gateway_socket_inode",
})
CONTROLLER_STATUS_FIELDS = frozenset({
    "schema", "version", "campaign_id", "controller_pid",
    "controller_uid", "controller_gid", "state", "started_at_ms",
    "updated_at_ms", "observer_invocations",
    "last_export_receipt_body_sha256", "last_snapshot_body_sha256",
    "last_lease_generation", "locked_execution_service_epoch",
    "locked_execution_service_fencing_generation", "observer_status",
    "observer_outcome", "completed_iterations", "reason",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
LEASE_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_id", "agent_uid", "boundary",
    "operation", "lease_generation", "previous_lease_generation",
    "previous_receipt_body_sha256", "accepted", "reason_code",
    "accepted_at_ms", "ttl_seconds", "expires_at_ms", "paper_authorized",
    "live_authorized", "mutation_authorized", "body_sha256",
})
EXPORT_SNAPSHOT_V1_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_uid", "generated_at_ms",
    "instrument", "catalog_sha256", "descriptor_sha256", "reads",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
EXPORT_SNAPSHOT_V2_FIELDS = EXPORT_SNAPSHOT_V1_FIELDS | frozenset({
    "collection_started_at_ms", "collection_finished_at_ms",
    "read_finished_at_ms",
})
EXPORT_RECEIPT_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_uid", "reader_uid",
    "reader_gid", "boundary", "lease_generation",
    "lease_receipt_body_sha256", "lease_receipt_file_sha256",
    "snapshot_body_sha256", "snapshot_file_sha256",
    "snapshot_generated_at_ms", "exported_at_ms", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "body_sha256",
})
EXPORT_COMMIT_FIELDS = frozenset({
    "schema", "version", "authority_status", "authority_changed_at_ms",
    "close_reason", "commit_sequence", "generation", "domain_id",
    "agent_uid", "reader_uid", "reader_gid", "lease_generation",
    "snapshot_body_sha256", "snapshot_file_sha256",
    "lease_receipt_body_sha256", "lease_receipt_file_sha256",
    "export_receipt_body_sha256", "export_receipt_file_sha256",
    "committed_at_ms", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "body_sha256",
})
EXPORT_FILES = (
    "snapshot.json",
    "shadow-watch-lease-receipt.json",
    "shadow-watch-export-receipt.json",
)
EXPORT_COMMIT_NAME = "current.json"
EXPORT_GENERATIONS_NAME = "generations"
ACTIVATION_RECEIPT_FIELDS = frozenset({
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
SHADOW_INSTALL_LOCK_FIELDS = frozenset({
    "path", "device", "inode", "nlink", "uid", "gid", "mode", "size",
    "mtime_ns", "ctime_ns", "created_during_transaction", "persistent",
    "held_during_transaction",
})
SHADOW_INSTALL_RECEIPT_PATH = (
    "/var/lib/hepta/shadow-runtime-install-receipts/"
    "hepta-p1-round114-generation22-passive.json")
SHADOW_INSTALL_MANIFEST_PATH = (
    "/var/lib/hepta/shadow-runtime-install-artifacts/"
    "hepta-p1-round114-generation22-shadow-runtime.manifest.json")
SHADOW_INSTALL_BACKUP_ROOT = (
    "/var/lib/hepta/shadow-runtime-backups/hepta-p1-round114-generation22-passive")
SHADOW_INSTALL_LOCK_PATH = "/var/lib/hepta/.shadow-runtime-install.lock"
SHADOW_CURRENT_INSTALL_POINTER_PATH = (
    "/var/lib/hepta/shadow-runtime-install-state/current-install-v1.json")
SHADOW_DEFAULT_DENY_IDENTITY_SHA256 = (
    "sha256:4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435")
SHADOW_PREDECESSOR_POINTER_SHA256 = (
    "sha256:2beeb507fcafbbfc2c93d2e4756fddf0b27e9872733ff97d28af47006461d406")
ACTIVATION_GATEWAY_AFTER_FIELDS = frozenset({
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


class ObserverError(RuntimeError):
    """Stable fail-closed observer error."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ClockSample:
    wall_ms: int
    boottime_ns: int
    boot_id: str


@dataclass(frozen=True)
class Snapshot:
    path: Path
    payload: bytes
    metadata: os.stat_result
    document: dict[str, Any]
    file_sha256: str
    body_sha256: str


@dataclass(frozen=True)
class ProducerBinding:
    path: Path
    payload: bytes
    metadata: os.stat_result

    @property
    def reference(self) -> dict[str, str]:
        return {"path": str(self.path), "file_sha256": digest_bytes(self.payload)}

    def reopen(self) -> None:
        payload, metadata = secure_read(
            self.path, expected_uid=ROOT_UID, expected_gid=ROOT_GID,
            modes=frozenset({0o755}))
        _require(payload == self.payload and
                 _file_identity(metadata) == _file_identity(self.metadata),
                 "P1_OBSERVER_EXECUTING_IMAGE_DRIFT")


@dataclass(frozen=True)
class ObservedDocument:
    path: Path
    document: dict[str, Any]
    file_sha256: str
    body_sha256: str
    path_identity: dict[str, Any]


@dataclass(frozen=True)
class CommittedExport:
    commit: ObservedDocument
    snapshot: ObservedDocument
    lease: ObservedDocument
    receipt: ObservedDocument
    commit_sequence: int
    generation: str


@dataclass(frozen=True)
class Layout:
    state_base: Path = Path("/var/lib/hepta/p1-admission")
    export_root: Path = Path("/run/hepta-shadow-watch-export-alpha")
    sessions_root: Path = Path("/run/hepta-agent-alpha/sessions")
    watch_private: Path = Path("/var/lib/hepta-shadow-watch-alpha/private")
    custodian_transaction: Path = Path(
        "/var/lib/hepta-shadow-watch-custodian/alpha/transaction.json")
    kill_switch: Path = Path(
        "/run/hepta/ib-paper-control-alpha/kill-switch")
    campaign_socket: Path = Path("/run/hepta-agent-alpha/campaign.sock")
    activation_receipt: Path = Path(
        "/var/lib/hepta/shadow-observation/"
        "p1-watch-activation-round114-receipt-v4.json")

    def controller_status(self, campaign_id: str) -> Path:
        return self.state_base / "readers" / campaign_id / \
            "controller-status.json"

    def formal_marker(self, round_number: int) -> Path:
        return self.state_base / "public" / f"round{round_number}" / \
            "formal-authority-marker.json"

    @property
    def export_commit(self) -> Path:
        return self.export_root / EXPORT_COMMIT_NAME

    @property
    def export_generations(self) -> Path:
        return self.export_root / EXPORT_GENERATIONS_NAME

    @property
    def token(self) -> Path:
        return self.sessions_root / "session.token"

    @property
    def fence(self) -> Path:
        return self.sessions_root / ".session-fence.token"

    @property
    def session_lease(self) -> Path:
        return self.sessions_root / "shadow-watch-lease-receipt.json"


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ObserverError("P1_OBSERVER_CANONICALIZATION_FAILED") from error


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def seal(body: Mapping[str, Any]) -> dict[str, Any]:
    document = dict(body)
    document["body_sha256"] = digest_bytes(canonical_bytes(document))
    return document


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ObserverError(reason)


def _exact(value: Any, fields: frozenset[str], reason: str) \
        -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields, reason)
    return value


def _integer(value: Any, reason: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, reason)
    return value


def _signed_integer(value: Any, reason: str) -> int:
    _require(type(value) is int, reason)
    return value


def _digest(value: Any, reason: str) -> str:
    _require(type(value) is str and DIGEST.fullmatch(value) is not None,
             reason)
    return value


def _validate_activation_predecessor_lineage(
    success_value: Any, failure_value: Any, reason: str,
) -> None:
    """Bind v4 to exact Round95 receipts, which commit the Round86 ancestor."""
    success = _exact(success_value, PREDECESSOR_ACTIVATION_SUCCESS_FIELDS,
                     reason)
    failure = _exact(failure_value, PREDECESSOR_ACTIVATION_FAILURE_FIELDS,
                     reason)
    _require(
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
        success.get("receipt_domain") == "alpha" and
        all(type(success.get(field)) is int for field in (
            "receipt_device", "receipt_inode", "receipt_mode",
            "receipt_nlink", "receipt_uid", "receipt_gid", "receipt_bytes",
            "receipt_mtime_ns", "receipt_ctime_ns")) and
        success["receipt_device"] >= 0 and success["receipt_inode"] > 0 and
        stat.S_ISREG(success["receipt_mode"]) and
        stat.S_IMODE(success["receipt_mode"]) == 0o600 and
        success["receipt_nlink"] == 1 and success["receipt_uid"] == 0 and
        success["receipt_gid"] == 0 and
        0 < success["receipt_bytes"] <= MAXIMUM_INPUT_BYTES and
        success["receipt_mtime_ns"] >= 0 and success["receipt_ctime_ns"] >= 0,
        reason)
    _require(
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
        failure.get("receipt_domain") == "alpha" and
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
        0 < failure["receipt_bytes"] <= MAXIMUM_INPUT_BYTES and
        failure["receipt_mtime_ns"] >= 0 and failure["receipt_ctime_ns"] >= 0 and
        failure.get("journal_path") ==
            PREDECESSOR_ACTIVATION_FAILURE_JOURNAL_PATH and
        failure.get("journal_sha256") ==
            PREDECESSOR_ACTIVATION_FAILURE_JOURNAL_SHA256 and
        failure["journal_record_count"] == 21 and
        failure.get("journal_terminal_phase") == "FAILED_CLOSED", reason)


def _validate_activation_install_lineage(
    value: Any, expected_source_manifest_sha256: str, reason: str,
) -> dict[str, Any]:
    evidence = _exact(value, SHADOW_INSTALL_EVIDENCE_FIELDS, reason)
    _require(
        evidence.get("schema") ==
            "hepta.shadow-runtime-install-consumption-evidence.v3" and
        evidence.get("version") == 3 and
        evidence.get("receipt_path") == SHADOW_INSTALL_RECEIPT_PATH and
        evidence.get("manifest_path") == SHADOW_INSTALL_MANIFEST_PATH and
        evidence.get("backup_root") == SHADOW_INSTALL_BACKUP_ROOT and
        evidence.get("current_install_pointer_path") ==
            SHADOW_CURRENT_INSTALL_POINTER_PATH and
        evidence.get("domain") == "alpha" and
        evidence.get("installed_file_count") == 128 and
        evidence.get("install_generation") == 22 and
        evidence.get("predecessor_install_generation") == 21 and
        evidence.get("predecessor_current_install_pointer_file_sha256") ==
            SHADOW_PREDECESSOR_POINTER_SHA256 and
        evidence.get("default_deny_identity_sha256") ==
            SHADOW_DEFAULT_DENY_IDENTITY_SHA256 and
        evidence.get("source_baseline_sha256") ==
            expected_source_manifest_sha256 and
        evidence.get("lock_mode") == "exclusive" and
        evidence.get("verified_under_lock") is True and
        all(evidence.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")), reason)
    for field in (
            "receipt_file_sha256", "receipt_body_sha256",
            "manifest_file_sha256", "archive_sha256",
            "source_baseline_sha256", "installer_sha256",
            "installed_paths_sha256", "closure_sha256",
            "default_deny_identity_sha256",
            "current_install_pointer_file_sha256",
            "predecessor_current_install_pointer_file_sha256"):
        _digest(evidence.get(field), reason)
    lock = _exact(evidence.get("transaction_lock"),
                  SHADOW_INSTALL_LOCK_FIELDS, reason)
    _require(
        lock.get("path") == SHADOW_INSTALL_LOCK_PATH and
        type(lock.get("device")) is int and lock["device"] >= 0 and
        type(lock.get("inode")) is int and lock["inode"] > 0 and
        lock.get("nlink") == 1 and lock.get("uid") == ROOT_UID and
        lock.get("gid") == ROOT_GID and lock.get("mode") == "0600" and
        lock.get("size") == 0 and
        type(lock.get("mtime_ns")) is int and lock["mtime_ns"] >= 0 and
        type(lock.get("ctime_ns")) is int and lock["ctime_ns"] >= 0 and
        type(lock.get("created_during_transaction")) is bool and
        lock.get("persistent") is True and
        lock.get("held_during_transaction") is True, reason)
    return evidence


def _identifier(value: Any, reason: str) -> str:
    _require(type(value) is str and IDENTIFIER.fullmatch(value) is not None,
             reason)
    return value


def _reject_authority(value: Any, reason: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PERMISSION_FIELDS:
                _require(child is False, reason)
            _reject_authority(child, reason)
    elif isinstance(value, list):
        for child in value:
            _reject_authority(child, reason)


def _validate_seal(document: dict[str, Any], reason: str) -> str:
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    _digest(claimed, reason)
    _require(claimed == digest_bytes(canonical_bytes(body)), reason)
    return claimed


def _strict_document(payload: bytes, reason: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            _require(key not in result, reason)
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("ascii", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ObserverError(reason)),
        )
    except ObserverError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ObserverError(reason) from error
    _require(isinstance(document, dict) and
             payload == canonical_bytes(document), reason)
    _reject_authority(document, reason)
    _validate_seal(document, reason)
    return document


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_uid, value.st_gid,
        value.st_mode, value.st_nlink, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_uid, value.st_gid,
        stat.S_IMODE(value.st_mode),
    )


def _stable_directory_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_uid, value.st_gid,
        value.st_mode, value.st_nlink, value.st_mtime_ns, value.st_ctime_ns,
    )


def _canonical_path(path: Path, reason: str) -> Path:
    _require(path.is_absolute(), reason)
    normalized = Path(os.path.normpath(os.fspath(path)))
    _require(normalized == path and path.name not in {"", ".", ".."} and
             all(part not in {"", ".", ".."} for part in path.parts[1:]),
             reason)
    return normalized


def _open_directory(path: Path, reason: str) -> int:
    path = _canonical_path(path, reason)
    try:
        descriptor = os.open("/", DIRECTORY_FLAGS)
    except OSError as error:
        raise ObserverError(reason) from error
    try:
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
    except (OSError, ObserverError) as error:
        os.close(descriptor)
        if isinstance(error, ObserverError):
            raise
        raise ObserverError(reason) from error


def secure_read(
    path: Path, *, expected_uid: int = ROOT_UID,
    expected_gid: int | None = ROOT_GID,
    modes: frozenset[int] = frozenset({0o400, 0o600}),
    maximum: int = MAXIMUM_INPUT_BYTES,
) -> tuple[bytes, os.stat_result]:
    """Anchored O_NOFOLLOW read with metadata and parent revalidation."""

    path = _canonical_path(path, "P1_OBSERVER_INPUT_PATH_INVALID")
    parent = _open_directory(path.parent,
                             "P1_OBSERVER_INPUT_PARENT_INVALID")
    rebound_parent: int | None = None
    descriptor: int | None = None
    reopened: int | None = None
    try:
        parent_before = os.fstat(parent)
        _require(parent_before.st_uid == expected_uid and
                 (expected_gid is None or
                  parent_before.st_gid == expected_gid) and
                 stat.S_IMODE(parent_before.st_mode) & 0o022 == 0,
                 "P1_OBSERVER_INPUT_PARENT_UNTRUSTED")
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        opened = os.fstat(descriptor)
        _require(
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            opened.st_uid == expected_uid and
            (expected_gid is None or opened.st_gid == expected_gid) and
            stat.S_IMODE(opened.st_mode) in modes and
            0 < opened.st_size <= maximum and
            _file_identity(before) == _file_identity(opened),
            "P1_OBSERVER_INPUT_METADATA_INVALID")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(
                descriptor, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        parent_after = os.fstat(parent)
        rebound_parent = _open_directory(
            path.parent, "P1_OBSERVER_INPUT_PARENT_INVALID")
        rebound_parent_metadata = os.fstat(rebound_parent)
        rebound_entry = os.stat(
            path.name, dir_fd=rebound_parent, follow_symlinks=False)
        reopened = os.open(path.name, READ_FLAGS, dir_fd=rebound_parent)
        reopened_metadata = os.fstat(reopened)
        identity = _file_identity(opened)
        parent_identity = _directory_identity(parent_before)
        _require(
            0 < len(payload) <= maximum and
            identity == _file_identity(after) == _file_identity(final) ==
            _file_identity(rebound_entry) ==
            _file_identity(reopened_metadata) and
            parent_identity == _directory_identity(parent_after) ==
            _directory_identity(rebound_parent_metadata),
            "P1_OBSERVER_INPUT_SECURE_REOPEN_MISMATCH")
        reopened_payload = bytearray()
        while len(reopened_payload) <= maximum:
            chunk = os.read(
                reopened, min(65536, maximum + 1 - len(reopened_payload)))
            if not chunk:
                break
            reopened_payload.extend(chunk)
        _require(bytes(payload) == bytes(reopened_payload),
                 "P1_OBSERVER_INPUT_SECURE_REOPEN_MISMATCH")
        return bytes(payload), opened
    except ObserverError:
        raise
    except OSError as error:
        raise ObserverError("P1_OBSERVER_INPUT_SECURE_READ_FAILED") from error
    finally:
        for file_descriptor in (
                reopened, descriptor, rebound_parent, parent):
            if file_descriptor is not None:
                os.close(file_descriptor)


def load_snapshot(
    path: Path, *, expected_uid: int = ROOT_UID,
    expected_gid: int | None = ROOT_GID,
) -> Snapshot:
    payload, metadata = secure_read(
        path, expected_uid=expected_uid, expected_gid=expected_gid)
    document = _strict_document(payload, "P1_OBSERVER_INPUT_INVALID")
    return Snapshot(
        path=path, payload=payload, metadata=metadata, document=document,
        file_sha256=digest_bytes(payload),
        body_sha256=document["body_sha256"],
    )


def bind_executing_image() -> ProducerBinding:
    try:
        executing = Path(__file__)
        _require(not executing.is_symlink() and
                 executing.resolve(strict=True) == INSTALLED_EXECUTABLE and
                 os.path.samefile(executing, INSTALLED_EXECUTABLE),
                 "P1_OBSERVER_INSTALLED_IMAGE_REQUIRED")
        payload, metadata = secure_read(
            INSTALLED_EXECUTABLE, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID, modes=frozenset({0o755}))
    except (OSError, ObserverError):
        raise ObserverError("P1_OBSERVER_INSTALLED_IMAGE_REQUIRED")
    return ProducerBinding(INSTALLED_EXECUTABLE, payload, metadata)


def _assert_snapshot_unchanged(
    snapshot: Snapshot, *, expected_uid: int,
    expected_gid: int | None = ROOT_GID,
) -> None:
    current = load_snapshot(
        snapshot.path, expected_uid=expected_uid,
        expected_gid=expected_gid)
    _require(current.payload == snapshot.payload and
             _file_identity(current.metadata) ==
             _file_identity(snapshot.metadata),
             "P1_OBSERVER_INPUT_DRIFT")


def _rename_noreplace(parent: int, source: str, destination: str) -> None:
    function = getattr(_LIBC, "renameat2", None)
    _require(function is not None, "P1_OBSERVER_RENAMEAT2_UNAVAILABLE")
    function.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint)
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = function(
        parent, os.fsencode(source), parent, os.fsencode(destination),
        RENAME_NOREPLACE)
    if result != 0:
        number = ctypes.get_errno()
        if number == errno.EEXIST:
            raise ObserverError("P1_OBSERVER_OUTPUT_ALREADY_EXISTS")
        raise ObserverError("P1_OBSERVER_OUTPUT_RENAME_FAILED")


def publish_receipt(
    document: dict[str, Any], output: Path, *, expected_uid: int = ROOT_UID,
    expected_gid: int = ROOT_GID,
) -> dict[str, Any]:
    """Publish once with 0600, fd/parent fsync and secure canonical reopen."""

    output = _canonical_path(output, "P1_OBSERVER_OUTPUT_PATH_INVALID")
    payload = canonical_bytes(document)
    _require(len(payload) <= MAXIMUM_OUTPUT_BYTES,
             "P1_OBSERVER_OUTPUT_TOO_LARGE")
    restored = _strict_document(payload, "P1_OBSERVER_OUTPUT_INVALID")
    _require(restored == document, "P1_OBSERVER_OUTPUT_INVALID")
    parent = _open_directory(output.parent,
                             "P1_OBSERVER_OUTPUT_PARENT_INVALID")
    temporary = f".{output.name}.observer-{secrets.token_hex(16)}.tmp"
    descriptor: int | None = None
    renamed = False
    try:
        parent_metadata = os.fstat(parent)
        _require(parent_metadata.st_uid == expected_uid and
                 parent_metadata.st_gid == expected_gid and
                 stat.S_IMODE(parent_metadata.st_mode) & 0o022 == 0,
                 "P1_OBSERVER_OUTPUT_PARENT_UNTRUSTED")
        try:
            os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ObserverError("P1_OBSERVER_OUTPUT_ALREADY_EXISTS")
        descriptor = os.open(temporary, CREATE_FLAGS, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, expected_uid, expected_gid)
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            _require(count > 0, "P1_OBSERVER_OUTPUT_WRITE_FAILED")
            written += count
        os.fsync(descriptor)
        prepared = os.fstat(descriptor)
        _require(
            stat.S_ISREG(prepared.st_mode) and prepared.st_nlink == 1 and
            prepared.st_uid == expected_uid and
            prepared.st_gid == expected_gid and
            stat.S_IMODE(prepared.st_mode) == 0o600 and
            prepared.st_size == len(payload),
            "P1_OBSERVER_OUTPUT_METADATA_INVALID")
        os.fsync(parent)
        _rename_noreplace(parent, temporary, output.name)
        renamed = True
        os.fsync(parent)
    except ObserverError:
        raise
    except OSError as error:
        raise ObserverError("P1_OBSERVER_OUTPUT_PUBLISH_FAILED") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not renamed:
            try:
                os.unlink(temporary, dir_fd=parent)
                os.fsync(parent)
            except OSError:
                pass
        os.close(parent)
    committed = load_snapshot(
        output, expected_uid=expected_uid, expected_gid=expected_gid)
    _require(committed.payload == payload and committed.document == document,
             "P1_OBSERVER_OUTPUT_POST_VERIFY_FAILED")
    return committed.document


def _boundary() -> dict[str, bool]:
    return {field: False for field in BOUNDARY_FIELDS}


def _state_seal(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["state_sha256"] = digest_bytes(canonical_bytes(result))
    return result


def _default_clock() -> ClockSample:
    descriptor: int | None = None
    reopened: int | None = None
    try:
        path = Path("/proc/sys/kernel/random/boot_id")
        parent = _open_directory(path.parent, "P1_OBSERVER_BOOT_ID_INVALID")
        try:
            descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
            before = os.fstat(descriptor)
            payload = os.read(descriptor, 129)
            after = os.fstat(descriptor)
            reopened = os.open(path.name, READ_FLAGS, dir_fd=parent)
            reopened_payload = os.read(reopened, 129)
            reopened_metadata = os.fstat(reopened)
            _require(
                stat.S_ISREG(before.st_mode) and before.st_uid == ROOT_UID and
                stat.S_IMODE(before.st_mode) == 0o444 and
                0 < len(payload) <= 128 and payload == reopened_payload and
                _file_identity(before) == _file_identity(after) ==
                    _file_identity(reopened_metadata),
                "P1_OBSERVER_BOOT_ID_INVALID")
        finally:
            for opened in (reopened, descriptor, parent):
                if opened is not None:
                    os.close(opened)
            descriptor = None
            reopened = None
        boot_id = payload.decode("ascii", errors="strict").strip()
        boottime_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    except ObserverError:
        raise
    except (OSError, AttributeError, UnicodeError) as error:
        raise ObserverError("P1_OBSERVER_CLOCK_UNAVAILABLE") from error
    _require(BOOT_ID.fullmatch(boot_id) is not None,
             "P1_OBSERVER_BOOT_ID_INVALID")
    return ClockSample(
        wall_ms=time.time_ns() // 1_000_000,
        boottime_ns=boottime_ns, boot_id=boot_id)


def _validate_item_state(
    value: Any, fields: frozenset[str], reason: str,
) -> dict[str, Any]:
    item = _exact(value, fields, reason)
    body = dict(item)
    claimed = body.pop("state_sha256", None)
    _digest(claimed, reason)
    _require(claimed == digest_bytes(canonical_bytes(body)), reason)
    return item


def validate_unit(value: Any, reason: str) -> dict[str, Any]:
    unit = _validate_item_state(value, UNIT_FIELDS, reason)
    _require(type(unit.get("unit")) is str and
             UNIT_NAME.fullmatch(unit["unit"]) is not None, reason)
    for field in ("load_state", "active_state", "sub_state",
                  "unit_file_state"):
        _require(type(unit.get(field)) is str and bool(unit[field]) and
                 len(unit[field]) <= 128, reason)
    _integer(unit.get("main_pid"), reason)
    invocation = unit.get("invocation_id")
    _require(invocation == "" or
             (type(invocation) is str and
              INVOCATION_ID.fullmatch(invocation) is not None), reason)
    _integer(unit.get("exec_main_start_timestamp_monotonic_us"), reason)
    _integer(unit.get("n_restarts"), reason)
    return unit


def validate_process(value: Any, reason: str) -> dict[str, Any]:
    process = _validate_item_state(value, PROCESS_FIELDS, reason)
    _integer(process.get("pid"), reason, 2)
    _integer(process.get("uid"), reason)
    _integer(process.get("gid"), reason)
    _integer(process.get("starttime_ticks"), reason, 1)
    _integer(process.get("exe_device"), reason)
    _integer(process.get("exe_inode"), reason, 1)
    _digest(process.get("cgroup_sha256"), reason)
    return process


def validate_path(value: Any, reason: str) -> dict[str, Any]:
    item = _validate_item_state(value, PATH_FIELDS, reason)
    path = item.get("path")
    _require(type(path) is str, reason)
    _canonical_path(Path(path), reason)
    _require(type(item.get("present")) is bool, reason)
    for field in (
        "parent_device", "parent_inode", "parent_uid", "parent_gid",
        "parent_mode", "parent_nlink",
    ):
        _integer(item.get(field), reason)
    nullable_numbers = (
        "device", "inode", "uid", "gid", "mode", "nlink", "size",
        "mtime_ns", "ctime_ns",
    )
    if item["present"]:
        _require(item.get("file_type") in {
            "regular", "directory", "socket", "fifo", "other"}, reason)
        for field in nullable_numbers:
            _integer(item.get(field), reason)
    else:
        _require(item.get("file_type") is None and
                 all(item.get(field) is None for field in nullable_numbers),
                 reason)
    for field in ("content_file_sha256", "content_body_sha256"):
        value_digest = item.get(field)
        _require(value_digest is None or
                 (type(value_digest) is str and
                  DIGEST.fullmatch(value_digest) is not None), reason)
    _require(item["present"] or
             (item["content_file_sha256"] is None and
              item["content_body_sha256"] is None), reason)
    return item


def _validate_export_document_identity(
    observed: ObservedDocument, expected_path: Path, reader_gid: int,
    reason: str,
) -> None:
    _require(observed.path == expected_path, reason)
    _digest(observed.file_sha256, reason)
    _digest(observed.body_sha256, reason)
    _require(
        observed.file_sha256 ==
            digest_bytes(canonical_bytes(observed.document)) and
        observed.body_sha256 == observed.document.get("body_sha256"), reason)
    _validate_seal(observed.document, reason)
    _reject_authority(observed.document, reason)
    identity = validate_path(observed.path_identity, reason)
    _require(
        identity.get("path") == str(expected_path) and
        identity.get("present") is True and
        identity.get("file_type") == "regular" and
        identity.get("uid") == ROOT_UID and
        identity.get("gid") == reader_gid and
        identity.get("mode") == 0o440 and
        identity.get("nlink") == 1 and
        identity.get("parent_uid") == ROOT_UID and
        identity.get("parent_gid") == reader_gid and
        identity.get("parent_mode") == 0o750 and
        identity.get("content_file_sha256") == observed.file_sha256 and
        identity.get("content_body_sha256") == observed.body_sha256, reason)


def validate_committed_export(
    commit: ObservedDocument,
    snapshot: ObservedDocument,
    lease: ObservedDocument,
    receipt: ObservedDocument,
    *,
    export_root: Path,
) -> CommittedExport:
    """Validate one ACTIVE generation and every pointer/hash binding."""

    reason = "P1_OBSERVER_EXPORT_BINDING_INVALID"
    export_root = _canonical_path(export_root, reason)
    commit_value = _exact(commit.document, EXPORT_COMMIT_FIELDS, reason)
    sequence = commit_value.get("commit_sequence")
    generation = commit_value.get("generation")
    reader_gid = commit_value.get("reader_gid")
    match = EXPORT_GENERATION.fullmatch(str(generation)) \
        if isinstance(generation, str) else None
    _require(
        commit_value.get("schema") ==
            "hepta.shadow-watch-export-commit.v1" and
        commit_value.get("version") == VERSION and
        commit_value.get("authority_status") == "ACTIVE" and
        type(commit_value.get("authority_changed_at_ms")) is int and
        commit_value["authority_changed_at_ms"] >= 0 and
        commit_value.get("close_reason") is None and
        type(sequence) is int and 1 <= sequence < (1 << 64) and
        match is not None and int(match.group(1)) == sequence and
        type(commit_value.get("domain_id")) is str and
        bool(commit_value["domain_id"]) and
        type(commit_value.get("agent_uid")) is int and
        commit_value["agent_uid"] > 0 and
        commit_value.get("reader_uid") == 1000 and
        type(reader_gid) is int and reader_gid > 0 and
        type(commit_value.get("lease_generation")) is int and
        commit_value["lease_generation"] >= 1 and
        type(commit_value.get("committed_at_ms")) is int and
        commit_value["committed_at_ms"] >= 0 and
        commit_value["authority_changed_at_ms"] ==
            commit_value["committed_at_ms"] and
        all(
            type(commit_value.get(field)) is str and
            DIGEST.fullmatch(commit_value[field]) is not None
            for field in (
                "snapshot_body_sha256", "snapshot_file_sha256",
                "lease_receipt_body_sha256", "lease_receipt_file_sha256",
                "export_receipt_body_sha256", "export_receipt_file_sha256",
            )) and
        all(commit_value.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")), reason)

    generation_root = (
        export_root / EXPORT_GENERATIONS_NAME / str(generation))
    expected_paths = (
        export_root / EXPORT_COMMIT_NAME,
        generation_root / EXPORT_FILES[0],
        generation_root / EXPORT_FILES[1],
        generation_root / EXPORT_FILES[2],
    )
    for observed, expected_path in zip(
            (commit, snapshot, lease, receipt), expected_paths, strict=True):
        _validate_export_document_identity(
            observed, expected_path, reader_gid, reason)

    snapshot_value = snapshot.document
    schema = snapshot_value.get("schema")
    version = snapshot_value.get("version")
    if schema == "hepta.shadow-watch-snapshot.v1" and version == 1:
        _exact(snapshot_value, EXPORT_SNAPSHOT_V1_FIELDS, reason)
    elif schema == "hepta.shadow-watch-snapshot.v2" and version == 2:
        _exact(snapshot_value, EXPORT_SNAPSHOT_V2_FIELDS, reason)
        started = snapshot_value.get("collection_started_at_ms")
        finished = snapshot_value.get("collection_finished_at_ms")
        generated = snapshot_value.get("generated_at_ms")
        _require(
            type(started) is int and type(finished) is int and
            type(generated) is int and
            0 <= started <= finished <= generated, reason)
    else:
        raise ObserverError(reason)
    _require(
        type(snapshot_value.get("generated_at_ms")) is int and
        snapshot_value["generated_at_ms"] >= 0 and
        all(snapshot_value.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")), reason)

    lease_value = _exact(lease.document, LEASE_FIELDS, reason)
    lease_generation = lease_value.get("lease_generation")
    accepted_at_ms = lease_value.get("accepted_at_ms")
    ttl_seconds = lease_value.get("ttl_seconds")
    expires_at_ms = lease_value.get("expires_at_ms")
    operation = lease_value.get("operation")
    _require(
        lease_value.get("schema") ==
            "hepta.shadow-watch-lease-receipt.v1" and
        lease_value.get("version") == VERSION and
        lease_value.get("domain_id") == commit_value["domain_id"] and
        lease_value.get("agent_id") == commit_value["domain_id"] and
        lease_value.get("agent_uid") == commit_value["agent_uid"] and
        lease_value.get("boundary") == "WATCH" and
        operation in {"PROVISION", "ROTATE"} and
        type(lease_generation) is int and lease_generation >= 1 and
        lease_value.get("accepted") is True and
        lease_value.get("reason_code") == "OK" and
        type(accepted_at_ms) is int and accepted_at_ms >= 0 and
        type(ttl_seconds) is int and 60 <= ttl_seconds <= 3600 and
        type(expires_at_ms) is int and
        expires_at_ms == accepted_at_ms + ttl_seconds * 1000 and
        all(lease_value.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_authorized")),
        reason)
    if operation == "PROVISION":
        _require(
            lease_value.get("previous_lease_generation") is None and
            lease_value.get("previous_receipt_body_sha256") is None, reason)
    else:
        _require(
            lease_value.get("previous_lease_generation") ==
                lease_generation - 1 and
            type(lease_value.get("previous_receipt_body_sha256")) is str and
            DIGEST.fullmatch(lease_value["previous_receipt_body_sha256"])
                is not None, reason)

    receipt_value = _exact(receipt.document, EXPORT_RECEIPT_FIELDS, reason)
    _require(
        receipt_value.get("schema") ==
            "hepta.shadow-watch-export-receipt.v1" and
        receipt_value.get("version") == VERSION and
        receipt_value.get("boundary") == "WATCH_EXPORT" and
        receipt_value.get("domain_id") == commit_value["domain_id"] and
        receipt_value.get("agent_uid") == commit_value["agent_uid"] and
        receipt_value.get("reader_uid") == commit_value["reader_uid"] and
        receipt_value.get("reader_gid") == reader_gid and
        type(receipt_value.get("snapshot_generated_at_ms")) is int and
        receipt_value["snapshot_generated_at_ms"] >= 0 and
        type(receipt_value.get("exported_at_ms")) is int and
        receipt_value["exported_at_ms"] >= 0 and
        all(receipt_value.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")), reason)

    _require(
        commit_value["snapshot_file_sha256"] == snapshot.file_sha256 and
        commit_value["snapshot_body_sha256"] == snapshot.body_sha256 and
        commit_value["lease_receipt_file_sha256"] == lease.file_sha256 and
        commit_value["lease_receipt_body_sha256"] == lease.body_sha256 and
        commit_value["export_receipt_file_sha256"] == receipt.file_sha256 and
        commit_value["export_receipt_body_sha256"] == receipt.body_sha256 and
        commit_value["lease_generation"] == lease_generation and
        receipt_value.get("snapshot_file_sha256") == snapshot.file_sha256 and
        receipt_value.get("snapshot_body_sha256") == snapshot.body_sha256 and
        receipt_value.get("lease_receipt_file_sha256") == lease.file_sha256 and
        receipt_value.get("lease_receipt_body_sha256") == lease.body_sha256 and
        receipt_value.get("snapshot_generated_at_ms") ==
            snapshot_value.get("generated_at_ms") and
        receipt_value.get("lease_generation") == lease_generation and
        snapshot_value.get("domain_id") == commit_value["domain_id"] and
        snapshot_value.get("agent_uid") == commit_value["agent_uid"], reason)
    return CommittedExport(
        commit=commit, snapshot=snapshot, lease=lease, receipt=receipt,
        commit_sequence=sequence, generation=str(generation))


def validate_broker(value: Any, reason: str) -> dict[str, Any]:
    broker = _validate_item_state(value, BROKER_FIELDS, reason)
    helper = broker.get("helper_path")
    _require(type(helper) is str and Path(helper) == BROKER_HELPER, reason)
    _digest(broker.get("helper_file_sha256"), reason)
    _digest(broker.get("policy_sha256"), reason)
    _integer(broker.get("authorized_connector_count"), reason)
    uids = broker.get("authorized_uids")
    _require(isinstance(uids, list) and uids == sorted(set(uids)) and
             all(type(uid) is int and uid >= 0 for uid in uids), reason)
    _integer(broker.get("protected_port_count"), reason)
    _require(type(broker.get("deny_all")) is bool, reason)
    _integer(broker.get("checked_boottime_ns"), reason)
    return broker


def validate_reference(value: Any, reason: str) -> dict[str, str]:
    reference = _exact(value, ARTIFACT_REFERENCE_FIELDS, reason)
    path = reference.get("path")
    _require(type(path) is str, reason)
    _canonical_path(Path(path), reason)
    _digest(reference.get("file_sha256"), reason)
    _digest(reference.get("body_sha256"), reason)
    _require(reference.get("schema") == INJECTION_SCHEMA, reason)
    return reference


def validate_evidence(value: Any, expected_kind: str, reason: str) \
        -> dict[str, Any]:
    evidence = _exact(value, EVIDENCE_FIELDS, reason)
    _validate_seal(evidence, reason)
    _require(evidence.get("schema") == EVIDENCE_SCHEMA and
             evidence.get("version") == VERSION and
             evidence.get("kind") == expected_kind and
             BOOT_ID.fullmatch(str(evidence.get("boot_id"))) is not None,
             reason)
    _integer(evidence.get("observed_boottime_ns"), reason)
    units = evidence.get("systemd_units")
    processes = evidence.get("processes")
    paths = evidence.get("paths")
    _require(isinstance(units, list) and isinstance(processes, list) and
             isinstance(paths, list) and bool(units or processes or paths),
             reason)
    for item in units:
        validate_unit(item, reason)
    for item in processes:
        validate_process(item, reason)
    for item in paths:
        validate_path(item, reason)
    _require([item["unit"] for item in units] ==
             sorted({item["unit"] for item in units}) and
             [item["pid"] for item in processes] ==
             sorted({item["pid"] for item in processes}) and
             [item["path"] for item in paths] ==
             sorted({item["path"] for item in paths}), reason)
    broker = evidence.get("broker_deny_all")
    if broker is not None:
        validate_broker(broker, reason)
        _require(
            0 <= evidence["observed_boottime_ns"] -
                broker["checked_boottime_ns"] <=
                MAXIMUM_BOOTTIME_SKEW_NS,
            reason)
    fault = evidence.get("fault_injection_receipt")
    if fault is not None:
        validate_reference(fault, reason)
    _require((expected_kind == "FAULT") == (fault is not None), reason)
    return evidence


def validate_activation_gateway_after(
    value: Any, reason: str,
) -> dict[str, Any]:
    gateway = _exact(value, ACTIVATION_GATEWAY_AFTER_FIELDS, reason)
    for field in (
            "gateway_executable_sha256", "domain_config_sha256",
            "gateway_profile_sha256", "gateway_process_profile_sha256",
            "unit_contract_sha256"):
        _digest(gateway.get(field), reason)
    _require(
        gateway.get("unit") == GATEWAY_UNIT and
        gateway.get("active_state") == "active" and
        gateway.get("sub_state") == "running" and
        _integer(gateway.get("gateway_main_pid"), reason, 2) > 1 and
        type(gateway.get("gateway_invocation_id")) is str and
        INVOCATION_ID.fullmatch(gateway["gateway_invocation_id"]) is not None and
        _integer(gateway.get(
            "gateway_exec_main_start_timestamp_monotonic_us"), reason, 1) > 0 and
        _integer(gateway.get("process_starttime_ticks"), reason, 1) > 0 and
        gateway.get("gateway_executable_path") == str(GATEWAY_EXECUTABLE) and
        gateway.get("gateway_profile_path") == str(GATEWAY_PROFILE) and
        gateway.get("gateway_socket_path") == str(GATEWAY_TOOL_SOCKET) and
        gateway.get("supervisor_socket_path") ==
            str(GATEWAY_SUPERVISOR_SOCKET) and
        gateway.get("execution_remote_mode") == "SIMULATOR" and
        gateway.get("tool_account") == "SIM" and
        gateway.get("execution_domain_id") == "SIM:alpha" and
        gateway.get("tool_allow_trade") == "0" and
        gateway.get("session_templates") == "watch" and
        gateway.get("contract_bindings") ==
            "EUR.USD|EUR|CASH|IDEALPRO|USD",
        reason)
    for field in (
            "gateway_socket_device", "gateway_socket_inode",
            "supervisor_socket_device", "supervisor_socket_inode"):
        _integer(gateway.get(field), reason, 1)
    return gateway


def validate_live_gateway_binding(
    activation_gateway: Mapping[str, Any], *, gateway_unit: dict[str, Any],
    gateway_process: dict[str, Any], gateway_executable: dict[str, Any],
    gateway_profile: dict[str, Any], gateway_domain_config: dict[str, Any],
    tool_socket: dict[str, Any], supervisor_socket: dict[str, Any],
    initial: bool, reason: str,
) -> None:
    unit = validate_unit(gateway_unit, reason)
    process = validate_process(gateway_process, reason)
    executable = validate_path(gateway_executable, reason)
    profile = validate_path(gateway_profile, reason)
    domain = validate_path(gateway_domain_config, reason)
    tool = validate_path(tool_socket, reason)
    supervisor = validate_path(supervisor_socket, reason)
    _require(
        unit["unit"] == GATEWAY_UNIT and unit["load_state"] == "loaded" and
        unit["active_state"] == "active" and unit["sub_state"] == "running" and
        unit["main_pid"] == process["pid"] and unit["main_pid"] > 1 and
        process["exe_device"] == executable["device"] and
        process["exe_inode"] == executable["inode"] and
        executable["path"] == str(GATEWAY_EXECUTABLE) and
        executable["present"] is True and
        executable["file_type"] == "regular" and
        executable["content_file_sha256"] ==
            activation_gateway["gateway_executable_sha256"] and
        profile["path"] == str(GATEWAY_PROFILE) and
        profile["present"] is True and profile["file_type"] == "regular" and
        profile["content_file_sha256"] ==
            activation_gateway["gateway_profile_sha256"] and
        domain["path"] == str(GATEWAY_DOMAIN_CONFIG) and
        domain["present"] is True and domain["file_type"] == "regular" and
        domain["content_file_sha256"] ==
            activation_gateway["domain_config_sha256"] and
        tool["path"] == str(GATEWAY_TOOL_SOCKET) and
        tool["present"] is True and tool["file_type"] == "socket" and
        supervisor["path"] == str(GATEWAY_SUPERVISOR_SOCKET) and
        supervisor["present"] is True and
        supervisor["file_type"] == "socket", reason)
    if initial:
        _require(
            unit["main_pid"] == activation_gateway["gateway_main_pid"] and
            unit["invocation_id"] ==
                activation_gateway["gateway_invocation_id"] and
            unit["exec_main_start_timestamp_monotonic_us"] ==
                activation_gateway[
                    "gateway_exec_main_start_timestamp_monotonic_us"] and
            process["starttime_ticks"] ==
                activation_gateway["process_starttime_ticks"] and
            tool["device"] == activation_gateway["gateway_socket_device"] and
            tool["inode"] == activation_gateway["gateway_socket_inode"] and
            supervisor["device"] ==
                activation_gateway["supervisor_socket_device"] and
            supervisor["inode"] ==
                activation_gateway["supervisor_socket_inode"], reason)


def validate_fault_identity(
    value: Any, phase: str, target_id: str, fault_type: str, reason: str,
) -> dict[str, Any]:
    identity = _exact(value, FAULT_IDENTITY_FIELDS, reason)
    _validate_seal(identity, reason)
    _require(identity.get("schema") == FAULT_IDENTITY_SCHEMA and
             identity.get("version") == VERSION and
             identity.get("phase") == phase and
             identity.get("target_id") == target_id and
             BOOT_ID.fullmatch(str(identity.get("boot_id"))) is not None,
             reason)
    _integer(identity.get("observed_boottime_ns"), reason)
    epoch = identity.get("service_epoch")
    fence = identity.get("fencing_generation")
    lease = identity.get("lease_generation")
    context_missing = epoch is None and fence is None and lease is None
    context_complete = (
        type(epoch) is str and IDENTIFIER.fullmatch(epoch) is not None and
        type(fence) is int and fence >= 0 and
        type(lease) is int and lease >= 0)
    _require(context_complete or
             (context_missing and fault_type in {
                 "NETWORK_DENY_RELOAD", "CLOCK_STEP"}), reason)
    for field in ("fixture_generation", "fixture_expires_boottime_ns"):
        number = identity.get(field)
        _require(number is None or (type(number) is int and number >= 0),
                 reason)
    fixture_valid = identity.get("fixture_valid")
    _require(fixture_valid is None or type(fixture_valid) is bool, reason)
    fixture_fault = fault_type in {"TOKEN_LOSS", "LEASE_EXPIRY"}
    _require(
        ((fault_type == "CLOCK_STEP" and
          type(identity.get("wall_clock_delta_ms")) is int) or
         (fault_type != "CLOCK_STEP" and
          identity.get("wall_clock_delta_ms") is None)) and
        ((fixture_fault and
          type(identity.get("fixture_generation")) is int and
          type(identity.get("fixture_expires_boottime_ns")) is int and
          type(identity.get("fixture_valid")) is bool) or
         (not fixture_fault and
          identity.get("fixture_generation") is None and
          identity.get("fixture_expires_boottime_ns") is None and
          identity.get("fixture_valid") is None)), reason)
    units = identity.get("systemd_units")
    processes = identity.get("processes")
    paths = identity.get("paths")
    _require(isinstance(units, list) and isinstance(processes, list) and
             isinstance(paths, list), reason)
    for item in units:
        validate_unit(item, reason)
    for item in processes:
        validate_process(item, reason)
    for item in paths:
        validate_path(item, reason)
    _require([item["unit"] for item in units] ==
             sorted({item["unit"] for item in units}) and
             [item["pid"] for item in processes] ==
             sorted({item["pid"] for item in processes}) and
             [item["path"] for item in paths] ==
             sorted({item["path"] for item in paths}), reason)
    broker = identity.get("broker_deny_all")
    if broker is not None:
        validate_broker(broker, reason)
    _integer(identity.get("residue_count"), reason)
    wall_delta = identity.get("wall_clock_delta_ms")
    _require(wall_delta is None or type(wall_delta) is int, reason)
    return identity


class Host(Protocol):
    def clock(self) -> ClockSample: ...
    def unit(self, unit: str) -> dict[str, Any]: ...
    def process(self, pid: int) -> dict[str, Any]: ...
    def path(self, path: Path, content: str | None = None) \
            -> dict[str, Any]: ...
    def document(self, path: Path, *, expected_uid: int) \
            -> ObservedDocument: ...
    def committed_export(self, export_root: Path) -> CommittedExport: ...
    def broker(self) -> dict[str, Any]: ...


class ReadOnlyHost:
    """Production host reader with a deliberately tiny command allowlist."""

    _UNIT_PROPERTIES = (
        "LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID",
        "InvocationID", "ExecMainStartTimestampMonotonic", "NRestarts",
    )

    def __init__(self, clock: Callable[[], ClockSample] = _default_clock):
        self._clock = clock

    def clock(self) -> ClockSample:
        return self._clock()

    @staticmethod
    def _run(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        allowed_systemctl = (
            len(arguments) == 4 + len(ReadOnlyHost._UNIT_PROPERTIES) and
            arguments[0:3] == (SYSTEMCTL, "show", "--no-pager") and
            all(arguments[3 + index] == f"--property={name}"
                for index, name in
                enumerate(ReadOnlyHost._UNIT_PROPERTIES)) and
            UNIT_NAME.fullmatch(arguments[-1]) is not None)
        allowed_broker = arguments == (str(BROKER_HELPER), "--check-deny-all")
        _require(allowed_systemctl or allowed_broker,
                 "P1_OBSERVER_COMMAND_NOT_ALLOWLISTED")
        try:
            result = subprocess.run(
                arguments, check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
                     "LC_ALL": "C", "PYTHONNOUSERSITE": "1"},
                cwd="/", timeout=15)
        except (OSError, subprocess.SubprocessError) as error:
            raise ObserverError("P1_OBSERVER_COMMAND_FAILED") from error
        _require(len(result.stdout) <= MAXIMUM_COMMAND_BYTES and
                 len(result.stderr) <= MAXIMUM_COMMAND_BYTES,
                 "P1_OBSERVER_COMMAND_OUTPUT_TOO_LARGE")
        return result

    def unit(self, unit: str) -> dict[str, Any]:
        _require(unit in set(WATCH_UNITS) | set(PAPER_UNITS) |
                 {BROKER_UNIT} or
                 re.fullmatch(
                     r"hepta-p1-shadow-(reader|host|admission)-round"
                     r"[1-9][0-9]*\.service", unit) is not None,
                 "P1_OBSERVER_UNIT_NOT_ALLOWLISTED")
        arguments = (
            SYSTEMCTL, "show", "--no-pager",
            *(f"--property={name}" for name in self._UNIT_PROPERTIES), unit)
        result = self._run(arguments)
        _require(result.returncode == 0 and not result.stderr,
                 "P1_OBSERVER_SYSTEMD_SHOW_FAILED")
        try:
            lines = result.stdout.decode("utf-8", errors="strict").splitlines()
        except UnicodeError as error:
            raise ObserverError("P1_OBSERVER_SYSTEMD_SHOW_INVALID") from error
        fields: dict[str, str] = {}
        for line in lines:
            key, separator, value = line.partition("=")
            _require(separator == "=" and key not in fields,
                     "P1_OBSERVER_SYSTEMD_SHOW_INVALID")
            fields[key] = value
        _require(set(fields) == set(self._UNIT_PROPERTIES),
                 "P1_OBSERVER_SYSTEMD_SHOW_INVALID")
        for field in ("MainPID", "ExecMainStartTimestampMonotonic", "NRestarts"):
            _require(fields[field].isdigit(),
                     "P1_OBSERVER_SYSTEMD_SHOW_INVALID")
        invocation = fields["InvocationID"]
        _require(invocation == "" or INVOCATION_ID.fullmatch(invocation),
                 "P1_OBSERVER_SYSTEMD_SHOW_INVALID")
        return _state_seal({
            "unit": unit, "load_state": fields["LoadState"],
            "active_state": fields["ActiveState"],
            "sub_state": fields["SubState"],
            "unit_file_state": fields["UnitFileState"],
            "main_pid": int(fields["MainPID"]),
            "invocation_id": invocation,
            "exec_main_start_timestamp_monotonic_us":
                int(fields["ExecMainStartTimestampMonotonic"]),
            "n_restarts": int(fields["NRestarts"]),
        })

    @staticmethod
    def _read_proc_entry(
        directory: int, name: str, maximum: int,
    ) -> bytes:
        _require(name in {"stat", "cgroup"},
                 "P1_OBSERVER_PROC_ENTRY_NOT_ALLOWLISTED")
        descriptor: int | None = None
        try:
            descriptor = os.open(
                name, os.O_RDONLY | NOFOLLOW | CLOEXEC | NONBLOCK,
                dir_fd=directory)
            payload = bytearray()
            while len(payload) <= maximum:
                chunk = os.read(
                    descriptor, min(4096, maximum + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            _require(0 < len(payload) <= maximum,
                     "P1_OBSERVER_PROC_ENTRY_INVALID")
            return bytes(payload)
        except OSError as error:
            raise ObserverError("P1_OBSERVER_PROC_ENTRY_INVALID") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def process(self, pid: int) -> dict[str, Any]:
        _integer(pid, "P1_OBSERVER_PROCESS_INVALID", 2)
        proc = _open_directory(Path("/proc"), "P1_OBSERVER_PROC_INVALID")
        first: int | None = None
        second: int | None = None
        try:
            first = os.open(str(pid), DIRECTORY_FLAGS, dir_fd=proc)
            before = os.fstat(first)
            _require(stat.S_ISDIR(before.st_mode),
                     "P1_OBSERVER_PROCESS_INVALID")
            stat_payload = self._read_proc_entry(first, "stat", 4096)
            cgroup_payload = self._read_proc_entry(first, "cgroup", 65536)
            prefix = str(pid).encode("ascii") + b" ("
            close = stat_payload.rfind(b") ")
            _require(stat_payload.startswith(prefix) and
                     stat_payload.endswith(b"\n") and close >= len(prefix),
                     "P1_OBSERVER_PROCESS_STAT_INVALID")
            fields = stat_payload[close + 2:-1].split(b" ")
            _require(len(fields) >= 20 and fields[19].isdigit(),
                     "P1_OBSERVER_PROCESS_STAT_INVALID")
            starttime = int(fields[19])
            exe = os.stat("exe", dir_fd=first, follow_symlinks=True)
            second = os.open(str(pid), DIRECTORY_FLAGS, dir_fd=proc)
            after = os.fstat(second)
            rebound_stat = self._read_proc_entry(second, "stat", 4096)
            rebound_exe = os.stat("exe", dir_fd=second, follow_symlinks=True)
            _require(_directory_identity(before) == _directory_identity(after)
                     and rebound_stat == stat_payload and starttime > 0 and
                     (exe.st_dev, exe.st_ino) ==
                     (rebound_exe.st_dev, rebound_exe.st_ino),
                     "P1_OBSERVER_PROCESS_REBOUND")
            return _state_seal({
                "pid": pid, "uid": before.st_uid, "gid": before.st_gid,
                "starttime_ticks": starttime, "exe_device": exe.st_dev,
                "exe_inode": exe.st_ino,
                "cgroup_sha256": digest_bytes(cgroup_payload),
            })
        except ObserverError:
            raise
        except OSError as error:
            raise ObserverError("P1_OBSERVER_PROCESS_INVALID") from error
        finally:
            for descriptor in (second, first, proc):
                if descriptor is not None:
                    os.close(descriptor)

    @staticmethod
    def _path_type(metadata: os.stat_result) -> str:
        if stat.S_ISREG(metadata.st_mode):
            return "regular"
        if stat.S_ISDIR(metadata.st_mode):
            return "directory"
        if stat.S_ISSOCK(metadata.st_mode):
            return "socket"
        if stat.S_ISFIFO(metadata.st_mode):
            return "fifo"
        return "other"

    def path(self, path: Path, content: str | None = None) -> dict[str, Any]:
        path = _canonical_path(path, "P1_OBSERVER_OBSERVED_PATH_INVALID")
        _require(content in {None, "json", "bytes"},
                 "P1_OBSERVER_PATH_CONTENT_MODE_INVALID")
        parent = _open_directory(path.parent,
                                 "P1_OBSERVER_OBSERVED_PARENT_INVALID")
        try:
            parent_before = os.fstat(parent)
            _require(stat.S_IMODE(parent_before.st_mode) & 0o002 == 0,
                     "P1_OBSERVER_OBSERVED_PARENT_UNTRUSTED")
            common = {
                "path": str(path),
                "parent_device": parent_before.st_dev,
                "parent_inode": parent_before.st_ino,
                "parent_uid": parent_before.st_uid,
                "parent_gid": parent_before.st_gid,
                "parent_mode": stat.S_IMODE(parent_before.st_mode),
                "parent_nlink": parent_before.st_nlink,
            }
            try:
                before = os.stat(path.name, dir_fd=parent,
                                 follow_symlinks=False)
            except FileNotFoundError:
                return _state_seal({
                    **common, "present": False, "file_type": None,
                    "device": None, "inode": None, "uid": None,
                    "gid": None, "mode": None, "nlink": None,
                    "size": None, "mtime_ns": None, "ctime_ns": None,
                    "content_file_sha256": None,
                    "content_body_sha256": None,
                })
            _require(not stat.S_ISLNK(before.st_mode),
                     "P1_OBSERVER_OBSERVED_PATH_SYMLINK")
            file_sha: str | None = None
            body_sha: str | None = None
            if content is not None:
                expected_uid = before.st_uid
                modes = frozenset({stat.S_IMODE(before.st_mode)})
                payload, metadata = secure_read(
                    path, expected_uid=expected_uid, expected_gid=None,
                    modes=modes)
                _require(_file_identity(metadata) == _file_identity(before),
                         "P1_OBSERVER_OBSERVED_PATH_REBOUND")
                file_sha = digest_bytes(payload)
                if content == "json":
                    document = _strict_document(
                        payload, "P1_OBSERVER_OBSERVED_JSON_INVALID")
                    body_sha = document["body_sha256"]
            final = os.stat(path.name, dir_fd=parent,
                            follow_symlinks=False)
            parent_after = os.fstat(parent)
            _require(_file_identity(before) == _file_identity(final) and
                     _directory_identity(parent_before) ==
                     _directory_identity(parent_after),
                     "P1_OBSERVER_OBSERVED_PATH_REBOUND")
            return _state_seal({
                **common, "present": True,
                "file_type": self._path_type(before),
                "device": before.st_dev, "inode": before.st_ino,
                "uid": before.st_uid, "gid": before.st_gid,
                "mode": stat.S_IMODE(before.st_mode),
                "nlink": before.st_nlink, "size": before.st_size,
                "mtime_ns": before.st_mtime_ns, "ctime_ns": before.st_ctime_ns,
                "content_file_sha256": file_sha,
                "content_body_sha256": body_sha,
            })
        except ObserverError:
            raise
        except OSError as error:
            raise ObserverError("P1_OBSERVER_OBSERVED_PATH_INVALID") from error
        finally:
            os.close(parent)

    def document(self, path: Path, *, expected_uid: int) -> ObservedDocument:
        snapshot = load_snapshot(
            path, expected_uid=expected_uid, expected_gid=None)
        identity = self.path(path, "json")
        _require(identity["content_file_sha256"] == snapshot.file_sha256 and
                 identity["content_body_sha256"] == snapshot.body_sha256,
                 "P1_OBSERVER_DOCUMENT_REBOUND")
        return ObservedDocument(
            path=path, document=snapshot.document,
            file_sha256=snapshot.file_sha256,
            body_sha256=snapshot.body_sha256, path_identity=identity)

    def _committed_document(
        self, path: Path, reader_gid: int,
    ) -> ObservedDocument:
        payload, _metadata = secure_read(
            path, expected_uid=ROOT_UID, expected_gid=reader_gid,
            modes=frozenset({0o440}))
        document = _strict_document(
            payload, "P1_OBSERVER_EXPORT_DOCUMENT_INVALID")
        identity = self.path(path, "json")
        file_sha256 = digest_bytes(payload)
        _require(
            identity.get("content_file_sha256") == file_sha256 and
            identity.get("content_body_sha256") ==
                document.get("body_sha256"),
            "P1_OBSERVER_EXPORT_DOCUMENT_REBOUND")
        return ObservedDocument(
            path=path, document=document, file_sha256=file_sha256,
            body_sha256=document["body_sha256"], path_identity=identity)

    @staticmethod
    def _export_directory_metadata(
        path: Path, reader_gid: int, reason: str,
    ) -> os.stat_result:
        descriptor = _open_directory(path, reason)
        try:
            opened = os.fstat(descriptor)
            named = path.lstat()
            _require(
                _directory_identity(opened) == _directory_identity(named) and
                stat.S_ISDIR(opened.st_mode) and
                opened.st_uid == ROOT_UID and opened.st_gid == reader_gid and
                stat.S_IMODE(opened.st_mode) == 0o750, reason)
            return opened
        except OSError as error:
            raise ObserverError(reason) from error
        finally:
            os.close(descriptor)

    def committed_export(self, export_root: Path) -> CommittedExport:
        """Resolve and verify one committed generation under a shared lock."""

        reason = "P1_OBSERVER_EXPORT_COMMIT_INVALID"
        export_root = _canonical_path(export_root, reason)
        descriptor = _open_directory(export_root, reason)
        rebound: int | None = None
        locked = False
        try:
            root_opened = os.fstat(descriptor)
            root_named = export_root.lstat()
            reader_gid = root_opened.st_gid
            _require(
                _directory_identity(root_opened) ==
                    _directory_identity(root_named) and
                stat.S_ISDIR(root_opened.st_mode) and
                root_opened.st_uid == ROOT_UID and reader_gid > 0 and
                stat.S_IMODE(root_opened.st_mode) == 0o750, reason)
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            locked = True
            root_locked = os.fstat(descriptor)
            current_path = export_root / EXPORT_COMMIT_NAME
            current_before = current_path.lstat()
            first_commit = self._committed_document(
                current_path, reader_gid)
            current_middle = current_path.lstat()
            commit_value = _exact(
                first_commit.document, EXPORT_COMMIT_FIELDS, reason)
            if commit_value.get("authority_status") in {"CLOSING", "CLOSED"}:
                raise ObserverError("P1_OBSERVER_EXPORT_AUTHORITY_ENDED")
            sequence = commit_value.get("commit_sequence")
            generation_name = commit_value.get("generation")
            generation_match = EXPORT_GENERATION.fullmatch(
                generation_name) if isinstance(generation_name, str) else None
            _require(
                generation_match is not None and type(sequence) is int and
                int(generation_match.group(1)) == sequence and
                commit_value.get("reader_gid") == reader_gid, reason)

            generations = export_root / EXPORT_GENERATIONS_NAME
            generations_before = self._export_directory_metadata(
                generations, reader_gid,
                "P1_OBSERVER_EXPORT_GENERATIONS_INVALID")
            generation = generations / generation_name
            generation_before = self._export_directory_metadata(
                generation, reader_gid,
                "P1_OBSERVER_EXPORT_GENERATION_INVALID")
            _require(
                set(os.listdir(generation)) == set(EXPORT_FILES),
                "P1_OBSERVER_EXPORT_GENERATION_INVENTORY_INVALID")
            documents = tuple(
                self._committed_document(generation / name, reader_gid)
                for name in EXPORT_FILES)
            second_commit = self._committed_document(
                current_path, reader_gid)
            current_after = current_path.lstat()
            generation_after = generation.lstat()
            generations_after = generations.lstat()
            root_after = os.fstat(descriptor)
            rebound = _open_directory(export_root, reason)
            root_rebound = os.fstat(rebound)
            _require(
                first_commit == second_commit and
                _file_identity(current_before) ==
                    _file_identity(current_middle) ==
                    _file_identity(current_after) and
                _stable_directory_identity(generation_before) ==
                    _stable_directory_identity(generation_after) and
                _stable_directory_identity(generations_before) ==
                    _stable_directory_identity(generations_after) and
                _stable_directory_identity(root_locked) ==
                    _stable_directory_identity(root_after) ==
                    _stable_directory_identity(root_rebound),
                "P1_OBSERVER_EXPORT_GENERATION_DRIFT")
            snapshot, lease, receipt = documents
            return validate_committed_export(
                first_commit, snapshot, lease, receipt,
                export_root=export_root)
        except ObserverError:
            raise
        except OSError as error:
            raise ObserverError(reason) from error
        finally:
            if rebound is not None:
                os.close(rebound)
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def broker(self) -> dict[str, Any]:
        sample = self.clock()
        helper_payload, _metadata = secure_read(
            BROKER_HELPER, expected_uid=ROOT_UID,
            expected_gid=ROOT_GID,
            modes=frozenset({0o500, 0o555, 0o700, 0o755}))
        result = self._run((str(BROKER_HELPER), "--check-deny-all"))
        _require(result.returncode == 0 and not result.stderr,
                 "P1_OBSERVER_BROKER_CHECK_FAILED")
        match = BROKER_PASS.fullmatch(result.stdout)
        _require(match is not None,
                 "P1_OBSERVER_BROKER_CHECK_INVALID")
        connectors = int(match.group(2))
        raw_uids = match.group(3).decode("ascii")
        uids = [] if raw_uids == "" else [int(item) for item in
                                           raw_uids.split(",")]
        _require(uids == sorted(set(uids)),
                 "P1_OBSERVER_BROKER_CHECK_INVALID")
        return _state_seal({
            "helper_path": str(BROKER_HELPER),
            "helper_file_sha256": digest_bytes(helper_payload),
            "policy_sha256": match.group(1).decode("ascii"),
            "authorized_connector_count": connectors,
            "authorized_uids": uids,
            "protected_port_count": int(match.group(4)),
            "deny_all": connectors == 0 and not uids,
            "checked_boottime_ns": sample.boottime_ns,
        })


def validate_spec(document: dict[str, Any]) -> dict[str, Any]:
    reason = "P1_OBSERVER_CAMPAIGN_SPEC_INVALID"
    _exact(document, SPEC_FIELDS, reason)
    _validate_seal(document, reason)
    _reject_authority(document, reason)
    _require(document.get("schema") == SPEC_SCHEMA and
             document.get("version") == VERSION, reason)
    for field in (
        "campaign_id", "domain_id", "strategy_id", "strategy_version",
        "independent_auditor_id",
    ):
        _identifier(document.get(field), reason)
    for field in (
        "source_manifest_sha256", "policy_sha256", "strategy_sha256",
        "trading_calendar_sha256", "fault_plan_body_sha256",
    ):
        _digest(document.get(field), reason)
    freeze_bundle = _exact(
        document.get("freeze_bundle"), FREEZE_REFERENCE_FIELDS, reason)
    _require(type(freeze_bundle.get("path")) is str and
             Path(freeze_bundle["path"]).is_absolute(), reason)
    _digest(freeze_bundle.get("file_sha256"), reason)
    _digest(freeze_bundle.get("body_sha256"), reason)
    campaigns = document.get("formal_campaigns")
    _require(isinstance(campaigns, list) and bool(campaigns), reason)
    identifiers: list[str] = []
    for item in campaigns:
        campaign = _exact(item, FORMAL_CAMPAIGN_FIELDS, reason)
        identifiers.append(_identifier(campaign.get("campaign_id"), reason))
        for field in (
            "campaign_sha256", "policy_body_sha256", "policy_file_sha256",
        ):
            _digest(campaign.get(field), reason)
    _require(len(identifiers) == len(set(identifiers)), reason)

    days = document.get("declared_trading_days")
    _require(
        isinstance(days, list) and
        MINIMUM_TRADING_DAYS <= len(days) <= MAXIMUM_TRADING_DAYS and
        all(type(item) is str for item in days) and
        days == sorted(set(days)), reason)
    try:
        parsed_days = [date.fromisoformat(item) for item in days]
    except ValueError as error:
        raise ObserverError(reason) from error
    _require(
        all(item.isoformat() == raw and item.weekday() < 5
            for item, raw in zip(parsed_days, days, strict=True)), reason)
    try:
        trading_timezone = ZoneInfo(document.get("trading_timezone"))
    except (TypeError, ZoneInfoNotFoundError) as error:
        raise ObserverError(reason) from error
    _require(document.get("trading_timezone") == TRADING_TIMEZONE, reason)

    scheduled = _integer(
        document.get("scheduled_decision_count"), reason,
        MINIMUM_ELIGIBLE_DECISIONS)
    minimum = _integer(
        document.get("minimum_eligible_decisions"), reason,
        MINIMUM_ELIGIBLE_DECISIONS)
    eligible = document.get("eligible_scheduled_at_ms")
    _require(
        minimum <= scheduled and isinstance(eligible, list) and
        minimum <= len(eligible) <= scheduled and
        all(type(item) is int and item >= 0 for item in eligible) and
        eligible == sorted(set(eligible)), reason)
    derived_days = {
        datetime.fromtimestamp(item / 1000, tz=timezone.utc)
        .astimezone(trading_timezone).date().isoformat()
        for item in eligible
    }
    _require(derived_days == set(days), reason)
    completeness = _integer(document.get("minimum_complete_ppm"), reason)
    _require(MINIMUM_COMPLETE_PPM <= completeness <= 1_000_000, reason)
    _integer(
        document.get("minimum_boottime_duration_ns"), reason,
        MINIMUM_BOOTTIME_DURATION_NS)
    maximum_gap = _integer(
        document.get("maximum_checkpoint_gap_ns"), reason, 1)
    _require(maximum_gap <= MAXIMUM_CHECKPOINT_GAP_NS, reason)
    maximum_lateness = _integer(
        document.get("maximum_decision_lateness_ms"), reason)
    _require(maximum_lateness <= MAXIMUM_DECISION_LATENESS_MS, reason)
    _integer(document.get("frozen_at_ms"), reason)
    return document


def _validate_file_reference(
    value: Any, reason: str, *, schema: bool = False,
) -> dict[str, Any]:
    fields = RUNTIME_REFERENCE_FIELDS if schema else FREEZE_REFERENCE_FIELDS
    reference = _exact(value, fields, reason)
    path = reference.get("path")
    _require(type(path) is str, reason)
    _canonical_path(Path(path), reason)
    _digest(reference.get("file_sha256"), reason)
    _digest(reference.get("body_sha256"), reason)
    if schema:
        _require(type(reference.get("schema")) is str and
                 bool(reference["schema"]), reason)
    return reference


def validate_campaign_runtime(
    document: dict[str, Any], spec_snapshot: Snapshot,
    spec: Mapping[str, Any], sample: ClockSample,
    *, expected_observer_sha256: str | None,
) -> dict[str, Any]:
    """Validate the frozen campaign grid consumed by the observer worker."""

    reason = "P1_OBSERVER_CAMPAIGN_RUNTIME_INVALID"
    _exact(document, RUNTIME_FIELDS, reason)
    _validate_seal(document, reason)
    _reject_authority(document, reason)
    _require(
        document.get("schema") == CAMPAIGN_RUNTIME_SCHEMA and
        document.get("version") == VERSION and
        document.get("status") == "FROZEN" and
        document.get("campaign_id") == spec.get("campaign_id") and
        document.get("round") == 114 and
        document.get("boot_id") == sample.boot_id,
        reason)
    issued = _integer(document.get("issued_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason, issued + 1)
    _require(issued <= sample.wall_ms < expires, reason)
    spec_reference = _validate_file_reference(
        document.get("campaign_spec"), reason)
    _require(
        spec_reference == {
            "path": str(spec_snapshot.path),
            "file_sha256": spec_snapshot.file_sha256,
            "body_sha256": spec_snapshot.body_sha256,
        } and
        document.get("freeze_bundle") == spec.get("freeze_bundle"), reason)
    fault_plan = _validate_file_reference(document.get("fault_plan"), reason)
    _require(fault_plan["body_sha256"] ==
             spec.get("fault_plan_body_sha256"), reason)

    formals = document.get("formal_campaigns")
    _require(isinstance(formals, list) and bool(formals), reason)
    frozen_formals = {
        item["campaign_id"]: item for item in spec["formal_campaigns"]}
    identifiers: list[str] = []
    previous_teardown: int | None = None
    for raw in formals:
        item = _exact(raw, FORMAL_RUNTIME_FIELDS, reason)
        formal_id = _identifier(item.get("formal_campaign_id"), reason)
        identifiers.append(formal_id)
        frozen = frozen_formals.get(formal_id)
        _require(frozen is not None, reason)
        _identifier(item.get("probe_campaign_id"), reason)
        launcher_start = _integer(item.get("launcher_start_ms"), reason, 1)
        dispatch = _integer(
            item.get("launcher_dispatch_at_ms"), reason, 1)
        valid_after = _integer(item.get("valid_after_ms"), reason, 1)
        interval = _integer(item.get("slot_interval_ms"), reason, 1)
        maximum = _integer(item.get("maximum_iterations"), reason, 1)
        formal_expiry = _integer(
            item.get("expires_at_ms"), reason, valid_after + 1)
        completion = _integer(
            item.get("launcher_completion_deadline_ms"), reason,
            formal_expiry)
        projection = _integer(
            item.get("projection_deadline_ms"), reason, completion)
        teardown = _integer(
            item.get("teardown_deadline_ms"), reason, projection)
        policy = _validate_file_reference(item.get("policy"), reason)
        _require(
            dispatch < launcher_start < valid_after and
            interval == POLICY_SLOT_INTERVAL_MS and
            maximum == POLICY_MAXIMUM_ITERATIONS and
            formal_expiry == valid_after + interval * maximum and
            completion <= projection <= teardown and
            (previous_teardown is None or
             valid_after == (
                (previous_teardown + LAUNCHER_WARMUP_MS +
                 LAUNCHER_EARLY_START_LEAD_MS) // interval + 1
             ) * interval) and
            policy["file_sha256"] == frozen["policy_file_sha256"] and
            policy["body_sha256"] == frozen["policy_body_sha256"], reason)
        previous_teardown = teardown
        for field in (
                "launcher_receipt_path", "verified_closure_path",
                "artifact_root"):
            value = item.get(field)
            _require(type(value) is str, reason)
            _canonical_path(Path(value), reason)
    _require(
        identifiers == list(frozen_formals) and
        len(identifiers) == len(set(identifiers)) and
        document.get("pin_formal_campaign_id") in set(identifiers) and
        previous_teardown is not None and expires > previous_teardown,
        reason)
    cadence = _integer(document.get("observer_cadence_ms"), reason, 1)
    lateness = _integer(document.get("maximum_slot_lateness_ms"), reason)
    _require(lateness < cadence, reason)
    for field in (
            "state_root", "raw_observation_directory", "recorder_root",
            "injector_journal_directory", "injector_output_directory",
            "control_directory"):
        value = document.get(field)
        _require(type(value) is str, reason)
        _canonical_path(Path(value), reason)
    executables = document.get("executables")
    _require(isinstance(executables, dict) and
             "independent_observer" in executables, reason)
    for value in executables.values():
        executable = _exact(value, RUNTIME_EXECUTABLE_FIELDS, reason)
        path = executable.get("path")
        _require(type(path) is str, reason)
        _canonical_path(Path(path), reason)
        _digest(executable.get("file_sha256"), reason)
    observer = executables["independent_observer"]
    _require(observer["path"] == str(INSTALLED_EXECUTABLE) and
             (expected_observer_sha256 is None or
              observer["file_sha256"] == expected_observer_sha256), reason)
    return document


def validate_plan(
    document: dict[str, Any], spec: Mapping[str, Any],
) -> list[dict[str, Any]]:
    reason = "P1_OBSERVER_FAULT_PLAN_INVALID"
    _exact(document, FAULT_PLAN_FIELDS, reason)
    _validate_seal(document, reason)
    _reject_authority(document, reason)
    _require(
        document.get("schema") == FAULT_PLAN_SCHEMA and
        document.get("version") == VERSION and
        document.get("campaign_id") == spec.get("campaign_id") and
        document.get("source_manifest_sha256") ==
            spec.get("source_manifest_sha256") and
        document.get("policy_sha256") == spec.get("policy_sha256") and
        document.get("strategy_sha256") == spec.get("strategy_sha256") and
        document.get("body_sha256") == spec.get("fault_plan_body_sha256"),
        reason)
    faults = document.get("planned_faults")
    _require(isinstance(faults, list) and bool(faults), reason)
    formal_ids = {
        item.get("campaign_id") for item in spec.get("formal_campaigns", [])
        if isinstance(item, Mapping)
    }
    _require(bool(formal_ids) and None not in formal_ids, reason)
    identifiers: list[str] = []
    ordering: list[tuple[int, str]] = []
    previous_window_end: int | None = None
    for item in faults:
        fault = _exact(item, PLANNED_FAULT_FIELDS, reason)
        fault_id = _identifier(fault.get("fault_id"), reason)
        fault_type = fault.get("fault_type")
        _require(fault_type in ALLOWED_FAULT_TYPES and
                 fault.get("target_id") == FAULT_TARGET_IDS[fault_type] and
                 fault.get("formal_campaign_id") in formal_ids,
                 reason)
        injection = _integer(fault.get("inject_at_boottime_ns"), reason)
        lateness = _integer(
            fault.get("maximum_injection_lateness_ns"), reason)
        recovery = _integer(fault.get("maximum_recovery_ns"), reason, 1)
        _require(
            lateness <= MAXIMUM_FAULT_INJECTION_LATENESS_NS and
            recovery <= MAXIMUM_FAULT_RECOVERY_NS and
            (previous_window_end is None or
             injection > previous_window_end),
            reason)
        identifiers.append(fault_id)
        ordering.append((injection, fault_id))
        previous_window_end = injection + lateness + recovery
    _require(len(identifiers) == len(set(identifiers)) and
             ordering == sorted(ordering), reason)
    return faults


def _round(campaign_id: str) -> int:
    match = re.search(r"(?:^|-)round([1-9][0-9]*)(?:-|$)", campaign_id)
    _require(match is not None, "P1_OBSERVER_CAMPAIGN_ROUND_INVALID")
    return int(match.group(1))


def _artifact_reference(snapshot: Snapshot) -> dict[str, str]:
    return {
        "path": str(snapshot.path), "file_sha256": snapshot.file_sha256,
        "body_sha256": snapshot.body_sha256,
        "schema": str(snapshot.document.get("schema")),
    }


def _evidence(
    kind: str, sample: ClockSample, *, units: Sequence[dict[str, Any]],
    processes: Sequence[dict[str, Any]], paths: Sequence[dict[str, Any]],
    broker: dict[str, Any] | None,
    fault_reference: dict[str, str] | None = None,
) -> dict[str, Any]:
    value = seal({
        "schema": EVIDENCE_SCHEMA, "version": VERSION, "kind": kind,
        "boot_id": sample.boot_id,
        "observed_boottime_ns": sample.boottime_ns,
        "systemd_units": sorted(units, key=lambda item: item["unit"]),
        "processes": sorted(processes, key=lambda item: item["pid"]),
        "paths": sorted(paths, key=lambda item: item["path"]),
        "broker_deny_all": broker,
        "fault_injection_receipt": fault_reference,
    })
    validate_evidence(value, kind, "P1_OBSERVER_EVIDENCE_INVALID")
    return value


def _observation_header(
    schema: str, spec: Mapping[str, Any], sample: ClockSample,
    evidence: dict[str, Any], producer: Mapping[str, str],
    production_mode: str, expires_at_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "schema": schema, "version": VERSION, "status": "COMPLETE",
        "observed_at_ms": sample.wall_ms,
        "expires_at_ms": sample.wall_ms + OBSERVATION_LIFETIME_MS
        if expires_at_ms is None else expires_at_ms,
        "campaign_id": spec["campaign_id"],
        "observer_id": "p1-independent-root-observer-v1",
        "observation_complete": True, "clock_id": "CLOCK_BOOTTIME",
        "boot_id": sample.boot_id,
        "source_manifest_sha256": spec["source_manifest_sha256"],
        "policy_sha256": spec["policy_sha256"],
        "strategy_sha256": spec["strategy_sha256"],
        "producer": dict(producer), "production_mode": production_mode,
        "observation_evidence": evidence,
        **_boundary(),
    }


def _validate_observation(document: dict[str, Any]) -> None:
    schema = document.get("schema")
    mapping = {
        SERVICE_SCHEMA: (SERVICE_FIELDS, "SERVICE"),
        CAMPAIGN_CONTINUITY_SCHEMA: (
            CAMPAIGN_CONTINUITY_FIELDS, "CAMPAIGN_CONTINUITY"),
        FAULT_SCHEMA: (FAULT_FIELDS, "FAULT"),
        AUTHORITY_SCHEMA: (AUTHORITY_FIELDS, "AUTHORITY"),
        CLEANUP_SCHEMA: (CLEANUP_FIELDS, "CLEANUP"),
    }
    _require(schema in mapping, "P1_OBSERVER_OUTPUT_SCHEMA_INVALID")
    fields, kind = mapping[schema]
    _exact(document, fields, "P1_OBSERVER_OUTPUT_SCHEMA_INVALID")
    _validate_seal(document, "P1_OBSERVER_OUTPUT_SCHEMA_INVALID")
    _reject_authority(document, "P1_OBSERVER_OUTPUT_AUTHORITY")
    _require(document.get("version") == VERSION and
             document.get("status") == "COMPLETE" and
             document.get("observation_complete") is True and
             document.get("clock_id") == "CLOCK_BOOTTIME" and
             BOOT_ID.fullmatch(str(document.get("boot_id"))) is not None,
             "P1_OBSERVER_OUTPUT_SCHEMA_INVALID")
    producer = _exact(
        document.get("producer"), PRODUCER_FIELDS,
        "P1_OBSERVER_OUTPUT_PRODUCER_INVALID")
    _require(type(producer.get("path")) is str and
             type(producer.get("file_sha256")) is str and
             DIGEST.fullmatch(producer["file_sha256"]) is not None and
             producer["file_sha256"] != "sha256:" + "0" * 64 and
             document.get("production_mode") in {
                 PRODUCTION_MODE, REHEARSAL_MODE},
             "P1_OBSERVER_OUTPUT_PRODUCER_INVALID")
    if document["production_mode"] == PRODUCTION_MODE:
        _require(producer["path"] == str(INSTALLED_EXECUTABLE),
                 "P1_OBSERVER_OUTPUT_PRODUCER_INVALID")
    validate_evidence(
        document.get("observation_evidence"), kind,
        "P1_OBSERVER_OUTPUT_EVIDENCE_INVALID")


class IndependentObserver:
    """Construct exact raw observer receipts from a read-only host view."""

    def __init__(
        self, host: Host, *, layout: Layout = Layout(),
        expected_uid: int = ROOT_UID, expected_gid: int = ROOT_GID,
        producer: ProducerBinding | None = None,
    ) -> None:
        self.host = host
        self.layout = layout
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid
        self._producer_binding = producer
        if producer is not None:
            _require(type(host) is ReadOnlyHost and
                     producer.path == INSTALLED_EXECUTABLE,
                     "P1_OBSERVER_PRODUCTION_HOST_REQUIRED")
            self.producer = producer.reference
            self.production_mode = PRODUCTION_MODE
        else:
            try:
                source = Path(__file__).resolve(strict=True)
                payload = source.read_bytes()
                self.producer = {
                    "path": str(source), "file_sha256": digest_bytes(payload)}
            except OSError as error:
                raise ObserverError("P1_OBSERVER_REHEARSAL_IMAGE_INVALID") \
                    from error
            self.production_mode = REHEARSAL_MODE

    def _spec(self, path: Path) -> tuple[Snapshot, dict[str, Any]]:
        snapshot = load_snapshot(
            path, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        return snapshot, validate_spec(snapshot.document)

    @staticmethod
    def _formal(
        spec: Mapping[str, Any], campaign_id: str,
    ) -> dict[str, Any]:
        value = next((item for item in spec["formal_campaigns"]
                      if item["campaign_id"] == campaign_id), None)
        _require(value is not None,
                 "P1_OBSERVER_FORMAL_CAMPAIGN_NOT_FROZEN")
        return value

    def _service_documents(
        self, spec: Mapping[str, Any], campaign_id: str, sample: ClockSample,
    ) -> tuple[
        ObservedDocument, ObservedDocument, ObservedDocument,
        dict[str, Any], dict[str, Any], dict[str, Any], int, CommittedExport,
    ]:
        formal = self._formal(spec, campaign_id)
        round_number = _round(campaign_id)
        marker = self.host.document(
            self.layout.formal_marker(round_number), expected_uid=ROOT_UID)
        status = self.host.document(
            self.layout.controller_status(campaign_id), expected_uid=1000)
        export_bundle = self.host.committed_export(self.layout.export_root)
        lease = export_bundle.lease
        marker_value = marker.document
        status_value = status.document
        lease_value = lease.document
        reason = "P1_OBSERVER_SERVICE_BINDING_INVALID"
        _exact(marker_value, FORMAL_MARKER_FIELDS, reason)
        _exact(status_value, CONTROLLER_STATUS_FIELDS, reason)
        _exact(lease_value, LEASE_FIELDS, reason)
        for value in (marker_value, status_value, lease_value):
            _validate_seal(value, reason)
            _reject_authority(value, reason)
        environment = _exact(
            marker_value.get("environment"), MARKER_ENVIRONMENT_FIELDS,
            reason)
        epoch = marker_value.get("execution_service_epoch")
        fence = marker_value.get("execution_service_fencing_generation")
        generation = lease_value.get("lease_generation")
        for field in (
            "collector_sha256", "exporter_sha256", "heptactl_sha256",
            "gateway_sha256", "custodian_sha256", "observer_sha256",
            "host_controller_sha256", "domain_config_sha256",
            "gateway_profile_sha256", "gateway_process_profile_sha256",
        ):
            _digest(environment.get(field), reason)
        for field in (
            "audit_journal_device", "audit_journal_inode",
            "gateway_main_pid",
            "gateway_exec_main_start_timestamp_monotonic_us",
            "gateway_socket_device", "gateway_socket_inode",
        ):
            _integer(environment.get(field), reason)
        _require(
            type(environment.get("gateway_invocation_id")) is str and
            INVOCATION_ID.fullmatch(
                environment["gateway_invocation_id"]) is not None,
            reason)
        _require(
            marker_value.get("schema") ==
                "hepta.p1-shadow-admission-authority-marker.v1" and
            marker_value.get("version") == VERSION and
            marker_value.get("status") == "ACTIVE" and
            marker_value.get("campaign_id") == campaign_id and
            marker_value.get("policy_file_sha256") ==
                formal["policy_file_sha256"] and
            marker_value.get("policy_body_sha256") ==
                formal["policy_body_sha256"] and
            type(marker_value.get("marker_created_at_ms")) is int and
            type(marker_value.get("expires_at_ms")) is int and
            marker_value["marker_created_at_ms"] <= sample.wall_ms <
                marker_value["expires_at_ms"] and
            type(epoch) is str and IDENTIFIER.fullmatch(epoch) is not None and
            type(fence) is int and fence >= 0 and
            environment.get("boot_id") == sample.boot_id and
            status_value.get("schema") ==
                "hepta.p1-shadow-observer-controller-status.v1" and
            status_value.get("version") == VERSION and
            status_value.get("campaign_id") == campaign_id and
            status_value.get("controller_uid") == 1000 and
            status_value.get("controller_gid") == 1000 and
            status_value.get("locked_execution_service_epoch") == epoch and
            status_value.get("locked_execution_service_fencing_generation") ==
                fence and
            status_value.get("last_export_receipt_body_sha256") ==
                export_bundle.receipt.body_sha256 and
            status_value.get("last_snapshot_body_sha256") ==
                export_bundle.snapshot.body_sha256 and
            status_value.get("last_lease_generation") == generation and
            lease_value.get("schema") ==
                "hepta.shadow-watch-lease-receipt.v1" and
            lease_value.get("version") == VERSION and
            lease_value.get("domain_id") == "alpha" and
            lease_value.get("agent_id") == "alpha" and
            lease_value.get("boundary") == "WATCH" and
            lease_value.get("accepted") is True and
            lease_value.get("reason_code") == "OK" and
            type(generation) is int and generation >= 1 and
            type(lease_value.get("expires_at_ms")) is int and
            sample.wall_ms < lease_value["expires_at_ms"], reason)
        return (
            marker, status, lease, marker_value, status_value, environment,
            generation, export_bundle)

    def service(
        self, spec_path: Path, campaign_id: str,
        transition_fault_id: str | None = None,
    ) -> dict[str, Any]:
        spec_snapshot, spec = self._spec(spec_path)
        sample = self.host.clock()
        marker, status, lease, marker_value, status_value, environment, \
            generation, export_bundle = self._service_documents(
                spec, campaign_id, sample)
        round_number = _round(campaign_id)
        reader_unit = f"hepta-p1-shadow-reader-round{round_number}.service"
        host_unit = f"hepta-p1-shadow-host-round{round_number}.service"
        names = (*WATCH_SERVICE_UNITS, reader_unit, host_unit, *PAPER_UNITS)
        units = [self.host.unit(name) for name in sorted(set(names))]
        by_name = {item["unit"]: item for item in units}
        reader = by_name[reader_unit]
        gateway = by_name[GATEWAY_UNIT]
        active_pids = sorted({
            item["main_pid"] for item in units if item["main_pid"] > 1 and
            item["active_state"] == "active"
        })
        processes = [self.host.process(pid) for pid in active_pids]
        reader_bound = (
            reader["active_state"] != "active" or
            reader["main_pid"] == status_value.get("controller_pid"))
        gateway_bound = (
            gateway["active_state"] != "active" or
            (gateway["main_pid"] == environment.get("gateway_main_pid") and
             gateway["invocation_id"] ==
                environment.get("gateway_invocation_id") and
             gateway["exec_main_start_timestamp_monotonic_us"] ==
                environment.get(
                    "gateway_exec_main_start_timestamp_monotonic_us")))
        _require(reader_bound and gateway_bound,
                 "P1_OBSERVER_SERVICE_PROCESS_BINDING_INVALID")
        required_active = {
            GATEWAY_UNIT: "running",
            "hepta-shadow-watch-custodian@alpha.service": "running",
            "hepta-shadow-watch-collector@alpha.timer": "waiting",
            reader_unit: "running", host_unit: "running",
        }
        continuity_ok = all(
            by_name[name]["load_state"] == "loaded" and
            by_name[name]["active_state"] == "active" and
            by_name[name]["sub_state"] == substate
            for name, substate in required_active.items())
        paper_inactive = all(
            by_name[name]["active_state"] == "inactive" and
            by_name[name]["main_pid"] == 0 for name in PAPER_UNITS)
        audit_ok = (
            status_value.get("state") == "RUNNING" and
            status_value.get("observer_status") == "RUNNING" and
            status_value.get("reason") is None)
        paths = [
            marker.path_identity, status.path_identity,
            export_bundle.commit.path_identity,
            export_bundle.snapshot.path_identity, lease.path_identity,
            export_bundle.receipt.path_identity,
            self.host.path(Path("/run/hepta-agent-alpha/tools.sock")),
        ]
        evidence = _evidence(
            "SERVICE", sample, units=units, processes=processes, paths=paths,
            broker=None)
        document = seal({
            **_observation_header(
                SERVICE_SCHEMA, spec, sample, evidence,
                self.producer, self.production_mode,
                expires_at_ms=min(
                    sample.wall_ms + OBSERVATION_LIFETIME_MS,
                    marker_value["expires_at_ms"],
                    lease.document["expires_at_ms"])),
            "observed_boottime_ns": sample.boottime_ns,
            "service_epoch": marker_value["execution_service_epoch"],
            "fencing_generation":
                marker_value["execution_service_fencing_generation"],
            "lease_generation": generation,
            "transition_fault_id": transition_fault_id,
            "continuity_ok": continuity_ok, "audit_ok": audit_ok,
            "cleanup_ok": paper_inactive,
        })
        _validate_observation(document)
        _assert_snapshot_unchanged(
            spec_snapshot, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        return document

    def campaign_continuity(
        self, spec_path: Path, runtime_path: Path, slot_index: int,
        transition_fault_id: str | None = None,
    ) -> dict[str, Any]:
        """Observe persistent WATCH health without a formal ACTIVE marker."""

        spec_snapshot, spec = self._spec(spec_path)
        sample = self.host.clock()
        reason = "P1_OBSERVER_CAMPAIGN_CONTINUITY_BINDING_INVALID"
        runtime_snapshot = load_snapshot(
            runtime_path, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        runtime = validate_campaign_runtime(
            runtime_snapshot.document, spec_snapshot, spec, sample,
            expected_observer_sha256=(
                self.producer["file_sha256"]
                if self.production_mode == PRODUCTION_MODE else None))
        slot = _integer(slot_index, reason)
        origin = runtime["formal_campaigns"][0]["launcher_dispatch_at_ms"]
        end = runtime["formal_campaigns"][-1]["teardown_deadline_ms"]
        cadence = runtime["observer_cadence_ms"]
        final_slot = math.ceil((end - origin) / cadence)
        _require(origin < end and 0 <= slot <= final_slot, reason)
        scheduled = min(origin + slot * cadence, end)
        _require(
            scheduled <= sample.wall_ms <=
                scheduled + runtime["maximum_slot_lateness_ms"] and
            (slot != final_slot or scheduled == end) and
            (slot != 0 or scheduled == origin) and
            cadence * 1_000_000 <= spec["maximum_checkpoint_gap_ns"], reason)
        activation = self.host.document(
            self.layout.activation_receipt, expected_uid=ROOT_UID)
        export_bundle = self.host.committed_export(self.layout.export_root)
        lease = export_bundle.lease
        activation_value = activation.document
        lease_value = lease.document
        _exact(activation_value, ACTIVATION_RECEIPT_FIELDS, reason)
        _exact(lease_value, LEASE_FIELDS, reason)
        for value in (activation_value, lease_value):
            _validate_seal(value, reason)
            _reject_authority(value, reason)
        generation = _integer(
            lease_value.get("lease_generation"), reason, 1)
        previous_generation = _integer(
            lease_value.get("previous_lease_generation"), reason)
        previous_body = _digest(
            lease_value.get("previous_receipt_body_sha256"), reason)
        _require(
            transition_fault_id is None or
            (type(transition_fault_id) is str and
             IDENTIFIER.fullmatch(transition_fault_id) is not None), reason)
        _require(slot != 0 or transition_fault_id is None, reason)
        _require(
            activation_value.get("schema") ==
                "hepta.p1-watch-activation-receipt.v4" and
            activation_value.get("version") == 4 and
            activation_value.get("status") == "WATCH_GATEWAY_ACTIVATED" and
            activation_value.get("round") == 114 and
            activation_value.get("domain") == "alpha" and
            activation_value.get("boot_id") == sample.boot_id and
            activation_value.get("gateway_activated") is True and
            activation_value.get("gateway_profile_loaded") is True and
            activation_value.get("gateway_contract_binding_loaded") is True and
            activation_value.get("broker_loaded_source_attested") is True and
            activation_value.get("broker_deny_all_continuity_attested") is True and
            activation_value.get("kill_switch_engaged") is True and
            activation_value.get("watch_authority_provisioned") is False and
            activation_value.get("campaign_launched") is False and
            activation_value.get("admission_prerequisite_satisfied") is True and
            activation_value.get("paper_prerequisite_satisfied") is False and
            lease_value.get("schema") ==
                "hepta.shadow-watch-lease-receipt.v1" and
            lease_value.get("version") == VERSION and
            lease_value.get("domain_id") == "alpha" and
            lease_value.get("agent_id") == "alpha" and
            lease_value.get("boundary") == "WATCH" and
            lease_value.get("accepted") is True and
            lease_value.get("reason_code") == "OK" and
            previous_generation == generation - 1 and
            type(lease_value.get("expires_at_ms")) is int and
            sample.wall_ms < lease_value["expires_at_ms"], reason)
        _validate_activation_install_lineage(
            activation_value.get("shadow_install_evidence"),
            spec["source_manifest_sha256"], reason)
        _validate_activation_predecessor_lineage(
            activation_value.get("predecessor_activation_success"),
            activation_value.get("predecessor_activation_failure"), reason)
        activation_gateway = validate_activation_gateway_after(
            activation_value.get("gateway_after"), reason)

        reconcile_timer = "hepta-p1-watch-activation-reconcile.timer"
        names = (*WATCH_SERVICE_UNITS, reconcile_timer, *PAPER_UNITS)
        units = [self.host.unit(name) for name in sorted(set(names))]
        by_name = {item["unit"]: item for item in units}
        required = {
            GATEWAY_UNIT: "running",
            "hepta-shadow-watch-custodian@alpha.service": "running",
            "hepta-shadow-watch-collector@alpha.timer": "waiting",
            reconcile_timer: "waiting",
        }
        process_units = {
            GATEWAY_UNIT,
            "hepta-shadow-watch-custodian@alpha.service",
        }
        persistent_stack_ok = all(
            by_name[name]["load_state"] == "loaded" and
            by_name[name]["active_state"] == "active" and
            by_name[name]["sub_state"] == substate and
            (by_name[name]["main_pid"] > 1
             if name in process_units else by_name[name]["main_pid"] == 0)
            for name, substate in required.items())
        active_pids = sorted({
            item["main_pid"] for item in units if item["main_pid"] > 1 and
            item["active_state"] == "active"
        })
        processes = [self.host.process(pid) for pid in active_pids]
        process_by_pid = {item["pid"]: item for item in processes}
        gateway_process = process_by_pid.get(by_name[GATEWAY_UNIT]["main_pid"])
        _require(gateway_process is not None, reason)
        gateway_executable = self.host.path(GATEWAY_EXECUTABLE, "bytes")
        gateway_profile = self.host.path(GATEWAY_PROFILE, "bytes")
        gateway_domain_config = self.host.path(
            GATEWAY_DOMAIN_CONFIG, "bytes")
        tool_socket = self.host.path(GATEWAY_TOOL_SOCKET)
        supervisor_socket = self.host.path(GATEWAY_SUPERVISOR_SOCKET)
        kill = self.host.path(self.layout.kill_switch, "bytes")
        campaign_socket = self.host.path(self.layout.campaign_socket)
        paths = [
            activation.path_identity, export_bundle.commit.path_identity,
            export_bundle.snapshot.path_identity, lease.path_identity,
            export_bundle.receipt.path_identity,
            gateway_executable, gateway_profile, gateway_domain_config,
            tool_socket, supervisor_socket, kill, campaign_socket,
        ]
        validate_live_gateway_binding(
            activation_gateway, gateway_unit=by_name[GATEWAY_UNIT],
            gateway_process=gateway_process,
            gateway_executable=gateway_executable,
            gateway_profile=gateway_profile,
            gateway_domain_config=gateway_domain_config,
            tool_socket=tool_socket, supervisor_socket=supervisor_socket,
            initial=slot == 0, reason=reason)
        broker = self.host.broker()
        paper_active = [
            item for item in units if item["unit"] in PAPER_UNITS and
            item["active_state"] != "inactive"
        ]
        kill_engaged = (
            kill["present"] and kill["file_type"] == "regular" and
            kill["content_file_sha256"] == digest_bytes(b"engaged"))
        zero_exposure = (
            broker["deny_all"] is True and
            broker["authorized_connector_count"] == 0 and
            broker["authorized_uids"] == [] and
            broker["protected_port_count"] == 4 and not paper_active and
            campaign_socket["present"] is False and kill_engaged)
        persistent_stack_ok = (
            persistent_stack_ok and tool_socket["present"] is True and
            tool_socket["file_type"] == "socket" and
            supervisor_socket["present"] is True and
            supervisor_socket["file_type"] == "socket")
        evidence = _evidence(
            "CAMPAIGN_CONTINUITY", sample, units=units,
            processes=processes, paths=paths, broker=broker)
        document = seal({
            **_observation_header(
                CAMPAIGN_CONTINUITY_SCHEMA, spec, sample, evidence,
                self.producer, self.production_mode,
                expires_at_ms=min(
                    sample.wall_ms + OBSERVATION_LIFETIME_MS,
                    lease_value["expires_at_ms"])),
            "observed_boottime_ns": sample.boottime_ns,
            "freeze_bundle": dict(spec["freeze_bundle"]),
            "campaign_runtime": _artifact_reference(runtime_snapshot),
            "continuity_slot_index": slot,
            "continuity_scheduled_at_ms": scheduled,
            "continuity_origin_ms": origin,
            "continuity_end_ms": end,
            "continuity_cadence_ms": cadence,
            "continuity_final_slot": final_slot,
            "continuity_is_final": slot == final_slot,
            "catch_up": False,
            "activation_receipt": _artifact_reference(activation),
            "activation_receipt_document": activation_value,
            "export_commit": _artifact_reference(export_bundle.commit),
            "export_commit_document": export_bundle.commit.document,
            "export_snapshot": _artifact_reference(export_bundle.snapshot),
            "lease_receipt": _artifact_reference(lease),
            "lease_receipt_document": lease_value,
            "export_receipt": _artifact_reference(export_bundle.receipt),
            "lease_generation": generation,
            "previous_lease_generation": previous_generation,
            "previous_lease_receipt_body_sha256": previous_body,
            "gateway_identity": by_name[GATEWAY_UNIT],
            "gateway_process_identity": gateway_process,
            "gateway_executable_identity": gateway_executable,
            "gateway_profile_identity": gateway_profile,
            "gateway_domain_config_identity": gateway_domain_config,
            "supervisor_socket_identity": supervisor_socket,
            "custodian_identity": by_name[
                "hepta-shadow-watch-custodian@alpha.service"],
            "collector_timer_identity": by_name[
                "hepta-shadow-watch-collector@alpha.timer"],
            "activation_reconcile_timer_identity": by_name[reconcile_timer],
            "tool_socket_identity": tool_socket,
            "transition_fault_id": transition_fault_id,
            "persistent_stack_ok": persistent_stack_ok,
            "lease_chain_ok": previous_generation == generation - 1,
            "connector_count": broker["authorized_connector_count"],
            "authorized_uids": broker["authorized_uids"],
            "paper_unit_active_count": len(paper_active),
            "campaign_socket_present": campaign_socket["present"],
            "kill_switch_engaged": kill_engaged,
            "zero_exposure": zero_exposure,
        })
        _validate_observation(document)
        _assert_snapshot_unchanged(
            spec_snapshot, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        _assert_snapshot_unchanged(
            runtime_snapshot, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        reopened_activation = self.host.document(
            self.layout.activation_receipt, expected_uid=ROOT_UID)
        reopened_export = self.host.committed_export(self.layout.export_root)
        _require(
            _artifact_reference(reopened_activation) ==
                _artifact_reference(activation) and
            reopened_activation.path_identity == activation.path_identity and
            reopened_export == export_bundle,
            "P1_OBSERVER_CAMPAIGN_CONTINUITY_INPUT_DRIFT")
        return document

    def authority(self, spec_path: Path) -> dict[str, Any]:
        spec_snapshot, spec = self._spec(spec_path)
        sample = self.host.clock()
        units = [self.host.unit(name)
                 for name in sorted({BROKER_UNIT, *PAPER_UNITS})]
        processes = [self.host.process(pid) for pid in sorted({
            item["main_pid"] for item in units if item["main_pid"] > 1
        })]
        kill = self.host.path(self.layout.kill_switch, "bytes")
        campaign_socket = self.host.path(self.layout.campaign_socket)
        paths = [kill, campaign_socket]
        broker = self.host.broker()
        paper_active = [item for item in units
                        if item["unit"] in PAPER_UNITS and
                        item["active_state"] != "inactive"]
        socket_count = int(campaign_socket["present"])
        broker_unit = next(item for item in units
                           if item["unit"] == BROKER_UNIT)
        kill_expected = digest_bytes(b"engaged")
        kill_engaged = (
            kill["present"] and kill["file_type"] == "regular" and
            kill["content_file_sha256"] == kill_expected)
        uncertain = not (
            broker_unit["active_state"] == "active" and
            broker_unit["sub_state"] == "running" and
            broker["protected_port_count"] == 4 and
            broker["deny_all"] is True)
        connector_count = broker["authorized_connector_count"]
        local_boundary_safe = (
            not uncertain and connector_count == 0 and
            broker["authorized_uids"] == [] and not paper_active and
            socket_count == 0 and kill_engaged)
        evidence = _evidence(
            "AUTHORITY", sample, units=units, processes=processes,
            paths=paths, broker=broker)
        document = seal({
            **_observation_header(
                AUTHORITY_SCHEMA, spec, sample, evidence,
                self.producer, self.production_mode),
            "observed_boottime_ns": sample.boottime_ns,
            "connector_count": connector_count,
            "authorized_uids": broker["authorized_uids"],
            "paper_unit_active_count": len(paper_active),
            "campaign_socket_present": campaign_socket["present"],
            "kill_switch_engaged": kill_engaged,
            "local_boundary_safe": local_boundary_safe,
            "local_boundary_uncertain": uncertain,
            "observation_scope": "LOCAL_HOST_BOUNDARY_ONLY",
            "authoritative_account_state_observed": False,
        })
        _validate_observation(document)
        _assert_snapshot_unchanged(
            spec_snapshot, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        return document

    def cleanup(
        self, spec_path: Path, *, subject_type: str, subject_id: str,
        formal_campaign_id: str | None = None,
        fault_receipt_path: Path | None = None,
    ) -> dict[str, Any]:
        spec_snapshot, spec = self._spec(spec_path)
        _require(subject_type in {"LAUNCHER", "FAULT", "FINAL"},
                 "P1_OBSERVER_CLEANUP_SUBJECT_INVALID")
        _identifier(subject_id, "P1_OBSERVER_CLEANUP_SUBJECT_INVALID")
        sample = self.host.clock()
        dynamic: tuple[str, ...] = ()
        fault_post: dict[str, Any] | None = None
        fault_snapshot: Snapshot | None = None
        if subject_type == "LAUNCHER":
            _require(formal_campaign_id is not None,
                     "P1_OBSERVER_CLEANUP_CAMPAIGN_REQUIRED")
            self._formal(spec, formal_campaign_id)
            round_number = _round(formal_campaign_id)
            dynamic = (
                f"hepta-p1-shadow-reader-round{round_number}.service",
                f"hepta-p1-shadow-host-round{round_number}.service",
                f"hepta-p1-shadow-admission-round{round_number}.service",
            )
        elif subject_type == "FAULT":
            _require(fault_receipt_path is not None,
                     "P1_OBSERVER_CLEANUP_FAULT_RECEIPT_REQUIRED")
            fault_snapshot = load_snapshot(
                fault_receipt_path, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid)
            fault_post = self._validate_injection(
                fault_snapshot, spec, None, sample)[1]
        final_watch = WATCH_UNITS if subject_type == "FINAL" else ()
        units = [self.host.unit(name) for name in
                 sorted(set((*dynamic, *final_watch, *PAPER_UNITS)))]
        processes = [self.host.process(pid) for pid in sorted({
            item["main_pid"] for item in units if item["main_pid"] > 1
        })]
        observed_paths = (
            self.layout.token, self.layout.fence, self.layout.session_lease,
            self.layout.export_root, self.layout.custodian_transaction,
            self.layout.campaign_socket,
        )
        paths = [self.host.path(path) for path in observed_paths]
        by_path = {item["path"]: item for item in paths}
        paper_active = sum(
            item["active_state"] != "inactive" for item in units
            if item["unit"] in PAPER_UNITS)
        session_count = sum(by_path[str(path)]["present"] for path in (
            self.layout.token, self.layout.fence, self.layout.session_lease))
        watch_count = int(
            by_path[str(self.layout.custodian_transaction)]["present"])
        export_count = int(by_path[str(self.layout.export_root)]["present"])
        campaign_socket = bool(
            by_path[str(self.layout.campaign_socket)]["present"])
        dynamic_active = sum(
            item["active_state"] != "inactive" for item in units
            if item["unit"] in dynamic)
        if subject_type == "FINAL":
            watch_count += sum(
                item["active_state"] != "inactive" for item in units
                if item["unit"] in WATCH_UNITS)
        if subject_type == "FAULT" and fault_post is not None:
            watch_count = fault_post["residue_count"]
            session_count = 0
            export_count = 0
        errors: list[str] = []
        if dynamic_active:
            errors.append("TRANSIENT_UNIT_ACTIVE")
        if watch_count:
            errors.append("WATCH_AUTHORITY_RESIDUE")
        if export_count:
            errors.append("WATCH_EXPORT_RESIDUE")
        if session_count:
            errors.append("SESSION_AUTHORITY_RESIDUE")
        if paper_active:
            errors.append("PAPER_UNIT_ACTIVE")
        if campaign_socket:
            errors.append("CAMPAIGN_SOCKET_PRESENT")
        complete = not errors
        evidence = _evidence(
            "CLEANUP", sample, units=units, processes=processes, paths=paths,
            broker=None)
        document = seal({
            **_observation_header(
                CLEANUP_SCHEMA, spec, sample, evidence,
                self.producer, self.production_mode),
            "observed_boottime_ns": sample.boottime_ns,
            "subject_type": subject_type, "subject_id": subject_id,
            "watch_authority_count": watch_count,
            "export_residue_count": export_count,
            "session_authority_count": session_count,
            "paper_unit_active_count": paper_active,
            "campaign_socket_present": campaign_socket,
            "cleanup_complete": complete,
            "cleanup_uncertain": False, "errors": errors,
        })
        _validate_observation(document)
        _assert_snapshot_unchanged(
            spec_snapshot, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        if fault_snapshot is not None:
            _assert_snapshot_unchanged(
                fault_snapshot, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid)
        return document

    def _validate_injection(
        self, snapshot: Snapshot, spec: Mapping[str, Any],
        plan: Sequence[dict[str, Any]] | None, sample: ClockSample,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        reason = "P1_OBSERVER_FAULT_INJECTION_RECEIPT_INVALID"
        value = snapshot.document
        _exact(value, INJECTION_FIELDS, reason)
        _validate_seal(value, reason)
        _reject_authority(value, reason)
        fault_type = value.get("fault_type")
        target_id = value.get("target_id")
        _require(
            value.get("schema") == INJECTION_SCHEMA and
            value.get("version") == VERSION and
            value.get("status") == "COMPLETE" and
            value.get("campaign_id") == spec.get("campaign_id") and
            value.get("source_manifest_sha256") ==
                spec.get("source_manifest_sha256") and
            value.get("policy_sha256") == spec.get("policy_sha256") and
            value.get("strategy_sha256") == spec.get("strategy_sha256") and
            fault_type in ALLOWED_FAULT_TYPES and
            target_id == FAULT_TARGET_IDS[fault_type] and
            value.get("clock_id") == "CLOCK_BOOTTIME" and
            value.get("boot_id") == sample.boot_id and
            value.get("injector_uid") == ROOT_UID and
            value.get("injector_gid") == ROOT_GID and
            value.get("injection_scope") == "P1_DECLARED_FAULT_ONLY" and
            value.get("production_mode") ==
                "PRODUCTION_ROOT_FAULT_INJECTION" and
            type(value.get("injection_performed")) is bool and
            type(value.get("recovery_complete")) is bool and
            type(value.get("cleanup_complete")) is bool,
            reason)
        _identifier(value.get("fault_id"), reason)
        _identifier(value.get("injector_id"), reason)
        producer = _exact(value.get("producer"), PRODUCER_FIELDS, reason)
        _require(
            producer.get("path") ==
                "/usr/libexec/hepta-p1-safety-soak-root-fault-injector",
            reason)
        _digest(producer.get("file_sha256"), reason)
        pins_reference = _exact(
            value.get("pins_reference"), FREEZE_REFERENCE_FIELDS, reason)
        _canonical_path(Path(pins_reference.get("path")), reason)
        _digest(pins_reference.get("file_sha256"), reason)
        _digest(pins_reference.get("body_sha256"), reason)
        _integer(value.get("journal_predecessor_sequence"), reason, 1)
        _digest(value.get("journal_predecessor_body_sha256"), reason)
        _digest(value.get("action_receipt_sha256"), reason)
        issued = _integer(value.get("issued_at_ms"), reason)
        expires = _integer(value.get("expires_at_ms"), reason)
        _require(issued <= sample.wall_ms + MAXIMUM_CLOCK_SKEW_MS and
                 sample.wall_ms < expires and issued < expires and
                 sample.wall_ms - issued <= MAXIMUM_OBSERVATION_AGE_MS,
                 reason)
        planned = _integer(value.get("planned_injection_boottime_ns"), reason)
        actual = _integer(value.get("actual_injection_boottime_ns"), reason,
                          planned)
        recovered = _integer(value.get("recovered_boottime_ns"), reason,
                             actual)
        maximum_recovery = _integer(value.get("maximum_recovery_ns"), reason,
                                    1)
        _require(recovered <= actual + maximum_recovery and
                 recovered <= sample.boottime_ns + MAXIMUM_BOOTTIME_SKEW_NS,
                 reason)
        expected: dict[str, Any] | None = None
        if plan is not None:
            expected = next((item for item in plan
                             if item["fault_id"] == value["fault_id"]), None)
            _require(expected is not None and
                     expected["fault_type"] == fault_type and
                     expected["target_id"] == target_id and
                     expected["inject_at_boottime_ns"] == planned and
                     expected["maximum_recovery_ns"] == maximum_recovery and
                     actual <= planned +
                        expected["maximum_injection_lateness_ns"], reason)
        pre = validate_fault_identity(
            value.get("pre_identity"), "PRE", target_id, fault_type, reason)
        post = validate_fault_identity(
            value.get("post_identity"), "POST", target_id, fault_type, reason)
        for identity in (pre, post):
            broker = identity["broker_deny_all"]
            _require(
                broker is not None and broker["deny_all"] is True and
                broker["authorized_connector_count"] == 0 and
                broker["authorized_uids"] == [] and
                broker["protected_port_count"] > 0 and
                0 <= identity["observed_boottime_ns"] -
                    broker["checked_boottime_ns"] <=
                    MAXIMUM_BOOTTIME_SKEW_NS,
                reason)
        _require(
            pre["boot_id"] == post["boot_id"] == sample.boot_id and
            pre["observed_boottime_ns"] <= actual <= recovered <=
                post["observed_boottime_ns"] <=
                sample.boottime_ns + MAXIMUM_BOOTTIME_SKEW_NS and
            all(type(value.get(field)) is bool for field in (
                "authority_failure", "audit_failure", "cleanup_failure")),
            reason)
        _require(
            value["cleanup_complete"] is not True or
            post["residue_count"] == 0,
            reason)
        self._validate_fault_transition(
            fault_type, pre, post, actual_ns=actual,
            recovered_ns=recovered,
            recovery_complete=value["recovery_complete"], reason=reason)
        return value, post

    def _validate_fault_transition(
        self,
        fault_type: str, pre: Mapping[str, Any], post: Mapping[str, Any],
        *, actual_ns: int, recovered_ns: int, recovery_complete: bool,
        reason: str,
    ) -> None:
        pre_triple = (
            pre["service_epoch"], pre["fencing_generation"],
            pre["lease_generation"])
        post_triple = (
            post["service_epoch"], post["fencing_generation"],
            post["lease_generation"])
        fixture_fields = (
            "fixture_generation", "fixture_expires_boottime_ns",
            "fixture_valid",
        )
        if fault_type not in {"TOKEN_LOSS", "LEASE_EXPIRY"}:
            _require(all(pre[field] is None and post[field] is None
                         for field in fixture_fields), reason)
        if fault_type == "SERVICE_RESTART":
            _require(
                pre_triple[0] != post_triple[0] and
                pre_triple[1] == post_triple[1] and
                type(pre_triple[2]) is int and
                post_triple[2] in {pre_triple[2], pre_triple[2] + 1},
                reason)
            _require(
                len(pre["systemd_units"]) ==
                    len(post["systemd_units"]) == 1 and
                pre["systemd_units"][0]["unit"] ==
                    post["systemd_units"][0]["unit"] == GATEWAY_UNIT,
                reason)
            before = pre["systemd_units"][0]
            after = post["systemd_units"][0]
            for field in (
                "invocation_id", "main_pid",
                "exec_main_start_timestamp_monotonic_us",
            ):
                _require(before[field] != after[field], reason)
        elif fault_type in {"TOKEN_LOSS", "LEASE_EXPIRY"}:
            _require(pre_triple[0:2] == post_triple[0:2] and
                     type(pre_triple[2]) is int and
                     type(post_triple[2]) is int and
                     post_triple[2] in {
                         pre_triple[2], pre_triple[2] + 1}, reason)
        else:
            _require(pre_triple == post_triple, reason)
        if fault_type in {"PROCESS_KILL", "EVIDENCE_WRITER_CRASH"}:
            _require(len(pre["processes"]) ==
                     len(post["processes"]) == 1, reason)
            before = pre["processes"][0]
            after = post["processes"][0]
            _require(
                (before["pid"], before["starttime_ticks"]) !=
                    (after["pid"], after["starttime_ticks"]) and
                (before["exe_device"], before["exe_inode"]) ==
                    (after["exe_device"], after["exe_inode"]), reason)
        if fault_type in {"TOKEN_LOSS", "LEASE_EXPIRY"}:
            fixture = TOKEN_FAULT_FIXTURE if fault_type == "TOKEN_LOSS" \
                else LEASE_FAULT_FIXTURE
            before = next((item for item in pre["paths"]
                           if item["path"] == str(fixture)), None)
            after = next((item for item in post["paths"]
                          if item["path"] == str(fixture)), None)
            _require(before is not None and after is not None and
                     before["present"] is True and
                     before["content_file_sha256"] is not None and
                     (before["device"], before["inode"],
                      before["content_file_sha256"]) !=
                     (after["device"], after["inode"],
                      after["content_file_sha256"]) and
                     type(pre["fixture_generation"]) is int and
                     type(post["fixture_generation"]) is int and
                     type(pre["fixture_expires_boottime_ns"]) is int and
                     type(post["fixture_expires_boottime_ns"]) is int and
                     pre["fixture_valid"] is True, reason)
            if fault_type == "LEASE_EXPIRY":
                _require(
                    pre["fixture_expires_boottime_ns"] <= actual_ns,
                    reason)
            if recovery_complete:
                _require(
                    post["fixture_valid"] is True and
                    post["fixture_generation"] ==
                        pre["fixture_generation"] + 1 and
                    post["fixture_expires_boottime_ns"] > recovered_ns,
                    reason)
            else:
                _require(post["fixture_valid"] is False, reason)
        if fault_type == "NETWORK_DENY_RELOAD":
            _require(
                     len(pre["systemd_units"]) ==
                        len(post["systemd_units"]) == 1 and
                     pre["systemd_units"][0]["unit"] ==
                        post["systemd_units"][0]["unit"] == BROKER_UNIT and
                     pre["broker_deny_all"] is not None and
                     post["broker_deny_all"] is not None and
                     pre["broker_deny_all"]["deny_all"] is True and
                     post["broker_deny_all"]["deny_all"] is True and
                     pre["broker_deny_all"]["helper_file_sha256"] ==
                        post["broker_deny_all"]["helper_file_sha256"] and
                     pre["broker_deny_all"]["policy_sha256"] ==
                        post["broker_deny_all"]["policy_sha256"] and
                     pre["broker_deny_all"]["checked_boottime_ns"] <
                        post["broker_deny_all"]["checked_boottime_ns"],
                     reason)
            before = pre["systemd_units"][0]
            after = post["systemd_units"][0]
            for field in (
                "invocation_id", "main_pid",
                "exec_main_start_timestamp_monotonic_us",
            ):
                _require(before[field] != after[field], reason)
        if fault_type == "CLOCK_STEP":
            _require(pre["broker_deny_all"] is not None and
                     post["broker_deny_all"] is not None and
                     pre["wall_clock_delta_ms"] == 0 and
                     post["wall_clock_delta_ms"] is not None and
                     MINIMUM_CLOCK_STEP_MS <=
                        abs(post["wall_clock_delta_ms"]) <=
                        MAXIMUM_CLOCK_STEP_MS and
                     pre["wall_clock_delta_ms"] !=
                        post["wall_clock_delta_ms"] and
                     pre["systemd_units"] == post["systemd_units"] and
                     pre["processes"] == post["processes"] and
                     pre["paths"] == post["paths"], reason)
            broker_before = dict(pre["broker_deny_all"])
            broker_after = dict(post["broker_deny_all"])
            for item in (broker_before, broker_after):
                item.pop("checked_boottime_ns")
                item.pop("state_sha256")
            _require(
                broker_before == broker_after and
                pre["broker_deny_all"]["checked_boottime_ns"] <
                    post["broker_deny_all"]["checked_boottime_ns"],
                reason)
        before_identity = dict(pre)
        after_identity = dict(post)
        for value in (before_identity, after_identity):
            for field in (
                "phase", "observed_boottime_ns", "body_sha256",
            ):
                value.pop(field)
        _require(before_identity != after_identity, reason)

    def _reobserve_post(
        self, post: Mapping[str, Any], sample: ClockSample,
        spec: Mapping[str, Any],
    ) -> None:
        reason = "P1_OBSERVER_FAULT_POST_IDENTITY_DRIFT"
        units = [self.host.unit(item["unit"])
                 for item in post["systemd_units"]]
        processes = [self.host.process(item["pid"])
                     for item in post["processes"]]
        allowed_documents: dict[str, tuple[str, int]] = {}
        for formal in spec["formal_campaigns"]:
            campaign_id = formal["campaign_id"]
            round_number = _round(campaign_id)
            allowed_documents[str(self.layout.formal_marker(round_number))] = (
                campaign_id, ROOT_UID)
            allowed_documents[str(self.layout.controller_status(campaign_id))] = (
                campaign_id, 1000)
        committed_export = self.host.committed_export(self.layout.export_root)
        committed_lease_path = str(committed_export.lease.path)
        fixture_paths = {str(TOKEN_FAULT_FIXTURE), str(LEASE_FAULT_FIXTURE)}
        paths: list[dict[str, Any]] = []
        observed_documents: dict[str, ObservedDocument] = {}
        for item in post["paths"]:
            raw_path = item["path"]
            if raw_path in allowed_documents:
                observed = self.host.document(
                    Path(raw_path), expected_uid=allowed_documents[raw_path][1])
                observed_documents[raw_path] = observed
                paths.append(observed.path_identity)
            elif raw_path == committed_lease_path:
                observed_documents[raw_path] = committed_export.lease
                paths.append(committed_export.lease.path_identity)
            elif raw_path in fixture_paths:
                paths.append(self.host.path(Path(raw_path), "json"))
            else:
                raise ObserverError(
                    "P1_OBSERVER_FAULT_POST_PATH_NOT_ALLOWLISTED")
        broker = self.host.broker() \
            if post["broker_deny_all"] is not None else None
        _require(sample.boot_id == post["boot_id"] and
                 units == post["systemd_units"] and
                 processes == post["processes"] and paths == post["paths"],
                 reason)
        matching_campaigns: list[str] = []
        for formal in spec["formal_campaigns"]:
            campaign_id = formal["campaign_id"]
            round_number = _round(campaign_id)
            required = {
                str(self.layout.formal_marker(round_number)),
                str(self.layout.controller_status(campaign_id)),
                committed_lease_path,
            }
            if required.issubset(observed_documents):
                matching_campaigns.append(campaign_id)
        _require(len(matching_campaigns) == 1, reason)
        _marker, _status, service_lease, marker_value, _status_value, \
            _environment, generation, _service_export = self._service_documents(
                spec, matching_campaigns[0], sample)
        _require(
            service_lease == committed_export.lease and
            post["service_epoch"] ==
                marker_value["execution_service_epoch"] and
            post["fencing_generation"] ==
                marker_value["execution_service_fencing_generation"] and
            post["lease_generation"] == generation,
            reason)
        if broker is not None:
            expected = dict(post["broker_deny_all"])
            current = dict(broker)
            expected.pop("checked_boottime_ns")
            expected.pop("state_sha256")
            current.pop("checked_boottime_ns")
            current.pop("state_sha256")
            _require(current == expected, reason)

    def fault(
        self, spec_path: Path, plan_path: Path, injection_path: Path,
    ) -> dict[str, Any]:
        spec_snapshot, spec = self._spec(spec_path)
        plan_snapshot = load_snapshot(
            plan_path, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        plan = validate_plan(plan_snapshot.document, spec)
        injection = load_snapshot(
            injection_path, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid)
        sample = self.host.clock()
        value, post = self._validate_injection(
            injection, spec, plan, sample)
        self._reobserve_post(post, sample, spec)
        reference = _artifact_reference(injection)
        evidence = _evidence(
            "FAULT", sample, units=post["systemd_units"],
            processes=post["processes"], paths=post["paths"],
            broker=post["broker_deny_all"], fault_reference=reference)
        document = seal({
            **_observation_header(
                FAULT_SCHEMA, spec, sample, evidence,
                self.producer, self.production_mode,
                expires_at_ms=min(
                    sample.wall_ms + OBSERVATION_LIFETIME_MS,
                    value["expires_at_ms"])),
            "fault_id": value["fault_id"],
            "fault_type": value["fault_type"],
            "target_id": value["target_id"],
            "injection_boottime_ns":
                value["planned_injection_boottime_ns"],
            "recovered_boottime_ns": value["recovered_boottime_ns"],
            "recovery_verified":
                value["injection_performed"] and
                value["recovery_complete"],
            "cleanup_verified": value["cleanup_complete"],
            "authority_failure": value["authority_failure"],
            "audit_failure": value["audit_failure"],
            "cleanup_failure": value["cleanup_failure"],
        })
        _validate_observation(document)
        for snapshot in (spec_snapshot, plan_snapshot, injection):
            _assert_snapshot_unchanged(
                snapshot, expected_uid=self.expected_uid,
                expected_gid=self.expected_gid)
        return document


def observation_is_unsafe(document: Mapping[str, Any]) -> bool:
    schema = document.get("schema")
    if schema == SERVICE_SCHEMA:
        return not all(document.get(field) is True for field in (
            "continuity_ok", "audit_ok", "cleanup_ok"))
    if schema == CAMPAIGN_CONTINUITY_SCHEMA:
        return bool(
            document.get("persistent_stack_ok") is not True or
            document.get("lease_chain_ok") is not True or
            document.get("zero_exposure") is not True or
            document.get("connector_count") or
            document.get("authorized_uids") or
            document.get("paper_unit_active_count") or
            document.get("campaign_socket_present") or
            document.get("kill_switch_engaged") is not True)
    if schema == AUTHORITY_SCHEMA:
        return bool(
            document.get("connector_count") or document.get("authorized_uids") or
            document.get("paper_unit_active_count") or
            document.get("campaign_socket_present") or
            document.get("kill_switch_engaged") is not True or
            document.get("local_boundary_safe") is not True or
            document.get("local_boundary_uncertain") is True or
            document.get("observation_scope") !=
                "LOCAL_HOST_BOUNDARY_ONLY" or
            document.get("authoritative_account_state_observed") is not False)
    if schema == CLEANUP_SCHEMA:
        return bool(
            document.get("watch_authority_count") or
            document.get("export_residue_count") or
            document.get("session_authority_count") or
            document.get("paper_unit_active_count") or
            document.get("campaign_socket_present") or
            document.get("cleanup_complete") is not True or
            document.get("cleanup_uncertain") is True or
            document.get("errors"))
    if schema == FAULT_SCHEMA:
        return (
            document.get("recovery_verified") is not True or
            document.get("cleanup_verified") is not True or
            any(document.get(field) is True for field in (
                "authority_failure", "audit_failure", "cleanup_failure")))
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (
            "service", "campaign-continuity", "authority", "cleanup",
            "fault"):
        child = subparsers.add_parser(command)
        child.add_argument("--campaign-spec", type=Path, required=True)
        child.add_argument("--output", type=Path, required=True)
    service = subparsers.choices["service"]
    service.add_argument("--formal-campaign-id", required=True)
    service.add_argument("--transition-fault-id")
    continuity = subparsers.choices["campaign-continuity"]
    continuity.add_argument("--campaign-runtime", type=Path, required=True)
    continuity.add_argument(
        "--continuity-slot-index", type=int, required=True)
    continuity.add_argument("--transition-fault-id")
    cleanup = subparsers.choices["cleanup"]
    cleanup.add_argument(
        "--subject-type", choices=("LAUNCHER", "FAULT", "FINAL"),
        required=True)
    cleanup.add_argument("--subject-id", required=True)
    cleanup.add_argument("--formal-campaign-id")
    cleanup.add_argument("--fault-injection-receipt", type=Path)
    fault = subparsers.choices["fault"]
    fault.add_argument("--fault-plan", type=Path, required=True)
    fault.add_argument("--fault-injection-receipt", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _require(arguments.run, "P1_OBSERVER_EXPLICIT_RUN_REQUIRED")
        _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
                 "P1_OBSERVER_ROOT_REQUIRED")
        producer = bind_executing_image()
        observer = IndependentObserver(ReadOnlyHost(), producer=producer)
        producer.reopen()
        if arguments.command == "service":
            document = observer.service(
                arguments.campaign_spec, arguments.formal_campaign_id,
                arguments.transition_fault_id)
        elif arguments.command == "campaign-continuity":
            document = observer.campaign_continuity(
                arguments.campaign_spec, arguments.campaign_runtime,
                arguments.continuity_slot_index,
                arguments.transition_fault_id)
        elif arguments.command == "authority":
            document = observer.authority(arguments.campaign_spec)
        elif arguments.command == "cleanup":
            document = observer.cleanup(
                arguments.campaign_spec, subject_type=arguments.subject_type,
                subject_id=arguments.subject_id,
                formal_campaign_id=arguments.formal_campaign_id,
                fault_receipt_path=arguments.fault_injection_receipt)
        else:
            document = observer.fault(
                arguments.campaign_spec, arguments.fault_plan,
                arguments.fault_injection_receipt)
        producer.reopen()
        publish_receipt(document, arguments.output)
        producer.reopen()
        unsafe = observation_is_unsafe(document)
        print(
            "hepta_p1_safety_soak_independent_observer: " +
            ("UNSAFE" if unsafe else "PASS") +
            f" schema={document['schema']} output={arguments.output}")
        return 2 if unsafe else 0
    except ObserverError as error:
        print(
            "hepta_p1_safety_soak_independent_observer: FAIL " +
            error.reason, file=sys.stderr)
        return 1
    except Exception:
        print(
            "hepta_p1_safety_soak_independent_observer: FAIL "
            "P1_OBSERVER_UNEXPECTED_FAILURE", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
