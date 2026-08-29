#!/usr/bin/env python3
"""Produce immutable P1 SHADOW soak evidence without operating anything.

The recorder freezes campaign inputs, projects independently verified SHADOW
artifacts into the exact contracts consumed by the P1 safety-soak auditor, and
records independently observed continuity, fault, authority, and cleanup
facts.  It has no systemd, broker, network, credential, order, or authority
surface.  In particular, authority state is copied only from a separately
produced root-owned observer receipt.

Every transaction is prepared in an immutable WAL before its business outputs
are published.  Outputs use 0600, RENAME_NOREPLACE, fd and directory fsync,
and canonical reopen.  A gap-free append-only journal commits the transaction;
an interrupted transaction is completed from its WAL after every referenced
input is securely reopened and rehashed.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
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
import sys
import time
from typing import Any, Callable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


VERSION = 1
ROOT_UID = 0
ROOT_GID = 0
MAXIMUM_INPUT_BYTES = 16 * 1024 * 1024
MAXIMUM_OUTPUT_BYTES = 16 * 1024 * 1024
MAXIMUM_OBSERVER_AGE_MS = 5 * 60 * 1000
MAXIMUM_OBSERVER_LIFETIME_MS = 4 * 60 * 1000
MAXIMUM_CLOCK_SKEW_MS = 30 * 1000
CLOCK_CORRELATION_TOLERANCE_NS = 1_000_000_000
MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS = 30 * 1_000_000_000
MAXIMUM_FAULT_INJECTION_LATENESS_NS = 30 * 1_000_000_000
MAXIMUM_FAULT_RECOVERY_NS = 5 * 60 * 1_000_000_000
MINIMUM_CLOCK_STEP_MS = 100
MAXIMUM_CLOCK_STEP_MS = 60 * 1000
MINIMUM_BOOTTIME_DURATION_NS = 72 * 60 * 60 * 1_000_000_000
MAXIMUM_CHECKPOINT_GAP_NS = 15 * 60 * 1_000_000_000
MINIMUM_TRADING_DAYS = 10
MAXIMUM_TRADING_DAYS = 20
MINIMUM_ELIGIBLE_DECISIONS = 200
MINIMUM_COMPLETE_PPM = 990_001
POST_FORMAL_PROJECTION_GUARD_MS = 20 * 60 * 1000
LAUNCHER_WARMUP_MS = 210 * 60 * 1000
LAUNCHER_EARLY_START_LEAD_MS = 20 * 60 * 1000
POLICY_SLOT_INTERVAL_MS = 2 * 60 * 1000
POLICY_MAXIMUM_ITERATIONS = 241
POLICY_MAXIMUM_LATENESS_MS = 60 * 1000
MAXIMUM_LAUNCH_LATENESS_MS = 15 * 60 * 1000
POST_FORMAL_TEARDOWN_GUARD_MS = 30 * 60 * 1000
INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-evidence-recorder")
PRODUCTION_MODE = "PRODUCTION_ROOT_EVIDENCE_RECORDING"

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}")
NUMBERED_JSON = re.compile(r"([0-9]{8})\.json")
EXPORT_GENERATION = re.compile(
    r"generation-([0-9]{20})-([A-Za-z0-9_-]{8,64})")
EXPORT_COMMIT_NAME = "current.json"
EXPORT_GENERATIONS_NAME = "generations"
EXPORT_FILES = (
    "snapshot.json",
    "shadow-watch-lease-receipt.json",
    "shadow-watch-export-receipt.json",
)

PERMISSION_FIELDS = frozenset({
    "paper_authorized", "live_authorized", "mutation_authorized",
    "mutation_attempted", "direct_broker_access",
})
BOUNDARY_FIELDS = (
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access",
)

NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW | CLOEXEC
READ_FLAGS = os.O_RDONLY | NOFOLLOW | CLOEXEC | NONBLOCK
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC
RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)

SPEC_SCHEMA = "hepta.p1-safety-soak-campaign-spec.v1"
FAULT_PLAN_SCHEMA = "hepta.p1-safety-soak-fault-plan.v1"
CHECKPOINT_SCHEMA = "hepta.p1-safety-soak-continuity-checkpoint.v1"
DECISION_SCHEMA = "hepta.p1-safety-soak-decision-receipt.v1"
FAULT_RESULT_SCHEMA = "hepta.p1-safety-soak-fault-result.v1"
AUTHORITY_SCHEMA = "hepta.p1-safety-soak-authority-snapshot.v1"
CLEANUP_SCHEMA = "hepta.p1-safety-soak-cleanup-snapshot.v1"
FORMAL_POLICY_SCHEMA = "hepta.strategy-shadow-observation-policy.v1"
VERIFIED_CLOSURE_SCHEMA = "hepta.bounded-shadow-campaign-closure.v1"

SOURCE_ANCHOR_SCHEMA = "hepta.p1-safety-soak-frozen-source.v1"
POLICY_ANCHOR_SCHEMA = "hepta.p1-safety-soak-frozen-policy.v1"
STRATEGY_ANCHOR_SCHEMA = "hepta.p1-safety-soak-frozen-strategy.v1"
SCHEDULE_SCHEMA = "hepta.p1-safety-soak-frozen-schedule.v1"
FAULT_SCHEDULE_SCHEMA = "hepta.p1-safety-soak-frozen-fault-schedule.v1"
FREEZE_BUNDLE_SCHEMA = "hepta.p1-safety-soak-freeze-bundle-receipt.v1"
FREEZER_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-campaign-freezer")
FREEZER_PRODUCTION_MODE = "PRODUCTION_ROOT_PREFLIGHT"
CALENDAR_SCHEMA = "hepta.p1-safety-soak-reviewed-trading-calendar.v1"
CALENDAR_ID = "EURUSD_NY_CORE_2026"
CALENDAR_VERSION = "v1"
CALENDAR_TIMEZONE = "America/New_York"
CALENDAR_EXCLUDED_DAYS_2026 = frozenset({
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
})
OBSERVER_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-independent-observer")
OBSERVER_PRODUCTION_MODE = "PRODUCTION_ROOT_OBSERVER"
SERVICE_OBSERVATION_SCHEMA = (
    "hepta.p1-safety-soak-independent-service-observation.v1")
CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA = (
    "hepta.p1-safety-soak-independent-campaign-continuity-observation.v1")
CAMPAIGN_RUNTIME_SCHEMA = "hepta.p1-safety-soak-campaign-runtime.v1"
FAULT_OBSERVATION_SCHEMA = (
    "hepta.p1-safety-soak-independent-fault-observation.v1")
AUTHORITY_OBSERVATION_SCHEMA = (
    "hepta.p1-safety-soak-independent-authority-observation.v1")
CLEANUP_OBSERVATION_SCHEMA = (
    "hepta.p1-safety-soak-independent-cleanup-observation.v1")
TRANSACTION_SCHEMA = "hepta.p1-safety-soak-recorder-transaction.v1"
JOURNAL_SCHEMA = "hepta.p1-safety-soak-recorder-journal-entry.v1"

FREEZER_PRODUCER_FIELDS = frozenset({"path", "file_sha256"})
OBSERVER_PRODUCER_FIELDS = frozenset({"path", "file_sha256"})
FREEZE_SOURCE_PRODUCER_PIN_FIELDS = frozenset({
    "role", "source_path", "installed_path", "file_sha256",
})
FREEZE_SOURCE_PRODUCER_PATHS = {
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
        "/usr/libexec/hepta-p1-safety-soak-root-fault-injector"),
    "auditor": (
        "scripts/hepta_p1_safety_soak_auditor.py",
        "/usr/libexec/hepta-p1-safety-soak-auditor"),
    "shadow_admission_launcher": (
        "scripts/hepta_p1_shadow_admission_launcher.py",
        "/usr/libexec/hepta-p1-shadow-admission-launcher"),
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
FREEZE_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256",
})
FREEZE_ANCHOR_COMMON_FIELDS = frozenset({
    "freeze_id", "producer", "production_mode",
})
SOURCE_ANCHOR_FIELDS = frozenset({
    "schema", "version", "status", "source_manifest_sha256",
    "source_frozen", "clean_source", "frozen_at_ms", "expires_at_ms",
    *FREEZE_ANCHOR_COMMON_FIELDS, *BOUNDARY_FIELDS, "body_sha256",
})
POLICY_ANCHOR_FIELDS = frozenset({
    "schema", "version", "status", "policy_sha256", "policy_frozen",
    "frozen_at_ms", "expires_at_ms", *FREEZE_ANCHOR_COMMON_FIELDS,
    *BOUNDARY_FIELDS, "body_sha256",
})
STRATEGY_ANCHOR_FIELDS = frozenset({
    "schema", "version", "status", "strategy_id", "strategy_version",
    "strategy_sha256", "strategy_frozen", "frozen_at_ms", "expires_at_ms",
    *FREEZE_ANCHOR_COMMON_FIELDS, *BOUNDARY_FIELDS, "body_sha256",
})
SCHEDULE_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "domain_id",
    "declared_trading_days", "trading_timezone",
    "eligible_scheduled_at_ms", "minimum_eligible_decisions",
    "minimum_complete_ppm", "minimum_boottime_duration_ns",
    "maximum_checkpoint_gap_ns", "maximum_decision_lateness_ms",
    "independent_auditor_id", "frozen_at_ms", "expires_at_ms",
    *FREEZE_ANCHOR_COMMON_FIELDS, *BOUNDARY_FIELDS, "body_sha256",
})
FAULT_SCHEDULE_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "boot_id",
    "frozen_boottime_ns", "planned_faults", "frozen_at_ms",
    "expires_at_ms", *FREEZE_ANCHOR_COMMON_FIELDS,
    *BOUNDARY_FIELDS, "body_sha256",
})

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
    "policy_sha256",
    "formal_policies", "strategy_id", "strategy_version",
    "strategy_sha256", "strategy_files", "trading_calendar",
    "calendar_id", "calendar_version", "calendar_source_sha256",
    "declared_trading_days",
    "trading_timezone", "trading_calendar_sha256",
    "eligible_scheduled_at_ms", "scheduled_decision_count",
    "planned_faults", "anchors", *BOUNDARY_FIELDS, "body_sha256",
})
CALENDAR_WINDOW_FIELDS = frozenset({"opens_at_ms", "closes_at_ms"})
CALENDAR_SESSION_FIELDS = frozenset({
    "trading_day", "opens_at_ms", "closes_at_ms", "maintenance_windows",
})
CALENDAR_FIELDS = frozenset({
    "schema", "version", "status", "freeze_id", "producer",
    "production_mode", "calendar_id", "calendar_version",
    "calendar_source_sha256", "trading_timezone", "sessions",
    "issued_at_ms", "expires_at_ms", *BOUNDARY_FIELDS, "body_sha256",
})

FORMAL_POLICY_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256",
    "strategy_id", "strategy_version", "strategy_sha256",
    "valid_after_ms", "expires_at_ms", "slot_interval_ms",
    "maximum_iterations", "maximum_lateness_ms", "shadow_only",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
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
    "inject_at_boottime_ns", "maximum_injection_lateness_ns",
    "maximum_recovery_ns",
})
FAULT_PLAN_FIELDS = frozenset({
    "schema", "version", "campaign_id", "source_manifest_sha256",
    "policy_sha256", "strategy_sha256", "planned_faults",
    *BOUNDARY_FIELDS, "body_sha256",
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

SERVICE_OBSERVATION_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "campaign_id", "observer_id", "observation_complete", "clock_id",
    "boot_id", "observed_boottime_ns", "service_epoch",
    "fencing_generation", "lease_generation", "transition_fault_id",
    "source_manifest_sha256", "policy_sha256", "strategy_sha256",
    "continuity_ok", "audit_ok", "cleanup_ok", "producer",
    "production_mode", "observation_evidence",
    *BOUNDARY_FIELDS,
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
    *BOUNDARY_FIELDS, "body_sha256",
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
    "previous_checkpoint_body_sha256", "observer_receipt",
    *BOUNDARY_FIELDS, "body_sha256",
})

DECISION_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "decision_id",
    "formal_campaign_id", "verified_closure_body_sha256",
    "closure_iteration", "trading_day", "scheduled_at_ms",
    "evaluated_at_ms", "clock_id", "boot_id", "scheduled_boottime_ns",
    "evaluated_boottime_ns", "clock_observer_receipt",
    "eligible", "complete", "catch_up", "outcome",
    "source_manifest_sha256", "policy_sha256", "strategy_sha256",
    "decision_artifact_file_sha256", "evidence_sha256",
    "previous_receipt_body_sha256", "audit_failure", "cleanup_failure",
    *BOUNDARY_FIELDS, "body_sha256",
})
ACTUAL_DECISION_FIELDS = frozenset({
    "schema", "campaign_id", "strategy_id", "strategy_version",
    "strategy_sha256", "decision_id", "cycle_id", "started_at_ms",
    "finished_at_ms", "paper_only", "live_authorized", "shadow_only",
    "information_packet_sha256", "catalog_sha256", "descriptor_sha256",
    "preflight_sha256", "regime", "setup_gates", "risk_challenges",
    "evidence_refs", "conflicts", "decision", "reason_codes",
    "trade_intent", "trade_intent_sha256", "campaign_open_request_id",
    "campaign_close_request_id", "mutation_attempted",
    "direct_broker_access", "final_outcome",
})
TRADE_INTENT_FIELDS = frozenset({
    "schema", "paper_only", "strategy_id", "strategy_version",
    "strategy_sha256", "intent_id", "instrument", "symbol", "currency",
    "sec_type", "exchange", "side", "quantity", "order_type",
    "limit_price", "tif", "observed_bid", "observed_ask",
    "observed_at_ms", "expires_at_ms", "entry_thesis",
    "invalidation_condition", "max_holding_ms", "max_adverse_move",
    "expected_slippage", "exit_plan",
})
ACTUAL_REGIMES = frozenset({
    "trend", "range", "event", "illiquid", "transition", "unknown",
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
    "watch_lease_receipt_file_sha256", "watch_export_receipt_body_sha256",
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

FAULT_OBSERVATION_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "campaign_id", "observer_id", "observation_complete", "clock_id",
    "boot_id", "fault_id", "fault_type", "target_id",
    "injection_boottime_ns",
    "recovered_boottime_ns", "recovery_verified", "cleanup_verified",
    "authority_failure", "audit_failure", "cleanup_failure",
    "source_manifest_sha256", "policy_sha256", "strategy_sha256",
    "producer", "production_mode", "observation_evidence",
    *BOUNDARY_FIELDS, "body_sha256",
})
FAULT_RESULT_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "fault_id",
    "fault_type", "target_id", "injection_boottime_ns",
    "recovered_boottime_ns",
    "recovery_verified", "cleanup_verified", "evidence_sha256",
    "observer_receipt", "previous_result_body_sha256", "authority_failure",
    "audit_failure", "cleanup_failure", *BOUNDARY_FIELDS, "body_sha256",
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
    "observation_evidence", *BOUNDARY_FIELDS,
    "body_sha256",
})
AUTHORITY_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "clock_id", "boot_id",
    "observed_boottime_ns", "source_manifest_sha256", "policy_sha256",
    "strategy_sha256", "connector_count", "authorized_uids",
    "paper_unit_active_count", "campaign_socket_present",
    "kill_switch_engaged", "local_boundary_safe",
    "local_boundary_uncertain", "observation_scope",
    "authoritative_account_state_observed", "observer_receipt",
    "previous_snapshot_body_sha256",
    *BOUNDARY_FIELDS, "body_sha256",
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
    *BOUNDARY_FIELDS, "body_sha256",
})
CLEANUP_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "clock_id", "boot_id",
    "observed_boottime_ns", "subject_type", "subject_id",
    "watch_authority_count", "export_residue_count",
    "session_authority_count", "paper_unit_active_count",
    "campaign_socket_present", "cleanup_complete", "cleanup_uncertain",
    "errors", "observer_receipt", "previous_snapshot_body_sha256",
    *BOUNDARY_FIELDS,
    "body_sha256",
})

REFERENCE_FIELDS = frozenset({
    "role", "path", "file_sha256", "body_sha256", "schema", "sealed",
})
OBSERVER_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "schema",
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
RUNTIME_FILE_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256",
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
CAMPAIGN_RUNTIME_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id", "round", "boot_id",
    "issued_at_ms", "expires_at_ms", "freeze_bundle", "campaign_spec",
    "fault_plan", "pin_formal_campaign_id", "formal_campaigns",
    "observer_cadence_ms", "maximum_slot_lateness_ms", "state_root",
    "raw_observation_directory", "recorder_root",
    "injector_journal_directory", "injector_output_directory",
    "control_directory", "executables", *BOUNDARY_FIELDS, "body_sha256",
})
GATEWAY_EXECUTABLE = Path("/usr/libexec/hepta-tool-gatewayd")
GATEWAY_PROFILE = Path("/etc/heptatrader/trust-domains/alpha.env")
GATEWAY_DOMAIN_CONFIG = Path("/etc/heptatrader/trust-domains/alpha.json")
GATEWAY_TOOL_SOCKET = Path("/run/hepta-agent-alpha/tools.sock")
GATEWAY_SUPERVISOR_SOCKET = Path(
    "/run/hepta-tool-gateway-alpha/session-supervisor.sock")
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
FAULT_INJECTOR_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-safety-soak-root-fault-injector")
FAULT_INJECTOR_PRODUCTION_MODE = "PRODUCTION_ROOT_FAULT_INJECTION"
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
    *BOUNDARY_FIELDS, "body_sha256",
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
TRANSACTION_OUTPUT_FIELDS = frozenset({
    "role", "path", "file_sha256", "body_sha256", "document",
})
TRANSACTION_FIELDS = frozenset({
    "schema", "version", "campaign_id", "transaction_id", "operation",
    "created_at_ms", "inputs", "outputs", *BOUNDARY_FIELDS,
    "body_sha256",
})
JOURNAL_FIELDS = frozenset({
    "schema", "version", "campaign_id", "sequence", "operation",
    "recorded_at_ms", "transaction_reference", "inputs", "outputs",
    "previous_entry_body_sha256", *BOUNDARY_FIELDS, "body_sha256",
})

OPERATIONS = frozenset({
    "FREEZE", "CHECKPOINT", "PROJECT_DECISIONS", "RECORD_FAULT",
    "RECORD_AUTHORITY", "RECORD_CLEANUP",
})
ROLE_SCHEMA_FIELDS: Mapping[str, tuple[str, frozenset[str]]] = {
    "campaign_spec": (SPEC_SCHEMA, SPEC_FIELDS),
    "fault_plan": (FAULT_PLAN_SCHEMA, FAULT_PLAN_FIELDS),
    "continuity_checkpoint": (CHECKPOINT_SCHEMA, CHECKPOINT_FIELDS),
    "decision_receipt": (DECISION_SCHEMA, DECISION_FIELDS),
    "fault_result": (FAULT_RESULT_SCHEMA, FAULT_RESULT_FIELDS),
    "authority_snapshot": (AUTHORITY_SCHEMA, AUTHORITY_FIELDS),
    "cleanup_snapshot": (CLEANUP_SCHEMA, CLEANUP_FIELDS),
}
OUTPUT_OBSERVER_SCHEMAS: Mapping[str, str] = {
    "continuity_checkpoint": CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA,
    "fault_result": FAULT_OBSERVATION_SCHEMA,
    "authority_snapshot": AUTHORITY_OBSERVATION_SCHEMA,
    "cleanup_snapshot": CLEANUP_OBSERVATION_SCHEMA,
}

STREAMS: Mapping[str, tuple[str, int, str]] = {
    "continuity_checkpoint": ("checkpoints", 0,
                              "previous_checkpoint_body_sha256"),
    "decision_receipt": ("decisions", 1,
                         "previous_receipt_body_sha256"),
    "fault_result": ("fault-results", 1, "previous_result_body_sha256"),
    "authority_snapshot": ("authority-snapshots", 0,
                           "previous_snapshot_body_sha256"),
    "cleanup_snapshot": ("cleanup-snapshots", 0,
                         "previous_snapshot_body_sha256"),
}


class RecorderError(RuntimeError):
    """Stable fail-closed recorder error."""

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
    role: str
    path: Path
    payload: bytes
    metadata: os.stat_result
    document: dict[str, Any]
    file_sha256: str
    body_sha256: str
    sealed: bool


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
        payload, metadata = secure_read(
            INSTALLED_EXECUTABLE, expected_uid=ROOT_UID,
            maximum=MAXIMUM_INPUT_BYTES,
            modes=frozenset({0o555, 0o755}))
        _require(payload == self.payload and
                 _file_identity(metadata) == _file_identity(self.metadata),
                 "P1_RECORDER_EXECUTING_IMAGE_DRIFT")


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise RecorderError("P1_RECORDER_CANONICALIZATION_FAILED") from error


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def seal(body: Mapping[str, Any]) -> dict[str, Any]:
    document = dict(body)
    document["body_sha256"] = digest_bytes(canonical_bytes(document))
    return document


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise RecorderError(reason)


def _integer(value: Any, reason: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, reason)
    return value


def _signed_integer(value: Any, reason: str) -> int:
    _require(type(value) is int, reason)
    return value


def _number(value: Any, reason: str, *, minimum: float | None = None) -> float:
    _require(type(value) in {int, float} and math.isfinite(value), reason)
    result = float(value)
    if minimum is not None:
        _require(result >= minimum, reason)
    return result


def _digest(value: Any, reason: str) -> str:
    _require(type(value) is str and DIGEST.fullmatch(value) is not None, reason)
    return value


def _identifier(value: Any, reason: str) -> str:
    _require(type(value) is str and IDENTIFIER.fullmatch(value) is not None,
             reason)
    return value


def _exact(value: Any, fields: frozenset[str], reason: str) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields, reason)
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
        lock.get("nlink") == 1 and lock.get("uid") == 0 and
        lock.get("gid") == 0 and lock.get("mode") == "0600" and
        lock.get("size") == 0 and
        type(lock.get("mtime_ns")) is int and lock["mtime_ns"] >= 0 and
        type(lock.get("ctime_ns")) is int and lock["ctime_ns"] >= 0 and
        type(lock.get("created_during_transaction")) is bool and
        lock.get("persistent") is True and
        lock.get("held_during_transaction") is True, reason)
    return evidence


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


def _strict_document(payload: bytes, reason: str, *, sealed: bool) \
        -> tuple[dict[str, Any], str]:
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
                RecorderError(reason)),
        )
    except RecorderError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise RecorderError(reason) from error
    _require(isinstance(document, dict) and
             payload == canonical_bytes(document), reason)
    _reject_authority(document, reason)
    if sealed:
        body_sha256 = _validate_seal(document, reason)
    else:
        _require("body_sha256" not in document, reason)
        body_sha256 = digest_bytes(canonical_bytes(document))
    return document, body_sha256


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_uid, value.st_gid,
        value.st_mode, value.st_nlink, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
    # Directory content mutation changes size/times and the link count when
    # unrelated child directories appear.  Those are deliberately excluded;
    # dev/inode/owner/mode still bind the stable no-follow anchor.
    return (
        value.st_dev, value.st_ino, value.st_uid, value.st_gid,
        stat.S_IMODE(value.st_mode),
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
        raise RecorderError(reason) from error
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
    except (OSError, RecorderError) as error:
        os.close(descriptor)
        if isinstance(error, RecorderError):
            raise
        raise RecorderError(reason) from error


def secure_read(
    path: Path, *, expected_uid: int = ROOT_UID,
    maximum: int = MAXIMUM_INPUT_BYTES,
    modes: frozenset[int] = frozenset({0o400, 0o600}),
) -> tuple[bytes, os.stat_result]:
    """Anchored no-follow read with full file identity and canonical reopen."""

    path = _canonical_path(path, "P1_RECORDER_INPUT_PATH_INVALID")
    parent = _open_directory(path.parent, "P1_RECORDER_INPUT_PARENT_INVALID")
    rebound_parent: int | None = None
    descriptor: int | None = None
    reopened: int | None = None
    try:
        parent_before = os.fstat(parent)
        _require(parent_before.st_uid == expected_uid and
                 stat.S_IMODE(parent_before.st_mode) & 0o022 == 0,
                 "P1_RECORDER_INPUT_PARENT_UNTRUSTED")
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        opened = os.fstat(descriptor)
        _require(
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            opened.st_uid == expected_uid and
            stat.S_IMODE(opened.st_mode) in modes and
            0 < opened.st_size <= maximum and
            _file_identity(before) == _file_identity(opened),
            "P1_RECORDER_INPUT_METADATA_INVALID")
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
            path.parent, "P1_RECORDER_INPUT_PARENT_INVALID")
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
            "P1_RECORDER_INPUT_SECURE_REOPEN_MISMATCH")
        reopened_payload = bytearray()
        while len(reopened_payload) <= maximum:
            chunk = os.read(
                reopened, min(65536, maximum + 1 - len(reopened_payload)))
            if not chunk:
                break
            reopened_payload.extend(chunk)
        _require(bytes(payload) == bytes(reopened_payload),
                 "P1_RECORDER_INPUT_SECURE_REOPEN_MISMATCH")
        return bytes(payload), opened
    except RecorderError:
        raise
    except OSError as error:
        raise RecorderError("P1_RECORDER_INPUT_SECURE_READ_FAILED") from error
    finally:
        for file_descriptor in (
                reopened, descriptor, rebound_parent, parent):
            if file_descriptor is not None:
                os.close(file_descriptor)


def bind_executing_image() -> ProducerBinding:
    """Bind production execution to the fixed root-owned installed helper."""

    try:
        lexical = Path(__file__).absolute()
        before = os.lstat(lexical)
        resolved = lexical.resolve(strict=True)
        installed = INSTALLED_EXECUTABLE.resolve(strict=True)
        _require(not stat.S_ISLNK(before.st_mode) and
                 resolved == installed == INSTALLED_EXECUTABLE and
                 os.path.samefile(lexical, INSTALLED_EXECUTABLE),
                 "P1_RECORDER_INSTALLED_IMAGE_REQUIRED")
        payload, metadata = secure_read(
            INSTALLED_EXECUTABLE, expected_uid=ROOT_UID,
            maximum=MAXIMUM_INPUT_BYTES,
            modes=frozenset({0o555, 0o755}))
        _require(_file_identity(before) == _file_identity(metadata),
                 "P1_RECORDER_INSTALLED_IMAGE_REQUIRED")
        return ProducerBinding(payload=payload, metadata=metadata)
    except RecorderError:
        raise
    except OSError as error:
        raise RecorderError("P1_RECORDER_INSTALLED_IMAGE_REQUIRED") from error


def load_snapshot(
    path: Path, role: str, *, sealed: bool = True,
    expected_uid: int = ROOT_UID,
) -> Snapshot:
    payload, metadata = secure_read(path, expected_uid=expected_uid)
    document, body_sha256 = _strict_document(
        payload, f"P1_RECORDER_{role.upper()}_INVALID", sealed=sealed)
    return Snapshot(
        role=role, path=path, payload=payload, metadata=metadata,
        document=document, file_sha256=digest_bytes(payload),
        body_sha256=body_sha256, sealed=sealed)


def _reference(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "role": snapshot.role,
        "path": str(snapshot.path),
        "file_sha256": snapshot.file_sha256,
        "body_sha256": snapshot.body_sha256,
        "schema": snapshot.document.get("schema"),
        "sealed": snapshot.sealed,
    }


def _observer_reference(snapshot: Snapshot) -> dict[str, str]:
    _require(snapshot.sealed,
             "P1_RECORDER_OBSERVER_REFERENCE_UNSEALED")
    return {
        "path": str(snapshot.path),
        "file_sha256": snapshot.file_sha256,
        "body_sha256": snapshot.body_sha256,
        "schema": str(snapshot.document.get("schema")),
    }


def _validate_observer_reference(
    value: Any, expected_schema: str, reason: str,
) -> dict[str, str]:
    reference = _exact(value, OBSERVER_REFERENCE_FIELDS, reason)
    path = reference.get("path")
    _require(type(path) is str, reason)
    _canonical_path(Path(path), reason)
    _digest(reference.get("file_sha256"), reason)
    _digest(reference.get("body_sha256"), reason)
    _require(reference.get("schema") == expected_schema, reason)
    return reference


def _validate_export_projection(
    document: Mapping[str, Any], *, expected_uid: int, reason: str,
) -> tuple[dict[str, Any], tuple[dict[str, str], ...]]:
    """Validate the committed pointer and its exact generation references."""

    commit_reference = _validate_observer_reference(
        document.get("export_commit"),
        "hepta.shadow-watch-export-commit.v1", reason)
    commit = _exact(
        document.get("export_commit_document"), EXPORT_COMMIT_FIELDS, reason)
    _validate_seal(commit, reason)
    _reject_authority(commit, reason)
    snapshot_reference = _exact(
        document.get("export_snapshot"), OBSERVER_REFERENCE_FIELDS, reason)
    snapshot_schema = snapshot_reference.get("schema")
    _require(snapshot_schema in {
        "hepta.shadow-watch-snapshot.v1",
        "hepta.shadow-watch-snapshot.v2",
    }, reason)
    snapshot_reference = _validate_observer_reference(
        snapshot_reference, str(snapshot_schema), reason)
    lease_reference = _validate_observer_reference(
        document.get("lease_receipt"),
        "hepta.shadow-watch-lease-receipt.v1", reason)
    receipt_reference = _validate_observer_reference(
        document.get("export_receipt"),
        "hepta.shadow-watch-export-receipt.v1", reason)

    sequence = commit.get("commit_sequence")
    generation = commit.get("generation")
    generation_match = (
        EXPORT_GENERATION.fullmatch(generation)
        if isinstance(generation, str) else None)
    _require(
        commit.get("schema") == commit_reference["schema"] and
        commit.get("version") == VERSION and
        commit.get("authority_status") == "ACTIVE" and
        type(commit.get("authority_changed_at_ms")) is int and
        commit["authority_changed_at_ms"] >= 0 and
        commit.get("close_reason") is None and
        type(sequence) is int and 1 <= sequence < (1 << 64) and
        generation_match is not None and
        int(generation_match.group(1)) == sequence and
        commit.get("domain_id") == "alpha" and
        type(commit.get("agent_uid")) is int and commit["agent_uid"] > 0 and
        commit.get("reader_uid") == expected_uid and
        type(commit.get("reader_gid")) is int and commit["reader_gid"] > 0 and
        type(commit.get("lease_generation")) is int and
        commit["lease_generation"] >= 1 and
        type(commit.get("committed_at_ms")) is int and
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

    commit_path = _canonical_path(Path(commit_reference["path"]), reason)
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


def _open_observer_reference(
    value: Any, expected_schema: str, reason: str, *, expected_uid: int,
) -> Snapshot:
    reference = _validate_observer_reference(value, expected_schema, reason)
    snapshot = load_snapshot(
        Path(reference["path"]), "observer_reference",
        expected_uid=expected_uid)
    _require(
        snapshot.file_sha256 == reference["file_sha256"] and
        snapshot.body_sha256 == reference["body_sha256"] and
        snapshot.document.get("schema") == expected_schema, reason)
    return snapshot


def _validate_state_seal(
    value: Any, fields: frozenset[str], reason: str,
) -> dict[str, Any]:
    result = _exact(value, fields, reason)
    body = dict(result)
    claimed = body.pop("state_sha256", None)
    _require(
        type(claimed) is str and DIGEST.fullmatch(claimed) is not None and
        claimed == digest_bytes(canonical_bytes(body)), reason)
    return result


def _validate_observation_unit(value: Any, reason: str) -> dict[str, Any]:
    result = _validate_state_seal(value, OBSERVATION_UNIT_FIELDS, reason)
    _require(
        type(result.get("unit")) is str and bool(result["unit"]) and
        len(result["unit"]) <= 256 and
        all(type(result.get(field)) is str and bool(result[field])
            for field in (
                "load_state", "active_state", "sub_state",
                "unit_file_state")) and
        _integer(result.get("main_pid"), reason) >= 0 and
        type(result.get("invocation_id")) is str and
        (result["invocation_id"] == "" or
         re.fullmatch(r"[0-9a-f]{32}", result["invocation_id"]) is not None) and
        _integer(result.get("exec_main_start_timestamp_monotonic_us"),
                 reason) >= 0 and
        _integer(result.get("n_restarts"), reason) >= 0,
        reason)
    return result


def _validate_observation_process(value: Any, reason: str) -> dict[str, Any]:
    result = _validate_state_seal(value, OBSERVATION_PROCESS_FIELDS, reason)
    _integer(result.get("pid"), reason, 2)
    _integer(result.get("uid"), reason)
    _integer(result.get("gid"), reason)
    _integer(result.get("starttime_ticks"), reason, 1)
    _integer(result.get("exe_device"), reason)
    _integer(result.get("exe_inode"), reason, 1)
    _digest(result.get("cgroup_sha256"), reason)
    return result


def _validate_observation_path(value: Any, reason: str) -> dict[str, Any]:
    result = _validate_state_seal(value, OBSERVATION_PATH_FIELDS, reason)
    raw_path = result.get("path")
    _require(type(raw_path) is str, reason)
    _canonical_path(Path(raw_path), reason)
    _require(type(result.get("present")) is bool, reason)
    for field in (
        "parent_device", "parent_inode", "parent_uid", "parent_gid",
        "parent_mode", "parent_nlink",
    ):
        _integer(result.get(field), reason)
    _require(result["parent_nlink"] >= 1 and
             result["parent_mode"] <= 0o7777, reason)
    file_type = result.get("file_type")
    metadata_fields = (
        "device", "inode", "uid", "gid", "mode", "nlink", "size",
        "mtime_ns", "ctime_ns",
    )
    if result["present"]:
        _require(file_type in {
            "regular", "directory", "socket", "fifo", "other",
        }, reason)
        for field in metadata_fields:
            _integer(result.get(field), reason)
        _require(result["inode"] >= 1 and result["nlink"] >= 1 and
                 result["mode"] <= 0o177777, reason)
    else:
        _require(file_type is None and
                 all(result.get(field) is None for field in metadata_fields),
                 reason)
    file_digest = result.get("content_file_sha256")
    body_digest = result.get("content_body_sha256")
    _require(
        (file_digest is None or
         (type(file_digest) is str and
          DIGEST.fullmatch(file_digest) is not None)) and
        (body_digest is None or
         (type(body_digest) is str and
          DIGEST.fullmatch(body_digest) is not None)) and
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
    raw_path = result.get("helper_path")
    _require(raw_path == "/usr/libexec/hepta-broker-egress-policy", reason)
    _canonical_path(Path(raw_path), reason)
    _digest(result.get("helper_file_sha256"), reason)
    _digest(result.get("policy_sha256"), reason)
    _integer(result.get("authorized_connector_count"), reason)
    authorized = result.get("authorized_uids")
    _require(
        isinstance(authorized, list) and
        all(type(item) is int and item >= 0 for item in authorized) and
        authorized == sorted(set(authorized)), reason)
    _integer(result.get("protected_port_count"), reason)
    _require(type(result.get("deny_all")) is bool, reason)
    _integer(result.get("checked_boottime_ns"), reason)
    return result


def _validate_observation_identity_lists(
    value: Mapping[str, Any], reason: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
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
    return units, processes, paths


def validate_observation_evidence(
    value: Any, *, kind: str, boot_id: str,
    expected_boottime_ns: int | None = None,
    minimum_boottime_ns: int | None = None,
    reason: str,
) -> dict[str, Any]:
    evidence = _exact(value, OBSERVATION_EVIDENCE_FIELDS, reason)
    _validate_seal(evidence, reason)
    _require(
        evidence.get("schema") == OBSERVATION_EVIDENCE_SCHEMA and
        evidence.get("version") == VERSION and
        evidence.get("kind") == kind and
        evidence.get("boot_id") == boot_id,
        reason)
    observed = _integer(evidence.get("observed_boottime_ns"), reason)
    if expected_boottime_ns is not None:
        _require(observed == expected_boottime_ns, reason)
    if minimum_boottime_ns is not None:
        _require(0 <= observed - minimum_boottime_ns <=
                 MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS, reason)
    _validate_observation_identity_lists(evidence, reason)
    broker = _validate_observation_broker(
        evidence.get("broker_deny_all"), reason)
    if broker is not None:
        _require(
            0 <= observed - broker["checked_boottime_ns"] <=
                MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS,
            reason)
    fault_reference = evidence.get("fault_injection_receipt")
    if kind == "FAULT":
        _validate_observer_reference(
            fault_reference, FAULT_INJECTION_SCHEMA, reason)
    else:
        _require(fault_reference is None, reason)
    return evidence


def validate_fault_target_identity(
    value: Any, *, phase: str, target_id: str, boot_id: str,
    fault_type: str, reason: str,
) -> dict[str, Any]:
    identity = _exact(value, FAULT_TARGET_IDENTITY_FIELDS, reason)
    _validate_seal(identity, reason)
    _require(
        identity.get("schema") == FAULT_TARGET_IDENTITY_SCHEMA and
        identity.get("version") == VERSION and
        identity.get("phase") == phase and
        identity.get("target_id") == target_id and
        identity.get("boot_id") == boot_id,
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
    _require(context_complete or (
        context_missing and fault_type in {
            "NETWORK_DENY_RELOAD", "CLOCK_STEP",
        }), reason)
    _validate_observation_identity_lists(identity, reason)
    _validate_observation_broker(identity.get("broker_deny_all"), reason)
    _integer(identity.get("residue_count"), reason)
    wall_delta = identity.get("wall_clock_delta_ms")
    fixture_generation = identity.get("fixture_generation")
    fixture_expiry = identity.get("fixture_expires_boottime_ns")
    fixture_valid = identity.get("fixture_valid")
    fixture_fault = fault_type in {"TOKEN_LOSS", "LEASE_EXPIRY"}
    _require(
        ((fault_type == "CLOCK_STEP" and type(wall_delta) is int) or
         (fault_type != "CLOCK_STEP" and wall_delta is None)) and
        ((fixture_fault and type(fixture_generation) is int and
          fixture_generation >= 0 and type(fixture_expiry) is int and
          fixture_expiry >= 0 and type(fixture_valid) is bool) or
         (not fixture_fault and fixture_generation is None and
          fixture_expiry is None and fixture_valid is None)), reason)
    return identity


FAULT_FIXTURE_PATHS = {
    "TOKEN_LOSS": "/run/hepta-p1-fault-fixture/watch-session-token.json",
    "LEASE_EXPIRY": "/run/hepta-p1-fault-fixture/watch-lease.json",
}


def _require_fault_target_transition(
    pre: Mapping[str, Any], post: Mapping[str, Any], *, fault_type: str,
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
            post["fixture_generation"] is not None,
            reason)
        _require(
            pre["service_epoch"] == post["service_epoch"] and
            pre["fencing_generation"] == post["fencing_generation"] and
            post["lease_generation"] in {
                pre["lease_generation"], pre["lease_generation"] + 1,
            }, reason)
        if fault_type == "LEASE_EXPIRY":
            _require(
                pre["fixture_expires_boottime_ns"] <= actual_ns,
                reason)
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
                len(post["systemd_units"]) == 1,
            reason)
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


def validate_fault_injection_receipt(
    document: dict[str, Any], spec: Mapping[str, Any],
    planned: Mapping[str, Any], sample: ClockSample,
) -> None:
    reason = "P1_RECORDER_FAULT_INJECTION_RECEIPT_INVALID"
    _validate_boundary_document(
        document, FAULT_INJECTION_FIELDS, FAULT_INJECTION_SCHEMA, reason)
    fault_type = planned["fault_type"]
    _require(
        document.get("status") == "COMPLETE" and
        document.get("campaign_id") == spec.get("campaign_id") and
        document.get("source_manifest_sha256") ==
            spec.get("source_manifest_sha256") and
        document.get("policy_sha256") == spec.get("policy_sha256") and
        document.get("strategy_sha256") == spec.get("strategy_sha256") and
        document.get("fault_id") == planned["fault_id"] and
        document.get("fault_type") == fault_type and
        document.get("target_id") == planned["target_id"] and
        document.get("clock_id") == "CLOCK_BOOTTIME" and
        document.get("boot_id") == sample.boot_id and
        document.get("planned_injection_boottime_ns") ==
            planned["inject_at_boottime_ns"] and
        document.get("maximum_recovery_ns") ==
            planned["maximum_recovery_ns"] and
        _identifier(document.get("injector_id"), reason) and
        document.get("injector_uid") == ROOT_UID and
        document.get("injector_gid") == ROOT_GID and
        document.get("injection_scope") == "P1_DECLARED_FAULT_ONLY" and
        document.get("production_mode") == FAULT_INJECTOR_PRODUCTION_MODE and
        _digest(document.get("action_receipt_sha256"), reason) and
        all(type(document.get(field)) is bool for field in (
            "injection_performed", "recovery_complete", "cleanup_complete",
            "authority_failure", "audit_failure", "cleanup_failure")),
        reason)
    producer = _exact(document.get("producer"), FREEZER_PRODUCER_FIELDS,
                      reason)
    _require(producer.get("path") == str(FAULT_INJECTOR_EXECUTABLE), reason)
    _digest(producer.get("file_sha256"), reason)
    _validate_freeze_reference(document.get("pins_reference"), reason)
    _integer(document.get("journal_predecessor_sequence"), reason, 1)
    _digest(document.get("journal_predecessor_body_sha256"), reason)
    _validate_fresh_window(
        document, observed_field="issued_at_ms", now_ms=sample.wall_ms,
        reason=reason, recent=True)
    planned_ns = planned["inject_at_boottime_ns"]
    actual_ns = _integer(
        document.get("actual_injection_boottime_ns"), reason, planned_ns)
    _require(
        actual_ns - planned_ns <= planned["maximum_injection_lateness_ns"],
        reason)
    recovered_ns = _integer(
        document.get("recovered_boottime_ns"), reason, actual_ns)
    _require(
        recovered_ns - actual_ns <= planned["maximum_recovery_ns"] and
        recovered_ns <= sample.boottime_ns +
            MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS,
        reason)
    pre = validate_fault_target_identity(
        document.get("pre_identity"), phase="PRE",
        target_id=planned["target_id"], boot_id=sample.boot_id,
        fault_type=fault_type, reason=reason)
    post = validate_fault_target_identity(
        document.get("post_identity"), phase="POST",
        target_id=planned["target_id"], boot_id=sample.boot_id,
        fault_type=fault_type, reason=reason)
    _require(
        0 <= actual_ns - pre["observed_boottime_ns"] <=
            MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS and
        0 <= post["observed_boottime_ns"] - recovered_ns <=
            MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS and
        post["observed_boottime_ns"] <= sample.boottime_ns +
            MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS,
        reason)
    if pre["fencing_generation"] is not None:
        _require(
            pre["service_epoch"] is not None and
            post["service_epoch"] is not None and
            post["fencing_generation"] >= pre["fencing_generation"] and
            post["fencing_generation"] <= pre["fencing_generation"] + 1 and
            post["lease_generation"] >= pre["lease_generation"] and
            post["lease_generation"] <= pre["lease_generation"] + 1,
            reason)
    _require_fault_target_transition(
        pre, post, fault_type=fault_type, actual_ns=actual_ns,
        recovered_ns=recovered_ns,
        recovery_complete=document["recovery_complete"],
        domain_id=str(spec["domain_id"]), reason=reason)


def _validate_reference(value: Any, reason: str) -> dict[str, Any]:
    reference = _exact(value, REFERENCE_FIELDS, reason)
    _identifier(reference.get("role"), reason)
    path = reference.get("path")
    _require(type(path) is str, reason)
    _canonical_path(Path(path), reason)
    _digest(reference.get("file_sha256"), reason)
    _digest(reference.get("body_sha256"), reason)
    _require(type(reference.get("schema")) is str and
             bool(reference["schema"]) and
             type(reference.get("sealed")) is bool, reason)
    return reference


def _assert_snapshot_unchanged(
    snapshot: Snapshot, *, expected_uid: int,
) -> None:
    current = load_snapshot(
        snapshot.path, snapshot.role, sealed=snapshot.sealed,
        expected_uid=expected_uid)
    _require(
        current.payload == snapshot.payload and
        _file_identity(current.metadata) == _file_identity(snapshot.metadata),
        "P1_RECORDER_INPUT_DRIFT")


def _rename_noreplace(parent: int, source: str, destination: str) -> None:
    function = getattr(_LIBC, "renameat2", None)
    _require(function is not None, "P1_RECORDER_RENAMEAT2_UNAVAILABLE")
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
            raise RecorderError("P1_RECORDER_OUTPUT_ALREADY_EXISTS")
        raise RecorderError("P1_RECORDER_OUTPUT_RENAME_FAILED")


def _atomic_publish(
    document: dict[str, Any], output: Path, *, expected_uid: int,
) -> Snapshot:
    output = _canonical_path(output, "P1_RECORDER_OUTPUT_PATH_INVALID")
    payload = canonical_bytes(document)
    _require(len(payload) <= MAXIMUM_OUTPUT_BYTES,
             "P1_RECORDER_OUTPUT_TOO_LARGE")
    restored, body_sha256 = _strict_document(
        payload, "P1_RECORDER_OUTPUT_INVALID", sealed=True)
    _require(restored == document, "P1_RECORDER_OUTPUT_INVALID")
    parent = _open_directory(output.parent, "P1_RECORDER_OUTPUT_PARENT_INVALID")
    temporary = f".{output.name}.recorder-{secrets.token_hex(16)}.tmp"
    descriptor: int | None = None
    renamed = False
    try:
        parent_metadata = os.fstat(parent)
        _require(parent_metadata.st_uid == expected_uid and
                 stat.S_IMODE(parent_metadata.st_mode) & 0o022 == 0,
                 "P1_RECORDER_OUTPUT_PARENT_UNTRUSTED")
        try:
            os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise RecorderError("P1_RECORDER_OUTPUT_ALREADY_EXISTS")
        descriptor = os.open(temporary, CREATE_FLAGS, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        os.fchown(
            descriptor, expected_uid,
            ROOT_GID if expected_uid == ROOT_UID else os.getegid())
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            _require(count > 0, "P1_RECORDER_OUTPUT_WRITE_FAILED")
            written += count
        os.fsync(descriptor)
        prepared = os.fstat(descriptor)
        _require(
            stat.S_ISREG(prepared.st_mode) and prepared.st_nlink == 1 and
            prepared.st_uid == expected_uid and
            stat.S_IMODE(prepared.st_mode) == 0o600 and
            prepared.st_size == len(payload),
            "P1_RECORDER_OUTPUT_METADATA_INVALID")
        os.fsync(parent)
        _rename_noreplace(parent, temporary, output.name)
        renamed = True
        os.fsync(parent)
    except RecorderError:
        raise
    except OSError as error:
        raise RecorderError("P1_RECORDER_OUTPUT_PUBLISH_FAILED") from error
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
        output, "published_output", expected_uid=expected_uid)
    _require(committed.payload == payload and
             committed.body_sha256 == body_sha256,
             "P1_RECORDER_OUTPUT_POST_VERIFY_FAILED")
    return Snapshot(
        role="published_output", path=output, payload=committed.payload,
        metadata=committed.metadata, document=committed.document,
        file_sha256=committed.file_sha256,
        body_sha256=committed.body_sha256, sealed=True)


def _boundary() -> dict[str, bool]:
    return {field: False for field in BOUNDARY_FIELDS}


def _default_clock() -> ClockSample:
    try:
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="ascii") \
                as stream:
            boot_id = stream.read().strip()
        boottime_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    except (OSError, AttributeError) as error:
        raise RecorderError("P1_RECORDER_CLOCK_UNAVAILABLE") from error
    _require(BOOT_ID.fullmatch(boot_id) is not None,
             "P1_RECORDER_BOOT_ID_INVALID")
    return ClockSample(
        wall_ms=time.time_ns() // 1_000_000,
        boottime_ns=boottime_ns, boot_id=boot_id)


class Recorder:
    """One locked, crash-recoverable recorder rooted at one campaign."""

    def __init__(
        self, root: Path, *, expected_uid: int = ROOT_UID,
        clock: Callable[[], ClockSample] = _default_clock,
    ) -> None:
        self.root = _canonical_path(
            root, "P1_RECORDER_ROOT_PATH_INVALID")
        self.expected_uid = expected_uid
        self.clock = clock
        self._lock_descriptor: int | None = None

    @property
    def spec_path(self) -> Path:
        return self.root / "campaign-spec.json"

    @property
    def fault_plan_path(self) -> Path:
        return self.root / "fault-plan.json"

    @property
    def transactions_path(self) -> Path:
        return self.root / "transactions"

    @property
    def journal_path(self) -> Path:
        return self.root / "journal"

    def _stream_path(self, role: str) -> Path:
        _require(role in STREAMS, "P1_RECORDER_STREAM_ROLE_INVALID")
        return self.root / STREAMS[role][0]

    def _validate_root(self) -> None:
        descriptor = _open_directory(
            self.root, "P1_RECORDER_ROOT_DIRECTORY_INVALID")
        try:
            metadata = os.fstat(descriptor)
            _require(
                stat.S_ISDIR(metadata.st_mode) and
                metadata.st_uid == self.expected_uid and
                stat.S_IMODE(metadata.st_mode) & 0o022 == 0,
                "P1_RECORDER_ROOT_DIRECTORY_UNTRUSTED")
        finally:
            os.close(descriptor)

    def _ensure_directory(self, name: str) -> None:
        _require("/" not in name and name not in {"", ".", ".."},
                 "P1_RECORDER_DIRECTORY_NAME_INVALID")
        parent = _open_directory(
            self.root, "P1_RECORDER_ROOT_DIRECTORY_INVALID")
        child: int | None = None
        try:
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                pass
            child = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
            metadata = os.fstat(child)
            _require(
                stat.S_ISDIR(metadata.st_mode) and
                metadata.st_uid == self.expected_uid and
                stat.S_IMODE(metadata.st_mode) & 0o022 == 0,
                "P1_RECORDER_DIRECTORY_UNTRUSTED")
        except RecorderError:
            raise
        except OSError as error:
            raise RecorderError("P1_RECORDER_DIRECTORY_CREATE_FAILED") \
                from error
        finally:
            if child is not None:
                os.close(child)
            os.close(parent)

    def _prepare_layout(self) -> None:
        self._validate_root()
        for name in (
            "transactions", "journal", "checkpoints", "decisions",
            "fault-results", "authority-snapshots", "cleanup-snapshots",
        ):
            self._ensure_directory(name)

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        self._validate_root()
        parent = _open_directory(
            self.root, "P1_RECORDER_ROOT_DIRECTORY_INVALID")
        descriptor: int | None = None
        try:
            flags = os.O_RDWR | os.O_CREAT | NOFOLLOW | CLOEXEC
            descriptor = os.open(".recorder.lock", flags, 0o600,
                                 dir_fd=parent)
            os.fchmod(descriptor, 0o600)
            os.fchown(
                descriptor, self.expected_uid,
                ROOT_GID if self.expected_uid == ROOT_UID else os.getegid())
            metadata = os.fstat(descriptor)
            _require(
                stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
                metadata.st_uid == self.expected_uid and
                stat.S_IMODE(metadata.st_mode) == 0o600,
                "P1_RECORDER_LOCK_UNTRUSTED")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RecorderError("P1_RECORDER_LOCK_BUSY") from error
            self._lock_descriptor = descriptor
            self._prepare_layout()
            self._recover_pending_locked()
            self._audit_committed_outputs_locked()
            yield
        finally:
            self._lock_descriptor = None
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
            os.close(parent)

    def _require_locked(self) -> None:
        _require(self._lock_descriptor is not None,
                 "P1_RECORDER_LOCK_REQUIRED")

    def _clean_temporary_files(self, directory: Path) -> None:
        self._require_locked()
        descriptor = _open_directory(
            directory, "P1_RECORDER_DIRECTORY_INVALID")
        try:
            for name in os.listdir(descriptor):
                if not (name.startswith(".") and
                        name.endswith(".tmp") and
                        ".recorder-" in name):
                    continue
                metadata = os.stat(
                    name, dir_fd=descriptor, follow_symlinks=False)
                _require(
                    stat.S_ISREG(metadata.st_mode) and
                    metadata.st_uid == self.expected_uid and
                    metadata.st_nlink == 1 and
                    stat.S_IMODE(metadata.st_mode) == 0o600,
                    "P1_RECORDER_TEMPORARY_UNTRUSTED")
                os.unlink(name, dir_fd=descriptor)
                os.fsync(descriptor)
        except RecorderError:
            raise
        except OSError as error:
            raise RecorderError("P1_RECORDER_TEMPORARY_CLEANUP_FAILED") \
                from error
        finally:
            os.close(descriptor)

    def _load_numbered(
        self, directory: Path, role: str, *, first_sequence: int,
    ) -> list[Snapshot]:
        self._clean_temporary_files(directory)
        try:
            names = sorted(
                item.name for item in directory.iterdir()
                if not item.name.startswith("."))
        except OSError as error:
            raise RecorderError("P1_RECORDER_STREAM_SCAN_FAILED") from error
        _require(all(NUMBERED_JSON.fullmatch(name) is not None for name in names),
                 "P1_RECORDER_STREAM_NAME_INVALID")
        expected_names = [
            f"{sequence:08d}.json"
            for sequence in range(first_sequence,
                                  first_sequence + len(names))]
        _require(names == expected_names, "P1_RECORDER_STREAM_GAP")
        return [
            load_snapshot(
                directory / name, role, expected_uid=self.expected_uid)
            for name in names
        ]

    def _load_journal(self) -> list[Snapshot]:
        entries = self._load_numbered(
            self.journal_path, "journal_entry", first_sequence=0)
        previous: str | None = None
        campaign: str | None = None
        seen_transactions: set[str] = set()
        for sequence, snapshot in enumerate(entries):
            document = snapshot.document
            _exact(document, JOURNAL_FIELDS, "P1_RECORDER_JOURNAL_INVALID")
            _validate_seal(document, "P1_RECORDER_JOURNAL_INVALID")
            _reject_authority(document, "P1_RECORDER_JOURNAL_AUTHORITY")
            _require(
                document.get("schema") == JOURNAL_SCHEMA and
                document.get("version") == VERSION and
                document.get("sequence") == sequence and
                document.get("operation") in OPERATIONS and
                document.get("previous_entry_body_sha256") == previous and
                _integer(document.get("recorded_at_ms"),
                         "P1_RECORDER_JOURNAL_INVALID") >= 0,
                "P1_RECORDER_JOURNAL_GAP_OR_DRIFT")
            current_campaign = _identifier(
                document.get("campaign_id"), "P1_RECORDER_JOURNAL_INVALID")
            if campaign is None:
                campaign = current_campaign
            _require(current_campaign == campaign,
                     "P1_RECORDER_JOURNAL_CAMPAIGN_DRIFT")
            transaction = _validate_reference(
                document.get("transaction_reference"),
                "P1_RECORDER_JOURNAL_INVALID")
            _require(transaction["body_sha256"] not in seen_transactions,
                     "P1_RECORDER_DUPLICATE_TRANSACTION_COMMIT")
            seen_transactions.add(transaction["body_sha256"])
            for key in ("inputs", "outputs"):
                values = document.get(key)
                _require(isinstance(values, list),
                         "P1_RECORDER_JOURNAL_INVALID")
                for item in values:
                    _validate_reference(item, "P1_RECORDER_JOURNAL_INVALID")
            previous = snapshot.body_sha256
        return entries

    def _validate_transaction(self, snapshot: Snapshot) -> dict[str, Any]:
        document = snapshot.document
        _exact(document, TRANSACTION_FIELDS,
               "P1_RECORDER_TRANSACTION_INVALID")
        _validate_seal(document, "P1_RECORDER_TRANSACTION_INVALID")
        _reject_authority(document, "P1_RECORDER_TRANSACTION_AUTHORITY")
        _require(
            document.get("schema") == TRANSACTION_SCHEMA and
            document.get("version") == VERSION and
            document.get("operation") in OPERATIONS and
            _integer(document.get("created_at_ms"),
                     "P1_RECORDER_TRANSACTION_INVALID") >= 0,
            "P1_RECORDER_TRANSACTION_INVALID")
        campaign = _identifier(
            document.get("campaign_id"), "P1_RECORDER_TRANSACTION_INVALID")
        transaction_id = _identifier(
            document.get("transaction_id"),
            "P1_RECORDER_TRANSACTION_INVALID")
        _require(snapshot.path.name == f"{transaction_id}.json",
                 "P1_RECORDER_TRANSACTION_NAME_INVALID")
        inputs = document.get("inputs")
        outputs = document.get("outputs")
        _require(isinstance(inputs, list) and isinstance(outputs, list) and
                 bool(outputs), "P1_RECORDER_TRANSACTION_INVALID")
        for item in inputs:
            _validate_reference(item, "P1_RECORDER_TRANSACTION_INVALID")
        output_paths: list[str] = []
        for item in outputs:
            output = _exact(
                item, TRANSACTION_OUTPUT_FIELDS,
                "P1_RECORDER_TRANSACTION_OUTPUT_INVALID")
            role = output.get("role")
            _require(role in ROLE_SCHEMA_FIELDS,
                     "P1_RECORDER_TRANSACTION_OUTPUT_INVALID")
            path = output.get("path")
            _require(type(path) is str, "P1_RECORDER_TRANSACTION_OUTPUT_INVALID")
            resolved = _canonical_path(
                Path(path), "P1_RECORDER_TRANSACTION_OUTPUT_INVALID")
            _require(resolved.is_relative_to(self.root),
                     "P1_RECORDER_TRANSACTION_OUTPUT_OUTSIDE_ROOT")
            _digest(output.get("file_sha256"),
                    "P1_RECORDER_TRANSACTION_OUTPUT_INVALID")
            _digest(output.get("body_sha256"),
                    "P1_RECORDER_TRANSACTION_OUTPUT_INVALID")
            output_document = output.get("document")
            self._validate_output_document(role, output_document)
            payload = canonical_bytes(output_document)
            _require(
                output["file_sha256"] == digest_bytes(payload) and
                output["body_sha256"] == output_document["body_sha256"],
                "P1_RECORDER_TRANSACTION_OUTPUT_DIGEST_INVALID")
            output_paths.append(path)
            if role in OUTPUT_OBSERVER_SCHEMAS:
                observer = _validate_observer_reference(
                    output_document.get("observer_receipt"),
                    OUTPUT_OBSERVER_SCHEMAS[role],
                    "P1_RECORDER_TRANSACTION_OBSERVER_BINDING_INVALID")
                _require(any(
                    reference.get("path") == observer["path"] and
                    reference.get("file_sha256") ==
                        observer["file_sha256"] and
                    reference.get("body_sha256") ==
                        observer["body_sha256"] and
                    reference.get("schema") == observer["schema"] and
                    reference.get("sealed") is True
                    for reference in inputs),
                    "P1_RECORDER_TRANSACTION_OBSERVER_BINDING_INVALID")
        _require(len(output_paths) == len(set(output_paths)),
                 "P1_RECORDER_TRANSACTION_OUTPUT_ALIAS")
        del campaign
        return document

    def _validate_output_document(self, role: str, value: Any) -> None:
        schema, fields = ROLE_SCHEMA_FIELDS[role]
        document = _exact(
            value, fields, "P1_RECORDER_BUSINESS_OUTPUT_INVALID")
        _validate_seal(document, "P1_RECORDER_BUSINESS_OUTPUT_INVALID")
        _reject_authority(document, "P1_RECORDER_BUSINESS_OUTPUT_AUTHORITY")
        _require(document.get("schema") == schema and
                 document.get("version") == VERSION,
                 "P1_RECORDER_BUSINESS_OUTPUT_INVALID")
        if role in OUTPUT_OBSERVER_SCHEMAS:
            _validate_observer_reference(
                document.get("observer_receipt"),
                OUTPUT_OBSERVER_SCHEMAS[role],
                "P1_RECORDER_BUSINESS_OUTPUT_INVALID")

    def _verify_reference(self, value: Mapping[str, Any]) -> Snapshot:
        reference = _validate_reference(
            value, "P1_RECORDER_REFERENCE_INVALID")
        snapshot = load_snapshot(
            Path(reference["path"]), reference["role"],
            sealed=reference["sealed"], expected_uid=self.expected_uid)
        _require(
            snapshot.file_sha256 == reference["file_sha256"] and
            snapshot.body_sha256 == reference["body_sha256"] and
            snapshot.document.get("schema") == reference["schema"],
            "P1_RECORDER_REFERENCE_DRIFT")
        return snapshot

    def _existing_or_publish(
        self, document: dict[str, Any], path: Path, role: str,
    ) -> Snapshot:
        payload = canonical_bytes(document)
        if path.exists():
            snapshot = load_snapshot(
                path, role, expected_uid=self.expected_uid)
            _require(snapshot.payload == payload,
                     "P1_RECORDER_EXISTING_OUTPUT_DRIFT")
            return snapshot
        published = _atomic_publish(
            document, path, expected_uid=self.expected_uid)
        return Snapshot(
            role=role, path=path, payload=published.payload,
            metadata=published.metadata, document=published.document,
            file_sha256=published.file_sha256,
            body_sha256=published.body_sha256, sealed=True)

    def _append_journal(
        self, transaction: Snapshot, transaction_document: dict[str, Any],
    ) -> Snapshot:
        entries = self._load_journal()
        sequence = len(entries)
        previous = entries[-1].body_sha256 if entries else None
        output_refs = [{
            "role": item["role"], "path": item["path"],
            "file_sha256": item["file_sha256"],
            "body_sha256": item["body_sha256"],
            "schema": item["document"]["schema"], "sealed": True,
        } for item in transaction_document["outputs"]]
        entry = seal({
            "schema": JOURNAL_SCHEMA, "version": VERSION,
            "campaign_id": transaction_document["campaign_id"],
            "sequence": sequence,
            "operation": transaction_document["operation"],
            "recorded_at_ms": self.clock().wall_ms,
            "transaction_reference": _reference(transaction),
            "inputs": transaction_document["inputs"],
            "outputs": output_refs,
            "previous_entry_body_sha256": previous,
            **_boundary(),
        })
        return self._existing_or_publish(
            entry, self.journal_path / f"{sequence:08d}.json",
            "journal_entry")

    def _complete_transaction(self, transaction: Snapshot) -> None:
        self._require_locked()
        document = self._validate_transaction(transaction)
        entries = self._load_journal()
        committed = {
            item.document["transaction_reference"]["body_sha256"]
            for item in entries
        }
        if transaction.body_sha256 in committed:
            return
        for reference in document["inputs"]:
            self._verify_reference(reference)
        for item in document["outputs"]:
            snapshot = self._existing_or_publish(
                item["document"], Path(item["path"]), item["role"])
            _require(
                snapshot.file_sha256 == item["file_sha256"] and
                snapshot.body_sha256 == item["body_sha256"],
                "P1_RECORDER_TRANSACTION_OUTPUT_DRIFT")
        for reference in document["inputs"]:
            self._verify_reference(reference)
        self._append_journal(transaction, document)

    def _recover_pending_locked(self) -> None:
        self._require_locked()
        for directory in (
            self.root, self.transactions_path, self.journal_path,
            *(self._stream_path(role) for role in STREAMS),
        ):
            self._clean_temporary_files(directory)
        transactions: list[Snapshot] = []
        try:
            names = sorted(
                item.name for item in self.transactions_path.iterdir()
                if not item.name.startswith("."))
        except OSError as error:
            raise RecorderError("P1_RECORDER_TRANSACTION_SCAN_FAILED") \
                from error
        _require(all(name.startswith("tx-") and name.endswith(".json")
                     for name in names),
                 "P1_RECORDER_TRANSACTION_NAME_INVALID")
        for name in names:
            snapshot = load_snapshot(
                self.transactions_path / name, "transaction",
                expected_uid=self.expected_uid)
            self._validate_transaction(snapshot)
            transactions.append(snapshot)
        transactions.sort(key=lambda item: (
            item.document["created_at_ms"], item.document["transaction_id"]))
        for transaction in transactions:
            self._complete_transaction(transaction)

    def _known_business_outputs(self) -> list[Path]:
        result: list[Path] = []
        for singleton in (self.spec_path, self.fault_plan_path):
            if singleton.exists():
                result.append(singleton)
        for role in STREAMS:
            directory = self._stream_path(role)
            try:
                result.extend(
                    item for item in directory.iterdir()
                    if not item.name.startswith("."))
            except OSError as error:
                raise RecorderError("P1_RECORDER_OUTPUT_SCAN_FAILED") from error
        return sorted(result)

    def _audit_committed_outputs_locked(self) -> None:
        entries = self._load_journal()
        committed: dict[str, dict[str, Any]] = {}
        for entry in entries:
            self._verify_reference(
                entry.document["transaction_reference"])
            for reference in entry.document["inputs"]:
                self._verify_reference(reference)
            for reference in entry.document["outputs"]:
                path = reference["path"]
                _require(path not in committed,
                         "P1_RECORDER_OUTPUT_COMMITTED_TWICE")
                committed[path] = reference
                self._verify_reference(reference)
        observed = {str(path) for path in self._known_business_outputs()}
        _require(observed == set(committed),
                 "P1_RECORDER_ORPHAN_OR_MISSING_OUTPUT")

    def _execute(
        self, *, operation: str, campaign_id: str,
        inputs: Sequence[Snapshot],
        outputs: Sequence[tuple[str, Path, dict[str, Any]]],
        created_at_ms: int,
    ) -> list[Snapshot]:
        self._require_locked()
        _require(operation in OPERATIONS and bool(outputs),
                 "P1_RECORDER_TRANSACTION_BUILD_INVALID")
        for snapshot in inputs:
            _assert_snapshot_unchanged(
                snapshot, expected_uid=self.expected_uid)
        output_values: list[dict[str, Any]] = []
        for role, path, document in outputs:
            self._validate_output_document(role, document)
            payload = canonical_bytes(document)
            output_values.append({
                "role": role, "path": str(path),
                "file_sha256": digest_bytes(payload),
                "body_sha256": document["body_sha256"],
                "document": document,
            })
        input_refs = sorted(
            (_reference(item) for item in inputs),
            key=lambda item: (item["role"], item["path"]))
        fingerprint = {
            "operation": operation, "campaign_id": campaign_id,
            "inputs": input_refs,
            "outputs": [{key: item[key] for key in (
                "role", "path", "file_sha256", "body_sha256")}
                for item in output_values],
        }
        transaction_id = "tx-" + hashlib.sha256(
            canonical_bytes(fingerprint)).hexdigest()
        transaction_document = seal({
            "schema": TRANSACTION_SCHEMA, "version": VERSION,
            "campaign_id": campaign_id, "transaction_id": transaction_id,
            "operation": operation, "created_at_ms": created_at_ms,
            "inputs": input_refs, "outputs": output_values,
            **_boundary(),
        })
        transaction_path = self.transactions_path / f"{transaction_id}.json"
        transaction = self._existing_or_publish(
            transaction_document, transaction_path, "transaction")
        self._complete_transaction(transaction)
        self._audit_committed_outputs_locked()
        return [
            load_snapshot(path, role, expected_uid=self.expected_uid)
            for role, path, _document in outputs
        ]

    def recover(self) -> None:
        """Complete any WAL transaction that lost its journal commit."""

        with self._exclusive():
            pass

    def _sample(self) -> ClockSample:
        sample = self.clock()
        _require(
            isinstance(sample, ClockSample) and
            type(sample.wall_ms) is int and sample.wall_ms >= 0 and
            type(sample.boottime_ns) is int and sample.boottime_ns >= 0 and
            type(sample.boot_id) is str and
            BOOT_ID.fullmatch(sample.boot_id) is not None,
            "P1_RECORDER_CLOCK_SAMPLE_INVALID")
        return sample

    def _load_campaign(self) -> tuple[Snapshot, Snapshot]:
        _require(self.spec_path.exists() and self.fault_plan_path.exists(),
                 "P1_RECORDER_CAMPAIGN_NOT_FROZEN")
        spec = load_snapshot(
            self.spec_path, "campaign_spec", expected_uid=self.expected_uid)
        plan = load_snapshot(
            self.fault_plan_path, "fault_plan", expected_uid=self.expected_uid)
        validate_campaign_spec(spec.document)
        validate_fault_plan(plan.document, spec.document)
        return spec, plan

    def _load_stream(self, role: str, campaign_id: str) -> list[Snapshot]:
        directory_name, first_sequence, previous_field = STREAMS[role]
        del directory_name
        values = self._load_numbered(
            self._stream_path(role), role, first_sequence=first_sequence)
        previous: str | None = None
        for offset, snapshot in enumerate(values):
            document = snapshot.document
            self._validate_output_document(role, document)
            _require(
                document.get("campaign_id") == campaign_id and
                document.get("sequence") == first_sequence + offset and
                document.get(previous_field) == previous,
                "P1_RECORDER_STREAM_GAP_OR_DRIFT")
            previous = snapshot.body_sha256
        return values

    def _idempotent_outputs(
        self, *, operation: str, input_snapshot: Snapshot,
    ) -> list[Snapshot] | None:
        target = _reference(input_snapshot)
        for entry in self._load_journal():
            document = entry.document
            if document["operation"] != operation or target not in \
                    document["inputs"]:
                continue
            return [self._verify_reference(item)
                    for item in document["outputs"]]
        return None

    def freeze(
        self, *, source_anchor_path: Path, policy_anchor_path: Path,
        strategy_anchor_path: Path, formal_policy_paths: Sequence[Path],
        schedule_path: Path, fault_schedule_path: Path,
        freeze_bundle_path: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Freeze one pre-campaign schedule and fault plan atomically."""

        with self._exclusive():
            sample = self._sample()
            snapshots = [
                load_snapshot(
                    source_anchor_path, "source_anchor",
                    expected_uid=self.expected_uid),
                load_snapshot(
                    policy_anchor_path, "policy_anchor",
                    expected_uid=self.expected_uid),
                load_snapshot(
                    strategy_anchor_path, "strategy_anchor",
                    expected_uid=self.expected_uid),
                load_snapshot(
                    schedule_path, "frozen_schedule",
                    expected_uid=self.expected_uid),
                load_snapshot(
                    fault_schedule_path, "frozen_fault_schedule",
                    expected_uid=self.expected_uid),
            ]
            _require(bool(formal_policy_paths),
                     "P1_RECORDER_FORMAL_POLICY_SET_EMPTY")
            formal_snapshots = [
                load_snapshot(
                    path, f"formal_policy_{index}",
                    expected_uid=self.expected_uid)
                for index, path in enumerate(formal_policy_paths)
            ]
            bundle = load_snapshot(
                freeze_bundle_path, "freeze_bundle",
                expected_uid=self.expected_uid)
            calendar_reference = _validate_freeze_reference(
                bundle.document.get("trading_calendar"),
                "P1_RECORDER_FREEZE_BUNDLE_INVALID")
            trading_calendar = load_snapshot(
                Path(calendar_reference["path"]), "trading_calendar",
                expected_uid=self.expected_uid)
            all_inputs = [
                *snapshots, *formal_snapshots, trading_calendar, bundle]
            _require(len({item.path for item in all_inputs}) == len(all_inputs),
                     "P1_RECORDER_FREEZE_INPUT_ALIAS")
            source, policy, strategy, schedule, fault_schedule = snapshots
            bundle_value = validate_freeze_bundle(
                bundle,
                anchors={
                    "source_anchor": source,
                    "policy_anchor": policy,
                    "strategy_anchor": strategy,
                    "frozen_schedule": schedule,
                    "frozen_fault_schedule": fault_schedule,
                },
                formal_policies=formal_snapshots,
                trading_calendar=trading_calendar, sample=sample,
                expected_uid=self.expected_uid)
            validate_source_anchor(source.document, sample.wall_ms)
            validate_policy_anchor(policy.document, sample.wall_ms)
            validate_strategy_anchor(strategy.document, sample.wall_ms)
            schedule_facts = validate_frozen_schedule(
                schedule.document, sample.wall_ms)
            faults = validate_frozen_fault_schedule(
                fault_schedule.document, sample)
            formal = [
                validate_formal_policy(item, sample.wall_ms)
                for item in formal_snapshots
            ]
            formal.sort(key=lambda item: item[1][0])
            campaign_id = schedule.document["campaign_id"]
            _require(fault_schedule.document["campaign_id"] == campaign_id,
                     "P1_RECORDER_FAULT_CAMPAIGN_DRIFT")
            _require(
                bundle_value["campaign_id"] == campaign_id and
                bundle_value["domain_id"] == schedule.document["domain_id"] and
                bundle_value["source_manifest_sha256"] ==
                    source.document["source_manifest_sha256"] and
                bundle_value["policy_sha256"] ==
                    policy.document["policy_sha256"] and
                bundle_value["strategy_id"] == strategy.document["strategy_id"] and
                bundle_value["strategy_version"] ==
                    strategy.document["strategy_version"] and
                bundle_value["strategy_sha256"] ==
                    strategy.document["strategy_sha256"] and
                bundle_value["declared_trading_days"] ==
                    schedule.document["declared_trading_days"] and
                bundle_value["trading_timezone"] ==
                    schedule.document["trading_timezone"] and
                bundle_value["eligible_scheduled_at_ms"] ==
                    schedule.document["eligible_scheduled_at_ms"] and
                bundle_value["planned_faults"] ==
                    fault_schedule.document["planned_faults"],
                "P1_RECORDER_FREEZE_BUNDLE_LINEAGE_DRIFT")
            _require(
                all(item[0]["strategy_id"] ==
                        strategy.document["strategy_id"] and
                    item[0]["strategy_version"] ==
                        strategy.document["strategy_version"] and
                    item[0]["strategy_sha256"] ==
                        strategy.document["strategy_sha256"]
                    for item in formal),
                "P1_RECORDER_FORMAL_STRATEGY_DRIFT")
            campaign_ids = [item[0]["campaign_id"] for item in formal]
            _require(len(campaign_ids) == len(set(campaign_ids)),
                     "P1_RECORDER_FORMAL_CAMPAIGN_DUPLICATE")
            all_slots = [slot for _document, slots, _snapshot in formal
                         for slot in slots]
            _require(
                all_slots == sorted(set(all_slots)) and
                len(all_slots) >= MINIMUM_ELIGIBLE_DECISIONS,
                "P1_RECORDER_FORMAL_SCHEDULE_GAP_OR_OVERLAP")
            eligible = schedule.document["eligible_scheduled_at_ms"]
            _require(set(eligible).issubset(set(all_slots)),
                     "P1_RECORDER_ELIGIBLE_SLOT_NOT_FORMAL")
            _require(sample.wall_ms < min(all_slots),
                     "P1_RECORDER_FREEZE_NOT_PRECAMPAIGN")
            maximum_expiry = max(item[0]["expires_at_ms"] for item in formal)
            for anchor in (source, policy, strategy, schedule, fault_schedule):
                _require(anchor.document["expires_at_ms"] >= maximum_expiry,
                         "P1_RECORDER_FREEZE_INPUT_EXPIRES_TOO_EARLY")
            del schedule_facts
            fault_plan = seal({
                "schema": FAULT_PLAN_SCHEMA, "version": VERSION,
                "campaign_id": campaign_id,
                "source_manifest_sha256":
                    source.document["source_manifest_sha256"],
                "policy_sha256": policy.document["policy_sha256"],
                "strategy_sha256": strategy.document["strategy_sha256"],
                "planned_faults": faults,
                **_boundary(),
            })
            formal_campaigns = [{
                "campaign_id": document["campaign_id"],
                "campaign_sha256": document["campaign_sha256"],
                "policy_body_sha256": snapshot.body_sha256,
                "policy_file_sha256": snapshot.file_sha256,
            } for document, _slots, snapshot in formal]
            trading_calendar_sha256 = bundle_value[
                "trading_calendar_sha256"]
            spec = seal({
                "schema": SPEC_SCHEMA, "version": VERSION,
                "campaign_id": campaign_id,
                "domain_id": schedule.document["domain_id"],
                "source_manifest_sha256":
                    source.document["source_manifest_sha256"],
                "policy_sha256": policy.document["policy_sha256"],
                "strategy_id": strategy.document["strategy_id"],
                "strategy_version": strategy.document["strategy_version"],
                "strategy_sha256": strategy.document["strategy_sha256"],
                "formal_campaigns": formal_campaigns,
                "declared_trading_days":
                    schedule.document["declared_trading_days"],
                "trading_timezone": schedule.document["trading_timezone"],
                "trading_calendar_sha256": trading_calendar_sha256,
                "eligible_scheduled_at_ms": eligible,
                "scheduled_decision_count": len(all_slots),
                "minimum_eligible_decisions":
                    schedule.document["minimum_eligible_decisions"],
                "minimum_complete_ppm":
                    schedule.document["minimum_complete_ppm"],
                "minimum_boottime_duration_ns":
                    schedule.document["minimum_boottime_duration_ns"],
                "maximum_checkpoint_gap_ns":
                    schedule.document["maximum_checkpoint_gap_ns"],
                "maximum_decision_lateness_ms":
                    schedule.document["maximum_decision_lateness_ms"],
                "fault_plan_body_sha256": fault_plan["body_sha256"],
                "independent_auditor_id":
                    schedule.document["independent_auditor_id"],
                "frozen_at_ms": schedule.document["frozen_at_ms"],
                "freeze_bundle": _freeze_reference(bundle),
                **_boundary(),
            })
            validate_campaign_spec(spec)
            validate_fault_plan(fault_plan, spec)
            if self.spec_path.exists() or self.fault_plan_path.exists():
                existing_spec, existing_plan = self._load_campaign()
                _require(
                    existing_spec.payload == canonical_bytes(spec) and
                    existing_plan.payload == canonical_bytes(fault_plan),
                    "P1_RECORDER_CAMPAIGN_ALREADY_FROZEN_DIFFERENTLY")
                return existing_spec.document, existing_plan.document
            results = self._execute(
                operation="FREEZE", campaign_id=campaign_id,
                inputs=all_inputs,
                outputs=(
                    ("fault_plan", self.fault_plan_path, fault_plan),
                    ("campaign_spec", self.spec_path, spec),
                ),
                created_at_ms=sample.wall_ms)
            by_role = {item.role: item.document for item in results}
            return by_role["campaign_spec"], by_role["fault_plan"]

    def checkpoint(self, observation_path: Path) -> dict[str, Any]:
        """Append one continuity checkpoint from an independent observation."""

        with self._exclusive():
            sample = self._sample()
            observation = load_snapshot(
                observation_path, "campaign_continuity_observation",
                expected_uid=self.expected_uid)
            prior_result = self._idempotent_outputs(
                operation="CHECKPOINT", input_snapshot=observation)
            _require(prior_result is None,
                     "P1_RECORDER_CAMPAIGN_CONTINUITY_OBSERVATION_REPLAY")
            spec, plan = self._load_campaign()
            runtime = validate_campaign_continuity_observation(
                observation.document, spec, sample,
                expected_uid=self.expected_uid)
            campaign_id = spec.document["campaign_id"]
            checkpoints = self._load_stream(
                "continuity_checkpoint", campaign_id)
            sequence = len(checkpoints)
            previous = checkpoints[-1] if checkpoints else None
            value = observation.document
            _require(
                value["continuity_slot_index"] == sequence and
                value["continuity_scheduled_at_ms"] == min(
                    value["continuity_origin_ms"] + sequence *
                        value["continuity_cadence_ms"],
                    value["continuity_end_ms"]) and
                value["continuity_is_final"] is
                    (sequence == value["continuity_final_slot"]) and
                value["catch_up"] is False,
                "P1_RECORDER_CHECKPOINT_GRID_GAP_OR_DUPLICATE")
            transition_inputs: list[Snapshot] = []
            if previous is not None:
                prior = previous.document
                _validate_checkpoint_clock_predecessor(
                    prior, value,
                    spec.document["maximum_checkpoint_gap_ns"])
                _require(
                    value["freeze_bundle"] == prior["freeze_bundle"] and
                    value["campaign_runtime"] == prior["campaign_runtime"] and
                    value["activation_receipt"] ==
                        prior["activation_receipt"] and
                    value["activation_receipt_document"] ==
                        prior["activation_receipt_document"] and
                    value["custodian_identity"] ==
                        prior["custodian_identity"] and
                    value["collector_timer_identity"] ==
                        prior["collector_timer_identity"] and
                    value["activation_reconcile_timer_identity"] ==
                        prior["activation_reconcile_timer_identity"] and
                    value["gateway_executable_identity"] ==
                        prior["gateway_executable_identity"] and
                    value["gateway_profile_identity"] ==
                        prior["gateway_profile_identity"] and
                    value["gateway_domain_config_identity"] ==
                        prior["gateway_domain_config_identity"] and
                    value["continuity_origin_ms"] ==
                        prior["continuity_origin_ms"] and
                    value["continuity_end_ms"] ==
                        prior["continuity_end_ms"] and
                    value["continuity_cadence_ms"] ==
                        prior["continuity_cadence_ms"] and
                    value["continuity_final_slot"] ==
                        prior["continuity_final_slot"] and
                    value["continuity_slot_index"] ==
                        prior["continuity_slot_index"] + 1 and
                    prior["continuity_is_final"] is False,
                    "P1_RECORDER_CHECKPOINT_PERSISTENT_IDENTITY_DRIFT")
                lease_unchanged = (
                    value["lease_generation"] == prior["lease_generation"] and
                    all(
                        value["lease_receipt"][field] ==
                            prior["lease_receipt"][field]
                        for field in (
                            "file_sha256", "body_sha256", "schema")) and
                    value["lease_receipt_document"] ==
                        prior["lease_receipt_document"])
                lease_advanced = (
                    value["lease_generation"] ==
                        prior["lease_generation"] + 1 and
                    value["previous_lease_generation"] ==
                        prior["lease_generation"] and
                    value["previous_lease_receipt_body_sha256"] ==
                        prior["lease_receipt"]["body_sha256"])
                _require(
                    value["export_commit_document"]["commit_sequence"] >
                        prior["export_commit_document"]["commit_sequence"] and
                    (lease_unchanged or lease_advanced),
                         "P1_RECORDER_CHECKPOINT_LEASE_CHAIN_GAP")
                changed = any(
                    value[field] != prior[field] for field in (
                        "gateway_identity", "gateway_process_identity",
                        "tool_socket_identity", "supervisor_socket_identity"))
                if changed:
                    transition = value["transition_fault_id"]
                    _require(transition is not None,
                             "P1_RECORDER_CHECKPOINT_TRANSITION_UNBOUND")
                    results = self._load_stream("fault_result", campaign_id)
                    match = next((item for item in results
                                  if item.document["fault_id"] == transition),
                                 None)
                    _require(
                        match is not None and
                        match.document["fault_type"] == "SERVICE_RESTART" and
                        match.document["target_id"] ==
                            "watch-execution-gateway" and
                        match.document["recovery_verified"] is True and
                        match.document["cleanup_verified"] is True and
                        match.document["authority_failure"] is False and
                        match.document["audit_failure"] is False and
                        match.document["cleanup_failure"] is False and
                        prior["observed_boottime_ns"] <=
                            match.document["injection_boottime_ns"] <=
                            match.document["recovered_boottime_ns"] <=
                            value["observed_boottime_ns"] and
                        transition not in {
                            item.document["transition_fault_id"]
                            for item in checkpoints},
                        "P1_RECORDER_CHECKPOINT_TRANSITION_UNVERIFIED")
                    transition_inputs.append(match)
                else:
                    _require(value["transition_fault_id"] is None,
                             "P1_RECORDER_CHECKPOINT_TRANSITION_SPURIOUS")
            else:
                _require(value["transition_fault_id"] is None,
                         "P1_RECORDER_INITIAL_CHECKPOINT_TRANSITION_INVALID")
            checkpoint = seal({
                "schema": CHECKPOINT_SCHEMA, "version": VERSION,
                "campaign_id": campaign_id, "sequence": sequence,
                "clock_id": "CLOCK_BOOTTIME", "boot_id": value["boot_id"],
                "observed_boottime_ns": value["observed_boottime_ns"],
                "freeze_bundle": value["freeze_bundle"],
                "campaign_runtime": value["campaign_runtime"],
                "continuity_slot_index": value["continuity_slot_index"],
                "continuity_scheduled_at_ms":
                    value["continuity_scheduled_at_ms"],
                "continuity_origin_ms": value["continuity_origin_ms"],
                "continuity_end_ms": value["continuity_end_ms"],
                "continuity_cadence_ms": value["continuity_cadence_ms"],
                "continuity_final_slot": value["continuity_final_slot"],
                "continuity_is_final": value["continuity_is_final"],
                "catch_up": value["catch_up"],
                "activation_receipt": value["activation_receipt"],
                "activation_receipt_document":
                    value["activation_receipt_document"],
                "export_commit": value["export_commit"],
                "export_commit_document": value["export_commit_document"],
                "export_snapshot": value["export_snapshot"],
                "lease_receipt": value["lease_receipt"],
                "lease_receipt_document": value["lease_receipt_document"],
                "export_receipt": value["export_receipt"],
                "lease_generation": value["lease_generation"],
                "previous_lease_generation":
                    value["previous_lease_generation"],
                "previous_lease_receipt_body_sha256":
                    value["previous_lease_receipt_body_sha256"],
                "gateway_identity": value["gateway_identity"],
                "gateway_process_identity": value[
                    "gateway_process_identity"],
                "gateway_executable_identity": value[
                    "gateway_executable_identity"],
                "gateway_profile_identity": value[
                    "gateway_profile_identity"],
                "gateway_domain_config_identity": value[
                    "gateway_domain_config_identity"],
                "supervisor_socket_identity": value[
                    "supervisor_socket_identity"],
                "custodian_identity": value["custodian_identity"],
                "collector_timer_identity": value["collector_timer_identity"],
                "activation_reconcile_timer_identity":
                    value["activation_reconcile_timer_identity"],
                "tool_socket_identity": value["tool_socket_identity"],
                "transition_fault_id": value["transition_fault_id"],
                "source_manifest_sha256": spec.document[
                    "source_manifest_sha256"],
                "policy_sha256": spec.document["policy_sha256"],
                "strategy_sha256": spec.document["strategy_sha256"],
                "previous_checkpoint_body_sha256":
                    None if previous is None else previous.body_sha256,
                "observer_receipt": _observer_reference(observation),
                "persistent_stack_ok": value["persistent_stack_ok"],
                "lease_chain_ok": value["lease_chain_ok"],
                "connector_count": value["connector_count"],
                "authorized_uids": value["authorized_uids"],
                "paper_unit_active_count": value["paper_unit_active_count"],
                "campaign_socket_present": value[
                    "campaign_socket_present"],
                "kill_switch_engaged": value["kill_switch_engaged"],
                "zero_exposure": value["zero_exposure"],
                **_boundary(),
            })
            result = self._execute(
                operation="CHECKPOINT", campaign_id=campaign_id,
                inputs=(
                    spec, plan, observation, runtime, *transition_inputs),
                outputs=((
                    "continuity_checkpoint",
                    self._stream_path("continuity_checkpoint") /
                        f"{sequence:08d}.json",
                    checkpoint,
                ),), created_at_ms=sample.wall_ms)
            return result[0].document

    def project_decisions(
        self, verified_closure_path: Path,
        decision_paths: Sequence[Path],
        clock_observation_path: Path,
    ) -> list[dict[str, Any]]:
        """Project verified closure iterations and real decision files.

        Eligibility is derived solely from the immutable exact schedule in the
        campaign spec.  A late/catch-up iteration is rejected; no wrapper can
        change or self-report that eligibility decision.
        """

        with self._exclusive():
            sample = self._sample()
            closure = load_snapshot(
                verified_closure_path, "verified_closure",
                expected_uid=self.expected_uid)
            committed = self._idempotent_outputs(
                operation="PROJECT_DECISIONS", input_snapshot=closure)
            _require(committed is None,
                     "P1_RECORDER_VERIFIED_CLOSURE_REPLAY")
            _require(bool(decision_paths),
                     "P1_RECORDER_DECISION_ARTIFACT_SET_EMPTY")
            decisions = [
                load_snapshot(
                    path, f"actual_decision_{index}", sealed=False,
                    expected_uid=self.expected_uid)
                for index, path in enumerate(decision_paths)
            ]
            _require(
                len({item.path for item in [closure, *decisions]}) ==
                    len(decisions) + 1,
                "P1_RECORDER_DECISION_INPUT_ALIAS")
            spec, plan = self._load_campaign()
            freeze_reference = _validate_freeze_reference(
                spec.document.get("freeze_bundle"),
                "P1_RECORDER_DECISION_FREEZE_BINDING_INVALID")
            freeze_bundle = load_snapshot(
                Path(freeze_reference["path"]), "freeze_bundle",
                expected_uid=self.expected_uid)
            _require(
                _freeze_reference(freeze_bundle) == freeze_reference,
                "P1_RECORDER_DECISION_FREEZE_BINDING_INVALID")
            _validate_boundary_document(
                freeze_bundle.document, FREEZE_BUNDLE_FIELDS,
                FREEZE_BUNDLE_SCHEMA,
                "P1_RECORDER_DECISION_FREEZE_BINDING_INVALID")
            clock_observation = load_snapshot(
                clock_observation_path, "decision_clock_observation",
                expected_uid=self.expected_uid)
            validate_historical_service_observation(
                clock_observation.document, spec.document, sample)
            freeze_boot = freeze_bundle.document.get("boot_id")
            frozen_wall_ms = _integer(
                freeze_bundle.document.get("issued_at_ms"),
                "P1_RECORDER_DECISION_CLOCK_BINDING_INVALID")
            frozen_boottime_ns = _integer(
                freeze_bundle.document.get("frozen_boottime_ns"),
                "P1_RECORDER_DECISION_CLOCK_BINDING_INVALID")
            anchor_wall_ms = clock_observation.document["observed_at_ms"]
            anchor_boottime_ns = clock_observation.document[
                "observed_boottime_ns"]
            _require(
                freeze_boot == sample.boot_id ==
                    clock_observation.document["boot_id"] and
                anchor_wall_ms >= frozen_wall_ms and
                anchor_boottime_ns >= frozen_boottime_ns and
                abs((anchor_wall_ms - frozen_wall_ms) * 1_000_000 -
                    (anchor_boottime_ns - frozen_boottime_ns)) <=
                    CLOCK_CORRELATION_TOLERANCE_NS,
                "P1_RECORDER_DECISION_CLOCK_BINDING_INVALID")
            iterations = validate_verified_closure(
                closure, spec.document)
            formal_references = [
                item for item in freeze_bundle.document["formal_policies"]
                if item.get("campaign_id") == closure.document["campaign_id"]
            ]
            _require(len(formal_references) == 1,
                     "P1_RECORDER_DECISION_CLOCK_BINDING_INVALID")
            formal_reference = _exact(
                formal_references[0], FREEZE_FORMAL_REFERENCE_FIELDS,
                "P1_RECORDER_DECISION_CLOCK_BINDING_INVALID")
            formal_expiry_ms = _integer(
                formal_reference.get("expires_at_ms"),
                "P1_RECORDER_DECISION_CLOCK_BINDING_INVALID")
            _require(
                sample.wall_ms <= formal_expiry_ms +
                    POST_FORMAL_PROJECTION_GUARD_MS,
                "P1_RECORDER_DECISION_PROJECTION_DEADLINE_EXCEEDED")
            _require(
                anchor_wall_ms >= iterations[-1]["evaluated_at_ms"] and
                anchor_wall_ms - iterations[-1]["evaluated_at_ms"] <=
                    spec.document["maximum_checkpoint_gap_ns"] // 1_000_000 and
                anchor_wall_ms <= formal_expiry_ms,
                "P1_RECORDER_DECISION_CLOCK_BINDING_INVALID")
            _require(len(decisions) == len(iterations),
                     "P1_RECORDER_DECISION_ARTIFACT_SET_GAP")
            by_digest: dict[str, Snapshot] = {}
            for decision in decisions:
                validate_actual_decision(
                    decision.document, spec.document,
                    closure.document["campaign_id"])
                _require(decision.file_sha256 not in by_digest,
                         "P1_RECORDER_DECISION_ARTIFACT_DUPLICATE")
                by_digest[decision.file_sha256] = decision
            required_digests = {
                item["decision_receipt_file_sha256"] for item in iterations}
            _require(set(by_digest) == required_digests,
                     "P1_RECORDER_DECISION_ARTIFACT_BINDING_GAP")
            campaign_id = spec.document["campaign_id"]
            existing = self._load_stream("decision_receipt", campaign_id)
            formal_order = [
                item["campaign_id"] for item in spec.document["formal_campaigns"]]
            projected_order: list[str] = []
            for item in existing:
                formal_id = item.document["formal_campaign_id"]
                if not projected_order or projected_order[-1] != formal_id:
                    projected_order.append(formal_id)
            _require(
                projected_order == formal_order[:len(projected_order)] and
                len(projected_order) < len(formal_order) and
                closure.document["campaign_id"] ==
                    formal_order[len(projected_order)],
                "P1_RECORDER_DECISION_FORMAL_CAMPAIGN_GAP")
            if existing:
                _require(
                    iterations[0]["scheduled_at_ms"] >
                        existing[-1].document["scheduled_at_ms"],
                    "P1_RECORDER_DECISION_SCHEDULE_REGRESSION")
            schedule = frozenset(spec.document["eligible_scheduled_at_ms"])
            timezone_value = ZoneInfo(spec.document["trading_timezone"])
            sequence = len(existing) + 1
            previous = existing[-1].body_sha256 if existing else None
            outputs: list[tuple[str, Path, dict[str, Any]]] = []
            ordered_inputs: list[Snapshot] = [spec, plan, closure]
            ordered_inputs.extend((freeze_bundle, clock_observation))
            for offset, iteration in enumerate(iterations):
                scheduled = iteration["scheduled_at_ms"]
                evaluated = iteration["evaluated_at_ms"]
                lateness = evaluated - scheduled
                _require(
                    0 <= lateness <=
                        spec.document["maximum_decision_lateness_ms"],
                    "P1_RECORDER_CATCH_UP_FORBIDDEN")
                scheduled_boottime_ns = frozen_boottime_ns + \
                    (scheduled - frozen_wall_ms) * 1_000_000
                evaluated_boottime_ns = frozen_boottime_ns + \
                    (evaluated - frozen_wall_ms) * 1_000_000
                _require(
                    frozen_boottime_ns <= scheduled_boottime_ns <=
                        evaluated_boottime_ns <= anchor_boottime_ns and
                    evaluated <= anchor_wall_ms,
                    "P1_RECORDER_DECISION_CLOCK_BINDING_INVALID")
                actual = by_digest[
                    iteration["decision_receipt_file_sha256"]]
                value = actual.document
                expected_outcome = (
                    "NO_TRADE"
                    if iteration["final_outcome"] == "NO_TRADE" else
                    "TRADE_CANDIDATE"
                    if iteration["final_outcome"] in {
                        "SHADOW_TRADE", "TRADE_CANDIDATE"} else None)
                _require(expected_outcome is not None and
                         value["final_outcome"] ==
                            iteration["final_outcome"],
                         "P1_RECORDER_DECISION_OUTCOME_DRIFT")
                _require(value["finished_at_ms"] <= evaluated,
                         "P1_RECORDER_DECISION_TIME_DRIFT")
                trading_day = datetime.fromtimestamp(
                    scheduled / 1000, tz=timezone.utc
                ).astimezone(timezone_value).date().isoformat()
                evidence_sha256 = digest_bytes(canonical_bytes({
                    "verified_closure_body_sha256": closure.body_sha256,
                    "closure_iteration": iteration["iteration"],
                    "decision_artifact_file_sha256": actual.file_sha256,
                    "scheduled_at_ms": scheduled,
                    "evaluated_at_ms": evaluated,
                    "clock_id": "CLOCK_BOOTTIME", "boot_id": freeze_boot,
                    "scheduled_boottime_ns": scheduled_boottime_ns,
                    "evaluated_boottime_ns": evaluated_boottime_ns,
                    "clock_observer_receipt":
                        _observer_reference(clock_observation),
                    "final_outcome": iteration["final_outcome"],
                }))
                wrapper = seal({
                    "schema": DECISION_SCHEMA, "version": VERSION,
                    "campaign_id": campaign_id,
                    "sequence": sequence + offset,
                    "decision_id": value["decision_id"],
                    "formal_campaign_id": closure.document["campaign_id"],
                    "verified_closure_body_sha256": closure.body_sha256,
                    "closure_iteration": iteration["iteration"],
                    "trading_day": trading_day,
                    "scheduled_at_ms": scheduled,
                    "evaluated_at_ms": evaluated,
                    "clock_id": "CLOCK_BOOTTIME", "boot_id": freeze_boot,
                    "scheduled_boottime_ns": scheduled_boottime_ns,
                    "evaluated_boottime_ns": evaluated_boottime_ns,
                    "clock_observer_receipt":
                        _observer_reference(clock_observation),
                    "eligible": scheduled in schedule,
                    "complete": True,
                    "catch_up": False,
                    "outcome": expected_outcome,
                    "source_manifest_sha256":
                        spec.document["source_manifest_sha256"],
                    "policy_sha256": spec.document["policy_sha256"],
                    "strategy_sha256": spec.document["strategy_sha256"],
                    "decision_artifact_file_sha256": actual.file_sha256,
                    "evidence_sha256": evidence_sha256,
                    "previous_receipt_body_sha256": previous,
                    "audit_failure": False, "cleanup_failure": False,
                    **_boundary(),
                })
                path = self._stream_path("decision_receipt") / \
                    f"{sequence + offset:08d}.json"
                outputs.append(("decision_receipt", path, wrapper))
                ordered_inputs.append(actual)
                previous = wrapper["body_sha256"]
            results = self._execute(
                operation="PROJECT_DECISIONS", campaign_id=campaign_id,
                inputs=ordered_inputs, outputs=outputs,
                created_at_ms=sample.wall_ms)
            return [item.document for item in results]

    def record_fault(self, observation_path: Path) -> dict[str, Any]:
        """Record one independently observed predeclared fault result."""

        with self._exclusive():
            sample = self._sample()
            observation = load_snapshot(
                observation_path, "fault_observation",
                expected_uid=self.expected_uid)
            prior = self._idempotent_outputs(
                operation="RECORD_FAULT", input_snapshot=observation)
            _require(prior is None,
                     "P1_RECORDER_FAULT_OBSERVATION_REPLAY")
            spec, plan = self._load_campaign()
            validate_fault_observation(
                observation.document, spec.document, sample)
            campaign_id = spec.document["campaign_id"]
            results = self._load_stream("fault_result", campaign_id)
            sequence = len(results) + 1
            planned = plan.document["planned_faults"]
            _require(sequence <= len(planned),
                     "P1_RECORDER_UNPLANNED_FAULT")
            expected = planned[sequence - 1]
            value = observation.document
            _require(
                value["fault_id"] == expected["fault_id"] and
                value["fault_type"] == expected["fault_type"] and
                value["target_id"] == expected["target_id"] and
                value["injection_boottime_ns"] ==
                    expected["inject_at_boottime_ns"],
                "P1_RECORDER_FAULT_PLAN_DRIFT")
            injection_reference = _validate_observer_reference(
                value["observation_evidence"]["fault_injection_receipt"],
                FAULT_INJECTION_SCHEMA,
                "P1_RECORDER_FAULT_INJECTION_REFERENCE_INVALID")
            injection_receipt = load_snapshot(
                Path(injection_reference["path"]),
                "fault_injection_receipt", expected_uid=self.expected_uid)
            _require(
                injection_receipt.file_sha256 ==
                    injection_reference["file_sha256"] and
                injection_receipt.body_sha256 ==
                    injection_reference["body_sha256"] and
                injection_receipt.document.get("schema") ==
                    injection_reference["schema"],
                "P1_RECORDER_FAULT_INJECTION_REFERENCE_DRIFT")
            validate_fault_injection_receipt(
                injection_receipt.document, spec.document, expected, sample)
            companion = injection_receipt.document
            _require(
                value["recovered_boottime_ns"] ==
                    companion["recovered_boottime_ns"] and
                value["recovery_verified"] is (
                    companion["injection_performed"] and
                    companion["recovery_complete"]) and
                value["cleanup_verified"] is companion["cleanup_complete"] and
                value["authority_failure"] is
                    companion["authority_failure"] and
                value["audit_failure"] is companion["audit_failure"] and
                value["cleanup_failure"] is companion["cleanup_failure"],
                "P1_RECORDER_FAULT_INJECTION_PROJECTION_DRIFT")
            previous = results[-1].body_sha256 if results else None
            result = seal({
                "schema": FAULT_RESULT_SCHEMA, "version": VERSION,
                "campaign_id": campaign_id, "sequence": sequence,
                "fault_id": value["fault_id"],
                "fault_type": value["fault_type"],
                "target_id": value["target_id"],
                "injection_boottime_ns": value["injection_boottime_ns"],
                "recovered_boottime_ns": value["recovered_boottime_ns"],
                "recovery_verified": value["recovery_verified"],
                "cleanup_verified": value["cleanup_verified"],
                "evidence_sha256": digest_bytes(canonical_bytes(
                    _reference(observation))),
                "observer_receipt": _observer_reference(observation),
                "previous_result_body_sha256": previous,
                "authority_failure": value["authority_failure"],
                "audit_failure": value["audit_failure"],
                "cleanup_failure": value["cleanup_failure"],
                **_boundary(),
            })
            output = self._stream_path("fault_result") / \
                f"{sequence:08d}.json"
            produced = self._execute(
                operation="RECORD_FAULT", campaign_id=campaign_id,
                inputs=(spec, plan, observation, injection_receipt),
                outputs=(("fault_result", output, result),),
                created_at_ms=sample.wall_ms)
            return produced[0].document

    def record_authority(self, observation_path: Path) -> dict[str, Any]:
        """Copy one independent broker/authority observation; never infer it."""

        with self._exclusive():
            sample = self._sample()
            observation = load_snapshot(
                observation_path, "authority_observation",
                expected_uid=self.expected_uid)
            prior = self._idempotent_outputs(
                operation="RECORD_AUTHORITY", input_snapshot=observation)
            _require(prior is None,
                     "P1_RECORDER_AUTHORITY_OBSERVATION_REPLAY")
            spec, plan = self._load_campaign()
            validate_authority_observation(
                observation.document, spec.document, sample)
            campaign_id = spec.document["campaign_id"]
            values = self._load_stream("authority_snapshot", campaign_id)
            sequence = len(values)
            previous = values[-1] if values else None
            value = observation.document
            if previous is not None:
                gap = value["observed_boottime_ns"] - \
                    previous.document["observed_boottime_ns"]
                _require(
                    0 < gap <= spec.document["maximum_checkpoint_gap_ns"],
                    "P1_RECORDER_AUTHORITY_OBSERVATION_GAP")
                _require(value["boot_id"] == previous.document["boot_id"],
                         "P1_RECORDER_AUTHORITY_BOOT_DRIFT")
            result = seal({
                "schema": AUTHORITY_SCHEMA, "version": VERSION,
                "campaign_id": campaign_id, "sequence": sequence,
                "clock_id": "CLOCK_BOOTTIME", "boot_id": value["boot_id"],
                "observed_boottime_ns": value["observed_boottime_ns"],
                "source_manifest_sha256":
                    spec.document["source_manifest_sha256"],
                "policy_sha256": spec.document["policy_sha256"],
                "strategy_sha256": spec.document["strategy_sha256"],
                "connector_count": value["connector_count"],
                "authorized_uids": value["authorized_uids"],
                "paper_unit_active_count":
                    value["paper_unit_active_count"],
                "campaign_socket_present":
                    value["campaign_socket_present"],
                "kill_switch_engaged": value["kill_switch_engaged"],
                "local_boundary_safe": value["local_boundary_safe"],
                "local_boundary_uncertain":
                    value["local_boundary_uncertain"],
                "observation_scope": value["observation_scope"],
                "authoritative_account_state_observed":
                    value["authoritative_account_state_observed"],
                "observer_receipt": _observer_reference(observation),
                "previous_snapshot_body_sha256":
                    None if previous is None else previous.body_sha256,
                **_boundary(),
            })
            output = self._stream_path("authority_snapshot") / \
                f"{sequence:08d}.json"
            produced = self._execute(
                operation="RECORD_AUTHORITY", campaign_id=campaign_id,
                inputs=(spec, plan, observation),
                outputs=(("authority_snapshot", output, result),),
                created_at_ms=sample.wall_ms)
            return produced[0].document

    def record_cleanup(self, observation_path: Path) -> dict[str, Any]:
        """Record one independent cleanup observation exactly once."""

        with self._exclusive():
            sample = self._sample()
            observation = load_snapshot(
                observation_path, "cleanup_observation",
                expected_uid=self.expected_uid)
            prior = self._idempotent_outputs(
                operation="RECORD_CLEANUP", input_snapshot=observation)
            _require(prior is None,
                     "P1_RECORDER_CLEANUP_OBSERVATION_REPLAY")
            spec, plan = self._load_campaign()
            validate_cleanup_observation(
                observation.document, spec.document, sample)
            campaign_id = spec.document["campaign_id"]
            values = self._load_stream("cleanup_snapshot", campaign_id)
            sequence = len(values)
            previous = values[-1] if values else None
            value = observation.document
            subjects = {
                (item.document["subject_type"], item.document["subject_id"])
                for item in values
            }
            _require((value["subject_type"], value["subject_id"])
                     not in subjects,
                     "P1_RECORDER_CLEANUP_SUBJECT_DUPLICATE")
            if previous is not None:
                _require(
                    value["boot_id"] == previous.document["boot_id"] and
                    value["observed_boottime_ns"] >=
                        previous.document["observed_boottime_ns"],
                    "P1_RECORDER_CLEANUP_TIME_OR_BOOT_DRIFT")
            result = seal({
                "schema": CLEANUP_SCHEMA, "version": VERSION,
                "campaign_id": campaign_id, "sequence": sequence,
                "clock_id": "CLOCK_BOOTTIME", "boot_id": value["boot_id"],
                "observed_boottime_ns": value["observed_boottime_ns"],
                "subject_type": value["subject_type"],
                "subject_id": value["subject_id"],
                "watch_authority_count": value["watch_authority_count"],
                "export_residue_count": value["export_residue_count"],
                "session_authority_count": value["session_authority_count"],
                "paper_unit_active_count": value["paper_unit_active_count"],
                "campaign_socket_present": value["campaign_socket_present"],
                "cleanup_complete": value["cleanup_complete"],
                "cleanup_uncertain": value["cleanup_uncertain"],
                "errors": value["errors"],
                "observer_receipt": _observer_reference(observation),
                "previous_snapshot_body_sha256":
                    None if previous is None else previous.body_sha256,
                **_boundary(),
            })
            output = self._stream_path("cleanup_snapshot") / \
                f"{sequence:08d}.json"
            produced = self._execute(
                operation="RECORD_CLEANUP", campaign_id=campaign_id,
                inputs=(spec, plan, observation),
                outputs=(("cleanup_snapshot", output, result),),
                created_at_ms=sample.wall_ms)
            return produced[0].document


