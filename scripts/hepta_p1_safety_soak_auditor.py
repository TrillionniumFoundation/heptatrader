#!/usr/bin/env python3

"""Independent, read-only P1 SHADOW safety and soak auditor.

The auditor consumes immutable canonical evidence and atomically publishes
one canonical, non-authorizing GO/NO_GO/HALT receipt (also echoed on stdout).
It deliberately has no service-control, credential, network, broker, policy,
or authority surface.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from datetime import date, datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAXIMUM_JSON_BYTES = 4 * 1024 * 1024
MAXIMUM_OUTPUT_BYTES = 16 * 1024 * 1024
MINIMUM_BOOTTIME_DURATION_NS = 72 * 60 * 60 * 1_000_000_000
MAXIMUM_CHECKPOINT_GAP_NS = 15 * 60 * 1_000_000_000
MAXIMUM_FAULT_INJECTION_LATENESS_NS = 30 * 1_000_000_000
MAXIMUM_FAULT_RECOVERY_NS = 5 * 60 * 1_000_000_000
MINIMUM_CLOCK_STEP_MS = 100
MAXIMUM_CLOCK_STEP_MS = 60 * 1000
CLOCK_CORRELATION_TOLERANCE_NS = 1_000_000_000
MINIMUM_TRADING_DAYS = 10
MAXIMUM_TRADING_DAYS = 20
MINIMUM_ELIGIBLE_DECISIONS = 200
ONE_HUNDRED_PERCENT_PPM = 1_000_000
STRICTLY_GREATER_THAN_99_PERCENT_PPM = 990_000
LAUNCHER_WARMUP_MS = 210 * 60 * 1000
LAUNCHER_EARLY_START_LEAD_MS = 20 * 60 * 1000
POLICY_SLOT_INTERVAL_MS = 2 * 60 * 1000
POLICY_MAXIMUM_ITERATIONS = 241
MAXIMUM_LAUNCH_LATENESS_MS = 15 * 60 * 1000
POST_FORMAL_PROJECTION_GUARD_MS = 20 * 60 * 1000
POST_FORMAL_TEARDOWN_GUARD_MS = 30 * 60 * 1000
ROOT_UID = 0
ROOT_GID = 0
INSTALLED_EXECUTABLE = Path("/usr/libexec/hepta-p1-safety-soak-auditor")
PRODUCTION_MODE = "PRODUCTION_ROOT_AUDIT"
REHEARSAL_MODE = "UNBOUND_REHEARSAL"
FREEZE_BUNDLE_SCHEMA = "hepta.p1-safety-soak-freeze-bundle-receipt.v1"
FREEZER_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-campaign-freezer")
FREEZER_PRODUCTION_MODE = "PRODUCTION_ROOT_PREFLIGHT"
OBSERVER_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-independent-observer")
OBSERVER_PRODUCTION_MODE = "PRODUCTION_ROOT_OBSERVER"
FAULT_INJECTOR_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-root-fault-injector")
FAULT_INJECTOR_PRODUCTION_MODE = "PRODUCTION_ROOT_FAULT_INJECTION"
LAUNCHER_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-shadow-admission-launcher")
CALENDAR_SCHEMA = "hepta.p1-safety-soak-reviewed-trading-calendar.v1"
CALENDAR_ID = "EURUSD_NY_CORE_2026"
CALENDAR_VERSION = "v1"
CALENDAR_TIMEZONE = "America/New_York"
CALENDAR_EXCLUDED_DAYS_2026 = frozenset({
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
})

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
EXPORT_GENERATION = re.compile(
    r"generation-([0-9]{20})-([A-Za-z0-9_-]{8,64})")
EXPORT_COMMIT_NAME = "current.json"
EXPORT_GENERATIONS_NAME = "generations"
EXPORT_FILES = (
    "snapshot.json",
    "shadow-watch-lease-receipt.json",
    "shadow-watch-export-receipt.json",
)
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}")

PERMISSION_FIELDS = frozenset({
    "paper_authorized", "live_authorized", "mutation_authorized",
    "mutation_attempted", "direct_broker_access",
})
PRODUCER_FIELDS = frozenset({"path", "file_sha256"})
REFERENCE_FIELDS = frozenset({"path", "file_sha256", "body_sha256"})
SOURCE_PRODUCER_PIN_FIELDS = frozenset({
    "role", "source_path", "installed_path", "file_sha256",
})
SOURCE_PRODUCER_PATHS = {
    "campaign_freezer": (
        "scripts/hepta_p1_safety_soak_campaign_freezer.py",
        str(FREEZER_EXECUTABLE)),
    "evidence_recorder": (
        "scripts/hepta_p1_safety_soak_evidence_recorder.py",
        "/usr/libexec/hepta-p1-safety-soak-evidence-recorder"),
    "independent_observer": (
        "scripts/hepta_p1_safety_soak_independent_observer.py",
        str(OBSERVER_EXECUTABLE)),
    "root_fault_injector": (
        "scripts/hepta_p1_safety_soak_root_fault_injector.py",
        str(FAULT_INJECTOR_EXECUTABLE)),
    "auditor": (
        "scripts/hepta_p1_safety_soak_auditor.py",
        str(INSTALLED_EXECUTABLE)),
    "shadow_admission_launcher": (
        "scripts/hepta_p1_shadow_admission_launcher.py",
        str(LAUNCHER_EXECUTABLE)),
    "watch_to_paper_handoff": (
        "scripts/hepta_p1_watch_to_paper_handoff.py",
        "/usr/libexec/hepta-p1-watch-to-paper-handoff"),
    "fault_pin_producer": (
        "scripts/hepta_p1_safety_soak_fault_pin_producer.py",
        "/usr/libexec/hepta-p1-safety-soak-fault-pin-producer"),
    "campaign_coordinator": (
        "scripts/hepta_p1_safety_soak_campaign_coordinator.py",
        "/usr/libexec/hepta-p1-safety-soak-campaign-coordinator"),
    "observer_worker": (
        "scripts/hepta_p1_safety_soak_observer_worker.py",
        "/usr/libexec/hepta-p1-safety-soak-observer-worker"),
    "recorder_worker": (
        "scripts/hepta_p1_safety_soak_recorder_worker.py",
        "/usr/libexec/hepta-p1-safety-soak-recorder-worker"),
    "policy_planner": (
        "scripts/hepta_p1_safety_soak_policy_planner.py",
        "/usr/libexec/hepta-p1-safety-soak-policy-planner"),
    "observation_policy_builder": (
        "scripts/build_hepta_p1_observation_policy.py",
        "/usr/libexec/build-hepta-p1-observation-policy"),
    "broker_egress_policy": (
        "scripts/hepta_broker_egress_policy.py",
        "/usr/libexec/hepta-broker-egress-policy"),
}
FREEZE_FORMAL_REFERENCE_FIELDS = frozenset({
    "campaign_id", "path", "file_sha256", "body_sha256",
    "launcher_start_ms", "launcher_dispatch_at_ms",
    "valid_after_ms", "expires_at_ms", "slot_interval_ms",
    "maximum_iterations", "launcher_completion_deadline_ms",
    "projection_deadline_ms", "teardown_deadline_ms",
})
FREEZE_STRATEGY_FILE_FIELDS = frozenset({
    "role", "path", "file_sha256", "body_sha256",
})
FREEZE_BUNDLE_FIELDS = frozenset({
    "schema", "version", "status", "round", "freeze_id", "issued_at_ms",
    "expires_at_ms", "campaign_id", "domain_id", "producer",
    "production_mode", "boot_id", "frozen_boottime_ns",
    "source_baseline", "source_manifest_sha256", "source_producer_pins",
    "policy_sha256", "formal_policies", "strategy_id", "strategy_version",
    "strategy_sha256", "strategy_files", "trading_calendar",
    "calendar_id", "calendar_version", "calendar_source_sha256",
    "declared_trading_days", "trading_timezone",
    "trading_calendar_sha256", "eligible_scheduled_at_ms",
    "scheduled_decision_count", "planned_faults", "anchors",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
})
CALENDAR_WINDOW_FIELDS = frozenset({"opens_at_ms", "closes_at_ms"})
CALENDAR_SESSION_FIELDS = frozenset({
    "trading_day", "opens_at_ms", "closes_at_ms", "maintenance_windows",
})
CALENDAR_FIELDS = frozenset({
    "schema", "version", "status", "freeze_id", "producer",
    "production_mode", "calendar_id", "calendar_version",
    "calendar_source_sha256", "trading_timezone", "sessions",
    "issued_at_ms", "expires_at_ms", "paper_authorized",
    "live_authorized", "mutation_authorized", "direct_broker_access",
    "body_sha256",
})
LAUNCHER_IDENTITY_FIELDS = frozenset({
    "unit", "invocation_id", "main_pid", "type", "restart",
    "remain_after_exit", "user", "group", "exec_start", "environment",
    "launcher_sha256", "conflicts",
})

OBSERVER_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "schema",
})
SERVICE_OBSERVATION_SCHEMA = (
    "hepta.p1-safety-soak-independent-service-observation.v1")
CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA = (
    "hepta.p1-safety-soak-independent-campaign-continuity-observation.v1")
CAMPAIGN_RUNTIME_SCHEMA = "hepta.p1-safety-soak-campaign-runtime.v1"
RUNTIME_EXECUTABLE_FIELDS = frozenset({"path", "file_sha256"})
FORMAL_RUNTIME_FIELDS = frozenset({
    "formal_campaign_id", "probe_campaign_id", "launcher_start_ms",
    "launcher_dispatch_at_ms", "valid_after_ms", "slot_interval_ms",
    "maximum_iterations", "expires_at_ms",
    "launcher_completion_deadline_ms", "projection_deadline_ms",
    "teardown_deadline_ms", "policy", "launcher_receipt_path",
    "verified_closure_path", "artifact_root",
})
CAMPAIGN_RUNTIME_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "round", "boot_id",
    "issued_at_ms", "expires_at_ms", "freeze_bundle", "campaign_spec",
    "fault_plan", "pin_formal_campaign_id", "formal_campaigns",
    "observer_cadence_ms", "maximum_slot_lateness_ms", "state_root",
    "raw_observation_directory", "recorder_root",
    "injector_journal_directory", "injector_output_directory",
    "control_directory", "executables", "paper_authorized",
    "live_authorized", "mutation_authorized", "direct_broker_access",
    "body_sha256",
})
FAULT_OBSERVATION_SCHEMA = (
    "hepta.p1-safety-soak-independent-fault-observation.v1")
AUTHORITY_OBSERVATION_SCHEMA = (
    "hepta.p1-safety-soak-independent-authority-observation.v1")
CLEANUP_OBSERVATION_SCHEMA = (
    "hepta.p1-safety-soak-independent-cleanup-observation.v1")
OBSERVER_SCHEMAS = frozenset({
    SERVICE_OBSERVATION_SCHEMA, CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA,
    FAULT_OBSERVATION_SCHEMA,
    AUTHORITY_OBSERVATION_SCHEMA, CLEANUP_OBSERVATION_SCHEMA,
})
SERVICE_OBSERVATION_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "campaign_id", "observer_id", "observation_complete", "clock_id",
    "boot_id", "observed_boottime_ns", "service_epoch",
    "fencing_generation", "lease_generation", "transition_fault_id",
    "source_manifest_sha256", "policy_sha256", "strategy_sha256",
    "continuity_ok", "audit_ok", "cleanup_ok", "producer",
    "production_mode", "observation_evidence",
    "paper_authorized",
    "live_authorized", "mutation_authorized", "direct_broker_access",
    "body_sha256",
})
CAMPAIGN_CONTINUITY_OBSERVATION_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "campaign_id", "observer_id", "observation_complete", "clock_id",
    "boot_id", "observed_boottime_ns", "source_manifest_sha256",
    "policy_sha256", "strategy_sha256", "freeze_bundle",
    "campaign_runtime", "continuity_slot_index",
    "continuity_scheduled_at_ms", "continuity_origin_ms",
    "continuity_end_ms", "continuity_cadence_ms",
    "continuity_final_slot", "continuity_is_final", "catch_up",
    "activation_receipt", "activation_receipt_document", "export_commit",
    "export_commit_document", "export_snapshot", "lease_receipt",
    "lease_receipt_document", "export_receipt", "lease_generation",
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
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
})
WATCH_LEASE_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_id", "agent_uid", "boundary",
    "operation", "lease_generation", "previous_lease_generation",
    "previous_receipt_body_sha256", "accepted", "reason_code",
    "accepted_at_ms", "ttl_seconds", "expires_at_ms", "paper_authorized",
    "live_authorized", "mutation_authorized", "body_sha256",
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
GATEWAY_EXECUTABLE = Path("/usr/libexec/hepta-tool-gatewayd")
GATEWAY_PROFILE = Path("/etc/heptatrader/trust-domains/alpha.env")
GATEWAY_DOMAIN_CONFIG = Path("/etc/heptatrader/trust-domains/alpha.json")
GATEWAY_TOOL_SOCKET = Path("/run/hepta-agent-alpha/tools.sock")
GATEWAY_SUPERVISOR_SOCKET = Path(
    "/run/hepta-tool-gateway-alpha/session-supervisor.sock")
FAULT_OBSERVATION_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "campaign_id", "observer_id", "observation_complete", "clock_id",
    "boot_id", "fault_id", "fault_type", "target_id",
    "injection_boottime_ns",
    "recovered_boottime_ns", "recovery_verified", "cleanup_verified",
    "authority_failure", "audit_failure", "cleanup_failure",
    "source_manifest_sha256", "policy_sha256", "strategy_sha256",
    "producer", "production_mode", "observation_evidence",
    "paper_authorized", "live_authorized",
    "mutation_authorized",
    "direct_broker_access", "body_sha256",
})
AUTHORITY_OBSERVATION_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "campaign_id", "observer_id", "observation_complete", "clock_id",
    "boot_id", "observed_boottime_ns", "source_manifest_sha256",
    "policy_sha256", "strategy_sha256", "connector_count",
    "authorized_uids", "paper_unit_active_count",
    "campaign_socket_present", "kill_switch_engaged",
    "local_boundary_safe", "local_boundary_uncertain",
    "observation_scope", "authoritative_account_state_observed",
    "producer", "production_mode",
    "observation_evidence", "paper_authorized",
    "live_authorized",
    "mutation_authorized", "direct_broker_access", "body_sha256",
})
CLEANUP_OBSERVATION_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "campaign_id", "observer_id", "observation_complete", "clock_id",
    "boot_id", "observed_boottime_ns", "source_manifest_sha256",
    "policy_sha256", "strategy_sha256", "subject_type", "subject_id",
    "watch_authority_count", "export_residue_count",
    "session_authority_count", "paper_unit_active_count",
    "campaign_socket_present", "cleanup_complete", "cleanup_uncertain",
    "errors", "producer", "production_mode", "observation_evidence",
    "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access", "body_sha256",
})
OBSERVATION_EVIDENCE_SCHEMA = (
    "hepta.p1-safety-soak-independent-observation-evidence.v1")
OBSERVATION_EVIDENCE_FIELDS = frozenset({
    "schema", "version", "kind", "boot_id", "observed_boottime_ns",
    "systemd_units", "processes", "paths", "broker_deny_all",
    "fault_injection_receipt", "body_sha256",
})
OBSERVATION_UNIT_FIELDS = frozenset({
    "unit", "load_state", "active_state", "sub_state", "unit_file_state",
    "main_pid", "invocation_id", "exec_main_start_timestamp_monotonic_us",
    "n_restarts", "state_sha256",
})
OBSERVATION_PROCESS_FIELDS = frozenset({
    "pid", "uid", "gid", "starttime_ticks", "exe_device", "exe_inode",
    "cgroup_sha256", "state_sha256",
})
OBSERVATION_PATH_FIELDS = frozenset({
    "path", "present", "parent_device", "parent_inode", "parent_uid",
    "parent_gid", "parent_mode", "parent_nlink", "file_type", "device",
    "inode", "uid", "gid", "mode", "nlink", "size", "mtime_ns",
    "ctime_ns", "content_file_sha256", "content_body_sha256",
    "state_sha256",
})
OBSERVATION_BROKER_FIELDS = frozenset({
    "helper_path", "helper_file_sha256", "policy_sha256",
    "authorized_connector_count", "authorized_uids",
    "protected_port_count", "deny_all", "checked_boottime_ns",
    "state_sha256",
})
FAULT_INJECTION_SCHEMA = (
    "hepta.p1-safety-soak-root-fault-injection-receipt.v1")
FAULT_INJECTION_FIELDS = frozenset({
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
    "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access", "body_sha256",
})
FAULT_TARGET_IDENTITY_SCHEMA = (
    "hepta.p1-safety-soak-fault-target-identity.v1")
FAULT_TARGET_IDENTITY_FIELDS = frozenset({
    "schema", "version", "phase", "target_id", "boot_id",
    "observed_boottime_ns", "service_epoch", "fencing_generation",
    "lease_generation", "systemd_units", "processes", "paths",
    "broker_deny_all", "residue_count", "wall_clock_delta_ms",
    "fixture_generation", "fixture_expires_boottime_ns", "fixture_valid",
    "body_sha256",
})

SPEC_FIELDS = frozenset({
    "schema", "version", "campaign_id", "domain_id",
    "source_manifest_sha256", "policy_sha256", "strategy_id",
    "strategy_version", "strategy_sha256", "formal_campaigns",
    "declared_trading_days", "trading_timezone",
    "trading_calendar_sha256",
    "eligible_scheduled_at_ms", "scheduled_decision_count",
    "minimum_eligible_decisions",
    "minimum_complete_ppm", "minimum_boottime_duration_ns",
    "maximum_checkpoint_gap_ns", "maximum_decision_lateness_ms",
    "fault_plan_body_sha256", "independent_auditor_id", "frozen_at_ms",
    "freeze_bundle",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
})
FORMAL_CAMPAIGN_FIELDS = frozenset({
    "campaign_id", "campaign_sha256", "policy_body_sha256",
    "policy_file_sha256",
})

LAUNCHER_RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "reason", "domain_id",
    "probe_campaign_id", "formal_campaign_id", "formal_start_ms",
    "completed_at_ms", "probe_reader_unit", "probe_host_unit",
    "formal_reader_unit", "formal_host_unit", "launcher_unit",
    "launcher_identity", "helper_sha256", "activation_receipt_path",
    "activation_receipt_file_sha256", "activation_receipt_body_sha256",
    "activation_profile_receipt_path",
    "activation_profile_receipt_file_sha256",
    "activation_profile_receipt_body_sha256", "activation_broker_epoch",
    "activation_gateway_epoch", "activation_reconcile_timer",
    "activation_predecessor_success", "activation_predecessor_failure",
    "gateway_identity",
    "probe_policy_file_sha256", "probe_marker_file_sha256",
    "probe_reader_pid", "probe_generation",
    "probe_host_receipt_file_sha256", "probe_closure",
    "admission_receipt_file_sha256", "formal_policy_file_sha256",
    "formal_marker_file_sha256", "formal_valid_after_ms",
    "formal_expected_iterations", "formal_completed_iterations",
    "formal_final_generation", "formal_controller_status_file_sha256",
    "formal_observer_state_file_sha256",
    "formal_verified_closure_file_sha256",
    "formal_verified_closure_body_sha256", "formal_host_result_sha256",
    "formal_reader_completion", "formal_post_verifier_reader_evidence",
    "execution_service_epoch", "execution_service_fencing_generation",
    "formal_reader_pid", "formal_generation", "formal_closure",
    "cleanup_errors", "authority_residue", "export_residue",
    "load_probe_admission_receipt_activation_binding_attested",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
})
CUSTODIAN_CLOSURE_FIELDS = frozenset({
    "schema", "version", "domain_id", "campaign_id", "lease_generation",
    "authoritative_revoke_outcome", "local_authority_removed",
    "export_evidence_removed", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access", "body_sha256",
})

VERIFIED_CLOSURE_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256",
    "policy_body_sha256", "policy_file_sha256", "strategy_id",
    "strategy_version", "strategy_sha256", "strategy_file_sha256",
    "observer_state_body_sha256", "observer_state_file_sha256",
    "strategy_state_file_sha256", "final_audit_body_sha256",
    "final_audit_file_sha256", "verified_at_ms", "completed_iterations",
    "maximum_iterations", "segment_count", "segments", "iteration_count",
    "iterations", "residual_evidence", "complete_revalidation",
    "closure_status", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "body_sha256",
})
VERIFIED_SEGMENT_FIELDS = frozenset({
    "segment_index", "record_count", "history_head_sha256",
    "source_sha256", "history_record_bytes", "history_index_bytes",
    "history_storage_bytes", "audit_sha256",
})
VERIFIED_ITERATION_FIELDS = frozenset({
    "iteration", "segment_index", "scheduled_at_ms", "evaluated_at_ms",
    "source_first_sequence", "source_last_sequence", "source_record_count",
    "source_total_record_count", "source_window_truncated",
    "source_predecessor_record_sha256", "source_records_sha256",
    "source_history_head_sha256", "source_history_index_body_sha256",
    "source_history_index_file_sha256", "materialization_window_ms",
    "materialization_maximum_records", "snapshot_body_sha256",
    "snapshot_file_sha256", "watch_lease_receipt_body_sha256",
    "watch_lease_receipt_file_sha256",
    "watch_export_receipt_body_sha256",
    "watch_export_receipt_file_sha256", "quote_history_body_sha256",
    "quote_history_file_sha256", "bar_history_body_sha256",
    "bar_history_file_sha256", "calendar_body_sha256",
    "calendar_file_sha256", "information_body_sha256",
    "information_file_sha256", "source_attestation",
    "information_packet_body_sha256", "information_packet_file_sha256",
    "decision_receipt_file_sha256", "source_window_manifest_body_sha256",
    "source_window_manifest_file_sha256", "final_outcome",
    "residual_evidence",
})
VERIFIED_SOURCE_ATTESTATION_FIELDS = frozenset({
    "receipt_body_sha256", "receipt_file_sha256", "extractor_code_sha256",
    "semantic_output_sha256", "completeness_sha256",
    "raw_payloads_verified",
})

DECISION_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "decision_id",
    "formal_campaign_id", "verified_closure_body_sha256",
    "closure_iteration",
    "trading_day", "scheduled_at_ms", "evaluated_at_ms", "clock_id",
    "boot_id", "scheduled_boottime_ns", "evaluated_boottime_ns",
    "clock_observer_receipt", "eligible",
    "complete", "catch_up", "outcome", "source_manifest_sha256",
    "policy_sha256", "strategy_sha256", "decision_artifact_file_sha256",
    "evidence_sha256", "previous_receipt_body_sha256", "audit_failure",
    "cleanup_failure", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access", "body_sha256",
})
CHECKPOINT_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "clock_id", "boot_id",
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
    "transition_fault_id", "persistent_stack_ok", "lease_chain_ok",
    "connector_count", "authorized_uids", "paper_unit_active_count",
    "campaign_socket_present", "kill_switch_engaged", "zero_exposure",
    "source_manifest_sha256", "policy_sha256", "strategy_sha256",
    "previous_checkpoint_body_sha256", "observer_receipt", "paper_authorized",
    "live_authorized", "mutation_authorized", "direct_broker_access",
    "body_sha256",
})
FAULT_PLAN_FIELDS = frozenset({
    "schema", "version", "campaign_id", "source_manifest_sha256",
    "policy_sha256", "strategy_sha256", "planned_faults",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
})
PLANNED_FAULT_FIELDS = frozenset({
    "fault_id", "fault_type", "target_id", "formal_campaign_id",
    "inject_at_boottime_ns", "maximum_injection_lateness_ns",
    "maximum_recovery_ns",
})
FAULT_RESULT_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "fault_id",
    "fault_type", "target_id", "injection_boottime_ns",
    "recovered_boottime_ns",
    "recovery_verified", "cleanup_verified", "evidence_sha256",
    "observer_receipt", "previous_result_body_sha256", "authority_failure",
    "audit_failure",
    "cleanup_failure", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access", "body_sha256",
})
AUTHORITY_SNAPSHOT_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "clock_id", "boot_id",
    "observed_boottime_ns", "source_manifest_sha256", "policy_sha256",
    "strategy_sha256", "connector_count", "authorized_uids",
    "paper_unit_active_count", "campaign_socket_present",
    "kill_switch_engaged", "local_boundary_safe",
    "local_boundary_uncertain", "observation_scope",
    "authoritative_account_state_observed", "observer_receipt",
    "previous_snapshot_body_sha256",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
})
CLEANUP_SNAPSHOT_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "clock_id", "boot_id",
    "observed_boottime_ns", "subject_type", "subject_id",
    "watch_authority_count", "export_residue_count",
    "session_authority_count", "paper_unit_active_count",
    "campaign_socket_present", "cleanup_complete", "cleanup_uncertain",
    "errors", "observer_receipt", "previous_snapshot_body_sha256",
    "paper_authorized",
    "live_authorized", "mutation_authorized", "direct_broker_access",
    "body_sha256",
})

ALLOWED_FAULT_TYPES = frozenset({
    "PROCESS_KILL", "SERVICE_RESTART", "TOKEN_LOSS", "LEASE_EXPIRY",
    "NETWORK_DENY_RELOAD", "EVIDENCE_WRITER_CRASH", "CLOCK_STEP",
})
REQUIRED_FAULT_TYPES = ALLOWED_FAULT_TYPES
FAULT_TARGET_IDS = {
    "PROCESS_KILL": "p1-independent-observer-process",
    "SERVICE_RESTART": "watch-execution-gateway",
    "TOKEN_LOSS": "fault-fixture-watch-session-token",
    "LEASE_EXPIRY": "fault-fixture-watch-lease",
    "NETWORK_DENY_RELOAD": "broker-egress-deny-policy",
    "EVIDENCE_WRITER_CRASH": "p1-safety-soak-evidence-recorder",
    "CLOCK_STEP": "wall-clock-discontinuity-detector",
}

AUDIT_RECEIPT_FIELDS = frozenset({
    "schema", "version", "phase", "verdict", "campaign_id", "domain_id",
    "independent_auditor_id", "audited_at_ms",
    "campaign_spec_file_sha256", "campaign_spec_body_sha256",
    "freeze_bundle", "campaign_runtime", "producer", "production_mode",
    "source_manifest_sha256", "policy_sha256", "strategy_sha256",
    "evaluated_interval", "counts", "completeness", "checked_artifacts",
    "failed_invariants", "exposure_summary", "cleanup_status",
    "p1_safety_soak_gate_satisfied", "paper_test_admission_candidate",
    "safest_allowed_next_action", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access", "body_sha256",
})
EVALUATED_INTERVAL_FIELDS = frozenset({
    "clock_id", "boot_id", "start_boottime_ns", "end_boottime_ns",
    "duration_ns", "maximum_checkpoint_gap_ns", "consecutive",
    "continuity_origin_ms", "continuity_end_ms", "continuity_final_slot",
})
COUNTS_FIELDS = frozenset({
    "launcher_receipts", "verified_closures", "continuity_checkpoints",
    "declared_trading_days", "observed_trading_days", "scheduled_decisions",
    "decision_receipts", "eligible_decisions", "complete_eligible_decisions",
    "incomplete_eligible_decisions", "catch_up_decisions", "planned_faults",
    "fault_results", "authority_snapshots", "cleanup_snapshots",
})
COMPLETENESS_FIELDS = frozenset({
    "numerator", "denominator", "ppm",
    "strictly_greater_than_99_percent",
})
CHECKED_ARTIFACT_FIELDS = frozenset({
    "role", "path", "file_sha256", "body_sha256",
})
EXPOSURE_SUMMARY_FIELDS = frozenset({
    "evidence_present", "maximum_connector_count",
    "maximum_authorized_uid_count", "maximum_paper_unit_active_count",
    "campaign_socket_ever_present", "kill_switch_continuously_engaged",
    "local_boundary_uncertain", "scope",
    "authoritative_account_state_observed",
})
CLEANUP_STATUS_FIELDS = frozenset({
    "required_subject_count", "verified_subject_count", "complete",
})

RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)


class AuditError(RuntimeError):
    """Stable input or contract error."""


class EvidenceError(AuditError):
    """One classified evidence invariant failure."""

    def __init__(self, reason: str, *, halt: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.halt = halt


def canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                separators=(",", ":")) + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise AuditError("P1_AUDIT_CANONICALIZATION_FAILED") from error


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def seal(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "body_sha256": digest_bytes(canonical_bytes(body))}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuditError("P1_AUDIT_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def decode_canonical_document(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                AuditError("P1_AUDIT_NON_FINITE_JSON")),
        )
    except AuditError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise AuditError(f"{label}_JSON_INVALID") from error
    if not isinstance(value, dict):
        raise AuditError(f"{label}_ROOT_INVALID")
    if canonical_bytes(value) != payload:
        raise AuditError(f"{label}_NOT_CANONICAL")
    body = dict(value)
    claimed = body.pop("body_sha256", None)
    if not _is_digest(claimed) or claimed != digest_bytes(canonical_bytes(body)):
        raise AuditError(f"{label}_BODY_DIGEST_INVALID")
    return value


def _stable_metadata(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_uid, value.st_gid,
        stat.S_IMODE(value.st_mode), value.st_nlink, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
    """Return stable identity without mutable content times/link count."""

    return (
        value.st_dev, value.st_ino, value.st_uid, value.st_gid,
        stat.S_IMODE(value.st_mode),
    )


def _open_anchored_directory(path: Path, label: str) -> int:
    if not path.is_absolute() or any(part in {"", ".", ".."}
                                     for part in path.parts[1:]):
        raise AuditError(f"{label}_PATH_INVALID")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    current: int | None = None
    try:
        current = os.open("/", flags)
        for component in path.parts[1:]:
            before = os.stat(component, dir_fd=current, follow_symlinks=False)
            child = os.open(component, flags, dir_fd=current)
            opened = os.fstat(child)
            if (not stat.S_ISDIR(before.st_mode) or
                    _directory_identity(before) !=
                    _directory_identity(opened)):
                os.close(child)
                raise AuditError(f"{label}_DIRECTORY_REBOUND")
            os.close(current)
            current = child
        if current is None:
            raise AuditError(f"{label}_DIRECTORY_INVALID")
        return current
    except AuditError:
        if current is not None:
            os.close(current)
        raise
    except OSError as error:
        if current is not None:
            os.close(current)
        raise AuditError(f"{label}_DIRECTORY_INVALID") from error


def secure_read(
    path: Path, label: str, maximum_bytes: int = MAXIMUM_JSON_BYTES,
    allowed_modes: frozenset[int] | None = None,
) -> bytes:
    """Read and canonical-reopen a single-link immutable regular file."""

    if (not path.is_absolute() or path.name in {"", ".", ".."} or
            maximum_bytes < 1):
        raise AuditError(f"{label}_PATH_INVALID")
    parent = _open_anchored_directory(path.parent, label)
    rebound_parent: int | None = None
    descriptor: int | None = None
    reopened: int | None = None
    try:
        parent_before = os.fstat(parent)
        if (not stat.S_ISDIR(parent_before.st_mode) or
                parent_before.st_uid != os.geteuid() or
                stat.S_IMODE(parent_before.st_mode) & 0o022):
            raise AuditError(f"{label}_PARENT_UNTRUSTED")
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path.name, flags, dir_fd=parent)
        opened = os.fstat(descriptor)
        modes = (frozenset({0o400, 0o440, 0o444, 0o600, 0o640, 0o644})
                 if allowed_modes is None else allowed_modes)
        if (not stat.S_ISREG(before.st_mode) or
                _stable_metadata(before) != _stable_metadata(opened) or
                opened.st_uid != os.geteuid() or opened.st_nlink != 1 or
                stat.S_IMODE(opened.st_mode) not in modes or
                not 1 <= opened.st_size <= maximum_bytes):
            raise AuditError(f"{label}_FILE_UNTRUSTED")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                raise AuditError(f"{label}_SHORT_READ")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1) != b"":
            raise AuditError(f"{label}_FILE_GREW")
        after = os.fstat(descriptor)
        final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        parent_after = os.fstat(parent)

        rebound_parent = _open_anchored_directory(path.parent, label)
        rebound_parent_metadata = os.fstat(rebound_parent)
        rebound_entry = os.stat(
            path.name, dir_fd=rebound_parent, follow_symlinks=False)
        reopened = os.open(path.name, flags, dir_fd=rebound_parent)
        reopened_metadata = os.fstat(reopened)
        identity = _stable_metadata(opened)
        parent_identity = _directory_identity(parent_before)
        if (identity != _stable_metadata(after) or
                identity != _stable_metadata(final) or
                identity != _stable_metadata(rebound_entry) or
                identity != _stable_metadata(reopened_metadata) or
                parent_identity != _directory_identity(parent_after) or
                parent_identity !=
                    _directory_identity(rebound_parent_metadata)):
            raise AuditError(f"{label}_CANONICAL_REOPEN_FAILED")

        reopened_chunks: list[bytes] = []
        remaining = reopened_metadata.st_size
        while remaining:
            chunk = os.read(reopened, min(remaining, 65536))
            if not chunk:
                raise AuditError(f"{label}_REOPEN_SHORT_READ")
            reopened_chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(reopened, 1) != b"":
            raise AuditError(f"{label}_REOPEN_FILE_GREW")
        payload = b"".join(chunks)
        if payload != b"".join(reopened_chunks):
            raise AuditError(f"{label}_REOPEN_CONTENT_DRIFT")
        return payload
    except AuditError:
        raise
    except OSError as error:
        raise AuditError(f"{label}_SECURE_READ_FAILED") from error
    finally:
        for file_descriptor in (reopened, descriptor, rebound_parent, parent):
            if file_descriptor is not None:
                os.close(file_descriptor)


@dataclass(frozen=True)
class Artifact:
    role: str
    path: str
    document: dict[str, Any]
    file_sha256: str
    body_sha256: str

    @classmethod
    def from_document(cls, role: str, path: str,
                      document: dict[str, Any]) -> "Artifact":
        payload = canonical_bytes(document)
        return cls(
            role=role, path=path, document=document,
            file_sha256=digest_bytes(payload),
            body_sha256=document.get("body_sha256", ""),
        )


@dataclass(frozen=True)
class ProducerBinding:
    payload: bytes
    metadata: os.stat_result

    @property
    def reference(self) -> dict[str, str]:
        return {
            "path": str(INSTALLED_EXECUTABLE),
            "file_sha256": digest_bytes(self.payload),
        }

    def reopen(self) -> None:
        payload = secure_read(
            INSTALLED_EXECUTABLE, "P1_AUDIT_EXECUTING_IMAGE_REOPEN",
            MAXIMUM_JSON_BYTES, frozenset({0o755}))
        metadata = os.stat(INSTALLED_EXECUTABLE, follow_symlinks=False)
        _require(payload == self.payload and
                 _stable_metadata(metadata) == _stable_metadata(self.metadata),
                 "P1_AUDIT_EXECUTING_IMAGE_DRIFT")


def bind_executing_image() -> ProducerBinding:
    try:
        lexical = Path(__file__).absolute()
        metadata = os.lstat(lexical)
        resolved = lexical.resolve(strict=True)
        installed = INSTALLED_EXECUTABLE.resolve(strict=True)
        _require(not stat.S_ISLNK(metadata.st_mode) and
                 resolved == installed == INSTALLED_EXECUTABLE and
                 os.path.samefile(lexical, INSTALLED_EXECUTABLE),
                 "P1_AUDIT_INSTALLED_IMAGE_REQUIRED")
        payload = secure_read(
            INSTALLED_EXECUTABLE, "P1_AUDIT_EXECUTING_IMAGE",
            MAXIMUM_JSON_BYTES, frozenset({0o755}))
        reopened = os.stat(INSTALLED_EXECUTABLE, follow_symlinks=False)
        _require(_stable_metadata(metadata) == _stable_metadata(reopened),
                 "P1_AUDIT_INSTALLED_IMAGE_REQUIRED")
        return ProducerBinding(payload, reopened)
    except (OSError, EvidenceError, AuditError) as error:
        if isinstance(error, AuditError):
            raise
        raise AuditError("P1_AUDIT_INSTALLED_IMAGE_REQUIRED") from error


def load_artifact(path: Path, role: str, index: int = 0) -> Artifact:
    label = f"P1_AUDIT_{role.upper()}_{index}"
    payload = secure_read(path, label)
    document = decode_canonical_document(payload, label)
    _reject_authority(document, f"{label}_AUTHORITY_NOT_FALSE")
    return Artifact(
        role=role, path=str(path), document=document,
        file_sha256=digest_bytes(payload),
        body_sha256=document["body_sha256"],
    )


def _is_int(value: Any, minimum: int | None = None) -> bool:
    return (type(value) is int and
            (minimum is None or value >= minimum))


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def _is_identifier(value: Any) -> bool:
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def _named_values(value: Any, name: str) -> list[Any]:
    result: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == name:
                result.append(child)
            result.extend(_named_values(child, name))
    elif isinstance(value, list):
        for child in value:
            result.extend(_named_values(child, name))
    return result


def _is_sorted_unique_text(values: Any, *, allow_empty: bool = False) -> bool:
    return (isinstance(values, list) and (allow_empty or bool(values)) and
            all(isinstance(item, str) and bool(item) for item in values) and
            values == sorted(set(values)))


def _exact(value: Any, fields: frozenset[str], reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise EvidenceError(reason)
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
        all(_is_int(success.get(field), 0) for field in (
            "receipt_device", "receipt_mode", "receipt_uid", "receipt_gid",
            "receipt_nlink", "receipt_mtime_ns", "receipt_ctime_ns")) and
        _is_int(success.get("receipt_inode"), 1) and
        _is_int(success.get("receipt_bytes"), 1) and
        success["receipt_bytes"] <= MAXIMUM_JSON_BYTES and
        stat.S_ISREG(success["receipt_mode"]) and
        stat.S_IMODE(success["receipt_mode"]) == 0o600 and
        success.get("receipt_nlink") == 1 and
        success.get("receipt_uid") == 0 and
        success.get("receipt_gid") == 0, reason)
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
        all(_is_int(failure.get(field), 0) for field in (
            "receipt_device", "receipt_mode", "receipt_uid", "receipt_gid",
            "receipt_nlink", "receipt_mtime_ns", "receipt_ctime_ns")) and
        _is_int(failure.get("receipt_inode"), 1) and
        _is_int(failure.get("receipt_bytes"), 1) and
        failure["receipt_bytes"] <= MAXIMUM_JSON_BYTES and
        stat.S_ISREG(failure["receipt_mode"]) and
        stat.S_IMODE(failure["receipt_mode"]) == 0o600 and
        failure.get("receipt_nlink") == 1 and
        failure.get("receipt_uid") == 0 and
        failure.get("receipt_gid") == 0 and
        failure.get("journal_path") ==
            PREDECESSOR_ACTIVATION_FAILURE_JOURNAL_PATH and
        failure.get("journal_sha256") ==
            PREDECESSOR_ACTIVATION_FAILURE_JOURNAL_SHA256 and
        failure.get("journal_record_count") == 21 and
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
        _require(_is_digest(evidence.get(field)), reason)
    lock = _exact(evidence.get("transaction_lock"),
                  SHADOW_INSTALL_LOCK_FIELDS, reason)
    _require(
        lock.get("path") == SHADOW_INSTALL_LOCK_PATH and
        _is_int(lock.get("device"), 0) and _is_int(lock.get("inode"), 1) and
        lock.get("nlink") == 1 and lock.get("uid") == 0 and
        lock.get("gid") == 0 and lock.get("mode") == "0600" and
        lock.get("size") == 0 and _is_int(lock.get("mtime_ns"), 0) and
        _is_int(lock.get("ctime_ns"), 0) and
        type(lock.get("created_during_transaction")) is bool and
        lock.get("persistent") is True and
        lock.get("held_during_transaction") is True, reason)
    return evidence


def _require(condition: bool, reason: str, *, halt: bool = True) -> None:
    if not condition:
        raise EvidenceError(reason, halt=halt)


def _reject_authority(value: Any, reason: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PERMISSION_FIELDS and child is not False:
                raise EvidenceError(reason)
            _reject_authority(child, reason)
    elif isinstance(value, list):
        for child in value:
            _reject_authority(child, reason)


def _validate_seal(document: dict[str, Any], reason: str) -> None:
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    _require(_is_digest(claimed) and
             claimed == digest_bytes(canonical_bytes(body)), reason)


def _observer_reference(
    value: Any, expected_schema: str, reason: str,
) -> dict[str, str]:
    reference = _exact(value, OBSERVER_REFERENCE_FIELDS, reason)
    raw_path = reference.get("path")
    canonical_path = Path(raw_path) if isinstance(raw_path, str) else None
    _require(
        canonical_path is not None and canonical_path.is_absolute() and
        canonical_path.name not in {"", ".", ".."} and
        str(canonical_path) == raw_path and
        not any(part in {"", ".", ".."}
                for part in canonical_path.parts[1:]) and
        _is_digest(reference.get("file_sha256")) and
        _is_digest(reference.get("body_sha256")) and
        reference.get("schema") == expected_schema,
        reason)
    return reference


def _validate_export_projection(
    value: Mapping[str, Any], reason: str,
) -> tuple[dict[str, Any], tuple[dict[str, str], ...]]:
    """Validate a checkpoint's ACTIVE commit and generation references."""

    commit_reference = _observer_reference(
        value.get("export_commit"),
        "hepta.shadow-watch-export-commit.v1", reason)
    commit = _exact(
        value.get("export_commit_document"), EXPORT_COMMIT_FIELDS, reason)
    _validate_seal(commit, reason)
    _reject_authority(commit, reason)
    snapshot_reference = _exact(
        value.get("export_snapshot"), OBSERVER_REFERENCE_FIELDS, reason)
    snapshot_schema = snapshot_reference.get("schema")
    _require(snapshot_schema in {
        "hepta.shadow-watch-snapshot.v1",
        "hepta.shadow-watch-snapshot.v2",
    }, reason)
    snapshot_reference = _observer_reference(
        snapshot_reference, str(snapshot_schema), reason)
    lease_reference = _observer_reference(
        value.get("lease_receipt"),
        "hepta.shadow-watch-lease-receipt.v1", reason)
    receipt_reference = _observer_reference(
        value.get("export_receipt"),
        "hepta.shadow-watch-export-receipt.v1", reason)

    sequence = commit.get("commit_sequence")
    generation = commit.get("generation")
    generation_match = (
        EXPORT_GENERATION.fullmatch(generation)
        if isinstance(generation, str) else None)
    _require(
        commit.get("schema") == commit_reference["schema"] and
        commit.get("version") == 1 and
        commit.get("authority_status") == "ACTIVE" and
        _is_int(commit.get("authority_changed_at_ms"), 0) and
        commit.get("close_reason") is None and
        _is_int(sequence, 1) and sequence < (1 << 64) and
        generation_match is not None and
        int(generation_match.group(1)) == sequence and
        commit.get("domain_id") == "alpha" and
        _is_int(commit.get("agent_uid"), 1) and
        commit.get("reader_uid") == 1000 and
        _is_int(commit.get("reader_gid"), 1) and
        _is_int(commit.get("lease_generation"), 1) and
        _is_int(commit.get("committed_at_ms"), 0) and
        commit["committed_at_ms"] == commit["authority_changed_at_ms"] and
        commit_reference["file_sha256"] ==
            digest_bytes(canonical_bytes(commit)) and
        commit_reference["body_sha256"] == commit["body_sha256"] and
        commit.get("snapshot_file_sha256") ==
            snapshot_reference["file_sha256"] and
        commit.get("snapshot_body_sha256") ==
            snapshot_reference["body_sha256"] and
        commit.get("lease_receipt_file_sha256") ==
            lease_reference["file_sha256"] and
        commit.get("lease_receipt_body_sha256") ==
            lease_reference["body_sha256"] and
        commit.get("export_receipt_file_sha256") ==
            receipt_reference["file_sha256"] and
        commit.get("export_receipt_body_sha256") ==
            receipt_reference["body_sha256"], reason)
    commit_path = Path(commit_reference["path"])
    generation_root = (
        commit_path.parent / EXPORT_GENERATIONS_NAME / str(generation))
    expected_paths = (
        commit_path.parent / EXPORT_COMMIT_NAME,
        generation_root / EXPORT_FILES[0],
        generation_root / EXPORT_FILES[1],
        generation_root / EXPORT_FILES[2],
    )
    references = (
        commit_reference, snapshot_reference, lease_reference,
        receipt_reference)
    _require(
        tuple(Path(reference["path"]) for reference in references) ==
            expected_paths, reason)
    return commit, references


