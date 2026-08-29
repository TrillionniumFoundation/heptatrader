#!/usr/bin/env python3
"""Enable or disable bounded IB PAPER authority without authorizing LIVE."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePath
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable


IDENTITY_SCHEMA = "hepta.agent-trust-domain-paper-identities.v1"
AUTHORITY_SCHEMA = "hepta.ib-paper-domain-authorizations.v1"
DOMAIN = re.compile(r"[a-z][a-z0-9-]{0,31}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

DEFAULT_AUTHORITY = Path(
    "/etc/heptatrader/hepta-ib-paper-domain-authorizations-v1.json")
PRODUCTION_IDENTITIES_PATH = Path(
    "/etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json")
DEFAULT_IDENTITIES = PRODUCTION_IDENTITIES_PATH
DEFAULT_ENV_ROOT = Path("/etc/heptatrader/trust-domains")
PRODUCTION_DROP_IN_PATH = Path(
    "/etc/systemd/system/hepta-broker-egress-policy.service.d/"
    "20-local-paper.conf")
DEFAULT_DROP_IN = PRODUCTION_DROP_IN_PATH
DEFAULT_GATEWAY_ENV_ROOT = Path("/etc/heptatrader/trust-domains")
BROKER_UNIT = "hepta-broker-egress-policy.service"
HOST_AUTHORITY_DIRECTORY = Path("/run/hepta/ib-paper-host-authority")
HOST_AUTHORITY_LEASE_NAME = "lease.lock"
HOST_AUTHORITY_OWNER_NAME = "owner.v1"
BROKER_ACTIVATION_RESERVATION_SCHEMA = (
    "hepta.local-paper-broker-activation-reservation.v1")
BROKER_ACTIVATION_RESERVATION_STATUS = "PENDING_BROKER_ACTIVE"
BROKER_ACTIVATION_REQUIRED_PREDECESSOR = "DENY_ALL"
BROKER_ACTIVATION_CONSUMED_SCHEMA = (
    "hepta.local-paper-broker-activation-consumed.v1")
BROKER_ACTIVATION_CONSUMED_STATUS = "ACTIVE_BOUNDARY_COMMITTED"
BROKER_ACTIVATION_INTENT_SCHEMA = (
    "hepta.local-paper-broker-activation-commit-intent.v1")
BROKER_ACTIVATION_INTENT_PREFIX = "activation-commit-intent."
BROKER_ACTIVATION_CONSUMED_PREFIX = "activation-consumed."
BROKER_ACTIVATION_ARTIFACT_SUFFIX = ".v1.json"
PAPER_RUNTIME_OWNER_SCHEMA = "hepta.ib-paper-runtime-owner.v1"
PAPER_RUNTIME_OWNER_STATUS = "ACTIVE_RUNTIME_GUARD"
BROKER_BOUNDARY_RECEIPT_PATH = Path(
    "/run/hepta-broker-egress-policy/current-boundary.v1.json")
BROKER_BOUNDARY_RECEIPT_SCHEMA = "hepta.broker-egress-current-boundary.v1"
MAX_BROKER_BOUNDARY_RECEIPT_BYTES = 64 * 1024
BROKER_BOUNDARY_MAXIMUM_AGE_MS = 2_000
MAX_HOST_AUTHORITY_ARTIFACT_BYTES = 4096
WATCH_RECONCILE_TIMER = "hepta-p1-watch-activation-reconcile.timer"
WATCH_RECONCILE_SERVICE = "hepta-p1-watch-activation-reconcile.service"
LOCAL_PAPER_QUOTE_MAX_AGE_MS = "30000"
EXTERNAL_P1_MAX_QUOTE_AGE_MS = 5000
EXTERNAL_P1_PAPER_PROFILE_SHA256 = (
    "sha256:99dd8ab1cd612989906a972abcaad0dd4234d908ea4ce295c0c01a9059604ee4")
EXTERNAL_P1_PAPER_PROFILE_BYTES = 767
ROOT_UID = 0
ROOT_GID = 0
PAPER_CONTROL_GID = 2121
GLOBAL_PAPER_CONTROL_GID = 2003
EXTERNAL_P1_HANDOFF_PATH = Path(
    "/var/lib/hepta/p1-admission/"
    "p1-watch-to-paper-handoff-receipt-v2.json")
EXTERNAL_P1_HANDOFF_SCHEMA = "hepta.p1-watch-to-paper-handoff-receipt.v2"
EXTERNAL_P1_HANDOFF_STATUS = "WATCH_RETIRED_HANDOFF_COMPLETE"
EXTERNAL_P1_HANDOFF_FIELDS = frozenset({
    "schema", "version", "status", "issued_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "producer", "production_mode", "activation_receipt", "p1_audit_receipt",
    "freeze_bundle", "watch_units_inactive", "watch_authority_count",
    "watch_socket_count", "watch_timer_count", "paper_units_inactive",
    "broker_deny_all", "kill_switch_engaged",
    "global_kill_switch_engaged", "identity_count",
    "identity_manifest_sha256", "paper_profile_restored",
    "paper_profile_restoration", "profile_candidate_absent",
    "paper_runtime_profile_hardened", "paper_runtime_profile_hardening",
    "paper_runtime_profile_candidate_absent",
    "crash_recovery_verified",
    "cleanup_residue_count", "paper_authorized", "live_authorized",
    "mutation_authorized", "direct_broker_access",
    "order_submission_authorized", "body_sha256",
})
PROFILE_RESTORATION_FIELDS = frozenset({
    "schema", "version", "status", "target", "dormant_backup",
    "forward_retained_dormant", "retired_watch",
    "forward_transition_receipt", "profile_deployment_receipt",
    "forward_preimage_evidence", "candidate_path", "retired_watch_path",
    "exchange_method", "forward_only_after_exchange",
    "restore_intent_record_sha256", "restore_exchange_record_sha256",
})
PROFILE_FILE_EVIDENCE_FIELDS = frozenset({
    "path", "file_sha256", "bytes", "mode", "uid", "gid", "nlink",
    "device", "inode", "mtime_ns", "ctime_ns",
})
PROFILE_SEALED_EVIDENCE_FIELDS = frozenset({
    *PROFILE_FILE_EVIDENCE_FIELDS, "body_sha256",
})
RUNTIME_PROFILE_HARDENING_FIELDS = frozenset({
    "schema", "version", "status", "target", "legacy_backup",
    "retained_legacy", "candidate_path", "retained_legacy_path",
    "exchange_method", "forward_only_after_exchange",
    "harden_intent_record_sha256", "harden_exchange_record_sha256",
})
DISABLED_IDENTITY_MANIFEST_SHA256 = (
    "sha256:4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435")
DORMANT_PAPER_PROFILE_SHA256 = (
    "sha256:e5866254918ebb23c39c3e3630b9281ab780ad82c2cdb8f63e68749b1f4e9012")
DORMANT_PAPER_PROFILE_BYTES = 878
WATCH_PROFILE_SHA256 = (
    "sha256:ffcde4c46237ecacb3c32603f3aca0ba1a51c5b353b4fd2e5ab2f42ca1470e3f")
WATCH_PROFILE_BYTES = 736
PROFILE_TARGET_PATH = Path("/etc/heptatrader/trust-domains/alpha.env")
EXTERNAL_P1_PAPER_ENV_PATH = Path(
    "/etc/heptatrader/trust-domains/alpha.ib-paper.env")
EXTERNAL_P1_PAPER_ENV_CANDIDATE_PATH = Path(
    "/etc/heptatrader/trust-domains/"
    ".alpha.ib-paper.env.hepta-p1-round114-runtime-harden.candidate")
EXTERNAL_P1_PAPER_ENV_BACKUP_PATH = Path(
    "/var/lib/heptatrader/p1-watch-to-paper-handoff/round114/"
    "legacy-paper-runtime-profile-backup.env")
EXTERNAL_P1_PAPER_ENV_RETAINED_PATH = Path(
    "/var/lib/heptatrader/p1-watch-to-paper-handoff/round114/"
    "retained-legacy-paper-runtime-profile.env")
EXTERNAL_P1_LEGACY_PAPER_PROFILE_SHA256 = (
    "sha256:2537f50ffe51f74e975f452e570d2c8ddaa82e1757955443014f5f28c9170f03")
EXTERNAL_P1_LEGACY_PAPER_PROFILE_BYTES = 776
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
    "/var/lib/heptatrader/p1-watch-profile-receipts/round114-generation22.json")
PROFILE_CANDIDATE_PATH = PROFILE_TARGET_PATH.with_name(
    ".alpha.env.hepta-p1-round114-watch-to-paper.candidate")
PROFILE_RETIRED_WATCH_PATH = Path(
    "/var/lib/heptatrader/p1-watch-to-paper-handoff/round114/"
    "retired-watch-profile.env")
GLOBAL_KILL_SWITCH_PATH = Path("/run/hepta/ib-paper-control/kill-switch")
DOMAIN_KILL_SWITCH_PATH = Path(
    "/run/hepta/ib-paper-control-alpha/kill-switch")
LOCAL_PAPER_STATE_ROOT = Path("/var/lib/hepta-local-ai-paper-agent")
SESSION_ROOT = Path("/run/hepta-agent-alpha/sessions")
SESSION_AUTHORITY_ROOT = LOCAL_PAPER_STATE_ROOT / "session-authority"
EXTERNAL_P1_RESIDUE_PATHS = (
    LOCAL_PAPER_STATE_ROOT / "start-permit.pending.json",
    LOCAL_PAPER_STATE_ROOT / "start-permit.claimed.json",
    LOCAL_PAPER_STATE_ROOT / "start-permit.consumed.json",
    LOCAL_PAPER_STATE_ROOT / "prepare-campaign-transaction.json",
    LOCAL_PAPER_STATE_ROOT / "deployment-evidence-transaction.json",
    LOCAL_PAPER_STATE_ROOT / "legacy-hsl5-paper-cleanup.intent.json",
    Path("/run/hepta-agent-alpha/tools.sock"),
    Path("/run/hepta-tool-gateway-alpha/session-supervisor.sock"),
    Path("/run/hepta-execution-alpha/execution.sock"),
    Path("/run/hepta-execution-alpha/events.sock"),
)
EXTERNAL_P1_INERT_UNITS = (
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
MAX_EXTERNAL_P1_JSON_BYTES = 16 * 1024 * 1024
MAX_EXTERNAL_P1_ENV_BYTES = 64 * 1024
CONTROL_TRANSACTION_SCHEMA = "hepta.local-paper-control-transaction.v1"
CONTROL_TRANSACTION_FIELDS = frozenset({
    "schema", "version", "transaction_id", "operation", "phase",
    "created_at_ms", "updated_at_ms", "domain", "request",
    "request_sha256", "target_identity_manifest_sha256",
    "target_drop_in_sha256", "recovery_record_file_sha256",
    "recovery_record_body_sha256", "body_sha256",
})
CONTROL_OPERATIONS = frozenset({"ENABLE", "DISABLE", "ENABLE_RECOVERY"})
CONTROL_NORMAL_TERMINAL_PHASE = "ACTIVE"
CONTROL_RECOVERY_TERMINAL_PHASE = "RECOVERY_READY"
GUARDIAN_UNIT = "hepta-local-paper-authority@alpha.service"
GUARDIAN_REQUEST_SCHEMA = "hepta.local-paper-authority-request.v1"
GUARDIAN_RUNTIME_SCHEMA = "hepta.local-paper-authority-active.v1"
BROKER_START_PERMIT_SCHEMA = "hepta.local-paper-broker-start-permit.v1"
GUARDIAN_RUNTIME_ROOT = Path("/run/hepta-local-paper-control/alpha")
GUARDIAN_REQUEST_PATH = GUARDIAN_RUNTIME_ROOT / "guardian-request.json"
GUARDIAN_ACTIVE_PATH = GUARDIAN_RUNTIME_ROOT / "active.json"
BROKER_START_PERMIT_PATH = GUARDIAN_RUNTIME_ROOT / "broker-start-permit.json"
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
GUARDIAN_REQUEST_TTL_MS = 30_000
# The execution/preflight pair is socket-activated.  systemd can return from
# the start request before preflight publishes the owner CAS.  Keep the
# bearer finite, but long enough for the bounded readiness snapshot and
# handoff; the verifier below still stops before this permit expires.
BROKER_START_PERMIT_TTL_MS = 45_000
RUNTIME_OWNER_ADOPTION_MAX_WAIT_MS = 40_000
RUNTIME_OWNER_ADOPTION_POLL_MS = 50
# An abnormal guardian exit must stop the PAPER consumers and broker before
# replacing their authority inputs.  Those systemd stops are allowed to wait
# for bounded unit shutdown, but they must neither starve the guardian
# watchdog nor keep an authorized input alive forever.  Keep this deadline
# below the guardian unit's two-minute stop budget so ExecStopPost retains a
# final independent fail-close opportunity.
GUARDIAN_FAIL_CLOSE_MAX_SECONDS = 90.0
GUARDIAN_REQUEST_FIELDS = frozenset({
    "schema", "version", "request_id", "operation", "issued_at_ms",
    "expires_at_ms", "boot_id", "requester_pid", "requester_start_ticks",
    "requester_exe_sha256", "requester_argv_sha256",
    "control_image_sha256", "domain", "arguments", "body_sha256",
})
GUARDIAN_RUNTIME_FIELDS = frozenset({
    "schema", "version", "status", "recorded_at_ms", "boot_id",
    "guardian_pid", "guardian_start_ticks", "guardian_exe_sha256",
    "guardian_argv_sha256", "control_image_sha256", "guardian_request_id",
    "domain", "transaction_id", "operation", "phase", "request_sha256",
    "target_identity_manifest_sha256", "target_drop_in_sha256", "mode",
    "body_sha256",
})
BROKER_START_PERMIT_FIELDS = frozenset({
    "schema", "version", "issued_at_ms", "expires_at_ms", "boot_id",
    "guardian_pid", "guardian_start_ticks", "guardian_exe_sha256",
    "guardian_argv_sha256", "control_image_sha256", "guardian_request_id",
    "domain", "transaction_id", "operation", "phase", "request_sha256",
    "target_identity_manifest_sha256", "target_drop_in_sha256",
    "body_sha256",
})
BROKER_ACTIVATION_RESERVATION_FIELDS = frozenset({
    "schema", "version", "status", "activation_id", "issued_at_ms",
    "expires_at_ms", "boot_id", "guardian_pid", "guardian_start_ticks",
    "guardian_exe_sha256", "guardian_argv_sha256",
    "control_image_sha256", "guardian_request_id", "domain",
    "transaction_id", "operation", "phase", "request_sha256",
    "target_identity_manifest_sha256", "target_drop_in_sha256",
    "broker_start_permit_file_sha256",
    "broker_start_permit_body_sha256", "required_pre_activation_boundary",
    "paper_only", "live_authorized", "body_sha256",
})
BROKER_ACTIVATION_CONSUMED_FIELDS = frozenset({
    "schema", "version", "status", "activation_id", "consumed_at_ms",
    "boot_id", "reservation_file_sha256", "reservation_body_sha256",
    "broker_start_permit_file_sha256",
    "broker_start_permit_body_sha256", "guardian_request_id", "domain",
    "transaction_id", "operation", "phase", "request_sha256",
    "target_identity_manifest_sha256", "target_drop_in_sha256",
    "control_image_sha256", "required_pre_activation_boundary",
    "pre_activation_boundary_state_sha256", "active_boundary_status",
    "active_boundary_state_sha256", "paper_authorized", "live_authorized",
    "body_sha256",
})
BROKER_ACTIVATION_INTENT_FIELDS = frozenset({
    "schema", "version", "status", "activation_id", "recorded_at_ms",
    "boot_id", "reservation_file_sha256", "reservation_body_sha256",
    "broker_start_permit_file_sha256",
    "broker_start_permit_body_sha256", "target_identity_manifest_sha256",
    "target_drop_in_sha256", "required_pre_activation_boundary",
    "pre_activation_boundary_state_sha256", "paper_authorized",
    "live_authorized", "body_sha256",
})
PAPER_RUNTIME_OWNER_FIELDS = frozenset({
    "schema", "version", "status", "adopted_at_ms", "boot_id", "domain",
    "activation_id", "transaction_id", "operation", "phase",
    "guardian_request_id", "request_sha256", "reservation_file_sha256",
    "reservation_body_sha256", "activation_consumed_file_sha256",
    "activation_consumed_body_sha256", "broker_start_permit_file_sha256",
    "broker_start_permit_body_sha256",
    "pre_activation_boundary_state_sha256", "active_boundary_state_sha256",
    "target_identity_manifest_sha256", "target_drop_in_sha256",
    "execution_identity", "execution_uid", "execution_gid",
    "control_directory", "kill_switch_marker", "guard_pid",
    "guard_start_ticks", "guard_exe_sha256", "guard_argv_sha256",
    "mutation_scope", "paper_authorized", "live_authorized", "body_sha256",
})
BROKER_BOUNDARY_RECEIPT_FIELDS = frozenset({
    "schema", "version", "status", "boot_id", "generation",
    "publisher_pid", "publisher_start_ticks", "observed_at_ms",
    "observed_monotonic_ns", "state", "family", "table", "chain",
    "guard_chain", "protected_tcp_destination_ports",
    "protected_port_count", "authorized_connector_count",
    "authorized_uids", "authorized_connectors", "paper_authorized",
    "live_authorized", "source_policy_sha256",
    "identity_manifest_sha256", "effective_policy_sha256",
    "table_semantic_sha256", "state_sha256", "source_fingerprints",
    "body_sha256",
})
RECOVERY_AUTHORITY_SCHEMA = (
    "hepta.local-paper-control-recovery-authority.v1")
RECOVERY_AUTHORITY_STATUS = "REDUCE_ONLY_RECOVERY_REQUIRED"
RECOVERY_AUTHORITY_FIELDS = frozenset({
    "schema", "version", "status", "recovery_id", "recorded_at_ms",
    "domain", "campaign_id", "suspension_id", "reason_code",
    "source_baseline_sha256", "watch_handoff_receipt_path",
    "watch_handoff_receipt_file_sha256",
    "watch_handoff_receipt_body_sha256", "recovery_required",
    "reduce_only", "paper_only", "live_authorized", "entry_authorized",
    "order_submission_authorized", "session_provision_authorized",
    "policy_preimage_reference", "incident_state_reference",
    "mutation_lineage_reference", "session_owner_set_reference",
    "session_owner_count", "all_original_session_owners_bound",
    "body_sha256",
})
RECOVERY_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "schema", "status", "bytes",
    "mode", "uid", "gid", "nlink",
})
RECOVERY_REFERENCE_SPECS = {
    "policy_preimage_reference": (
        "hepta.local-paper-recovery-policy-preimage.v1",
        "RECOVERY_POLICY_PREIMAGE_FROZEN"),
    "incident_state_reference": (
        "hepta.local-paper-recovery-incident-state.v1",
        "RECOVERY_INCIDENT_STATE_FROZEN"),
    "mutation_lineage_reference": (
        "hepta.local-paper-recovery-mutation-lineage.v1",
        "RECOVERY_MUTATION_LINEAGE_FROZEN"),
    "session_owner_set_reference": (
        "hepta.local-paper-recovery-session-owner-set.v1",
        "RECOVERY_SESSION_OWNER_SET_FROZEN"),
}
RECOVERY_AUTHORITY_PATH = (
    LOCAL_PAPER_STATE_ROOT / "local-paper-control-recovery-authority.json")
RECOVERY_COMPLETION_SCHEMA = (
    "hepta.local-paper-control-recovery-completion.v3")
RECOVERY_COMPLETION_STATUS = "REDUCE_ONLY_RECOVERY_TERMINAL_FLAT"
RECOVERY_COMPLETION_FIELDS = frozenset({
    "schema", "version", "status", "recovery_id", "completed_at_ms",
    "domain", "campaign_id", "suspension_id", "source_baseline_sha256",
    "recovery_authority_file_sha256", "recovery_authority_body_sha256",
    "authoritative_flat_receipt_reference", "finalization_id",
    "expected_owner_set_sha256", "expected_owner_count",
    "preliminary_finalization_receipt_sha256",
    "terminal_ack_receipt_sha256", "terminal_latch_sha256",
    "session_owner_count",
    "all_original_session_owners_closed",
    "terminal_acknowledged", "terminal_runtime_replay_verified",
    "hsl_owner_purged", "position_quantity",
    "gross_absolute_position", "active_order_count", "paper_only",
    "live_authorized", "body_sha256",
})
RECOVERY_TERMINAL_FLAT_SCHEMA = (
    "hepta.local-ai-paper-external-recovery-terminal-flat.v3")
RECOVERY_TERMINAL_FLAT_STATUS = "EXTERNAL_RECOVERY_TERMINAL_FLAT"
RECOVERY_TERMINAL_FLAT_FIELDS = frozenset({
    "schema", "version", "status", "completed_at_ms", "recovery_id",
    "domain", "campaign_id", "suspension_id", "source_baseline_sha256",
    "finalization_id", "expected_owner_set_sha256", "expected_owner_count",
    "preliminary_finalization_receipt_sha256",
    "preliminary_finalization_receipt", "preliminary_finalization_result",
    "terminal_ack_receipt_sha256", "terminal_ack_receipt",
    "terminal_ack_result", "terminal_latch_sha256", "session_owner_count",
    "session_owner_token_sha256s", "all_original_session_owners_closed",
    "terminal_acknowledged", "terminal_runtime_replay_verified",
    "hsl_owner_purged", "position_quantity",
    "gross_absolute_position", "active_order_count",
    "pre_finalization_diagnostic_zero_exposure_proofs", "paper_only",
    "live_authorized", "body_sha256",
})
PRELIMINARY_FINALIZATION_RESULT_FIELDS = frozenset({
    "accepted", "reason_code", "lease_generation",
    "paper_finalization_state", "paper_finalization_required",
    "recovery_id", "finalization_id",
    "expected_owner_set_sha256", "expected_owner_count",
    "owner_token_sha256", "finalization_receipt_sha256",
    "finalization_receipt", "owner_audit_authoritative",
    "owner_audit_complete", "owner_active_order_count",
    "owner_uncertain_command_count", "owner_account",
    "owner_execution_domain", "execution_service_epoch",
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
TERMINAL_ACK_RESULT_FIELDS = frozenset({
    *PRELIMINARY_FINALIZATION_RESULT_FIELDS,
    "preliminary_finalization_receipt_sha256",
    "terminalization_service_epoch",
    "terminalization_service_fencing_generation",
    "terminalization_generation", "terminal_latch_sha256",
    "execution_mutation_gate_closed", "broker_transport_connected",
    "broker_event_ingress_halted", "broker_callback_queue_drained",
    "broker_callbacks_in_flight", "broker_reconnect_permitted",
    "terminal_latch_durable", "terminal_runtime_latch_loaded",
    "terminal_runtime_verified", "terminal_replay",
})
PRELIMINARY_FINALIZATION_RECEIPT_KEYS = (
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
    "schema", "version", "status", "recovery_id", "finalization_id",
    "expected_owner_set_sha256", "expected_owner_count",
    "owner_set_canonical_hex", "preliminary_finalization_receipt_sha256",
    "owner_account", "owner_execution_domain", "execution_service_epoch",
    "execution_service_fencing_generation", "terminalization_generation",
    "terminal_latch_sha256", "execution_mutation_gate_closed",
    "broker_transport_connected", "broker_event_ingress_halted",
    "broker_callback_queue_drained", "broker_callbacks_in_flight",
    "broker_reconnect_permitted", "terminal_latch_durable",
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
RECOVERY_COMPLETION_PATH = (
    LOCAL_PAPER_STATE_ROOT / "local-paper-control-recovery-completion.json")
CONTROL_STOP_UNITS = (
    "hepta-local-ai-paper-agent.service",
    "hepta-ib-paper-campaign-operator@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.socket",
    "hepta-local-paper-session-renew.timer",
    "hepta-local-paper-session-renew.service",
    "hepta-local-paper-supervisor.timer",
    "hepta-local-paper-supervisor.service",
    "hepta-local-ai-paper-24h-stop.timer",
    "hepta-local-ai-paper-24h-stop.service",
    "hepta-local-ai-paper-end-flat-retry.timer",
    "hepta-tool-gateway@alpha.service",
    "hepta-tool-gateway@alpha.socket",
    "hepta-tool-session-supervisor@alpha.socket",
    "hepta-ib-paper-domain-preflight@alpha.service",
    "hepta-execution-ib-paper@alpha.service",
    "hepta-execution-ib-paper@alpha.socket",
    "hepta-execution-events-ib-paper@alpha.socket",
    "hepta-execution-simulator@alpha.service",
    "hepta-execution-simulator@alpha.socket",
    "hepta-execution-events-simulator@alpha.socket",
)
RECOVERY_START_UNITS = (
    "hepta-ib-paper-domain-preflight@alpha.service",
    "hepta-execution-ib-paper@alpha.service",
    "hepta-tool-gateway@alpha.socket",
    "hepta-tool-session-supervisor@alpha.socket",
    "hepta-tool-gateway@alpha.service",
)
RECOVERY_FORBIDDEN_UNITS = tuple(
    unit for unit in CONTROL_STOP_UNITS if unit not in RECOVERY_START_UNITS)


def _child_environment() -> dict[str, str]:
    """Do not pass this guardian's systemd notify contract to child tools.

    The guardian itself is a ``Type=notify`` service.  If these variables are
    inherited by a child ``systemctl``/helper process, systemd can attribute
    the child's datagrams to a non-main PID and emit misleading watchdog/
    readiness failures.  Keep the guardian's environment intact while
    isolating subprocesses from its notify socket and watchdog deadline.
    """
    environment = os.environ.copy()
    environment.pop("NOTIFY_SOCKET", None)
    environment.pop("WATCHDOG_USEC", None)
    return environment


def _control_stop_units(domain: str = "alpha") -> tuple[str, ...]:
    """Return the stop set, excluding only a verified caller self-unit.

    The deadline service invokes this image from inside its own oneshot unit.
    Stopping that unit from its child sends SIGTERM to the end-flat process
    before it can publish a terminal receipt.  Match systemd's invocation ID
    against the unit's recorded invocation, and only then omit that one unit.
    Any probe failure keeps the complete stop set (fail closed).
    """
    units = tuple(
        unit.replace("@alpha", f"@{domain}") for unit in CONTROL_STOP_UNITS)
    invocation_id = os.environ.get("INVOCATION_ID", "")
    if not re.fullmatch(r"[0-9a-f]{32}", invocation_id):
        return units
    caller = f"hepta-local-ai-paper-24h-stop.service"
    probe = subprocess.run(
        ["/usr/bin/systemctl", "show", caller,
         "-p", "InvocationID", "--value"],
        check=False, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=_child_environment())
    if probe.returncode == 0 and probe.stdout.strip() == invocation_id:
        return tuple(unit for unit in units if unit != caller)
    return units


class LocalPaperError(RuntimeError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LocalPaperError(f"invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise LocalPaperError(f"JSON root is not an object: {path}")
    return value


def _pretty_json(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, indent=2, sort_keys=True,
        allow_nan=False) + "\n").encode("ascii")


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False) + "\n").encode("ascii")


def _sealed_document(body: dict[str, Any]) -> dict[str, Any]:
    if "body_sha256" in body:
        raise LocalPaperError("sealed document body contains seal")
    return {**body, "body_sha256": _sha256(_canonical_json(body))}


def _validate_canonical_seal(
        payload: bytes, document: Any, failure: str,
) -> dict[str, Any]:
    if not isinstance(document, dict) or payload != _canonical_json(document):
        raise LocalPaperError(failure)
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    if (
            not isinstance(claimed, str) or DIGEST.fullmatch(claimed) is None or
            _sha256(_canonical_json(body)) != claimed):
        raise LocalPaperError(failure)
    return document


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_atomic_replace_parent(
        path: Path, *, uid: int = ROOT_UID, gid: int = ROOT_GID,
        mode: int = 0o755,
) -> None:
    """Require a pre-created, non-symlink parent for sibling-temp writes."""
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise LocalPaperError(
            f"atomic replacement parent unsafe: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        path_metadata = os.lstat(path)
        if (
                not stat.S_ISDIR(metadata.st_mode) or
                not stat.S_ISDIR(path_metadata.st_mode) or
                _metadata_identity(metadata) !=
                    _metadata_identity(path_metadata) or
                metadata.st_uid != uid or metadata.st_gid != gid or
                stat.S_IMODE(metadata.st_mode) != mode or
                metadata.st_nlink < 2):
            raise LocalPaperError(
                f"atomic replacement parent unsafe: {path}")
    finally:
        os.close(descriptor)


def _require_production_mutation_parents(
        identities_path: Path, drop_in_path: Path,
) -> None:
    if (identities_path != PRODUCTION_IDENTITIES_PATH or
            drop_in_path != PRODUCTION_DROP_IN_PATH):
        return
    _require_atomic_replace_parent(PRODUCTION_IDENTITIES_PATH.parent)
    _require_atomic_replace_parent(PRODUCTION_DROP_IN_PATH.parent)


def _ensure_private_root(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = os.lstat(path)
    if (
            not stat.S_ISDIR(metadata.st_mode) or
            metadata.st_uid != ROOT_UID or metadata.st_gid != ROOT_GID or
            stat.S_IMODE(metadata.st_mode) & 0o077):
        raise LocalPaperError("local PAPER control state root unsafe")


def _transaction_path(root: Path) -> Path:
    return root / "local-paper-control-transaction.json"


def _transaction_lock_path(root: Path) -> Path:
    return root / "local-paper-control-transaction.lock"


@contextmanager
def _control_transaction_lock(root: Path):  # type: ignore[no-untyped-def]
    _ensure_private_root(root)
    path = _transaction_lock_path(root)
    flags = os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, ROOT_UID, ROOT_GID)
        os.fsync(descriptor)
        _fsync_directory(root)
    except FileExistsError:
        descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        path_metadata = os.lstat(path)
        if (
                _metadata_identity(metadata) !=
                    _metadata_identity(path_metadata) or
                not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != ROOT_UID or metadata.st_gid != ROOT_GID or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise LocalPaperError("local PAPER control lock unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _host_authority_directory_identity(
        metadata: os.stat_result,
) -> tuple[int, ...]:
    return tuple(int(getattr(metadata, field)) for field in (
        "st_dev", "st_ino", "st_mode", "st_uid", "st_gid"))


def _host_authority_file_identity(
        metadata: os.stat_result,
) -> tuple[int, ...]:
    return tuple(int(getattr(metadata, field)) for field in (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size"))


def _validate_host_authority_directory(metadata: os.stat_result) -> None:
    if (
            not stat.S_ISDIR(metadata.st_mode) or
            metadata.st_uid != ROOT_UID or metadata.st_gid != ROOT_GID or
            stat.S_IMODE(metadata.st_mode) != 0o700):
        raise LocalPaperError("host PAPER authority directory unsafe")


def _validate_host_authority_lease_file(metadata: os.stat_result) -> None:
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != ROOT_UID or metadata.st_gid != ROOT_GID or
            stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size != 0):
        raise LocalPaperError("host PAPER authority lease unsafe")


def _prepare_host_authority_directory(root: Path) -> None:
    if not root.is_absolute():
        raise LocalPaperError("host PAPER authority directory unsafe")
    created = False
    try:
        os.mkdir(root, 0o700)
        created = True
    except FileExistsError:
        pass
    except OSError as error:
        raise LocalPaperError(
            "host PAPER authority directory unavailable") from error
    if created:
        os.chmod(root, 0o700, follow_symlinks=False)
        os.chown(root, ROOT_UID, ROOT_GID, follow_symlinks=False)
        _fsync_directory(root.parent)
    try:
        if root.resolve(strict=True) != root:
            raise LocalPaperError("host PAPER authority directory unsafe")
        _validate_host_authority_directory(os.lstat(root))
    except OSError as error:
        raise LocalPaperError(
            "host PAPER authority directory unsafe") from error


def _validate_live_host_authority_lease(
        lease: dict[str, Any],
) -> None:
    root = lease.get("root")
    directory = lease.get("directory")
    descriptor = lease.get("descriptor")
    if (
            not isinstance(root, Path) or type(directory) is not int or
            type(descriptor) is not int):
        raise LocalPaperError("host PAPER authority lease invalid")
    try:
        current_directory = os.fstat(directory)
        current_lease = os.fstat(descriptor)
        named_lease = os.stat(
            HOST_AUTHORITY_LEASE_NAME, dir_fd=directory,
            follow_symlinks=False)
        reopened = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
        try:
            reopened_directory = os.fstat(reopened)
        finally:
            os.close(reopened)
        _validate_host_authority_directory(current_directory)
        _validate_host_authority_directory(reopened_directory)
        _validate_host_authority_lease_file(current_lease)
        _validate_host_authority_lease_file(named_lease)
        if (
                _host_authority_directory_identity(current_directory) !=
                    lease.get("directory_identity") or
                _host_authority_directory_identity(reopened_directory) !=
                    lease.get("directory_identity") or
                _host_authority_file_identity(current_lease) !=
                    lease.get("lease_identity") or
                _host_authority_file_identity(named_lease) !=
                    lease.get("lease_identity")):
            raise LocalPaperError("host PAPER authority lease rebound")
    except OSError as error:
        raise LocalPaperError("host PAPER authority lease rebound") from error


@contextmanager
def _host_authority_lease(root: Path):  # type: ignore[no-untyped-def]
    _prepare_host_authority_directory(root)
    directory = -1
    descriptor = -1
    locked = False
    failure: BaseException | None = None
    try:
        directory = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
        directory_metadata = os.fstat(directory)
        _validate_host_authority_directory(directory_metadata)
        flags = os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(
                HOST_AUTHORITY_LEASE_NAME,
                flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
            os.fchmod(descriptor, 0o600)
            os.fchown(descriptor, ROOT_UID, ROOT_GID)
            os.fsync(descriptor)
            os.fsync(directory)
        except FileExistsError:
            descriptor = os.open(
                HOST_AUTHORITY_LEASE_NAME, flags, dir_fd=directory)
        lease_metadata = os.fstat(descriptor)
        named_metadata = os.stat(
            HOST_AUTHORITY_LEASE_NAME, dir_fd=directory,
            follow_symlinks=False)
        _validate_host_authority_lease_file(lease_metadata)
        _validate_host_authority_lease_file(named_metadata)
        if (_host_authority_file_identity(lease_metadata) !=
                _host_authority_file_identity(named_metadata)):
            raise LocalPaperError("host PAPER authority lease rebound")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise LocalPaperError("host PAPER authority lease busy") \
                    from error
            raise
        lease = {
            "root": root, "directory": directory, "descriptor": descriptor,
            "directory_identity":
                _host_authority_directory_identity(directory_metadata),
            "lease_identity": _host_authority_file_identity(lease_metadata),
        }
        _validate_live_host_authority_lease(lease)
        try:
            yield lease
        except BaseException as error:
            failure = error
        try:
            _validate_live_host_authority_lease(lease)
        except BaseException as error:
            if failure is None:
                failure = error
    except BaseException as error:
        if failure is None:
            failure = error
    finally:
        if descriptor >= 0:
            if locked:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except BaseException as error:
                    if failure is None:
                        failure = error
            try:
                os.close(descriptor)
            except BaseException as error:
                if failure is None:
                    failure = error
        if directory >= 0:
            try:
                os.close(directory)
            except BaseException as error:
                if failure is None:
                    failure = error
    if failure is not None:
        raise failure


def _host_authority_owner_payload(
        lease: dict[str, Any],
) -> bytes | None:
    _validate_live_host_authority_lease(lease)
    directory = int(lease["directory"])
    try:
        before = os.stat(
            HOST_AUTHORITY_OWNER_NAME, dir_fd=directory,
            follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise LocalPaperError("host PAPER authority owner unsafe") from error
    if (
            not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            before.st_uid != ROOT_UID or before.st_gid != ROOT_GID or
            stat.S_IMODE(before.st_mode) != 0o600 or
            not 1 <= before.st_size <= MAX_HOST_AUTHORITY_ARTIFACT_BYTES):
        raise LocalPaperError("host PAPER authority owner unsafe")
    descriptor = -1
    try:
        descriptor = os.open(
            HOST_AUTHORITY_OWNER_NAME,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK |
            getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
        opened = os.fstat(descriptor)
        if (_host_authority_file_identity(opened) !=
                _host_authority_file_identity(before)):
            raise LocalPaperError("host PAPER authority owner drifted")
        payload = bytearray()
        while len(payload) <= MAX_HOST_AUTHORITY_ARTIFACT_BYTES:
            chunk = os.read(
                descriptor,
                min(4096, MAX_HOST_AUTHORITY_ARTIFACT_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        named_after = os.stat(
            HOST_AUTHORITY_OWNER_NAME, dir_fd=directory,
            follow_symlinks=False)
        if (
                len(payload) > MAX_HOST_AUTHORITY_ARTIFACT_BYTES or
                _host_authority_file_identity(opened) !=
                    _host_authority_file_identity(after) or
                _host_authority_file_identity(after) !=
                    _host_authority_file_identity(named_after) or
                len(payload) != after.st_size):
            raise LocalPaperError("host PAPER authority owner drifted")
        return bytes(payload)
    except OSError as error:
        raise LocalPaperError("host PAPER authority owner unsafe") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_host_authority_artifact(
        lease: dict[str, Any], name: str, *, maximum: int,
) -> tuple[bytes, os.stat_result]:
    if (
            not isinstance(name, str) or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}", name) is None or
            not 1 <= maximum <= MAX_BROKER_BOUNDARY_RECEIPT_BYTES):
        raise LocalPaperError("host PAPER authority artifact path invalid")
    _validate_live_host_authority_lease(lease)
    directory = int(lease["directory"])
    descriptor = -1
    try:
        before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != ROOT_UID or before.st_gid != ROOT_GID or
                stat.S_IMODE(before.st_mode) != 0o600 or
                not 1 <= before.st_size <= maximum):
            raise LocalPaperError("host PAPER authority artifact unsafe")
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK |
            getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
        opened = os.fstat(descriptor)
        if (_host_authority_file_identity(opened) !=
                _host_authority_file_identity(before)):
            raise LocalPaperError("host PAPER authority artifact drifted")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(
                descriptor, min(4096, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        named_after = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (
                len(payload) > maximum or len(payload) != after.st_size or
                _host_authority_file_identity(opened) !=
                    _host_authority_file_identity(after) or
                _host_authority_file_identity(after) !=
                    _host_authority_file_identity(named_after)):
            raise LocalPaperError("host PAPER authority artifact drifted")
        return bytes(payload), after
    except FileNotFoundError as error:
        raise LocalPaperError("host PAPER authority artifact missing") from error
    except OSError as error:
        raise LocalPaperError("host PAPER authority artifact unsafe") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_host_authority_owner_absent(lease: dict[str, Any]) -> None:
    if _host_authority_owner_payload(lease) is not None:
        raise LocalPaperError("host PAPER authority owner active")


def _publish_host_authority_owner(
        lease: dict[str, Any], payload: bytes,
) -> None:
    if not 1 <= len(payload) <= MAX_HOST_AUTHORITY_ARTIFACT_BYTES:
        raise LocalPaperError("host PAPER activation reservation invalid")
    _require_host_authority_owner_absent(lease)
    directory = int(lease["directory"])
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            HOST_AUTHORITY_OWNER_NAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        created = True
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, ROOT_UID, ROOT_GID)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise LocalPaperError(
                    "host PAPER activation reservation write failed")
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
                not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != ROOT_UID or metadata.st_gid != ROOT_GID or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                metadata.st_size != len(payload)):
            raise LocalPaperError(
                "host PAPER activation reservation metadata invalid")
    except FileExistsError as error:
        raise LocalPaperError("host PAPER authority owner active") from error
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        if created:
            try:
                os.unlink(HOST_AUTHORITY_OWNER_NAME, dir_fd=directory)
                os.fsync(directory)
            except FileNotFoundError:
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.fsync(directory)
    if _host_authority_owner_payload(lease) != payload:
        raise LocalPaperError("host PAPER activation reservation drifted")


def _remove_exact_host_authority_owner(
        lease: dict[str, Any], expected_payload: bytes, *, absent_ok: bool,
) -> bool:
    payload = _host_authority_owner_payload(lease)
    if payload is None:
        if absent_ok:
            return False
        raise LocalPaperError("host PAPER activation reservation missing")
    if payload != expected_payload:
        raise LocalPaperError("host PAPER authority owner changed")
    directory = int(lease["directory"])
    os.unlink(HOST_AUTHORITY_OWNER_NAME, dir_fd=directory)
    os.fsync(directory)
    if _host_authority_owner_payload(lease) is not None:
        raise LocalPaperError("host PAPER activation reservation retained")
    return True


def _write_root_transaction(
        path: Path, payload: bytes, *, exclusive: bool,
) -> None:
    _ensure_private_root(path.parent)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, ROOT_UID, ROOT_GID)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise LocalPaperError("local PAPER control WAL write failed")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        if exclusive:
            os.link(temporary, path, follow_symlinks=False)
            temporary.unlink()
        else:
            os.replace(temporary, path)
        os.chmod(path, 0o600, follow_symlinks=False)
        os.chown(path, ROOT_UID, ROOT_GID, follow_symlinks=False)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _load_control_transaction(path: Path) -> dict[str, Any] | None:
    try:
        payload, _metadata = _secure_read(path, mode=0o600)
    except FileNotFoundError:
        return None
    except LocalPaperError as error:
        if not path.exists() and not path.is_symlink():
            return None
        raise LocalPaperError("local PAPER control WAL unsafe") from error
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalPaperError("local PAPER control WAL invalid") from error
    document = _validate_canonical_seal(
        payload, value, "local PAPER control WAL invalid")
    operation = document.get("operation")
    phase = document.get("phase")
    common_phase = (
        phase in {"BEGIN", "COMPLETE"} or
        (isinstance(phase, str) and re.fullmatch(
            r"BEFORE_[0-9]{3}_[A-Z0-9_]{1,112}", phase) is not None))
    operation_phase_valid = (
        (operation == "ENABLE" and (
            common_phase or phase == CONTROL_NORMAL_TERMINAL_PHASE)) or
        (operation == "DISABLE" and common_phase) or
        (operation == "ENABLE_RECOVERY" and (
            common_phase or phase in {
                CONTROL_RECOVERY_TERMINAL_PHASE,
                "RECOVERY_FAILED_DENY_ALL",
                "RECOVERY_RECONCILED_DENY_ALL"})))
    if (
            set(document) != CONTROL_TRANSACTION_FIELDS or
            document.get("schema") != CONTROL_TRANSACTION_SCHEMA or
            document.get("version") != 1 or
            operation not in CONTROL_OPERATIONS or
            document.get("domain") != "alpha" or
            not isinstance(document.get("transaction_id"), str) or
            re.fullmatch(r"[0-9a-f]{32}", document["transaction_id"]) is None or
            not operation_phase_valid or
            type(document.get("created_at_ms")) is not int or
            type(document.get("updated_at_ms")) is not int or
            document["created_at_ms"] <= 0 or
            document["updated_at_ms"] < document["created_at_ms"] or
            not isinstance(document.get("request"), dict) or
            document.get("request_sha256") !=
                _sha256(_canonical_json(document["request"])) or
            any(value is not None and (
                not isinstance(value, str) or DIGEST.fullmatch(value) is None)
                for value in (
                    document.get("target_identity_manifest_sha256"),
                    document.get("target_drop_in_sha256"),
                    document.get("recovery_record_file_sha256"),
                    document.get("recovery_record_body_sha256")))):
        raise LocalPaperError("local PAPER control WAL invalid")
    if ((document["operation"] == "ENABLE_RECOVERY") !=
            (document.get("recovery_record_file_sha256") is not None and
             document.get("recovery_record_body_sha256") is not None)):
        raise LocalPaperError("local PAPER control WAL invalid")
    if (
            (operation in {"ENABLE", "ENABLE_RECOVERY"} and any(
                not isinstance(document.get(field), str) or
                DIGEST.fullmatch(document[field]) is None
                for field in (
                    "target_identity_manifest_sha256",
                    "target_drop_in_sha256"))) or
            (operation == "DISABLE" and (
                not isinstance(document.get(
                    "target_identity_manifest_sha256"), str) or
                DIGEST.fullmatch(
                    document["target_identity_manifest_sha256"]) is None or
                document.get("target_drop_in_sha256") is not None))):
        raise LocalPaperError("local PAPER control WAL invalid")
    return document


def _new_control_transaction(
        *, path: Path, operation: str, request: dict[str, Any],
        target_identity_manifest_sha256: str | None,
        target_drop_in_sha256: str | None,
        recovery_record_file_sha256: str | None = None,
        recovery_record_body_sha256: str | None = None,
        now_ms: int | None = None,
) -> dict[str, Any]:
    if operation not in CONTROL_OPERATIONS:
        raise LocalPaperError("local PAPER control operation invalid")
    timestamp = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    body = {
        "schema": CONTROL_TRANSACTION_SCHEMA, "version": 1,
        "transaction_id": secrets.token_hex(16), "operation": operation,
        "phase": "BEGIN", "created_at_ms": timestamp,
        "updated_at_ms": timestamp, "domain": "alpha", "request": request,
        "request_sha256": _sha256(_canonical_json(request)),
        "target_identity_manifest_sha256":
            target_identity_manifest_sha256,
        "target_drop_in_sha256": target_drop_in_sha256,
        "recovery_record_file_sha256": recovery_record_file_sha256,
        "recovery_record_body_sha256": recovery_record_body_sha256,
    }
    document = _sealed_document(body)
    _write_root_transaction(path, _canonical_json(document), exclusive=True)
    return document


def _persist_control_phase(
        transaction: dict[str, Any], path: Path, phase: str,
) -> dict[str, Any]:
    if re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", phase) is None:
        raise LocalPaperError("local PAPER control phase invalid")
    body = {key: value for key, value in transaction.items()
            if key != "body_sha256"}
    body["phase"] = phase
    body["updated_at_ms"] = max(
        int(body["created_at_ms"]), time.time_ns() // 1_000_000)
    updated = _sealed_document(body)
    _write_root_transaction(path, _canonical_json(updated), exclusive=False)
    return updated


def _complete_control_transaction(
        transaction: dict[str, Any], path: Path,
) -> None:
    if transaction.get("operation") == "ENABLE_RECOVERY":
        raise LocalPaperError(
            "recovery WAL requires terminal-flat completion evidence")
    completed = _persist_control_phase(transaction, path, "COMPLETE")
    current = _load_control_transaction(path)
    if current != completed:
        raise LocalPaperError("local PAPER control WAL drifted before cleanup")
    path.unlink()
    _fsync_directory(path.parent)


def _complete_recovery_control_transaction(
        transaction: dict[str, Any], path: Path, *,
        recovery: dict[str, Any], completion: dict[str, Any],
) -> None:
    if (
            transaction.get("operation") != "ENABLE_RECOVERY" or
            completion.get("status") != RECOVERY_COMPLETION_STATUS or
            completion.get("recovery_id") != recovery.get("recovery_id") or
            completion.get("campaign_id") != recovery.get("campaign_id") or
            completion.get("suspension_id") != recovery.get("suspension_id") or
            completion.get("all_original_session_owners_closed") is not True or
            completion.get("terminal_acknowledged") is not True or
            completion.get("terminal_runtime_replay_verified") is not True or
            completion.get("hsl_owner_purged") is not True or
            completion.get("position_quantity") != "0" or
            completion.get("gross_absolute_position") != "0" or
            completion.get("active_order_count") != 0):
        raise LocalPaperError(
            "recovery WAL terminal-flat completion evidence invalid")
    completed = _persist_control_phase(transaction, path, "COMPLETE")
    current = _load_control_transaction(path)
    if current != completed:
        raise LocalPaperError("recovery WAL drifted before cleanup")
    path.unlink()
    _fsync_directory(path.parent)


def _same_transaction_request(
        transaction: dict[str, Any], operation: str,
        request: dict[str, Any],
) -> bool:
    return (
        transaction.get("operation") == operation and
        transaction.get("request") == request and
        transaction.get("request_sha256") == _sha256(_canonical_json(request)))


def _read_boot_id() -> str:
    try:
        value = BOOT_ID_PATH.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise LocalPaperError("local PAPER boot identity unavailable") from error
    if re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}", value) is None:
        raise LocalPaperError("local PAPER boot identity invalid")
    return value


def _hash_open_descriptor(descriptor: int, maximum: int = 256 << 20) -> str:
    digest = hashlib.sha256()
    total = 0
    os.lseek(descriptor, 0, os.SEEK_SET)
    while total <= maximum:
        chunk = os.read(descriptor, min(1 << 20, maximum + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        digest.update(chunk)
    if total > maximum:
        raise LocalPaperError("local PAPER process executable too large")
    return "sha256:" + digest.hexdigest()


def _control_image_sha256() -> str:
    path = Path(__file__).resolve(strict=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        digest = _hash_open_descriptor(descriptor)
        after = os.fstat(descriptor)
        if (
                _metadata_identity(before) != _metadata_identity(after) or
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != ROOT_UID or before.st_gid != ROOT_GID or
                stat.S_IMODE(before.st_mode) not in {
                    0o400, 0o500, 0o600, 0o644, 0o755}):
            raise LocalPaperError("local PAPER control image unsafe")
        return digest
    except OSError as error:
        raise LocalPaperError("local PAPER control image unavailable") from error
    finally:
        os.close(descriptor)


def _process_argv(pid: int) -> tuple[list[str], str]:
    try:
        payload = Path(f"/proc/{pid}/cmdline").read_bytes()
        values = payload.split(b"\0")
        if values and values[-1] == b"":
            values.pop()
        argv = [value.decode("utf-8", errors="strict") for value in values]
    except (OSError, UnicodeError) as error:
        raise LocalPaperError("local PAPER guardian argv unavailable") from error
    if not argv or not payload.endswith(b"\0"):
        raise LocalPaperError("local PAPER guardian argv invalid")
    return argv, _sha256(payload)


def _process_identity(pid: int) -> dict[str, Any]:
    if type(pid) is not int or pid <= 0:
        raise LocalPaperError("local PAPER guardian process invalid")
    try:
        stat_payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        right_parenthesis = stat_payload.rfind(")")
        fields = stat_payload[right_parenthesis + 2:].split()
        if right_parenthesis <= 0 or len(fields) < 20:
            raise ValueError("short proc stat")
        start_ticks = int(fields[19])
        descriptor = os.open(
            f"/proc/{pid}/exe", os.O_RDONLY | os.O_CLOEXEC)
        try:
            executable_sha256 = _hash_open_descriptor(descriptor)
        finally:
            os.close(descriptor)
        _argv, argv_sha256 = _process_argv(pid)
    except (OSError, UnicodeError, ValueError) as error:
        raise LocalPaperError("local PAPER guardian process unavailable") from error
    if start_ticks <= 0:
        raise LocalPaperError("local PAPER guardian process invalid")
    return {
        "boot_id": _read_boot_id(), "pid": pid,
        "start_ticks": start_ticks,
        "exe_sha256": executable_sha256, "argv_sha256": argv_sha256,
    }


def _process_in_guardian_unit(pid: int, domain: str) -> bool:
    expected = f"hepta-local-paper-authority@{domain}.service"
    try:
        lines = Path(f"/proc/{pid}/cgroup").read_text(
            encoding="ascii").splitlines()
    except (OSError, UnicodeError):
        return False
    for line in lines:
        _hierarchy, separator, remainder = line.partition(":")
        if not separator:
            continue
        _controllers, separator, path = remainder.partition(":")
        if separator and expected in PurePath(path).parts:
            return True
    return False


def _guardian_argv_is_exact(pid: int, domain: str) -> bool:
    try:
        argv, _argv_sha256 = _process_argv(pid)
    except LocalPaperError:
        return False
    if len(argv) != 7:
        return False
    script_path = Path(argv[3])
    return (
        argv[0] == "/usr/bin/python3.12" and
        argv[1:3] == ["-I", "-S"] and
        script_path.name == "hepta-local-paper-control.py" and
        "credentials" in script_path.parts and
        argv[4:] == ["guardian", "--domain", domain])


def _runtime_document(path: Path, *, fields: frozenset[str],
                      schema: str) -> dict[str, Any] | None:
    try:
        payload, _metadata = _secure_read(path, mode=0o600)
    except LocalPaperError:
        if not path.exists() and not path.is_symlink():
            return None
        raise
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalPaperError("local PAPER guardian artifact invalid") from error
    document = _validate_canonical_seal(
        payload, value, "local PAPER guardian artifact invalid")
    if (set(document) != fields or document.get("schema") != schema or
            document.get("version") != 1):
        raise LocalPaperError("local PAPER guardian artifact invalid")
    return document


def _remove_runtime_artifact(path: Path) -> None:
    try:
        _secure_read(path, mode=0o600)
    except LocalPaperError:
        if not path.exists() and not path.is_symlink():
            return
        raise
    path.unlink()
    _fsync_directory(path.parent)


def _guardian_identity_matches(
        document: dict[str, Any], *, prefix: str = "guardian",
        require_unit: bool = True,
) -> bool:
    pid = document.get(f"{prefix}_pid")
    try:
        current = _process_identity(pid)
    except LocalPaperError:
        return False
    matches = (
        document.get("boot_id") == current["boot_id"] and
        document.get(f"{prefix}_start_ticks") == current["start_ticks"] and
        document.get(f"{prefix}_exe_sha256") == current["exe_sha256"] and
        document.get(f"{prefix}_argv_sha256") == current["argv_sha256"])
    return bool(matches and (
        not require_unit or (
            _process_in_guardian_unit(pid, "alpha") and
            _guardian_argv_is_exact(pid, "alpha"))))


def _write_guardian_request(
        *, operation: str, arguments: dict[str, Any],
        now_ms: int | None = None,
) -> dict[str, Any]:
    if operation not in {"ENABLE", "ENABLE_RECOVERY"}:
        raise LocalPaperError("local PAPER guardian request invalid")
    timestamp = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    requester = _process_identity(os.getpid())
    body = {
        "schema": GUARDIAN_REQUEST_SCHEMA, "version": 1,
        "request_id": secrets.token_hex(16), "operation": operation,
        "issued_at_ms": timestamp,
        "expires_at_ms": timestamp + GUARDIAN_REQUEST_TTL_MS,
        "boot_id": requester["boot_id"],
        "requester_pid": requester["pid"],
        "requester_start_ticks": requester["start_ticks"],
        "requester_exe_sha256": requester["exe_sha256"],
        "requester_argv_sha256": requester["argv_sha256"],
        "control_image_sha256": _control_image_sha256(),
        "domain": "alpha", "arguments": arguments,
    }
    document = _sealed_document(body)
    _write_root_transaction(
        GUARDIAN_REQUEST_PATH, _canonical_json(document), exclusive=True)
    return document


def _consume_guardian_request(
        *, now_ms: int | None = None,
) -> dict[str, Any]:
    document = _runtime_document(
        GUARDIAN_REQUEST_PATH, fields=GUARDIAN_REQUEST_FIELDS,
        schema=GUARDIAN_REQUEST_SCHEMA)
    timestamp = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    if (
            document is None or
            document.get("operation") not in {"ENABLE", "ENABLE_RECOVERY"} or
            document.get("domain") != "alpha" or
            not isinstance(document.get("request_id"), str) or
            re.fullmatch(r"[0-9a-f]{32}", document["request_id"]) is None or
            type(document.get("issued_at_ms")) is not int or
            type(document.get("expires_at_ms")) is not int or
            document.get("expires_at_ms", 0) -
                document.get("issued_at_ms", 0) != GUARDIAN_REQUEST_TTL_MS or
            not document.get("issued_at_ms", timestamp + 1) <= timestamp <
                document.get("expires_at_ms", 0) or
            not isinstance(document.get("arguments"), dict)):
        raise LocalPaperError("local PAPER guardian request invalid")
    requester_view = {
        "boot_id": document.get("boot_id"),
        "requester_pid": document.get("requester_pid"),
        "requester_start_ticks": document.get("requester_start_ticks"),
        "requester_exe_sha256": document.get("requester_exe_sha256"),
        "requester_argv_sha256": document.get("requester_argv_sha256"),
    }
    if not _guardian_identity_matches(
            requester_view, prefix="requester", require_unit=False):
        raise LocalPaperError("local PAPER guardian requester unavailable")
    if document.get("control_image_sha256") != _control_image_sha256():
        raise LocalPaperError("local PAPER guardian control image drift")
    _remove_runtime_artifact(GUARDIAN_REQUEST_PATH)
    return document


def _guardian_runtime_body(
        *, schema: str, transaction: dict[str, Any],
        guardian_identity: dict[str, Any], phase: str,
        mode: str | None = None, now_ms: int | None = None,
) -> dict[str, Any]:
    timestamp = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    common = {
        "schema": schema, "version": 1, "boot_id": guardian_identity["boot_id"],
        "guardian_pid": guardian_identity["pid"],
        "guardian_start_ticks": guardian_identity["start_ticks"],
        "guardian_exe_sha256": guardian_identity["exe_sha256"],
        "guardian_argv_sha256": guardian_identity["argv_sha256"],
        "control_image_sha256": _control_image_sha256(),
        "guardian_request_id":
            transaction["request"].get("guardian_request_id"),
        "domain": "alpha", "transaction_id": transaction["transaction_id"],
        "operation": transaction["operation"], "phase": phase,
        "request_sha256": transaction["request_sha256"],
        "target_identity_manifest_sha256":
            transaction["target_identity_manifest_sha256"],
        "target_drop_in_sha256": transaction["target_drop_in_sha256"],
    }
    if schema == BROKER_START_PERMIT_SCHEMA:
        return {
            **common, "issued_at_ms": timestamp,
            "expires_at_ms": timestamp + BROKER_START_PERMIT_TTL_MS,
        }
    if schema == GUARDIAN_RUNTIME_SCHEMA and mode is not None:
        return {
            **common, "status": "ACTIVE", "recorded_at_ms": timestamp,
            "mode": mode,
        }
    raise LocalPaperError("local PAPER guardian runtime request invalid")


def _issue_broker_start_permit(
        transaction: dict[str, Any], *, guardian_identity: dict[str, Any],
        phase: str,
) -> dict[str, Any]:
    expected_suffix = (
        "START_BROKER_RECOVERY" if transaction["operation"] ==
        "ENABLE_RECOVERY" else "START_BROKER_LOCAL_PAPER")
    if transaction.get("phase") != phase or not phase.endswith(expected_suffix):
        raise LocalPaperError("local PAPER broker permit phase invalid")
    document = _sealed_document(_guardian_runtime_body(
        schema=BROKER_START_PERMIT_SCHEMA, transaction=transaction,
        guardian_identity=guardian_identity, phase=phase))
    _write_root_transaction(
        BROKER_START_PERMIT_PATH, _canonical_json(document), exclusive=True)
    return document


def _broker_activation_reservation_document(
        transaction: dict[str, Any], *, guardian_identity: dict[str, Any],
        permit: dict[str, Any],
) -> dict[str, Any]:
    issued_at_ms = permit.get("issued_at_ms")
    phase = transaction.get("phase")
    if type(issued_at_ms) is not int or not isinstance(phase, str):
        raise LocalPaperError("local PAPER broker permit invalid")
    expected_permit = _sealed_document(_guardian_runtime_body(
        schema=BROKER_START_PERMIT_SCHEMA, transaction=transaction,
        guardian_identity=guardian_identity, phase=phase,
        now_ms=issued_at_ms))
    if permit != expected_permit:
        raise LocalPaperError("local PAPER broker permit drifted")
    permit_payload = _canonical_json(permit)
    body = {
        "schema": BROKER_ACTIVATION_RESERVATION_SCHEMA, "version": 1,
        "status": BROKER_ACTIVATION_RESERVATION_STATUS,
        "activation_id": secrets.token_hex(16),
        "issued_at_ms": permit["issued_at_ms"],
        "expires_at_ms": permit["expires_at_ms"],
        "boot_id": permit["boot_id"],
        "guardian_pid": permit["guardian_pid"],
        "guardian_start_ticks": permit["guardian_start_ticks"],
        "guardian_exe_sha256": permit["guardian_exe_sha256"],
        "guardian_argv_sha256": permit["guardian_argv_sha256"],
        "control_image_sha256": permit["control_image_sha256"],
        "guardian_request_id": permit["guardian_request_id"],
        "domain": permit["domain"],
        "transaction_id": permit["transaction_id"],
        "operation": permit["operation"], "phase": permit["phase"],
        "request_sha256": permit["request_sha256"],
        "target_identity_manifest_sha256":
            permit["target_identity_manifest_sha256"],
        "target_drop_in_sha256": permit["target_drop_in_sha256"],
        "broker_start_permit_file_sha256": _sha256(permit_payload),
        "broker_start_permit_body_sha256": permit["body_sha256"],
        "required_pre_activation_boundary":
            BROKER_ACTIVATION_REQUIRED_PREDECESSOR,
        "paper_only": True, "live_authorized": False,
    }
    document = _sealed_document(body)
    payload = _canonical_json(document)
    if (
            set(document) != BROKER_ACTIVATION_RESERVATION_FIELDS or
            len(payload) > MAX_HOST_AUTHORITY_ARTIFACT_BYTES):
        raise LocalPaperError("local PAPER activation reservation invalid")
    return document


def _validate_broker_activation_reservation(
        payload: bytes, *, transaction: dict[str, Any],
        permit: dict[str, Any], now_ms: int,
        require_live_guardian: bool,
) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalPaperError(
            "local PAPER activation reservation invalid") from error
    document = _validate_canonical_seal(
        payload, value, "local PAPER activation reservation invalid")
    copied = (
        "boot_id", "guardian_pid", "guardian_start_ticks",
        "guardian_exe_sha256", "guardian_argv_sha256",
        "control_image_sha256", "guardian_request_id", "domain",
        "transaction_id", "operation", "phase", "request_sha256",
        "target_identity_manifest_sha256", "target_drop_in_sha256",
    )
    if (
            set(document) != BROKER_ACTIVATION_RESERVATION_FIELDS or
            document.get("schema") !=
                BROKER_ACTIVATION_RESERVATION_SCHEMA or
            document.get("version") != 1 or
            document.get("status") !=
                BROKER_ACTIVATION_RESERVATION_STATUS or
            not isinstance(document.get("activation_id"), str) or
            re.fullmatch(r"[0-9a-f]{32}", document["activation_id"]) is None or
            type(document.get("issued_at_ms")) is not int or
            type(document.get("expires_at_ms")) is not int or
            document.get("issued_at_ms") != permit.get("issued_at_ms") or
            document.get("expires_at_ms") != permit.get("expires_at_ms") or
            document.get("expires_at_ms", 0) -
                document.get("issued_at_ms", 0) !=
                    BROKER_START_PERMIT_TTL_MS or
            not document.get("issued_at_ms", now_ms + 1) <= now_ms <
                document.get("expires_at_ms", 0) or
            any(document.get(field) != permit.get(field) for field in copied) or
            document.get("broker_start_permit_file_sha256") !=
                _sha256(_canonical_json(permit)) or
            document.get("broker_start_permit_body_sha256") !=
                permit.get("body_sha256") or
            document.get("required_pre_activation_boundary") !=
                BROKER_ACTIVATION_REQUIRED_PREDECESSOR or
            document.get("paper_only") is not True or
            document.get("live_authorized") is not False or
            transaction.get("transaction_id") !=
                document.get("transaction_id") or
            transaction.get("operation") != document.get("operation") or
            transaction.get("phase") != document.get("phase") or
            transaction.get("request_sha256") !=
                document.get("request_sha256") or
            transaction.get("target_identity_manifest_sha256") !=
                document.get("target_identity_manifest_sha256") or
            transaction.get("target_drop_in_sha256") !=
                document.get("target_drop_in_sha256") or
            document.get("boot_id") != _read_boot_id() or
            document.get("control_image_sha256") != _control_image_sha256() or
            (require_live_guardian and not _guardian_identity_matches(document))):
        raise LocalPaperError("local PAPER activation reservation invalid")
    expected_suffix = (
        "START_BROKER_RECOVERY" if document["operation"] ==
        "ENABLE_RECOVERY" else "START_BROKER_LOCAL_PAPER")
    if (
            document["operation"] not in {"ENABLE", "ENABLE_RECOVERY"} or
            not document["phase"].endswith(expected_suffix)):
        raise LocalPaperError("local PAPER activation reservation invalid")
    return document


def _publish_broker_activation_reservation(
        lease: dict[str, Any], *, transaction: dict[str, Any],
        guardian_identity: dict[str, Any], permit: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    document = _broker_activation_reservation_document(
        transaction, guardian_identity=guardian_identity, permit=permit)
    payload = _canonical_json(document)
    _publish_host_authority_owner(lease, payload)
    _validate_broker_activation_reservation(
        payload, transaction=transaction, permit=permit,
        now_ms=time.time_ns() // 1_000_000, require_live_guardian=True)
    return document, payload


def _activation_reservation_belongs_to_transaction(
        payload: bytes, transaction: dict[str, Any],
) -> bool:
    try:
        value = json.loads(payload)
        document = _validate_canonical_seal(
            payload, value, "local PAPER activation reservation invalid")
    except (UnicodeDecodeError, json.JSONDecodeError, LocalPaperError):
        return False
    return bool(
        set(document) == BROKER_ACTIVATION_RESERVATION_FIELDS and
        document.get("schema") == BROKER_ACTIVATION_RESERVATION_SCHEMA and
        document.get("version") == 1 and
        document.get("status") == BROKER_ACTIVATION_RESERVATION_STATUS and
        isinstance(document.get("activation_id"), str) and
        re.fullmatch(r"[0-9a-f]{32}", document["activation_id"]) is not None and
        document.get("transaction_id") == transaction.get("transaction_id") and
        document.get("operation") == transaction.get("operation") and
        isinstance(document.get("phase"), str) and
        document["phase"].endswith(
            "START_BROKER_RECOVERY" if document["operation"] ==
            "ENABLE_RECOVERY" else "START_BROKER_LOCAL_PAPER") and
        document.get("request_sha256") == transaction.get("request_sha256") and
        document.get("target_identity_manifest_sha256") ==
            transaction.get("target_identity_manifest_sha256") and
        document.get("target_drop_in_sha256") ==
            transaction.get("target_drop_in_sha256") and
        document.get("required_pre_activation_boundary") ==
            BROKER_ACTIVATION_REQUIRED_PREDECESSOR and
        document.get("paper_only") is True and
        document.get("live_authorized") is False)


def _clear_transaction_activation_reservation(
        transaction: dict[str, Any], *, host_authority_root: Path,
) -> bool:
    with _host_authority_lease(host_authority_root) as lease:
        payload = _host_authority_owner_payload(lease)
        if payload is None:
            permit = _runtime_document(
                BROKER_START_PERMIT_PATH, fields=BROKER_START_PERMIT_FIELDS,
                schema=BROKER_START_PERMIT_SCHEMA)
            if permit is None:
                return False
            if (
                    permit.get("transaction_id") !=
                        transaction.get("transaction_id") or
                    permit.get("operation") != transaction.get("operation") or
                    permit.get("request_sha256") !=
                        transaction.get("request_sha256")):
                raise LocalPaperError(
                    "local PAPER broker permit is not this transaction")
            _remove_runtime_artifact(BROKER_START_PERMIT_PATH)
            return True
        if not _activation_reservation_belongs_to_transaction(
                payload, transaction):
            raise LocalPaperError(
                "host PAPER authority owner is not this activation")
        _remove_exact_activation_artifacts(lease, payload)
        _remove_exact_host_authority_owner(lease, payload, absent_ok=False)
        return True


def _broker_activation_consumed_path(
        host_authority_root: Path, activation_id: str,
) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", activation_id) is None:
        raise LocalPaperError("local PAPER activation identifier invalid")
    return host_authority_root / (
        BROKER_ACTIVATION_CONSUMED_PREFIX + activation_id +
        BROKER_ACTIVATION_ARTIFACT_SUFFIX)


def _broker_activation_intent_path(
        host_authority_root: Path, activation_id: str,
) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", activation_id) is None:
        raise LocalPaperError("local PAPER activation identifier invalid")
    return host_authority_root / (
        BROKER_ACTIVATION_INTENT_PREFIX + activation_id +
        BROKER_ACTIVATION_ARTIFACT_SUFFIX)


def _remove_exact_activation_artifacts(
        lease: dict[str, Any], reservation_payload: bytes,
) -> None:
    try:
        reservation = _validate_canonical_seal(
            reservation_payload, json.loads(reservation_payload),
            "local PAPER activation reservation invalid")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalPaperError(
            "local PAPER activation reservation invalid") from error
    activation_id = str(reservation.get("activation_id", ""))
    consumed_path = _broker_activation_consumed_path(
        Path(lease["root"]), activation_id)
    intent_path = _broker_activation_intent_path(
        Path(lease["root"]), activation_id)
    directory = int(lease["directory"])
    for path, fields, schema, label in (
            (consumed_path, BROKER_ACTIVATION_CONSUMED_FIELDS,
             BROKER_ACTIVATION_CONSUMED_SCHEMA, "completion"),
            (intent_path, BROKER_ACTIVATION_INTENT_FIELDS,
             BROKER_ACTIVATION_INTENT_SCHEMA, "intent")):
        if not (path.exists() or path.is_symlink()):
            continue
        payload, _metadata = _read_host_authority_artifact(
            lease, path.name, maximum=MAX_HOST_AUTHORITY_ARTIFACT_BYTES)
        try:
            document = _validate_canonical_seal(
                payload, json.loads(payload),
                f"local PAPER activation {label} invalid")
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LocalPaperError(
                f"local PAPER activation {label} invalid") from error
        if (
                set(document) != fields or
                document.get("schema") != schema or
                document.get("activation_id") != activation_id or
                document.get("reservation_file_sha256") !=
                    _sha256(reservation_payload) or
                document.get("reservation_body_sha256") !=
                    reservation.get("body_sha256")):
            raise LocalPaperError(
                f"local PAPER activation {label} does not belong to rollback")
        os.unlink(path.name, dir_fd=directory)
        os.fsync(directory)
    try:
        permit = _runtime_document(
            BROKER_START_PERMIT_PATH, fields=BROKER_START_PERMIT_FIELDS,
            schema=BROKER_START_PERMIT_SCHEMA)
    except LocalPaperError:
        permit = None
        if BROKER_START_PERMIT_PATH.exists() or \
                BROKER_START_PERMIT_PATH.is_symlink():
            raise
    if permit is not None:
        permit_payload = _canonical_json(permit)
        if (
                _sha256(permit_payload) !=
                    reservation.get("broker_start_permit_file_sha256") or
                permit.get("body_sha256") !=
                    reservation.get("broker_start_permit_body_sha256")):
            raise LocalPaperError(
                "local PAPER broker permit does not belong to rollback")
        _remove_runtime_artifact(BROKER_START_PERMIT_PATH)


def _load_current_broker_boundary() -> tuple[dict[str, Any], bytes]:
    try:
        payload, metadata = _secure_read(
            BROKER_BOUNDARY_RECEIPT_PATH, mode=0o600,
            maximum=MAX_BROKER_BOUNDARY_RECEIPT_BYTES)
        value = json.loads(payload)
    except (LocalPaperError, FileNotFoundError, UnicodeDecodeError,
            json.JSONDecodeError) as error:
        raise LocalPaperError(
            "local PAPER current broker boundary invalid") from error
    document = _validate_canonical_seal(
        payload, value, "local PAPER current broker boundary invalid")
    now_ms = time.time_ns() // 1_000_000
    if (
            set(document) != BROKER_BOUNDARY_RECEIPT_FIELDS or
            document.get("schema") != BROKER_BOUNDARY_RECEIPT_SCHEMA or
            document.get("version") != 1 or
            document.get("status") != "EXACT_ACTIVE" or
            document.get("state") != "ACTIVE" or
            document.get("boot_id") != _read_boot_id() or
            type(document.get("generation")) is not int or
            document.get("generation", 0) < 1 or
            type(document.get("publisher_pid")) is not int or
            document.get("publisher_pid", 0) <= 0 or
            type(document.get("publisher_start_ticks")) is not int or
            document.get("publisher_start_ticks", 0) <= 0 or
            type(document.get("observed_at_ms")) is not int or
            not 0 <= now_ms - document.get("observed_at_ms", now_ms + 1) <=
                BROKER_BOUNDARY_MAXIMUM_AGE_MS or
            type(document.get("observed_monotonic_ns")) is not int or
            document.get("observed_monotonic_ns", 0) <= 0 or
            document.get("protected_port_count") != 4 or
            document.get("authorized_connector_count") != 1 or
            document.get("paper_authorized") is not True or
            document.get("live_authorized") is not False or
            not isinstance(document.get("authorized_uids"), list) or
            len(document["authorized_uids"]) != 1 or
            not isinstance(document.get("authorized_connectors"), list) or
            len(document["authorized_connectors"]) != 1 or
            any(not isinstance(document.get(field), str) or
                DIGEST.fullmatch(document[field]) is None
                for field in (
                    "source_policy_sha256", "identity_manifest_sha256",
                    "effective_policy_sha256", "table_semantic_sha256",
                    "state_sha256", "body_sha256")) or
            metadata.st_nlink != 1):
        raise LocalPaperError("local PAPER current broker boundary invalid")
    try:
        publisher = _process_identity(document["publisher_pid"])
    except LocalPaperError as error:
        raise LocalPaperError(
            "local PAPER current broker publisher unavailable") from error
    if publisher.get("start_ticks") != document["publisher_start_ticks"]:
        raise LocalPaperError("local PAPER current broker publisher drifted")
    return document, payload


def _paper_identity_digest_from_boundary(
        boundary: dict[str, Any]) -> str:
    """Return the digest for the dynamic PAPER identity input.

    The broker boundary's top-level ``identity_manifest_sha256`` identifies
    the fixed service-identity policy used to build the nftables policy.  The
    per-domain PAPER identity manifest is a separate source fingerprint.
    Activation handoff records bind to that dynamic PAPER manifest, so the
    two top-level/source values must not be compared with each other.
    """
    fingerprints = boundary.get("source_fingerprints")
    if not isinstance(fingerprints, list):
        raise LocalPaperError(
            "local PAPER boundary source fingerprints are invalid")
    matches = [
        item for item in fingerprints
        if isinstance(item, dict) and
        item.get("path") == str(PRODUCTION_IDENTITIES_PATH)
    ]
    if len(matches) != 1:
        raise LocalPaperError(
            "local PAPER boundary PAPER identity fingerprint is missing")
    digest = matches[0].get("sha256")
    if (matches[0].get("present") is not True or
            not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
        raise LocalPaperError(
            "local PAPER boundary PAPER identity fingerprint is invalid")
    return digest


def _verify_broker_activation_completion(
        reservation: dict[str, Any], reservation_payload: bytes,
        host_authority_root: Path,
) -> tuple[dict[str, Any], bytes]:
    activation_id = reservation.get("activation_id")
    if not isinstance(activation_id, str):
        raise LocalPaperError("local PAPER activation completion invalid")
    path = _broker_activation_consumed_path(
        host_authority_root, activation_id)
    with _host_authority_lease(host_authority_root) as lease:
        if _host_authority_owner_payload(lease) != reservation_payload:
            raise LocalPaperError(
                "local PAPER activation reservation was not retained")
        try:
            payload, metadata = _read_host_authority_artifact(
                lease, path.name, maximum=MAX_HOST_AUTHORITY_ARTIFACT_BYTES)
        except LocalPaperError as error:
            raise LocalPaperError(
                "local PAPER activation completion missing or unsafe") \
                from error
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LocalPaperError(
                "local PAPER activation completion invalid") from error
        document = _validate_canonical_seal(
            payload, value, "local PAPER activation completion invalid")
        copied = (
            "activation_id", "boot_id", "guardian_request_id", "domain",
            "transaction_id", "operation", "phase", "request_sha256",
            "target_identity_manifest_sha256", "target_drop_in_sha256",
            "control_image_sha256", "required_pre_activation_boundary",
            "broker_start_permit_file_sha256",
            "broker_start_permit_body_sha256",
        )
        consumed_at_ms = document.get("consumed_at_ms")
        if (
                set(document) != BROKER_ACTIVATION_CONSUMED_FIELDS or
                document.get("schema") != BROKER_ACTIVATION_CONSUMED_SCHEMA or
                document.get("version") != 1 or
                document.get("status") != BROKER_ACTIVATION_CONSUMED_STATUS or
                any(document.get(field) != reservation.get(field)
                    for field in copied) or
                document.get("reservation_file_sha256") !=
                    _sha256(reservation_payload) or
                document.get("reservation_body_sha256") !=
                    reservation.get("body_sha256") or
                type(consumed_at_ms) is not int or
                not reservation.get("issued_at_ms", consumed_at_ms + 1) <=
                    consumed_at_ms <=
                        reservation.get("expires_at_ms", consumed_at_ms - 1) or
                document.get("boot_id") != _read_boot_id() or
                not isinstance(document.get(
                    "pre_activation_boundary_state_sha256"), str) or
                DIGEST.fullmatch(document[
                    "pre_activation_boundary_state_sha256"]) is None or
                document.get("active_boundary_status") != "EXACT_ACTIVE" or
                not isinstance(document.get(
                    "active_boundary_state_sha256"), str) or
                DIGEST.fullmatch(document[
                    "active_boundary_state_sha256"]) is None or
                document.get("active_boundary_state_sha256") ==
                    document.get("pre_activation_boundary_state_sha256") or
                document.get("paper_authorized") is not True or
                document.get("live_authorized") is not False or
                metadata.st_nlink != 1):
            raise LocalPaperError(
                "local PAPER activation completion invalid")
        try:
            permit = _runtime_document(
                BROKER_START_PERMIT_PATH, fields=BROKER_START_PERMIT_FIELDS,
                schema=BROKER_START_PERMIT_SCHEMA)
        except LocalPaperError as error:
            raise LocalPaperError(
                "local PAPER activation permit cleanup invalid") from error
        if permit is not None:
            raise LocalPaperError(
                "local PAPER activation permit was not consumed")
        intent_path = _broker_activation_intent_path(
            host_authority_root, activation_id)
        if intent_path.exists() or intent_path.is_symlink():
            raise LocalPaperError(
                "local PAPER activation intent was not consumed")
        boundary, _boundary_payload = _load_current_broker_boundary()
        if (
                boundary.get("boot_id") != document.get("boot_id") or
                boundary.get("state_sha256") !=
                    document.get("active_boundary_state_sha256") or
                boundary.get("status") !=
                    document.get("active_boundary_status")):
            raise LocalPaperError(
                "local PAPER active broker boundary drifted")
        # Both artifacts remain durable across the lock handoff.  The
        # execution preflight atomically replaces this exact reservation with
        # its runtime owner and only then fsync-unlinks the consumed receipt.
        return document, payload


def _verify_runtime_owner_adoption_once(
        reservation: dict[str, Any], reservation_payload: bytes,
        consumed: dict[str, Any], consumed_payload: bytes,
        host_authority_root: Path,
) -> tuple[dict[str, Any], bytes]:
    owner_path = host_authority_root / HOST_AUTHORITY_OWNER_NAME
    try:
        payload, metadata = _secure_read(
            owner_path, mode=0o600,
            maximum=MAX_HOST_AUTHORITY_ARTIFACT_BYTES)
        value = json.loads(payload)
    except (LocalPaperError, FileNotFoundError, UnicodeDecodeError,
            json.JSONDecodeError) as error:
        raise LocalPaperError(
            "local PAPER runtime owner adoption invalid") from error
    owner = _validate_canonical_seal(
        payload, value, "local PAPER runtime owner adoption invalid")
    copied = (
        "activation_id", "transaction_id", "operation", "phase",
        "guardian_request_id", "request_sha256",
        "broker_start_permit_file_sha256",
        "broker_start_permit_body_sha256",
        "target_identity_manifest_sha256", "target_drop_in_sha256")
    process: dict[str, Any] | None = None
    try:
        process = _process_identity(int(owner.get("guard_pid", 0)))
    except (LocalPaperError, TypeError, ValueError):
        pass
    if (
            set(owner) != PAPER_RUNTIME_OWNER_FIELDS or
            owner.get("schema") != PAPER_RUNTIME_OWNER_SCHEMA or
            owner.get("version") != 1 or
            owner.get("status") != PAPER_RUNTIME_OWNER_STATUS or
            owner.get("boot_id") != _read_boot_id() or
            owner.get("domain") != "alpha" or
            type(owner.get("adopted_at_ms")) is not int or
            owner.get("adopted_at_ms", 0) <= 0 or
            any(owner.get(field) != reservation.get(field)
                for field in copied) or
            owner.get("reservation_file_sha256") !=
                _sha256(reservation_payload) or
            owner.get("reservation_body_sha256") !=
                reservation.get("body_sha256") or
            owner.get("activation_consumed_file_sha256") !=
                _sha256(consumed_payload) or
            owner.get("activation_consumed_body_sha256") !=
                consumed.get("body_sha256") or
            owner.get("pre_activation_boundary_state_sha256") !=
                consumed.get("pre_activation_boundary_state_sha256") or
            owner.get("active_boundary_state_sha256") !=
                consumed.get("active_boundary_state_sha256") or
            owner.get("execution_identity") != "hepta-ib-exec-alpha" or
            owner.get("execution_uid") != 2121 or
            owner.get("execution_gid") != 2121 or
            owner.get("control_directory") !=
                "/run/hepta/ib-paper-control-alpha" or
            owner.get("kill_switch_marker") !=
                "/run/hepta/ib-paper-control-alpha/kill-switch" or
            owner.get("mutation_scope") !=
                "PAPER_DOMAIN_EGRESS_GUARD_ONLY" or
            owner.get("paper_authorized") is not True or
            owner.get("live_authorized") is not False or
            process is None or
            process.get("boot_id") != owner.get("boot_id") or
            process.get("pid") != owner.get("guard_pid") or
            process.get("start_ticks") != owner.get("guard_start_ticks") or
            process.get("exe_sha256") != owner.get("guard_exe_sha256") or
            process.get("argv_sha256") != owner.get("guard_argv_sha256") or
            metadata.st_nlink != 1):
        raise LocalPaperError("local PAPER runtime owner adoption invalid")
    consumed_path = _broker_activation_consumed_path(
        host_authority_root, str(reservation.get("activation_id", "")))
    if consumed_path.exists() or consumed_path.is_symlink():
        raise LocalPaperError(
            "local PAPER activation consumed receipt was not adopted")
    boundary, _boundary_payload = _load_current_broker_boundary()
    if (
            boundary.get("boot_id") != owner.get("boot_id") or
            boundary.get("state_sha256") !=
                owner.get("active_boundary_state_sha256") or
            _paper_identity_digest_from_boundary(boundary) !=
                owner.get("target_identity_manifest_sha256") or
            boundary.get("authorized_uids") != [owner.get("execution_uid")] or
            boundary.get("status") != "EXACT_ACTIVE"):
        raise LocalPaperError(
            "local PAPER runtime owner active boundary drifted")
    return owner, payload


def _verify_runtime_owner_adoption(
        reservation: dict[str, Any], reservation_payload: bytes,
        consumed: dict[str, Any], consumed_payload: bytes,
        host_authority_root: Path,
) -> tuple[dict[str, Any], bytes]:
    """Wait only for the existing preflight reservation-to-owner CAS.

    ``systemctl start`` queues the socket-activated units asynchronously.
    A single read here can observe the short interval before preflight has
    atomically replaced the consumed activation receipt with ``owner.v1``.
    Poll the exact verifier without creating authority, extending the permit,
    or reconnecting to IB; fail closed at the earlier bounded deadline or
    permit expiry.
    """
    now_ms = time.time_ns() // 1_000_000
    expires_at_ms = reservation.get("expires_at_ms")
    if type(expires_at_ms) is not int:
        expires_at_ms = now_ms + RUNTIME_OWNER_ADOPTION_MAX_WAIT_MS
    deadline_ms = min(
        now_ms + RUNTIME_OWNER_ADOPTION_MAX_WAIT_MS,
        expires_at_ms - 100,
    )
    last_error: LocalPaperError | None = None
    while True:
        try:
            return _verify_runtime_owner_adoption_once(
                reservation, reservation_payload, consumed, consumed_payload,
                host_authority_root)
        except LocalPaperError as error:
            last_error = error
            if time.time_ns() // 1_000_000 >= deadline_ms:
                raise LocalPaperError(
                    "local PAPER runtime owner adoption timed out") from error
            time.sleep(RUNTIME_OWNER_ADOPTION_POLL_MS / 1000.0)
    raise LocalPaperError("local PAPER runtime owner adoption failed") from last_error


def _write_guardian_active_receipt(
        transaction: dict[str, Any], *, guardian_identity: dict[str, Any],
        phase: str, mode: str,
) -> dict[str, Any]:
    document = _sealed_document(_guardian_runtime_body(
        schema=GUARDIAN_RUNTIME_SCHEMA, transaction=transaction,
        guardian_identity=guardian_identity, phase=phase, mode=mode))
    _write_root_transaction(
        GUARDIAN_ACTIVE_PATH, _canonical_json(document), exclusive=True)
    return document


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return tuple(int(getattr(metadata, field)) for field in (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns"))


def _secure_read(
        path: Path, *, mode: int, uid: int | None = None,
        gid: int | None = None,
        maximum: int = MAX_EXTERNAL_P1_JSON_BYTES,
) -> tuple[bytes, os.stat_result]:
    uid = ROOT_UID if uid is None else uid
    gid = ROOT_GID if gid is None else gid
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        before = os.lstat(path)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
                _metadata_identity(before) != _metadata_identity(opened) or
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_uid != uid or opened.st_gid != gid or
                stat.S_IMODE(opened.st_mode) != mode or
                opened.st_size < 1 or opened.st_size > maximum):
            raise LocalPaperError("external P1 artifact unsafe")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after_open = os.fstat(descriptor)
        after_path = os.lstat(path)
        if (
                len(payload) > maximum or len(payload) != opened.st_size or
                _metadata_identity(opened) != _metadata_identity(after_open) or
                _metadata_identity(opened) != _metadata_identity(after_path)):
            raise LocalPaperError("external P1 artifact rebound")
        return bytes(payload), opened
    except (OSError, UnicodeError) as error:
        if isinstance(error, LocalPaperError):
            raise
        raise LocalPaperError("external P1 artifact unsafe") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _file_evidence(path: Path, payload: bytes, metadata: os.stat_result) -> dict[str, Any]:
    return {
        "path": str(path), "file_sha256": _sha256(payload),
        "bytes": len(payload), "mode": metadata.st_mode,
        "uid": metadata.st_uid, "gid": metadata.st_gid,
        "nlink": metadata.st_nlink, "device": metadata.st_dev,
        "inode": metadata.st_ino, "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


def _validate_recovery_reference(
        reference: Any, *, field: str, campaign_id: str,
        suspension_id: str, source_baseline_sha256: str,
) -> tuple[dict[str, Any], int]:
    expected_schema, expected_status = RECOVERY_REFERENCE_SPECS[field]
    if (
            not isinstance(reference, dict) or
            set(reference) != RECOVERY_REFERENCE_FIELDS or
            reference.get("schema") != expected_schema or
            reference.get("status") != expected_status or
            not isinstance(reference.get("path"), str) or
            not Path(reference["path"]).is_absolute() or
            not isinstance(reference.get("file_sha256"), str) or
            DIGEST.fullmatch(reference["file_sha256"]) is None or
            not isinstance(reference.get("body_sha256"), str) or
            DIGEST.fullmatch(reference["body_sha256"]) is None or
            type(reference.get("bytes")) is not int or
            not 1 <= reference["bytes"] <= MAX_EXTERNAL_P1_JSON_BYTES or
            reference.get("mode") != stat.S_IFREG | 0o600 or
            reference.get("uid") != ROOT_UID or
            reference.get("gid") != ROOT_GID or
            reference.get("nlink") != 1):
        raise LocalPaperError("recovery authority reference invalid")
    path = Path(reference["path"])
    if path in {
            Path("/etc/heptatrader/paper-campaigns/alpha.json"),
            LOCAL_PAPER_STATE_ROOT / "state.json"}:
        raise LocalPaperError("mutable recovery authority reference rejected")
    payload, metadata = _secure_read(path, mode=0o600)
    if (
            len(payload) != reference["bytes"] or
            metadata.st_mode != reference["mode"] or
            metadata.st_uid != reference["uid"] or
            metadata.st_gid != reference["gid"] or
            metadata.st_nlink != reference["nlink"] or
            _sha256(payload) != reference["file_sha256"]):
        raise LocalPaperError("recovery authority reference drifted")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalPaperError("recovery authority artifact invalid") from error
    document = _validate_canonical_seal(
        payload, value, "recovery authority artifact invalid")
    recorded_at_ms = document.get("recorded_at_ms")
    if (
            document.get("schema") != expected_schema or
            document.get("version") != 1 or
            document.get("status") != expected_status or
            document.get("domain") != "alpha" or
            document.get("campaign_id") != campaign_id or
            document.get("suspension_id") != suspension_id or
            document.get("source_baseline_sha256") !=
                source_baseline_sha256 or
            document.get("body_sha256") != reference["body_sha256"] or
            type(recorded_at_ms) is not int or recorded_at_ms <= 0):
        raise LocalPaperError("recovery authority artifact binding invalid")
    if field == "policy_preimage_reference":
        if (
                document.get("policy_terminal") is not True or
                document.get("policy_enabled") is not False or
                document.get("policy_mutations_authorized") is not False or
                document.get("paper_only") is not True or
                document.get("live_authorized") is not False):
            raise LocalPaperError("recovery policy preimage invalid")
    elif field == "incident_state_reference":
        if (
                document.get("recovery_required") is not True or
                document.get("trading_suspended") is not True):
            raise LocalPaperError("recovery incident snapshot invalid")
    elif field == "mutation_lineage_reference":
        call_lists = tuple(document.get(name) for name in (
            "place_call_ids", "cancel_call_ids", "flatten_call_ids"))
        digest_lists = tuple(document.get(name) for name in (
            "request_sha256s", "response_sha256s", "journal_sha256s"))
        if (
                any(not isinstance(values, list) or any(
                    not isinstance(item, str) or not item or len(item) > 512
                    for item in values) for values in call_lists) or
                not any(call_lists) or
                any(not isinstance(values, list) or not values or any(
                    not isinstance(item, str) or DIGEST.fullmatch(item) is None
                    for item in values) for values in digest_lists)):
            raise LocalPaperError("recovery mutation lineage invalid")
    else:
        owners = document.get("owners")
        if (
                document.get("lease_store_schema") != "HSL8" or
                type(document.get("owner_count")) is not int or
                document.get("owner_count", 0) < 1 or
                not isinstance(owners, list) or
                len(owners) != document["owner_count"] or
                any(not isinstance(owner, dict) or not owner for owner in owners) or
                owners != sorted(owners, key=_canonical_json) or
                len({_sha256(_canonical_json(owner)) for owner in owners}) !=
                    len(owners)):
            raise LocalPaperError("recovery session owner set invalid")
    return document, recorded_at_ms


def validate_recovery_authority(
        *, recovery_path: Path, expected_file_sha256: str,
        expected_body_sha256: str, now_ms: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
            recovery_path != RECOVERY_AUTHORITY_PATH or
            DIGEST.fullmatch(expected_file_sha256) is None or
            expected_file_sha256 == "sha256:" + "0" * 64 or
            DIGEST.fullmatch(expected_body_sha256) is None or
            expected_body_sha256 == "sha256:" + "0" * 64):
        raise LocalPaperError("recovery authority arguments invalid")
    payload, metadata = _secure_read(recovery_path, mode=0o600)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalPaperError("recovery authority invalid") from error
    document = _validate_canonical_seal(
        payload, value, "recovery authority invalid")
    timestamp = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    recorded_at_ms = document.get("recorded_at_ms")
    if (
            set(document) != RECOVERY_AUTHORITY_FIELDS or
            document.get("schema") != RECOVERY_AUTHORITY_SCHEMA or
            document.get("version") != 1 or
            document.get("status") != RECOVERY_AUTHORITY_STATUS or
            document.get("domain") != "alpha" or
            not isinstance(document.get("recovery_id"), str) or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}",
                         document["recovery_id"]) is None or
            not isinstance(document.get("campaign_id"), str) or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,255}",
                         document["campaign_id"]) is None or
            not isinstance(document.get("suspension_id"), str) or
            not document["suspension_id"] or
            len(document["suspension_id"]) > 512 or
            not isinstance(document.get("reason_code"), str) or
            re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}",
                         document["reason_code"]) is None or
            not isinstance(document.get("source_baseline_sha256"), str) or
            DIGEST.fullmatch(document["source_baseline_sha256"]) is None or
            document["source_baseline_sha256"] == "sha256:" + "0" * 64 or
            document.get("watch_handoff_receipt_path") !=
                str(EXTERNAL_P1_HANDOFF_PATH) or
            any(not isinstance(document.get(field), str) or
                DIGEST.fullmatch(document[field]) is None or
                document[field] == "sha256:" + "0" * 64
                for field in (
                    "watch_handoff_receipt_file_sha256",
                    "watch_handoff_receipt_body_sha256")) or
            type(recorded_at_ms) is not int or
            not 0 < recorded_at_ms <= timestamp or
            document.get("recovery_required") is not True or
            document.get("reduce_only") is not True or
            document.get("paper_only") is not True or
            any(document.get(field) is not False for field in (
                "live_authorized", "entry_authorized",
                "order_submission_authorized",
                "session_provision_authorized")) or
            type(document.get("session_owner_count")) is not int or
            document.get("session_owner_count", 0) < 1 or
            document.get("all_original_session_owners_bound") is not True or
            _sha256(payload) != expected_file_sha256 or
            document.get("body_sha256") != expected_body_sha256 or
            metadata.st_nlink != 1):
        raise LocalPaperError("recovery authority invalid")
    references: dict[str, dict[str, Any]] = {}
    reference_times: list[int] = []
    for field in RECOVERY_REFERENCE_SPECS:
        artifact, artifact_time = _validate_recovery_reference(
            document.get(field), field=field,
            campaign_id=document["campaign_id"],
            suspension_id=document["suspension_id"],
            source_baseline_sha256=document["source_baseline_sha256"])
        references[field] = artifact
        reference_times.append(artifact_time)
    reference_values = [document[field] for field in RECOVERY_REFERENCE_SPECS]
    for key in ("path", "file_sha256", "body_sha256"):
        if len({reference[key] for reference in reference_values}) != 4:
            raise LocalPaperError("recovery authority references collide")
    owners = references["session_owner_set_reference"]
    if (
            owners.get("owner_count") != document["session_owner_count"] or
            recorded_at_ms < max(reference_times)):
        raise LocalPaperError("recovery authority lineage invalid")
    return document, {
        "recovery_record": _file_evidence(recovery_path, payload, metadata),
        "references": reference_values,
        "boundary_fingerprint": _sha256(_canonical_json({
            "record": expected_file_sha256,
            "body": expected_body_sha256,
            "references": reference_values,
        })),
    }


def _recovery_finalization_binding(
        owner_reference: dict[str, Any],
) -> tuple[str, int, bytes, str, str, dict[str, dict[str, Any]]]:
    failure = "recovery finalization owner binding invalid"
    durable = owner_reference.get("durable_owners")
    owners = owner_reference.get("owners")
    if (
            not isinstance(durable, list) or not durable or
            not isinstance(owners, list) or len(owners) != len(durable)):
        raise LocalPaperError(failure)
    rows: list[tuple[str, bytes]] = []
    by_token: dict[str, dict[str, Any]] = {}
    accounts: set[str] = set()
    domains: set[str] = set()
    for item in durable:
        if not isinstance(item, dict):
            raise LocalPaperError(failure)
        token = item.get("token_sha256")
        generation = item.get("lease_generation")
        account = item.get("owner_account")
        domain = item.get("owner_execution_domain")
        if (
                not isinstance(token, str) or DIGEST.fullmatch(token) is None or
                token in by_token or type(generation) is not int or
                generation < 1 or not isinstance(account, str) or
                re.fullmatch(r"DU[0-9]{1,16}", account) is None or
                domain != "PAPER:alpha" or
                item.get("paper_finalization_required") is not True):
            raise LocalPaperError(failure)
        by_token[token] = item
        accounts.add(account)
        domains.add(domain)
        rows.append((token, (
            f"{token}\t{generation}\t{account.encode().hex()}\t"
            f"{domain.encode().hex()}\n").encode("ascii")))
    owner_tokens = {
        item.get("token_sha256") for item in owners
        if isinstance(item, dict)}
    if (
            len(owner_tokens) != len(owners) or
            owner_tokens != set(by_token) or len(accounts) != 1 or
            len(domains) != 1):
        raise LocalPaperError(failure)
    rows.sort(key=lambda item: item[0])
    canonical = b"".join(row for _token, row in rows)
    return (
        _sha256(canonical), len(rows), canonical, next(iter(accounts)),
        next(iter(domains)), by_token)


def _parse_ordered_finalization_receipt(
        value: Any, *, keys: tuple[str, ...], failure: str,
) -> tuple[dict[str, str], bytes]:
    if not isinstance(value, str):
        raise LocalPaperError(failure)
    try:
        raw = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise LocalPaperError(failure) from error
    if (not 1 <= len(raw) <= 4096 or
            not raw.endswith(b"\n") or b"\r" in raw or b"\x00" in raw):
        raise LocalPaperError(failure)
    rows = raw[:-1].split(b"\n")
    if len(rows) != len(keys):
        raise LocalPaperError(failure)
    receipt: dict[str, str] = {}
    for row, expected_key in zip(rows, keys, strict=True):
        key, separator, raw_value = row.partition(b"=")
        try:
            decoded_key = key.decode("ascii")
            decoded_value = raw_value.decode("ascii")
        except UnicodeDecodeError as error:
            raise LocalPaperError(failure) from error
        if separator != b"=" or decoded_key != expected_key:
            raise LocalPaperError(failure)
        receipt[expected_key] = decoded_value
    if tuple(receipt) != keys:
        raise LocalPaperError(failure)
    return receipt, raw


def _validate_preliminary_finalization_result(
        result: Any, *, recovery: dict[str, Any],
        finalization_id: str, owner_set_sha256: str, owner_count: int,
        owner_set_canonical: bytes, common_account: str, common_domain: str,
        owner_by_token: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    failure = "recovery preliminary finalization result invalid"
    if (not isinstance(result, dict) or
            set(result) != PRELIMINARY_FINALIZATION_RESULT_FIELDS):
        raise LocalPaperError(failure)
    bool_fields = {
        "accepted", "paper_finalization_required",
        "owner_audit_authoritative", "owner_audit_complete",
        "broker_post_fill_risk_reconciliation_pending",
        "broker_recovery_audit_barrier_complete",
        "broker_recovery_audit_new_connection_epoch_required",
    }
    int_fields = {
        "lease_generation", "expected_owner_count",
        "owner_active_order_count", "owner_uncertain_command_count",
        "execution_service_fencing_generation", "broker_connection_epoch",
        "broker_active_generation", "broker_terminal_generation",
        "broker_risk_generation", "broker_account_generation",
        "broker_position_generation", "broker_fx_cash_generation",
        "broker_exposure_generation",
        "broker_terminal_exposure_generation",
        "broker_risk_absorbed_exposure_generation",
        "broker_global_active_order_count",
    }
    if (
            any(type(result.get(field)) is not bool for field in bool_fields) or
            any(type(result.get(field)) is not int for field in int_fields) or
            any(not isinstance(result.get(field), str)
                for field in set(result) - bool_fields - int_fields)):
        raise LocalPaperError(failure)
    owner = owner_by_token.get(str(result.get("owner_token_sha256")))
    if (
            owner is None or
            result.get("accepted") is not True or
            result.get("paper_finalization_required") is not True or
            result.get("reason_code") !=
                "PAPER_FINALIZATION_AUDIT_SEALED" or
            result.get("paper_finalization_state") != "AUDIT_SEALED" or
            result.get("recovery_id") != recovery.get("recovery_id") or
            result.get("finalization_id") != finalization_id or
            result.get("expected_owner_set_sha256") != owner_set_sha256 or
            result.get("expected_owner_count") != owner_count or
            result.get("lease_generation") != owner.get("lease_generation")):
        raise LocalPaperError(failure)
    receipt, receipt_raw = _parse_ordered_finalization_receipt(
        result.get("finalization_receipt"),
        keys=PRELIMINARY_FINALIZATION_RECEIPT_KEYS, failure=failure)
    if (
            not isinstance(result.get("finalization_receipt_sha256"), str) or
            DIGEST.fullmatch(result["finalization_receipt_sha256"]) is None or
            _sha256(receipt_raw) != result["finalization_receipt_sha256"] or
            receipt.get("schema") !=
                "hepta.paper-session-finalization-receipt.v1" or
            receipt.get("version") != "1" or
            receipt.get("status") != "AUDIT_SEALED" or
            receipt.get("recovery_id") != recovery.get("recovery_id") or
            receipt.get("finalization_id") != finalization_id or
            receipt.get("expected_owner_set_sha256") != owner_set_sha256 or
            receipt.get("expected_owner_count") != str(owner_count) or
            receipt.get("owner_set_canonical_hex") !=
                owner_set_canonical.hex() or
            receipt.get("owner_account") != common_account or
            receipt.get("owner_execution_domain") != common_domain or
            receipt.get("paper_only") != "1" or
            receipt.get("live_authorized") != "0"):
        raise LocalPaperError(failure)
    paired_ints = int_fields - {"lease_generation", "expected_owner_count"}
    if any(receipt.get(field) != str(result[field]) for field in paired_ints):
        raise LocalPaperError(failure)
    for field in (
            "owner_account", "owner_execution_domain",
            "execution_service_epoch", "broker_position_quantity",
            "broker_gross_absolute_position"):
        if receipt.get(field) != result[field]:
            raise LocalPaperError(failure)
    positive = {
        "execution_service_fencing_generation", "broker_connection_epoch",
        "broker_active_generation", "broker_terminal_generation",
        "broker_risk_generation", "broker_account_generation",
        "broker_position_generation", "broker_fx_cash_generation",
    }
    if (
            any(result[field] < 1 for field in positive) or
            result["broker_exposure_generation"] < 0 or
            result["broker_risk_absorbed_exposure_generation"] < 0 or
            result["broker_terminal_exposure_generation"] < 0 or
            result["broker_terminal_exposure_generation"] >
                result["broker_risk_absorbed_exposure_generation"] or
            result["broker_risk_absorbed_exposure_generation"] !=
                result["broker_exposure_generation"] or
            any(result[field] != 0 for field in (
                "broker_global_active_order_count", "owner_active_order_count",
                "owner_uncertain_command_count")) or
            result.get("owner_audit_authoritative") is not True or
            result.get("owner_audit_complete") is not True or
            result.get("broker_post_fill_risk_reconciliation_pending") is not
                False or
            result.get("broker_recovery_audit_barrier_complete") is not True or
            result.get("broker_recovery_audit_new_connection_epoch_required")
                is not False or
            receipt.get("broker_post_fill_risk_reconciliation_pending") !=
                "0" or
            receipt.get("broker_recovery_audit_barrier_complete") != "1" or
            receipt.get("broker_recovery_audit_new_connection_epoch_required")
                != "0" or
            result.get("broker_position_quantity") != "0" or
            result.get("broker_gross_absolute_position") != "0" or
            result.get("owner_account") != common_account or
            result.get("owner_execution_domain") != common_domain or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", str(
                result.get("execution_service_epoch"))) is None):
        raise LocalPaperError(failure)
    return result


def _validate_terminal_ack_result(
        result: Any, *, preliminary: dict[str, Any],
        recovery: dict[str, Any], finalization_id: str,
        owner_set_sha256: str, owner_count: int,
        owner_set_canonical: bytes, common_account: str, common_domain: str,
        owner_by_token: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    failure = "recovery terminal ACK result invalid"
    if not isinstance(result, dict) or set(result) != TERMINAL_ACK_RESULT_FIELDS:
        raise LocalPaperError(failure)
    bool_fields = {
        "accepted", "paper_finalization_required",
        "owner_audit_authoritative", "owner_audit_complete",
        "broker_post_fill_risk_reconciliation_pending",
        "broker_recovery_audit_barrier_complete",
        "broker_recovery_audit_new_connection_epoch_required",
        "execution_mutation_gate_closed", "broker_transport_connected",
        "broker_event_ingress_halted", "broker_callback_queue_drained",
        "broker_reconnect_permitted", "terminal_latch_durable",
        "terminal_runtime_latch_loaded", "terminal_runtime_verified",
        "terminal_replay",
    }
    int_fields = {
        "lease_generation", "expected_owner_count",
        "owner_active_order_count", "owner_uncertain_command_count",
        "execution_service_fencing_generation", "broker_connection_epoch",
        "broker_active_generation", "broker_terminal_generation",
        "broker_risk_generation", "broker_account_generation",
        "broker_position_generation", "broker_fx_cash_generation",
        "broker_exposure_generation",
        "broker_terminal_exposure_generation",
        "broker_risk_absorbed_exposure_generation",
        "broker_global_active_order_count",
        "terminalization_service_fencing_generation",
        "terminalization_generation", "broker_callbacks_in_flight",
    }
    if (
            any(type(result.get(field)) is not bool for field in bool_fields) or
            any(type(result.get(field)) is not int for field in int_fields) or
            any(not isinstance(result.get(field), str)
                for field in set(result) - bool_fields - int_fields)):
        raise LocalPaperError(failure)
    expected_token = min(owner_by_token)
    owner = owner_by_token[expected_token]
    preliminary_sha256 = preliminary.get("finalization_receipt_sha256")
    terminal_sha256 = result.get("finalization_receipt_sha256")
    if (
            result.get("accepted") is not True or
            result.get("reason_code") !=
                "PAPER_FINALIZATION_TERMINAL_ACKED" or
            result.get("paper_finalization_state") != "ACKED" or
            result.get("paper_finalization_required") is not True or
            result.get("recovery_id") != recovery.get("recovery_id") or
            result.get("finalization_id") != finalization_id or
            result.get("expected_owner_set_sha256") != owner_set_sha256 or
            result.get("expected_owner_count") != owner_count or
            result.get("owner_token_sha256") != expected_token or
            result.get("lease_generation") != owner.get("lease_generation") or
            result.get("preliminary_finalization_receipt_sha256") !=
                preliminary_sha256 or
            not isinstance(preliminary_sha256, str) or
            DIGEST.fullmatch(preliminary_sha256) is None or
            not isinstance(terminal_sha256, str) or
            DIGEST.fullmatch(terminal_sha256) is None or
            terminal_sha256 == preliminary_sha256):
        raise LocalPaperError(failure)
    receipt, receipt_raw = _parse_ordered_finalization_receipt(
        result.get("finalization_receipt"), keys=TERMINAL_ACK_RECEIPT_KEYS,
        failure=failure)
    if (
            _sha256(receipt_raw) != terminal_sha256 or
            receipt.get("schema") !=
                "hepta.paper-session-terminal-ack-receipt.v2" or
            receipt.get("version") != "2" or
            receipt.get("status") != "TERMINAL_ACKED" or
            receipt.get("recovery_id") != recovery.get("recovery_id") or
            receipt.get("finalization_id") != finalization_id or
            receipt.get("expected_owner_set_sha256") != owner_set_sha256 or
            receipt.get("expected_owner_count") != str(owner_count) or
            receipt.get("owner_set_canonical_hex") !=
                owner_set_canonical.hex() or
            receipt.get("preliminary_finalization_receipt_sha256") !=
                preliminary_sha256 or
            receipt.get("owner_account") != common_account or
            receipt.get("owner_execution_domain") != common_domain or
            receipt.get("paper_only") != "1" or
            receipt.get("live_authorized") != "0"):
        raise LocalPaperError(failure)
    paired_ints = {
        "execution_service_fencing_generation", "terminalization_generation",
        "broker_connection_epoch", "broker_active_generation",
        "broker_terminal_generation", "broker_risk_generation",
        "broker_account_generation", "broker_position_generation",
        "broker_fx_cash_generation", "broker_exposure_generation",
        "broker_terminal_exposure_generation",
        "broker_risk_absorbed_exposure_generation",
        "broker_global_active_order_count", "owner_active_order_count",
        "owner_uncertain_command_count", "broker_callbacks_in_flight",
    }
    if any(receipt.get(field) != str(result[field]) for field in paired_ints):
        raise LocalPaperError(failure)
    for field in (
            "owner_account", "owner_execution_domain",
            "execution_service_epoch", "terminal_latch_sha256",
            "broker_position_quantity", "broker_gross_absolute_position"):
        if receipt.get(field) != result[field]:
            raise LocalPaperError(failure)
    fixed_receipt = {
        "execution_mutation_gate_closed": "1",
        "broker_transport_connected": "0",
        "broker_event_ingress_halted": "1",
        "broker_callback_queue_drained": "1",
        "broker_reconnect_permitted": "0",
        "terminal_latch_durable": "1",
        "broker_post_fill_risk_reconciliation_pending": "0",
        "broker_recovery_audit_barrier_complete": "1",
        "broker_recovery_audit_new_connection_epoch_required": "0",
    }
    if any(receipt.get(field) != expected
           for field, expected in fixed_receipt.items()):
        raise LocalPaperError(failure)
    positive = {
        "execution_service_fencing_generation", "broker_connection_epoch",
        "broker_active_generation", "broker_terminal_generation",
        "broker_risk_generation", "broker_account_generation",
        "broker_position_generation", "broker_fx_cash_generation",
        "terminalization_service_fencing_generation",
        "terminalization_generation",
    }
    zero_digest = "sha256:" + "0" * 64
    if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", str(
                result.get("execution_service_epoch"))) is None or
            result.get("terminalization_service_epoch") !=
                result.get("execution_service_epoch") or
            result.get("terminalization_service_fencing_generation") !=
                result.get("execution_service_fencing_generation") or
            result.get("execution_service_epoch") !=
                preliminary.get("execution_service_epoch") or
            result.get("execution_service_fencing_generation") !=
                preliminary.get("execution_service_fencing_generation") or
            any(result[field] < 1 for field in positive) or
            result.get("terminalization_generation") != 1 or
            not isinstance(result.get("terminal_latch_sha256"), str) or
            DIGEST.fullmatch(str(result["terminal_latch_sha256"])) is None or
            result.get("terminal_latch_sha256") == zero_digest or
            result.get("owner_account") != common_account or
            result.get("owner_execution_domain") != common_domain or
            result.get("owner_audit_authoritative") is not True or
            result.get("owner_audit_complete") is not True or
            result.get("execution_mutation_gate_closed") is not True or
            result.get("broker_transport_connected") is not False or
            result.get("broker_event_ingress_halted") is not True or
            result.get("broker_callback_queue_drained") is not True or
            result.get("broker_callbacks_in_flight") != 0 or
            result.get("broker_reconnect_permitted") is not False or
            result.get("terminal_latch_durable") is not True or
            result.get("terminal_runtime_latch_loaded") is not True or
            result.get("terminal_runtime_verified") is not True or
            result.get("terminal_replay") is not True or
            result.get("broker_post_fill_risk_reconciliation_pending") is not
                False or
            result.get("broker_recovery_audit_barrier_complete") is not True or
            result.get("broker_recovery_audit_new_connection_epoch_required")
                is not False or
            result.get("broker_position_quantity") != "0" or
            result.get("broker_gross_absolute_position") != "0" or
            any(result[field] != 0 for field in (
                "broker_global_active_order_count", "owner_active_order_count",
                "owner_uncertain_command_count")) or
            result["broker_exposure_generation"] < 0 or
            result["broker_terminal_exposure_generation"] < 0 or
            result["broker_risk_absorbed_exposure_generation"] < 0 or
            result["broker_terminal_exposure_generation"] >
                result["broker_risk_absorbed_exposure_generation"] or
            result["broker_risk_absorbed_exposure_generation"] !=
                result["broker_exposure_generation"]):
        raise LocalPaperError(failure)
    return result


def validate_recovery_completion(
        *, completion_path: Path, expected_file_sha256: str,
        expected_body_sha256: str, recovery: dict[str, Any],
        recovery_file_sha256: str, recovery_body_sha256: str,
        now_ms: int | None = None,
) -> dict[str, Any]:
    if (
            completion_path != RECOVERY_COMPLETION_PATH or
            any(not isinstance(value, str) or DIGEST.fullmatch(value) is None or
                value == "sha256:" + "0" * 64
                for value in (
                    expected_file_sha256, expected_body_sha256,
                    recovery_file_sha256, recovery_body_sha256))):
        raise LocalPaperError("recovery completion arguments invalid")
    payload, _metadata = _secure_read(completion_path, mode=0o600)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalPaperError("recovery completion invalid") from error
    document = _validate_canonical_seal(
        payload, value, "recovery completion invalid")
    owner_reference, _owner_recorded_at_ms = _validate_recovery_reference(
        recovery.get("session_owner_set_reference"),
        field="session_owner_set_reference",
        campaign_id=str(recovery.get("campaign_id")),
        suspension_id=str(recovery.get("suspension_id")),
        source_baseline_sha256=str(recovery.get("source_baseline_sha256")))
    (owner_set_sha256, owner_count, owner_set_canonical, common_account,
     common_domain, owner_by_token) = _recovery_finalization_binding(
         owner_reference)
    timestamp = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    completed_at_ms = document.get("completed_at_ms")
    expected_finalization_id = "paper-finalization-" + hashlib.sha256((
        str(recovery.get("recovery_id")) + "\n" + owner_set_sha256 + "\n" +
        str(owner_count) + "\n").encode("ascii")).hexdigest()[:32]
    preliminary_sha256 = document.get(
        "preliminary_finalization_receipt_sha256")
    terminal_sha256 = document.get("terminal_ack_receipt_sha256")
    terminal_latch_sha256 = document.get("terminal_latch_sha256")
    if (
            set(document) != RECOVERY_COMPLETION_FIELDS or
            document.get("schema") != RECOVERY_COMPLETION_SCHEMA or
            document.get("version") != 3 or
            document.get("status") != RECOVERY_COMPLETION_STATUS or
            document.get("recovery_id") != recovery.get("recovery_id") or
            document.get("domain") != "alpha" or
            document.get("campaign_id") != recovery.get("campaign_id") or
            document.get("suspension_id") != recovery.get("suspension_id") or
            document.get("source_baseline_sha256") !=
                recovery.get("source_baseline_sha256") or
            document.get("recovery_authority_file_sha256") !=
                recovery_file_sha256 or
            document.get("recovery_authority_body_sha256") !=
                recovery_body_sha256 or
            not isinstance(document.get("finalization_id"), str) or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", str(
                document["finalization_id"])) is None or
            document.get("finalization_id") != expected_finalization_id or
            document.get("expected_owner_set_sha256") != owner_set_sha256 or
            type(document.get("expected_owner_count")) is not int or
            document.get("expected_owner_count") != owner_count or
            any(not isinstance(item, str) or DIGEST.fullmatch(item) is None or
                item == "sha256:" + "0" * 64
                for item in (
                    preliminary_sha256, terminal_sha256,
                    terminal_latch_sha256)) or
            terminal_sha256 == preliminary_sha256 or
            type(completed_at_ms) is not int or
            not recovery.get("recorded_at_ms", timestamp + 1) <=
                completed_at_ms <= timestamp or
            type(document.get("session_owner_count")) is not int or
            document.get("session_owner_count") != owner_count or
            document.get("session_owner_count") !=
                recovery.get("session_owner_count") or
            document.get("all_original_session_owners_closed") is not True or
            document.get("terminal_acknowledged") is not True or
            document.get("terminal_runtime_replay_verified") is not True or
            document.get("hsl_owner_purged") is not True or
            document.get("position_quantity") != "0" or
            document.get("gross_absolute_position") != "0" or
            type(document.get("active_order_count")) is not int or
            document.get("active_order_count") != 0 or
            document.get("paper_only") is not True or
            document.get("live_authorized") is not False or
            _sha256(payload) != expected_file_sha256 or
            document.get("body_sha256") != expected_body_sha256):
        raise LocalPaperError("recovery completion invalid")
    reference = document.get("authoritative_flat_receipt_reference")
    if (
            not isinstance(reference, dict) or
            set(reference) != RECOVERY_REFERENCE_FIELDS or
            reference.get("schema") != RECOVERY_TERMINAL_FLAT_SCHEMA or
            reference.get("status") != RECOVERY_TERMINAL_FLAT_STATUS or
            not isinstance(reference.get("path"), str) or
            not Path(reference["path"]).is_absolute() or
            any(not isinstance(reference.get(field), str) or
                DIGEST.fullmatch(reference[field]) is None
                for field in ("file_sha256", "body_sha256")) or
            type(reference.get("bytes")) is not int or
            not 1 <= reference["bytes"] <= MAX_EXTERNAL_P1_JSON_BYTES or
            reference.get("mode") != stat.S_IFREG | 0o600 or
            reference.get("uid") != ROOT_UID or
            reference.get("gid") != ROOT_GID or reference.get("nlink") != 1):
        raise LocalPaperError("recovery terminal-flat reference invalid")
    flat_path = Path(reference["path"])
    suspension_id = str(recovery.get("suspension_id"))
    expected_flat_path = LOCAL_PAPER_STATE_ROOT / (
        "external-recovery-" + hashlib.sha256(
            suspension_id.encode("utf-8")).hexdigest()[:24] +
        ".terminal-flat.json")
    if flat_path != expected_flat_path:
        raise LocalPaperError("recovery terminal-flat reference invalid")
    flat_payload, flat_metadata = _secure_read(flat_path, mode=0o600)
    if (
            len(flat_payload) != reference["bytes"] or
            flat_metadata.st_mode != reference["mode"] or
            _sha256(flat_payload) != reference["file_sha256"]):
        raise LocalPaperError("recovery terminal-flat reference drifted")
    try:
        flat_value = json.loads(flat_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalPaperError("recovery terminal-flat invalid") from error
    flat = _validate_canonical_seal(
        flat_payload, flat_value, "recovery terminal-flat invalid")
    preliminary = flat.get("preliminary_finalization_result")
    terminal_ack = flat.get("terminal_ack_result")
    finalization_id = str(document["finalization_id"])
    preliminary_result = _validate_preliminary_finalization_result(
        preliminary, recovery=recovery,
        finalization_id=finalization_id,
        owner_set_sha256=owner_set_sha256, owner_count=owner_count,
        owner_set_canonical=owner_set_canonical,
        common_account=common_account, common_domain=common_domain,
        owner_by_token=owner_by_token)
    terminal_result = _validate_terminal_ack_result(
        terminal_ack, preliminary=preliminary_result, recovery=recovery,
        finalization_id=finalization_id,
        owner_set_sha256=owner_set_sha256, owner_count=owner_count,
        owner_set_canonical=owner_set_canonical,
        common_account=common_account, common_domain=common_domain,
        owner_by_token=owner_by_token)
    diagnostic_proofs = flat.get(
        "pre_finalization_diagnostic_zero_exposure_proofs")
    if (
            set(flat) != RECOVERY_TERMINAL_FLAT_FIELDS or
            flat.get("schema") != RECOVERY_TERMINAL_FLAT_SCHEMA or
            flat.get("version") != 3 or
            flat.get("status") != RECOVERY_TERMINAL_FLAT_STATUS or
            flat.get("completed_at_ms") != completed_at_ms or
            flat.get("domain") != "alpha" or
            flat.get("campaign_id") != recovery.get("campaign_id") or
            flat.get("suspension_id") != recovery.get("suspension_id") or
            flat.get("source_baseline_sha256") !=
                recovery.get("source_baseline_sha256") or
            flat.get("recovery_id") != recovery.get("recovery_id") or
            flat.get("finalization_id") != finalization_id or
            flat.get("expected_owner_set_sha256") != owner_set_sha256 or
            type(flat.get("expected_owner_count")) is not int or
            flat.get("expected_owner_count") != owner_count or
            flat.get("preliminary_finalization_receipt_sha256") !=
                preliminary_sha256 or
            flat.get("preliminary_finalization_receipt_sha256") !=
                preliminary_result.get("finalization_receipt_sha256") or
            flat.get("preliminary_finalization_receipt") !=
                preliminary_result.get("finalization_receipt") or
            flat.get("terminal_ack_receipt_sha256") != terminal_sha256 or
            flat.get("terminal_ack_receipt_sha256") !=
                terminal_result.get("finalization_receipt_sha256") or
            flat.get("terminal_ack_receipt") !=
                terminal_result.get("finalization_receipt") or
            flat.get("terminal_latch_sha256") != terminal_latch_sha256 or
            flat.get("terminal_latch_sha256") !=
                terminal_result.get("terminal_latch_sha256") or
            type(flat.get("session_owner_count")) is not int or
            flat.get("session_owner_count") != owner_count or
            flat.get("session_owner_count") !=
                recovery.get("session_owner_count") or
            flat.get("session_owner_token_sha256s") != sorted(owner_by_token) or
            flat.get("all_original_session_owners_closed") is not True or
            flat.get("terminal_acknowledged") is not True or
            flat.get("terminal_runtime_replay_verified") is not True or
            flat.get("hsl_owner_purged") is not True or
            flat.get("position_quantity") != "0" or
            flat.get("gross_absolute_position") != "0" or
            type(flat.get("active_order_count")) is not int or
            flat.get("active_order_count") != 0 or
            flat.get("paper_only") is not True or
            flat.get("live_authorized") is not False or
            flat.get("body_sha256") != reference["body_sha256"] or
            not isinstance(diagnostic_proofs, list)):
        raise LocalPaperError("recovery terminal-flat invalid")
    return document


def _environment_from_payload(payload: bytes, path: Path) -> dict[str, str]:
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise LocalPaperError(f"invalid PAPER environment: {path}") from error
    result: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if (
                not separator or not key or key in result or
                re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None):
            raise LocalPaperError(f"invalid PAPER environment line: {path}")
        result[key] = value
    return result


def _canonical_positive_decimal(value: str, maximum: int) -> bool:
    if re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value) is None:
        return False
    numerator = value.replace(".", "")
    scale = len(value) - value.index(".") - 1 if "." in value else 0
    return int(numerator) > 0 and int(numerator) <= maximum * (10 ** scale)


def _external_p1_paper_environment(
        path: Path,
) -> tuple[dict[str, str], bytes, os.stat_result]:
    """Securely open and validate the one-canary PAPER runtime profile."""
    if path != EXTERNAL_P1_PAPER_ENV_PATH:
        raise LocalPaperError("external P1 PAPER profile path invalid")
    payload, metadata = _secure_read(
        path, mode=0o644, maximum=MAX_EXTERNAL_P1_ENV_BYTES)
    if (
            len(payload) != EXTERNAL_P1_PAPER_PROFILE_BYTES or
            _sha256(payload) != EXTERNAL_P1_PAPER_PROFILE_SHA256):
        raise LocalPaperError("external P1 PAPER profile artifact invalid")
    environment = _environment_from_payload(payload, path)
    account = environment.get("HEPTA_IB_PAPER_ACCOUNT", "")
    client_id = environment.get("HEPTA_IB_PAPER_CLIENT_ID", "")
    quote_age = environment.get("HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS", "")
    if (
            environment.get("HEPTA_IB_EXECUTION_MODE") != "PAPER" or
            re.fullmatch(r"DU[0-9]{1,16}", account) is None or
            environment.get("HEPTA_IB_PAPER_HOST") != "127.0.0.1" or
            environment.get("HEPTA_IB_PAPER_PORT") != "4002" or
            re.fullmatch(r"[1-9][0-9]{0,4}", client_id) is None or
            int(client_id) > 65535 or
            environment.get("HEPTA_IB_PAPER_MAX_ORDER_QTY") != "1" or
            not _canonical_positive_decimal(
                environment.get("HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL", ""),
                35_000) or
            environment.get("HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE") != "1" or
            environment.get("HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS") != "1" or
            environment.get("HEPTA_IB_PAPER_MAX_GROSS_POSITION") != "1" or
            environment.get("HEPTA_IB_PAPER_QUOTE_CONTRACTS") !=
                "EUR.USD|EUR|CASH|IDEALPRO|USD" or
            environment.get("HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT") !=
                "EUR.USD" or
            re.fullmatch(r"[1-9][0-9]*", quote_age) is None or
            not 100 <= int(quote_age) <= EXTERNAL_P1_MAX_QUOTE_AGE_MS or
            environment.get("HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID") != "alpha" or
            environment.get("HEPTA_IB_EXECUTION_DOMAIN_ID") != "PAPER:alpha"):
        raise LocalPaperError("external P1 PAPER profile boundary invalid")
    return environment, payload, metadata


def _validate_profile_evidence(
        value: Any, *, path: Path, sha256: str, size: int, mode: int,
        sealed: bool = False,
) -> dict[str, Any]:
    fields = (
        PROFILE_SEALED_EVIDENCE_FIELDS if sealed else
        PROFILE_FILE_EVIDENCE_FIELDS)
    if (
            not isinstance(value, dict) or set(value) != fields or
            value.get("path") != str(path) or
            not isinstance(value.get("file_sha256"), str) or
            DIGEST.fullmatch(value["file_sha256"]) is None or
            value.get("file_sha256") != sha256 or
            value.get("bytes") != size or
            value.get("mode") != stat.S_IFREG | mode or
            value.get("uid") != ROOT_UID or value.get("gid") != ROOT_GID or
            value.get("nlink") != 1 or
            any(type(value.get(field)) is not int or value[field] < 0
                for field in ("device", "inode", "mtime_ns", "ctime_ns")) or
            value.get("inode", 0) <= 0 or
            (sealed and (
                not isinstance(value.get("body_sha256"), str) or
                DIGEST.fullmatch(value["body_sha256"]) is None))):
        raise LocalPaperError("external P1 profile restoration invalid")
    return value


def _validate_external_handoff_document(
        payload: bytes, *, expected_file_sha256: str,
        expected_body_sha256: str, campaign_id: str,
        source_baseline_sha256: str, now_ms: int,
        require_fresh: bool = True,
) -> dict[str, Any]:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalPaperError("external P1 handoff invalid") from error
    if (
            not isinstance(document, dict) or
            set(document) != EXTERNAL_P1_HANDOFF_FIELDS or
            payload != _canonical_json(document) or
            document.get("schema") != EXTERNAL_P1_HANDOFF_SCHEMA or
            document.get("version") != 2 or
            document.get("status") != EXTERNAL_P1_HANDOFF_STATUS or
            document.get("round") != 114 or document.get("domain") != "alpha" or
            document.get("campaign_id") != campaign_id or
            document.get("source_baseline_sha256") != source_baseline_sha256 or
            document.get("production_mode") != "PRODUCTION_ROOT_SYSTEMD" or
            _sha256(payload) != expected_file_sha256 or
            document.get("body_sha256") != expected_body_sha256):
        raise LocalPaperError("external P1 handoff invalid")
    body = dict(document)
    body_sha256 = body.pop("body_sha256", None)
    if (
            not isinstance(body_sha256, str) or
            DIGEST.fullmatch(body_sha256) is None or
            _sha256(_canonical_json(body)) != body_sha256):
        raise LocalPaperError("external P1 handoff seal invalid")
    issued = document.get("issued_at_ms")
    expires = document.get("expires_at_ms")
    if (
            type(issued) is not int or type(expires) is not int or
            expires - issued != 5 * 60 * 1000 or issued > now_ms or
            (require_fresh and now_ms >= expires)):
        raise LocalPaperError("external P1 handoff expired")
    if (
            document.get("watch_units_inactive") is not True or
            document.get("watch_authority_count") != 0 or
            document.get("watch_socket_count") != 0 or
            document.get("watch_timer_count") != 0 or
            document.get("paper_units_inactive") is not True or
            document.get("broker_deny_all") is not True or
            document.get("kill_switch_engaged") is not True or
            document.get("global_kill_switch_engaged") is not True or
            document.get("identity_count") != 0 or
            document.get("identity_manifest_sha256") !=
                DISABLED_IDENTITY_MANIFEST_SHA256 or
            document.get("paper_profile_restored") is not True or
            document.get("profile_candidate_absent") is not True or
            document.get("paper_runtime_profile_hardened") is not True or
            document.get("paper_runtime_profile_candidate_absent") is not True or
            document.get("crash_recovery_verified") is not True or
            document.get("cleanup_residue_count") != 0 or
            any(document.get(field) is not False for field in (
                "paper_authorized", "live_authorized", "mutation_authorized",
                "direct_broker_access", "order_submission_authorized"))):
        raise LocalPaperError("external P1 handoff boundary invalid")
    restoration = document.get("paper_profile_restoration")
    if (
            not isinstance(restoration, dict) or
            set(restoration) != PROFILE_RESTORATION_FIELDS or
            restoration.get("schema") !=
                "hepta.p1-watch-to-paper-profile-restoration.v1" or
            restoration.get("version") != 1 or
            restoration.get("status") != "DORMANT_PAPER_PROFILE_RESTORED" or
            restoration.get("candidate_path") != str(PROFILE_CANDIDATE_PATH) or
            restoration.get("retired_watch_path") !=
                str(PROFILE_RETIRED_WATCH_PATH) or
            restoration.get("exchange_method") != "RENAME_EXCHANGE" or
            restoration.get("forward_only_after_exchange") is not True):
        raise LocalPaperError("external P1 profile restoration invalid")
    _validate_profile_evidence(
        restoration.get("target"), path=PROFILE_TARGET_PATH,
        sha256=DORMANT_PAPER_PROFILE_SHA256,
        size=DORMANT_PAPER_PROFILE_BYTES, mode=0o644)
    for field, path in (
            ("dormant_backup", PROFILE_DORMANT_BACKUP_PATH),
            ("forward_retained_dormant", PROFILE_FORWARD_RETAINED_PATH)):
        _validate_profile_evidence(
            restoration.get(field), path=path,
            sha256=DORMANT_PAPER_PROFILE_SHA256,
            size=DORMANT_PAPER_PROFILE_BYTES, mode=0o600)
    _validate_profile_evidence(
        restoration.get("retired_watch"), path=PROFILE_RETIRED_WATCH_PATH,
        sha256=WATCH_PROFILE_SHA256, size=WATCH_PROFILE_BYTES, mode=0o600)
    for field, path in (
            ("forward_transition_receipt",
             PROFILE_FORWARD_TRANSITION_RECEIPT_PATH),
            ("profile_deployment_receipt", PROFILE_DEPLOYMENT_RECEIPT_PATH),
            ("forward_preimage_evidence", PROFILE_FORWARD_PREIMAGE_PATH)):
        value = restoration.get(field)
        if not isinstance(value, dict):
            raise LocalPaperError("external P1 profile restoration invalid")
        _validate_profile_evidence(
            value, path=path, sha256=str(value.get("file_sha256")),
            size=int(value.get("bytes", -1)), mode=0o600, sealed=True)
    for field in (
            "restore_intent_record_sha256", "restore_exchange_record_sha256"):
        value = restoration.get(field)
        if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
            raise LocalPaperError("external P1 profile restoration invalid")
    hardening = document.get("paper_runtime_profile_hardening")
    if (
            not isinstance(hardening, dict) or
            set(hardening) != RUNTIME_PROFILE_HARDENING_FIELDS or
            hardening.get("schema") !=
                "hepta.p1-watch-to-paper-runtime-profile-hardening.v1" or
            hardening.get("version") != 1 or
            hardening.get("status") != "PAPER_RUNTIME_PROFILE_HARDENED" or
            hardening.get("candidate_path") !=
                str(EXTERNAL_P1_PAPER_ENV_CANDIDATE_PATH) or
            hardening.get("retained_legacy_path") !=
                str(EXTERNAL_P1_PAPER_ENV_RETAINED_PATH) or
            hardening.get("exchange_method") != "RENAME_EXCHANGE" or
            hardening.get("forward_only_after_exchange") is not True):
        raise LocalPaperError("external P1 runtime profile hardening invalid")
    _validate_profile_evidence(
        hardening.get("target"), path=EXTERNAL_P1_PAPER_ENV_PATH,
        sha256=EXTERNAL_P1_PAPER_PROFILE_SHA256,
        size=EXTERNAL_P1_PAPER_PROFILE_BYTES, mode=0o644)
    for field, path in (
            ("legacy_backup", EXTERNAL_P1_PAPER_ENV_BACKUP_PATH),
            ("retained_legacy", EXTERNAL_P1_PAPER_ENV_RETAINED_PATH)):
        _validate_profile_evidence(
            hardening.get(field), path=path,
            sha256=EXTERNAL_P1_LEGACY_PAPER_PROFILE_SHA256,
            size=EXTERNAL_P1_LEGACY_PAPER_PROFILE_BYTES, mode=0o600)
    for field in (
            "harden_intent_record_sha256",
            "harden_exchange_record_sha256"):
        value = hardening.get(field)
        if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
            raise LocalPaperError(
                "external P1 runtime profile hardening invalid")
    return document


def _run_command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments, check=False, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=30, env=_child_environment())


def _require_external_p1_deny_all(
        *, identities_path: Path,
        command: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> str:
    payload, _metadata = _secure_read(identities_path, mode=0o600)
    if _sha256(payload) != DISABLED_IDENTITY_MANIFEST_SHA256:
        raise LocalPaperError("external P1 identity manifest drifted")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalPaperError("external P1 identity manifest invalid") from error
    if (
            not isinstance(document, dict) or
            document.get("schema") != IDENTITY_SCHEMA or
            document.get("version") != 1 or
            document.get("identities") != [] or
            document.get("paper_authorized") is not False or
            document.get("live_authorized") is not False or
            not isinstance(document.get("source_policy_sha256"), str) or
            DIGEST.fullmatch(document["source_policy_sha256"]) is None):
        raise LocalPaperError("external P1 identity manifest invalid")
    completed = command([
        "/usr/libexec/hepta-broker-egress-policy", "--policy",
        "/usr/share/heptatrader/hepta-broker-network-policy-v1.json",
        "--identity-manifest",
        "/usr/share/heptatrader/hepta-service-identities-v1.json",
        "--check-deny-all",
    ])
    pattern = re.compile(
        r"hepta_broker_egress_policy: PASS "
        r"policy_sha256=([0-9a-f]{64}) "
        r"authorized_connectors=0 authorized_uids= protected_ports=4\s*")
    match = pattern.fullmatch(completed.stdout) if completed.returncode == 0 else None
    if match is None:
        raise LocalPaperError("external P1 broker is not exact DENY_ALL")
    return "sha256:" + match.group(1)


def _require_kill_switch(path: Path, gid: int) -> None:
    payload, _metadata = _secure_read(
        path, mode=0o440, uid=ROOT_UID, gid=gid, maximum=64)
    if payload != b"engaged":
        raise LocalPaperError("external P1 kill switch is not engaged")


def _require_external_p1_runtime_inactive(
        command: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> None:
    for unit in EXTERNAL_P1_INERT_UNITS:
        completed = command([
            "/usr/bin/systemctl", "show", unit,
            "-p", "LoadState", "-p", "ActiveState", "-p", "Job",
        ])
        properties: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in properties:
                raise LocalPaperError("external P1 runtime state invalid")
            properties[key] = value
        if (
                completed.returncode != 0 or
                set(properties) != {"LoadState", "ActiveState", "Job"} or
                properties["LoadState"] not in {"loaded", "not-found"} or
                properties["ActiveState"] != "inactive" or
                properties["Job"] != ""):
            raise LocalPaperError("external P1 runtime is not inactive")


def _require_external_p1_residue_absent() -> None:
    if PROFILE_CANDIDATE_PATH.exists() or PROFILE_CANDIDATE_PATH.is_symlink():
        raise LocalPaperError("external P1 profile candidate residue")
    if (EXTERNAL_P1_PAPER_ENV_CANDIDATE_PATH.exists() or
            EXTERNAL_P1_PAPER_ENV_CANDIDATE_PATH.is_symlink()):
        raise LocalPaperError("external P1 PAPER profile candidate residue")
    if any(path.exists() or path.is_symlink() for path in EXTERNAL_P1_RESIDUE_PATHS):
        raise LocalPaperError("external P1 transaction residue")
    try:
        sessions = tuple(SESSION_ROOT.iterdir())
    except FileNotFoundError:
        sessions = ()
    if sessions:
        raise LocalPaperError("external P1 session residue")
    try:
        authorities = tuple(SESSION_AUTHORITY_ROOT.iterdir())
    except FileNotFoundError:
        authorities = ()
    if authorities:
        raise LocalPaperError("external P1 session authority residue")


def _reopen_external_p1_restoration_artifact(
        evidence: Any, *, path: Path, mode: int, sealed: bool = False,
) -> None:
    fields = (
        PROFILE_SEALED_EVIDENCE_FIELDS if sealed else
        PROFILE_FILE_EVIDENCE_FIELDS)
    if not isinstance(evidence, dict) or set(evidence) != fields:
        raise LocalPaperError("external P1 restoration artifact invalid")
    payload, metadata = _secure_read(
        path, mode=mode, maximum=max(
            MAX_EXTERNAL_P1_JSON_BYTES,
            int(evidence.get("bytes", 0))))
    if _file_evidence(path, payload, metadata) != {
            field: evidence[field] for field in PROFILE_FILE_EVIDENCE_FIELDS}:
        raise LocalPaperError("external P1 restoration artifact drifted")
    if sealed:
        try:
            document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LocalPaperError(
                "external P1 restoration seal invalid") from error
        body = dict(document) if isinstance(document, dict) else {}
        claimed = body.pop("body_sha256", None)
        if (
                not isinstance(document, dict) or
                payload != _canonical_json(document) or
                claimed != evidence.get("body_sha256") or
                not isinstance(claimed, str) or
                DIGEST.fullmatch(claimed) is None or
                _sha256(_canonical_json(body)) != claimed):
            raise LocalPaperError("external P1 restoration seal invalid")


def verify_external_p1(
        *, handoff_path: Path, expected_file_sha256: str,
        expected_body_sha256: str, campaign_id: str,
        source_baseline_sha256: str, identities_path: Path = DEFAULT_IDENTITIES,
        gateway_env_path: Path = PROFILE_TARGET_PATH,
        paper_env_path: Path = EXTERNAL_P1_PAPER_ENV_PATH,
        command: Callable[
            [list[str]], subprocess.CompletedProcess[str]] = _run_command,
        now_ms: int | None = None,
        require_fresh: bool = True,
        require_inert: bool = True,
        require_residue_absent: bool = True,
) -> dict[str, Any]:
    """Reopen and prove the finalized external-P1 dormant boundary."""
    if (
            handoff_path != EXTERNAL_P1_HANDOFF_PATH or
            gateway_env_path != PROFILE_TARGET_PATH or
            paper_env_path != EXTERNAL_P1_PAPER_ENV_PATH or
            not isinstance(campaign_id, str) or
            not campaign_id or DOMAIN.fullmatch("alpha") is None or
            DIGEST.fullmatch(expected_file_sha256) is None or
            expected_file_sha256 == "sha256:" + "0" * 64 or
            DIGEST.fullmatch(expected_body_sha256) is None or
            expected_body_sha256 == "sha256:" + "0" * 64 or
            DIGEST.fullmatch(source_baseline_sha256) is None or
            source_baseline_sha256 == "sha256:" + "0" * 64):
        raise LocalPaperError("external P1 verification arguments invalid")
    handoff_payload, _handoff_metadata = _secure_read(handoff_path, mode=0o600)
    document = _validate_external_handoff_document(
        handoff_payload, expected_file_sha256=expected_file_sha256,
        expected_body_sha256=expected_body_sha256, campaign_id=campaign_id,
        source_baseline_sha256=source_baseline_sha256,
        now_ms=(time.time_ns() // 1_000_000 if now_ms is None else now_ms),
        require_fresh=require_fresh)
    restoration = document["paper_profile_restoration"]
    for field, path, mode, sealed in (
            ("dormant_backup", PROFILE_DORMANT_BACKUP_PATH, 0o600, False),
            ("forward_retained_dormant", PROFILE_FORWARD_RETAINED_PATH,
             0o600, False),
            ("retired_watch", PROFILE_RETIRED_WATCH_PATH, 0o600, False),
            ("forward_transition_receipt",
             PROFILE_FORWARD_TRANSITION_RECEIPT_PATH, 0o600, True),
            ("profile_deployment_receipt",
             PROFILE_DEPLOYMENT_RECEIPT_PATH, 0o600, True),
            ("forward_preimage_evidence",
             PROFILE_FORWARD_PREIMAGE_PATH, 0o600, True)):
        _reopen_external_p1_restoration_artifact(
            restoration[field], path=path, mode=mode, sealed=sealed)
    hardening = document["paper_runtime_profile_hardening"]
    for field, path, mode in (
            ("target", EXTERNAL_P1_PAPER_ENV_PATH, 0o644),
            ("legacy_backup", EXTERNAL_P1_PAPER_ENV_BACKUP_PATH, 0o600),
            ("retained_legacy", EXTERNAL_P1_PAPER_ENV_RETAINED_PATH, 0o600)):
        _reopen_external_p1_restoration_artifact(
            hardening[field], path=path, mode=mode)
    profile_payload, profile_metadata = _secure_read(
        gateway_env_path, mode=0o644, maximum=DORMANT_PAPER_PROFILE_BYTES)
    if (
            len(profile_payload) != DORMANT_PAPER_PROFILE_BYTES or
            _sha256(profile_payload) != DORMANT_PAPER_PROFILE_SHA256 or
            _file_evidence(gateway_env_path, profile_payload, profile_metadata) !=
                document["paper_profile_restoration"]["target"]):
        raise LocalPaperError("external P1 current dormant profile drifted")
    paper_environment, paper_payload, paper_metadata = (
        _external_p1_paper_environment(paper_env_path))
    _require_kill_switch(DOMAIN_KILL_SWITCH_PATH, PAPER_CONTROL_GID)
    _require_kill_switch(GLOBAL_KILL_SWITCH_PATH, GLOBAL_PAPER_CONTROL_GID)
    if require_residue_absent:
        _require_external_p1_residue_absent()
    if require_inert:
        _require_external_p1_runtime_inactive(command)
    broker_policy_sha256 = _require_external_p1_deny_all(
        identities_path=identities_path, command=command)
    return {
        "mode": "DENY_ALL", "admission_mode": "external-p1-finalized",
        "campaign_id": campaign_id, "domain": "alpha",
        "paper_authorized": False, "live_authorized": False,
        "identity_count": 0,
        "identity_manifest_sha256": DISABLED_IDENTITY_MANIFEST_SHA256,
        "broker_policy_sha256": broker_policy_sha256,
        "watch_handoff_receipt_file_sha256": expected_file_sha256,
        "watch_handoff_receipt_body_sha256": expected_body_sha256,
        "source_baseline_sha256": source_baseline_sha256,
        "dormant_profile_sha256": DORMANT_PAPER_PROFILE_SHA256,
        "paper_runtime_profile_sha256": _sha256(paper_payload),
        "quote_max_age_ms": int(
            paper_environment["HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"]),
        "boundary_fingerprint": _sha256(_canonical_json({
            "handoff": expected_file_sha256,
            "handoff_body": expected_body_sha256,
            "profile": _file_evidence(
                gateway_env_path, profile_payload, profile_metadata),
            "paper_runtime_profile": _file_evidence(
                paper_env_path, paper_payload, paper_metadata),
            "identity_manifest": DISABLED_IDENTITY_MANIFEST_SHA256,
            "broker": broker_policy_sha256,
        })),
    }


def _atomic_write(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        mode)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    os.chmod(path, mode)
    _fsync_directory(path.parent)


def _read_env(path: Path) -> dict[str, str]:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise LocalPaperError(f"missing PAPER environment: {path}") from error
    return _environment_from_payload(payload, path)


def _set_environment_value(path: Path, key: str, value: str) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as error:
        raise LocalPaperError(f"missing PAPER environment: {path}") from error
    matches = 0
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        current_key, separator, _current_value = stripped.partition("=")
        if (not stripped.startswith("#") and separator and
                current_key == key):
            matches += 1
            if matches == 1:
                rendered.append(f"{key}={value}")
            continue
        rendered.append(line)
    if matches == 0:
        rendered.append(f"{key}={value}")
    _atomic_write(path, ("\n".join(rendered) + "\n").encode("ascii"), mode)


def _environment_with_value(path: Path, key: str, value: str) -> tuple[bytes, int]:
    """Render an environment update without changing a supervised input."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as error:
        raise LocalPaperError(f"missing PAPER environment: {path}") from error
    matches = 0
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        current_key, separator, _current_value = stripped.partition("=")
        if (not stripped.startswith("#") and separator and
                current_key == key):
            matches += 1
            if matches == 1:
                rendered.append(f"{key}={value}")
            continue
        rendered.append(line)
    if matches == 0:
        rendered.append(f"{key}={value}")
    return ("\n".join(rendered) + "\n").encode("ascii"), mode