def _validate_boundary_document(
    document: dict[str, Any], fields: frozenset[str], schema: str,
    reason: str,
) -> None:
    _exact(document, fields, reason)
    _validate_seal(document, reason)
    _reject_authority(document, reason)
    _require(document.get("schema") == schema and
             document.get("version") == VERSION, reason)


def _validate_fresh_window(
    document: Mapping[str, Any], *, observed_field: str, now_ms: int,
    reason: str, recent: bool,
) -> tuple[int, int]:
    observed = _integer(document.get(observed_field), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    _require(observed < expires and
             observed <= now_ms + MAXIMUM_CLOCK_SKEW_MS and
             now_ms < expires, reason)
    if recent:
        _require(now_ms - observed <= MAXIMUM_OBSERVER_AGE_MS, reason)
    return observed, expires


def _freeze_reference(snapshot: Snapshot) -> dict[str, str]:
    return {
        "path": str(snapshot.path),
        "file_sha256": snapshot.file_sha256,
        "body_sha256": snapshot.body_sha256,
    }


def _validate_checkpoint_clock_predecessor(
    previous: Mapping[str, Any], current: Mapping[str, Any],
    maximum_gap_ns: int,
) -> None:
    """Retain the inner boot/gap fence beneath runtime and journal checks."""

    gap = current["observed_boottime_ns"] - previous["observed_boottime_ns"]
    _require(0 < gap <= maximum_gap_ns, "P1_RECORDER_CHECKPOINT_GAP")
    _require(current["boot_id"] == previous["boot_id"],
             "P1_RECORDER_CHECKPOINT_BOOT_DRIFT")


def _validate_freeze_reference(value: Any, reason: str) -> dict[str, str]:
    result = _exact(value, FREEZE_REFERENCE_FIELDS, reason)
    path = result.get("path")
    _require(type(path) is str, reason)
    _canonical_path(Path(path), reason)
    _digest(result.get("file_sha256"), reason)
    _digest(result.get("body_sha256"), reason)
    return result


def _validate_freezer_producer(
    value: Any, reason: str, *, expected_uid: int | None,
) -> dict[str, str]:
    producer = _exact(value, FREEZER_PRODUCER_FIELDS, reason)
    _require(producer.get("path") == str(FREEZER_EXECUTABLE), reason)
    claimed = _digest(producer.get("file_sha256"), reason)
    _require(claimed != "sha256:" + "0" * 64, reason)
    # Non-root expected_uid exists only for hermetic contract tests.  A
    # production recorder always uses ROOT_UID and must bind the actual fixed
    # installed producer image, not merely a claimed digest.
    if expected_uid == ROOT_UID:
        payload, metadata = secure_read(
            FREEZER_EXECUTABLE, expected_uid=ROOT_UID,
            modes=frozenset({0o755}))
        _require(metadata.st_gid == ROOT_GID and
                 digest_bytes(payload) == claimed, reason)
    return producer


def _validate_anchor_freezer(
    document: Mapping[str, Any], reason: str, *, expected_uid: int | None,
) -> tuple[str, dict[str, str]]:
    freeze_id = document.get("freeze_id")
    _require(type(freeze_id) is str and
             re.fullmatch(r"[0-9a-f]{32}", freeze_id) is not None and
             document.get("production_mode") == FREEZER_PRODUCTION_MODE,
             reason)
    producer = _validate_freezer_producer(
        document.get("producer"), reason, expected_uid=expected_uid)
    return freeze_id, producer


def _calendar_source_sha256() -> str:
    source_contract = {
        "schema": "hepta.p1-safety-soak-calendar-rule.v1",
        "calendar_id": CALENDAR_ID, "calendar_version": CALENDAR_VERSION,
        "instrument": "EURUSD", "year": 2026,
        "trading_timezone": CALENDAR_TIMEZONE,
        "core_open_local": "09:00", "core_close_local": "16:00",
        "maintenance_open_local": "12:00",
        "maintenance_close_local": "12:15",
        "excluded_days": sorted(CALENDAR_EXCLUDED_DAYS_2026),
    }
    return digest_bytes(canonical_bytes(source_contract))


def _expected_calendar_sessions(
    all_slots: Sequence[int], reason: str,
) -> list[dict[str, Any]]:
    try:
        zone = ZoneInfo(CALENDAR_TIMEZONE)
        candidate_days = sorted({
            datetime.fromtimestamp(slot / 1000, tz=timezone.utc)
            .astimezone(zone).date() for slot in all_slots
        })
    except (OSError, OverflowError, ValueError,
            ZoneInfoNotFoundError) as error:
        raise RecorderError(reason) from error
    days = [item for item in candidate_days
            if item.year == 2026 and item.weekday() < 5 and
            item.isoformat() not in CALENDAR_EXCLUDED_DAYS_2026]
    _require(MINIMUM_TRADING_DAYS <= len(days) <= MAXIMUM_TRADING_DAYS,
             reason)
    sessions: list[dict[str, Any]] = []
    for day in days:
        opens = datetime(day.year, day.month, day.day, 9, 0, tzinfo=zone)
        closes = datetime(day.year, day.month, day.day, 16, 0, tzinfo=zone)
        maintenance_start = datetime(
            day.year, day.month, day.day, 12, 0, tzinfo=zone)
        maintenance_end = datetime(
            day.year, day.month, day.day, 12, 15, tzinfo=zone)
        sessions.append({
            "trading_day": day.isoformat(),
            "opens_at_ms": int(opens.timestamp() * 1000),
            "closes_at_ms": int(closes.timestamp() * 1000),
            "maintenance_windows": [{
                "opens_at_ms": int(maintenance_start.timestamp() * 1000),
                "closes_at_ms": int(maintenance_end.timestamp() * 1000),
            }],
        })
    return sessions


def _validate_calendar(
    snapshot: Snapshot, *, bundle: Mapping[str, Any],
    formal_policies: Sequence[Snapshot], producer: Mapping[str, str],
    reason: str,
) -> None:
    value = snapshot.document
    _validate_boundary_document(value, CALENDAR_FIELDS, CALENDAR_SCHEMA, reason)
    _require(
        _freeze_reference(snapshot) == bundle.get("trading_calendar") and
        value.get("status") == "FROZEN" and
        value.get("freeze_id") == bundle.get("freeze_id") and
        value.get("producer") == producer and
        value.get("production_mode") == FREEZER_PRODUCTION_MODE and
        value.get("calendar_id") == CALENDAR_ID ==
            bundle.get("calendar_id") and
        value.get("calendar_version") == CALENDAR_VERSION ==
            bundle.get("calendar_version") and
        value.get("calendar_source_sha256") == _calendar_source_sha256() ==
            bundle.get("calendar_source_sha256") and
        value.get("trading_timezone") == CALENDAR_TIMEZONE ==
            bundle.get("trading_timezone") and
        value.get("issued_at_ms") == bundle.get("issued_at_ms") and
        value.get("expires_at_ms") == bundle.get("expires_at_ms") and
        snapshot.body_sha256 == bundle.get("trading_calendar_sha256"),
        reason)
    all_slots: list[int] = []
    for snapshot_item in formal_policies:
        document = snapshot_item.document
        valid_after = _integer(document.get("valid_after_ms"), reason)
        interval = _integer(document.get("slot_interval_ms"), reason, 1)
        maximum = _integer(document.get("maximum_iterations"), reason, 1)
        all_slots.extend(valid_after + offset * interval
                         for offset in range(maximum))
    _require(all_slots == sorted(set(all_slots)) and
             bundle.get("scheduled_decision_count") == len(all_slots), reason)
    expected_sessions = _expected_calendar_sessions(all_slots, reason)
    sessions = value.get("sessions")
    _require(sessions == expected_sessions, reason)
    for session in sessions:
        _exact(session, CALENDAR_SESSION_FIELDS, reason)
        windows = session.get("maintenance_windows")
        _require(isinstance(windows, list) and len(windows) == 1, reason)
        for window in windows:
            _exact(window, CALENDAR_WINDOW_FIELDS, reason)
    eligible: list[int] = []
    observed_days: set[str] = set()
    for slot in all_slots:
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
    _require(observed_days == set(days) and
             days == bundle.get("declared_trading_days") and
             eligible == bundle.get("eligible_scheduled_at_ms") and
             len(eligible) >= MINIMUM_ELIGIBLE_DECISIONS, reason)


def validate_freeze_bundle(
    bundle: Snapshot, *, anchors: Mapping[str, Snapshot],
    formal_policies: Sequence[Snapshot], trading_calendar: Snapshot,
    sample: ClockSample,
    expected_uid: int,
) -> dict[str, Any]:
    reason = "P1_RECORDER_FREEZE_BUNDLE_INVALID"
    value = bundle.document
    _validate_boundary_document(
        value, FREEZE_BUNDLE_FIELDS, FREEZE_BUNDLE_SCHEMA, reason)
    _require(
        value.get("status") == "FROZEN" and value.get("round") == 114 and
        value.get("production_mode") == FREEZER_PRODUCTION_MODE and
        type(value.get("freeze_id")) is str and
        re.fullmatch(r"[0-9a-f]{32}", value["freeze_id"]) is not None and
        type(value.get("boot_id")) is str and
        BOOT_ID.fullmatch(value["boot_id"]) is not None and
        value["boot_id"] == sample.boot_id,
        reason)
    producer = _validate_freezer_producer(
        value.get("producer"), reason, expected_uid=expected_uid)
    issued = _integer(value.get("issued_at_ms"), reason)
    expires = _integer(value.get("expires_at_ms"), reason, issued + 1)
    _require(
        issued <= sample.wall_ms + MAXIMUM_CLOCK_SKEW_MS and
        sample.wall_ms < expires and
        sample.wall_ms - issued <= MAXIMUM_OBSERVER_AGE_MS and
        type(value.get("frozen_boottime_ns")) is int and
        0 <= sample.boottime_ns - value["frozen_boottime_ns"] <=
            MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS,
        reason)
    _identifier(value.get("campaign_id"), reason)
    _identifier(value.get("domain_id"), reason)
    _identifier(value.get("strategy_id"), reason)
    _identifier(value.get("strategy_version"), reason)
    _identifier(value.get("calendar_id"), reason)
    _identifier(value.get("calendar_version"), reason)
    for field in (
        "source_manifest_sha256", "policy_sha256", "strategy_sha256",
        "trading_calendar_sha256", "calendar_source_sha256",
    ):
        _digest(value.get(field), reason)
    _validate_freeze_reference(value.get("source_baseline"), reason)
    _validate_freeze_reference(value.get("trading_calendar"), reason)
    producer_pins = value.get("source_producer_pins")
    _require(isinstance(producer_pins, list) and
             len(producer_pins) == len(FREEZE_SOURCE_PRODUCER_PATHS), reason)
    seen_roles: set[str] = set()
    for item in producer_pins:
        _exact(item, FREEZE_SOURCE_PRODUCER_PIN_FIELDS, reason)
        role = item.get("role")
        _require(type(role) is str and
                 role in FREEZE_SOURCE_PRODUCER_PATHS and
                 role not in seen_roles, reason)
        seen_roles.add(role)
        source_path, installed_path = FREEZE_SOURCE_PRODUCER_PATHS[role]
        _require(item.get("source_path") == source_path and
                 item.get("installed_path") == installed_path, reason)
        _digest(item.get("file_sha256"), reason)
    _require(seen_roles == set(FREEZE_SOURCE_PRODUCER_PATHS) and
             producer_pins == sorted(
                 producer_pins, key=lambda item: item["role"]), reason)
    freezer_pin = next(item for item in producer_pins
                       if item["role"] == "campaign_freezer")
    _require(freezer_pin["file_sha256"] == producer["file_sha256"], reason)
    anchor_values = value.get("anchors")
    _require(isinstance(anchor_values, dict) and
             set(anchor_values) == set(anchors), reason)
    for role, snapshot in anchors.items():
        _require(_validate_freeze_reference(anchor_values[role], reason) ==
                 _freeze_reference(snapshot), reason)
        anchor_freeze, anchor_producer = _validate_anchor_freezer(
            snapshot.document, reason, expected_uid=expected_uid)
        _require(anchor_freeze == value["freeze_id"] and
                 anchor_producer == producer, reason)
    formal = value.get("formal_policies")
    _require(isinstance(formal, list) and len(formal) == len(formal_policies),
             reason)
    expected_formal = []
    for snapshot in formal_policies:
        document = snapshot.document
        expected_formal.append({
            "campaign_id": document.get("campaign_id"),
            **_freeze_reference(snapshot),
            "launcher_start_ms":
                document.get("valid_after_ms") - LAUNCHER_WARMUP_MS,
            "launcher_dispatch_at_ms":
                document.get("valid_after_ms") - LAUNCHER_WARMUP_MS -
                LAUNCHER_EARLY_START_LEAD_MS,
            "valid_after_ms": document.get("valid_after_ms"),
            "expires_at_ms": document.get("expires_at_ms"),
            "slot_interval_ms": document.get("slot_interval_ms"),
            "maximum_iterations": document.get("maximum_iterations"),
            "launcher_completion_deadline_ms":
                document.get("expires_at_ms") + MAXIMUM_LAUNCH_LATENESS_MS,
            "projection_deadline_ms":
                document.get("expires_at_ms") +
                POST_FORMAL_PROJECTION_GUARD_MS,
            "teardown_deadline_ms":
                document.get("expires_at_ms") +
                POST_FORMAL_TEARDOWN_GUARD_MS,
        })
    expected_formal.sort(key=lambda item: item["valid_after_ms"])
    _require(formal == expected_formal, reason)
    for item in formal:
        _exact(item, FREEZE_FORMAL_REFERENCE_FIELDS, reason)
    _validate_calendar(
        trading_calendar, bundle=value, formal_policies=formal_policies,
        producer=producer, reason=reason)
    strategy_files = value.get("strategy_files")
    _require(isinstance(strategy_files, list) and len(strategy_files) == 5,
             reason)
    for item in strategy_files:
        _exact(item, FREEZE_STRATEGY_FILE_FIELDS, reason)
        _validate_freeze_reference({key: item[key] for key in
                                   FREEZE_REFERENCE_FIELDS}, reason)
    days = value.get("declared_trading_days")
    eligible = value.get("eligible_scheduled_at_ms")
    faults = value.get("planned_faults")
    _require(
        isinstance(days, list) and
        MINIMUM_TRADING_DAYS <= len(days) <= MAXIMUM_TRADING_DAYS and
        days == sorted(set(days)) and
        isinstance(eligible, list) and
        len(eligible) >= MINIMUM_ELIGIBLE_DECISIONS and
        eligible == sorted(set(eligible)) and
        type(value.get("scheduled_decision_count")) is int and
        value["scheduled_decision_count"] >= len(eligible) and
        isinstance(faults, list) and len(faults) == len(REQUIRED_FAULT_TYPES),
        reason)
    for fault in faults:
        _exact(fault, PLANNED_FAULT_FIELDS, reason)
        formal_window = next(
            (item for item in formal
             if item["campaign_id"] == fault.get("formal_campaign_id")),
            None)
        injection = fault.get("inject_at_boottime_ns")
        lateness = fault.get("maximum_injection_lateness_ns")
        recovery = fault.get("maximum_recovery_ns")
        _require(
            formal_window is not None and type(injection) is int and
            type(lateness) is int and type(recovery) is int and
            injection >= 0 and lateness >= 0 and recovery > 0,
            reason)
        window_start = value["frozen_boottime_ns"] + (
            formal_window["valid_after_ms"] - issued) * 1_000_000
        window_end = value["frozen_boottime_ns"] + (
            formal_window["expires_at_ms"] - issued) * 1_000_000
        _require(
            window_start <= injection and
            injection + lateness + recovery < window_end,
            reason)
    return value


def validate_source_anchor(document: dict[str, Any], now_ms: int) -> None:
    reason = "P1_RECORDER_SOURCE_ANCHOR_INVALID"
    _validate_boundary_document(
        document, SOURCE_ANCHOR_FIELDS, SOURCE_ANCHOR_SCHEMA, reason)
    _require(document.get("status") == "FROZEN" and
             document.get("source_frozen") is True and
             document.get("clean_source") is True, reason)
    _digest(document.get("source_manifest_sha256"), reason)
    _validate_anchor_freezer(document, reason, expected_uid=None)
    _validate_fresh_window(
        document, observed_field="frozen_at_ms", now_ms=now_ms,
        reason=reason, recent=False)


def validate_policy_anchor(document: dict[str, Any], now_ms: int) -> None:
    reason = "P1_RECORDER_POLICY_ANCHOR_INVALID"
    _validate_boundary_document(
        document, POLICY_ANCHOR_FIELDS, POLICY_ANCHOR_SCHEMA, reason)
    _require(document.get("status") == "FROZEN" and
             document.get("policy_frozen") is True, reason)
    _digest(document.get("policy_sha256"), reason)
    _validate_anchor_freezer(document, reason, expected_uid=None)
    _validate_fresh_window(
        document, observed_field="frozen_at_ms", now_ms=now_ms,
        reason=reason, recent=False)


def validate_strategy_anchor(document: dict[str, Any], now_ms: int) -> None:
    reason = "P1_RECORDER_STRATEGY_ANCHOR_INVALID"
    _validate_boundary_document(
        document, STRATEGY_ANCHOR_FIELDS, STRATEGY_ANCHOR_SCHEMA, reason)
    _require(document.get("status") == "FROZEN" and
             document.get("strategy_frozen") is True, reason)
    _identifier(document.get("strategy_id"), reason)
    _identifier(document.get("strategy_version"), reason)
    _digest(document.get("strategy_sha256"), reason)
    _validate_anchor_freezer(document, reason, expected_uid=None)
    _validate_fresh_window(
        document, observed_field="frozen_at_ms", now_ms=now_ms,
        reason=reason, recent=False)


def validate_frozen_schedule(
    document: dict[str, Any], now_ms: int,
) -> tuple[tuple[str, ...], ZoneInfo, tuple[int, ...]]:
    reason = "P1_RECORDER_FROZEN_SCHEDULE_INVALID"
    _validate_boundary_document(
        document, SCHEDULE_FIELDS, SCHEDULE_SCHEMA, reason)
    _require(document.get("status") == "FROZEN", reason)
    _identifier(document.get("campaign_id"), reason)
    _identifier(document.get("domain_id"), reason)
    _identifier(document.get("independent_auditor_id"), reason)
    _validate_anchor_freezer(document, reason, expected_uid=None)
    days = document.get("declared_trading_days")
    _require(
        isinstance(days, list) and
        MINIMUM_TRADING_DAYS <= len(days) <= MAXIMUM_TRADING_DAYS and
        days == sorted(set(days)), reason)
    try:
        parsed_days = [date.fromisoformat(item) for item in days]
    except (TypeError, ValueError) as error:
        raise RecorderError(reason) from error
    _require(
        all(item.isoformat() == raw and item.weekday() < 5
            for item, raw in zip(parsed_days, days)), reason)
    try:
        trading_timezone = ZoneInfo(document.get("trading_timezone"))
    except (TypeError, ZoneInfoNotFoundError) as error:
        raise RecorderError(reason) from error
    eligible = document.get("eligible_scheduled_at_ms")
    minimum = _integer(
        document.get("minimum_eligible_decisions"), reason,
        MINIMUM_ELIGIBLE_DECISIONS)
    _require(
        isinstance(eligible, list) and len(eligible) >= minimum and
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
    _integer(document.get("minimum_boottime_duration_ns"), reason,
             MINIMUM_BOOTTIME_DURATION_NS)
    maximum_gap = _integer(
        document.get("maximum_checkpoint_gap_ns"), reason, 1)
    _require(maximum_gap <= MAXIMUM_CHECKPOINT_GAP_NS, reason)
    lateness = _integer(
        document.get("maximum_decision_lateness_ms"), reason)
    _require(lateness <= 15 * 60 * 1000, reason)
    _validate_fresh_window(
        document, observed_field="frozen_at_ms", now_ms=now_ms,
        reason=reason, recent=True)
    return tuple(days), trading_timezone, tuple(eligible)


def _validate_planned_faults(
    value: Any, reason: str, *, minimum_boottime_ns: int | None = None,
    formal_campaign_ids: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    _require(isinstance(value, list) and bool(value), reason)
    identifiers: list[str] = []
    ordering: list[tuple[int, str]] = []
    result: list[dict[str, Any]] = []
    for item in value:
        fault = _exact(item, PLANNED_FAULT_FIELDS, reason)
        identifier = _identifier(fault.get("fault_id"), reason)
        formal_campaign_id = _identifier(
            fault.get("formal_campaign_id"), reason)
        if formal_campaign_ids is not None:
            _require(formal_campaign_id in formal_campaign_ids, reason)
        fault_type = fault.get("fault_type")
        _require(
            type(fault_type) is str and fault_type in ALLOWED_FAULT_TYPES and
            fault.get("target_id") == FAULT_TARGET_IDS[fault_type],
            reason)
        injection = _integer(fault.get("inject_at_boottime_ns"), reason)
        lateness = _integer(
            fault.get("maximum_injection_lateness_ns"), reason)
        recovery = _integer(fault.get("maximum_recovery_ns"), reason, 1)
        _require(lateness <= MAXIMUM_FAULT_INJECTION_LATENESS_NS and
                 recovery <= MAXIMUM_FAULT_RECOVERY_NS, reason)
        if minimum_boottime_ns is not None:
            _require(injection > minimum_boottime_ns, reason)
        identifiers.append(identifier)
        ordering.append((injection, identifier))
        result.append(dict(fault))
    _require(len(identifiers) == len(set(identifiers)) and
             ordering == sorted(ordering), reason)
    _require(
        {item["fault_type"] for item in result} == REQUIRED_FAULT_TYPES,
        reason)
    for before, after in zip(result, result[1:]):
        _require(
            after["inject_at_boottime_ns"] >
                before["inject_at_boottime_ns"] +
                before["maximum_injection_lateness_ns"] +
                before["maximum_recovery_ns"],
            reason)
    return result


def validate_frozen_fault_schedule(
    document: dict[str, Any], sample: ClockSample,
) -> list[dict[str, Any]]:
    reason = "P1_RECORDER_FROZEN_FAULT_SCHEDULE_INVALID"
    _validate_boundary_document(
        document, FAULT_SCHEDULE_FIELDS, FAULT_SCHEDULE_SCHEMA, reason)
    _require(document.get("status") == "FROZEN", reason)
    _identifier(document.get("campaign_id"), reason)
    _validate_anchor_freezer(document, reason, expected_uid=None)
    boot_id = document.get("boot_id")
    _require(type(boot_id) is str and BOOT_ID.fullmatch(boot_id) is not None and
             boot_id == sample.boot_id, reason)
    frozen_boottime = _integer(document.get("frozen_boottime_ns"), reason)
    _require(
        0 <= sample.boottime_ns - frozen_boottime <=
            MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS, reason)
    _validate_fresh_window(
        document, observed_field="frozen_at_ms", now_ms=sample.wall_ms,
        reason=reason, recent=True)
    return _validate_planned_faults(
        document.get("planned_faults"), reason,
        minimum_boottime_ns=sample.boottime_ns)


def validate_formal_policy(
    snapshot: Snapshot, now_ms: int,
) -> tuple[dict[str, Any], list[int], Snapshot]:
    reason = "P1_RECORDER_FORMAL_POLICY_INVALID"
    document = snapshot.document
    _validate_boundary_document(
        document, FORMAL_POLICY_FIELDS, FORMAL_POLICY_SCHEMA, reason)
    _identifier(document.get("campaign_id"), reason)
    _identifier(document.get("strategy_id"), reason)
    _identifier(document.get("strategy_version"), reason)
    _digest(document.get("strategy_sha256"), reason)
    valid_after = _integer(document.get("valid_after_ms"), reason, 1)
    expires = _integer(document.get("expires_at_ms"), reason, 1)
    interval = _integer(document.get("slot_interval_ms"), reason, 1)
    maximum = _integer(document.get("maximum_iterations"), reason, 1)
    lateness = _integer(document.get("maximum_lateness_ms"), reason)
    _require(
        now_ms < valid_after and
        interval == POLICY_SLOT_INTERVAL_MS and
        maximum == POLICY_MAXIMUM_ITERATIONS and
        lateness == POLICY_MAXIMUM_LATENESS_MS and
        expires == valid_after + maximum * interval and
        document.get("shadow_only") is True,
        reason)
    campaign_binding = {
        "schema": "hepta.strategy-shadow-observation-campaign.v1",
        "campaign_id": document["campaign_id"],
        "valid_after_ms": valid_after,
        "expires_at_ms": expires,
        "slot_interval_ms": interval,
        "maximum_iterations": maximum,
        "maximum_lateness_ms": lateness,
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    _require(
        document.get("campaign_sha256") ==
            digest_bytes(canonical_bytes(campaign_binding)), reason)
    slots = [valid_after + offset * interval for offset in range(maximum)]
    return document, slots, snapshot


def validate_campaign_spec(document: dict[str, Any]) -> None:
    reason = "P1_RECORDER_CAMPAIGN_SPEC_INVALID"
    _validate_boundary_document(document, SPEC_FIELDS, SPEC_SCHEMA, reason)
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
    _validate_freeze_reference(document.get("freeze_bundle"), reason)
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
    eligible = document.get("eligible_scheduled_at_ms")
    _require(isinstance(days, list) and
             MINIMUM_TRADING_DAYS <= len(days) <= MAXIMUM_TRADING_DAYS and
             days == sorted(set(days)) and isinstance(eligible, list) and
             eligible == sorted(set(eligible)), reason)
    try:
        trading_timezone = ZoneInfo(document.get("trading_timezone"))
        parsed_days = [date.fromisoformat(item) for item in days]
    except (TypeError, ValueError, ZoneInfoNotFoundError) as error:
        raise RecorderError(reason) from error
    _require(all(item.weekday() < 5 and item.isoformat() == raw
                 for item, raw in zip(parsed_days, days)), reason)
    _digest(document.get("trading_calendar_sha256"), reason)
    _require(all(
        datetime.fromtimestamp(item / 1000, tz=timezone.utc)
        .astimezone(trading_timezone).date().isoformat() in set(days)
        for item in eligible), reason)
    scheduled = _integer(
        document.get("scheduled_decision_count"), reason,
        MINIMUM_ELIGIBLE_DECISIONS)
    minimum = _integer(
        document.get("minimum_eligible_decisions"), reason,
        MINIMUM_ELIGIBLE_DECISIONS)
    _require(minimum <= len(eligible) <= scheduled, reason)
    completeness = _integer(document.get("minimum_complete_ppm"), reason)
    _require(MINIMUM_COMPLETE_PPM <= completeness <= 1_000_000, reason)
    _integer(document.get("minimum_boottime_duration_ns"), reason,
             MINIMUM_BOOTTIME_DURATION_NS)
    maximum_gap = _integer(
        document.get("maximum_checkpoint_gap_ns"), reason, 1)
    _require(maximum_gap <= MAXIMUM_CHECKPOINT_GAP_NS, reason)
    _integer(document.get("maximum_decision_lateness_ms"), reason)
    _integer(document.get("frozen_at_ms"), reason)


def validate_fault_plan(
    document: dict[str, Any], spec: Mapping[str, Any],
) -> None:
    reason = "P1_RECORDER_FAULT_PLAN_INVALID"
    _validate_boundary_document(
        document, FAULT_PLAN_FIELDS, FAULT_PLAN_SCHEMA, reason)
    _require(
        document.get("campaign_id") == spec.get("campaign_id") and
        document.get("source_manifest_sha256") ==
            spec.get("source_manifest_sha256") and
        document.get("policy_sha256") == spec.get("policy_sha256") and
        document.get("strategy_sha256") == spec.get("strategy_sha256") and
        document.get("body_sha256") == spec.get("fault_plan_body_sha256"),
        reason)
    formal_campaign_ids = frozenset(
        item.get("campaign_id")
        for item in spec.get("formal_campaigns", [])
        if isinstance(item, Mapping)
    )
    _require(bool(formal_campaign_ids) and None not in formal_campaign_ids,
             reason)
    _validate_planned_faults(
        document.get("planned_faults"), reason,
        formal_campaign_ids=formal_campaign_ids)


def _validate_observer_header(
    document: dict[str, Any], fields: frozenset[str], schema: str,
    spec: Mapping[str, Any], sample: ClockSample, reason: str,
) -> None:
    _validate_boundary_document(document, fields, schema, reason)
    _require(
        document.get("status") == "COMPLETE" and
        document.get("observation_complete") is True and
        document.get("campaign_id") == spec.get("campaign_id") and
        document.get("source_manifest_sha256") ==
            spec.get("source_manifest_sha256") and
        document.get("policy_sha256") == spec.get("policy_sha256") and
        document.get("strategy_sha256") == spec.get("strategy_sha256") and
        document.get("clock_id") == "CLOCK_BOOTTIME" and
        document.get("boot_id") == sample.boot_id,
        reason)
    _identifier(document.get("observer_id"), reason)
    producer = _exact(
        document.get("producer"), OBSERVER_PRODUCER_FIELDS, reason)
    _require(
        producer.get("path") == str(OBSERVER_EXECUTABLE) and
        type(producer.get("file_sha256")) is str and
        DIGEST.fullmatch(producer["file_sha256"]) is not None and
        producer["file_sha256"] != "sha256:" + "0" * 64 and
        document.get("production_mode") == OBSERVER_PRODUCTION_MODE,
        reason)
    _validate_fresh_window(
        document, observed_field="observed_at_ms", now_ms=sample.wall_ms,
        reason=reason, recent=True)


def validate_service_observation(
    document: dict[str, Any], spec: Mapping[str, Any], sample: ClockSample,
) -> None:
    reason = "P1_RECORDER_SERVICE_OBSERVATION_INVALID"
    _validate_observer_header(
        document, SERVICE_OBSERVATION_FIELDS, SERVICE_OBSERVATION_SCHEMA,
        spec, sample, reason)
    observed = _integer(document.get("observed_boottime_ns"), reason)
    _require(abs(sample.boottime_ns - observed) <=
             MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS, reason)
    _identifier(document.get("service_epoch"), reason)
    _integer(document.get("fencing_generation"), reason)
    _integer(document.get("lease_generation"), reason)
    transition = document.get("transition_fault_id")
    _require(transition is None or
             (type(transition) is str and
              IDENTIFIER.fullmatch(transition) is not None), reason)
    for field in ("continuity_ok", "audit_ok", "cleanup_ok"):
        _require(type(document.get(field)) is bool, reason)
    validate_observation_evidence(
        document.get("observation_evidence"), kind="SERVICE",
        boot_id=sample.boot_id, expected_boottime_ns=observed,
        reason=reason)


def _validate_campaign_runtime_snapshot(
    reference_value: Any, spec_snapshot: Snapshot, sample: ClockSample,
    *, expected_uid: int, expected_observer_sha256: str,
) -> Snapshot:
    reason = "P1_RECORDER_CAMPAIGN_RUNTIME_INVALID"
    runtime = _open_observer_reference(
        reference_value, CAMPAIGN_RUNTIME_SCHEMA, reason,
        expected_uid=expected_uid)
    value = runtime.document
    _validate_boundary_document(
        value, CAMPAIGN_RUNTIME_FIELDS, CAMPAIGN_RUNTIME_SCHEMA, reason)
    spec = spec_snapshot.document
    _require(
        value.get("version") == VERSION and value.get("status") == "FROZEN" and
        value.get("campaign_id") == spec.get("campaign_id") and
        value.get("round") == 114 and value.get("boot_id") == sample.boot_id,
        reason)
    issued = _integer(value.get("issued_at_ms"), reason)
    expires = _integer(value.get("expires_at_ms"), reason, issued + 1)
    _require(issued <= sample.wall_ms < expires, reason)
    campaign_spec = _exact(
        value.get("campaign_spec"), RUNTIME_FILE_REFERENCE_FIELDS, reason)
    _require(campaign_spec == _freeze_reference(spec_snapshot) and
             value.get("freeze_bundle") == spec.get("freeze_bundle"), reason)
    fault_plan = _exact(
        value.get("fault_plan"), RUNTIME_FILE_REFERENCE_FIELDS, reason)
    _require(fault_plan.get("body_sha256") ==
             spec.get("fault_plan_body_sha256"), reason)
    for reference in (campaign_spec, fault_plan, value.get("freeze_bundle")):
        _validate_freeze_reference(reference, reason)
    formals = value.get("formal_campaigns")
    _require(isinstance(formals, list) and bool(formals), reason)
    frozen_by_id = {item["campaign_id"]: item
                    for item in spec["formal_campaigns"]}
    identifiers: list[str] = []
    previous_teardown: int | None = None
    for raw in formals:
        item = _exact(raw, FORMAL_RUNTIME_FIELDS, reason)
        formal_id = _identifier(item.get("formal_campaign_id"), reason)
        identifiers.append(formal_id)
        frozen = frozen_by_id.get(formal_id)
        _require(frozen is not None, reason)
        _identifier(item.get("probe_campaign_id"), reason)
        dispatch = _integer(item.get("launcher_dispatch_at_ms"), reason, 1)
        start = _integer(item.get("launcher_start_ms"), reason, dispatch + 1)
        valid_after = _integer(item.get("valid_after_ms"), reason, start + 1)
        interval = _integer(item.get("slot_interval_ms"), reason, 1)
        maximum = _integer(item.get("maximum_iterations"), reason, 1)
        expiry = _integer(item.get("expires_at_ms"), reason, valid_after + 1)
        completion = _integer(
            item.get("launcher_completion_deadline_ms"), reason, expiry)
        projection = _integer(
            item.get("projection_deadline_ms"), reason, completion)
        teardown = _integer(
            item.get("teardown_deadline_ms"), reason, projection)
        policy = _exact(
            item.get("policy"), RUNTIME_FILE_REFERENCE_FIELDS, reason)
        _validate_freeze_reference(policy, reason)
        _require(
            dispatch < start < valid_after and
            interval == POLICY_SLOT_INTERVAL_MS and
            maximum == POLICY_MAXIMUM_ITERATIONS and
            expiry == valid_after + interval * maximum and
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
            path = item.get(field)
            _require(type(path) is str, reason)
            _canonical_path(Path(path), reason)
    _require(
        identifiers == list(frozen_by_id) and
        len(identifiers) == len(set(identifiers)) and
        value.get("pin_formal_campaign_id") in set(identifiers) and
        previous_teardown is not None and expires > previous_teardown,
        reason)
    cadence = _integer(value.get("observer_cadence_ms"), reason, 1)
    lateness = _integer(value.get("maximum_slot_lateness_ms"), reason)
    _require(lateness < cadence, reason)
    for field in (
            "state_root", "raw_observation_directory", "recorder_root",
            "injector_journal_directory", "injector_output_directory",
            "control_directory"):
        path = value.get(field)
        _require(type(path) is str, reason)
        _canonical_path(Path(path), reason)
    executables = value.get("executables")
    _require(isinstance(executables, dict) and
             "independent_observer" in executables, reason)
    for raw in executables.values():
        executable = _exact(raw, RUNTIME_EXECUTABLE_FIELDS, reason)
        path = executable.get("path")
        _require(type(path) is str, reason)
        _canonical_path(Path(path), reason)
        _digest(executable.get("file_sha256"), reason)
    observer = executables["independent_observer"]
    _require(observer.get("path") == str(OBSERVER_EXECUTABLE) and
             observer.get("file_sha256") == expected_observer_sha256, reason)
    return runtime


def _validate_activation_gateway_binding(
    activation: Mapping[str, Any], document: Mapping[str, Any], reason: str,
) -> None:
    frozen = _exact(
        activation.get("gateway_after"),
        ACTIVATION_GATEWAY_AFTER_FIELDS, reason)
    for field in (
            "gateway_executable_sha256", "domain_config_sha256",
            "gateway_profile_sha256", "gateway_process_profile_sha256",
            "unit_contract_sha256"):
        _digest(frozen.get(field), reason)
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
    gateway = _validate_observation_unit(document.get("gateway_identity"), reason)
    process = _validate_observation_process(
        document.get("gateway_process_identity"), reason)
    executable = _validate_observation_path(
        document.get("gateway_executable_identity"), reason)
    profile = _validate_observation_path(
        document.get("gateway_profile_identity"), reason)
    domain = _validate_observation_path(
        document.get("gateway_domain_config_identity"), reason)
    tool = _validate_observation_path(
        document.get("tool_socket_identity"), reason)
    supervisor = _validate_observation_path(
        document.get("supervisor_socket_identity"), reason)
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
    if document.get("continuity_slot_index") == 0:
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


def validate_campaign_continuity_observation(
    document: dict[str, Any], spec_snapshot: Snapshot, sample: ClockSample,
    *, expected_uid: int,
) -> Snapshot:
    reason = "P1_RECORDER_CAMPAIGN_CONTINUITY_OBSERVATION_INVALID"
    spec = spec_snapshot.document
    _validate_observer_header(
        document, CAMPAIGN_CONTINUITY_OBSERVATION_FIELDS,
        CAMPAIGN_CONTINUITY_OBSERVATION_SCHEMA, spec, sample, reason)
    observed = _integer(document.get("observed_boottime_ns"), reason)
    _require(abs(sample.boottime_ns - observed) <=
             MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS, reason)
    _require(
        _validate_freeze_reference(document.get("freeze_bundle"), reason) ==
            spec.get("freeze_bundle"), reason)
    producer = _exact(document.get("producer"), OBSERVER_PRODUCER_FIELDS, reason)
    runtime = _validate_campaign_runtime_snapshot(
        document.get("campaign_runtime"), spec_snapshot, sample,
        expected_uid=expected_uid,
        expected_observer_sha256=producer["file_sha256"])
    runtime_value = runtime.document
    origin = runtime_value["formal_campaigns"][0]["launcher_dispatch_at_ms"]
    end = runtime_value["formal_campaigns"][-1]["teardown_deadline_ms"]
    cadence = runtime_value["observer_cadence_ms"]
    final_slot = (end - origin + cadence - 1) // cadence
    slot = _integer(document.get("continuity_slot_index"), reason)
    scheduled = min(origin + slot * cadence, end)
    _require(
        0 <= slot <= final_slot and
        document.get("continuity_scheduled_at_ms") == scheduled and
        document.get("continuity_origin_ms") == origin and
        document.get("continuity_end_ms") == end and
        document.get("continuity_cadence_ms") == cadence and
        document.get("continuity_final_slot") == final_slot and
        document.get("continuity_is_final") is (slot == final_slot) and
        document.get("catch_up") is False and
        scheduled <= document["observed_at_ms"] <=
            scheduled + runtime_value["maximum_slot_lateness_ms"] and
        (slot != 0 or document.get("transition_fault_id") is None), reason)
    activation = _validate_observer_reference(
        document.get("activation_receipt"),
        "hepta.p1-watch-activation-receipt.v4", reason)
    lease = _validate_observer_reference(
        document.get("lease_receipt"),
        "hepta.shadow-watch-lease-receipt.v1", reason)
    activation_document = _exact(
        document.get("activation_receipt_document"),
        ACTIVATION_RECEIPT_FIELDS, reason)
    _validate_seal(activation_document, reason)
    _reject_authority(activation_document, reason)
    _require(
        activation_document.get("schema") == activation["schema"] and
        activation_document.get("version") == 4 and
        activation_document.get("status") == "WATCH_GATEWAY_ACTIVATED" and
        activation_document.get("boot_id") == sample.boot_id and
        activation_document.get("gateway_activated") is True and
        activation_document.get("broker_deny_all_continuity_attested") is True and
        activation_document.get("kill_switch_engaged") is True and
        activation_document.get("watch_authority_provisioned") is False and
        digest_bytes(canonical_bytes(activation_document)) ==
            activation["file_sha256"] and
        activation_document["body_sha256"] == activation["body_sha256"],
        reason)
    _validate_activation_install_lineage(
        activation_document.get("shadow_install_evidence"),
        spec["source_manifest_sha256"], reason)
    _validate_activation_predecessor_lineage(
        activation_document.get("predecessor_activation_success"),
        activation_document.get("predecessor_activation_failure"), reason)
    _validate_activation_gateway_binding(
        activation_document, document, reason)
    export_commit, export_references = _validate_export_projection(
        document, expected_uid=expected_uid, reason=reason)
    lease_document = _exact(
        document.get("lease_receipt_document"), WATCH_LEASE_FIELDS, reason)
    _validate_seal(lease_document, reason)
    _reject_authority(lease_document, reason)
    generation = _integer(document.get("lease_generation"), reason, 1)
    _require(
        lease_document.get("schema") == lease["schema"] and
        digest_bytes(canonical_bytes(lease_document)) ==
            lease["file_sha256"] and
        lease_document["body_sha256"] == lease["body_sha256"] and
        export_commit.get("lease_generation") == generation and
        export_commit.get("lease_receipt_file_sha256") ==
            lease["file_sha256"] and
        export_commit.get("lease_receipt_body_sha256") ==
            lease["body_sha256"] and
        lease_document.get("operation") == "ROTATE" and
        lease_document.get("accepted") is True and
        lease_document.get("boundary") == "WATCH", reason)
    previous_generation = _integer(
        document.get("previous_lease_generation"), reason)
    _digest(document.get("previous_lease_receipt_body_sha256"), reason)
    _require(
        previous_generation == generation - 1 and
        lease_document.get("lease_generation") == generation and
        lease_document.get("previous_lease_generation") ==
            previous_generation and
        lease_document.get("previous_receipt_body_sha256") ==
            document.get("previous_lease_receipt_body_sha256"), reason)
    gateway = _validate_observation_unit(
        document.get("gateway_identity"), reason)
    gateway_process = _validate_observation_process(
        document.get("gateway_process_identity"), reason)
    gateway_executable = _validate_observation_path(
        document.get("gateway_executable_identity"), reason)
    gateway_profile = _validate_observation_path(
        document.get("gateway_profile_identity"), reason)
    gateway_domain = _validate_observation_path(
        document.get("gateway_domain_config_identity"), reason)
    supervisor_socket = _validate_observation_path(
        document.get("supervisor_socket_identity"), reason)
    custodian = _validate_observation_unit(
        document.get("custodian_identity"), reason)
    collector = _validate_observation_unit(
        document.get("collector_timer_identity"), reason)
    reconcile = _validate_observation_unit(
        document.get("activation_reconcile_timer_identity"), reason)
    tool_socket = _validate_observation_path(
        document.get("tool_socket_identity"), reason)
    _require(
        gateway.get("unit") == "hepta-tool-gateway@alpha.service" and
        custodian.get("unit") ==
            "hepta-shadow-watch-custodian@alpha.service" and
        collector.get("unit") ==
            "hepta-shadow-watch-collector@alpha.timer" and
        reconcile.get("unit") ==
            "hepta-p1-watch-activation-reconcile.timer" and
        tool_socket.get("path") == "/run/hepta-agent-alpha/tools.sock",
        reason)
    transition = document.get("transition_fault_id")
    _require(transition is None or
             (type(transition) is str and
              IDENTIFIER.fullmatch(transition) is not None), reason)
    for field in (
        "persistent_stack_ok", "lease_chain_ok", "campaign_socket_present",
        "kill_switch_engaged", "zero_exposure",
    ):
        _require(type(document.get(field)) is bool, reason)
    _integer(document.get("connector_count"), reason)
    _integer(document.get("paper_unit_active_count"), reason)
    authorized = document.get("authorized_uids")
    _require(isinstance(authorized, list) and
             authorized == sorted(set(authorized)) and
             all(type(item) is int and item >= 0 for item in authorized),
             reason)
    evidence = validate_observation_evidence(
        document.get("observation_evidence"), kind="CAMPAIGN_CONTINUITY",
        boot_id=sample.boot_id, expected_boottime_ns=observed,
        reason=reason)
    units, processes, paths = _validate_observation_identity_lists(
        evidence, reason)
    _require(
        all(item in units for item in (
            gateway, custodian, collector, reconcile)) and
        gateway_process in processes and
        all(item in paths for item in (
            gateway_executable, gateway_profile, gateway_domain,
            tool_socket, supervisor_socket)) and
        all(any(
            item.get("path") == reference["path"] and
            item.get("present") is True and
            item.get("file_type") == "regular" and
            item.get("uid") == ROOT_UID and
            item.get("gid") == export_commit["reader_gid"] and
            item.get("mode") == 0o440 and
            item.get("nlink") == 1 and
            item.get("parent_uid") == ROOT_UID and
            item.get("parent_gid") == export_commit["reader_gid"] and
            item.get("parent_mode") == 0o750 and
            item.get("content_file_sha256") == reference["file_sha256"] and
            item.get("content_body_sha256") == reference["body_sha256"]
            for item in paths)
            for reference in export_references), reason)
    broker = _validate_observation_broker(
        evidence.get("broker_deny_all"), reason)
    _require(broker is not None and
             broker["authorized_connector_count"] ==
                document["connector_count"] and
             broker["authorized_uids"] == authorized and
             document["persistent_stack_ok"] == (
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
             document["lease_chain_ok"] is True and
             document["zero_exposure"] == (
                 document["connector_count"] == 0 and not authorized and
                 document["paper_unit_active_count"] == 0 and
                 document["campaign_socket_present"] is False and
                 document["kill_switch_engaged"] is True and
                 broker["deny_all"] is True and
                 broker["protected_port_count"] == 4), reason)
    return runtime


def validate_historical_service_observation(
    document: dict[str, Any], spec: Mapping[str, Any], sample: ClockSample,
) -> None:
    """Validate a sealed projection anchor without treating it as live state.

    A decision projection may legitimately run after the observer receipt's
    short freshness window, but only before the frozen formal projection
    deadline enforced by ``project_decisions``.  This validator preserves all
    identity, lineage, boot, wall/boottime and original lifetime checks while
    deliberately omitting the assertion that the receipt is still current.
    """

    reason = "P1_RECORDER_SERVICE_OBSERVATION_INVALID"
    _validate_boundary_document(
        document, SERVICE_OBSERVATION_FIELDS, SERVICE_OBSERVATION_SCHEMA,
        reason)
    _require(
        document.get("status") == "COMPLETE" and
        document.get("observation_complete") is True and
        document.get("campaign_id") == spec.get("campaign_id") and
        document.get("source_manifest_sha256") ==
            spec.get("source_manifest_sha256") and
        document.get("policy_sha256") == spec.get("policy_sha256") and
        document.get("strategy_sha256") == spec.get("strategy_sha256") and
        document.get("clock_id") == "CLOCK_BOOTTIME" and
        document.get("boot_id") == sample.boot_id,
        reason)
    _identifier(document.get("observer_id"), reason)
    producer = _exact(
        document.get("producer"), OBSERVER_PRODUCER_FIELDS, reason)
    _require(
        producer.get("path") == str(OBSERVER_EXECUTABLE) and
        type(producer.get("file_sha256")) is str and
        DIGEST.fullmatch(producer["file_sha256"]) is not None and
        producer["file_sha256"] != "sha256:" + "0" * 64 and
        document.get("production_mode") == OBSERVER_PRODUCTION_MODE,
        reason)
    observed_at = _integer(document.get("observed_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    observed_boottime = _integer(
        document.get("observed_boottime_ns"), reason)
    _require(
        observed_at < expires <=
            observed_at + MAXIMUM_OBSERVER_LIFETIME_MS and
        observed_at <= sample.wall_ms and
        observed_boottime <= sample.boottime_ns and
        abs((sample.wall_ms - observed_at) * 1_000_000 -
            (sample.boottime_ns - observed_boottime)) <=
                CLOCK_CORRELATION_TOLERANCE_NS,
        reason)
    _identifier(document.get("service_epoch"), reason)
    _integer(document.get("fencing_generation"), reason)
    _integer(document.get("lease_generation"), reason)
    transition = document.get("transition_fault_id")
    _require(transition is None or
             (type(transition) is str and
              IDENTIFIER.fullmatch(transition) is not None), reason)
    for field in ("continuity_ok", "audit_ok", "cleanup_ok"):
        _require(type(document.get(field)) is bool, reason)
    validate_observation_evidence(
        document.get("observation_evidence"), kind="SERVICE",
        boot_id=sample.boot_id, expected_boottime_ns=observed_boottime,
        reason=reason)


def validate_actual_decision(
    document: dict[str, Any], spec: Mapping[str, Any], formal_campaign_id: str,
) -> None:
    reason = "P1_RECORDER_ACTUAL_DECISION_INVALID"
    _exact(document, ACTUAL_DECISION_FIELDS, reason)
    _reject_authority(document, reason)
    _require(
        document.get("schema") ==
            "hepta.autonomous-paper-decision-receipt.v1" and
        document.get("campaign_id") == formal_campaign_id and
        document.get("strategy_id") == spec.get("strategy_id") and
        document.get("strategy_version") == spec.get("strategy_version") and
        document.get("strategy_sha256") == spec.get("strategy_sha256") and
        document.get("paper_only") is True and
        document.get("shadow_only") is True and
        document.get("live_authorized") is False and
        document.get("mutation_attempted") is False and
        document.get("direct_broker_access") is False,
        reason)
    _identifier(document.get("decision_id"), reason)
    started = _integer(document.get("started_at_ms"), reason)
    _integer(document.get("finished_at_ms"), reason, started)
    _digest(document.get("information_packet_sha256"), reason)
    _digest(document.get("catalog_sha256"), reason)
    _digest(document.get("descriptor_sha256"), reason)
    _require(
        document.get("preflight_sha256") is None and
        document.get("campaign_open_request_id") is None and
        document.get("campaign_close_request_id") is None and
        document.get("regime") in ACTUAL_REGIMES,
        reason)
    list_values: dict[str, list[str]] = {}
    for field in (
        "setup_gates", "risk_challenges", "conflicts", "reason_codes",
    ):
        value = document.get(field)
        _require(isinstance(value, list) and
                 all(type(item) is str and
                     IDENTIFIER.fullmatch(item) is not None for item in value)
                 and len(value) == len(set(value)), reason)
        list_values[field] = value
    evidence = document.get("evidence_refs")
    _require(isinstance(evidence, list) and bool(evidence) and
             all(type(item) is str and DIGEST.fullmatch(item) is not None
                 for item in evidence) and
             len(evidence) == len(set(evidence)), reason)
    formal = next((item for item in spec.get("formal_campaigns", [])
                   if item.get("campaign_id") == formal_campaign_id), None)
    _require(formal is not None and {
        formal["campaign_sha256"], formal["policy_file_sha256"]
    }.issubset(set(evidence)), reason)
    if document.get("decision") == "NO_TRADE":
        _require(
            document.get("cycle_id") is None and
            document.get("trade_intent") is None and
            document.get("trade_intent_sha256") is None and
            document.get("final_outcome") == "NO_TRADE" and
            list_values["setup_gates"] == [] and
            bool(list_values["reason_codes"]) and
            list_values["risk_challenges"] == list_values["reason_codes"],
            reason)
        return
    _require(document.get("decision") == "TRADE" and
             document.get("final_outcome") == "SHADOW_TRADE" and
             bool(list_values["setup_gates"]) and
             list_values["risk_challenges"] == [] and
             list_values["reason_codes"] == [], reason)
    _identifier(document.get("cycle_id"), reason)
    intent = _exact(document.get("trade_intent"), TRADE_INTENT_FIELDS, reason)
    _require(
        intent.get("schema") == "hepta.trade-intent.v1" and
        intent.get("paper_only") is True and
        intent.get("strategy_id") == spec.get("strategy_id") and
        intent.get("strategy_version") == spec.get("strategy_version") and
        intent.get("strategy_sha256") == spec.get("strategy_sha256") and
        intent.get("instrument") == "EUR.USD" and
        intent.get("symbol") == "EUR" and intent.get("currency") == "USD" and
        intent.get("sec_type") == "CASH" and
        intent.get("exchange") == "IDEALPRO" and
        intent.get("side") in {"BUY", "SELL"} and
        intent.get("order_type") == "LMT" and intent.get("tif") == "DAY",
        reason)
    _identifier(intent.get("intent_id"), reason)
    quantity = _integer(intent.get("quantity"), reason, 1)
    _require(quantity <= 1000, reason)
    limit_price = _number(intent.get("limit_price"), reason, minimum=0.0)
    bid = _number(intent.get("observed_bid"), reason, minimum=0.0)
    ask = _number(intent.get("observed_ask"), reason, minimum=0.0)
    _require(bid > 0 and ask > 0 and limit_price > 0 and bid <= ask and
             math.isclose(limit_price, ask if intent["side"] == "BUY" else bid,
                          rel_tol=0.0, abs_tol=1e-12), reason)
    observed = _integer(intent.get("observed_at_ms"), reason)
    expires = _integer(intent.get("expires_at_ms"), reason, observed + 1)
    _require(observed <= document["started_at_ms"] and
             expires >= document["finished_at_ms"], reason)
    for field in ("entry_thesis", "invalidation_condition", "exit_plan"):
        _require(type(intent.get(field)) is str and bool(intent[field]) and
                 len(intent[field]) <= 4096, reason)
    holding = _integer(intent.get("max_holding_ms"), reason, 1)
    _require(holding <= 86_400_000, reason)
    _number(intent.get("max_adverse_move"), reason, minimum=0.0)
    _number(intent.get("expected_slippage"), reason, minimum=0.0)
    claimed_intent = _digest(document.get("trade_intent_sha256"), reason)
    _require(claimed_intent == digest_bytes(canonical_bytes(intent)), reason)


def validate_verified_closure(
    snapshot: Snapshot, spec: Mapping[str, Any],
) -> list[dict[str, Any]]:
    reason = "P1_RECORDER_VERIFIED_CLOSURE_INVALID"
    value = snapshot.document
    _validate_boundary_document(
        value, VERIFIED_CLOSURE_FIELDS, VERIFIED_CLOSURE_SCHEMA, reason)
    formal = next((item for item in spec.get("formal_campaigns", [])
                   if item.get("campaign_id") == value.get("campaign_id")),
                  None)
    _require(
        formal is not None and
        value.get("campaign_sha256") == formal["campaign_sha256"] and
        value.get("policy_body_sha256") == formal["policy_body_sha256"] and
        value.get("policy_file_sha256") == formal["policy_file_sha256"] and
        value.get("strategy_id") == spec.get("strategy_id") and
        value.get("strategy_version") == spec.get("strategy_version") and
        value.get("strategy_sha256") == spec.get("strategy_sha256"), reason)
    for field in (
        "strategy_file_sha256", "observer_state_body_sha256",
        "observer_state_file_sha256", "strategy_state_file_sha256",
        "final_audit_body_sha256", "final_audit_file_sha256",
    ):
        _digest(value.get(field), reason)
    completed = _integer(value.get("completed_iterations"), reason, 1)
    _require(
        value.get("maximum_iterations") == completed and
        value.get("iteration_count") == completed and
        isinstance(value.get("iterations"), list) and
        len(value["iterations"]) == completed and
        _integer(value.get("segment_count"), reason, 1) ==
            len(value.get("segments", [])) and
        _integer(value.get("verified_at_ms"), reason) >= 0 and
        type(value.get("complete_revalidation")) is bool and
        value.get("closure_status") ==
            "VERIFIED_WITH_RETAINED_EVIDENCE_RESIDUALS" and
        isinstance(value.get("residual_evidence"), list) and
        bool(value["residual_evidence"]), reason)
    for index, segment in enumerate(value["segments"], start=1):
        _exact(segment, VERIFIED_SEGMENT_FIELDS, reason)
        _require(segment.get("segment_index") == index and
                 _integer(segment.get("record_count"), reason, 1) >= 1 and
                 all(DIGEST.fullmatch(segment.get(field, "")) is not None
                     for field in (
                         "history_head_sha256", "source_sha256",
                         "audit_sha256")), reason)
    digest_fields = VERIFIED_ITERATION_FIELDS - {
        "iteration", "segment_index", "scheduled_at_ms", "evaluated_at_ms",
        "source_first_sequence", "source_last_sequence",
        "source_record_count", "source_total_record_count",
        "source_window_truncated", "source_predecessor_record_sha256",
        "materialization_window_ms", "materialization_maximum_records",
        "source_attestation", "final_outcome", "residual_evidence",
    }
    previous_scheduled: int | None = None
    result: list[dict[str, Any]] = []
    for index, iteration in enumerate(value["iterations"], start=1):
        _exact(iteration, VERIFIED_ITERATION_FIELDS, reason)
        scheduled = _integer(iteration.get("scheduled_at_ms"), reason)
        evaluated = _integer(
            iteration.get("evaluated_at_ms"), reason, scheduled)
        _require(
            iteration.get("iteration") == index and
            _integer(iteration.get("segment_index"), reason, 1) <=
                value["segment_count"] and
            (previous_scheduled is None or scheduled > previous_scheduled) and
            iteration.get("final_outcome") in {
                "NO_TRADE", "SHADOW_TRADE", "TRADE_CANDIDATE"} and
            isinstance(iteration.get("residual_evidence"), list) and
            bool(iteration["residual_evidence"]) and
            all(DIGEST.fullmatch(iteration.get(field, "")) is not None
                for field in digest_fields), reason)
        attestation = _exact(
            iteration.get("source_attestation"),
            VERIFIED_SOURCE_ATTESTATION_FIELDS, reason)
        _require(attestation.get("raw_payloads_verified") is True and
                 all(DIGEST.fullmatch(attestation.get(field, "")) is not None
                     for field in VERIFIED_SOURCE_ATTESTATION_FIELDS - {
                         "raw_payloads_verified"}), reason)
        del evaluated
        previous_scheduled = scheduled
        result.append(iteration)
    return result


def validate_fault_observation(
    document: dict[str, Any], spec: Mapping[str, Any], sample: ClockSample,
) -> None:
    reason = "P1_RECORDER_FAULT_OBSERVATION_INVALID"
    _validate_observer_header(
        document, FAULT_OBSERVATION_FIELDS, FAULT_OBSERVATION_SCHEMA,
        spec, sample, reason)
    _identifier(document.get("fault_id"), reason)
    fault_type = document.get("fault_type")
    _require(
        type(fault_type) is str and fault_type in ALLOWED_FAULT_TYPES and
        document.get("target_id") == FAULT_TARGET_IDS[fault_type], reason)
    injection = _integer(document.get("injection_boottime_ns"), reason)
    recovered = _integer(
        document.get("recovered_boottime_ns"), reason, injection)
    _require(recovered <= sample.boottime_ns +
             MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS, reason)
    for field in (
        "recovery_verified", "cleanup_verified", "authority_failure",
        "audit_failure", "cleanup_failure",
    ):
        _require(type(document.get(field)) is bool, reason)
    validate_observation_evidence(
        document.get("observation_evidence"), kind="FAULT",
        boot_id=sample.boot_id, minimum_boottime_ns=recovered,
        reason=reason)


def validate_authority_observation(
    document: dict[str, Any], spec: Mapping[str, Any], sample: ClockSample,
) -> None:
    reason = "P1_RECORDER_AUTHORITY_OBSERVATION_INVALID"
    _validate_observer_header(
        document, AUTHORITY_OBSERVATION_FIELDS,
        AUTHORITY_OBSERVATION_SCHEMA, spec, sample, reason)
    observed = _integer(document.get("observed_boottime_ns"), reason)
    _require(abs(sample.boottime_ns - observed) <=
             MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS, reason)
    for field in ("connector_count", "paper_unit_active_count"):
        _integer(document.get(field), reason)
    uids = document.get("authorized_uids")
    _require(isinstance(uids, list) and
             uids == sorted(set(uids)) and
             all(type(uid) is int and uid >= 0 for uid in uids), reason)
    _require(
        all(type(document.get(field)) is bool for field in (
            "campaign_socket_present", "kill_switch_engaged",
            "local_boundary_safe", "local_boundary_uncertain",
            "authoritative_account_state_observed")) and
        document.get("observation_scope") == "LOCAL_HOST_BOUNDARY_ONLY" and
        document.get("authoritative_account_state_observed") is False and
        document["local_boundary_safe"] == (
            document["connector_count"] == 0 and not uids and
            document["paper_unit_active_count"] == 0 and
            document["campaign_socket_present"] is False and
            document["kill_switch_engaged"] is True and
            document["local_boundary_uncertain"] is False), reason)
    validate_observation_evidence(
        document.get("observation_evidence"), kind="AUTHORITY",
        boot_id=sample.boot_id, expected_boottime_ns=observed,
        reason=reason)


def validate_cleanup_observation(
    document: dict[str, Any], spec: Mapping[str, Any], sample: ClockSample,
) -> None:
    reason = "P1_RECORDER_CLEANUP_OBSERVATION_INVALID"
    _validate_observer_header(
        document, CLEANUP_OBSERVATION_FIELDS, CLEANUP_OBSERVATION_SCHEMA,
        spec, sample, reason)
    observed = _integer(document.get("observed_boottime_ns"), reason)
    _require(abs(sample.boottime_ns - observed) <=
             MAXIMUM_OBSERVER_BOOTTIME_SKEW_NS, reason)
    _require(document.get("subject_type") in {"LAUNCHER", "FAULT", "FINAL"},
             reason)
    _identifier(document.get("subject_id"), reason)
    for field in (
        "watch_authority_count", "export_residue_count",
        "session_authority_count", "paper_unit_active_count",
    ):
        _integer(document.get(field), reason)
    for field in (
        "campaign_socket_present", "cleanup_complete", "cleanup_uncertain",
    ):
        _require(type(document.get(field)) is bool, reason)
    errors = document.get("errors")
    _require(isinstance(errors, list) and
             all(type(item) is str and bool(item) for item in errors), reason)
    validate_observation_evidence(
        document.get("observation_evidence"), kind="CLEANUP",
        boot_id=sample.boot_id, expected_boottime_ns=observed,
        reason=reason)


def evidence_is_unsafe(document: Mapping[str, Any]) -> bool:
    """Return whether a produced recorder artifact requires fail-closed exit."""

    schema = document.get("schema")
    if schema == CHECKPOINT_SCHEMA:
        return bool(
            document.get("persistent_stack_ok") is not True or
            document.get("lease_chain_ok") is not True or
            document.get("zero_exposure") is not True or
            document.get("connector_count") or
            document.get("authorized_uids") or
            document.get("paper_unit_active_count") or
            document.get("campaign_socket_present") or
            document.get("kill_switch_engaged") is not True)
    if schema == FAULT_RESULT_SCHEMA:
        return (
            document.get("recovery_verified") is not True or
            document.get("cleanup_verified") is not True or
            any(document.get(field) is True for field in (
                "authority_failure", "audit_failure", "cleanup_failure")))
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
    return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    recover_parser = subparsers.add_parser("recover")
    recover_parser.add_argument("--root", type=Path, required=True)

    freeze_parser = subparsers.add_parser("freeze")
    freeze_parser.add_argument("--root", type=Path, required=True)
    freeze_parser.add_argument("--source-anchor", type=Path, required=True)
    freeze_parser.add_argument("--policy-anchor", type=Path, required=True)
    freeze_parser.add_argument("--strategy-anchor", type=Path, required=True)
    freeze_parser.add_argument(
        "--formal-policy", type=Path, action="append", required=True)
    freeze_parser.add_argument("--schedule", type=Path, required=True)
    freeze_parser.add_argument("--fault-schedule", type=Path, required=True)
    freeze_parser.add_argument("--freeze-bundle", type=Path, required=True)

    checkpoint_parser = subparsers.add_parser("checkpoint")
    checkpoint_parser.add_argument("--root", type=Path, required=True)
    checkpoint_parser.add_argument("--observation", type=Path, required=True)

    project_parser = subparsers.add_parser("project-decisions")
    project_parser.add_argument("--root", type=Path, required=True)
    project_parser.add_argument(
        "--verified-closure", type=Path, required=True)
    project_parser.add_argument(
        "--decision", type=Path, action="append", required=True)
    project_parser.add_argument(
        "--clock-observation", type=Path, required=True)

    for command in ("record-fault", "record-authority", "record-cleanup"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--root", type=Path, required=True)
        command_parser.add_argument(
            "--observation", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _require(arguments.run, "P1_RECORDER_EXPLICIT_RUN_REQUIRED")
        _require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
                 "P1_RECORDER_ROOT_REQUIRED")
        producer = bind_executing_image()
        producer.reopen()
        recorder = Recorder(arguments.root)
        if arguments.command == "recover":
            recorder.recover()
            output: Any = {"status": "RECOVERED", **_boundary()}
        elif arguments.command == "freeze":
            spec, plan = recorder.freeze(
                source_anchor_path=arguments.source_anchor,
                policy_anchor_path=arguments.policy_anchor,
                strategy_anchor_path=arguments.strategy_anchor,
                formal_policy_paths=arguments.formal_policy,
                schedule_path=arguments.schedule,
                fault_schedule_path=arguments.fault_schedule,
                freeze_bundle_path=arguments.freeze_bundle)
            output = {"campaign_spec": spec, "fault_plan": plan}
        elif arguments.command == "checkpoint":
            output = recorder.checkpoint(arguments.observation)
        elif arguments.command == "project-decisions":
            output = recorder.project_decisions(
                arguments.verified_closure, arguments.decision,
                arguments.clock_observation)
        elif arguments.command == "record-fault":
            output = recorder.record_fault(arguments.observation)
        elif arguments.command == "record-authority":
            output = recorder.record_authority(arguments.observation)
        elif arguments.command == "record-cleanup":
            output = recorder.record_cleanup(arguments.observation)
        else:  # pragma: no cover - argparse owns the command set.
            raise RecorderError("P1_RECORDER_COMMAND_INVALID")
        producer.reopen()
    except RecorderError as error:
        print("hepta_p1_safety_soak_evidence_recorder: FAIL " + error.reason,
              file=sys.stderr)
        return 4
    sys.stdout.buffer.write(canonical_bytes(output))
    values = output if isinstance(output, list) else [output]
    return 3 if any(isinstance(item, dict) and evidence_is_unsafe(item)
                    for item in values) else 0


if __name__ == "__main__":
    raise SystemExit(main())