def _is_canonical_absolute_path(value: Any) -> bool:
    return (
        isinstance(value, str) and Path(value).is_absolute() and
        Path(value).name not in {"", ".", ".."} and
        str(Path(value)) == value and
        not any(part in {"", ".", ".."} for part in Path(value).parts[1:]))


def _validate_state_seal(
    value: Any, fields: frozenset[str], reason: str,
) -> dict[str, Any]:
    result = _exact(value, fields, reason)
    body = dict(result)
    claimed = body.pop("state_sha256", None)
    _require(
        _is_digest(claimed) and
        claimed == digest_bytes(canonical_bytes(body)), reason)
    return result


def _validate_observation_unit(value: Any, reason: str) -> dict[str, Any]:
    result = _validate_state_seal(value, OBSERVATION_UNIT_FIELDS, reason)
    _require(
        isinstance(result.get("unit"), str) and bool(result["unit"]) and
        len(result["unit"]) <= 256 and
        all(isinstance(result.get(field), str) and bool(result[field])
            for field in (
                "load_state", "active_state", "sub_state",
                "unit_file_state")) and
        _is_int(result.get("main_pid"), 0) and
        isinstance(result.get("invocation_id"), str) and
        (result["invocation_id"] == "" or
         re.fullmatch(r"[0-9a-f]{32}", result["invocation_id"]) is not None) and
        _is_int(result.get("exec_main_start_timestamp_monotonic_us"), 0) and
        _is_int(result.get("n_restarts"), 0), reason)
    return result


def _validate_observation_process(value: Any, reason: str) -> dict[str, Any]:
    result = _validate_state_seal(value, OBSERVATION_PROCESS_FIELDS, reason)
    _require(
        _is_int(result.get("pid"), 2) and
        _is_int(result.get("uid"), 0) and
        _is_int(result.get("gid"), 0) and
        _is_int(result.get("starttime_ticks"), 1) and
        _is_int(result.get("exe_device"), 0) and
        _is_int(result.get("exe_inode"), 1) and
        _is_digest(result.get("cgroup_sha256")), reason)
    return result


def _validate_observation_path(value: Any, reason: str) -> dict[str, Any]:
    result = _validate_state_seal(value, OBSERVATION_PATH_FIELDS, reason)
    _require(
        _is_canonical_absolute_path(result.get("path")) and
        type(result.get("present")) is bool and
        all(_is_int(result.get(field), 0) for field in (
            "parent_device", "parent_inode", "parent_uid", "parent_gid",
            "parent_mode", "parent_nlink")) and
        result["parent_nlink"] >= 1 and result["parent_mode"] <= 0o7777,
        reason)
    metadata_fields = (
        "device", "inode", "uid", "gid", "mode", "nlink", "size",
        "mtime_ns", "ctime_ns",
    )
    if result["present"]:
        _require(
            result.get("file_type") in {
                "regular", "directory", "socket", "fifo", "other",
            } and
            all(_is_int(result.get(field), 0) for field in metadata_fields) and
            result["inode"] >= 1 and result["nlink"] >= 1 and
            result["mode"] <= 0o177777, reason)
    else:
        _require(
            result["file_type"] is None and
            all(result.get(field) is None for field in metadata_fields),
            reason)
    file_digest = result.get("content_file_sha256")
    body_digest = result.get("content_body_sha256")
    _require(
        (file_digest is None or _is_digest(file_digest)) and
        (body_digest is None or _is_digest(body_digest)) and
        (body_digest is None or file_digest is not None) and
        (result["present"] or
         (file_digest is None and body_digest is None)), reason)
    return result


def _validate_observation_broker(
    value: Any, reason: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    result = _validate_state_seal(value, OBSERVATION_BROKER_FIELDS, reason)
    uids = result.get("authorized_uids")
    _require(
        result.get("helper_path") ==
            "/usr/libexec/hepta-broker-egress-policy" and
        _is_digest(result.get("helper_file_sha256")) and
        _is_digest(result.get("policy_sha256")) and
        _is_int(result.get("authorized_connector_count"), 0) and
        isinstance(uids, list) and
        all(_is_int(uid, 0) for uid in uids) and
        uids == sorted(set(uids)) and
        _is_int(result.get("protected_port_count"), 0) and
        type(result.get("deny_all")) is bool and
        _is_int(result.get("checked_boottime_ns"), 0), reason)
    return result


def _validate_observation_identity_lists(
    value: dict[str, Any], reason: str,
) -> None:
    units_raw = value.get("systemd_units")
    processes_raw = value.get("processes")
    paths_raw = value.get("paths")
    _require(all(isinstance(item, list) for item in (
        units_raw, processes_raw, paths_raw)), reason)
    units = [_validate_observation_unit(item, reason) for item in units_raw]
    processes = [_validate_observation_process(item, reason)
                 for item in processes_raw]
    paths = [_validate_observation_path(item, reason) for item in paths_raw]
    _require(
        [item["unit"] for item in units] ==
            sorted(set(item["unit"] for item in units)) and
        [item["pid"] for item in processes] ==
            sorted(set(item["pid"] for item in processes)) and
        [item["path"] for item in paths] ==
            sorted(set(item["path"] for item in paths)) and
        bool(units or processes or paths), reason)


def validate_observation_evidence(
    value: Any, *, kind: str, boot_id: str,
    expected_boottime_ns: int | None = None,
    minimum_boottime_ns: int | None = None,
    reason: str = "P1_AUDIT_RAW_OBSERVER_EVIDENCE_INVALID",
) -> dict[str, Any]:
    evidence = _exact(value, OBSERVATION_EVIDENCE_FIELDS, reason)
    _validate_seal(evidence, reason)
    observed = evidence.get("observed_boottime_ns")
    _require(
        evidence.get("schema") == OBSERVATION_EVIDENCE_SCHEMA and
        evidence.get("version") == 1 and evidence.get("kind") == kind and
        evidence.get("boot_id") == boot_id and _is_int(observed, 0), reason)
    if expected_boottime_ns is not None:
        _require(observed == expected_boottime_ns, reason)
    if minimum_boottime_ns is not None:
        _require(0 <= observed - minimum_boottime_ns <=
                 30 * 1_000_000_000, reason)
    _validate_observation_identity_lists(evidence, reason)
    broker = _validate_observation_broker(
        evidence.get("broker_deny_all"), reason)
    if broker is not None:
        _require(
            0 <= observed - broker["checked_boottime_ns"] <=
                30 * 1_000_000_000, reason)
    fault_reference = evidence.get("fault_injection_receipt")
    if kind == "FAULT":
        _observer_reference(fault_reference, FAULT_INJECTION_SCHEMA, reason)
    else:
        _require(fault_reference is None, reason)
    return evidence


def _validate_activation_gateway_projection(
    activation: Mapping[str, Any], value: Mapping[str, Any], reason: str,
) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any],
    dict[str, Any], dict[str, Any], dict[str, Any],
]:
    frozen = _exact(
        activation.get("gateway_after"),
        ACTIVATION_GATEWAY_AFTER_FIELDS, reason)
    for field in (
            "gateway_executable_sha256", "domain_config_sha256",
            "gateway_profile_sha256", "gateway_process_profile_sha256",
            "unit_contract_sha256"):
        _require(_is_digest(frozen.get(field)), reason)
    _require(
        frozen.get("unit") == "hepta-tool-gateway@alpha.service" and
        frozen.get("active_state") == "active" and
        frozen.get("sub_state") == "running" and
        frozen.get("gateway_executable_path") == str(GATEWAY_EXECUTABLE) and
        frozen.get("gateway_profile_path") == str(GATEWAY_PROFILE) and
        frozen.get("gateway_socket_path") == str(GATEWAY_TOOL_SOCKET) and
        frozen.get("supervisor_socket_path") ==
            str(GATEWAY_SUPERVISOR_SOCKET) and
        frozen.get("execution_remote_mode") == "SIMULATOR" and
        frozen.get("tool_account") == "SIM" and
        frozen.get("execution_domain_id") == "SIM:alpha" and
        frozen.get("tool_allow_trade") == "0" and
        frozen.get("session_templates") == "watch" and
        frozen.get("contract_bindings") ==
            "EUR.USD|EUR|CASH|IDEALPRO|USD", reason)
    gateway = _validate_observation_unit(value.get("gateway_identity"), reason)
    process = _validate_observation_process(
        value.get("gateway_process_identity"), reason)
    executable = _validate_observation_path(
        value.get("gateway_executable_identity"), reason)
    profile = _validate_observation_path(
        value.get("gateway_profile_identity"), reason)
    domain = _validate_observation_path(
        value.get("gateway_domain_config_identity"), reason)
    tool = _validate_observation_path(value.get("tool_socket_identity"), reason)
    supervisor = _validate_observation_path(
        value.get("supervisor_socket_identity"), reason)
    _require(
        gateway["unit"] == frozen["unit"] and
        gateway["active_state"] == "active" and
        gateway["sub_state"] == "running" and
        gateway["main_pid"] == process["pid"] and
        process["exe_device"] == executable["device"] and
        process["exe_inode"] == executable["inode"] and
        executable["path"] == str(GATEWAY_EXECUTABLE) and
        executable["present"] is True and
        executable["file_type"] == "regular" and
        executable["content_file_sha256"] ==
            frozen["gateway_executable_sha256"] and
        profile["path"] == str(GATEWAY_PROFILE) and
        profile["present"] is True and profile["file_type"] == "regular" and
        profile["content_file_sha256"] == frozen["gateway_profile_sha256"] and
        domain["path"] == str(GATEWAY_DOMAIN_CONFIG) and
        domain["present"] is True and domain["file_type"] == "regular" and
        domain["content_file_sha256"] == frozen["domain_config_sha256"] and
        tool["path"] == str(GATEWAY_TOOL_SOCKET) and tool["present"] is True and
        tool["file_type"] == "socket" and
        supervisor["path"] == str(GATEWAY_SUPERVISOR_SOCKET) and
        supervisor["present"] is True and supervisor["file_type"] == "socket",
        reason)
    if value.get("continuity_slot_index") == 0:
        _require(
            gateway["main_pid"] == frozen["gateway_main_pid"] and
            gateway["invocation_id"] == frozen["gateway_invocation_id"] and
            gateway["exec_main_start_timestamp_monotonic_us"] ==
                frozen["gateway_exec_main_start_timestamp_monotonic_us"] and
            process["starttime_ticks"] == frozen["process_starttime_ticks"] and
            tool["device"] == frozen["gateway_socket_device"] and
            tool["inode"] == frozen["gateway_socket_inode"] and
            supervisor["device"] == frozen["supervisor_socket_device"] and
            supervisor["inode"] == frozen["supervisor_socket_inode"], reason)
    return gateway, process, executable, profile, domain, tool, supervisor