def _validate_paper_environment(path: Path) -> None:
    environment = _read_env(path)
    account = environment.get("HEPTA_IB_PAPER_ACCOUNT", "")
    host = environment.get("HEPTA_IB_PAPER_HOST", "")
    port = environment.get("HEPTA_IB_PAPER_PORT", "")
    if (
            environment.get("HEPTA_IB_EXECUTION_MODE") != "PAPER" or
            not account.startswith("DU") or
            host not in {"127.0.0.1", "::1", "localhost"} or
            port not in {"4002", "7497"} or
            environment.get("HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS") != "1"):
        raise LocalPaperError("local PAPER environment safety boundary failed")


def _paper_gateway_environment(
        current_path: Path, paper_path: Path, domain: str,
        execution_uid: int, *,
        current_environment: dict[str, str] | None = None,
        paper_environment: dict[str, str] | None = None) -> bytes:
    current = (
        _read_env(current_path) if current_environment is None else
        current_environment)
    paper = (
        _read_env(paper_path) if paper_environment is None else
        paper_environment)
    account = paper["HEPTA_IB_PAPER_ACCOUNT"]
    values = {
        **current,
        "HEPTA_EXECUTION_REMOTE_MODE": "PAPER",
        "HEPTA_EXECUTION_SOCKET": f"/run/hepta-execution-{domain}/execution.sock",
        "HEPTA_EXECUTION_EVENT_SOCKET": f"/run/hepta-execution-{domain}/events.sock",
        "HEPTA_EXECUTION_SERVICE_UID": str(execution_uid),
        "HEPTA_TOOL_ACCOUNT": account,
        "HEPTA_TOOL_AGENT_ID": domain,
        "HEPTA_EXECUTION_DOMAIN_ID": f"PAPER:{domain}",
        "HEPTA_TOOL_ALLOW_TRADE": "1",
        "HEPTA_TOOL_SESSION_TEMPLATES": "watch,paper",
        "HEPTA_TOOL_MAX_ORDER_QTY": paper.get(
            "HEPTA_IB_PAPER_MAX_ORDER_QTY", "1"),
        "HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN": paper.get(
            "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE", "1"),
        "HEPTA_TOOL_DECISION_LEASE_TTL_MS": "5000",
    }
    order = [
        "HEPTA_EXECUTION_REMOTE_MODE", "HEPTA_EXECUTION_SOCKET",
        "HEPTA_EXECUTION_EVENT_SOCKET", "HEPTA_EXECUTION_SERVICE_UID",
        "HEPTA_EXECUTION_IO_TIMEOUT_MS", "HEPTA_EXECUTION_MAX_RESPONSE_BYTES",
        "HEPTA_TOOL_ACCOUNT", "HEPTA_TOOL_AGENT_ID",
        "HEPTA_EXECUTION_DOMAIN_ID", "HEPTA_TOOL_ALLOW_TRADE",
        "HEPTA_TOOL_SESSION_TEMPLATES", "HEPTA_TOOL_CONTRACT_BINDINGS",
        "HEPTA_TOOL_MAX_ORDER_QTY", "HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN",
        "HEPTA_TOOL_DECISION_LEASE_TTL_MS", "HEPTA_TOOL_AGENT_UID",
        "HEPTA_TOOL_SUPERVISOR_UID", "HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC",
        "HEPTA_TOOL_SERVER_WORKERS", "HEPTA_TOOL_SERVER_MAX_PENDING",
        "HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER",
        "HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER",
        "HEPTA_TOOL_SERVER_INGRESS_WORKERS",
    ]
    missing = [key for key in order if not values.get(key)]
    if missing:
        raise LocalPaperError(
            "local PAPER gateway environment incomplete: " + ",".join(missing))
    return "".join(f"{key}={values[key]}\n" for key in order).encode("ascii")


def _identity_document(
        authority: dict[str, Any], source_policy_sha256: str,
        domain: str,
) -> dict[str, Any]:
    if (
            authority.get("schema") != AUTHORITY_SCHEMA or
            authority.get("version") != 1 or
            authority.get("paper_authorized") is not True or
            authority.get("live_authorized") is not False or
            not isinstance(authority.get("authorizations"), list) or
            DIGEST.fullmatch(source_policy_sha256) is None):
        raise LocalPaperError("local PAPER authority contract invalid")
    matches = [
        item for item in authority["authorizations"]
        if isinstance(item, dict) and item.get("domain_id") == domain
    ]
    if len(matches) != 1:
        raise LocalPaperError("local PAPER domain authorization missing")
    item = matches[0]
    identity = {
        "domain_id": domain,
        "gid": item.get("gid"),
        "identity": item.get("identity"),
        "role": "ib-paper-execution-authority",
        "uid": item.get("uid"),
    }
    if (
            identity["identity"] != f"hepta-ib-exec-{domain}" or
            type(identity["uid"]) is not int or
            type(identity["gid"]) is not int):
        raise LocalPaperError("local PAPER identity contract invalid")
    return {
        "identities": [identity],
        "live_authorized": False,
        "paper_authorized": True,
        "schema": IDENTITY_SCHEMA,
        "source_policy_sha256": source_policy_sha256,
        "version": 1,
    }