def validate_observer_artifact(
    artifact: Artifact, spec: "Spec",
    expected_producer_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate one independently produced raw observer receipt."""

    value = artifact.document
    schema = value.get("schema")
    fields = {
        SERVICE_OBSERVATION_SCHEMA: SERVICE_OBSERVATION_FIELDS,
        CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA:
            CAMPAIGN_CONTINUITY_OBSERVATION_FIELDS,
        FAULT_OBSERVATION_SCHEMA: FAULT_OBSERVATION_FIELDS,
        AUTHORITY_OBSERVATION_SCHEMA: AUTHORITY_OBSERVATION_FIELDS,
        CLEANUP_OBSERVATION_SCHEMA: CLEANUP_OBSERVATION_FIELDS,
    }.get(schema)
    reason = "P1_AUDIT_RAW_OBSERVER_INVALID"
    _require(fields is not None, reason)
    _require(
        isinstance(artifact.path, str) and Path(artifact.path).is_absolute() and
        str(Path(artifact.path)) == artifact.path and
        artifact.file_sha256 == digest_bytes(canonical_bytes(value)) and
        artifact.body_sha256 == value.get("body_sha256"),
        "P1_AUDIT_RAW_OBSERVER_ARTIFACT_DIGEST_INVALID")
    _exact(value, fields, reason)
    _validate_seal(value, reason)
    _reject_authority(value, "P1_AUDIT_RAW_OBSERVER_AUTHORITY_NOT_FALSE")
    _require(
        value.get("version") == 1 and value.get("status") == "COMPLETE" and
        value.get("campaign_id") == spec.campaign_id and
        _is_identifier(value.get("observer_id")) and
        value.get("observation_complete") is True and
        value.get("clock_id") == "CLOCK_BOOTTIME" and
        isinstance(value.get("boot_id"), str) and
        BOOT_ID.fullmatch(value["boot_id"]) is not None and
        _is_int(value.get("observed_at_ms"), 0) and
        _is_int(value.get("expires_at_ms"), value["observed_at_ms"] + 1) and
        value.get("source_manifest_sha256") == spec.source_manifest_sha256 and
        value.get("policy_sha256") == spec.policy_sha256 and
        value.get("strategy_sha256") == spec.strategy_sha256,
        reason)
    producer = _exact(value.get("producer"), PRODUCER_FIELDS, reason)
    _require(
        producer.get("path") == str(OBSERVER_EXECUTABLE) and
        _is_digest(producer.get("file_sha256")) and
        (expected_producer_sha256 is None or
         producer["file_sha256"] == expected_producer_sha256) and
        value.get("production_mode") == OBSERVER_PRODUCTION_MODE,
        reason)
    if schema == SERVICE_OBSERVATION_SCHEMA:
        _require(
            _is_int(value.get("observed_boottime_ns"), 0) and
            _is_identifier(value.get("service_epoch")) and
            _is_int(value.get("fencing_generation"), 0) and
            _is_int(value.get("lease_generation"), 0) and
            (value.get("transition_fault_id") is None or
             _is_identifier(value.get("transition_fault_id"))) and
            all(type(value.get(field)) is bool for field in (
                "continuity_ok", "audit_ok", "cleanup_ok")), reason)
        validate_observation_evidence(
            value.get("observation_evidence"), kind="SERVICE",
            boot_id=value["boot_id"],
            expected_boottime_ns=value["observed_boottime_ns"])
    elif schema == CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA:
        reason = "P1_AUDIT_RAW_CAMPAIGN_CONTINUITY_INVALID"
        _require(_is_int(value.get("observed_boottime_ns"), 0), reason)
        freeze_reference = _validate_reference(
            value.get("freeze_bundle"), reason)
        _require(freeze_reference == spec.freeze_bundle, reason)
        _observer_reference(
            value.get("campaign_runtime"), CAMPAIGN_RUNTIME_SCHEMA, reason)
        slot = value.get("continuity_slot_index")
        scheduled = value.get("continuity_scheduled_at_ms")
        origin = value.get("continuity_origin_ms")
        end = value.get("continuity_end_ms")
        cadence = value.get("continuity_cadence_ms")
        final_slot = value.get("continuity_final_slot")
        _require(
            _is_int(slot, 0) and _is_int(origin, 1) and
            _is_int(end, origin + 1) and _is_int(cadence, 1) and
            final_slot == (end - origin + cadence - 1) // cadence and
            slot <= final_slot and
            scheduled == min(origin + slot * cadence, end) and
            value.get("continuity_is_final") is (slot == final_slot) and
            value.get("catch_up") is False and
            scheduled <= value["observed_at_ms"] < scheduled + cadence and
            (slot != 0 or value.get("transition_fault_id") is None), reason)
        activation_reference = _observer_reference(
            value.get("activation_receipt"),
            "hepta.p1-watch-activation-receipt.v4", reason)
        lease_reference = _observer_reference(
            value.get("lease_receipt"),
            "hepta.shadow-watch-lease-receipt.v1", reason)
        activation = _exact(
            value.get("activation_receipt_document"),
            ACTIVATION_RECEIPT_FIELDS, reason)
        _validate_seal(activation, reason)
        _reject_authority(activation, reason)
        _require(
            activation.get("schema") == activation_reference["schema"] and
            activation.get("version") == 4 and
            activation.get("status") == "WATCH_GATEWAY_ACTIVATED" and
            activation.get("boot_id") == value["boot_id"] and
            activation.get("gateway_activated") is True and
            activation.get("broker_deny_all_continuity_attested") is True and
            activation.get("kill_switch_engaged") is True and
            activation.get("watch_authority_provisioned") is False and
            digest_bytes(canonical_bytes(activation)) ==
                activation_reference["file_sha256"] and
            activation["body_sha256"] ==
                activation_reference["body_sha256"], reason)
        gateway, gateway_process, gateway_executable, gateway_profile, \
            gateway_domain, tool_socket, supervisor_socket = \
            _validate_activation_gateway_projection(
                activation, value, reason)
        export_commit, export_references = _validate_export_projection(
            value, reason)
        lease = _exact(
            value.get("lease_receipt_document"), WATCH_LEASE_FIELDS, reason)
        _validate_seal(lease, reason)
        _reject_authority(lease, reason)
        generation = value.get("lease_generation")
        previous_generation = value.get("previous_lease_generation")
        _require(
            lease.get("schema") == lease_reference["schema"] and
            digest_bytes(canonical_bytes(lease)) ==
                lease_reference["file_sha256"] and
            lease["body_sha256"] == lease_reference["body_sha256"] and
            export_commit.get("lease_generation") == generation and
            export_commit.get("lease_receipt_file_sha256") ==
                lease_reference["file_sha256"] and
            export_commit.get("lease_receipt_body_sha256") ==
                lease_reference["body_sha256"] and
            lease.get("accepted") is True and
            lease.get("boundary") == "WATCH" and
            _is_int(generation, 1) and
            _is_int(previous_generation, 0) and
            previous_generation == generation - 1 and
            lease.get("lease_generation") == generation and
            lease.get("previous_lease_generation") == previous_generation and
            lease.get("previous_receipt_body_sha256") ==
                value.get("previous_lease_receipt_body_sha256") and
            _is_digest(value.get("previous_lease_receipt_body_sha256")),
            reason)
        custodian = _validate_observation_unit(
            value.get("custodian_identity"), reason)
        collector = _validate_observation_unit(
            value.get("collector_timer_identity"), reason)
        reconcile = _validate_observation_unit(
            value.get("activation_reconcile_timer_identity"), reason)
        _require(
            gateway["unit"] == "hepta-tool-gateway@alpha.service" and
            custodian["unit"] ==
                "hepta-shadow-watch-custodian@alpha.service" and
            collector["unit"] ==
                "hepta-shadow-watch-collector@alpha.timer" and
            reconcile["unit"] ==
                "hepta-p1-watch-activation-reconcile.timer" and
            tool_socket["path"] == str(GATEWAY_TOOL_SOCKET) and
            (value.get("transition_fault_id") is None or
             _is_identifier(value.get("transition_fault_id"))) and
            all(type(value.get(field)) is bool for field in (
                "persistent_stack_ok", "lease_chain_ok",
                "campaign_socket_present", "kill_switch_engaged",
                "zero_exposure")) and
            _is_int(value.get("connector_count"), 0) and
            _is_int(value.get("paper_unit_active_count"), 0) and
            isinstance(value.get("authorized_uids"), list) and
            value["authorized_uids"] ==
                sorted(set(value["authorized_uids"])) and
            all(_is_int(uid, 0) for uid in value["authorized_uids"]), reason)
        evidence = validate_observation_evidence(
            value.get("observation_evidence"), kind="CAMPAIGN_CONTINUITY",
            boot_id=value["boot_id"],
            expected_boottime_ns=value["observed_boottime_ns"], reason=reason)
        broker = _validate_observation_broker(
            evidence.get("broker_deny_all"), reason)
        _require(
            all(item in evidence["systemd_units"] for item in (
                gateway, custodian, collector, reconcile)) and
            gateway_process in evidence["processes"] and
            all(item in evidence["paths"] for item in (
                gateway_executable, gateway_profile, gateway_domain,
                tool_socket, supervisor_socket)) and
            all(any(
                item.get("path") == reference["path"] and
                item.get("present") is True and
                item.get("file_type") == "regular" and
                item.get("uid") == 0 and
                item.get("gid") == export_commit["reader_gid"] and
                item.get("mode") == 0o440 and
                item.get("nlink") == 1 and
                item.get("parent_uid") == 0 and
                item.get("parent_gid") == export_commit["reader_gid"] and
                item.get("parent_mode") == 0o750 and
                item.get("content_file_sha256") ==
                    reference["file_sha256"] and
                item.get("content_body_sha256") ==
                    reference["body_sha256"]
                for item in evidence["paths"])
                for reference in export_references) and
            broker is not None and
            broker["authorized_connector_count"] == value["connector_count"] and
            broker["authorized_uids"] == value["authorized_uids"] and
            value["persistent_stack_ok"] == (
                all(item["load_state"] == "loaded" and
                    item["active_state"] == "active"
                    for item in (gateway, custodian, collector, reconcile)) and
                gateway["sub_state"] == "running" and
                custodian["sub_state"] == "running" and
                collector["sub_state"] == "waiting" and
                reconcile["sub_state"] == "waiting" and
                tool_socket["present"] is True and
                tool_socket["file_type"] == "socket" and
                supervisor_socket["present"] is True and
                supervisor_socket["file_type"] == "socket") and
            value["lease_chain_ok"] is True and
            value["zero_exposure"] == (
                value["connector_count"] == 0 and
                not value["authorized_uids"] and
                value["paper_unit_active_count"] == 0 and
                value["campaign_socket_present"] is False and
                value["kill_switch_engaged"] is True and
                broker["deny_all"] is True and
                broker["protected_port_count"] == 4), reason)
    elif schema == FAULT_OBSERVATION_SCHEMA:
        fault_type = value.get("fault_type")
        _require(
            _is_identifier(value.get("fault_id")) and
            isinstance(fault_type, str) and
            fault_type in ALLOWED_FAULT_TYPES and
            value.get("target_id") == FAULT_TARGET_IDS[fault_type] and
            _is_int(value.get("injection_boottime_ns"), 0) and
            _is_int(value.get("recovered_boottime_ns"),
                    value["injection_boottime_ns"]) and
            all(type(value.get(field)) is bool for field in (
                "recovery_verified", "cleanup_verified",
                "authority_failure", "audit_failure", "cleanup_failure")),
            reason)
        validate_observation_evidence(
            value.get("observation_evidence"), kind="FAULT",
            boot_id=value["boot_id"],
            minimum_boottime_ns=value["recovered_boottime_ns"])
    elif schema == AUTHORITY_OBSERVATION_SCHEMA:
        _require(
            _is_int(value.get("observed_boottime_ns"), 0) and
            all(_is_int(value.get(field), 0) for field in (
                "connector_count", "paper_unit_active_count")) and
            isinstance(value.get("authorized_uids"), list) and
            value["authorized_uids"] == sorted(set(value["authorized_uids"])) and
            all(_is_int(uid, 0) for uid in value["authorized_uids"]) and
            all(type(value.get(field)) is bool for field in (
                "campaign_socket_present", "kill_switch_engaged",
                "local_boundary_safe", "local_boundary_uncertain",
                "authoritative_account_state_observed")) and
            value.get("observation_scope") == "LOCAL_HOST_BOUNDARY_ONLY" and
            value.get("authoritative_account_state_observed") is False and
            value["local_boundary_safe"] == (
                value["connector_count"] == 0 and
                not value["authorized_uids"] and
                value["paper_unit_active_count"] == 0 and
                value["campaign_socket_present"] is False and
                value["kill_switch_engaged"] is True and
                value["local_boundary_uncertain"] is False), reason)
        validate_observation_evidence(
            value.get("observation_evidence"), kind="AUTHORITY",
            boot_id=value["boot_id"],
            expected_boottime_ns=value["observed_boottime_ns"])
    else:
        _require(
            _is_int(value.get("observed_boottime_ns"), 0) and
            value.get("subject_type") in {"LAUNCHER", "FAULT", "FINAL"} and
            _is_identifier(value.get("subject_id")) and
            all(_is_int(value.get(field), 0) for field in (
                "watch_authority_count", "export_residue_count",
                "session_authority_count", "paper_unit_active_count")) and
            all(type(value.get(field)) is bool for field in (
                "campaign_socket_present", "cleanup_complete",
                "cleanup_uncertain")) and
            isinstance(value.get("errors"), list) and
            all(isinstance(item, str) and item for item in value["errors"]),
            reason)
        validate_observation_evidence(
            value.get("observation_evidence"), kind="CLEANUP",
            boot_id=value["boot_id"],
            expected_boottime_ns=value["observed_boottime_ns"])
    return value


def _observation_broker_is_safe(value: Any) -> bool:
    return (
        isinstance(value, dict) and value.get("deny_all") is True and
        value.get("authorized_connector_count") == 0 and
        value.get("authorized_uids") == [] and
        _is_int(value.get("protected_port_count"), 1))


def validate_fault_target_identity(
    value: Any, *, phase: str, target_id: str, boot_id: str,
    fault_type: str, reason: str,
) -> dict[str, Any]:
    identity = _exact(value, FAULT_TARGET_IDENTITY_FIELDS, reason)
    _validate_seal(identity, reason)
    _require(
        identity.get("schema") == FAULT_TARGET_IDENTITY_SCHEMA and
        identity.get("version") == 1 and identity.get("phase") == phase and
        identity.get("target_id") == target_id and
        identity.get("boot_id") == boot_id and
        _is_int(identity.get("observed_boottime_ns"), 0), reason)
    epoch = identity.get("service_epoch")
    fence = identity.get("fencing_generation")
    lease = identity.get("lease_generation")
    context_missing = epoch is None and fence is None and lease is None
    context_complete = (
        _is_identifier(epoch) and _is_int(fence, 0) and _is_int(lease, 0))
    _require(context_complete or (
        context_missing and fault_type in {
            "NETWORK_DENY_RELOAD", "CLOCK_STEP",
        }), reason)
    _validate_observation_identity_lists(identity, reason)
    _validate_observation_broker(identity.get("broker_deny_all"), reason)
    _require(_is_int(identity.get("residue_count"), 0), reason)
    wall_delta = identity.get("wall_clock_delta_ms")
    fixture_generation = identity.get("fixture_generation")
    fixture_expiry = identity.get("fixture_expires_boottime_ns")
    fixture_valid = identity.get("fixture_valid")
    fixture_fault = fault_type in {"TOKEN_LOSS", "LEASE_EXPIRY"}
    _require(
        ((fault_type == "CLOCK_STEP" and type(wall_delta) is int) or
         (fault_type != "CLOCK_STEP" and wall_delta is None)) and
        ((fixture_fault and _is_int(fixture_generation, 0) and
          _is_int(fixture_expiry, 0) and type(fixture_valid) is bool) or
         (not fixture_fault and fixture_generation is None and
          fixture_expiry is None and fixture_valid is None)), reason)
    return identity


FAULT_FIXTURE_PATHS = {
    "TOKEN_LOSS": "/run/hepta-p1-fault-fixture/watch-session-token.json",
    "LEASE_EXPIRY": "/run/hepta-p1-fault-fixture/watch-lease.json",
}


def _require_fault_target_transition(
    pre: dict[str, Any], post: dict[str, Any], *, fault_type: str,
    actual_ns: int, recovered_ns: int, recovery_complete: bool,
    domain_id: str, reason: str,
) -> None:
    _require(pre["observed_boottime_ns"] < post["observed_boottime_ns"], reason)
    if fault_type in {"PROCESS_KILL", "EVIDENCE_WRITER_CRASH"}:
        _require(
            len(pre["processes"]) == len(post["processes"]) == 1 and
            pre["processes"][0]["exe_device"] ==
                post["processes"][0]["exe_device"] and
            pre["processes"][0]["exe_inode"] ==
                post["processes"][0]["exe_inode"] and
            pre["processes"][0]["pid"] != post["processes"][0]["pid"] and
            pre["processes"][0]["starttime_ticks"] !=
                post["processes"][0]["starttime_ticks"] and
            pre["service_epoch"] == post["service_epoch"] and
            pre["fencing_generation"] == post["fencing_generation"] and
            pre["lease_generation"] == post["lease_generation"], reason)
        return
    if fault_type == "SERVICE_RESTART":
        _require(len(pre["systemd_units"]) ==
                 len(post["systemd_units"]) == 1, reason)
        before = pre["systemd_units"][0]
        after = post["systemd_units"][0]
        _require(
            before["unit"] == after["unit"] and
            before["unit"] == f"hepta-tool-gateway@{domain_id}.service" and
            bool(before["invocation_id"]) and bool(after["invocation_id"]) and
            before["invocation_id"] != after["invocation_id"] and
            before["main_pid"] > 0 and after["main_pid"] > 0 and
            before["main_pid"] != after["main_pid"] and
            before["exec_main_start_timestamp_monotonic_us"] !=
                after["exec_main_start_timestamp_monotonic_us"] and
            pre["service_epoch"] != post["service_epoch"] and
            pre["fencing_generation"] == post["fencing_generation"] and
            post["lease_generation"] >= pre["lease_generation"] and
            post["lease_generation"] <= pre["lease_generation"] + 1,
            reason)
        return
    if fault_type in FAULT_FIXTURE_PATHS:
        fixture_path = FAULT_FIXTURE_PATHS[fault_type]
        before = next((item for item in pre["paths"]
                       if item["path"] == fixture_path), None)
        after = next((item for item in post["paths"]
                      if item["path"] == fixture_path), None)
        _require(
            before is not None and after is not None and
            before["present"] is True and
            before["content_file_sha256"] is not None and
            (before["inode"] != after["inode"] or
             before["content_file_sha256"] !=
                after["content_file_sha256"] or
             before["content_body_sha256"] !=
                after["content_body_sha256"]) and
            pre["fixture_valid"] is True and
            pre["fixture_generation"] is not None and
            post["fixture_generation"] is not None, reason)
        _require(
            pre["service_epoch"] == post["service_epoch"] and
            pre["fencing_generation"] == post["fencing_generation"] and
            post["lease_generation"] in {
                pre["lease_generation"], pre["lease_generation"] + 1,
            }, reason)
        if fault_type == "LEASE_EXPIRY":
            _require(pre["fixture_expires_boottime_ns"] <= actual_ns, reason)
        if recovery_complete:
            _require(
                after["present"] is True and
                after["content_file_sha256"] is not None and
                post["fixture_valid"] is True and
                post["fixture_generation"] ==
                    pre["fixture_generation"] + 1 and
                post["fixture_expires_boottime_ns"] > recovered_ns,
                reason)
        else:
            _require(post["fixture_valid"] is False, reason)
        return
    if fault_type == "NETWORK_DENY_RELOAD":
        before_broker = pre["broker_deny_all"]
        after_broker = post["broker_deny_all"]
        _require(
            isinstance(before_broker, dict) and
            isinstance(after_broker, dict) and
            before_broker["deny_all"] is True and
            after_broker["deny_all"] is True and
            before_broker["helper_path"] == after_broker["helper_path"] and
            before_broker["helper_file_sha256"] ==
                after_broker["helper_file_sha256"] and
            before_broker["policy_sha256"] ==
                after_broker["policy_sha256"] and
            before_broker["checked_boottime_ns"] <
                after_broker["checked_boottime_ns"] and
            len(pre["systemd_units"]) ==
                len(post["systemd_units"]) == 1, reason)
        before = pre["systemd_units"][0]
        after = post["systemd_units"][0]
        _require(
            before["unit"] == after["unit"] and
            before["unit"] == "hepta-broker-egress-policy.service" and
            before["invocation_id"] != after["invocation_id"] and
            before["main_pid"] != after["main_pid"] and
            before["exec_main_start_timestamp_monotonic_us"] !=
                after["exec_main_start_timestamp_monotonic_us"] and
            (pre["service_epoch"], pre["fencing_generation"],
             pre["lease_generation"]) ==
                (post["service_epoch"], post["fencing_generation"],
                 post["lease_generation"]), reason)
        return
    _require(fault_type == "CLOCK_STEP" and
             isinstance(pre["broker_deny_all"], dict) and
             isinstance(post["broker_deny_all"], dict), reason)
    before_broker = dict(pre["broker_deny_all"])
    after_broker = dict(post["broker_deny_all"])
    before_broker.pop("checked_boottime_ns")
    before_broker.pop("state_sha256")
    after_broker.pop("checked_boottime_ns")
    after_broker.pop("state_sha256")
    _require(
        pre["wall_clock_delta_ms"] == 0 and
        MINIMUM_CLOCK_STEP_MS <= abs(post["wall_clock_delta_ms"]) <=
            MAXIMUM_CLOCK_STEP_MS and
        pre["service_epoch"] == post["service_epoch"] and
        pre["fencing_generation"] == post["fencing_generation"] and
        pre["lease_generation"] == post["lease_generation"] and
        pre["systemd_units"] == post["systemd_units"] and
        pre["processes"] == post["processes"] and
        pre["paths"] == post["paths"] and
        before_broker == after_broker and
        pre["broker_deny_all"]["checked_boottime_ns"] <
            post["broker_deny_all"]["checked_boottime_ns"], reason)


def validate_fault_injection_artifact(
    artifact: Artifact, spec: "Spec", planned: dict[str, Any],
    raw_fault: dict[str, Any], expected_producer_sha256: str | None = None,
) -> dict[str, Any]:
    reason = "P1_AUDIT_FAULT_INJECTION_RECEIPT_INVALID"
    value = artifact.document
    _require(
        _is_canonical_absolute_path(artifact.path) and
        artifact.file_sha256 == digest_bytes(canonical_bytes(value)) and
        artifact.body_sha256 == value.get("body_sha256"), reason)
    _exact(value, FAULT_INJECTION_FIELDS, reason)
    _validate_seal(value, reason)
    _reject_authority(value, reason)
    fault_type = planned["fault_type"]
    _require(
        value.get("schema") == FAULT_INJECTION_SCHEMA and
        value.get("version") == 1 and value.get("status") == "COMPLETE" and
        value.get("campaign_id") == spec.campaign_id and
        value.get("source_manifest_sha256") == spec.source_manifest_sha256 and
        value.get("policy_sha256") == spec.policy_sha256 and
        value.get("strategy_sha256") == spec.strategy_sha256 and
        value.get("fault_id") == planned["fault_id"] and
        value.get("fault_type") == fault_type and
        value.get("target_id") == planned["target_id"] and
        value.get("clock_id") == "CLOCK_BOOTTIME" and
        value.get("boot_id") == raw_fault["boot_id"] and
        value.get("planned_injection_boottime_ns") ==
            planned["inject_at_boottime_ns"] and
        value.get("maximum_recovery_ns") == planned["maximum_recovery_ns"] and
        _is_identifier(value.get("injector_id")) and
        value.get("injector_uid") == 0 and value.get("injector_gid") == 0 and
        value.get("injection_scope") == "P1_DECLARED_FAULT_ONLY" and
        value.get("production_mode") == FAULT_INJECTOR_PRODUCTION_MODE and
        _is_digest(value.get("action_receipt_sha256")) and
        all(type(value.get(field)) is bool for field in (
            "injection_performed", "recovery_complete", "cleanup_complete",
            "authority_failure", "audit_failure", "cleanup_failure")),
        reason)
    producer = _exact(value.get("producer"), PRODUCER_FIELDS, reason)
    _require(
        producer.get("path") == str(FAULT_INJECTOR_EXECUTABLE) and
        _is_digest(producer.get("file_sha256")) and
        (expected_producer_sha256 is None or
         producer["file_sha256"] == expected_producer_sha256), reason)
    _validate_reference(value.get("pins_reference"), reason)
    _require(_is_int(value.get("journal_predecessor_sequence"), 1) and
             _is_digest(value.get("journal_predecessor_body_sha256")), reason)
    issued = value.get("issued_at_ms")
    expires = value.get("expires_at_ms")
    _require(
        _is_int(issued, 0) and _is_int(expires, issued + 1) and
        issued <= raw_fault["observed_at_ms"] < expires, reason)
    planned_ns = planned["inject_at_boottime_ns"]
    actual_ns = value.get("actual_injection_boottime_ns")
    recovered_ns = value.get("recovered_boottime_ns")
    _require(
        _is_int(actual_ns, planned_ns) and
        actual_ns - planned_ns <= planned["maximum_injection_lateness_ns"] and
        _is_int(recovered_ns, actual_ns) and
        recovered_ns - actual_ns <= planned["maximum_recovery_ns"], reason)
    pre = validate_fault_target_identity(
        value.get("pre_identity"), phase="PRE",
        target_id=planned["target_id"], boot_id=value["boot_id"],
        fault_type=fault_type, reason=reason)
    post = validate_fault_target_identity(
        value.get("post_identity"), phase="POST",
        target_id=planned["target_id"], boot_id=value["boot_id"],
        fault_type=fault_type, reason=reason)
    _require(
        0 <= actual_ns - pre["observed_boottime_ns"] <=
            30 * 1_000_000_000 and
        0 <= post["observed_boottime_ns"] - recovered_ns <=
            30 * 1_000_000_000 and
        raw_fault["injection_boottime_ns"] == planned_ns and
        raw_fault["recovered_boottime_ns"] == recovered_ns and
        raw_fault["recovery_verified"] is (
            value["injection_performed"] and value["recovery_complete"]) and
        raw_fault["cleanup_verified"] is value["cleanup_complete"] and
        raw_fault["authority_failure"] is value["authority_failure"] and
        raw_fault["audit_failure"] is value["audit_failure"] and
        raw_fault["cleanup_failure"] is value["cleanup_failure"], reason)
    if pre["fencing_generation"] is not None:
        _require(
            post["service_epoch"] is not None and
            post["fencing_generation"] >= pre["fencing_generation"] and
            post["fencing_generation"] <= pre["fencing_generation"] + 1 and
            post["lease_generation"] >= pre["lease_generation"] and
            post["lease_generation"] <= pre["lease_generation"] + 1,
            reason)
    _require_fault_target_transition(
        pre, post, fault_type=fault_type, actual_ns=actual_ns,
        recovered_ns=recovered_ns,
        recovery_complete=value["recovery_complete"],
        domain_id=spec.domain_id, reason=reason)
    return value


def _reference(value: Artifact) -> dict[str, str]:
    return {
        "path": value.path, "file_sha256": value.file_sha256,
        "body_sha256": value.body_sha256,
    }


def _validate_reference(value: Any, reason: str) -> dict[str, str]:
    result = _exact(value, REFERENCE_FIELDS, reason)
    _require(type(result.get("path")) is str and
             Path(result["path"]).is_absolute() and
             Path(os.path.normpath(result["path"])) == Path(result["path"]) and
             _is_digest(result.get("file_sha256")) and
             _is_digest(result.get("body_sha256")), reason)
    return result


def _calendar_source_sha256() -> str:
    contract = {
        "schema": "hepta.p1-safety-soak-calendar-rule.v1",
        "calendar_id": CALENDAR_ID, "calendar_version": CALENDAR_VERSION,
        "instrument": "EURUSD", "year": 2026,
        "trading_timezone": CALENDAR_TIMEZONE,
        "core_open_local": "09:00", "core_close_local": "16:00",
        "maintenance_open_local": "12:00",
        "maintenance_close_local": "12:15",
        "excluded_days": sorted(CALENDAR_EXCLUDED_DAYS_2026),
    }
    return digest_bytes(canonical_bytes(contract))


def _expected_calendar_sessions(
    slots: Sequence[int], reason: str,
) -> list[dict[str, Any]]:
    try:
        zone = ZoneInfo(CALENDAR_TIMEZONE)
        candidate_days = sorted({
            datetime.fromtimestamp(slot / 1000, tz=timezone.utc)
            .astimezone(zone).date() for slot in slots
        })
    except (OSError, OverflowError, ValueError,
            ZoneInfoNotFoundError) as error:
        raise EvidenceError(reason) from error
    days = [item for item in candidate_days
            if item.year == 2026 and item.weekday() < 5 and
            item.isoformat() not in CALENDAR_EXCLUDED_DAYS_2026]
    _require(MINIMUM_TRADING_DAYS <= len(days) <= MAXIMUM_TRADING_DAYS, reason)
    result: list[dict[str, Any]] = []
    for day in days:
        opens = datetime(day.year, day.month, day.day, 9, 0, tzinfo=zone)
        closes = datetime(day.year, day.month, day.day, 16, 0, tzinfo=zone)
        maintenance_start = datetime(
            day.year, day.month, day.day, 12, 0, tzinfo=zone)
        maintenance_end = datetime(
            day.year, day.month, day.day, 12, 15, tzinfo=zone)
        result.append({
            "trading_day": day.isoformat(),
            "opens_at_ms": int(opens.timestamp() * 1000),
            "closes_at_ms": int(closes.timestamp() * 1000),
            "maintenance_windows": [{
                "opens_at_ms": int(maintenance_start.timestamp() * 1000),
                "closes_at_ms": int(maintenance_end.timestamp() * 1000),
            }],
        })
    return result


def validate_freeze_lineage(
    bundle_artifact: Artifact, calendar_artifact: Artifact, spec: "Spec",
    audit_producer: Mapping[str, str],
) -> dict[str, dict[str, str]]:
    reason = "P1_AUDIT_FREEZE_LINEAGE_INVALID"
    value = bundle_artifact.document
    _exact(value, FREEZE_BUNDLE_FIELDS, reason)
    _validate_seal(value, reason)
    _reject_authority(value, reason)
    _require(
        _reference(bundle_artifact) == spec.freeze_bundle and
        value.get("schema") == FREEZE_BUNDLE_SCHEMA and
        value.get("version") == 1 and value.get("status") == "FROZEN" and
        value.get("round") == 114 and
        value.get("campaign_id") == spec.campaign_id and
        value.get("domain_id") == spec.domain_id and
        value.get("source_manifest_sha256") == spec.source_manifest_sha256 and
        value.get("policy_sha256") == spec.policy_sha256 and
        value.get("strategy_id") == spec.strategy_id and
        value.get("strategy_version") == spec.strategy_version and
        value.get("strategy_sha256") == spec.strategy_sha256 and
        value.get("production_mode") == FREEZER_PRODUCTION_MODE and
        value.get("trading_timezone") == CALENDAR_TIMEZONE and
        value.get("calendar_id") == CALENDAR_ID and
        value.get("calendar_version") == CALENDAR_VERSION and
        value.get("calendar_source_sha256") == _calendar_source_sha256() and
        isinstance(value.get("boot_id"), str) and
        BOOT_ID.fullmatch(value["boot_id"]) is not None and
        _is_int(value.get("frozen_boottime_ns"), 0) and
        _is_int(value.get("issued_at_ms"), 0) and
        _is_int(value.get("expires_at_ms"), value["issued_at_ms"] + 1),
        reason)
    freeze_id = value.get("freeze_id")
    _require(type(freeze_id) is str and
             re.fullmatch(r"[0-9a-f]{32}", freeze_id) is not None, reason)
    freezer = _exact(value.get("producer"), PRODUCER_FIELDS, reason)
    _require(freezer.get("path") == str(FREEZER_EXECUTABLE) and
             _is_digest(freezer.get("file_sha256")), reason)
    _validate_reference(value.get("source_baseline"), reason)
    source_pin_values = value.get("source_producer_pins")
    _require(isinstance(source_pin_values, list) and
             len(source_pin_values) == len(SOURCE_PRODUCER_PATHS), reason)
    source_pins: dict[str, dict[str, str]] = {}
    for item in source_pin_values:
        _exact(item, SOURCE_PRODUCER_PIN_FIELDS, reason)
        role = item.get("role")
        _require(type(role) is str and role in SOURCE_PRODUCER_PATHS and
                 role not in source_pins, reason)
        source_path, installed_path = SOURCE_PRODUCER_PATHS[role]
        _require(item.get("source_path") == source_path and
                 item.get("installed_path") == installed_path and
                 _is_digest(item.get("file_sha256")), reason)
        source_pins[role] = dict(item)
    _require(source_pin_values == sorted(
                 source_pin_values, key=lambda item: item["role"]) and
             freezer["file_sha256"] ==
                source_pins["campaign_freezer"]["file_sha256"] and
             audit_producer == {
                "path": str(INSTALLED_EXECUTABLE),
                "file_sha256": source_pins["auditor"]["file_sha256"],
             }, reason)
    formal = value.get("formal_policies")
    _require(isinstance(formal, list) and bool(formal), reason)
    formal_by_id = {item["campaign_id"]: item for item in spec.formal_campaigns}
    slots: list[int] = []
    seen: set[str] = set()
    previous_teardown = 0
    for item in formal:
        _exact(item, FREEZE_FORMAL_REFERENCE_FIELDS, reason)
        identifier = item.get("campaign_id")
        _require(type(identifier) is str and identifier in formal_by_id and
                 identifier not in seen and
                 item.get("file_sha256") ==
                    formal_by_id[identifier]["policy_file_sha256"] and
                 item.get("body_sha256") ==
                    formal_by_id[identifier]["policy_body_sha256"], reason)
        seen.add(identifier)
        valid_after = item.get("valid_after_ms")
        interval = item.get("slot_interval_ms")
        maximum = item.get("maximum_iterations")
        expires = item.get("expires_at_ms")
        launcher_start = item.get("launcher_start_ms")
        dispatch = item.get("launcher_dispatch_at_ms")
        completion = item.get("launcher_completion_deadline_ms")
        projection = item.get("projection_deadline_ms")
        teardown = item.get("teardown_deadline_ms")
        _require(_is_int(valid_after, 0) and _is_int(interval, 1) and
                 _is_int(maximum, 1) and
                 interval == POLICY_SLOT_INTERVAL_MS and
                 maximum == POLICY_MAXIMUM_ITERATIONS and
                 _is_int(launcher_start, 1) and _is_int(dispatch, 1) and
                 expires == valid_after + interval * maximum and
                 launcher_start == valid_after - LAUNCHER_WARMUP_MS and
                 dispatch == launcher_start - LAUNCHER_EARLY_START_LEAD_MS and
                 completion == expires + MAXIMUM_LAUNCH_LATENESS_MS and
                 projection == expires +
                    POST_FORMAL_PROJECTION_GUARD_MS and
                 teardown == expires + POST_FORMAL_TEARDOWN_GUARD_MS and
                 (previous_teardown == 0 or
                  valid_after == (
                    (previous_teardown + LAUNCHER_WARMUP_MS +
                     LAUNCHER_EARLY_START_LEAD_MS) // interval + 1
                  ) * interval),
                 reason)
        previous_teardown = teardown
        slots.extend(valid_after + offset * interval
                     for offset in range(maximum))
    _require(seen == set(formal_by_id) and slots == sorted(set(slots)) and
             value.get("scheduled_decision_count") == len(slots), reason)
    strategy_files = value.get("strategy_files")
    _require(isinstance(strategy_files, list) and len(strategy_files) == 5 and
             {item.get("role") for item in strategy_files} == {
                 "config", "evaluator", "context_builder", "normalizer",
                 "contracts"}, reason)
    for item in strategy_files:
        _exact(item, FREEZE_STRATEGY_FILE_FIELDS, reason)
        _validate_reference({key: item[key] for key in REFERENCE_FIELDS}, reason)
    anchors = value.get("anchors")
    _require(isinstance(anchors, dict) and set(anchors) == {
        "source_anchor", "policy_anchor", "strategy_anchor",
        "frozen_schedule", "frozen_fault_schedule"}, reason)
    for item in anchors.values():
        _validate_reference(item, reason)
    calendar = calendar_artifact.document
    _exact(calendar, CALENDAR_FIELDS, reason)
    _validate_seal(calendar, reason)
    _reject_authority(calendar, reason)
    _require(
        _reference(calendar_artifact) == value.get("trading_calendar") and
        calendar.get("schema") == CALENDAR_SCHEMA and
        calendar.get("version") == 1 and calendar.get("status") == "FROZEN" and
        calendar.get("freeze_id") == freeze_id and
        calendar.get("producer") == freezer and
        calendar.get("production_mode") == FREEZER_PRODUCTION_MODE and
        calendar.get("calendar_id") == CALENDAR_ID and
        calendar.get("calendar_version") == CALENDAR_VERSION and
        calendar.get("calendar_source_sha256") == _calendar_source_sha256() and
        calendar.get("trading_timezone") == CALENDAR_TIMEZONE and
        calendar.get("issued_at_ms") == value.get("issued_at_ms") and
        calendar.get("expires_at_ms") == value.get("expires_at_ms") and
        calendar_artifact.body_sha256 == value.get("trading_calendar_sha256")
            == spec.trading_calendar_sha256,
        reason)
    expected_sessions = _expected_calendar_sessions(slots, reason)
    sessions = calendar.get("sessions")
    _require(sessions == expected_sessions, reason)
    for session in sessions:
        _exact(session, CALENDAR_SESSION_FIELDS, reason)
        windows = session.get("maintenance_windows")
        _require(isinstance(windows, list) and len(windows) == 1, reason)
        for window in windows:
            _exact(window, CALENDAR_WINDOW_FIELDS, reason)
    eligible: list[int] = []
    observed_days: set[str] = set()
    for slot in slots:
        for session in sessions:
            if not (session["opens_at_ms"] <= slot < session["closes_at_ms"]):
                continue
            if any(window["opens_at_ms"] <= slot < window["closes_at_ms"]
                   for window in session["maintenance_windows"]):
                continue
            eligible.append(slot)
            observed_days.add(session["trading_day"])
            break
    days = [item["trading_day"] for item in sessions]
    planned_faults = value.get("planned_faults")
    _require(observed_days == set(days) and
             days == value.get("declared_trading_days") ==
                list(spec.declared_trading_days) and
             eligible == value.get("eligible_scheduled_at_ms") ==
                list(spec.eligible_scheduled_at_ms) and
             len(eligible) >= MINIMUM_ELIGIBLE_DECISIONS and
             isinstance(planned_faults, list) and bool(planned_faults), reason)
    formal_windows = {item["campaign_id"]: item for item in formal}
    frozen_boottime = value["frozen_boottime_ns"]
    issued_at = value["issued_at_ms"]
    for raw in planned_faults:
        fault = _exact(raw, PLANNED_FAULT_FIELDS, reason)
        window = formal_windows.get(fault.get("formal_campaign_id"))
        injection = fault.get("inject_at_boottime_ns")
        lateness = fault.get("maximum_injection_lateness_ns")
        recovery = fault.get("maximum_recovery_ns")
        _require(
            window is not None and _is_int(injection, 0) and
            _is_int(lateness, 0) and _is_int(recovery, 1), reason)
        window_start = frozen_boottime + (
            window["valid_after_ms"] - issued_at) * 1_000_000
        window_end = frozen_boottime + (
            window["expires_at_ms"] - issued_at) * 1_000_000
        _require(
            window_start <= injection and
            injection + lateness + recovery < window_end,
            reason)
    return source_pins


def assert_installed_source_pins(
    source_pins: Mapping[str, Mapping[str, str]],
) -> None:
    """Reopen every frozen production helper and verify its installed SHA."""

    reason = "P1_AUDIT_INSTALLED_SOURCE_PIN_DRIFT"
    _require(set(source_pins) == set(SOURCE_PRODUCER_PATHS), reason)
    for role in sorted(SOURCE_PRODUCER_PATHS):
        _source_path, installed_path = SOURCE_PRODUCER_PATHS[role]
        pin = source_pins[role]
        _require(pin.get("installed_path") == installed_path and
                 _is_digest(pin.get("file_sha256")), reason)
        payload = secure_read(
            Path(installed_path),
            f"P1_AUDIT_INSTALLED_{role.upper()}",
            MAXIMUM_OUTPUT_BYTES,
            frozenset({0o555, 0o755}),
        )
        _require(digest_bytes(payload) == pin["file_sha256"], reason)


@dataclass(frozen=True)
class Spec:
    artifact: Artifact
    campaign_id: str
    domain_id: str
    source_manifest_sha256: str
    policy_sha256: str
    strategy_id: str
    strategy_version: str
    strategy_sha256: str
    formal_campaigns: tuple[dict[str, str], ...]
    declared_trading_days: tuple[str, ...]
    trading_timezone: ZoneInfo
    trading_calendar_sha256: str
    eligible_scheduled_at_ms: tuple[int, ...]
    scheduled_decision_count: int
    minimum_eligible_decisions: int
    minimum_complete_ppm: int
    minimum_boottime_duration_ns: int
    maximum_checkpoint_gap_ns: int
    maximum_decision_lateness_ms: int
    fault_plan_body_sha256: str
    independent_auditor_id: str
    freeze_bundle: dict[str, str]


def validate_spec(artifact: Artifact) -> Spec:
    value = artifact.document
    _exact(value, SPEC_FIELDS, "P1_AUDIT_SPEC_FIELDS_INVALID")
    _validate_seal(value, "P1_AUDIT_SPEC_DIGEST_INVALID")
    _reject_authority(value, "P1_AUDIT_SPEC_AUTHORITY_NOT_FALSE")
    _require(value.get("schema") == "hepta.p1-safety-soak-campaign-spec.v1" and
             value.get("version") == 1,
             "P1_AUDIT_SPEC_SCHEMA_INVALID")
    for field in ("campaign_id", "domain_id", "strategy_id",
                  "strategy_version", "independent_auditor_id"):
        _require(_is_identifier(value.get(field)),
                 "P1_AUDIT_SPEC_IDENTIFIER_INVALID")
    for field in ("source_manifest_sha256", "policy_sha256",
                  "strategy_sha256", "fault_plan_body_sha256"):
        _require(_is_digest(value.get(field)),
                 "P1_AUDIT_SPEC_DIGEST_FIELD_INVALID")

    formal_values = value.get("formal_campaigns")
    _require(isinstance(formal_values, list) and bool(formal_values),
             "P1_AUDIT_SPEC_FORMAL_CAMPAIGNS_INVALID")
    formal_campaigns: list[dict[str, str]] = []
    identifiers: list[str] = []
    for formal in formal_values:
        _exact(formal, FORMAL_CAMPAIGN_FIELDS,
               "P1_AUDIT_SPEC_FORMAL_CAMPAIGN_FIELDS_INVALID")
        _require(_is_identifier(formal.get("campaign_id")) and all(
            _is_digest(formal.get(field)) for field in (
                "campaign_sha256", "policy_body_sha256",
                "policy_file_sha256")),
            "P1_AUDIT_SPEC_FORMAL_CAMPAIGN_INVALID")
        identifiers.append(formal["campaign_id"])
        formal_campaigns.append(formal)
    _require(len(identifiers) == len(set(identifiers)),
             "P1_AUDIT_SPEC_FORMAL_CAMPAIGN_DUPLICATE")

    days = value.get("declared_trading_days")
    _require(_is_sorted_unique_text(days) and
             MINIMUM_TRADING_DAYS <= len(days) <= MAXIMUM_TRADING_DAYS,
             "P1_AUDIT_SPEC_TRADING_DAYS_INVALID")
    parsed_days: list[date] = []
    try:
        parsed_days = [date.fromisoformat(item) for item in days]
    except ValueError as error:
        raise EvidenceError("P1_AUDIT_SPEC_TRADING_DAYS_INVALID") from error
    _require(all(day.isoformat() == raw and day.weekday() < 5
                 for day, raw in zip(parsed_days, days)),
             "P1_AUDIT_SPEC_TRADING_DAYS_INVALID")
    try:
        trading_timezone = ZoneInfo(value.get("trading_timezone"))
    except (TypeError, ZoneInfoNotFoundError) as error:
        raise EvidenceError("P1_AUDIT_SPEC_TIMEZONE_INVALID") from error
    calendar_digest = value.get("trading_calendar_sha256")
    _require(
        value.get("trading_timezone") == CALENDAR_TIMEZONE and
        _is_digest(calendar_digest),
        "P1_AUDIT_SPEC_TRADING_CALENDAR_BINDING_INVALID")
    freeze_bundle = _validate_reference(
        value.get("freeze_bundle"), "P1_AUDIT_SPEC_FREEZE_BUNDLE_INVALID")

    scheduled = value.get("scheduled_decision_count")
    minimum_decisions = value.get("minimum_eligible_decisions")
    eligible_schedule = value.get("eligible_scheduled_at_ms")
    completeness = value.get("minimum_complete_ppm")
    duration = value.get("minimum_boottime_duration_ns")
    maximum_gap = value.get("maximum_checkpoint_gap_ns")
    lateness = value.get("maximum_decision_lateness_ms")
    _require(_is_int(scheduled, MINIMUM_ELIGIBLE_DECISIONS) and
             _is_int(minimum_decisions, MINIMUM_ELIGIBLE_DECISIONS) and
             minimum_decisions <= scheduled,
             "P1_AUDIT_SPEC_DECISION_THRESHOLD_INVALID")
    _require(
        isinstance(eligible_schedule, list) and
        len(eligible_schedule) >= minimum_decisions and
        len(eligible_schedule) <= scheduled and
        all(_is_int(item, 0) for item in eligible_schedule) and
        eligible_schedule == sorted(set(eligible_schedule)),
        "P1_AUDIT_SPEC_ELIGIBLE_SCHEDULE_INVALID")
    declared_day_set = set(days)
    _require(all(
        datetime.fromtimestamp(item / 1000, tz=timezone.utc)
        .astimezone(trading_timezone).date().isoformat() in declared_day_set
        for item in eligible_schedule),
        "P1_AUDIT_SPEC_ELIGIBLE_SCHEDULE_INVALID")
    _require(_is_int(completeness, STRICTLY_GREATER_THAN_99_PERCENT_PPM + 1)
             and completeness <= ONE_HUNDRED_PERCENT_PPM,
             "P1_AUDIT_SPEC_COMPLETENESS_THRESHOLD_INVALID")
    _require(_is_int(duration, MINIMUM_BOOTTIME_DURATION_NS),
             "P1_AUDIT_SPEC_DURATION_THRESHOLD_INVALID")
    _require(_is_int(maximum_gap, 1) and
             maximum_gap <= MAXIMUM_CHECKPOINT_GAP_NS,
             "P1_AUDIT_SPEC_CHECKPOINT_GAP_INVALID")
    _require(_is_int(lateness, 0) and lateness <= 15 * 60 * 1000,
             "P1_AUDIT_SPEC_LATENESS_INVALID")
    _require(_is_int(value.get("frozen_at_ms"), 0),
             "P1_AUDIT_SPEC_FROZEN_TIME_INVALID")

    return Spec(
        artifact=artifact, campaign_id=value["campaign_id"],
        domain_id=value["domain_id"],
        source_manifest_sha256=value["source_manifest_sha256"],
        policy_sha256=value["policy_sha256"],
        strategy_id=value["strategy_id"],
        strategy_version=value["strategy_version"],
        strategy_sha256=value["strategy_sha256"],
        formal_campaigns=tuple(formal_campaigns),
        declared_trading_days=tuple(days),
        trading_timezone=trading_timezone,
        trading_calendar_sha256=calendar_digest,
        eligible_scheduled_at_ms=tuple(eligible_schedule),
        scheduled_decision_count=scheduled,
        minimum_eligible_decisions=minimum_decisions,
        minimum_complete_ppm=completeness,
        minimum_boottime_duration_ns=duration,
        maximum_checkpoint_gap_ns=maximum_gap,
        maximum_decision_lateness_ms=lateness,
        fault_plan_body_sha256=value["fault_plan_body_sha256"],
        independent_auditor_id=value["independent_auditor_id"],
        freeze_bundle=freeze_bundle,
    )


def validate_campaign_runtime(
    artifact: Artifact, spec: Spec, freeze_bundle: Artifact,
    fault_plan: Artifact, *, expected_observer_sha256: str | None,
) -> dict[str, Any]:
    """Validate the immutable campaign-wide continuity grid independently."""

    reason = "P1_AUDIT_CAMPAIGN_RUNTIME_INVALID"
    value = artifact.document
    _exact(value, CAMPAIGN_RUNTIME_FIELDS, reason)
    _validate_seal(value, reason)
    _reject_authority(value, reason)
    _require(
        artifact.file_sha256 == digest_bytes(canonical_bytes(value)) and
        artifact.body_sha256 == value.get("body_sha256") and
        value.get("schema") == CAMPAIGN_RUNTIME_SCHEMA and
        value.get("version") == 1 and value.get("status") == "FROZEN" and
        value.get("campaign_id") == spec.campaign_id and
        value.get("round") == 114 and
        value.get("boot_id") == freeze_bundle.document.get("boot_id"),
        reason)
    issued = value.get("issued_at_ms")
    expires = value.get("expires_at_ms")
    _require(
        _is_int(issued, freeze_bundle.document.get("issued_at_ms")) and
        _is_int(expires, issued + 1) and
        expires <= freeze_bundle.document.get("expires_at_ms") and
        _validate_reference(value.get("freeze_bundle"), reason) ==
            spec.freeze_bundle and
        _validate_reference(value.get("campaign_spec"), reason) ==
            _reference(spec.artifact) and
        _validate_reference(value.get("fault_plan"), reason) ==
            _reference(fault_plan),
        reason)
    frozen_formals = freeze_bundle.document.get("formal_policies")
    _require(isinstance(frozen_formals, list) and bool(frozen_formals), reason)
    frozen_by_id = {item.get("campaign_id"): item for item in frozen_formals}
    formals = value.get("formal_campaigns")
    _require(isinstance(formals, list) and bool(formals), reason)
    identifiers: list[str] = []
    previous_teardown: int | None = None
    for raw in formals:
        item = _exact(raw, FORMAL_RUNTIME_FIELDS, reason)
        formal_id = item.get("formal_campaign_id")
        frozen = frozen_by_id.get(formal_id)
        _require(_is_identifier(formal_id) and frozen is not None and
                 _is_identifier(item.get("probe_campaign_id")), reason)
        identifiers.append(formal_id)
        dispatch = item.get("launcher_dispatch_at_ms")
        start = item.get("launcher_start_ms")
        valid_after = item.get("valid_after_ms")
        interval = item.get("slot_interval_ms")
        maximum = item.get("maximum_iterations")
        formal_expiry = item.get("expires_at_ms")
        completion = item.get("launcher_completion_deadline_ms")
        projection = item.get("projection_deadline_ms")
        teardown = item.get("teardown_deadline_ms")
        policy = _validate_reference(item.get("policy"), reason)
        _require(
            _is_int(dispatch, 1) and _is_int(start, dispatch + 1) and
            _is_int(valid_after, start + 1) and _is_int(interval, 1) and
            _is_int(maximum, 1) and
            interval == POLICY_SLOT_INTERVAL_MS and
            maximum == POLICY_MAXIMUM_ITERATIONS and
            formal_expiry == valid_after + interval * maximum and
            _is_int(completion, formal_expiry) and
            _is_int(projection, completion) and
            _is_int(teardown, projection) and
            (previous_teardown is None or
             valid_after == (
                (previous_teardown + LAUNCHER_WARMUP_MS +
                 LAUNCHER_EARLY_START_LEAD_MS) // interval + 1
             ) * interval) and
            all(item.get(field) == frozen.get(field) for field in (
                "launcher_start_ms", "launcher_dispatch_at_ms",
                "valid_after_ms", "slot_interval_ms", "maximum_iterations",
                "expires_at_ms", "launcher_completion_deadline_ms",
                "projection_deadline_ms", "teardown_deadline_ms")) and
            policy == {
                "path": frozen.get("path"),
                "file_sha256": frozen.get("file_sha256"),
                "body_sha256": frozen.get("body_sha256"),
            }, reason)
        for field in (
                "launcher_receipt_path", "verified_closure_path",
                "artifact_root"):
            _require(_is_canonical_absolute_path(item.get(field)), reason)
        previous_teardown = teardown
    _require(
        identifiers == [item["campaign_id"] for item in spec.formal_campaigns]
        and len(identifiers) == len(set(identifiers)) and
        value.get("pin_formal_campaign_id") in set(identifiers) and
        previous_teardown is not None and expires > previous_teardown,
        reason)
    cadence = value.get("observer_cadence_ms")
    lateness = value.get("maximum_slot_lateness_ms")
    _require(
        _is_int(cadence, 1) and _is_int(lateness, 0) and lateness < cadence and
        cadence * 1_000_000 <= spec.maximum_checkpoint_gap_ns,
        reason)
    for field in (
            "state_root", "raw_observation_directory", "recorder_root",
            "injector_journal_directory", "injector_output_directory",
            "control_directory"):
        _require(_is_canonical_absolute_path(value.get(field)), reason)
    executables = value.get("executables")
    _require(isinstance(executables, dict) and
             "independent_observer" in executables, reason)
    for executable in executables.values():
        _exact(executable, RUNTIME_EXECUTABLE_FIELDS, reason)
        _require(_is_canonical_absolute_path(executable.get("path")) and
                 _is_digest(executable.get("file_sha256")), reason)
    observer = executables["independent_observer"]
    _require(
        observer.get("path") == str(OBSERVER_EXECUTABLE) and
        (expected_observer_sha256 is None or
         observer.get("file_sha256") == expected_observer_sha256), reason)
    return value


@dataclass
class Findings:
    no_go: set[str]
    halt: set[str]

    def add(self, error: EvidenceError) -> None:
        (self.halt if error.halt else self.no_go).add(error.reason)

    def require(self, condition: bool, reason: str,
                *, halt: bool = False) -> None:
        if not condition:
            (self.halt if halt else self.no_go).add(reason)


def _formal_map(spec: Spec) -> dict[str, dict[str, str]]:
    return {item["campaign_id"]: item for item in spec.formal_campaigns}


def _validate_custodian_closure(value: Any, spec: Spec,
                                campaign_id: str) -> None:
    closure = _exact(value, CUSTODIAN_CLOSURE_FIELDS,
                     "P1_AUDIT_LAUNCHER_CUSTODIAN_CLOSURE_INVALID")
    _validate_seal(closure, "P1_AUDIT_LAUNCHER_CUSTODIAN_DIGEST_INVALID")
    _require(
        closure.get("schema") == "hepta.shadow-watch-custodian-closure.v1" and
        closure.get("version") == 1 and
        closure.get("domain_id") == spec.domain_id and
        closure.get("campaign_id") == campaign_id and
        _is_int(closure.get("lease_generation"), 1) and
        closure.get("authoritative_revoke_outcome") in {
            "ACCEPTED", "ALREADY_ABSENT", "EXPIRED"} and
        closure.get("local_authority_removed") is True and
        closure.get("export_evidence_removed") is True,
        "P1_AUDIT_LAUNCHER_CUSTODIAN_CLOSURE_INVALID")


def validate_launcher(
    artifact: Artifact, spec: Spec,
    expected_launcher_sha256: str | None = None,
) -> dict[str, Any]:
    value = artifact.document
    _exact(value, LAUNCHER_RECEIPT_FIELDS,
           "P1_AUDIT_LAUNCHER_FIELDS_INVALID")
    _validate_seal(value, "P1_AUDIT_LAUNCHER_DIGEST_INVALID")
    _reject_authority(value, "P1_AUDIT_LAUNCHER_AUTHORITY_NOT_FALSE")
    campaign_id = value.get("formal_campaign_id")
    formal = _formal_map(spec).get(campaign_id)
    _require(formal is not None, "P1_AUDIT_LAUNCHER_CAMPAIGN_INVALID")
    _require(
        value.get("schema") ==
            "hepta.p1-shadow-admission-launcher-receipt.v1" and
        value.get("version") == 1 and value.get("domain_id") == spec.domain_id,
        "P1_AUDIT_LAUNCHER_SCHEMA_INVALID")
    cleanup = value.get("cleanup_errors")
    _require(isinstance(cleanup, list) and
             all(isinstance(item, str) and item for item in cleanup),
             "P1_AUDIT_LAUNCHER_CLEANUP_FIELDS_INVALID")
    helper_sha256 = value.get("helper_sha256")
    _require(isinstance(helper_sha256, dict) and bool(helper_sha256) and
             all(type(key) is str and key.endswith("_sha256") and
                 _is_digest(item) for key, item in helper_sha256.items()) and
             "launcher_sha256" in helper_sha256 and
             (expected_launcher_sha256 is None or
              helper_sha256["launcher_sha256"] == expected_launcher_sha256),
             "P1_AUDIT_LAUNCHER_HELPER_BINDING_INVALID")
    identity = _exact(
        value.get("launcher_identity"), LAUNCHER_IDENTITY_FIELDS,
        "P1_AUDIT_LAUNCHER_IDENTITY_INVALID")
    match = re.search(r"round([1-9][0-9]*)\Z", str(campaign_id))
    _require(match is not None and
             identity.get("unit") ==
                f"hepta-p1-shadow-admission-round{match.group(1)}.service" and
             type(identity.get("invocation_id")) is str and
             re.fullmatch(r"[0-9a-f]{32}", identity["invocation_id"]) is not None and
             _is_int(identity.get("main_pid"), 2) and
             identity.get("type") == "exec" and
             identity.get("restart") == "no" and
             identity.get("remain_after_exit") == "no" and
             identity.get("user") == "root" and identity.get("group") == "root" and
             identity.get("launcher_sha256") == helper_sha256["launcher_sha256"] and
             isinstance(identity.get("exec_start"), list) and
             identity["exec_start"] == [
                 str(LAUNCHER_EXECUTABLE), "--probe-campaign-id",
                 value.get("probe_campaign_id"), "--formal-campaign-id",
                 campaign_id, "--formal-start-ms", str(value.get("formal_start_ms")),
             ] and isinstance(identity.get("environment"), dict) and
             isinstance(identity.get("conflicts"), list) and
             len(identity["conflicts"]) == len(set(identity["conflicts"])),
             "P1_AUDIT_LAUNCHER_IDENTITY_INVALID")
    if value.get("status") != "FORMAL_COMPLETE" or value.get("reason") is not None:
        return value
    _require(value.get("formal_policy_file_sha256") ==
             formal["policy_file_sha256"],
             "P1_AUDIT_LAUNCHER_POLICY_DRIFT")
    expected = value.get("formal_expected_iterations")
    completed = value.get("formal_completed_iterations")
    _require(_is_int(expected, 1) and completed == expected,
             "P1_AUDIT_LAUNCHER_ITERATION_COUNT_INVALID")
    for field in (
            "formal_verified_closure_file_sha256",
            "formal_verified_closure_body_sha256",
            "formal_controller_status_file_sha256",
            "formal_observer_state_file_sha256", "formal_host_result_sha256",
            "activation_receipt_file_sha256", "activation_receipt_body_sha256",
            "activation_profile_receipt_file_sha256",
            "activation_profile_receipt_body_sha256"):
        _require(_is_digest(value.get(field)),
                 "P1_AUDIT_LAUNCHER_DIGEST_FIELD_INVALID")
    _require(_is_int(value.get("formal_valid_after_ms"), 0) and
             _is_int(value.get("formal_final_generation"), 1) and
             _is_int(value.get("formal_generation"), 1) and
             value["formal_final_generation"] >= value["formal_generation"] and
             isinstance(value.get("execution_service_epoch"), str) and
             bool(value["execution_service_epoch"]) and
             _is_int(value.get("execution_service_fencing_generation"), 0),
             "P1_AUDIT_LAUNCHER_CONTINUITY_BINDING_INVALID")
    _validate_custodian_closure(value.get("formal_closure"), spec, campaign_id)
    _require(value["formal_closure"]["lease_generation"] ==
             value["formal_final_generation"],
             "P1_AUDIT_LAUNCHER_GENERATION_DRIFT")
    return value


def _validate_source_attestation(value: Any) -> None:
    attestation = _exact(
        value, VERIFIED_SOURCE_ATTESTATION_FIELDS,
        "P1_AUDIT_CLOSURE_SOURCE_ATTESTATION_INVALID")
    _require(attestation.get("raw_payloads_verified") is True and all(
        _is_digest(attestation.get(field))
        for field in VERIFIED_SOURCE_ATTESTATION_FIELDS - {
            "raw_payloads_verified"}),
        "P1_AUDIT_CLOSURE_SOURCE_ATTESTATION_INVALID")


def validate_verified_closure(artifact: Artifact, spec: Spec) -> dict[str, Any]:
    value = artifact.document
    _exact(value, VERIFIED_CLOSURE_FIELDS,
           "P1_AUDIT_CLOSURE_FIELDS_INVALID")
    _validate_seal(value, "P1_AUDIT_CLOSURE_DIGEST_INVALID")
    _reject_authority(value, "P1_AUDIT_CLOSURE_AUTHORITY_NOT_FALSE")
    campaign_id = value.get("campaign_id")
    formal = _formal_map(spec).get(campaign_id)
    _require(formal is not None and
             value.get("schema") == "hepta.bounded-shadow-campaign-closure.v1" and
             value.get("version") == 1,
             "P1_AUDIT_CLOSURE_SCHEMA_INVALID")
    _require(value.get("campaign_sha256") == formal["campaign_sha256"] and
             value.get("policy_body_sha256") == formal["policy_body_sha256"] and
             value.get("policy_file_sha256") == formal["policy_file_sha256"] and
             value.get("strategy_id") == spec.strategy_id and
             value.get("strategy_version") == spec.strategy_version and
             value.get("strategy_sha256") == spec.strategy_sha256,
             "P1_AUDIT_CLOSURE_FROZEN_LINEAGE_DRIFT")
    for field in (
            "strategy_file_sha256", "observer_state_body_sha256",
            "observer_state_file_sha256", "strategy_state_file_sha256",
            "final_audit_body_sha256", "final_audit_file_sha256"):
        _require(_is_digest(value.get(field)),
                 "P1_AUDIT_CLOSURE_DIGEST_FIELD_INVALID")
    completed = value.get("completed_iterations")
    maximum = value.get("maximum_iterations")
    iteration_count = value.get("iteration_count")
    segments = value.get("segments")
    iterations = value.get("iterations")
    _require(_is_int(completed, 1) and completed == maximum == iteration_count and
             isinstance(iterations, list) and len(iterations) == completed and
             _is_int(value.get("segment_count"), 1) and
             isinstance(segments, list) and
             len(segments) == value["segment_count"] and
             _is_int(value.get("verified_at_ms"), 0) and
             type(value.get("complete_revalidation")) is bool and
             value.get("closure_status") ==
                "VERIFIED_WITH_RETAINED_EVIDENCE_RESIDUALS" and
             _is_sorted_unique_text(value.get("residual_evidence")),
             "P1_AUDIT_CLOSURE_COMPLETION_INVALID")
    for index, segment in enumerate(segments, start=1):
        _exact(segment, VERIFIED_SEGMENT_FIELDS,
               "P1_AUDIT_CLOSURE_SEGMENT_FIELDS_INVALID")
        _require(segment.get("segment_index") == index and
                 _is_int(segment.get("record_count"), 1) and
                 _is_int(segment.get("history_record_bytes"), 1) and
                 _is_int(segment.get("history_index_bytes"), 1) and
                 segment.get("history_storage_bytes") ==
                    segment["history_record_bytes"] +
                    segment["history_index_bytes"] and
                 all(_is_digest(segment.get(field)) for field in (
                     "history_head_sha256", "source_sha256", "audit_sha256")),
                 "P1_AUDIT_CLOSURE_SEGMENT_INVALID")

    digest_fields = VERIFIED_ITERATION_FIELDS - {
        "iteration", "segment_index", "scheduled_at_ms", "evaluated_at_ms",
        "source_first_sequence", "source_last_sequence",
        "source_record_count", "source_total_record_count",
        "source_window_truncated", "source_predecessor_record_sha256",
        "materialization_window_ms", "materialization_maximum_records",
        "source_attestation", "final_outcome", "residual_evidence",
    }
    previous_scheduled: int | None = None
    previous_segment = 1
    for index, iteration in enumerate(iterations, start=1):
        _exact(iteration, VERIFIED_ITERATION_FIELDS,
               "P1_AUDIT_CLOSURE_ITERATION_FIELDS_INVALID")
        scheduled = iteration.get("scheduled_at_ms")
        evaluated = iteration.get("evaluated_at_ms")
        first = iteration.get("source_first_sequence")
        last = iteration.get("source_last_sequence")
        predecessor = iteration.get("source_predecessor_record_sha256")
        current_segment = iteration.get("segment_index")
        _require(iteration.get("iteration") == index and
                 _is_int(current_segment, previous_segment) and
                 current_segment <= value["segment_count"] and
                 _is_int(scheduled, 0) and _is_int(evaluated, scheduled) and
                 (previous_scheduled is None or scheduled > previous_scheduled) and
                 _is_int(first, 1) and _is_int(last, first) and
                 iteration.get("source_record_count") == last - first + 1 and
                 iteration.get("source_total_record_count") == last and
                 iteration.get("source_window_truncated") is (first > 1) and
                 ((first == 1 and predecessor is None) or
                  (first > 1 and _is_digest(predecessor))) and
                 _is_int(iteration.get("materialization_window_ms"), 1) and
                 _is_int(iteration.get("materialization_maximum_records"), 1) and
                 isinstance(iteration.get("final_outcome"), str) and
                 bool(iteration["final_outcome"]) and
                 _is_sorted_unique_text(iteration.get("residual_evidence")) and
                 all(_is_digest(iteration.get(field)) for field in digest_fields),
                 "P1_AUDIT_CLOSURE_ITERATION_INVALID")
        _validate_source_attestation(iteration.get("source_attestation"))
        previous_scheduled = scheduled
        previous_segment = current_segment
    return value


def validate_decision(artifact: Artifact, spec: Spec) -> dict[str, Any]:
    value = artifact.document
    _exact(value, DECISION_FIELDS, "P1_AUDIT_DECISION_FIELDS_INVALID")
    _validate_seal(value, "P1_AUDIT_DECISION_DIGEST_INVALID")
    _reject_authority(value, "P1_AUDIT_DECISION_AUTHORITY_NOT_FALSE")
    _require(value.get("schema") ==
             "hepta.p1-safety-soak-decision-receipt.v1" and
             value.get("version") == 1 and
             value.get("campaign_id") == spec.campaign_id and
             _is_int(value.get("sequence"), 1) and
             _is_identifier(value.get("decision_id")) and
             value.get("formal_campaign_id") in _formal_map(spec) and
             _is_digest(value.get("verified_closure_body_sha256")) and
             _is_int(value.get("closure_iteration"), 1),
             "P1_AUDIT_DECISION_SCHEMA_INVALID")
    _require(value.get("source_manifest_sha256") ==
             spec.source_manifest_sha256 and
             value.get("policy_sha256") == spec.policy_sha256 and
             value.get("strategy_sha256") == spec.strategy_sha256,
             "P1_AUDIT_DECISION_FROZEN_LINEAGE_DRIFT")
    _require(type(value.get("eligible")) is bool and
             type(value.get("complete")) is bool and
             type(value.get("catch_up")) is bool and
             type(value.get("audit_failure")) is bool and
             type(value.get("cleanup_failure")) is bool and
             _is_int(value.get("scheduled_at_ms"), 0) and
             _is_int(value.get("evaluated_at_ms"), value["scheduled_at_ms"]) and
             value.get("clock_id") == "CLOCK_BOOTTIME" and
             isinstance(value.get("boot_id"), str) and
             BOOT_ID.fullmatch(value["boot_id"]) is not None and
             _is_int(value.get("scheduled_boottime_ns"), 0) and
             _is_int(value.get("evaluated_boottime_ns"),
                     value["scheduled_boottime_ns"]) and
             value["evaluated_boottime_ns"] -
                value["scheduled_boottime_ns"] ==
                (value["evaluated_at_ms"] - value["scheduled_at_ms"]) *
                    1_000_000,
             "P1_AUDIT_DECISION_STATE_INVALID")
    _observer_reference(
        value.get("clock_observer_receipt"), SERVICE_OBSERVATION_SCHEMA,
        "P1_AUDIT_DECISION_CLOCK_OBSERVER_REFERENCE_INVALID")
    try:
        parsed_day = date.fromisoformat(value.get("trading_day"))
    except (TypeError, ValueError) as error:
        raise EvidenceError("P1_AUDIT_DECISION_TRADING_DAY_INVALID") from error
    derived_day = datetime.fromtimestamp(
        value["scheduled_at_ms"] / 1000, tz=timezone.utc
    ).astimezone(spec.trading_timezone).date()
    _require(parsed_day == derived_day and
             value["trading_day"] == parsed_day.isoformat(),
             "P1_AUDIT_DECISION_TRADING_DAY_INVALID")
    _require(value.get("outcome") in {"NO_TRADE", "TRADE_CANDIDATE"} and
             _is_digest(value.get("decision_artifact_file_sha256")) and
             _is_digest(value.get("evidence_sha256")),
             "P1_AUDIT_DECISION_EVIDENCE_INVALID")
    return value


def validate_checkpoint(artifact: Artifact, spec: Spec) -> dict[str, Any]:
    value = artifact.document
    _exact(value, CHECKPOINT_FIELDS, "P1_AUDIT_CHECKPOINT_FIELDS_INVALID")
    _validate_seal(value, "P1_AUDIT_CHECKPOINT_DIGEST_INVALID")
    _reject_authority(value, "P1_AUDIT_CHECKPOINT_AUTHORITY_NOT_FALSE")
    _require(value.get("schema") ==
             "hepta.p1-safety-soak-continuity-checkpoint.v1" and
             value.get("version") == 1 and
             value.get("campaign_id") == spec.campaign_id and
             _is_int(value.get("sequence"), 0) and
             value.get("clock_id") == "CLOCK_BOOTTIME" and
             isinstance(value.get("boot_id"), str) and
             BOOT_ID.fullmatch(value["boot_id"]) is not None and
             _is_int(value.get("observed_boottime_ns"), 0) and
             value.get("continuity_slot_index") == value.get("sequence") and
             _is_int(value.get("continuity_origin_ms"), 1) and
             _is_int(value.get("continuity_end_ms"),
                     value["continuity_origin_ms"] + 1) and
             _is_int(value.get("continuity_cadence_ms"), 1) and
             value.get("continuity_final_slot") == (
                 value["continuity_end_ms"] - value["continuity_origin_ms"] +
                 value["continuity_cadence_ms"] - 1) //
                    value["continuity_cadence_ms"] and
             value["continuity_slot_index"] <=
                value["continuity_final_slot"] and
             value.get("continuity_scheduled_at_ms") == min(
                 value["continuity_origin_ms"] +
                    value["continuity_slot_index"] *
                    value["continuity_cadence_ms"],
                 value["continuity_end_ms"]) and
             value.get("continuity_is_final") is (
                 value["continuity_slot_index"] ==
                    value["continuity_final_slot"]) and
             value.get("catch_up") is False and
             _is_int(value.get("lease_generation"), 1) and
             _is_int(value.get("previous_lease_generation"), 0) and
             value["previous_lease_generation"] ==
                value["lease_generation"] - 1 and
             _is_digest(value.get("previous_lease_receipt_body_sha256")) and
             (value.get("transition_fault_id") is None or
              _is_identifier(value.get("transition_fault_id"))) and
             (value["continuity_slot_index"] != 0 or
              value.get("transition_fault_id") is None),
             "P1_AUDIT_CHECKPOINT_SCHEMA_INVALID")
    _require(value.get("source_manifest_sha256") ==
             spec.source_manifest_sha256 and
             value.get("policy_sha256") == spec.policy_sha256 and
             value.get("strategy_sha256") == spec.strategy_sha256,
             "P1_AUDIT_CHECKPOINT_FROZEN_LINEAGE_DRIFT")
    _require(
        _validate_reference(
            value.get("freeze_bundle"),
            "P1_AUDIT_CHECKPOINT_FREEZE_REFERENCE_INVALID") ==
            spec.freeze_bundle,
        "P1_AUDIT_CHECKPOINT_FREEZE_REFERENCE_INVALID")
    _observer_reference(
        value.get("campaign_runtime"), CAMPAIGN_RUNTIME_SCHEMA,
        "P1_AUDIT_CHECKPOINT_RUNTIME_REFERENCE_INVALID")
    activation_reference = _observer_reference(
        value.get("activation_receipt"),
        "hepta.p1-watch-activation-receipt.v4",
        "P1_AUDIT_CHECKPOINT_ACTIVATION_INVALID")
    lease_reference = _observer_reference(
        value.get("lease_receipt"),
        "hepta.shadow-watch-lease-receipt.v1",
        "P1_AUDIT_CHECKPOINT_LEASE_INVALID")
    activation = _exact(
        value.get("activation_receipt_document"), ACTIVATION_RECEIPT_FIELDS,
        "P1_AUDIT_CHECKPOINT_ACTIVATION_INVALID")
    _validate_seal(activation, "P1_AUDIT_CHECKPOINT_ACTIVATION_INVALID")
    _reject_authority(activation, "P1_AUDIT_CHECKPOINT_ACTIVATION_INVALID")
    _require(
        activation.get("schema") == activation_reference["schema"] and
        activation.get("version") == 4 and
        activation.get("status") == "WATCH_GATEWAY_ACTIVATED" and
        activation.get("boot_id") == value["boot_id"] and
        activation.get("gateway_activated") is True and
        activation.get("broker_deny_all_continuity_attested") is True and
        activation.get("kill_switch_engaged") is True and
        activation.get("watch_authority_provisioned") is False and
        digest_bytes(canonical_bytes(activation)) ==
            activation_reference["file_sha256"] and
        activation["body_sha256"] == activation_reference["body_sha256"],
        "P1_AUDIT_CHECKPOINT_ACTIVATION_INVALID")
    _validate_activation_predecessor_lineage(
        activation.get("predecessor_activation_success"),
        activation.get("predecessor_activation_failure"),
        "P1_AUDIT_CHECKPOINT_ACTIVATION_PREDECESSOR_INVALID")
    _validate_activation_install_lineage(
        activation.get("shadow_install_evidence"),
        spec.source_manifest_sha256,
        "P1_AUDIT_CHECKPOINT_ACTIVATION_INSTALL_LINEAGE_INVALID")
    gateway, _gateway_process, _gateway_executable, _gateway_profile, \
        _gateway_domain, tool_socket, supervisor_socket = \
        _validate_activation_gateway_projection(
            activation, value, "P1_AUDIT_CHECKPOINT_IDENTITY_INVALID")
    export_commit, _export_references = _validate_export_projection(
        value, "P1_AUDIT_CHECKPOINT_EXPORT_INVALID")
    lease = _exact(
        value.get("lease_receipt_document"), WATCH_LEASE_FIELDS,
        "P1_AUDIT_CHECKPOINT_LEASE_INVALID")
    _validate_seal(lease, "P1_AUDIT_CHECKPOINT_LEASE_INVALID")
    _reject_authority(lease, "P1_AUDIT_CHECKPOINT_LEASE_INVALID")
    _require(
        lease.get("schema") == lease_reference["schema"] and
        digest_bytes(canonical_bytes(lease)) == lease_reference["file_sha256"] and
        lease["body_sha256"] == lease_reference["body_sha256"] and
        export_commit.get("lease_generation") == value["lease_generation"] and
        export_commit.get("lease_receipt_file_sha256") ==
            lease_reference["file_sha256"] and
        export_commit.get("lease_receipt_body_sha256") ==
            lease_reference["body_sha256"] and
        lease.get("operation") == "ROTATE" and
        lease.get("accepted") is True and lease.get("boundary") == "WATCH" and
        lease.get("lease_generation") == value["lease_generation"] and
        lease.get("previous_lease_generation") ==
            value["previous_lease_generation"] and
        lease.get("previous_receipt_body_sha256") ==
            value["previous_lease_receipt_body_sha256"],
        "P1_AUDIT_CHECKPOINT_LEASE_INVALID")
    custodian = _validate_observation_unit(
        value.get("custodian_identity"), "P1_AUDIT_CHECKPOINT_IDENTITY_INVALID")
    collector = _validate_observation_unit(
        value.get("collector_timer_identity"),
        "P1_AUDIT_CHECKPOINT_IDENTITY_INVALID")
    reconcile = _validate_observation_unit(
        value.get("activation_reconcile_timer_identity"),
        "P1_AUDIT_CHECKPOINT_IDENTITY_INVALID")
    authorized = value.get("authorized_uids")
    _require(
        gateway["unit"] == "hepta-tool-gateway@alpha.service" and
        custodian["unit"] ==
            "hepta-shadow-watch-custodian@alpha.service" and
        collector["unit"] == "hepta-shadow-watch-collector@alpha.timer" and
        reconcile["unit"] ==
            "hepta-p1-watch-activation-reconcile.timer" and
        tool_socket["path"] == str(GATEWAY_TOOL_SOCKET) and
        supervisor_socket["path"] == str(GATEWAY_SUPERVISOR_SOCKET) and
        all(type(value.get(field)) is bool for field in (
            "persistent_stack_ok", "lease_chain_ok", "campaign_socket_present",
            "kill_switch_engaged", "zero_exposure")) and
        value["persistent_stack_ok"] is True and
        value["lease_chain_ok"] is True and value["zero_exposure"] is True and
        _is_int(value.get("connector_count"), 0) and
        _is_int(value.get("paper_unit_active_count"), 0) and
        isinstance(authorized, list) and authorized == sorted(set(authorized)) and
        all(_is_int(uid, 0) for uid in authorized) and
        value["connector_count"] == 0 and not authorized and
        value["paper_unit_active_count"] == 0 and
        value["campaign_socket_present"] is False and
        value["kill_switch_engaged"] is True,
        "P1_AUDIT_CHECKPOINT_UNSAFE")
    _observer_reference(
        value.get("observer_receipt"),
        CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA,
        "P1_AUDIT_CHECKPOINT_OBSERVER_REFERENCE_INVALID")
    return value


def validate_fault_plan(artifact: Artifact, spec: Spec) -> list[dict[str, Any]]:
    value = artifact.document
    _exact(value, FAULT_PLAN_FIELDS, "P1_AUDIT_FAULT_PLAN_FIELDS_INVALID")
    _validate_seal(value, "P1_AUDIT_FAULT_PLAN_DIGEST_INVALID")
    _reject_authority(value, "P1_AUDIT_FAULT_PLAN_AUTHORITY_NOT_FALSE")
    _require(value.get("schema") == "hepta.p1-safety-soak-fault-plan.v1" and
             value.get("version") == 1 and
             value.get("campaign_id") == spec.campaign_id and
             artifact.body_sha256 == spec.fault_plan_body_sha256,
             "P1_AUDIT_FAULT_PLAN_BINDING_INVALID")
    _require(value.get("source_manifest_sha256") ==
             spec.source_manifest_sha256 and
             value.get("policy_sha256") == spec.policy_sha256 and
             value.get("strategy_sha256") == spec.strategy_sha256,
             "P1_AUDIT_FAULT_PLAN_FROZEN_LINEAGE_DRIFT")
    planned = value.get("planned_faults")
    _require(isinstance(planned, list) and bool(planned),
             "P1_AUDIT_FAULT_PLAN_EMPTY")
    identifiers: list[str] = []
    ordering: list[tuple[int, str]] = []
    formal_ids = {
        item["campaign_id"] for item in spec.formal_campaigns
    }
    for item in planned:
        _exact(item, PLANNED_FAULT_FIELDS,
               "P1_AUDIT_PLANNED_FAULT_FIELDS_INVALID")
        fault_type = item.get("fault_type")
        _require(_is_identifier(item.get("fault_id")) and
                 item.get("formal_campaign_id") in formal_ids and
                 isinstance(fault_type, str) and
                 fault_type in ALLOWED_FAULT_TYPES and
                 item.get("target_id") == FAULT_TARGET_IDS[fault_type] and
                 _is_int(item.get("inject_at_boottime_ns"), 0) and
                 _is_int(item.get("maximum_injection_lateness_ns"), 0) and
                 item["maximum_injection_lateness_ns"] <=
                    MAXIMUM_FAULT_INJECTION_LATENESS_NS and
                 _is_int(item.get("maximum_recovery_ns"), 1),
                 "P1_AUDIT_PLANNED_FAULT_INVALID")
        _require(item["maximum_recovery_ns"] <= MAXIMUM_FAULT_RECOVERY_NS,
                 "P1_AUDIT_PLANNED_FAULT_INVALID")
        identifiers.append(item["fault_id"])
        ordering.append((item["inject_at_boottime_ns"], item["fault_id"]))
    _require(len(identifiers) == len(set(identifiers)) and
             ordering == sorted(ordering),
             "P1_AUDIT_FAULT_PLAN_ORDER_INVALID")
    _require(
        {item["fault_type"] for item in planned} == REQUIRED_FAULT_TYPES,
        "P1_AUDIT_FAULT_TYPE_COVERAGE_INCOMPLETE")
    _require(all(
        after["inject_at_boottime_ns"] >
            before["inject_at_boottime_ns"] +
            before["maximum_injection_lateness_ns"] +
            before["maximum_recovery_ns"]
        for before, after in zip(planned, planned[1:])),
        "P1_AUDIT_FAULT_PLAN_OVERLAP")
    return planned


def validate_fault_result(artifact: Artifact, spec: Spec) -> dict[str, Any]:
    value = artifact.document
    _exact(value, FAULT_RESULT_FIELDS, "P1_AUDIT_FAULT_RESULT_FIELDS_INVALID")
    _validate_seal(value, "P1_AUDIT_FAULT_RESULT_DIGEST_INVALID")
    _reject_authority(value, "P1_AUDIT_FAULT_RESULT_AUTHORITY_NOT_FALSE")
    fault_type = value.get("fault_type")
    _require(value.get("schema") == "hepta.p1-safety-soak-fault-result.v1" and
             value.get("version") == 1 and
             value.get("campaign_id") == spec.campaign_id and
             _is_int(value.get("sequence"), 1) and
             _is_identifier(value.get("fault_id")) and
             isinstance(fault_type, str) and
             fault_type in ALLOWED_FAULT_TYPES and
             value.get("target_id") == FAULT_TARGET_IDS[fault_type] and
             _is_int(value.get("injection_boottime_ns"), 0) and
             _is_int(value.get("recovered_boottime_ns"),
                     value["injection_boottime_ns"]) and
             type(value.get("recovery_verified")) is bool and
             type(value.get("cleanup_verified")) is bool and
             _is_digest(value.get("evidence_sha256")) and
             all(type(value.get(field)) is bool for field in (
                 "authority_failure", "audit_failure", "cleanup_failure")),
             "P1_AUDIT_FAULT_RESULT_SCHEMA_INVALID")
    _observer_reference(
        value.get("observer_receipt"), FAULT_OBSERVATION_SCHEMA,
        "P1_AUDIT_FAULT_RESULT_OBSERVER_REFERENCE_INVALID")
    return value


def validate_authority_snapshot(artifact: Artifact,
                                spec: Spec) -> dict[str, Any]:
    value = artifact.document
    _exact(value, AUTHORITY_SNAPSHOT_FIELDS,
           "P1_AUDIT_AUTHORITY_SNAPSHOT_FIELDS_INVALID")
    _validate_seal(value, "P1_AUDIT_AUTHORITY_SNAPSHOT_DIGEST_INVALID")
    _reject_authority(value, "P1_AUDIT_AUTHORITY_SNAPSHOT_PERMISSION")
    _require(value.get("schema") ==
             "hepta.p1-safety-soak-authority-snapshot.v1" and
             value.get("version") == 1 and
             value.get("campaign_id") == spec.campaign_id and
             _is_int(value.get("sequence"), 0) and
             value.get("clock_id") == "CLOCK_BOOTTIME" and
             isinstance(value.get("boot_id"), str) and
             BOOT_ID.fullmatch(value["boot_id"]) is not None and
             _is_int(value.get("observed_boottime_ns"), 0),
             "P1_AUDIT_AUTHORITY_SNAPSHOT_SCHEMA_INVALID")
    _require(value.get("source_manifest_sha256") ==
             spec.source_manifest_sha256 and
             value.get("policy_sha256") == spec.policy_sha256 and
             value.get("strategy_sha256") == spec.strategy_sha256,
             "P1_AUDIT_AUTHORITY_SNAPSHOT_LINEAGE_DRIFT")
    _require(all(_is_int(value.get(field), 0) for field in (
        "connector_count", "paper_unit_active_count")) and
        isinstance(value.get("authorized_uids"), list) and
        value["authorized_uids"] == sorted(set(value["authorized_uids"])) and
        all(_is_int(uid, 0) for uid in value["authorized_uids"]) and
        all(type(value.get(field)) is bool for field in (
            "campaign_socket_present", "kill_switch_engaged",
            "local_boundary_safe", "local_boundary_uncertain",
            "authoritative_account_state_observed")) and
        value.get("observation_scope") == "LOCAL_HOST_BOUNDARY_ONLY" and
        value.get("authoritative_account_state_observed") is False and
        value["local_boundary_safe"] == (
            value["connector_count"] == 0 and
            not value["authorized_uids"] and
            value["paper_unit_active_count"] == 0 and
            value["campaign_socket_present"] is False and
            value["kill_switch_engaged"] is True and
            value["local_boundary_uncertain"] is False),
        "P1_AUDIT_AUTHORITY_SNAPSHOT_STATE_INVALID")
    _observer_reference(
        value.get("observer_receipt"), AUTHORITY_OBSERVATION_SCHEMA,
        "P1_AUDIT_AUTHORITY_OBSERVER_REFERENCE_INVALID")
    return value


def validate_cleanup_snapshot(artifact: Artifact,
                              spec: Spec) -> dict[str, Any]:
    value = artifact.document
    _exact(value, CLEANUP_SNAPSHOT_FIELDS,
           "P1_AUDIT_CLEANUP_SNAPSHOT_FIELDS_INVALID")
    _validate_seal(value, "P1_AUDIT_CLEANUP_SNAPSHOT_DIGEST_INVALID")
    _reject_authority(value, "P1_AUDIT_CLEANUP_SNAPSHOT_PERMISSION")
    _require(value.get("schema") ==
             "hepta.p1-safety-soak-cleanup-snapshot.v1" and
             value.get("version") == 1 and
             value.get("campaign_id") == spec.campaign_id and
             _is_int(value.get("sequence"), 0) and
             value.get("clock_id") == "CLOCK_BOOTTIME" and
             isinstance(value.get("boot_id"), str) and
             BOOT_ID.fullmatch(value["boot_id"]) is not None and
             _is_int(value.get("observed_boottime_ns"), 0) and
             value.get("subject_type") in {"LAUNCHER", "FAULT", "FINAL"} and
             _is_identifier(value.get("subject_id")),
             "P1_AUDIT_CLEANUP_SNAPSHOT_SCHEMA_INVALID")
    _require(all(_is_int(value.get(field), 0) for field in (
        "watch_authority_count", "export_residue_count",
        "session_authority_count", "paper_unit_active_count")) and
        type(value.get("campaign_socket_present")) is bool and
        type(value.get("cleanup_complete")) is bool and
        type(value.get("cleanup_uncertain")) is bool and
        isinstance(value.get("errors"), list) and
        all(isinstance(item, str) and item for item in value["errors"]),
        "P1_AUDIT_CLEANUP_SNAPSHOT_STATE_INVALID")
    _observer_reference(
        value.get("observer_receipt"), CLEANUP_OBSERVATION_SCHEMA,
        "P1_AUDIT_CLEANUP_OBSERVER_REFERENCE_INVALID")
    return value


def _validate_chain(
    values: Sequence[dict[str, Any]], artifacts: Sequence[Artifact],
    *, sequence_field: str, previous_field: str, first_sequence: int,
    reason: str,
) -> None:
    previous: str | None = None
    for offset, (value, artifact) in enumerate(zip(values, artifacts)):
        expected_sequence = first_sequence + offset
        _require(value.get(sequence_field) == expected_sequence and
                 value.get(previous_field) == previous,
                 reason)
        previous = artifact.body_sha256


def _checked_artifacts(groups: Iterable[Sequence[Artifact]]) -> list[dict[str, str]]:
    result = [{
        "role": item.role,
        "path": item.path,
        "file_sha256": item.file_sha256,
        "body_sha256": item.body_sha256,
    } for group in groups for item in group]
    return sorted(result, key=lambda item: (item["role"], item["path"]))


def validate_audit_receipt(value: dict[str, Any]) -> None:
    """Validate the single machine-consumable P1 audit output contract."""

    _exact(value, AUDIT_RECEIPT_FIELDS,
           "P1_AUDIT_OUTPUT_FIELDS_INVALID")
    _validate_seal(value, "P1_AUDIT_OUTPUT_DIGEST_INVALID")
    _reject_authority(value, "P1_AUDIT_OUTPUT_AUTHORITY_NOT_FALSE")
    _require(
        value.get("schema") == "hepta.p1-safety-soak-audit-receipt.v1" and
        value.get("version") == 1 and value.get("phase") == "P1_SHADOW" and
        value.get("verdict") in {"GO", "NO_GO", "HALT"} and
        _is_identifier(value.get("campaign_id")) and
        _is_identifier(value.get("domain_id")) and
        _is_identifier(value.get("independent_auditor_id")) and
        _is_int(value.get("audited_at_ms"), 0) and
        all(_is_digest(value.get(field)) for field in (
            "campaign_spec_file_sha256", "campaign_spec_body_sha256",
            "source_manifest_sha256", "policy_sha256", "strategy_sha256")),
        "P1_AUDIT_OUTPUT_HEADER_INVALID")
    _validate_reference(
        value.get("freeze_bundle"), "P1_AUDIT_OUTPUT_FREEZE_BUNDLE_INVALID")
    is_go = value["verdict"] == "GO"
    campaign_runtime = value.get("campaign_runtime")
    if campaign_runtime is None:
        _require(not is_go,
                 "P1_AUDIT_OUTPUT_CAMPAIGN_RUNTIME_MISSING")
    else:
        _observer_reference(
            campaign_runtime, CAMPAIGN_RUNTIME_SCHEMA,
            "P1_AUDIT_OUTPUT_CAMPAIGN_RUNTIME_INVALID")
    producer = _exact(
        value.get("producer"), PRODUCER_FIELDS,
        "P1_AUDIT_OUTPUT_PRODUCER_INVALID")
    _require(producer.get("path") == str(INSTALLED_EXECUTABLE) and
             _is_digest(producer.get("file_sha256")) and
             value.get("production_mode") in {PRODUCTION_MODE, REHEARSAL_MODE},
             "P1_AUDIT_OUTPUT_PRODUCER_INVALID")
    interval = _exact(
        value.get("evaluated_interval"), EVALUATED_INTERVAL_FIELDS,
        "P1_AUDIT_OUTPUT_INTERVAL_FIELDS_INVALID")
    _require(
        interval.get("clock_id") == "CLOCK_BOOTTIME" and
        (interval.get("boot_id") is None or
         (isinstance(interval["boot_id"], str) and
          BOOT_ID.fullmatch(interval["boot_id"]) is not None)) and
        all(item is None or _is_int(item, 0) for item in (
            interval.get("start_boottime_ns"),
            interval.get("end_boottime_ns"))) and
        _is_int(interval.get("duration_ns"), 0) and
        _is_int(interval.get("maximum_checkpoint_gap_ns"), 0) and
        type(interval.get("consecutive")) is bool and
        all(item is None or _is_int(item, 0) for item in (
            interval.get("continuity_origin_ms"),
            interval.get("continuity_end_ms"),
            interval.get("continuity_final_slot"))) and
        (interval.get("continuity_origin_ms") is None or
         (interval["continuity_origin_ms"] >= 1 and
          interval["continuity_end_ms"] >
              interval["continuity_origin_ms"] and
          interval["continuity_final_slot"] >= 1)) and
        (not is_go or all(interval.get(field) is not None for field in (
            "continuity_origin_ms", "continuity_end_ms",
            "continuity_final_slot"))),
        "P1_AUDIT_OUTPUT_INTERVAL_INVALID")
    counts = _exact(value.get("counts"), COUNTS_FIELDS,
                    "P1_AUDIT_OUTPUT_COUNTS_FIELDS_INVALID")
    _require(all(_is_int(item, 0) for item in counts.values()),
             "P1_AUDIT_OUTPUT_COUNTS_INVALID")
    completeness = _exact(
        value.get("completeness"), COMPLETENESS_FIELDS,
        "P1_AUDIT_OUTPUT_COMPLETENESS_FIELDS_INVALID")
    _require(all(_is_int(completeness.get(field), 0) for field in (
        "numerator", "denominator", "ppm")) and
        completeness["ppm"] <= ONE_HUNDRED_PERCENT_PPM and
        type(completeness.get("strictly_greater_than_99_percent")) is bool,
        "P1_AUDIT_OUTPUT_COMPLETENESS_INVALID")
    checked = value.get("checked_artifacts")
    _require(isinstance(checked, list) and bool(checked),
             "P1_AUDIT_OUTPUT_ARTIFACTS_INVALID")
    previous_key: tuple[str, str] | None = None
    for item in checked:
        _exact(item, CHECKED_ARTIFACT_FIELDS,
               "P1_AUDIT_OUTPUT_ARTIFACT_FIELDS_INVALID")
        key = (item.get("role"), item.get("path"))
        _require(
            isinstance(key[0], str) and bool(key[0]) and
            isinstance(key[1], str) and key[1].startswith("/") and
            _is_digest(item.get("file_sha256")) and
            _is_digest(item.get("body_sha256")) and
            (previous_key is None or key > previous_key),
            "P1_AUDIT_OUTPUT_ARTIFACT_INVALID")
        previous_key = key
    _require(_is_sorted_unique_text(value.get("failed_invariants"),
                                    allow_empty=True),
             "P1_AUDIT_OUTPUT_FINDINGS_INVALID")
    exposure = _exact(
        value.get("exposure_summary"), EXPOSURE_SUMMARY_FIELDS,
        "P1_AUDIT_OUTPUT_EXPOSURE_FIELDS_INVALID")
    _require(type(exposure.get("evidence_present")) is bool and
             type(exposure.get("campaign_socket_ever_present")) is bool and
             type(exposure.get("kill_switch_continuously_engaged")) is bool and
             type(exposure.get("local_boundary_uncertain")) is bool and
             exposure.get("scope") == "LOCAL_HOST_BOUNDARY_ONLY" and
             exposure.get("authoritative_account_state_observed") is False and
             all(_is_int(exposure.get(field), 0) for field in (
                 "maximum_connector_count", "maximum_authorized_uid_count",
                 "maximum_paper_unit_active_count")),
             "P1_AUDIT_OUTPUT_EXPOSURE_INVALID")
    cleanup = _exact(value.get("cleanup_status"), CLEANUP_STATUS_FIELDS,
                     "P1_AUDIT_OUTPUT_CLEANUP_FIELDS_INVALID")
    _require(_is_int(cleanup.get("required_subject_count"), 0) and
             _is_int(cleanup.get("verified_subject_count"), 0) and
             type(cleanup.get("complete")) is bool,
             "P1_AUDIT_OUTPUT_CLEANUP_INVALID")
    _require(value.get("p1_safety_soak_gate_satisfied") is is_go and
             (not is_go or value.get("production_mode") == PRODUCTION_MODE) and
             value.get("paper_test_admission_candidate") is False and
             _is_identifier(value.get("safest_allowed_next_action")),
             "P1_AUDIT_OUTPUT_BOUNDARY_INVALID")


OBSERVER_PROJECTION_FIELDS: dict[str, frozenset[str]] = {
    SERVICE_OBSERVATION_SCHEMA: frozenset({
        "campaign_id", "clock_id", "boot_id", "observed_boottime_ns",
        "service_epoch", "fencing_generation", "lease_generation",
        "transition_fault_id", "source_manifest_sha256", "policy_sha256",
        "strategy_sha256", "continuity_ok", "audit_ok", "cleanup_ok",
    }),
    CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA: frozenset({
        "campaign_id", "clock_id", "boot_id", "observed_boottime_ns",
        "freeze_bundle", "campaign_runtime", "continuity_slot_index",
        "continuity_scheduled_at_ms", "continuity_origin_ms",
        "continuity_end_ms", "continuity_cadence_ms",
        "continuity_final_slot", "continuity_is_final", "catch_up",
        "activation_receipt", "activation_receipt_document",
        "export_commit", "export_commit_document", "export_snapshot",
        "lease_receipt", "lease_receipt_document", "lease_generation",
        "export_receipt",
        "previous_lease_generation", "previous_lease_receipt_body_sha256",
        "gateway_identity", "gateway_process_identity",
        "gateway_executable_identity", "gateway_profile_identity",
        "gateway_domain_config_identity", "supervisor_socket_identity",
        "custodian_identity", "collector_timer_identity",
        "activation_reconcile_timer_identity", "tool_socket_identity",
        "transition_fault_id", "persistent_stack_ok", "lease_chain_ok",
        "connector_count", "authorized_uids", "paper_unit_active_count",
        "campaign_socket_present", "kill_switch_engaged", "zero_exposure",
        "source_manifest_sha256", "policy_sha256", "strategy_sha256",
    }),
    FAULT_OBSERVATION_SCHEMA: frozenset({
        "campaign_id", "fault_id", "fault_type", "target_id",
        "injection_boottime_ns",
        "recovered_boottime_ns", "recovery_verified", "cleanup_verified",
        "authority_failure", "audit_failure", "cleanup_failure",
    }),
    AUTHORITY_OBSERVATION_SCHEMA: frozenset({
        "campaign_id", "clock_id", "boot_id", "observed_boottime_ns",
        "source_manifest_sha256", "policy_sha256", "strategy_sha256",
        "connector_count", "authorized_uids", "paper_unit_active_count",
        "campaign_socket_present", "kill_switch_engaged",
        "local_boundary_safe", "local_boundary_uncertain",
        "observation_scope", "authoritative_account_state_observed",
    }),
    CLEANUP_OBSERVATION_SCHEMA: frozenset({
        "campaign_id", "clock_id", "boot_id", "observed_boottime_ns",
        "subject_type", "subject_id", "watch_authority_count",
        "export_residue_count", "session_authority_count",
        "paper_unit_active_count", "campaign_socket_present",
        "cleanup_complete", "cleanup_uncertain", "errors",
    }),
}


def _observer_artifact_key(
    artifact: Artifact, value: dict[str, Any],
) -> tuple[str, str, str, str]:
    return (
        artifact.path, artifact.file_sha256, artifact.body_sha256,
        value["schema"],
    )


def _observer_reference_key(
    reference: dict[str, Any],
) -> tuple[str, str, str, str]:
    return (
        reference["path"], reference["file_sha256"],
        reference["body_sha256"], reference["schema"],
    )


def _observer_projection_matches(
    projected: dict[str, Any], raw: dict[str, Any],
) -> bool:
    fields = OBSERVER_PROJECTION_FIELDS.get(raw["schema"])
    if fields is None or any(projected.get(field) != raw.get(field)
                             for field in fields):
        return False
    if raw["schema"] == FAULT_OBSERVATION_SCHEMA:
        reference = projected["observer_receipt"]
        evidence_reference = {
            "role": "fault_observation",
            "path": reference["path"],
            "file_sha256": reference["file_sha256"],
            "body_sha256": reference["body_sha256"],
            "schema": reference["schema"],
            "sealed": True,
        }
        return projected.get("evidence_sha256") == digest_bytes(
            canonical_bytes(evidence_reference))
    return True


def _bind_observer_receipts(
    *, projected: Sequence[dict[str, Any]],
    observers: Sequence[tuple[Artifact, dict[str, Any]]],
    additional_reference_keys: Sequence[
        tuple[str, str, str, str]
    ] = (),
    findings: Findings,
) -> None:
    """Require exact one-to-one raw observer provenance for every projection."""

    raw_keys = [_observer_artifact_key(artifact, value)
                for artifact, value in observers]
    raw_paths = [key[0] for key in raw_keys]
    raw_bodies = [key[2] for key in raw_keys]
    if (len(raw_keys) != len(set(raw_keys)) or
            len(raw_paths) != len(set(raw_paths)) or
            len(raw_bodies) != len(set(raw_bodies))):
        findings.halt.add("P1_AUDIT_OBSERVER_RECEIPT_REPLAY")

    projected_pairs: list[tuple[dict[str, Any], tuple[str, str, str, str]]] = []
    for value in projected:
        reference = value.get("observer_receipt")
        if not isinstance(reference, dict) or set(reference) != \
                OBSERVER_REFERENCE_FIELDS:
            continue
        projected_pairs.append((value, _observer_reference_key(reference)))
    projected_keys = [key for _, key in projected_pairs]
    projected_bodies = [key[2] for key in projected_keys]
    if (len(projected_keys) != len(set(projected_keys)) or
            len(projected_bodies) != len(set(projected_bodies))):
        findings.halt.add("P1_AUDIT_OBSERVER_RECEIPT_REPLAY")

    additional_keys = list(additional_reference_keys)
    if (len(additional_keys) != len(set(additional_keys)) or
            set(additional_keys) & set(projected_keys)):
        findings.halt.add("P1_AUDIT_OBSERVER_RECEIPT_REPLAY")

    raw_by_key = {key: value for key, (_, value) in zip(raw_keys, observers)}
    findings.require(
        set(projected_keys) | set(additional_keys) == set(raw_keys),
        "P1_AUDIT_OBSERVER_RECEIPT_BINDING_INCOMPLETE", halt=True)
    for value, key in projected_pairs:
        raw = raw_by_key.get(key)
        if raw is not None:
            findings.require(
                _observer_projection_matches(value, raw),
                "P1_AUDIT_OBSERVER_PROJECTION_DRIFT", halt=True)


def _bind_fault_injection_receipts(
    *, spec: Spec, planned_by_id: dict[str, dict[str, Any]],
    observers: Sequence[tuple[Artifact, dict[str, Any]]],
    injection_receipts: Sequence[Artifact], findings: Findings,
    expected_producer_sha256: str | None = None,
) -> None:
    raw_faults = [value for _artifact, value in observers
                  if value.get("schema") == FAULT_OBSERVATION_SCHEMA]
    references = [
        value["observation_evidence"]["fault_injection_receipt"]
        for value in raw_faults
    ]
    reference_keys = [_observer_reference_key(item) for item in references]
    actual_keys = [(
        artifact.path, artifact.file_sha256, artifact.body_sha256,
        artifact.document.get("schema"),
    ) for artifact in injection_receipts]
    if (len(reference_keys) != len(set(reference_keys)) or
            len(actual_keys) != len(set(actual_keys)) or
            len([key[0] for key in actual_keys]) !=
                len(set(key[0] for key in actual_keys)) or
            len([key[2] for key in actual_keys]) !=
                len(set(key[2] for key in actual_keys))):
        findings.halt.add("P1_AUDIT_FAULT_INJECTION_RECEIPT_REPLAY")
    findings.require(
        set(reference_keys) == set(actual_keys),
        "P1_AUDIT_FAULT_INJECTION_RECEIPT_BINDING_INCOMPLETE", halt=True)
    raw_by_key = {
        _observer_reference_key(
            value["observation_evidence"]["fault_injection_receipt"]): value
        for value in raw_faults
    }
    validated_values: list[dict[str, Any]] = []
    for artifact, key in zip(injection_receipts, actual_keys):
        raw = raw_by_key.get(key)
        if raw is None:
            continue
        planned = planned_by_id.get(raw["fault_id"])
        if planned is None:
            findings.halt.add(
                "P1_AUDIT_FAULT_INJECTION_PLAN_BINDING_INCOMPLETE")
            continue
        try:
            value = validate_fault_injection_artifact(
                artifact, spec, planned, raw, expected_producer_sha256)
            validated_values.append(value)
            if (not value["injection_performed"] or
                    not value["recovery_complete"] or
                    not value["cleanup_complete"] or
                    value["authority_failure"] or value["audit_failure"] or
                    value["cleanup_failure"] or
                    value["post_identity"]["residue_count"] != 0 or
                    not _observation_broker_is_safe(
                        value["pre_identity"]["broker_deny_all"]) or
                    not _observation_broker_is_safe(
                        value["post_identity"]["broker_deny_all"])):
                findings.halt.add("P1_AUDIT_FAULT_INJECTION_UNSAFE")
        except EvidenceError as error:
            findings.add(error)
    if validated_values:
        pins = [canonical_bytes(item["pins_reference"])
                for item in validated_values]
        journal_sequences = [item["journal_predecessor_sequence"]
                             for item in validated_values]
        journal_digests = [item["journal_predecessor_body_sha256"]
                           for item in validated_values]
        findings.require(
            len(set(pins)) == 1 and
            journal_sequences == sorted(set(journal_sequences)) and
            len(journal_digests) == len(set(journal_digests)),
            "P1_AUDIT_FAULT_INJECTION_JOURNAL_LINEAGE_INVALID", halt=True)


def audit_evidence(
    *, campaign_spec: Artifact, launcher_receipts: Sequence[Artifact],
    verified_closures: Sequence[Artifact], decision_receipts: Sequence[Artifact],
    continuity_checkpoints: Sequence[Artifact], fault_plan: Artifact,
    fault_results: Sequence[Artifact], authority_snapshots: Sequence[Artifact],
    cleanup_snapshots: Sequence[Artifact],
    observer_receipts: Sequence[Artifact] = (),
    fault_injection_receipts: Sequence[Artifact] = (),
    freeze_bundle: Artifact | None = None,
    campaign_runtime: Artifact | None = None,
    trading_calendar: Artifact | None = None,
    producer: Mapping[str, str] | None = None,
    audited_at_ms: int | None = None,
) -> dict[str, Any]:
    """Evaluate already-loaded immutable evidence into a non-authorizing receipt."""

    spec = validate_spec(campaign_spec)
    findings = Findings(no_go=set(), halt=set())
    producer_reference = ({
        "path": str(INSTALLED_EXECUTABLE),
        "file_sha256": digest_bytes(b"UNBOUND_P1_AUDITOR"),
    } if producer is None else dict(producer))
    production_mode = PRODUCTION_MODE if producer is not None else REHEARSAL_MODE
    if (set(producer_reference) != PRODUCER_FIELDS or
            producer_reference.get("path") != str(INSTALLED_EXECUTABLE) or
            not _is_digest(producer_reference.get("file_sha256"))):
        raise EvidenceError("P1_AUDIT_PRODUCER_INVALID")
    source_pins: dict[str, dict[str, str]] = {}
    freeze_boot_id: str | None = None
    frozen_wall_ms: int | None = None
    frozen_boottime_ns: int | None = None
    runtime_reference: dict[str, str] | None = None
    if freeze_bundle is None or trading_calendar is None:
        findings.no_go.add("P1_AUDIT_FREEZE_LINEAGE_MISSING")
    else:
        try:
            source_pins = validate_freeze_lineage(
                freeze_bundle, trading_calendar, spec, producer_reference)
            freeze_boot_id = freeze_bundle.document["boot_id"]
            frozen_wall_ms = freeze_bundle.document["issued_at_ms"]
            frozen_boottime_ns = freeze_bundle.document[
                "frozen_boottime_ns"]
        except EvidenceError as error:
            findings.add(error)
    if campaign_runtime is None:
        findings.no_go.add("P1_AUDIT_CAMPAIGN_RUNTIME_MISSING")
    elif freeze_bundle is not None and source_pins:
        try:
            validate_campaign_runtime(
                campaign_runtime, spec, freeze_bundle, fault_plan,
                expected_observer_sha256=(
                    source_pins.get("independent_observer") or {}
                ).get("file_sha256"))
            runtime_reference = {
                "path": campaign_runtime.path,
                "file_sha256": campaign_runtime.file_sha256,
                "body_sha256": campaign_runtime.body_sha256,
                "schema": CAMPAIGN_RUNTIME_SCHEMA,
            }
        except EvidenceError as error:
            findings.add(error)
    checked_observers: list[Artifact] = []
    checked_observer_paths: set[tuple[str, str]] = set()
    for artifact in observer_receipts:
        checked_key = (artifact.role, artifact.path)
        if checked_key not in checked_observer_paths:
            checked_observer_paths.add(checked_key)
            checked_observers.append(artifact)
    checked_injections: list[Artifact] = []
    checked_injection_paths: set[tuple[str, str]] = set()
    for artifact in fault_injection_receipts:
        checked_key = (artifact.role, artifact.path)
        if checked_key not in checked_injection_paths:
            checked_injection_paths.add(checked_key)
            checked_injections.append(artifact)
    all_artifacts: tuple[Sequence[Artifact], ...] = (
        (campaign_spec,), launcher_receipts, verified_closures,
        decision_receipts, continuity_checkpoints, (fault_plan,),
        fault_results, authority_snapshots, cleanup_snapshots,
        checked_observers, checked_injections,
        (() if freeze_bundle is None else (freeze_bundle,)),
        (() if campaign_runtime is None else (campaign_runtime,)),
        (() if trading_calendar is None else (trading_calendar,)),
    )
    for group in all_artifacts:
        for artifact in group:
            try:
                _reject_authority(
                    artifact.document,
                    f"P1_AUDIT_{artifact.role.upper()}_AUTHORITY_NOT_FALSE")
            except EvidenceError as error:
                findings.add(error)
            if freeze_boot_id is not None:
                findings.require(
                    all(value == freeze_boot_id for value in
                        _named_values(artifact.document, "boot_id")),
                    "P1_AUDIT_FREEZE_BOOT_SPLICE", halt=True)

    validated_observers: list[tuple[Artifact, dict[str, Any]]] = []
    for artifact in observer_receipts:
        try:
            value = validate_observer_artifact(
                artifact, spec,
                (source_pins.get("independent_observer") or {}).get(
                    "file_sha256"))
            validated_observers.append((artifact, value))
            if not _observation_broker_is_safe(
                    value["observation_evidence"]["broker_deny_all"]):
                findings.halt.add(
                    "P1_AUDIT_RAW_OBSERVER_BROKER_BOUNDARY_UNSAFE")
        except EvidenceError as error:
            findings.add(error)

    validated_launchers: list[tuple[Artifact, dict[str, Any]]] = []
    for artifact in launcher_receipts:
        try:
            launcher = validate_launcher(
                artifact, spec,
                (source_pins.get("shadow_admission_launcher") or {}).get(
                    "file_sha256"))
            validated_launchers.append((artifact, launcher))
            if (launcher["cleanup_errors"] or
                    launcher.get("authority_residue") is not False or
                    launcher.get("export_residue") is not False):
                findings.halt.add("P1_AUDIT_LAUNCHER_UNCLEAN")
            if (launcher.get("status") != "FORMAL_COMPLETE" or
                    launcher.get("reason") is not None):
                findings.no_go.add(
                    "P1_AUDIT_LAUNCHER_NOT_FORMAL_COMPLETE")
        except EvidenceError as error:
            findings.add(error)
    validated_closures: list[tuple[Artifact, dict[str, Any]]] = []
    for artifact in verified_closures:
        try:
            validated_closures.append(
                (artifact, validate_verified_closure(artifact, spec)))
        except EvidenceError as error:
            findings.add(error)

    formal_ids = [item["campaign_id"] for item in spec.formal_campaigns]
    launcher_by_id = {value["formal_campaign_id"]: (artifact, value)
                      for artifact, value in validated_launchers}
    closure_by_id = {value["campaign_id"]: (artifact, value)
                     for artifact, value in validated_closures}
    findings.require(len(launcher_by_id) == len(validated_launchers),
                     "P1_AUDIT_DUPLICATE_LAUNCHER_CAMPAIGN", halt=True)
    findings.require(len(closure_by_id) == len(validated_closures),
                     "P1_AUDIT_DUPLICATE_CLOSURE_CAMPAIGN", halt=True)
    findings.require(set(launcher_by_id) == set(formal_ids),
                     "P1_AUDIT_LAUNCHER_SET_INCOMPLETE")
    findings.require(set(closure_by_id) == set(formal_ids),
                     "P1_AUDIT_CLOSURE_SET_INCOMPLETE")
    for campaign_id in set(launcher_by_id) & set(closure_by_id):
        launcher_artifact, launcher = launcher_by_id[campaign_id]
        closure_artifact, closure = closure_by_id[campaign_id]
        if launcher.get("status") != "FORMAL_COMPLETE":
            continue
        findings.require(
            launcher.get("formal_verified_closure_file_sha256") ==
                closure_artifact.file_sha256 and
            launcher.get("formal_verified_closure_body_sha256") ==
                closure_artifact.body_sha256 and
            launcher.get("formal_expected_iterations") ==
                closure.get("maximum_iterations") and
            launcher.get("formal_completed_iterations") ==
                closure.get("completed_iterations") and
            launcher.get("formal_valid_after_ms") ==
                closure["iterations"][0]["scheduled_at_ms"],
            "P1_AUDIT_LAUNCHER_CLOSURE_BINDING_DRIFT", halt=True)
        del launcher_artifact

    closure_iterations: dict[tuple[str, int], tuple[Artifact, dict[str, Any]]] = {}
    for campaign_id, (closure_artifact, closure) in closure_by_id.items():
        for iteration in closure["iterations"]:
            key = (campaign_id, iteration["iteration"])
            if key in closure_iterations:
                findings.halt.add("P1_AUDIT_DUPLICATE_CLOSURE_ITERATION")
            closure_iterations[key] = (closure_artifact, iteration)
    findings.require(
        len(closure_iterations) == spec.scheduled_decision_count,
        "P1_AUDIT_CLOSURE_DECISION_SET_INCOMPLETE")

    decisions: list[tuple[Artifact, dict[str, Any]]] = []
    for artifact in decision_receipts:
        try:
            decision = validate_decision(artifact, spec)
            decisions.append((artifact, decision))
            if decision["eligible"] is not (
                    decision["scheduled_at_ms"] in
                    spec.eligible_scheduled_at_ms):
                findings.halt.add(
                    "P1_AUDIT_DECISION_ELIGIBILITY_BINDING_INVALID")
            if decision["audit_failure"] or decision["cleanup_failure"]:
                findings.halt.add("P1_AUDIT_DECISION_FAILURE_RECORDED")
        except EvidenceError as error:
            findings.add(error)
    decisions.sort(key=lambda pair: pair[1].get("sequence", -1))
    decision_artifacts = [pair[0] for pair in decisions]
    decision_values = [pair[1] for pair in decisions]
    try:
        _validate_chain(
            decision_values, decision_artifacts, sequence_field="sequence",
            previous_field="previous_receipt_body_sha256", first_sequence=1,
            reason="P1_AUDIT_DECISION_HASH_CHAIN_GAP")
    except EvidenceError as error:
        findings.add(error)
    decision_keys = [
        (value["formal_campaign_id"], value["closure_iteration"])
        for value in decision_values
    ]
    findings.require(len(decision_keys) == len(set(decision_keys)),
                     "P1_AUDIT_DUPLICATE_DECISION_CLOSURE_BINDING",
                     halt=True)
    findings.require(set(decision_keys) == set(closure_iterations),
                     "P1_AUDIT_DECISION_CLOSURE_SET_INCOMPLETE")
    decision_ids = [value["decision_id"] for value in decision_values]
    findings.require(len(decision_ids) == len(set(decision_ids)),
                     "P1_AUDIT_DUPLICATE_DECISION_ID", halt=True)
    for previous, current in zip(decision_values, decision_values[1:]):
        findings.require(
            current["scheduled_at_ms"] > previous["scheduled_at_ms"],
            "P1_AUDIT_DECISION_SCHEDULE_REGRESSION", halt=True)
    for value in decision_values:
        key = (value["formal_campaign_id"], value["closure_iteration"])
        bound = closure_iterations.get(key)
        if bound is None:
            continue
        closure_artifact, iteration = bound
        closure_outcome = (
            "NO_TRADE" if iteration["final_outcome"] == "NO_TRADE" else
            "TRADE_CANDIDATE"
            if iteration["final_outcome"] in {
                "SHADOW_TRADE", "TRADE_CANDIDATE"} else None)
        findings.require(
            value["verified_closure_body_sha256"] ==
                closure_artifact.body_sha256 and
            value["decision_artifact_file_sha256"] ==
                iteration["decision_receipt_file_sha256"] and
            value["scheduled_at_ms"] == iteration["scheduled_at_ms"] and
            value["evaluated_at_ms"] == iteration["evaluated_at_ms"] and
            value["outcome"] == closure_outcome,
            "P1_AUDIT_DECISION_CLOSURE_BINDING_DRIFT", halt=True)
    raw_service_by_key = {
        _observer_artifact_key(artifact, value): value
        for artifact, value in validated_observers
        if value.get("schema") == SERVICE_OBSERVATION_SCHEMA
    }
    anchor_keys_by_formal: dict[str, set[tuple[str, str, str, str]]] = {}
    for value in decision_values:
        reference = value.get("clock_observer_receipt")
        if not isinstance(reference, dict) or set(reference) != \
                OBSERVER_REFERENCE_FIELDS:
            findings.halt.add(
                "P1_AUDIT_DECISION_CLOCK_OBSERVER_REFERENCE_INVALID")
            continue
        key = _observer_reference_key(reference)
        raw = raw_service_by_key.get(key)
        findings.require(
            raw is not None,
            "P1_AUDIT_DECISION_CLOCK_OBSERVER_BINDING_INVALID", halt=True)
        if raw is None or freeze_boot_id is None or \
                frozen_wall_ms is None or frozen_boottime_ns is None:
            continue
        expected_scheduled = frozen_boottime_ns + \
            (value["scheduled_at_ms"] - frozen_wall_ms) * 1_000_000
        expected_evaluated = frozen_boottime_ns + \
            (value["evaluated_at_ms"] - frozen_wall_ms) * 1_000_000
        findings.require(
            value["boot_id"] == raw["boot_id"] == freeze_boot_id and
            value["scheduled_boottime_ns"] == expected_scheduled and
            value["evaluated_boottime_ns"] == expected_evaluated and
            frozen_boottime_ns <= expected_scheduled <= expected_evaluated <=
                raw["observed_boottime_ns"] and
            value["evaluated_at_ms"] <= raw["observed_at_ms"],
            "P1_AUDIT_DECISION_CLOCK_BINDING_INVALID", halt=True)
        bound = closure_iterations.get(
            (value["formal_campaign_id"], value["closure_iteration"]))
        if bound is None:
            continue
        expected_evidence = digest_bytes(canonical_bytes({
            "verified_closure_body_sha256":
                value["verified_closure_body_sha256"],
            "closure_iteration": value["closure_iteration"],
            "decision_artifact_file_sha256":
                value["decision_artifact_file_sha256"],
            "scheduled_at_ms": value["scheduled_at_ms"],
            "evaluated_at_ms": value["evaluated_at_ms"],
            "clock_id": "CLOCK_BOOTTIME", "boot_id": value["boot_id"],
            "scheduled_boottime_ns": value["scheduled_boottime_ns"],
            "evaluated_boottime_ns": value["evaluated_boottime_ns"],
            "clock_observer_receipt": reference,
            "final_outcome": bound[1]["final_outcome"],
        }))
        findings.require(
            value["evidence_sha256"] == expected_evidence,
            "P1_AUDIT_DECISION_CLOCK_EVIDENCE_DRIFT", halt=True)
        anchor_keys_by_formal.setdefault(
            value["formal_campaign_id"], set()).add(key)
    findings.require(
        set(anchor_keys_by_formal) == set(formal_ids) and
        all(len(keys) == 1 for keys in anchor_keys_by_formal.values()) and
        len({next(iter(keys)) for keys in anchor_keys_by_formal.values()
             if keys}) == len(anchor_keys_by_formal),
        "P1_AUDIT_DECISION_CLOCK_ANCHOR_SET_INVALID", halt=True)
    for formal_id, keys in anchor_keys_by_formal.items():
        if len(keys) != 1:
            continue
        raw = raw_service_by_key.get(next(iter(keys)))
        formal_decisions = [
            value for value in decision_values
            if value["formal_campaign_id"] == formal_id]
        if raw is not None and formal_decisions:
            final_evaluated = max(
                value["evaluated_at_ms"] for value in formal_decisions)
            findings.require(
                0 <= raw["observed_at_ms"] - final_evaluated <=
                    spec.maximum_checkpoint_gap_ns // 1_000_000,
                "P1_AUDIT_DECISION_CLOCK_ANCHOR_LATENESS", halt=True)
    findings.require(len(decision_values) == spec.scheduled_decision_count,
                     "P1_AUDIT_DECISION_RECEIPT_SET_INCOMPLETE")
    eligible = [value for value in decision_values if value["eligible"]]
    complete = [value for value in eligible if value["complete"]]
    catch_up = [value for value in decision_values if value["catch_up"] or
                value["evaluated_at_ms"] - value["scheduled_at_ms"] >
                    spec.maximum_decision_lateness_ms]
    eligible_days = sorted(set(value["trading_day"] for value in eligible))
    findings.require(len(eligible) >= spec.minimum_eligible_decisions,
                     "P1_AUDIT_ELIGIBLE_DECISIONS_BELOW_MINIMUM")
    findings.require(eligible_days == list(spec.declared_trading_days),
                     "P1_AUDIT_TRADING_DAY_COVERAGE_INVALID")
    findings.require(not catch_up, "P1_AUDIT_CATCH_UP_DECISION_PRESENT")
    complete_ppm = ((len(complete) * ONE_HUNDRED_PERCENT_PPM) // len(eligible)
                    if eligible else 0)
    greater_than_99 = bool(eligible) and len(complete) * 100 > len(eligible) * 99
    findings.require(greater_than_99 and
                     complete_ppm >= spec.minimum_complete_ppm,
                     "P1_AUDIT_DECISION_COMPLETENESS_BELOW_THRESHOLD")

    try:
        planned_faults = validate_fault_plan(fault_plan, spec)
    except EvidenceError as error:
        findings.add(error)
        planned_faults = []
    if freeze_bundle is not None and source_pins:
        findings.require(
            freeze_bundle.document.get("planned_faults") == planned_faults,
            "P1_AUDIT_FREEZE_FAULT_PLAN_DRIFT", halt=True)
    results: list[tuple[Artifact, dict[str, Any]]] = []
    for artifact in fault_results:
        try:
            result = validate_fault_result(artifact, spec)
            results.append((artifact, result))
            if (result["authority_failure"] or result["audit_failure"] or
                    result["cleanup_failure"] or
                    not result["cleanup_verified"]):
                findings.halt.add("P1_AUDIT_FAULT_RESULT_UNSAFE")
        except EvidenceError as error:
            findings.add(error)
    results.sort(key=lambda pair: pair[1].get("sequence", -1))
    result_artifacts = [pair[0] for pair in results]
    result_values = [pair[1] for pair in results]
    try:
        _validate_chain(
            result_values, result_artifacts, sequence_field="sequence",
            previous_field="previous_result_body_sha256", first_sequence=1,
            reason="P1_AUDIT_FAULT_RESULT_HASH_CHAIN_GAP")
    except EvidenceError as error:
        findings.add(error)
    planned_by_id = {item["fault_id"]: item for item in planned_faults}
    result_by_id = {item["fault_id"]: item for item in result_values}
    findings.require(len(result_by_id) == len(result_values),
                     "P1_AUDIT_DUPLICATE_FAULT_RESULT", halt=True)
    findings.require(set(result_by_id) == set(planned_by_id),
                     "P1_AUDIT_FAULT_RESULT_SET_INCOMPLETE")
    for fault_id in set(result_by_id) & set(planned_by_id):
        planned = planned_by_id[fault_id]
        result = result_by_id[fault_id]
        findings.require(
            result["fault_type"] == planned["fault_type"] and
            result["target_id"] == planned["target_id"] and
            result["injection_boottime_ns"] ==
                planned["inject_at_boottime_ns"],
            "P1_AUDIT_FAULT_RESULT_PLAN_DRIFT", halt=True)
        findings.require(
            result["recovery_verified"] and
            result["recovered_boottime_ns"] -
                result["injection_boottime_ns"] <=
                planned["maximum_recovery_ns"],
            "P1_AUDIT_FAULT_RECOVERY_INCOMPLETE")

    checkpoints: list[tuple[Artifact, dict[str, Any]]] = []
    for artifact in continuity_checkpoints:
        try:
            checkpoint = validate_checkpoint(artifact, spec)
            checkpoints.append((artifact, checkpoint))
            if not (checkpoint["persistent_stack_ok"] and
                    checkpoint["lease_chain_ok"] and
                    checkpoint["zero_exposure"]):
                findings.halt.add("P1_AUDIT_CHECKPOINT_FAILURE_RECORDED")
        except EvidenceError as error:
            findings.add(error)
    checkpoints.sort(key=lambda pair: pair[1].get("sequence", -1))
    checkpoint_artifacts = [pair[0] for pair in checkpoints]
    checkpoint_values = [pair[1] for pair in checkpoints]
    try:
        _validate_chain(
            checkpoint_values, checkpoint_artifacts, sequence_field="sequence",
            previous_field="previous_checkpoint_body_sha256", first_sequence=0,
            reason="P1_AUDIT_CHECKPOINT_HASH_CHAIN_GAP")
    except EvidenceError as error:
        findings.add(error)
    findings.require(len(checkpoint_values) >= 2,
                     "P1_AUDIT_CONTINUITY_EVIDENCE_INSUFFICIENT")
    grid_origin: int | None = None
    grid_end: int | None = None
    grid_final: int | None = None
    grid_runtime: dict[str, Any] | None = None
    if checkpoint_values:
        grid_origin = checkpoint_values[0]["continuity_origin_ms"]
        grid_end = checkpoint_values[0]["continuity_end_ms"]
        grid_cadence = checkpoint_values[0]["continuity_cadence_ms"]
        grid_final = checkpoint_values[0]["continuity_final_slot"]
        grid_runtime = checkpoint_values[0]["campaign_runtime"]
        findings.require(
            len(checkpoint_values) == grid_final + 1,
            "P1_AUDIT_CONTINUITY_GRID_INCOMPLETE", halt=True)
        if runtime_reference is not None:
            findings.require(
                grid_runtime == runtime_reference,
                "P1_AUDIT_CONTINUITY_RUNTIME_BINDING_INVALID", halt=True)
        for index, checkpoint in enumerate(checkpoint_values):
            findings.require(
                checkpoint["sequence"] == index and
                checkpoint["continuity_slot_index"] == index and
                checkpoint["continuity_origin_ms"] == grid_origin and
                checkpoint["continuity_end_ms"] == grid_end and
                checkpoint["continuity_cadence_ms"] == grid_cadence and
                checkpoint["continuity_final_slot"] == grid_final and
                checkpoint["campaign_runtime"] == grid_runtime and
                checkpoint["continuity_scheduled_at_ms"] == min(
                    grid_origin + index * grid_cadence, grid_end) and
                checkpoint["continuity_is_final"] is (index == grid_final) and
                checkpoint["catch_up"] is False,
                "P1_AUDIT_CONTINUITY_GRID_GAP_DUPLICATE_OR_DRIFT",
                halt=True)
        findings.require(
            checkpoint_values[0]["continuity_scheduled_at_ms"] == grid_origin and
            checkpoint_values[-1]["continuity_scheduled_at_ms"] == grid_end and
            checkpoint_values[-1]["continuity_is_final"] is True,
            "P1_AUDIT_CONTINUITY_GRID_ANCHOR_MISSING", halt=True)
        if freeze_bundle is not None:
            frozen_formals = freeze_bundle.document.get("formal_policies")
            findings.require(
                isinstance(frozen_formals, list) and bool(frozen_formals) and
                grid_origin == frozen_formals[0].get(
                    "launcher_dispatch_at_ms") and
                grid_end == frozen_formals[-1].get("teardown_deadline_ms"),
                "P1_AUDIT_CONTINUITY_GRID_FREEZE_ANCHOR_DRIFT", halt=True)
        activation_references = {
            (
                checkpoint["activation_receipt"]["path"],
                checkpoint["activation_receipt"]["file_sha256"],
                checkpoint["activation_receipt"]["body_sha256"],
            )
            for checkpoint in checkpoint_values
        }
        findings.require(
            len(activation_references) == 1,
            "P1_AUDIT_CONTINUITY_ACTIVATION_REFERENCE_DRIFT", halt=True)
        if len(activation_references) == 1:
            activation_path, activation_file_sha, activation_body_sha = next(
                iter(activation_references))
            for _launcher_artifact, launcher in validated_launchers:
                if launcher.get("status") != "FORMAL_COMPLETE":
                    continue
                findings.require(
                    launcher.get("activation_receipt_path") ==
                        activation_path and
                    launcher.get("activation_receipt_file_sha256") ==
                        activation_file_sha and
                    launcher.get("activation_receipt_body_sha256") ==
                        activation_body_sha,
                    "P1_AUDIT_LAUNCHER_ACTIVATION_BINDING_DRIFT", halt=True)
    boot_id: str | None = None
    start_ns: int | None = None
    end_ns: int | None = None
    maximum_observed_gap_ns = 0
    if checkpoint_values:
        boot_id = checkpoint_values[0]["boot_id"]
        start_ns = checkpoint_values[0]["observed_boottime_ns"]
        end_ns = checkpoint_values[-1]["observed_boottime_ns"]
        previous = checkpoint_values[0]
        for current in checkpoint_values[1:]:
            gap = current["observed_boottime_ns"] - previous["observed_boottime_ns"]
            maximum_observed_gap_ns = max(maximum_observed_gap_ns, gap)
            findings.require(gap > 0 and gap <= spec.maximum_checkpoint_gap_ns,
                             "P1_AUDIT_CONTINUITY_CHECKPOINT_GAP")
            findings.require(current["boot_id"] == boot_id,
                             "P1_AUDIT_BOOTTIME_BOOT_CHANGED")
            findings.require(
                current["freeze_bundle"] == previous["freeze_bundle"] and
                current["campaign_runtime"] == previous["campaign_runtime"] and
                current["activation_receipt"] ==
                    previous["activation_receipt"] and
                current["activation_receipt_document"] ==
                    previous["activation_receipt_document"] and
                current["custodian_identity"] ==
                    previous["custodian_identity"] and
                current["collector_timer_identity"] ==
                    previous["collector_timer_identity"] and
                current["activation_reconcile_timer_identity"] ==
                    previous["activation_reconcile_timer_identity"] and
                current["gateway_executable_identity"] ==
                    previous["gateway_executable_identity"] and
                current["gateway_profile_identity"] ==
                    previous["gateway_profile_identity"] and
                current["gateway_domain_config_identity"] ==
                    previous["gateway_domain_config_identity"],
                "P1_AUDIT_CONTINUITY_PERSISTENT_IDENTITY_DRIFT", halt=True)
            lease_unchanged = (
                current["lease_generation"] == previous["lease_generation"] and
                all(
                    current["lease_receipt"][field] ==
                        previous["lease_receipt"][field]
                    for field in (
                        "file_sha256", "body_sha256", "schema")) and
                current["lease_receipt_document"] ==
                    previous["lease_receipt_document"])
            lease_advanced = (
                current["lease_generation"] ==
                    previous["lease_generation"] + 1 and
                current["previous_lease_generation"] ==
                    previous["lease_generation"] and
                current["previous_lease_receipt_body_sha256"] ==
                    previous["lease_receipt"]["body_sha256"])
            findings.require(
                current["export_commit_document"]["commit_sequence"] >
                    previous["export_commit_document"]["commit_sequence"] and
                (lease_unchanged or lease_advanced),
                "P1_AUDIT_CONTINUITY_LEASE_CHAIN_GAP", halt=True)
            gateway_changed = any(
                current[field] != previous[field] for field in (
                    "gateway_identity", "gateway_process_identity",
                    "tool_socket_identity", "supervisor_socket_identity"))
            transition = current["transition_fault_id"]
            if gateway_changed:
                findings.require(
                    transition in result_by_id and
                    result_by_id[transition]["fault_type"] ==
                        "SERVICE_RESTART" and
                    result_by_id[transition]["target_id"] ==
                        "watch-execution-gateway" and
                    result_by_id[transition]["recovery_verified"] and
                    result_by_id[transition]["cleanup_verified"] and
                    not any(result_by_id[transition][field] for field in (
                        "authority_failure", "audit_failure",
                        "cleanup_failure")) and
                    previous["observed_boottime_ns"] <=
                        result_by_id[transition]["injection_boottime_ns"] <=
                        result_by_id[transition]["recovered_boottime_ns"] <=
                        current["observed_boottime_ns"] and
                    sum(item.get("transition_fault_id") == transition
                        for item in checkpoint_values) == 1,
                    "P1_AUDIT_UNBOUND_CONTINUITY_TRANSITION", halt=True)
            else:
                findings.require(transition is None,
                                 "P1_AUDIT_SPURIOUS_CONTINUITY_TRANSITION",
                                 halt=True)
            previous = current
        duration_ns = end_ns - start_ns
        findings.require(duration_ns >= spec.minimum_boottime_duration_ns,
                         "P1_AUDIT_BOOTTIME_DURATION_BELOW_MINIMUM")
        for planned in planned_faults:
            findings.require(start_ns <= planned["inject_at_boottime_ns"] <= end_ns,
                             "P1_AUDIT_FAULT_OUTSIDE_SOAK_INTERVAL")
        for result in result_values:
            findings.require(
                start_ns <= result["injection_boottime_ns"] and
                result["recovered_boottime_ns"] <= end_ns,
                "P1_AUDIT_FAULT_RECOVERY_OUTSIDE_SOAK_INTERVAL")
    else:
        duration_ns = 0

    continuity_values = sorted(
        (value for _artifact, value in validated_observers
         if value.get("schema") ==
            CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA),
        key=lambda value: value["continuity_slot_index"])
    findings.require(
        bool(continuity_values) and
        len(continuity_values) == len(checkpoint_values),
        "P1_AUDIT_CAMPAIGN_CONTINUITY_CLOCK_CHAIN_INCOMPLETE", halt=True)
    for index, (raw, checkpoint) in enumerate(zip(
            continuity_values, checkpoint_values)):
        findings.require(
            raw["continuity_slot_index"] == index and
            checkpoint["continuity_slot_index"] == index and
            all(raw[field] == checkpoint[field] for field in (
                "campaign_runtime", "continuity_scheduled_at_ms",
                "continuity_origin_ms", "continuity_end_ms",
                "continuity_cadence_ms", "continuity_final_slot",
                "continuity_is_final", "catch_up")) and
            raw["observed_boottime_ns"] ==
                checkpoint["observed_boottime_ns"],
            "P1_AUDIT_RAW_CHECKPOINT_GRID_BINDING_INVALID", halt=True)
    if continuity_values and freeze_boot_id is not None and \
            frozen_wall_ms is not None and frozen_boottime_ns is not None:
        first_continuity = continuity_values[0]
        findings.require(
            first_continuity["boot_id"] == freeze_boot_id and
            first_continuity["observed_at_ms"] >= frozen_wall_ms and
            first_continuity["observed_boottime_ns"] >= frozen_boottime_ns and
            abs((first_continuity["observed_at_ms"] - frozen_wall_ms) *
                1_000_000 -
                (first_continuity["observed_boottime_ns"] -
                 frozen_boottime_ns)) <= CLOCK_CORRELATION_TOLERANCE_NS,
            "P1_AUDIT_FREEZE_CLOCK_ANCHOR_DRIFT", halt=True)
        for previous_continuity, current_continuity in zip(
                continuity_values, continuity_values[1:]):
            wall_delta_ns = (
                current_continuity["observed_at_ms"] -
                previous_continuity["observed_at_ms"]) * 1_000_000
            boot_delta_ns = (
                current_continuity["observed_boottime_ns"] -
                previous_continuity["observed_boottime_ns"])
            findings.require(
                current_continuity["boot_id"] == freeze_boot_id and
                0 < wall_delta_ns <= spec.maximum_checkpoint_gap_ns and
                0 < boot_delta_ns <= spec.maximum_checkpoint_gap_ns and
                abs(wall_delta_ns - boot_delta_ns) <=
                    CLOCK_CORRELATION_TOLERANCE_NS,
                "P1_AUDIT_CAMPAIGN_WALL_BOOTTIME_CHAIN_INVALID", halt=True)
        if decision_values:
            first_scheduled_wall = min(
                value["scheduled_at_ms"] for value in decision_values)
            last_evaluated_wall = max(
                value["evaluated_at_ms"] for value in decision_values)
            first_scheduled_boot = min(
                value["scheduled_boottime_ns"] for value in decision_values)
            last_evaluated_boot = max(
                value["evaluated_boottime_ns"] for value in decision_values)
            findings.require(
                first_continuity["observed_at_ms"] <= first_scheduled_wall and
                continuity_values[-1]["observed_at_ms"] >=
                    last_evaluated_wall and
                first_continuity["observed_boottime_ns"] <=
                    first_scheduled_boot and
                continuity_values[-1]["observed_boottime_ns"] >=
                    last_evaluated_boot and
                start_ns is not None and end_ns is not None and
                start_ns <= first_scheduled_boot <= last_evaluated_boot <=
                    end_ns,
                "P1_AUDIT_DECISION_INTERVAL_NOT_CONTINUOUSLY_COVERED",
                halt=True)

    authorities: list[tuple[Artifact, dict[str, Any]]] = []
    for artifact in authority_snapshots:
        try:
            snapshot = validate_authority_snapshot(artifact, spec)
            authorities.append((artifact, snapshot))
            if (snapshot["connector_count"] or snapshot["authorized_uids"] or
                    snapshot["paper_unit_active_count"] or
                    snapshot["campaign_socket_present"] or
                    not snapshot["kill_switch_engaged"] or
                    not snapshot["local_boundary_safe"] or
                    snapshot["local_boundary_uncertain"] or
                    snapshot["observation_scope"] !=
                        "LOCAL_HOST_BOUNDARY_ONLY" or
                    snapshot["authoritative_account_state_observed"]):
                findings.halt.add(
                    "P1_AUDIT_AUTHORITY_EXPOSURE_OR_UNCERTAINTY")
        except EvidenceError as error:
            findings.add(error)
    authorities.sort(key=lambda pair: pair[1].get("sequence", -1))
    authority_artifacts = [pair[0] for pair in authorities]
    authority_values = [pair[1] for pair in authorities]
    try:
        _validate_chain(
            authority_values, authority_artifacts, sequence_field="sequence",
            previous_field="previous_snapshot_body_sha256", first_sequence=0,
            reason="P1_AUDIT_AUTHORITY_SNAPSHOT_HASH_CHAIN_GAP")
    except EvidenceError as error:
        findings.add(error)
    findings.require(len(authority_values) >= 2,
                     "P1_AUDIT_AUTHORITY_SNAPSHOT_EVIDENCE_INSUFFICIENT")
    if authority_values and start_ns is not None and end_ns is not None:
        findings.require(authority_values[0]["observed_boottime_ns"] <= start_ns and
                         authority_values[-1]["observed_boottime_ns"] >= end_ns,
                         "P1_AUDIT_AUTHORITY_SNAPSHOT_COVERAGE_INVALID")
        previous_time = authority_values[0]["observed_boottime_ns"]
        for snapshot in authority_values:
            findings.require(snapshot["boot_id"] == boot_id,
                             "P1_AUDIT_AUTHORITY_SNAPSHOT_BOOT_DRIFT", halt=True)
            if snapshot is not authority_values[0]:
                findings.require(
                    0 < snapshot["observed_boottime_ns"] - previous_time <=
                        spec.maximum_checkpoint_gap_ns,
                    "P1_AUDIT_AUTHORITY_SNAPSHOT_GAP")
            previous_time = snapshot["observed_boottime_ns"]

    cleanups: list[tuple[Artifact, dict[str, Any]]] = []
    for artifact in cleanup_snapshots:
        try:
            cleanup = validate_cleanup_snapshot(artifact, spec)
            cleanups.append((artifact, cleanup))
            if (cleanup["watch_authority_count"] or
                    cleanup["export_residue_count"] or
                    cleanup["session_authority_count"] or
                    cleanup["paper_unit_active_count"] or
                    cleanup["campaign_socket_present"] or
                    not cleanup["cleanup_complete"] or
                    cleanup["cleanup_uncertain"] or cleanup["errors"]):
                findings.halt.add(
                    "P1_AUDIT_CLEANUP_FAILURE_OR_UNCERTAINTY")
        except EvidenceError as error:
            findings.add(error)
    cleanups.sort(key=lambda pair: pair[1].get("sequence", -1))
    cleanup_artifacts = [pair[0] for pair in cleanups]
    cleanup_values = [pair[1] for pair in cleanups]
    try:
        _validate_chain(
            cleanup_values, cleanup_artifacts, sequence_field="sequence",
            previous_field="previous_snapshot_body_sha256", first_sequence=0,
            reason="P1_AUDIT_CLEANUP_SNAPSHOT_HASH_CHAIN_GAP")
    except EvidenceError as error:
        findings.add(error)
    observed_subjects = {(value["subject_type"], value["subject_id"])
                         for value in cleanup_values}
    required_subjects = (
        {("LAUNCHER", item) for item in formal_ids} |
        {("FAULT", item) for item in planned_by_id} |
        {("FINAL", spec.campaign_id)}
    )
    findings.require(required_subjects.issubset(observed_subjects),
                     "P1_AUDIT_CLEANUP_SUBJECT_SET_INCOMPLETE")
    findings.require(len(observed_subjects) == len(cleanup_values),
                     "P1_AUDIT_DUPLICATE_CLEANUP_SUBJECT", halt=True)
    cleanup_by_subject = {
        (value["subject_type"], value["subject_id"]): value
        for value in cleanup_values
    }
    for fault_id, result in result_by_id.items():
        cleanup = cleanup_by_subject.get(("FAULT", fault_id))
        if cleanup is not None:
            findings.require(
                cleanup["observed_boottime_ns"] >= result["recovered_boottime_ns"],
                "P1_AUDIT_FAULT_CLEANUP_PRECEDES_RECOVERY", halt=True)
    final_cleanup = cleanup_by_subject.get(("FINAL", spec.campaign_id))
    if final_cleanup is not None and end_ns is not None:
        findings.require(final_cleanup["observed_boottime_ns"] >= end_ns,
                         "P1_AUDIT_FINAL_CLEANUP_TOO_EARLY")
        findings.require(final_cleanup["boot_id"] == boot_id,
                         "P1_AUDIT_FINAL_CLEANUP_BOOT_DRIFT", halt=True)

    _bind_observer_receipts(
        projected=[
            *checkpoint_values, *result_values,
            *authority_values, *cleanup_values,
        ],
        observers=validated_observers,
        additional_reference_keys=sorted({
            key for keys in anchor_keys_by_formal.values() for key in keys
        }),
        findings=findings,
    )
    _bind_fault_injection_receipts(
        spec=spec, planned_by_id=planned_by_id,
        observers=validated_observers,
        injection_receipts=fault_injection_receipts,
        findings=findings,
        expected_producer_sha256=(
            source_pins.get("root_fault_injector") or {}).get("file_sha256"),
    )

    if findings.halt:
        verdict = "HALT"
        next_action = "SUPERVISOR_HALT_AND_ROOT_RECONCILE"
    elif findings.no_go:
        verdict = "NO_GO"
        next_action = "REMAIN_SHADOW_AND_COLLECT_EVIDENCE"
    else:
        verdict = "GO"
        next_action = "CONTINUE_REMAINING_PAPER_ADMISSION_GATES"

    audited_at = (time.time_ns() // 1_000_000
                  if audited_at_ms is None else audited_at_ms)
    if not _is_int(audited_at, 0):
        raise AuditError("P1_AUDIT_TIME_INVALID")
    receipt = seal({
        "schema": "hepta.p1-safety-soak-audit-receipt.v1",
        "version": 1,
        "phase": "P1_SHADOW",
        "verdict": verdict,
        "campaign_id": spec.campaign_id,
        "domain_id": spec.domain_id,
        "independent_auditor_id": spec.independent_auditor_id,
        "audited_at_ms": audited_at,
        "campaign_spec_file_sha256": campaign_spec.file_sha256,
        "campaign_spec_body_sha256": campaign_spec.body_sha256,
        "freeze_bundle": dict(spec.freeze_bundle),
        "campaign_runtime": runtime_reference,
        "producer": producer_reference,
        "production_mode": production_mode,
        "source_manifest_sha256": spec.source_manifest_sha256,
        "policy_sha256": spec.policy_sha256,
        "strategy_sha256": spec.strategy_sha256,
        "evaluated_interval": {
            "clock_id": "CLOCK_BOOTTIME",
            "boot_id": boot_id,
            "start_boottime_ns": start_ns,
            "end_boottime_ns": end_ns,
            "duration_ns": duration_ns,
            "maximum_checkpoint_gap_ns": maximum_observed_gap_ns,
            "consecutive": bool(
                start_ns is not None and end_ns is not None and
                not ({"P1_AUDIT_CONTINUITY_CHECKPOINT_GAP",
                      "P1_AUDIT_BOOTTIME_BOOT_CHANGED"} & findings.no_go)),
            "continuity_origin_ms": grid_origin,
            "continuity_end_ms": grid_end,
            "continuity_final_slot": grid_final,
        },
        "counts": {
            "launcher_receipts": len(validated_launchers),
            "verified_closures": len(validated_closures),
            "continuity_checkpoints": len(checkpoint_values),
            "declared_trading_days": len(spec.declared_trading_days),
            "observed_trading_days": len(eligible_days),
            "scheduled_decisions": spec.scheduled_decision_count,
            "decision_receipts": len(decision_values),
            "eligible_decisions": len(eligible),
            "complete_eligible_decisions": len(complete),
            "incomplete_eligible_decisions": len(eligible) - len(complete),
            "catch_up_decisions": len(catch_up),
            "planned_faults": len(planned_faults),
            "fault_results": len(result_values),
            "authority_snapshots": len(authority_values),
            "cleanup_snapshots": len(cleanup_values),
        },
        "completeness": {
            "numerator": len(complete),
            "denominator": len(eligible),
            "ppm": complete_ppm,
            "strictly_greater_than_99_percent": greater_than_99,
        },
        "checked_artifacts": _checked_artifacts(all_artifacts),
        "failed_invariants": sorted(findings.halt | findings.no_go),
        "exposure_summary": {
            "evidence_present": bool(authority_values),
            "maximum_connector_count": max(
                (item["connector_count"] for item in authority_values),
                default=0),
            "maximum_authorized_uid_count": max(
                (len(item["authorized_uids"]) for item in authority_values),
                default=0),
            "maximum_paper_unit_active_count": max(
                (item["paper_unit_active_count"] for item in authority_values),
                default=0),
            "campaign_socket_ever_present": any(
                item["campaign_socket_present"] for item in authority_values),
            "kill_switch_continuously_engaged": bool(authority_values) and all(
                item["kill_switch_engaged"] for item in authority_values),
            "local_boundary_uncertain": any(
                item["local_boundary_uncertain"] for item in authority_values),
            "scope": "LOCAL_HOST_BOUNDARY_ONLY",
            "authoritative_account_state_observed": False,
        },
        "cleanup_status": {
            "required_subject_count": len(required_subjects),
            "verified_subject_count": len(required_subjects & observed_subjects),
            "complete": required_subjects.issubset(observed_subjects) and
                        not any(reason.startswith("P1_AUDIT_CLEANUP")
                                for reason in findings.halt | findings.no_go),
        },
        "p1_safety_soak_gate_satisfied": verdict == "GO",
        "paper_test_admission_candidate": False,
        "safest_allowed_next_action": next_action,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    })
    validate_audit_receipt(receipt)
    return receipt


def _load_many(paths: Sequence[Path], role: str) -> list[Artifact]:
    return [load_artifact(path, role, index) for index, path in enumerate(paths)]


def _assert_artifacts_unchanged(artifacts: Sequence[Artifact]) -> None:
    for index, artifact in enumerate(artifacts):
        path = Path(artifact.path)
        label = f"P1_AUDIT_REOPEN_{artifact.role.upper()}_{index}"
        payload = secure_read(path, label)
        document = decode_canonical_document(payload, label)
        _reject_authority(document, f"{label}_AUTHORITY_NOT_FALSE")
        if (digest_bytes(payload) != artifact.file_sha256 or
                document.get("body_sha256") != artifact.body_sha256):
            raise AuditError(f"{label}_DRIFT")


def _rename_noreplace(parent: int, source: str, destination: str) -> None:
    function = getattr(_LIBC, "renameat2", None)
    if function is None:
        raise AuditError("P1_AUDIT_RENAMEAT2_UNAVAILABLE")
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
            raise AuditError("P1_AUDIT_OUTPUT_ALREADY_EXISTS")
        raise AuditError("P1_AUDIT_OUTPUT_RENAME_FAILED")


def publish_receipt(
    receipt: dict[str, Any], output: Path, input_artifacts: Sequence[Artifact],
) -> str:
    """Atomically publish a new 0600 receipt and securely reopen it."""

    validate_audit_receipt(receipt)
    payload = canonical_bytes(receipt)
    if len(payload) > MAXIMUM_OUTPUT_BYTES:
        raise AuditError("P1_AUDIT_OUTPUT_TOO_LARGE")
    if (not output.is_absolute() or output.name in {"", ".", ".."} or
            any(part in {"", ".", ".."} for part in output.parts[1:])):
        raise AuditError("P1_AUDIT_OUTPUT_PATH_INVALID")
    if output in {Path(artifact.path) for artifact in input_artifacts}:
        raise AuditError("P1_AUDIT_OUTPUT_ALIASES_INPUT")
    _assert_artifacts_unchanged(input_artifacts)
    parent = _open_anchored_directory(output.parent, "P1_AUDIT_OUTPUT")
    temporary = (
        f".{output.name}.hepta-p1-audit-{secrets.token_hex(16)}.tmp")
    descriptor: int | None = None
    renamed = False
    try:
        parent_metadata = os.fstat(parent)
        if (parent_metadata.st_uid != os.geteuid() or
                stat.S_IMODE(parent_metadata.st_mode) & 0o022):
            raise AuditError("P1_AUDIT_OUTPUT_PARENT_UNTRUSTED")
        try:
            os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AuditError("P1_AUDIT_OUTPUT_ALREADY_EXISTS")
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
                 getattr(os, "O_NOFOLLOW", 0))
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise AuditError("P1_AUDIT_OUTPUT_WRITE_FAILED")
            written += count
        os.fsync(descriptor)
        prepared = os.fstat(descriptor)
        if (not stat.S_ISREG(prepared.st_mode) or prepared.st_nlink != 1 or
                prepared.st_uid != os.geteuid() or
                stat.S_IMODE(prepared.st_mode) != 0o600 or
                prepared.st_size != len(payload)):
            raise AuditError("P1_AUDIT_OUTPUT_METADATA_INVALID")
        os.fsync(parent)
        _assert_artifacts_unchanged(input_artifacts)
        _rename_noreplace(parent, temporary, output.name)
        renamed = True
        os.fsync(parent)
    except AuditError:
        raise
    except OSError as error:
        raise AuditError("P1_AUDIT_OUTPUT_PUBLISH_FAILED") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not renamed:
            try:
                os.unlink(temporary, dir_fd=parent)
                os.fsync(parent)
            except (FileNotFoundError, OSError):
                pass
        os.close(parent)
    committed = secure_read(
        output, "P1_AUDIT_OUTPUT_POST_VERIFY", MAXIMUM_OUTPUT_BYTES)
    if committed != payload:
        raise AuditError("P1_AUDIT_OUTPUT_POST_VERIFY_FAILED")
    restored = decode_canonical_document(
        committed, "P1_AUDIT_OUTPUT_POST_VERIFY")
    try:
        validate_audit_receipt(restored)
    except EvidenceError as error:
        raise AuditError("P1_AUDIT_OUTPUT_POST_VERIFY_FAILED") from error
    return digest_bytes(committed)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only cumulative P1 SHADOW safety/soak auditor")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--campaign-spec", required=True, type=Path)
    parser.add_argument("--campaign-runtime", required=True, type=Path)
    parser.add_argument("--launcher-receipt", action="append", default=[],
                        type=Path)
    parser.add_argument("--verified-closure", action="append", default=[],
                        type=Path)
    parser.add_argument("--decision-receipt", action="append", default=[],
                        type=Path)
    parser.add_argument("--continuity-checkpoint", action="append", default=[],
                        type=Path)
    parser.add_argument("--fault-plan", required=True, type=Path)
    parser.add_argument("--fault-result", action="append", default=[], type=Path)
    parser.add_argument("--authority-snapshot", action="append", default=[],
                        type=Path)
    parser.add_argument("--cleanup-snapshot", action="append", default=[],
                        type=Path)
    parser.add_argument("--observer-receipt", action="append", default=[],
                        type=Path)
    parser.add_argument("--fault-injection-receipt", action="append",
                        default=[], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        _require(arguments.run, "P1_AUDIT_EXPLICIT_RUN_REQUIRED")
        _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
                 "P1_AUDIT_ROOT_REQUIRED")
        producer = bind_executing_image()
        producer.reopen()
        campaign_spec = load_artifact(
            arguments.campaign_spec, "campaign_spec")
        campaign_runtime = load_artifact(
            arguments.campaign_runtime, "campaign_runtime")
        spec = validate_spec(campaign_spec)
        freeze_bundle = load_artifact(
            Path(spec.freeze_bundle["path"]), "freeze_bundle")
        _require(_reference(freeze_bundle) == spec.freeze_bundle,
                 "P1_AUDIT_FREEZE_BUNDLE_REFERENCE_DRIFT")
        calendar_reference = _validate_reference(
            freeze_bundle.document.get("trading_calendar"),
            "P1_AUDIT_TRADING_CALENDAR_REFERENCE_INVALID")
        trading_calendar = load_artifact(
            Path(calendar_reference["path"]), "trading_calendar")
        _require(_reference(trading_calendar) == calendar_reference,
                 "P1_AUDIT_TRADING_CALENDAR_REFERENCE_DRIFT")
        source_pins = validate_freeze_lineage(
            freeze_bundle, trading_calendar, spec, producer.reference)
        assert_installed_source_pins(source_pins)
        launcher_receipts = _load_many(
            arguments.launcher_receipt, "launcher_receipt")
        verified_closures = _load_many(
            arguments.verified_closure, "verified_closure")
        decision_receipts = _load_many(
            arguments.decision_receipt, "decision_receipt")
        continuity_checkpoints = _load_many(
            arguments.continuity_checkpoint, "continuity_checkpoint")
        fault_plan = load_artifact(arguments.fault_plan, "fault_plan")
        fault_results = _load_many(arguments.fault_result, "fault_result")
        authority_snapshots = _load_many(
            arguments.authority_snapshot, "authority_snapshot")
        cleanup_snapshots = _load_many(
            arguments.cleanup_snapshot, "cleanup_snapshot")
        observer_receipts = _load_many(
            arguments.observer_receipt, "observer_receipt")
        fault_injection_receipts = _load_many(
            arguments.fault_injection_receipt,
            "fault_injection_receipt")
        receipt = audit_evidence(
            campaign_spec=campaign_spec,
            launcher_receipts=launcher_receipts,
            verified_closures=verified_closures,
            decision_receipts=decision_receipts,
            continuity_checkpoints=continuity_checkpoints,
            fault_plan=fault_plan, fault_results=fault_results,
            authority_snapshots=authority_snapshots,
            cleanup_snapshots=cleanup_snapshots,
            observer_receipts=observer_receipts,
            fault_injection_receipts=fault_injection_receipts,
            freeze_bundle=freeze_bundle,
            campaign_runtime=campaign_runtime,
            trading_calendar=trading_calendar,
            producer=producer.reference,
        )
        producer.reopen()
        assert_installed_source_pins(source_pins)
        publish_receipt(
            receipt, arguments.output,
            [campaign_spec, campaign_runtime, freeze_bundle, trading_calendar,
             *launcher_receipts, *verified_closures,
             *decision_receipts, *continuity_checkpoints, fault_plan,
             *fault_results, *authority_snapshots, *cleanup_snapshots,
             *observer_receipts, *fault_injection_receipts],
        )
        producer.reopen()
        assert_installed_source_pins(source_pins)
    except (AuditError, OSError, ValueError) as error:
        print("hepta_p1_safety_soak_auditor: FAIL " + str(error),
              file=sys.stderr)
        return 3
    sys.stdout.buffer.write(canonical_bytes(receipt))
    return {"GO": 0, "NO_GO": 1, "HALT": 2}[receipt["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