def _deny_all_document(source_policy_sha256: str) -> dict[str, Any]:
    return {
        "identities": [],
        "live_authorized": False,
        "paper_authorized": False,
        "schema": IDENTITY_SCHEMA,
        "source_policy_sha256": source_policy_sha256,
        "version": 1,
    }


def _run_systemctl(
        arguments: list[str], *, timeout: float | None = None,
) -> None:
    command = ["systemctl", *arguments]
    environment = _child_environment()
    if timeout is None:
        completed = subprocess.run(
            command, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment)
        if completed.returncode != 0:
            raise LocalPaperError(
                f"systemctl {' '.join(arguments)} failed: "
                f"{completed.stderr.strip()}")
        return
    process = subprocess.Popen(
        command, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, close_fds=True, start_new_session=True)
    try:
        _stdout, stderr = process.communicate(timeout=timeout)
    except BaseException as error:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    process.wait()
        if isinstance(error, subprocess.TimeoutExpired):
            raise LocalPaperError(
                f"systemctl {' '.join(arguments)} timed out") from error
        raise
    if process.returncode != 0:
        raise LocalPaperError(
            f"systemctl {' '.join(arguments)} failed: "
            f"{stderr.strip()}")


def _broker_override_payload() -> bytes:
    return (
        "[Service]\n"
        "ExecStart=\n"
        "ExecStart=/usr/bin/python3.12 -I -S "
        "${CREDENTIALS_DIRECTORY}/hepta-broker-egress-policy.py "
        "--supervise --paper-identities "
        "/etc/heptatrader/"
        "hepta-agent-trust-domain-paper-identities-v1.json\n"
    ).encode("ascii")


def _phase_driver(
        transaction: dict[str, Any], transaction_path: Path,
        after_persist: Callable[[dict[str, Any], str, str], None] | None = None,
) -> tuple[list[dict[str, Any]], Callable[[str], None]]:
    holder = [transaction]
    counter = 0

    def before(label: str) -> None:
        nonlocal counter
        counter += 1
        normalized = re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_")
        phase = f"BEFORE_{counter:03d}_{normalized}"[:128].rstrip("_")
        holder[0] = _persist_control_phase(
            holder[0], transaction_path, phase)
        if after_persist is not None:
            after_persist(holder[0], phase, normalized)

    return holder, before


def _enable_request(
        *, domain: str, authority_path: Path, identities_path: Path,
        env_root: Path, drop_in_path: Path, gateway_env_root: Path,
        host_authority_root: Path,
        external_p1_finalized: bool, handoff_path: Path | None,
        handoff_file_sha256: str | None, handoff_body_sha256: str | None,
        campaign_id: str | None, source_baseline_sha256: str | None,
        source_policy_sha256: str, guardian_request_id: str | None,
) -> dict[str, Any]:
    return {
        "domain": domain, "authority_path": str(authority_path),
        "identities_path": str(identities_path), "env_root": str(env_root),
        "drop_in_path": str(drop_in_path),
        "gateway_env_root": str(gateway_env_root),
        "host_authority_root": str(host_authority_root),
        "external_p1_finalized": external_p1_finalized,
        "handoff_path": str(handoff_path) if handoff_path is not None else None,
        "handoff_file_sha256": handoff_file_sha256,
        "handoff_body_sha256": handoff_body_sha256,
        "campaign_id": campaign_id,
        "source_baseline_sha256": source_baseline_sha256,
        "source_policy_sha256": source_policy_sha256,
        "guardian_request_id": guardian_request_id,
    }


def _rollback_staged_broker_activation(
        *, lease: dict[str, Any], identities_path: Path,
        deny_all_payload: bytes, drop_in_path: Path,
        systemctl: Callable[[list[str]], None],
        before_side_effect: Callable[[str], None],
        reservation_payload: bytes | None,
        paper_env_restore: tuple[Path, bytes, int] | None = None,
        gateway_env_restore: tuple[Path, bytes, int] | None = None,
) -> None:
    before_side_effect("ROLLBACK_STOP_BROKER")
    systemctl(["stop", BROKER_UNIT])
    before_side_effect("ROLLBACK_WRITE_DENY_ALL_IDENTITIES")
    _atomic_write(identities_path, deny_all_payload, 0o600)
    if paper_env_restore is not None:
        before_side_effect("ROLLBACK_RESTORE_PAPER_ENV")
        _atomic_write(*paper_env_restore)
    if gateway_env_restore is not None:
        before_side_effect("ROLLBACK_RESTORE_GATEWAY_ENV")
        _atomic_write(*gateway_env_restore)
    before_side_effect("ROLLBACK_REMOVE_BROKER_DROP_IN")
    try:
        drop_in_path.unlink()
        _fsync_directory(drop_in_path.parent)
    except FileNotFoundError:
        pass
    before_side_effect("ROLLBACK_DAEMON_RELOAD")
    systemctl(["daemon-reload"])
    if reservation_payload is not None:
        before_side_effect("ROLLBACK_REMOVE_ACTIVATION_ARTIFACTS")
        _remove_exact_activation_artifacts(lease, reservation_payload)
        before_side_effect("ROLLBACK_REMOVE_ACTIVATION_RESERVATION")
        _remove_exact_host_authority_owner(
            lease, reservation_payload, absent_ok=True)
    elif BROKER_START_PERMIT_PATH.exists() or \
            BROKER_START_PERMIT_PATH.is_symlink():
        # Without the exact reservation hashes, this process has no authority
        # to classify or delete a bearer left by another transaction.
        raise LocalPaperError(
            "local PAPER broker permit has no rollback reservation")


def _enable_body(
        *, domain: str, authority_path: Path, identities_path: Path,
        env_root: Path, drop_in_path: Path,
        gateway_env_root: Path = DEFAULT_GATEWAY_ENV_ROOT,
        external_p1_finalized: bool = False,
        handoff_path: Path | None = None,
        handoff_file_sha256: str | None = None,
        handoff_body_sha256: str | None = None,
        campaign_id: str | None = None,
        source_baseline_sha256: str | None = None,
        systemctl: Callable[[list[str]], None] = _run_systemctl,
        external_verifier: Callable[..., dict[str, Any]] = verify_external_p1,
        before_side_effect: Callable[[str], None] = lambda _phase: None,
        host_authority_root: Path | None = None,
        activation_reservation_factory: Callable[
            [dict[str, Any]], tuple[dict[str, Any], bytes]
        ] | None = None,
        activation_completion_verifier: Callable[
            [dict[str, Any], bytes, Path], tuple[dict[str, Any], bytes]
        ] | None = None,
        runtime_owner_verifier: Callable[
            [dict[str, Any], bytes, dict[str, Any], bytes, Path],
            tuple[dict[str, Any], bytes]
        ] = _verify_runtime_owner_adoption,
) -> dict[str, Any]:
    systemctl_impl = systemctl
    authority_root = (
        HOST_AUTHORITY_DIRECTORY
        if host_authority_root is None else host_authority_root)

    def transactional_systemctl(label: str, arguments: list[str]) -> None:
        before_side_effect(label)
        systemctl_impl(arguments)
    paper_env_path = env_root / f"{domain}.ib-paper.env"
    external_paper_environment: dict[str, str] | None = None
    external_paper_payload: bytes | None = None
    if external_p1_finalized:
        (external_paper_environment, external_paper_payload,
         _external_paper_metadata) = _external_p1_paper_environment(
             paper_env_path)
    else:
        _validate_paper_environment(paper_env_path)
    authority = _load_json(authority_path)
    current = _load_json(identities_path)
    source_policy_sha256 = current.get("source_policy_sha256", "")
    document = _identity_document(
        authority, source_policy_sha256, domain)
    payload = _pretty_json(document)
    deny_all_payload = _pretty_json(_deny_all_document(source_policy_sha256))
    if _sha256(payload) != authority.get("network_identity_manifest_sha256"):
        raise LocalPaperError("local PAPER identity digest mismatch")
    if external_p1_finalized:
        assert external_paper_payload is not None
        paper_env_payload = external_paper_payload
        paper_env_mode = 0o644
    else:
        original_paper_payload = paper_env_path.read_bytes()
        paper_env_payload, paper_env_mode = _environment_with_value(
            paper_env_path, "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS",
            LOCAL_PAPER_QUOTE_MAX_AGE_MS)
    gateway_env_path = gateway_env_root / f"{domain}.env"
    if external_p1_finalized:
        current_gateway_payload, _current_gateway_metadata = _secure_read(
            gateway_env_path, mode=0o644,
            maximum=DORMANT_PAPER_PROFILE_BYTES)
        current_gateway_environment = _environment_from_payload(
            current_gateway_payload, gateway_env_path)
    else:
        original_gateway_payload = gateway_env_path.read_bytes()
        original_gateway_mode = stat.S_IMODE(gateway_env_path.stat().st_mode)
        current_gateway_payload = None
        current_gateway_environment = None
    gateway_payload = _paper_gateway_environment(
        gateway_env_path, paper_env_path, domain,
        int(document["identities"][0]["uid"]),
        current_environment=current_gateway_environment,
        paper_environment=external_paper_environment)
    external_arguments = (
        handoff_path, handoff_file_sha256, handoff_body_sha256,
        campaign_id, source_baseline_sha256)
    if external_p1_finalized:
        if (
                domain != "alpha" or any(value is None for value in external_arguments) or
                gateway_env_path != PROFILE_TARGET_PATH or
                paper_env_path != EXTERNAL_P1_PAPER_ENV_PATH):
            raise LocalPaperError("external P1 enable arguments invalid")
        assert current_gateway_payload is not None
        if current_gateway_payload != gateway_payload:
            raise LocalPaperError("external P1 dormant profile is not executable")
        initial_boundary = external_verifier(
            handoff_path=handoff_path,
            expected_file_sha256=handoff_file_sha256,
            expected_body_sha256=handoff_body_sha256,
            campaign_id=campaign_id,
            source_baseline_sha256=source_baseline_sha256,
            identities_path=identities_path,
            gateway_env_path=gateway_env_path,
            paper_env_path=paper_env_path)
    else:
        if any(value is not None for value in external_arguments):
            raise LocalPaperError("external P1 pins require finalized mode")
        initial_boundary = None
    override = _broker_override_payload()

    # The running deny-all supervisor pins the identity file's inode and
    # digest. Replacing that file first is interpreted as hostile drift and
    # races the later restart. Stop every PAPER consumer, then stop the broker
    # guard (whose stop path installs exact deny-all) before committing any
    # authorization input. Type=notify makes the broker start block until the
    # replacement policy has been applied and verified.
    transactional_systemctl(
        "DISABLE_WATCH_RECONCILE_TIMER",
        ["disable", "--now", WATCH_RECONCILE_TIMER])
    transactional_systemctl(
        "STOP_WATCH_RECONCILE_SERVICE", ["stop", WATCH_RECONCILE_SERVICE])
    transactional_systemctl(
        "STOP_CAMPAIGN_OPERATOR_SOCKET",
        ["stop", f"hepta-ib-paper-campaign-operator@{domain}.socket"])
    transactional_systemctl(
        "STOP_TOOL_GATEWAY_SERVICE",
        ["stop", f"hepta-tool-gateway@{domain}.service"])
    transactional_systemctl(
        "STOP_TOOL_GATEWAY_SOCKET",
        ["stop", f"hepta-tool-gateway@{domain}.socket"])
    transactional_systemctl(
        "STOP_SESSION_SUPERVISOR_SOCKET",
        ["stop", f"hepta-tool-session-supervisor@{domain}.socket"])
    transactional_systemctl(
        "STOP_PAPER_EXECUTION",
        ["stop", f"hepta-execution-ib-paper@{domain}.service"])
    transactional_systemctl(
        "STOP_SIMULATOR_EXECUTION_SERVICE",
        ["stop", f"hepta-execution-simulator@{domain}.service"])
    transactional_systemctl(
        "STOP_SIMULATOR_EXECUTION_SOCKET",
        ["stop", f"hepta-execution-simulator@{domain}.socket"])
    transactional_systemctl(
        "STOP_SIMULATOR_EVENTS_SOCKET",
        ["stop", f"hepta-execution-events-simulator@{domain}.socket"])
    for unit in (
            f"hepta-tool-gateway@{domain}.service",
            f"hepta-tool-gateway@{domain}.socket",
            f"hepta-tool-session-supervisor@{domain}.socket",
            f"hepta-ib-paper-campaign-operator@{domain}.socket"):
        transactional_systemctl("UNMASK_PAPER_UNIT", ["unmask", unit])
        transactional_systemctl(
            "UNMASK_RUNTIME_PAPER_UNIT", ["unmask", "--runtime", unit])
    transactional_systemctl("STOP_BROKER", ["stop", BROKER_UNIT])
    if external_p1_finalized:
        final_boundary = external_verifier(
            handoff_path=handoff_path,
            expected_file_sha256=handoff_file_sha256,
            expected_body_sha256=handoff_body_sha256,
            campaign_id=campaign_id,
            source_baseline_sha256=source_baseline_sha256,
            identities_path=identities_path,
            gateway_env_path=gateway_env_path,
            paper_env_path=paper_env_path)
        if final_boundary != initial_boundary:
            raise LocalPaperError("external P1 boundary drifted before mutation")
    reservation_document: dict[str, Any] | None = None
    reservation_payload: bytes | None = None
    paper_restore = (
        None if external_p1_finalized else
        (paper_env_path, original_paper_payload, paper_env_mode))
    gateway_restore = (
        None if external_p1_finalized else
        (gateway_env_path, original_gateway_payload, original_gateway_mode))
    with _host_authority_lease(authority_root) as lease:
        _require_host_authority_owner_absent(lease)
        try:
            before_side_effect("WRITE_IDENTITIES")
            _atomic_write(identities_path, payload, 0o600)
            if not external_p1_finalized:
                before_side_effect("WRITE_PAPER_ENV")
                _atomic_write(paper_env_path, paper_env_payload, paper_env_mode)
                before_side_effect("WRITE_GATEWAY_ENV")
                _atomic_write(gateway_env_path, gateway_payload, 0o644)
            before_side_effect("WRITE_BROKER_DROP_IN")
            _atomic_write(drop_in_path, override, 0o644)
            transactional_systemctl("DAEMON_RELOAD", ["daemon-reload"])
            before_side_effect("START_BROKER_LOCAL_PAPER")
            if activation_reservation_factory is None:
                raise LocalPaperError(
                    "local PAPER activation reservation unavailable")
            reservation_document, reservation_payload = (
                activation_reservation_factory(lease))
        except Exception:
            _rollback_staged_broker_activation(
                lease=lease, identities_path=identities_path,
                deny_all_payload=deny_all_payload,
                drop_in_path=drop_in_path, systemctl=systemctl_impl,
                before_side_effect=before_side_effect,
                reservation_payload=reservation_payload,
                paper_env_restore=paper_restore,
                gateway_env_restore=gateway_restore)
            raise
    if reservation_document is None or reservation_payload is None:
        raise LocalPaperError(
            "local PAPER activation reservation was not published")
    try:
        systemctl_impl(["restart", BROKER_UNIT])
        if activation_completion_verifier is None:
            raise LocalPaperError(
                "local PAPER activation completion verifier unavailable")
        consumed_document, consumed_payload = activation_completion_verifier(
            reservation_document, reservation_payload, authority_root)
    except Exception:
        with _host_authority_lease(authority_root) as lease:
            _rollback_staged_broker_activation(
                lease=lease, identities_path=identities_path,
                deny_all_payload=deny_all_payload,
                drop_in_path=drop_in_path, systemctl=systemctl_impl,
                before_side_effect=before_side_effect,
                reservation_payload=reservation_payload,
                paper_env_restore=paper_restore,
                gateway_env_restore=gateway_restore)
        raise
    # Deliberate lock handoff: execution preflight acquires the same lease and
    # creates the runtime owner.  Never hold the local-control descriptor while
    # waiting for that unit.  At this point the durable broker boundary is
    # already ACTIVE, so any terminal verifier racing this release fails its
    # mandatory DENY_ALL boundary check.
    transactional_systemctl(
        "START_PAPER_EXECUTION",
        ["start", f"hepta-execution-ib-paper@{domain}.service"])
    runtime_owner_verifier(
        reservation_document, reservation_payload,
        consumed_document, consumed_payload, authority_root)
    transactional_systemctl(
        "START_TOOL_GATEWAY_SOCKET",
        ["start", f"hepta-tool-gateway@{domain}.socket"])
    transactional_systemctl(
        "START_SESSION_SUPERVISOR_SOCKET",
        ["start", f"hepta-tool-session-supervisor@{domain}.socket"])
    transactional_systemctl(
        "START_TOOL_GATEWAY_SERVICE",
        ["start", f"hepta-tool-gateway@{domain}.service"])
    transactional_systemctl(
        "START_CAMPAIGN_OPERATOR_SOCKET",
        ["start", f"hepta-ib-paper-campaign-operator@{domain}.socket"])
    result = {
        "mode": "LOCAL_PAPER", "domain": domain,
        "paper_authorized": True, "live_authorized": False,
        "quote_max_age_ms": (
            int(external_paper_environment[
                "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"])
            if external_p1_finalized and external_paper_environment is not None
            else int(LOCAL_PAPER_QUOTE_MAX_AGE_MS)),
        "identity_manifest_sha256": _sha256(payload),
    }
    if external_p1_finalized:
        result.update({
            "admission_mode": "external-p1-finalized",
            "campaign_id": campaign_id,
            "watch_handoff_receipt_file_sha256": handoff_file_sha256,
            "watch_handoff_receipt_body_sha256": handoff_body_sha256,
            "dormant_profile_sha256": DORMANT_PAPER_PROFILE_SHA256,
            "paper_runtime_profile_sha256": _sha256(paper_env_payload),
        })
    else:
        result["admission_mode"] = "local-only"
    return result


def _enable_transaction(
        *, domain: str, authority_path: Path, identities_path: Path,
        env_root: Path, drop_in_path: Path,
        gateway_env_root: Path = DEFAULT_GATEWAY_ENV_ROOT,
        external_p1_finalized: bool = False,
        handoff_path: Path | None = None,
        handoff_file_sha256: str | None = None,
        handoff_body_sha256: str | None = None,
        campaign_id: str | None = None,
        source_baseline_sha256: str | None = None,
        systemctl: Callable[[list[str]], None] = _run_systemctl,
        external_verifier: Callable[..., dict[str, Any]] = verify_external_p1,
        transaction_root: Path | None = None,
        host_authority_root: Path | None = None,
        guardian_identity: dict[str, Any] | None = None,
        guardian_request_id: str | None = None,
) -> dict[str, Any]:
    root = LOCAL_PAPER_STATE_ROOT if transaction_root is None else transaction_root
    authority_root = (
        HOST_AUTHORITY_DIRECTORY
        if host_authority_root is None else host_authority_root)
    current = _load_json(identities_path)
    source_policy_sha256 = current.get("source_policy_sha256")
    if (not isinstance(source_policy_sha256, str) or
            DIGEST.fullmatch(source_policy_sha256) is None):
        raise LocalPaperError("local PAPER identity source policy invalid")
    authority = _load_json(authority_path)
    target_payload = _pretty_json(_identity_document(
        authority, source_policy_sha256, domain))
    if _sha256(target_payload) != authority.get(
            "network_identity_manifest_sha256"):
        raise LocalPaperError("local PAPER identity digest mismatch")
    request = _enable_request(
        domain=domain, authority_path=authority_path,
        identities_path=identities_path, env_root=env_root,
        drop_in_path=drop_in_path, gateway_env_root=gateway_env_root,
        host_authority_root=authority_root,
        external_p1_finalized=external_p1_finalized,
        handoff_path=handoff_path,
        handoff_file_sha256=handoff_file_sha256,
        handoff_body_sha256=handoff_body_sha256,
        campaign_id=campaign_id,
        source_baseline_sha256=source_baseline_sha256,
        source_policy_sha256=source_policy_sha256,
        guardian_request_id=guardian_request_id)
    transaction_path = _transaction_path(root)
    with _control_transaction_lock(root):
        transaction = _load_control_transaction(transaction_path)
        if transaction is not None:
            if not _same_transaction_request(transaction, "ENABLE", request):
                raise LocalPaperError("local PAPER control WAL argument drift")
            if not (
                    guardian_identity is not None and
                    transaction.get("phase") == "BEGIN"):
                holder, before = _phase_driver(transaction, transaction_path)
                result = _disable_body(
                    identities_path=identities_path, drop_in_path=drop_in_path,
                    domain=domain, systemctl=systemctl,
                    before_side_effect=before)
                _clear_transaction_activation_reservation(
                    holder[0], host_authority_root=authority_root)
                _complete_control_transaction(holder[0], transaction_path)
                result["enable_replay_forward_safe"] = True
                return result
        transaction = _new_control_transaction(
            path=transaction_path, operation="ENABLE", request=request,
            target_identity_manifest_sha256=_sha256(target_payload),
            target_drop_in_sha256=_sha256(_broker_override_payload())
        ) if transaction is None else transaction

        permit_holder: list[dict[str, Any] | None] = [None]

        def after_persist(
                current_transaction: dict[str, Any], phase: str,
                normalized: str,
        ) -> None:
            if normalized == "START_BROKER_LOCAL_PAPER":
                if guardian_identity is None:
                    raise LocalPaperError(
                        "local PAPER broker start requires guardian")
                permit_holder[0] = _issue_broker_start_permit(
                    current_transaction,
                    guardian_identity=guardian_identity, phase=phase)

        holder, before = _phase_driver(
            transaction, transaction_path, after_persist=after_persist)

        def publish_activation(
                lease: dict[str, Any],
        ) -> tuple[dict[str, Any], bytes]:
            if guardian_identity is None or permit_holder[0] is None:
                raise LocalPaperError(
                    "local PAPER activation reservation requires guardian")
            return _publish_broker_activation_reservation(
                lease, transaction=holder[0],
                guardian_identity=guardian_identity,
                permit=permit_holder[0])

        try:
            result = _enable_body(
                domain=domain, authority_path=authority_path,
                identities_path=identities_path, env_root=env_root,
                drop_in_path=drop_in_path,
                gateway_env_root=gateway_env_root,
                external_p1_finalized=external_p1_finalized,
                handoff_path=handoff_path,
                handoff_file_sha256=handoff_file_sha256,
                handoff_body_sha256=handoff_body_sha256,
                campaign_id=campaign_id,
                source_baseline_sha256=source_baseline_sha256,
                systemctl=systemctl, external_verifier=external_verifier,
                before_side_effect=before,
                host_authority_root=authority_root,
                activation_reservation_factory=publish_activation,
                activation_completion_verifier=
                    _verify_broker_activation_completion)
        except Exception:
            try:
                _disable_body(
                    identities_path=identities_path,
                    drop_in_path=drop_in_path, domain=domain,
                    systemctl=systemctl, before_side_effect=before)
                _clear_transaction_activation_reservation(
                    holder[0], host_authority_root=authority_root)
                _complete_control_transaction(holder[0], transaction_path)
            except Exception:
                pass
            raise
        if guardian_identity is None:
            _complete_control_transaction(holder[0], transaction_path)
        else:
            before("WRITE_GUARDIAN_ACTIVE_RECEIPT")
            _write_guardian_active_receipt(
                holder[0], guardian_identity=guardian_identity,
                phase=CONTROL_NORMAL_TERMINAL_PHASE, mode="LOCAL_PAPER")
            holder[0] = _persist_control_phase(
                holder[0], transaction_path, CONTROL_NORMAL_TERMINAL_PHASE)
            result["transaction_retained"] = True
            result["guardian_supervised"] = True
        return result


def _disable_body(
        *, identities_path: Path, drop_in_path: Path,
        domain: str,
        systemctl: Callable[[list[str]], None] = _run_systemctl,
        before_side_effect: Callable[[str], None] = lambda _phase: None,
) -> dict[str, Any]:
    current = _load_json(identities_path)
    source_policy_sha256 = current.get("source_policy_sha256", "")
    payload = _pretty_json(_deny_all_document(source_policy_sha256))
    before_side_effect("STOP_ALL_CONSUMERS")
    systemctl(["stop", *_control_stop_units(domain)])
    before_side_effect("STOP_BROKER_DENY_ALL")
    systemctl(["stop", BROKER_UNIT])
    before_side_effect("WRITE_DENY_ALL_IDENTITIES")
    _atomic_write(identities_path, payload, 0o600)
    before_side_effect("REMOVE_BROKER_DROP_IN")
    try:
        drop_in_path.unlink()
        _fsync_directory(drop_in_path.parent)
    except FileNotFoundError:
        pass
    before_side_effect("DAEMON_RELOAD")
    systemctl(["daemon-reload"])
    before_side_effect("START_BROKER_DENY_ALL")
    systemctl(["start", BROKER_UNIT])
    return {
        "mode": "DENY_ALL", "paper_authorized": False,
        "live_authorized": False,
        "identity_count": 0,
        "identity_manifest_sha256": _sha256(payload),
    }


def _disable_transaction(
        *, identities_path: Path, drop_in_path: Path, domain: str = "alpha",
        systemctl: Callable[[list[str]], None] = _run_systemctl,
        transaction_root: Path | None = None,
) -> dict[str, Any]:
    root = LOCAL_PAPER_STATE_ROOT if transaction_root is None else transaction_root
    current = _load_json(identities_path)
    source_policy_sha256 = current.get("source_policy_sha256")
    if (not isinstance(source_policy_sha256, str) or
            DIGEST.fullmatch(source_policy_sha256) is None):
        raise LocalPaperError("local PAPER identity source policy invalid")
    deny_payload = _pretty_json(_deny_all_document(source_policy_sha256))
    request = {
        "domain": domain, "identities_path": str(identities_path),
        "drop_in_path": str(drop_in_path),
        "source_policy_sha256": source_policy_sha256,
    }
    transaction_path = _transaction_path(root)
    with _control_transaction_lock(root):
        transaction = _load_control_transaction(transaction_path)
        if transaction is not None:
            if transaction.get("operation") == "ENABLE":
                previous = transaction.get("request")
                if (not isinstance(previous, dict) or
                        previous.get("domain") != domain or
                        previous.get("identities_path") != str(identities_path) or
                        previous.get("drop_in_path") != str(drop_in_path) or
                        previous.get("source_policy_sha256") !=
                            source_policy_sha256):
                    raise LocalPaperError(
                        "local PAPER control WAL argument drift")
            elif not _same_transaction_request(transaction, "DISABLE", request):
                raise LocalPaperError("local PAPER control WAL argument drift")
        else:
            transaction = _new_control_transaction(
                path=transaction_path, operation="DISABLE", request=request,
                target_identity_manifest_sha256=_sha256(deny_payload),
                target_drop_in_sha256=None)
        holder, before = _phase_driver(transaction, transaction_path)
        result = _disable_body(
            identities_path=identities_path, drop_in_path=drop_in_path,
            domain=domain, systemctl=systemctl,
            before_side_effect=before)
        _complete_control_transaction(holder[0], transaction_path)
        return result


def disable(
        *, identities_path: Path, drop_in_path: Path, domain: str = "alpha",
        systemctl: Callable[[list[str]], None] = _run_systemctl,
        transaction_root: Path | None = None,
) -> dict[str, Any]:
    root = LOCAL_PAPER_STATE_ROOT if transaction_root is None else transaction_root
    if (
            domain != "alpha" or identities_path != DEFAULT_IDENTITIES or
            drop_in_path != DEFAULT_DROP_IN or root != LOCAL_PAPER_STATE_ROOT):
        raise LocalPaperError(
            "guardian-managed disable paths must be canonical")
    # A clean guardian stop is completed by its ExecStopPost fail-close
    # transaction.  That transaction stops the consumers and broker before
    # replacing the identity manifest, so the long-lived egress/domain
    # watchdogs never mistake the planned replacement for source drift.
    systemctl(["stop", GUARDIAN_UNIT])
    transaction = _load_control_transaction(_transaction_path(root))
    if transaction is not None:
        return reconcile_control_transaction(
            identities_path=identities_path, drop_in_path=drop_in_path,
            systemctl=systemctl, transaction_root=root)
    return _disable_transaction(
        identities_path=identities_path, drop_in_path=drop_in_path,
        domain=domain, systemctl=systemctl, transaction_root=root)


def _enable_recovery_transaction(
        *, authority_path: Path, identities_path: Path, env_root: Path,
        drop_in_path: Path, recovery_path: Path,
        recovery_file_sha256: str, recovery_body_sha256: str,
        gateway_env_root: Path = DEFAULT_GATEWAY_ENV_ROOT,
        systemctl: Callable[[list[str]], None] = _run_systemctl,
        external_verifier: Callable[..., dict[str, Any]] = verify_external_p1,
        command: Callable[
            [list[str]], subprocess.CompletedProcess[str]] = _run_command,
        transaction_root: Path | None = None,
        host_authority_root: Path | None = None,
        now_ms: int | None = None,
        guardian_identity: dict[str, Any] | None = None,
        guardian_request_id: str | None = None,
) -> dict[str, Any]:
    root = LOCAL_PAPER_STATE_ROOT if transaction_root is None else transaction_root
    authority_root = (
        HOST_AUTHORITY_DIRECTORY
        if host_authority_root is None else host_authority_root)
    recovery, initial_recovery_boundary = validate_recovery_authority(
        recovery_path=recovery_path,
        expected_file_sha256=recovery_file_sha256,
        expected_body_sha256=recovery_body_sha256, now_ms=now_ms)
    domain = "alpha"
    paper_env_path = env_root / "alpha.ib-paper.env"
    gateway_env_path = gateway_env_root / "alpha.env"
    paper_environment, paper_payload, _paper_metadata = (
        _external_p1_paper_environment(paper_env_path))
    gateway_payload, _gateway_metadata = _secure_read(
        gateway_env_path, mode=0o644, maximum=DORMANT_PAPER_PROFILE_BYTES)
    gateway_environment = _environment_from_payload(
        gateway_payload, gateway_env_path)
    current = _load_json(identities_path)
    source_policy_sha256 = current.get("source_policy_sha256")
    if (not isinstance(source_policy_sha256, str) or
            DIGEST.fullmatch(source_policy_sha256) is None):
        raise LocalPaperError("recovery identity source policy invalid")
    authority = _load_json(authority_path)
    target_document = _identity_document(
        authority, source_policy_sha256, domain)
    target_payload = _pretty_json(target_document)
    if _sha256(target_payload) != authority.get(
            "network_identity_manifest_sha256"):
        raise LocalPaperError("recovery identity digest mismatch")
    expected_gateway = _paper_gateway_environment(
        gateway_env_path, paper_env_path, domain,
        int(target_document["identities"][0]["uid"]),
        current_environment=gateway_environment,
        paper_environment=paper_environment)
    if expected_gateway != gateway_payload:
        raise LocalPaperError("recovery dormant profile is not executable")
    references = {
        field: recovery[field]["file_sha256"]
        for field in RECOVERY_REFERENCE_SPECS}
    reference_bodies = {
        field: recovery[field]["body_sha256"]
        for field in RECOVERY_REFERENCE_SPECS}
    request = {
        "domain": domain, "authority_path": str(authority_path),
        "identities_path": str(identities_path), "env_root": str(env_root),
        "gateway_env_root": str(gateway_env_root),
        "drop_in_path": str(drop_in_path),
        "host_authority_root": str(authority_root),
        "recovery_path": str(recovery_path),
        "recovery_file_sha256": recovery_file_sha256,
        "recovery_body_sha256": recovery_body_sha256,
        "campaign_id": recovery["campaign_id"],
        "suspension_id": recovery["suspension_id"],
        "source_baseline_sha256": recovery["source_baseline_sha256"],
        "watch_handoff_receipt_path":
            recovery["watch_handoff_receipt_path"],
        "watch_handoff_receipt_file_sha256":
            recovery["watch_handoff_receipt_file_sha256"],
        "watch_handoff_receipt_body_sha256":
            recovery["watch_handoff_receipt_body_sha256"],
        "recovery_reference_file_sha256s": references,
        "recovery_reference_body_sha256s": reference_bodies,
        "source_policy_sha256": source_policy_sha256,
        "guardian_request_id": guardian_request_id,
    }
    transaction_path = _transaction_path(root)
    with _control_transaction_lock(root):
        transaction = _load_control_transaction(transaction_path)
        if transaction is not None:
            if not _same_transaction_request(
                    transaction, "ENABLE_RECOVERY", request):
                raise LocalPaperError("local PAPER control WAL argument drift")
            if not (
                    guardian_identity is not None and
                    transaction.get("phase") == "BEGIN"):
                holder, before = _phase_driver(transaction, transaction_path)
                result = _disable_body(
                    identities_path=identities_path,
                    drop_in_path=drop_in_path, domain=domain,
                    systemctl=systemctl, before_side_effect=before)
                _clear_transaction_activation_reservation(
                    holder[0], host_authority_root=authority_root)
                holder[0] = _persist_control_phase(
                    holder[0], transaction_path,
                    "RECOVERY_RECONCILED_DENY_ALL")
                result.update({
                    "recovery_replay_forward_safe": True,
                    "recovery_retained": True,
                    "transaction_retained": True,
                })
                return result
        else:
            transaction = _new_control_transaction(
                path=transaction_path, operation="ENABLE_RECOVERY",
                request=request,
                target_identity_manifest_sha256=_sha256(target_payload),
                target_drop_in_sha256=_sha256(_broker_override_payload()),
                recovery_record_file_sha256=recovery_file_sha256,
                recovery_record_body_sha256=recovery_body_sha256,
                now_ms=now_ms)

        permit_holder: list[dict[str, Any] | None] = [None]

        def after_persist(
                current_transaction: dict[str, Any], phase: str,
                normalized: str,
        ) -> None:
            if normalized == "START_BROKER_RECOVERY":
                if guardian_identity is None:
                    raise LocalPaperError(
                        "recovery broker start requires guardian")
                permit_holder[0] = _issue_broker_start_permit(
                    current_transaction,
                    guardian_identity=guardian_identity, phase=phase)

        holder, before = _phase_driver(
            transaction, transaction_path, after_persist=after_persist)

        def publish_activation(
                lease: dict[str, Any],
        ) -> tuple[dict[str, Any], bytes]:
            if guardian_identity is None or permit_holder[0] is None:
                raise LocalPaperError(
                    "recovery activation reservation requires guardian")
            return _publish_broker_activation_reservation(
                lease, transaction=holder[0],
                guardian_identity=guardian_identity,
                permit=permit_holder[0])

        try:
            before("STOP_ALL_ENTRY_AND_RECOVERY_UNITS")
            systemctl(["stop", *_control_stop_units(domain)])
            before("STOP_BROKER_DENY_ALL")
            systemctl(["stop", BROKER_UNIT])
            historical_boundary = external_verifier(
                handoff_path=Path(recovery["watch_handoff_receipt_path"]),
                expected_file_sha256=
                    recovery["watch_handoff_receipt_file_sha256"],
                expected_body_sha256=
                    recovery["watch_handoff_receipt_body_sha256"],
                campaign_id=recovery["campaign_id"],
                source_baseline_sha256=recovery["source_baseline_sha256"],
                identities_path=identities_path,
                gateway_env_path=gateway_env_path,
                paper_env_path=paper_env_path, command=command,
                now_ms=now_ms, require_fresh=False,
                require_inert=True, require_residue_absent=False)
            final_recovery, final_recovery_boundary = (
                validate_recovery_authority(
                    recovery_path=recovery_path,
                    expected_file_sha256=recovery_file_sha256,
                    expected_body_sha256=recovery_body_sha256,
                    now_ms=now_ms))
            if (
                    final_recovery != recovery or
                    final_recovery_boundary != initial_recovery_boundary):
                raise LocalPaperError(
                    "recovery authority drifted before mutation")
            _require_kill_switch(DOMAIN_KILL_SWITCH_PATH, PAPER_CONTROL_GID)
            _require_kill_switch(
                GLOBAL_KILL_SWITCH_PATH, GLOBAL_PAPER_CONTROL_GID)
            reservation_document: dict[str, Any] | None = None
            reservation_payload: bytes | None = None
            deny_all_payload = _pretty_json(
                _deny_all_document(source_policy_sha256))
            with _host_authority_lease(authority_root) as lease:
                _require_host_authority_owner_absent(lease)
                try:
                    before("WRITE_RECOVERY_IDENTITIES")
                    _atomic_write(identities_path, target_payload, 0o600)
                    before("WRITE_RECOVERY_BROKER_DROP_IN")
                    _atomic_write(
                        drop_in_path, _broker_override_payload(), 0o644)
                    before("DAEMON_RELOAD")
                    systemctl(["daemon-reload"])
                    before("START_BROKER_RECOVERY")
                    reservation_document, reservation_payload = (
                        publish_activation(lease))
                except Exception:
                    _rollback_staged_broker_activation(
                        lease=lease, identities_path=identities_path,
                        deny_all_payload=deny_all_payload,
                        drop_in_path=drop_in_path, systemctl=systemctl,
                        before_side_effect=before,
                        reservation_payload=reservation_payload)
                    raise
            if reservation_document is None or reservation_payload is None:
                raise LocalPaperError(
                    "recovery activation reservation was not published")
            try:
                systemctl(["start", BROKER_UNIT])
                consumed_document, consumed_payload = (
                    _verify_broker_activation_completion(
                    reservation_document, reservation_payload,
                    authority_root))
            except Exception:
                with _host_authority_lease(authority_root) as lease:
                    _rollback_staged_broker_activation(
                        lease=lease, identities_path=identities_path,
                        deny_all_payload=deny_all_payload,
                        drop_in_path=drop_in_path, systemctl=systemctl,
                        before_side_effect=before,
                        reservation_payload=reservation_payload)
                raise
            # The broker boundary is durably ACTIVE and the exact reservation
            # plus consumed receipt bridge the lease handoff to preflight.
            for unit in RECOVERY_START_UNITS:
                before("START_RECOVERY_UNIT")
                systemctl(["start", unit])
                if unit == "hepta-ib-paper-domain-preflight@alpha.service":
                    _verify_runtime_owner_adoption(
                        reservation_document, reservation_payload,
                        consumed_document, consumed_payload, authority_root)
            _require_kill_switch(DOMAIN_KILL_SWITCH_PATH, PAPER_CONTROL_GID)
            _require_kill_switch(
                GLOBAL_KILL_SWITCH_PATH, GLOBAL_PAPER_CONTROL_GID)
            if guardian_identity is None:
                raise LocalPaperError(
                    "recovery activation requires guardian")
            before("WRITE_GUARDIAN_ACTIVE_RECEIPT")
            _write_guardian_active_receipt(
                holder[0], guardian_identity=guardian_identity,
                phase=CONTROL_RECOVERY_TERMINAL_PHASE,
                mode="RECOVERY_PAPER")
            holder[0] = _persist_control_phase(
                holder[0], transaction_path,
                CONTROL_RECOVERY_TERMINAL_PHASE)
        except Exception:
            try:
                _disable_body(
                    identities_path=identities_path,
                    drop_in_path=drop_in_path, domain=domain,
                    systemctl=systemctl, before_side_effect=before)
                _clear_transaction_activation_reservation(
                    holder[0], host_authority_root=authority_root)
                holder[0] = _persist_control_phase(
                    holder[0], transaction_path, "RECOVERY_FAILED_DENY_ALL")
            except Exception:
                pass
            raise
        return {
            "mode": "RECOVERY_PAPER", "domain": domain,
            "admission_mode": "external-p1-recovery",
            "paper_authorized": True, "live_authorized": False,
            "entry_authorized": False, "reduce_only": True,
            "session_provision_authorized": False,
            "campaign_id": recovery["campaign_id"],
            "suspension_id": recovery["suspension_id"],
            "session_owner_count": recovery["session_owner_count"],
            "identity_manifest_sha256": _sha256(target_payload),
            "recovery_record_file_sha256": recovery_file_sha256,
            "historical_handoff_file_sha256":
                historical_boundary["watch_handoff_receipt_file_sha256"],
            "transaction_retained": True,
        }


def _submit_guardian_request(
        *, operation: str, arguments: dict[str, Any],
        systemctl: Callable[[list[str]], None],
) -> dict[str, Any]:
    _require_production_mutation_parents(
        DEFAULT_IDENTITIES, DEFAULT_DROP_IN)
    if (
            _load_control_transaction(
                _transaction_path(LOCAL_PAPER_STATE_ROOT)) is not None or
            any(path.exists() or path.is_symlink() for path in (
                GUARDIAN_REQUEST_PATH, GUARDIAN_ACTIVE_PATH,
                BROKER_START_PERMIT_PATH))):
        raise LocalPaperError(
            "guardian must be inactive with zero transaction residue")
    request = _write_guardian_request(
        operation=operation, arguments=arguments)
    try:
        systemctl(["start", GUARDIAN_UNIT])
    except Exception:
        try:
            systemctl(["stop", GUARDIAN_UNIT])
        except Exception:
            pass
        raise
    transaction = _load_control_transaction(
        _transaction_path(LOCAL_PAPER_STATE_ROOT))
    try:
        active = _runtime_document(
            GUARDIAN_ACTIVE_PATH, fields=GUARDIAN_RUNTIME_FIELDS,
            schema=GUARDIAN_RUNTIME_SCHEMA)
    except LocalPaperError:
        active = None
    expected_phase = (
        CONTROL_RECOVERY_TERMINAL_PHASE if operation == "ENABLE_RECOVERY"
        else CONTROL_NORMAL_TERMINAL_PHASE)
    expected_mode = (
        "RECOVERY_PAPER" if operation == "ENABLE_RECOVERY" else
        "LOCAL_PAPER")
    effective = status(DEFAULT_IDENTITIES, drop_in_path=DEFAULT_DROP_IN)
    effective_identity_manifest_sha256 = effective.get(
        "identity_manifest_sha256")
    if (
            GUARDIAN_REQUEST_PATH.exists() or
            GUARDIAN_REQUEST_PATH.is_symlink() or
            transaction is None or active is None or
            transaction.get("operation") != operation or
            transaction.get("phase") != expected_phase or
            transaction.get("request", {}).get("guardian_request_id") !=
                request["request_id"] or
            active.get("guardian_request_id") != request["request_id"] or
            active.get("operation") != operation or
            active.get("phase") != expected_phase or
            active.get("mode") != expected_mode or
            effective.get("mode") != expected_mode or
            effective.get("effective_state_verified") is not True or
            not isinstance(effective_identity_manifest_sha256, str) or
            DIGEST.fullmatch(effective_identity_manifest_sha256) is None or
            effective_identity_manifest_sha256 !=
                transaction.get("target_identity_manifest_sha256") or
            active.get("target_identity_manifest_sha256") !=
                effective_identity_manifest_sha256 or
            not _runtime_binding_matches(
                active, transaction, identities_path=DEFAULT_IDENTITIES,
                drop_in_path=DEFAULT_DROP_IN)):
        try:
            systemctl(["stop", GUARDIAN_UNIT])
        except Exception:
            pass
        raise LocalPaperError("guardian activation did not commit exactly")
    return {
        "mode": expected_mode,
        "domain": "alpha", "paper_authorized": True,
        "live_authorized": False, "guardian_supervised": True,
        "identity_manifest_sha256": effective_identity_manifest_sha256,
        # Keep the guardian admission boundary explicit for callers that
        # validate the returned activation contract.  The transaction body
        # already enforces the corresponding policy/profile; exposing the
        # same canonical mode here prevents a successful local-only enable
        # from being mistaken for an incomplete response during recovery.
        "admission_mode": (
            "external-p1-recovery" if operation == "ENABLE_RECOVERY" else
            ("external-p1-finalized"
             if arguments.get("external_p1_finalized") is True else
             "local-only")),
        "guardian_unit": GUARDIAN_UNIT,
        "guardian_request_id": request["request_id"],
    }


def enable(
        *, domain: str, authority_path: Path, identities_path: Path,
        env_root: Path, drop_in_path: Path,
        gateway_env_root: Path = DEFAULT_GATEWAY_ENV_ROOT,
        external_p1_finalized: bool = False,
        handoff_path: Path | None = None,
        handoff_file_sha256: str | None = None,
        handoff_body_sha256: str | None = None,
        campaign_id: str | None = None,
        source_baseline_sha256: str | None = None,
        systemctl: Callable[[list[str]], None] = _run_systemctl,
        external_verifier: Callable[..., dict[str, Any]] = verify_external_p1,
        transaction_root: Path | None = None,
) -> dict[str, Any]:
    del external_verifier
    root = LOCAL_PAPER_STATE_ROOT if transaction_root is None else transaction_root
    if (
            domain != "alpha" or authority_path != DEFAULT_AUTHORITY or
            identities_path != DEFAULT_IDENTITIES or
            env_root != DEFAULT_ENV_ROOT or
            drop_in_path != DEFAULT_DROP_IN or
            gateway_env_root != DEFAULT_GATEWAY_ENV_ROOT or
            root != LOCAL_PAPER_STATE_ROOT):
        raise LocalPaperError(
            "guardian-managed local PAPER paths must be canonical")
    arguments = {
        "domain": domain, "authority_path": str(authority_path),
        "identities_path": str(identities_path), "env_root": str(env_root),
        "drop_in_path": str(drop_in_path),
        "gateway_env_root": str(gateway_env_root),
        "external_p1_finalized": external_p1_finalized,
        "handoff_path": str(handoff_path) if handoff_path is not None else None,
        "handoff_file_sha256": handoff_file_sha256,
        "handoff_body_sha256": handoff_body_sha256,
        "campaign_id": campaign_id,
        "source_baseline_sha256": source_baseline_sha256,
    }
    return _submit_guardian_request(
        operation="ENABLE", arguments=arguments, systemctl=systemctl)


def enable_recovery(
        *, authority_path: Path, identities_path: Path, env_root: Path,
        drop_in_path: Path, recovery_path: Path,
        recovery_file_sha256: str, recovery_body_sha256: str,
        gateway_env_root: Path = DEFAULT_GATEWAY_ENV_ROOT,
        systemctl: Callable[[list[str]], None] = _run_systemctl,
        external_verifier: Callable[..., dict[str, Any]] = verify_external_p1,
        command: Callable[
            [list[str]], subprocess.CompletedProcess[str]] = _run_command,
        transaction_root: Path | None = None,
        now_ms: int | None = None,
) -> dict[str, Any]:
    del external_verifier, command
    root = LOCAL_PAPER_STATE_ROOT if transaction_root is None else transaction_root
    if (
            authority_path != DEFAULT_AUTHORITY or
            identities_path != DEFAULT_IDENTITIES or
            env_root != DEFAULT_ENV_ROOT or drop_in_path != DEFAULT_DROP_IN or
            gateway_env_root != DEFAULT_GATEWAY_ENV_ROOT or
            recovery_path != RECOVERY_AUTHORITY_PATH or
            root != LOCAL_PAPER_STATE_ROOT or now_ms is not None):
        raise LocalPaperError(
            "guardian-managed recovery paths must be canonical")
    arguments = {
        "authority_path": str(authority_path),
        "identities_path": str(identities_path), "env_root": str(env_root),
        "drop_in_path": str(drop_in_path),
        "gateway_env_root": str(gateway_env_root),
        "recovery_path": str(recovery_path),
        "recovery_file_sha256": recovery_file_sha256,
        "recovery_body_sha256": recovery_body_sha256,
    }
    return _submit_guardian_request(
        operation="ENABLE_RECOVERY", arguments=arguments,
        systemctl=systemctl)


def _sd_notify(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET", "")
    if not address:
        raise LocalPaperError("guardian notify socket unavailable")
    if address.startswith("@"):
        address = "\0" + address[1:]
    notifier = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        notifier.connect(address)
        notifier.sendall(message.encode("ascii"))
    except OSError as error:
        raise LocalPaperError("guardian systemd notification failed") from error
    finally:
        notifier.close()


def _guardian_request_paths_valid(arguments: dict[str, Any]) -> bool:
    expected = {
        "authority_path": str(DEFAULT_AUTHORITY),
        "identities_path": str(DEFAULT_IDENTITIES),
        "env_root": str(DEFAULT_ENV_ROOT),
        "drop_in_path": str(DEFAULT_DROP_IN),
        "gateway_env_root": str(DEFAULT_GATEWAY_ENV_ROOT),
    }
    return all(arguments.get(key) == value for key, value in expected.items())


def _guardian_run_body(
        *, command: Callable[
            [list[str]], subprocess.CompletedProcess[str]] = _run_command,
        systemctl: Callable[[list[str]], None] = _run_systemctl,
) -> dict[str, Any]:
    identity = _process_identity(os.getpid())
    if not _process_in_guardian_unit(os.getpid(), "alpha"):
        raise LocalPaperError("guardian must run inside its static systemd unit")
    _require_production_mutation_parents(
        DEFAULT_IDENTITIES, DEFAULT_DROP_IN)
    request = _consume_guardian_request()
    arguments = request["arguments"]
    if not _guardian_request_paths_valid(arguments):
        raise LocalPaperError("guardian request path drift")
    operation = request["operation"]
    if operation == "ENABLE":
        expected_fields = {
            "domain", "authority_path", "identities_path", "env_root",
            "drop_in_path", "gateway_env_root", "external_p1_finalized",
            "handoff_path", "handoff_file_sha256", "handoff_body_sha256",
            "campaign_id", "source_baseline_sha256",
        }
        if set(arguments) != expected_fields or arguments.get("domain") != "alpha":
            raise LocalPaperError("guardian enable request invalid")
        result = _enable_transaction(
            domain="alpha", authority_path=Path(arguments["authority_path"]),
            identities_path=Path(arguments["identities_path"]),
            env_root=Path(arguments["env_root"]),
            drop_in_path=Path(arguments["drop_in_path"]),
            gateway_env_root=Path(arguments["gateway_env_root"]),
            external_p1_finalized=bool(arguments["external_p1_finalized"]),
            handoff_path=(Path(arguments["handoff_path"])
                          if arguments["handoff_path"] is not None else None),
            handoff_file_sha256=arguments["handoff_file_sha256"],
            handoff_body_sha256=arguments["handoff_body_sha256"],
            campaign_id=arguments["campaign_id"],
            source_baseline_sha256=arguments["source_baseline_sha256"],
            systemctl=systemctl, guardian_identity=identity,
            guardian_request_id=request["request_id"])
    else:
        expected_fields = {
            "authority_path", "identities_path", "env_root", "drop_in_path",
            "gateway_env_root", "recovery_path", "recovery_file_sha256",
            "recovery_body_sha256",
        }
        if set(arguments) != expected_fields:
            raise LocalPaperError("guardian recovery request invalid")
        result = _enable_recovery_transaction(
            authority_path=Path(arguments["authority_path"]),
            identities_path=Path(arguments["identities_path"]),
            env_root=Path(arguments["env_root"]),
            drop_in_path=Path(arguments["drop_in_path"]),
            gateway_env_root=Path(arguments["gateway_env_root"]),
            recovery_path=Path(arguments["recovery_path"]),
            recovery_file_sha256=arguments["recovery_file_sha256"],
            recovery_body_sha256=arguments["recovery_body_sha256"],
            systemctl=systemctl, command=command,
            guardian_identity=identity,
            guardian_request_id=request["request_id"])
    expected_mode = result["mode"]
    effective = status(DEFAULT_IDENTITIES, command=command)
    if (
            effective.get("mode") != expected_mode or
            effective.get("effective_state_verified") is not True):
        raise LocalPaperError("guardian effective PAPER state not verified")
    stop_requested = False

    def stop_handler(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_term = signal.signal(signal.SIGTERM, stop_handler)
    previous_int = signal.signal(signal.SIGINT, stop_handler)
    try:
        _sd_notify(f"READY=1\nSTATUS={expected_mode} guardian active")
        watchdog_usec = int(os.environ.get("WATCHDOG_USEC", "10000000"))
        interval = max(0.25, min(5.0, watchdog_usec / 3_000_000))
        while not stop_requested:
            effective = status(DEFAULT_IDENTITIES, command=command)
            if (
                    effective.get("mode") != expected_mode or
                    effective.get("effective_state_verified") is not True):
                raise LocalPaperError("guardian runtime health failed")
            _sd_notify("WATCHDOG=1")
            deadline = time.monotonic() + interval
            while not stop_requested and time.monotonic() < deadline:
                time.sleep(min(0.1, deadline - time.monotonic()))
        _remove_runtime_artifact(GUARDIAN_ACTIVE_PATH)
        _sd_notify("STOPPING=1\nSTATUS=guardian fail-close requested")
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
    return {
        "mode": "DENY_ALL_PENDING_EXEC_STOP_POST",
        "guardian_stopped": True,
    }


def _guardian_direct_input_fail_close() -> None:
    source_policy_sha256 = _fallback_source_policy_sha256(DEFAULT_IDENTITIES)
    payload = _pretty_json(_deny_all_document(source_policy_sha256))
    _atomic_write(DEFAULT_IDENTITIES, payload, 0o600)
    try:
        DEFAULT_DROP_IN.unlink()
        _fsync_directory(DEFAULT_DROP_IN.parent)
    except FileNotFoundError:
        pass
    for path in (
            GUARDIAN_ACTIVE_PATH, BROKER_START_PERMIT_PATH,
            GUARDIAN_REQUEST_PATH):
        try:
            _remove_runtime_artifact(path)
        except LocalPaperError:
            pass


def _guardian_watchdog_interval() -> float:
    raw = os.environ.get("WATCHDOG_USEC", "10000000")
    if not raw.isdecimal() or int(raw) <= 0:
        raise LocalPaperError("guardian watchdog interval invalid")
    return max(0.25, min(5.0, int(raw) / 3_000_000))


def _bounded_guardian_systemctl(
        systemctl: Callable[[list[str]], None], arguments: list[str],
        *, deadline: float, interval: float,
) -> None:
    completed = threading.Event()
    failure: list[BaseException] = []

    def invoke() -> None:
        try:
            # The production callback is the module's systemctl runner.  Give
            # it the same absolute deadline so the systemctl subprocess client
            # is terminated on timeout; injected callbacks retain the
            # thread-isolated compatibility path used by tests and recovery
            # callers.
            if systemctl is _run_systemctl:
                _run_systemctl(
                    arguments,
                    timeout=max(0.001, deadline - time.monotonic()))
            else:
                systemctl(arguments)
        except BaseException as error:
            failure.append(error)
        finally:
            completed.set()

    threading.Thread(
        target=invoke, name="hepta-guardian-systemctl", daemon=True).start()
    while not completed.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LocalPaperError("guardian fail-close systemctl timed out")
        if completed.wait(min(interval, remaining)):
            break
        _sd_notify(
            "WATCHDOG=1\nSTATUS=guardian ordered fail-close in progress")
    if failure:
        raise failure[0]


def _guardian_ordered_fail_close_or_direct(
        *, systemctl: Callable[[list[str]], None],
) -> None:
    """Run the ordered guardian close, with a last-resort input revoke.

    The ordered transaction stops every PAPER consumer and the broker before
    replacing the identity manifest.  That ordering is important because the
    egress watchdog fingerprints the manifest while it is running.  The
    direct path is intentionally kept as a fallback for a broken control
    plane, but must never be the first operation on an abnormal guardian
    exit.
    """
    try:
        deadline = time.monotonic() + GUARDIAN_FAIL_CLOSE_MAX_SECONDS
        interval = _guardian_watchdog_interval()
        _sd_notify(
            "WATCHDOG=1\nSTATUS=guardian ordered fail-close requested")

        def bounded_systemctl(arguments: list[str]) -> None:
            _bounded_guardian_systemctl(
                systemctl, arguments, deadline=deadline, interval=interval)

        # Pass the live module paths explicitly.  Besides making the cleanup
        # boundary auditable, this avoids accidentally using a function
        # default captured before a test/packaged runtime swapped its
        # canonical path constants.
        guardian_fail_close(
            identities_path=DEFAULT_IDENTITIES,
            drop_in_path=DEFAULT_DROP_IN,
            systemctl=bounded_systemctl,
            transaction_root=LOCAL_PAPER_STATE_ROOT)
    except BaseException:
        # A direct revoke is the only safe fallback if the ordered root
        # transaction cannot run.  If this fallback itself fails, let its
        # exception propagate to the caller (which may be preserving an
        # already-active guardian exception).
        _guardian_direct_input_fail_close()


def guardian_run(
        *, command: Callable[
            [list[str]], subprocess.CompletedProcess[str]] = _run_command,
        systemctl: Callable[[list[str]], None] = _run_systemctl,
) -> dict[str, Any]:
    clean_stop = False
    try:
        result = _guardian_run_body(command=command, systemctl=systemctl)
    except BaseException:
        # Preserve the original guardian error even if either cleanup path
        # fails.  In particular, a runtime-health exception must not be
        # replaced by an identity-write/systemctl exception.
        try:
            _guardian_ordered_fail_close_or_direct(systemctl=systemctl)
        except BaseException:
            pass
        raise
    clean_stop = (
        isinstance(result, dict) and
        result.get("mode") == "DENY_ALL_PENDING_EXEC_STOP_POST" and
        result.get("guardian_stopped") is True)
    if not clean_stop:
        # On a normal SIGTERM/SIGINT shutdown, ExecStopPost invokes the
        # ordered root fail-close transaction (stop consumers/broker, then
        # replace the identity manifest).  Avoiding a second close here keeps
        # that planned replacement free of watchdog path/inode/digest drift.
        # Any other return is abnormal: use the same ordered path as an
        # exception, with direct input revoke only as its fallback.
        _guardian_ordered_fail_close_or_direct(systemctl=systemctl)
    return result


def _fallback_source_policy_sha256(identities_path: Path) -> str:
    try:
        value = _load_json(identities_path).get("source_policy_sha256")
    except LocalPaperError:
        value = None
    return value if isinstance(value, str) and DIGEST.fullmatch(value) else (
        "sha256:" + "0" * 64)


def _target_artifacts_match(
        transaction: dict[str, Any], *, identities_path: Path,
        drop_in_path: Path,
) -> bool:
    try:
        identities_payload, _identities_metadata = _secure_read(
            identities_path, mode=0o600)
        drop_in_payload, _drop_in_metadata = _secure_read(
            drop_in_path, mode=0o644)
    except LocalPaperError:
        return False
    return (
        _sha256(identities_payload) ==
            transaction.get("target_identity_manifest_sha256") and
        _sha256(drop_in_payload) ==
            transaction.get("target_drop_in_sha256"))


def _runtime_binding_matches(
        document: dict[str, Any], transaction: dict[str, Any],
        *, identities_path: Path, drop_in_path: Path,
) -> bool:
    return (
        document.get("domain") == "alpha" and
        document.get("transaction_id") == transaction.get("transaction_id") and
        document.get("operation") == transaction.get("operation") and
        document.get("phase") == transaction.get("phase") and
        document.get("request_sha256") == transaction.get("request_sha256") and
        document.get("target_identity_manifest_sha256") ==
            transaction.get("target_identity_manifest_sha256") and
        document.get("target_drop_in_sha256") ==
            transaction.get("target_drop_in_sha256") and
        document.get("guardian_request_id") ==
            transaction.get("request", {}).get("guardian_request_id") and
        isinstance(document.get("guardian_request_id"), str) and
        re.fullmatch(r"[0-9a-f]{32}",
                     document["guardian_request_id"]) is not None and
        document.get("control_image_sha256") == _control_image_sha256() and
        _guardian_identity_matches(document) and
        _target_artifacts_match(
            transaction, identities_path=identities_path,
            drop_in_path=drop_in_path))


def _validate_pending_broker_start_permit(
        transaction: dict[str, Any], *, identities_path: Path,
        drop_in_path: Path, now_ms: int, host_authority_root: Path,
) -> bool:
    try:
        permit = _runtime_document(
            BROKER_START_PERMIT_PATH, fields=BROKER_START_PERMIT_FIELDS,
            schema=BROKER_START_PERMIT_SCHEMA)
    except LocalPaperError:
        return False
    if permit is None:
        return False
    expected_suffix = (
        "START_BROKER_RECOVERY" if transaction.get("operation") ==
        "ENABLE_RECOVERY" else "START_BROKER_LOCAL_PAPER")
    try:
        valid = (
            transaction.get("operation") in {"ENABLE", "ENABLE_RECOVERY"} and
            isinstance(transaction.get("phase"), str) and
            transaction["phase"].endswith(expected_suffix) and
            type(permit.get("issued_at_ms")) is int and
            type(permit.get("expires_at_ms")) is int and
            permit.get("expires_at_ms", 0) - permit.get("issued_at_ms", 0) ==
                BROKER_START_PERMIT_TTL_MS and
            permit.get("issued_at_ms", now_ms + 1) <= now_ms <
                permit.get("expires_at_ms", 0) and
            _runtime_binding_matches(
                permit, transaction, identities_path=identities_path,
                drop_in_path=drop_in_path))
    except LocalPaperError:
        valid = False
    if not valid:
        return False
    try:
        final = _runtime_document(
            BROKER_START_PERMIT_PATH, fields=BROKER_START_PERMIT_FIELDS,
            schema=BROKER_START_PERMIT_SCHEMA)
        if final != permit:
            return False
        with _host_authority_lease(host_authority_root) as lease:
            owner_payload = _host_authority_owner_payload(lease)
            if owner_payload is None:
                return False
            _validate_broker_activation_reservation(
                owner_payload, transaction=transaction, permit=permit,
                now_ms=now_ms, require_live_guardian=True)
            final = _runtime_document(
                BROKER_START_PERMIT_PATH, fields=BROKER_START_PERMIT_FIELDS,
                schema=BROKER_START_PERMIT_SCHEMA)
            if final != permit or _host_authority_owner_payload(lease) != (
                    owner_payload):
                return False
    except LocalPaperError:
        return False
    return True


def _valid_established_guardian(
        transaction: dict[str, Any], *, identities_path: Path,
        drop_in_path: Path,
) -> bool:
    try:
        active = _runtime_document(
            GUARDIAN_ACTIVE_PATH, fields=GUARDIAN_RUNTIME_FIELDS,
            schema=GUARDIAN_RUNTIME_SCHEMA)
    except LocalPaperError:
        return False
    if active is None:
        return False
    expected = (
        ("ENABLE", CONTROL_NORMAL_TERMINAL_PHASE, "LOCAL_PAPER")
        if transaction.get("operation") == "ENABLE" else
        ("ENABLE_RECOVERY", CONTROL_RECOVERY_TERMINAL_PHASE,
         "RECOVERY_PAPER"))
    try:
        return (
            transaction.get("operation") == expected[0] and
            transaction.get("phase") == expected[1] and
            active.get("status") == "ACTIVE" and
            active.get("mode") == expected[2] and
            type(active.get("recorded_at_ms")) is int and
            active.get("recorded_at_ms", 0) > 0 and
            _runtime_binding_matches(
                active, transaction, identities_path=identities_path,
                drop_in_path=drop_in_path))
    except LocalPaperError:
        return False


def _valid_committed_activation_handoff(
        transaction: dict[str, Any], *, identities_path: Path,
        drop_in_path: Path, host_authority_root: Path,
) -> bool:
    """Recognize the durable broker->preflight handoff without reauthorizing.

    This state has no start permit.  It is accepted only so a supervised
    broker restart can replay its already-committed ACTIVE generation while
    the exact reservation/consumed pair awaits preflight adoption.
    """
    try:
        with _host_authority_lease(host_authority_root) as lease:
            reservation_payload = _host_authority_owner_payload(lease)
            if (
                    reservation_payload is None or
                    not _activation_reservation_belongs_to_transaction(
                        reservation_payload, transaction)):
                return False
            reservation = _validate_canonical_seal(
                reservation_payload, json.loads(reservation_payload),
                "local PAPER activation reservation invalid")
            if reservation.get("phase") != transaction.get("phase"):
                return False
            activation_id = str(reservation.get("activation_id", ""))
            path = _broker_activation_consumed_path(
                host_authority_root, activation_id)
            payload, _metadata = _read_host_authority_artifact(
                lease, path.name,
                maximum=MAX_HOST_AUTHORITY_ARTIFACT_BYTES)
            consumed = _validate_canonical_seal(
                payload, json.loads(payload),
                "local PAPER activation completion invalid")
            if (
                    set(consumed) != BROKER_ACTIVATION_CONSUMED_FIELDS or
                    consumed.get("schema") !=
                        BROKER_ACTIVATION_CONSUMED_SCHEMA or
                    consumed.get("status") !=
                        BROKER_ACTIVATION_CONSUMED_STATUS or
                    consumed.get("boot_id") != _read_boot_id() or
                    consumed.get("reservation_file_sha256") !=
                        _sha256(reservation_payload) or
                    consumed.get("reservation_body_sha256") !=
                        reservation.get("body_sha256") or
                    any(consumed.get(field) != reservation.get(field)
                        for field in (
                            "activation_id", "guardian_request_id", "domain",
                            "transaction_id", "operation", "phase",
                            "request_sha256",
                            "target_identity_manifest_sha256",
                            "target_drop_in_sha256", "control_image_sha256",
                            "required_pre_activation_boundary",
                            "broker_start_permit_file_sha256",
                            "broker_start_permit_body_sha256")) or
                    _sha256(_secure_read(
                        identities_path, mode=0o600)[0]) !=
                        reservation.get("target_identity_manifest_sha256") or
                    _sha256(_secure_read(
                        drop_in_path, mode=0o644)[0]) !=
                        reservation.get("target_drop_in_sha256") or
                    BROKER_START_PERMIT_PATH.exists() or
                    BROKER_START_PERMIT_PATH.is_symlink() or
                    _broker_activation_intent_path(
                        host_authority_root, activation_id).exists() or
                    _broker_activation_intent_path(
                        host_authority_root, activation_id).is_symlink()):
                return False
            names = sorted(
                name for name in os.listdir(host_authority_root)
                if name.startswith(BROKER_ACTIVATION_CONSUMED_PREFIX))
            return names == [path.name]
    except (LocalPaperError, OSError, UnicodeError, json.JSONDecodeError):
        return False
def _current_input_is_exact_deny_all(
        identities_path: Path, drop_in_path: Path,
) -> bool:
    if drop_in_path.exists() or drop_in_path.is_symlink():
        return False
    try:
        payload, _metadata = _secure_read(identities_path, mode=0o600)
        document = json.loads(payload)
    except (LocalPaperError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(document, dict) and
        payload == _pretty_json(document) and
        document == _deny_all_document(
            str(document.get("source_policy_sha256", ""))) and
        isinstance(document.get("source_policy_sha256"), str) and
        DIGEST.fullmatch(document["source_policy_sha256"]) is not None)


def reconcile_before_broker(
        *, identities_path: Path, drop_in_path: Path,
        transaction_root: Path | None = None,
        host_authority_root: Path | None = None,
) -> dict[str, Any]:
    _require_production_mutation_parents(identities_path, drop_in_path)
    root = LOCAL_PAPER_STATE_ROOT if transaction_root is None else transaction_root
    authority_root = (
        HOST_AUTHORITY_DIRECTORY
        if host_authority_root is None else host_authority_root)
    transaction_path = _transaction_path(root)
    try:
        transaction = _load_control_transaction(transaction_path)
        invalid = False
    except LocalPaperError:
        transaction = {"operation": "INVALID", "phase": "INVALID"}
        invalid = True
    if transaction is None and _current_input_is_exact_deny_all(
            identities_path, drop_in_path):
        return {
            "mode": "DENY_ALL", "wal_present": False,
            "paper_authorized": False, "live_authorized": False,
            "identity_count": 0, "input_normalized": False,
        }
    timestamp = time.time_ns() // 1_000_000
    if (
            transaction is not None and not invalid and
            (_validate_pending_broker_start_permit(
                transaction, identities_path=identities_path,
                drop_in_path=drop_in_path, now_ms=timestamp,
                host_authority_root=authority_root) or
             _valid_committed_activation_handoff(
                transaction, identities_path=identities_path,
                drop_in_path=drop_in_path,
                host_authority_root=authority_root) or
             _valid_established_guardian(
                transaction, identities_path=identities_path,
                drop_in_path=drop_in_path))):
        return {
            "mode": "GUARDIAN_AUTHORIZED_START", "wal_present": True,
            "wal_operation": transaction["operation"],
            "wal_phase": transaction["phase"],
            "guardian_verified": True, "input_normalized": False,
        }
    source_policy_sha256 = _fallback_source_policy_sha256(identities_path)
    payload = _pretty_json(_deny_all_document(source_policy_sha256))
    _atomic_write(identities_path, payload, 0o600)
    try:
        drop_in_path.unlink()
        _fsync_directory(drop_in_path.parent)
    except FileNotFoundError:
        pass
    return {
        "mode": "DENY_ALL", "paper_authorized": False,
        "live_authorized": False, "identity_count": 0,
        "identity_manifest_sha256": _sha256(payload), "wal_present": True,
        "wal_operation": (
            transaction.get("operation") if transaction is not None else None),
        "wal_phase": (
            transaction.get("phase") if transaction is not None else None),
        "wal_retained": transaction is not None,
        "input_normalized": True,
    }


def reconcile_control_transaction(
        *, identities_path: Path, drop_in_path: Path,
        systemctl: Callable[[list[str]], None] = _run_systemctl,
        transaction_root: Path | None = None,
) -> dict[str, Any]:
    root = LOCAL_PAPER_STATE_ROOT if transaction_root is None else transaction_root
    transaction_path = _transaction_path(root)
    with _control_transaction_lock(root):
        transaction = _load_control_transaction(transaction_path)
        if transaction is None:
            return {"mode": "UNCHANGED", "wal_present": False}
        request = transaction["request"]
        if (
                request.get("domain") != "alpha" or
                request.get("identities_path") != str(identities_path) or
                request.get("drop_in_path") != str(drop_in_path)):
            raise LocalPaperError("local PAPER control WAL argument drift")
        holder, before = _phase_driver(transaction, transaction_path)
        result = _disable_body(
            identities_path=identities_path, drop_in_path=drop_in_path,
            domain="alpha", systemctl=systemctl,
            before_side_effect=before)
        authority_root_value = request.get("host_authority_root")
        if not isinstance(authority_root_value, str):
            authority_root_value = str(HOST_AUTHORITY_DIRECTORY)
        _clear_transaction_activation_reservation(
            holder[0], host_authority_root=Path(authority_root_value))
        if transaction["operation"] == "ENABLE_RECOVERY":
            holder[0] = _persist_control_phase(
                holder[0], transaction_path,
                "RECOVERY_RECONCILED_DENY_ALL")
            result.update({
                "recovery_retained": True, "transaction_retained": True,
                "wal_operation": "ENABLE_RECOVERY",
            })
        else:
            _complete_control_transaction(holder[0], transaction_path)
            result.update({
                "recovery_retained": False,
                "transaction_retained": False,
                "reconciled_operation": transaction["operation"],
            })
        return result


def _emergency_guardian_deny_all(
        *, identities_path: Path, drop_in_path: Path,
        systemctl: Callable[[list[str]], None],
) -> dict[str, Any]:
    source_policy_sha256 = _fallback_source_policy_sha256(identities_path)
    payload = _pretty_json(_deny_all_document(source_policy_sha256))
    systemctl(["stop", *_control_stop_units("alpha")])
    systemctl(["stop", BROKER_UNIT])
    _atomic_write(identities_path, payload, 0o600)
    try:
        drop_in_path.unlink()
        _fsync_directory(drop_in_path.parent)
    except FileNotFoundError:
        pass
    systemctl(["daemon-reload"])
    systemctl(["start", BROKER_UNIT])
    return {
        "mode": "DENY_ALL", "paper_authorized": False,
        "live_authorized": False, "identity_count": 0,
        "identity_manifest_sha256": _sha256(payload),
        "guardian_fail_close": True,
    }


def guardian_fail_close(
        *, identities_path: Path = DEFAULT_IDENTITIES,
        drop_in_path: Path = DEFAULT_DROP_IN,
        systemctl: Callable[[list[str]], None] = _run_systemctl,
        transaction_root: Path | None = None,
) -> dict[str, Any]:
    _require_production_mutation_parents(identities_path, drop_in_path)
    root = LOCAL_PAPER_STATE_ROOT if transaction_root is None else transaction_root
    for path in (
            GUARDIAN_ACTIVE_PATH, BROKER_START_PERMIT_PATH,
            GUARDIAN_REQUEST_PATH):
        try:
            _remove_runtime_artifact(path)
        except LocalPaperError:
            # Invalid runtime residue can never authorize a broker start. Keep
            # it as forensic evidence and continue the fail-close path.
            pass
    try:
        result = reconcile_control_transaction(
            identities_path=identities_path, drop_in_path=drop_in_path,
            systemctl=systemctl, transaction_root=root)
        if result.get("mode") == "UNCHANGED":
            result = _emergency_guardian_deny_all(
                identities_path=identities_path,
                drop_in_path=drop_in_path, systemctl=systemctl)
    except LocalPaperError:
        result = _emergency_guardian_deny_all(
            identities_path=identities_path,
            drop_in_path=drop_in_path, systemctl=systemctl)
        result["invalid_wal_retained"] = True
    result["guardian_fail_close"] = True
    return result


def complete_recovery(
        *, identities_path: Path, drop_in_path: Path,
        recovery_path: Path, recovery_file_sha256: str,
        recovery_body_sha256: str, completion_path: Path,
        completion_file_sha256: str, completion_body_sha256: str,
        systemctl: Callable[[list[str]], None] = _run_systemctl,
        transaction_root: Path | None = None,
        now_ms: int | None = None,
) -> dict[str, Any]:
    root = LOCAL_PAPER_STATE_ROOT if transaction_root is None else transaction_root
    recovery, _recovery_boundary = validate_recovery_authority(
        recovery_path=recovery_path,
        expected_file_sha256=recovery_file_sha256,
        expected_body_sha256=recovery_body_sha256, now_ms=now_ms)
    completion = validate_recovery_completion(
        completion_path=completion_path,
        expected_file_sha256=completion_file_sha256,
        expected_body_sha256=completion_body_sha256, recovery=recovery,
        recovery_file_sha256=recovery_file_sha256,
        recovery_body_sha256=recovery_body_sha256, now_ms=now_ms)
    systemctl(["stop", GUARDIAN_UNIT])
    transaction_path = _transaction_path(root)
    with _control_transaction_lock(root):
        transaction = _load_control_transaction(transaction_path)
        if (
                transaction is None or
                transaction.get("operation") != "ENABLE_RECOVERY" or
                transaction.get("recovery_record_file_sha256") !=
                    recovery_file_sha256 or
                transaction.get("recovery_record_body_sha256") !=
                    recovery_body_sha256):
            raise LocalPaperError("recovery control WAL missing or drifted")
        request = transaction["request"]
        if (
                request.get("identities_path") != str(identities_path) or
                request.get("drop_in_path") != str(drop_in_path) or
                request.get("recovery_path") != str(recovery_path) or
                request.get("campaign_id") != recovery["campaign_id"] or
                request.get("suspension_id") != recovery["suspension_id"]):
            raise LocalPaperError("recovery control WAL argument drift")
        holder, before = _phase_driver(transaction, transaction_path)
        result = _disable_body(
            identities_path=identities_path, drop_in_path=drop_in_path,
            domain="alpha", systemctl=systemctl,
            before_side_effect=before)
        final_recovery, _final_boundary = validate_recovery_authority(
            recovery_path=recovery_path,
            expected_file_sha256=recovery_file_sha256,
            expected_body_sha256=recovery_body_sha256, now_ms=now_ms)
        final_completion = validate_recovery_completion(
            completion_path=completion_path,
            expected_file_sha256=completion_file_sha256,
            expected_body_sha256=completion_body_sha256,
            recovery=final_recovery,
            recovery_file_sha256=recovery_file_sha256,
            recovery_body_sha256=recovery_body_sha256, now_ms=now_ms)
        if final_recovery != recovery or final_completion != completion:
            raise LocalPaperError("recovery completion evidence drifted")
        _complete_recovery_control_transaction(
            holder[0], transaction_path, recovery=final_recovery,
            completion=final_completion)
        result.update({
            "recovery_completed": True,
            "recovery_id": recovery["recovery_id"],
            "campaign_id": recovery["campaign_id"],
            "suspension_id": recovery["suspension_id"],
            "completion_file_sha256": completion_file_sha256,
            "transaction_retained": False,
        })
        return result


def _unit_active(
        command: Callable[[list[str]], subprocess.CompletedProcess[str]],
        unit: str,
) -> bool:
    completed = command([
        "/usr/bin/systemctl", "show", unit,
        "-p", "LoadState", "-p", "ActiveState", "-p", "Job"])
    properties: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key not in properties:
            properties[key] = value
    return (
        completed.returncode == 0 and
        properties == {"LoadState": "loaded", "ActiveState": "active",
                       "Job": ""})


def status(
        identities_path: Path,
        *, transaction_root: Path | None = None,
        drop_in_path: Path = DEFAULT_DROP_IN,
        command: Callable[
            [list[str]], subprocess.CompletedProcess[str]] = _run_command,
) -> dict[str, Any]:
    root = LOCAL_PAPER_STATE_ROOT if transaction_root is None else transaction_root
    try:
        payload, _metadata = _secure_read(identities_path, mode=0o600)
        document = json.loads(payload)
        if (not isinstance(document, dict) or
                payload != _pretty_json(document)):
            raise LocalPaperError("identity manifest invalid")
    except (LocalPaperError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "mode": "DENY_ALL", "paper_authorized": False,
            "live_authorized": False, "identity_count": 0,
            "identity_manifest_sha256": None,
            "effective_state_verified": False, "wal_state": "INVALID",
        }
    try:
        transaction = _load_control_transaction(_transaction_path(root))
        wal_state = "ABSENT" if transaction is None else str(
            transaction["operation"])
    except LocalPaperError:
        transaction = None
        wal_state = "INVALID"
    identities = document.get("identities")
    manifest_local = (
        document.get("schema") == IDENTITY_SCHEMA and
        document.get("version") == 1 and
        document.get("paper_authorized") is True and
        document.get("live_authorized") is False and
        isinstance(identities, list) and len(identities) == 1)
    manifest_deny = (
        document.get("schema") == IDENTITY_SCHEMA and
        document.get("version") == 1 and identities == [] and
        document.get("paper_authorized") is False and
        document.get("live_authorized") is False)
    if manifest_local:
        completed = command([
            "/usr/libexec/hepta-broker-egress-policy", "--policy",
            "/usr/share/heptatrader/hepta-broker-network-policy-v1.json",
            "--identity-manifest",
            "/usr/share/heptatrader/hepta-service-identities-v1.json",
            "--paper-identities", str(identities_path), "--check-active"])
        egress_ok = (
            completed.returncode == 0 and re.fullmatch(
                r"hepta_broker_egress_policy: PASS policy_sha256=[0-9a-f]{64} "
                r"authorized_connectors=1 authorized_uids=[1-9][0-9]* "
                r"protected_ports=4\s*", completed.stdout) is not None)
    elif manifest_deny:
        try:
            _require_external_p1_deny_all(
                identities_path=identities_path, command=command)
            egress_ok = True
        except LocalPaperError:
            egress_ok = False
    else:
        egress_ok = False
    local_units = (
        BROKER_UNIT,
        "hepta-execution-ib-paper@alpha.service",
        "hepta-tool-gateway@alpha.socket",
        "hepta-tool-session-supervisor@alpha.socket",
        "hepta-tool-gateway@alpha.service",
        "hepta-ib-paper-campaign-operator@alpha.socket",
    )
    local_units_ok = manifest_local and all(
        _unit_active(command, unit) for unit in local_units)
    local_ready = (
        transaction is not None and
        transaction.get("operation") == "ENABLE" and
        transaction.get("phase") == CONTROL_NORMAL_TERMINAL_PHASE and
        _valid_established_guardian(
            transaction, identities_path=identities_path,
            drop_in_path=drop_in_path))
    recovery_ready = (
        transaction is not None and
        transaction.get("operation") == "ENABLE_RECOVERY" and
        transaction.get("phase") == CONTROL_RECOVERY_TERMINAL_PHASE and
        _valid_established_guardian(
            transaction, identities_path=identities_path,
            drop_in_path=drop_in_path))
    recovery_units_ok = recovery_ready and all(
        _unit_active(command, unit) for unit in (
            BROKER_UNIT, *RECOVERY_START_UNITS)) and all(
                not _unit_active(command, unit)
                for unit in RECOVERY_FORBIDDEN_UNITS)
    mode = "DENY_ALL"
    effective = manifest_deny and egress_ok
    if manifest_local and egress_ok and local_ready and local_units_ok:
        mode = "LOCAL_PAPER"
        effective = True
    elif manifest_local and egress_ok and recovery_units_ok:
        try:
            _require_kill_switch(DOMAIN_KILL_SWITCH_PATH, PAPER_CONTROL_GID)
            _require_kill_switch(
                GLOBAL_KILL_SWITCH_PATH, GLOBAL_PAPER_CONTROL_GID)
            mode = "RECOVERY_PAPER"
            effective = True
        except LocalPaperError:
            pass
    return {
        "mode": mode,
        "paper_authorized": mode in {"LOCAL_PAPER", "RECOVERY_PAPER"},
        "live_authorized": False,
        "identity_count": len(identities) if isinstance(identities, list) else 0,
        "identity_manifest_sha256": _sha256(payload),
        "effective_state_verified": effective, "wal_state": wal_state,
        "egress_verified": egress_ok,
        "runtime_units_verified": local_units_ok or recovery_units_ok,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=(
            "enable", "disable", "status", "verify-external-p1",
            "enable-recovery", "reconcile-before-broker", "reconcile",
            "complete-recovery", "guardian", "guardian-fail-close"))
    parser.add_argument("--domain", default="alpha")
    parser.add_argument("--authority", type=Path, default=DEFAULT_AUTHORITY)
    parser.add_argument("--identities", type=Path, default=DEFAULT_IDENTITIES)
    parser.add_argument("--env-root", type=Path, default=DEFAULT_ENV_ROOT)
    parser.add_argument(
        "--gateway-env-root", type=Path, default=DEFAULT_GATEWAY_ENV_ROOT)
    parser.add_argument("--drop-in", type=Path, default=DEFAULT_DROP_IN)
    parser.add_argument("--external-p1-finalized", action="store_true")
    parser.add_argument(
        "--watch-handoff-receipt", type=Path,
        default=EXTERNAL_P1_HANDOFF_PATH)
    parser.add_argument("--watch-handoff-receipt-file-sha256")
    parser.add_argument("--watch-handoff-receipt-body-sha256")
    parser.add_argument("--campaign-id")
    parser.add_argument("--source-baseline-sha256")
    parser.add_argument(
        "--recovery-authority", type=Path, default=RECOVERY_AUTHORITY_PATH)
    parser.add_argument("--recovery-authority-file-sha256")
    parser.add_argument("--recovery-authority-body-sha256")
    parser.add_argument(
        "--recovery-completion", type=Path,
        default=RECOVERY_COMPLETION_PATH)
    parser.add_argument("--recovery-completion-file-sha256")
    parser.add_argument("--recovery-completion-body-sha256")
    arguments = parser.parse_args(argv)
    try:
        if DOMAIN.fullmatch(arguments.domain) is None:
            raise LocalPaperError("invalid domain")
        if arguments.action != "status" and os.geteuid() != 0:
            raise LocalPaperError("root required")
        if arguments.action == "enable":
            result = enable(
                domain=arguments.domain, authority_path=arguments.authority,
                identities_path=arguments.identities,
                env_root=arguments.env_root,
                drop_in_path=arguments.drop_in,
                gateway_env_root=arguments.gateway_env_root,
                external_p1_finalized=arguments.external_p1_finalized,
                handoff_path=(arguments.watch_handoff_receipt
                              if arguments.external_p1_finalized else None),
                handoff_file_sha256=(
                    arguments.watch_handoff_receipt_file_sha256
                    if arguments.external_p1_finalized else None),
                handoff_body_sha256=(
                    arguments.watch_handoff_receipt_body_sha256
                    if arguments.external_p1_finalized else None),
                campaign_id=(arguments.campaign_id
                             if arguments.external_p1_finalized else None),
                source_baseline_sha256=(
                    arguments.source_baseline_sha256
                    if arguments.external_p1_finalized else None))
        elif arguments.action == "disable":
            result = disable(
                identities_path=arguments.identities,
                drop_in_path=arguments.drop_in, domain=arguments.domain)
        elif arguments.action == "status":
            result = status(
                arguments.identities, drop_in_path=arguments.drop_in)
        elif arguments.action == "verify-external-p1":
            result = verify_external_p1(
                handoff_path=arguments.watch_handoff_receipt,
                expected_file_sha256=(
                    arguments.watch_handoff_receipt_file_sha256 or ""),
                expected_body_sha256=(
                    arguments.watch_handoff_receipt_body_sha256 or ""),
                campaign_id=arguments.campaign_id or "",
                source_baseline_sha256=arguments.source_baseline_sha256 or "",
                identities_path=arguments.identities,
                gateway_env_path=(arguments.gateway_env_root /
                                  f"{arguments.domain}.env"))
        elif arguments.action == "enable-recovery":
            result = enable_recovery(
                authority_path=arguments.authority,
                identities_path=arguments.identities,
                env_root=arguments.env_root,
                gateway_env_root=arguments.gateway_env_root,
                drop_in_path=arguments.drop_in,
                recovery_path=arguments.recovery_authority,
                recovery_file_sha256=(
                    arguments.recovery_authority_file_sha256 or ""),
                recovery_body_sha256=(
                    arguments.recovery_authority_body_sha256 or ""))
        elif arguments.action == "reconcile-before-broker":
            result = reconcile_before_broker(
                identities_path=arguments.identities,
                drop_in_path=arguments.drop_in)
        elif arguments.action == "reconcile":
            result = reconcile_control_transaction(
                identities_path=arguments.identities,
                drop_in_path=arguments.drop_in)
        elif arguments.action == "complete-recovery":
            result = complete_recovery(
                identities_path=arguments.identities,
                drop_in_path=arguments.drop_in,
                recovery_path=arguments.recovery_authority,
                recovery_file_sha256=(
                    arguments.recovery_authority_file_sha256 or ""),
                recovery_body_sha256=(
                    arguments.recovery_authority_body_sha256 or ""),
                completion_path=arguments.recovery_completion,
                completion_file_sha256=(
                    arguments.recovery_completion_file_sha256 or ""),
                completion_body_sha256=(
                    arguments.recovery_completion_body_sha256 or ""))
        elif arguments.action == "guardian":
            result = guardian_run()
        else:
            result = guardian_fail_close(
                identities_path=arguments.identities,
                drop_in_path=arguments.drop_in)
    except LocalPaperError as error:
        print(f"hepta_local_paper_control: FAIL {error}", file=sys.stderr)
        return 4
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
