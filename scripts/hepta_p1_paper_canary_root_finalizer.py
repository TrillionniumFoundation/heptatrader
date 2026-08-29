#!/usr/bin/env python3

"""Crash-safe fixed-alpha root finalizer for one external-P1 PAPER canary.

The service consumes one handoff-prebound out-of-band cleanup request.  It
never performs a PAPER tool call.  Its only mutation direction is toward the
root-owned DENY_ALL terminal state, and a sealed success receipt is returned
only after that state has been independently reopened and proved.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import struct
import subprocess
import sys
import time
from types import ModuleType
from typing import Any, Callable, Mapping, Protocol


VERSION = 1
MAX_BYTES = 1024 * 1024
PEER_UID = 2104
PEER_GID = 2104
PEER_CGROUP = "/system.slice/hepta-p1-paper-canary-executor.service"
PEER_UNIT = "hepta-p1-paper-canary-executor.service"
PEER_USER = "hepta-agent-alpha"
PEER_CREDENTIAL_IMAGE = (
    "/run/credentials/hepta-p1-paper-canary-executor/"
    "hepta-p1-paper-canary-executor.py")
PEER_IMAGE_PATH = "/usr/libexec/hepta-p1-paper-canary-executor"
ROOT_COORDINATOR_UNIT = "hepta-p1-paper-canary-root-coordinator.service"
ROOT_COORDINATOR_CGROUP = (
    "/system.slice/hepta-p1-paper-canary-root-coordinator.service")
ROOT_COORDINATOR_CREDENTIAL_ROOT = (
    "/run/credentials/hepta-p1-paper-canary-root-coordinator")
ROOT_COORDINATOR_CREDENTIAL_IMAGE = (
    ROOT_COORDINATOR_CREDENTIAL_ROOT +
    "/hepta-p1-paper-canary-root-coordinator.py")
ROOT_EMERGENCY_CLOSER_CREDENTIAL_IMAGE = (
    ROOT_COORDINATOR_CREDENTIAL_ROOT +
    "/hepta-p1-paper-canary-crash-emergency-closer.py")
ROOT_COORDINATOR_IMAGE_PATH = (
    "/usr/libexec/hepta-p1-paper-canary-root-coordinator")
ROOT_EMERGENCY_CLOSER_IMAGE_PATH = (
    "/usr/libexec/hepta-p1-paper-canary-crash-emergency-closer")
ROOT_TERMINAL_PROVER_IMAGE_PATH = (
    "/usr/libexec/hepta-p1-paper-canary-terminal-prover")
DOMAIN = "alpha"
SOCKET_PATH = "/run/hepta-p1-paper-canary-finalizer.sock"
ARTIFACT_ROOT = Path("/var/lib/hepta/p1-paper-canary")
CONTROL_ROOT = Path("/var/lib/hepta/p1-paper-canary-control")
STATE_ROOT = Path("/var/lib/hepta-local-ai-paper-agent")
WAL_PATH = STATE_ROOT / "p1-paper-canary-root-cleanup-transaction.v4.json"
LEGACY_WAL_PATHS = (
    STATE_ROOT / "p1-paper-canary-root-cleanup-transaction.json",
    STATE_ROOT / "p1-paper-canary-root-cleanup-transaction.v1.json",
    STATE_ROOT / "p1-paper-canary-root-cleanup-transaction.v2.json",
    STATE_ROOT / "p1-paper-canary-root-cleanup-transaction.v3.json",
)
LOCK_PATH = STATE_ROOT / "p1-paper-canary-root-cleanup.lock"
CONTROL_CREDENTIAL = "hepta-local-paper-control.py"
CONTROL_IMAGE_PATH = Path("/usr/libexec/hepta-local-paper-control")
OWNER_TOKEN_PATH = Path("/run/hepta-agent-alpha/sessions/session.token")
OWNER_AUTHORITY_ROOT = STATE_ROOT / "session-authority"
OWNER_AUTHORITY_PATH = OWNER_AUTHORITY_ROOT / "session.token.authority.json"
OWNER_REVOKE_PATH = OWNER_AUTHORITY_ROOT / "session.token.revoke-token"
OWNER_INTENT_PATH = OWNER_AUTHORITY_ROOT / \
    "session.token.owner-may-exist.v1.json"
SUPERVISOR_SOCKET = \
    "/run/hepta-tool-gateway-alpha/session-supervisor.sock"
TERMINAL_EVIDENCE_PATH = Path(
    "/run/hepta/paper-terminal-witness/alpha/terminal-evidence.v1")
SELF_IMAGE_PATH = Path("/usr/libexec/hepta-p1-paper-canary-finalizer")
HANDOFF_SCHEMA = "hepta.p1-paper-canary-execution-handoff.v1"
PRE_CLEANUP_SCHEMA = "hepta.p1-paper-canary-pre-cleanup-flat-evidence.v1"
PRE_CLEANUP_RESPONSE_BUNDLE_SCHEMA = (
    "hepta.p1-paper-canary-pre-cleanup-response-bundle.v1")
REQUEST_SCHEMA = "hepta.p1-paper-canary-root-cleanup-request.v1"
EMERGENCY_REQUEST_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-request.v1")
RECEIPT_SCHEMA = "hepta.p1-paper-canary-root-cleanup-receipt.v4"
EMERGENCY_RECEIPT_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-receipt.v1")
WAL_SCHEMA = "hepta.p1-paper-canary-root-cleanup-transaction.v4"
ERROR_SCHEMA = "hepta.p1-paper-canary-root-cleanup-error.v1"
SUCCESS_STATUS = "ROOT_CLEANUP_COMPLETE_DENY_ALL"
EMERGENCY_SUCCESS_STATUS = "ROOT_EMERGENCY_CLEANUP_COMPLETE_DENY_ALL"
OWNER_RETIREMENT_SCHEMA = (
    "hepta.p1-paper-canary-durable-owner-retirement-receipt.v4")
OWNER_RECOVERY_SCHEMA = (
    "hepta.p1-paper-canary-durable-recovery-owner-reference.v1")
BACKEND_TRANSFORM_VERSION = "hepta.p1-paper-canary-backend-transform.v1"
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
EMERGENCY_REQUIRED_ACTIONS = [
    "STOP_GUARDIAN",
    "DISABLE_EXECUTION_CONTROL",
    "ENGAGE_KILL_SWITCH",
    "ENFORCE_DENY_ALL",
    "PROVE_CONNECTOR_ZERO",
]
NORMAL_RECEIPT_VERSION = 4
OWNER_RETIREMENT_VERSION = 4
WAL_VERSION = 4
OWNER_FINALIZATION_SCHEMA = "hepta.p1-paper-canary-owner-finalization.v3"
OWNER_FINALIZATION_VERSION = 3
HSL8_TERMINAL_PROOF = "HSL8_POST_CUTOFF_SIGNED_TERMINAL_ACK_V3"
SESSIONCTL_IO_TIMEOUT_MS = 110_000
SESSIONCTL_SUBPROCESS_TIMEOUT_SECONDS = 120
SESSIONCTL_QUERY_IO_TIMEOUT_MS = 10_000
SESSIONCTL_QUERY_SUBPROCESS_TIMEOUT_SECONDS = 15
SESSIONCTL_ACK_IO_TIMEOUT_MS = 25_000
SESSIONCTL_ACK_SUBPROCESS_TIMEOUT_SECONDS = 30
ROOT_CLEANUP_TIMEOUT_MS = 240_000
ROOT_CLEANUP_CALL = {
    "call_role": "cleanup-control",
    "tool_name": "host.finalize_external_p1",
    "operation": "FINALIZE_EXTERNAL_P1",
    "effect": "CONTROL",
    "phase": "ROOT_CLEANUP",
    "socket_path": SOCKET_PATH,
    "request_schema": REQUEST_SCHEMA,
    "emergency_request_schema": EMERGENCY_REQUEST_SCHEMA,
    "response_schema": RECEIPT_SCHEMA,
    "emergency_response_schema": EMERGENCY_RECEIPT_SCHEMA,
}
ROOT_CLEANUP_DESCRIPTOR = {
    "schema": "hepta.p1-paper-canary-root-cleanup-operation-descriptor.v1",
    "version": 1,
    **ROOT_CLEANUP_CALL,
    "max_request_bytes": MAX_BYTES,
    "max_response_bytes": MAX_BYTES,
    "timeout_ms": ROOT_CLEANUP_TIMEOUT_MS,
    "paper_only": True,
    "live_authorized": False,
    "authority_granted": False,
}

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
REASON = re.compile(r"[A-Z][A-Z0-9_]{1,95}")

HANDOFF_FIELDS = frozenset({
    "schema", "version", "issued_at_ms", "expires_at_ms",
    "campaign_id", "domain_id", "policy_sha256",
    "source_baseline_sha256", "p1_audit_receipt_sha256",
    "watch_handoff_receipt_file_sha256",
    "watch_handoff_receipt_body_sha256",
    "zero_exposure_attestation_sha256",
    "admission_finalization_receipt_sha256",
    "strategy_id", "strategy_version", "strategy_sha256",
    "decision_id", "decision_sha256", "cycle_id", "intent",
    "intent_sha256", "tool_catalog_sha256", "tool_descriptor_set_sha256",
    "tool_calls", "root_cleanup_call", "installed_images",
    "installed_images_sha256", "runtime_profile_reference",
    "backend_transform_version", "execution_service_epoch",
    "execution_service_fencing_generation", "session_owner_reference",
    "paper_only", "live_authorized", "direct_broker_access",
    "authority_granted", "one_order_only", "end_flat_required",
    "body_sha256",
})
ROOT_CLEANUP_CALL_FIELDS = frozenset({
    *ROOT_CLEANUP_CALL, "tool_call_id", "command_id",
    "tool_descriptor_sha256",
})
PLANNED_CALL_FIELDS = frozenset({
    "call_role", "tool_call_id", "tool_name", "tool_descriptor_sha256",
    "effect", "phase", "command_id",
})
IMAGE_FIELDS = frozenset({
    "role", "path", "file_sha256", "mode", "uid", "gid", "nlink",
})
OWNER_FIELDS = frozenset({
    "token_name", "token_path", "authority_path", "authority_file_sha256",
    "authority_body_sha256", "lease_generation", "session_id", "peer_uid",
    "peer_gid", "token_sha256", "revoke_bearer_path",
    "revoke_bearer_sha256", "owner_account", "owner_execution_domain",
})
OWNER_FINALIZATION_FIELDS = frozenset({
    "schema", "version", "state", "recovery_id", "finalization_id",
    "expected_owner_set_sha256", "expected_owner_count",
    "owner_set_canonical_hex", "owner_token_sha256", "lease_generation",
    "query_command_id", "recovery_query_result", "finalization_result",
    "terminal_ack_result",
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

# Independent HPE1 parser/binder.  This root finalizer must not trust the
# privileged witness producer or a self-recomputed HSL8 receipt hash.
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
PRE_CLEANUP_FIELDS = frozenset({
    "schema", "version", "created_at_ms", "campaign_id", "domain_id",
    "cycle_id", "handoff_path", "handoff_file_sha256",
    "handoff_body_sha256", "intent_sha256", "installed_images_sha256",
    "executor_image_sha256", "backend_adapter_image_sha256",
    "backend_transform_version", "session_owner_reference_sha256",
    "execution_service_epoch", "execution_service_fencing_generation",
    "journal_path", "journal_sha256", "journal_size",
    "journal_last_sequence", "tool_evidence_sha256", "cycle_opened",
    "response_bundle_path", "response_bundle_file_sha256",
    "response_bundle_body_sha256",
    "cycle_closed", "place_attempted", "close_attempted", "close_outcome",
    "broker_state", "broker_state_sha256", "authority_granted",
    "body_sha256",
})
PRE_CLEANUP_RESPONSE_BUNDLE_FIELDS = frozenset({
    "schema", "version", "created_at_ms", "campaign_id", "domain_id",
    "cycle_id", "handoff_file_sha256", "handoff_body_sha256",
    "journal_path", "journal_sha256", "journal_last_sequence",
    "final_roles", "responses", "tool_evidence_sha256",
    "claimed_broker_state", "claimed_broker_state_sha256",
    "authority_granted", "body_sha256",
})
PRE_CLEANUP_BUNDLE_RESPONSE_FIELDS = frozenset({
    "call_role", "tool_call_id", "tool_name", "tool_descriptor_sha256",
    "effect", "phase", "request_sha256", "response_sha256", "status",
    "reason_code", "backend_response",
})
PRE_CLEANUP_FINAL_ROLES = (
    "final-health", "final-orders", "final-account", "final-positions",
    "cleanup-risk",
)
BACKEND_RESPONSE_FIELDS = frozenset({
    "schema", "version", "tool_call_id", "tool_name",
    "tool_catalog_sha256", "tool_descriptor_sha256", "status",
    "reason_code", "service_epoch", "fencing_generation", "command_id",
    "adapter_image_sha256", "adapter_transform_version",
    "raw_request_sha256", "raw_response_sha256",
    "normalized_payload_sha256", "payload",
})
TOOL_EVIDENCE_FIELDS = frozenset({
    "tool_call_id", "tool_name", "tool_descriptor_sha256", "effect",
    "phase", "request_sha256", "response_sha256", "status", "reason_code",
})
HEALTH_FIELDS = frozenset({
    "execution_mode", "paper_account", "connected",
    "authorized_connector_count", "complete",
})
ACCOUNT_FIELDS = frozenset({
    "account_id_sha256", "account_kind", "authoritative",
    "account_complete", "gross_absolute_position", "fx_cash_generation",
    "owner_account", "owner_execution_domain",
})
POSITIONS_FIELDS = frozenset({
    "authoritative", "complete", "snapshot_sha256", "positions",
    "gross_absolute_position", "position_generation", "fx_cash_generation",
    "owner_account", "owner_execution_domain",
})
ORDERS_FIELDS = frozenset({
    "authoritative", "complete", "snapshot_sha256", "orders",
    "connection_epoch", "generation", "owner_account",
    "owner_execution_domain",
})
ORDER_FIELDS = frozenset({
    "order_id_sha256", "instrument", "owned", "active",
})
RISK_FIELDS = frozenset({
    "paper_only", "live_authorized", "max_order_quantity",
    "max_order_notional", "max_orders_per_minute", "max_active_orders",
    "max_gross_position", "gross_absolute_position", "gross_scope",
    "connection_epoch", "orders_generation", "position_generation",
    "fx_cash_generation",
    "owner_account", "owner_execution_domain", "allowed_instruments",
    "order_types", "tifs", "complete",
})
BROKER_STATE_FIELDS = frozenset({
    "authoritative", "account_complete", "snapshot_sha256",
    "service_epoch", "fencing_generation", "active_order_id_sha256s",
    "positions", "gross_absolute_position", "authorized_connector_count",
    "end_flat",
})
POSITION_FIELDS = frozenset({"instrument", "quantity"})
REQUEST_FIELDS = frozenset({
    "schema", "version", "issued_at_ms", "expires_at_ms", "campaign_id",
    "domain_id", "cycle_id", "cleanup_tool_call_id", "cleanup_command_id",
    "tool_descriptor_sha256", "handoff_file_sha256",
    "handoff_body_sha256", "session_owner_reference_sha256",
    "execution_service_epoch", "execution_service_fencing_generation",
    "pre_cleanup_evidence_path", "pre_cleanup_evidence_file_sha256",
    "pre_cleanup_evidence_body_sha256", "required_actions", "paper_only",
    "live_authorized", "authority_granted", "body_sha256",
})
JOURNAL_HEADER_FIELDS = frozenset({
    "schema", "version", "record_type", "sequence", "created_at_ms",
    "handoff_file_sha256", "handoff_body_sha256",
    "session_owner_reference_sha256", "authority_granted",
})
JOURNAL_CALL_FIELDS = frozenset({
    "schema", "version", "record_type", "sequence", "recorded_at_ms",
    "call_role", "phase", "event", "tool_call_id", "tool_name",
    "command_id", "request_sha256", "response_sha256", "status",
    "reason_code", "service_epoch", "fencing_generation",
    "adapter_image_sha256", "adapter_transform_version",
    "raw_request_sha256", "raw_response_sha256",
    "normalized_payload_sha256",
})
RECEIPT_FIELDS = frozenset({
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
    "durable_owner_reference_sha256",
    "durable_owner_count", "durable_owner_status",
    "durable_owner_retirement_receipt_path",
    "durable_owner_retirement_receipt_file_sha256",
    "durable_owner_retirement_receipt_body_sha256",
    "mutation_credentials_destroyed", "credentials_destroyed_scope",
    "retained_root_recovery_bearer_count",
    "retained_root_recovery_bearer_path",
    "retained_root_recovery_bearer_sha256",
    "retained_root_recovery_bearer_mutation_authority",
    "authorized_connector_count",
    "identity_count", "identity_manifest_sha256", "broker_policy_sha256",
    "paper_only", "live_authorized", "authority_granted", "body_sha256",
})
EMERGENCY_EVIDENCE_FIELDS = frozenset({
    "schema", "version", "created_at_ms", "campaign_id", "domain_id",
    "cycle_id", "handoff_path", "handoff_file_sha256",
    "handoff_body_sha256", "intent_sha256", "installed_images_sha256",
    "executor_image_sha256", "backend_adapter_image_sha256",
    "root_finalizer_image_sha256", "backend_transform_version",
    "session_owner_reference_sha256", "execution_service_epoch",
    "execution_service_fencing_generation", "journal_path",
    "journal_sha256", "journal_size", "journal_last_sequence",
    "tool_evidence_sha256", "recovery_reason_codes", "last_known_state",
    "last_known_state_sha256", "cycle_opened", "cycle_closed",
    "place_attempted", "close_attempted", "close_outcome",
    "uncertainty_kind", "last_completed_phase", "uncertain_phase",
    "uncertain_tool_call_id", "broker_flat_proven", "authority_granted",
    "body_sha256",
})
EMERGENCY_REQUEST_FIELDS = frozenset({
    "schema", "version", "issued_at_ms", "expires_at_ms", "campaign_id",
    "domain_id", "cycle_id", "cleanup_tool_call_id", "cleanup_command_id",
    "tool_descriptor_sha256", "handoff_file_sha256", "handoff_body_sha256",
    "session_owner_reference_sha256", "execution_service_epoch",
    "execution_service_fencing_generation", "emergency_evidence_path",
    "emergency_evidence_file_sha256", "emergency_evidence_body_sha256",
    "recovery_reason_codes", "required_actions", "broker_flat_proven",
    "paper_only", "live_authorized", "authority_granted", "body_sha256",
})
EMERGENCY_RECEIPT_FIELDS = frozenset(
    (RECEIPT_FIELDS - {
        "pre_cleanup_evidence_path", "pre_cleanup_evidence_file_sha256",
        "pre_cleanup_evidence_body_sha256", "root_cleanup_request_path",
        "root_cleanup_request_file_sha256", "root_cleanup_request_body_sha256",
        "durable_owner_retirement_receipt_path",
        "durable_owner_retirement_receipt_file_sha256",
        "durable_owner_retirement_receipt_body_sha256",
        "mutation_credentials_destroyed", "credentials_destroyed_scope",
        "retained_root_recovery_bearer_count",
        "retained_root_recovery_bearer_path",
        "retained_root_recovery_bearer_sha256",
        "retained_root_recovery_bearer_mutation_authority",
    }) | {
        "emergency_evidence_path", "emergency_evidence_file_sha256",
        "emergency_evidence_body_sha256",
        "root_emergency_cleanup_request_path",
        "root_emergency_cleanup_request_file_sha256",
        "root_emergency_cleanup_request_body_sha256",
        "recovery_reason_codes", "broker_flat_proven", "recovery_required",
        "evidence_retained", "durable_recovery_owner_reference_path",
        "durable_recovery_owner_reference_file_sha256",
        "durable_recovery_owner_reference_body_sha256",
    }
)
WAL_FIELDS = frozenset({
    "schema", "version", "transaction_id", "phase", "created_at_ms",
    "updated_at_ms", "request_path", "request_file_sha256",
    "request_body_sha256", "pre_cleanup_evidence_path",
    "pre_cleanup_evidence_file_sha256", "pre_cleanup_evidence_body_sha256",
    "execution_handoff_path", "execution_handoff_file_sha256",
    "execution_handoff_body_sha256", "guardian_request_id",
    "local_control_transaction_id", "local_control_request_sha256",
    "guardian_active_receipt_file_sha256",
    "guardian_active_receipt_body_sha256", "transaction_kind",
    "owner_finalization", "owner_transition", "completion", "body_sha256",
})
OWNER_TRANSITION_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "durable_owner_count",
    "durable_owner_status", "document",
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
    "pre_cleanup_flat_evidence_role",
    "authority_path", "authority_file_sha256",
    "authority_body_sha256", "revoke_bearer_path",
    "revoke_bearer_file_sha256", "credentials_destroyed",
    "mutation_credentials_destroyed", "credentials_destroyed_scope",
    "retained_root_recovery_bearer_count",
    "retained_root_recovery_bearer_path",
    "retained_root_recovery_bearer_sha256",
    "retained_root_recovery_bearer_mutation_authority",
    "runtime_session_count", "durable_owner_count", "durable_owner_status",
    "paper_only", "live_authorized", "authority_granted", "body_sha256",
})
OWNER_RECOVERY_FIELDS = frozenset({
    "schema", "version", "completed_at_ms", "campaign_id", "domain_id",
    "cycle_id", "cleanup_command_id", "session_owner_reference_sha256",
    "token_sha256", "lease_generation", "session_id", "query_reason_code",
    "command_status", "command_reason_code", "order_id",
    "authoritative_command_status", "recovery_only",
    "paper_finalization_required", "owner_fenced",
    "owner_audit_authoritative", "owner_audit_complete",
    "execution_service_epoch", "execution_service_fencing_generation",
    "recovery_expires_at_ms", "owner_active_order_count",
    "owner_uncertain_command_count", "broker_connection_epoch",
    "broker_active_generation", "broker_terminal_generation",
    "owner_account", "owner_execution_domain", "runtime_session_count",
    "durable_owner_count", "durable_owner_status", "paper_only",
    "live_authorized", "authority_granted", "body_sha256",
})
OUTER_OWNER_PURGE_FIELDS = frozenset({
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
OUTER_OWNER_PURGE_INTENT_FIELDS = frozenset({
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


class FinalizerError(RuntimeError):
    def __init__(self, reason: str):
        if REASON.fullmatch(reason) is None:
            reason = "ROOT_FINALIZER_INTERNAL_ERROR"
        super().__init__(reason)
        self.reason = reason


def _fail(reason: str) -> None:
    raise FinalizerError(reason)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject(_value: str) -> None:
    raise ValueError("non-integral number")


def canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail("ROOT_FINALIZER_CANONICAL_JSON_INVALID")


def strict_json(raw: bytes, reason: str) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        _fail(reason)
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"), object_pairs_hook=_unique,
            parse_float=_reject, parse_constant=_reject)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail(reason)
    if not isinstance(value, dict) or canonical_json(value) != raw:
        _fail(reason)
    return value


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical_sha(value: Any) -> str:
    return sha(canonical_json(value))


def sealed(document: dict[str, Any], reason: str) -> str:
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    computed = canonical_sha(body)
    if claimed != computed:
        _fail(reason)
    return computed


def _exact(value: Any, fields: frozenset[str], reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        _fail(reason)
    return value


def _digest(value: Any, reason: str) -> str:
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        _fail(reason)
    return value


def _identifier(value: Any, reason: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        _fail(reason)
    return value


def _integer(value: Any, reason: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(reason)
    return value


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns)


def stable_read(path: Path, *, uid: int, gid: int, mode: int,
                maximum: int = MAX_BYTES) -> bytes:
    try:
        before = os.lstat(path)
        if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
                before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != mode or
                before.st_uid != uid or before.st_gid != gid or
                before.st_size < 1 or before.st_size > maximum):
            _fail("ROOT_FINALIZER_ARTIFACT_METADATA_INVALID")
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
    except FinalizerError:
        raise
    except OSError:
        _fail("ROOT_FINALIZER_ARTIFACT_UNAVAILABLE")
    if (len(payload) > maximum or _identity(before) != _identity(opened) or
            _identity(opened) != _identity(after)):
        _fail("ROOT_FINALIZER_ARTIFACT_CHANGED")
    return bytes(payload)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, raw: bytes, *, uid: int = 0, gid: int = 0,
                 mode: int = 0o600, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode)
    try:
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, uid, gid)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                _fail("ROOT_FINALIZER_WRITE_FAILED")
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
        os.chown(path, uid, gid, follow_symlinks=False)
        os.chmod(path, mode, follow_symlinks=False)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


@dataclass(frozen=True)
class Config:
    artifact_root: Path = ARTIFACT_ROOT
    control_root: Path = CONTROL_ROOT
    state_root: Path = STATE_ROOT
    wal_path: Path = WAL_PATH
    lock_path: Path = LOCK_PATH
    self_image_path: Path = SELF_IMAGE_PATH
    self_uid: int = 0
    self_gid: int = 0


@dataclass(frozen=True)
class Validated:
    request: dict[str, Any]
    request_raw: bytes
    request_path: Path
    request_body_sha256: str
    evidence: dict[str, Any]
    evidence_raw: bytes
    evidence_path: Path
    evidence_body_sha256: str
    handoff: dict[str, Any]
    handoff_raw: bytes
    handoff_path: Path
    handoff_body_sha256: str
    journal_raw: bytes
    images: dict[str, dict[str, Any]]
    response_bundle: dict[str, Any]
    response_bundle_raw: bytes
    response_bundle_body_sha256: str


class Control(Protocol):
    def capture_authority(self, handoff: Mapping[str, Any]) -> dict[str, str]: ...
    def stop_guardian(self) -> None: ...
    def advance_owner_finalization(
            self, handoff: Mapping[str, Any],
            finalization: Mapping[str, Any]) -> dict[str, Any]: ...
    def verify_owner_terminal_ack(
            self, handoff: Mapping[str, Any],
            finalization: Mapping[str, Any]) -> dict[str, Any]: ...
    def owner_terminal_bearer_present(
            self, handoff: Mapping[str, Any]) -> bool: ...
    def prepare_owner_transition(
            self, handoff: Mapping[str, Any], *, command_id: str,
            emergency: bool, artifact_directory: Path,
            completed_at_ms: int,
            finalization: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...
    def commit_owner_transition(
            self, handoff: Mapping[str, Any], transition: Mapping[str, Any],
            *, emergency: bool) -> None: ...
    def fail_close(self) -> None: ...
    def prove_deny(self, handoff: Mapping[str, Any]) -> dict[str, Any]: ...


def _artifact_cycle_dir(config: Config, campaign: str, cycle: str) -> Path:
    return config.artifact_root / campaign / cycle


def _control_cycle_dir(config: Config, campaign: str, cycle: str) -> Path:
    return config.control_root / campaign / cycle


def _validate_owner_reference(value: Any) -> dict[str, Any]:
    owner = _exact(value, OWNER_FIELDS,
                   "ROOT_FINALIZER_SESSION_OWNER_INVALID")
    if (owner["peer_uid"] != PEER_UID or owner["peer_gid"] != PEER_GID or
            owner["token_name"] != "session.token" or
            owner["token_path"] != str(OWNER_TOKEN_PATH) or
            owner["authority_path"] != str(OWNER_AUTHORITY_PATH) or
            owner["revoke_bearer_path"] != str(OWNER_REVOKE_PATH) or
            owner["revoke_bearer_sha256"] != owner["token_sha256"] or
            not isinstance(owner["owner_account"], str) or
            re.fullmatch(r"DU[0-9]{1,16}", owner["owner_account"]) is None or
            owner["owner_execution_domain"] != "PAPER:alpha" or
            type(owner["lease_generation"]) is not int or
            owner["lease_generation"] < 1):
        _fail("ROOT_FINALIZER_SESSION_OWNER_INVALID")
    _identifier(owner["session_id"], "ROOT_FINALIZER_SESSION_OWNER_INVALID")
    for field in ("authority_file_sha256", "authority_body_sha256",
                  "token_sha256", "revoke_bearer_sha256"):
        _digest(owner[field], "ROOT_FINALIZER_SESSION_OWNER_INVALID")
    return owner


def _owner_finalization_intent(
        handoff: Mapping[str, Any], command_id: str,
) -> dict[str, Any]:
    owner = _validate_owner_reference(handoff["session_owner_reference"])
    _identifier(command_id, "ROOT_FINALIZER_OWNER_FINALIZATION_INVALID")
    canonical = (
        f"{owner['token_sha256']}\t{owner['lease_generation']}\t"
        f"{owner['owner_account'].encode('utf-8').hex()}\t"
        f"{owner['owner_execution_domain'].encode('utf-8').hex()}\n"
    ).encode("ascii")
    owner_set_sha256 = sha(canonical)
    recovery_seed = canonical_json({
        "campaign_id": handoff["campaign_id"],
        "cycle_id": handoff["cycle_id"],
        "query_command_id": command_id,
        "session_owner_reference_sha256": canonical_sha(owner),
        "expected_owner_set_sha256": owner_set_sha256,
    })
    recovery_id = "root-finalization-" + hashlib.sha256(
        recovery_seed).hexdigest()[:32]
    finalization_seed = (
        recovery_id + "\n" + owner_set_sha256 + "\n1\n").encode("ascii")
    return {
        "schema": OWNER_FINALIZATION_SCHEMA,
        "version": OWNER_FINALIZATION_VERSION,
        "state": "INTENT",
        "recovery_id": recovery_id,
        "finalization_id": "paper-finalization-" + hashlib.sha256(
            finalization_seed).hexdigest()[:32],
        "expected_owner_set_sha256": owner_set_sha256,
        "expected_owner_count": 1,
        "owner_set_canonical_hex": canonical.hex(),
        "owner_token_sha256": owner["token_sha256"],
        "lease_generation": owner["lease_generation"],
        "query_command_id": command_id,
        "recovery_query_result": None,
        "finalization_result": None,
        "terminal_ack_result": None,
    }


def _parse_ordered_receipt(
        value: Any, keys: tuple[str, ...], failure: str, *,
        maximum: int = 4096,
) -> tuple[dict[str, str], bytes]:
    if not isinstance(value, str):
        _fail(failure)
    try:
        raw = value.encode("ascii")
    except UnicodeEncodeError:
        _fail(failure)
    if (not 1 <= len(raw) <= maximum or not raw.endswith(b"\n") or
            b"\r" in raw or b"\x00" in raw):
        _fail(failure)
    rows = raw[:-1].split(b"\n")
    if len(rows) != len(keys):
        _fail(failure)
    receipt: dict[str, str] = {}
    for row, expected_key in zip(
            rows, keys, strict=True):
        key, separator, raw_value = row.partition(b"=")
        try:
            decoded_key = key.decode("ascii")
            decoded_value = raw_value.decode("ascii")
        except UnicodeDecodeError:
            _fail(failure)
        if separator != b"=" or decoded_key != expected_key:
            _fail(failure)
        receipt[decoded_key] = decoded_value
    if tuple(receipt) != keys:
        _fail(failure)
    return receipt, raw


def _parse_finalization_receipt(value: Any) -> tuple[dict[str, str], bytes]:
    return _parse_ordered_receipt(
        value, FINALIZATION_RECEIPT_KEYS,
        "ROOT_FINALIZER_FINALIZATION_RECEIPT_INVALID")


def _parse_terminal_ack_receipt(value: Any) -> tuple[dict[str, str], bytes]:
    return _parse_ordered_receipt(
        value, TERMINAL_ACK_RECEIPT_KEYS,
        "ROOT_FINALIZER_TERMINAL_ACK_RECEIPT_INVALID", maximum=12288)


def _parse_terminal_evidence(
        evidence: Any,
) -> tuple[dict[str, str], bytes, bytes]:
    """Parse the independent HPE1 stable terminal witness."""
    failure = "ROOT_FINALIZER_OWNER_TERMINAL_ACK_INVALID"
    if not isinstance(evidence, (bytes, bytearray, memoryview)):
        _fail(failure)
    raw = bytes(evidence)
    if not 1 <= len(raw) <= 12288:
        _fail(failure)
    try:
        raw.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        _fail(failure)
    rows = raw.splitlines(keepends=True)
    if (len(rows) != len(TERMINAL_EVIDENCE_KEYS) + 1 or
            rows[0] != b"HPE1\n" or
            any(not row.endswith(b"\n") or b"\r" in row for row in rows)):
        _fail(failure)
    values: dict[str, str] = {}
    for row, expected_key in zip(
            rows[1:], TERMINAL_EVIDENCE_KEYS, strict=True):
        prefix = (expected_key + "=").encode("ascii")
        if (not row.startswith(prefix) or row == prefix + b"\n" or
                b"=" in row[len(prefix):-1] or expected_key in values):
            _fail(failure)
        try:
            decoded = row[len(prefix):-1].decode("ascii", errors="strict")
        except UnicodeDecodeError:
            _fail(failure)
        if not decoded:
            _fail(failure)
        values[expected_key] = decoded
    if tuple(values) != TERMINAL_EVIDENCE_KEYS:
        _fail(failure)
    prefix = b"".join(rows[:-1])
    body = values["evidence_body_sha256"]
    if DIGEST.fullmatch(body) is None or body != sha(prefix):
        _fail(failure)
    return values, raw, prefix


def _current_terminal_evidence() -> bytes:
    """Read and syntactically validate the current root-owned HPE1 file."""
    raw = stable_read(
        TERMINAL_EVIDENCE_PATH, uid=0, gid=0, mode=0o400, maximum=12288)
    _parse_terminal_evidence(raw)
    return raw


def _owner_finalization_terminal_evidence(
        finalization: Mapping[str, Any] | None,
) -> bytes | None:
    """Load HPE1 whenever a persisted owner finalization is already ACKED."""
    if isinstance(finalization, Mapping) and finalization.get("state") == "ACKED":
        return _current_terminal_evidence()
    return None


def _validate_terminal_evidence_binding(
        evidence_raw: bytes, *, receipt: dict[str, str],
        result: Mapping[str, Any], checkpoint: Mapping[str, Any],
) -> None:
    """Cross-bind all repeated HSL8 provenance fields to HPE1 bytes."""
    failure = "ROOT_FINALIZER_OWNER_TERMINAL_ACK_INVALID"
    evidence, exact, _prefix = _parse_terminal_evidence(evidence_raw)
    file_digest = sha(exact)
    body_digest = evidence["evidence_body_sha256"]
    if (result.get("terminal_evidence_sha256") != file_digest or
            receipt.get("terminal_evidence_file_sha256") != file_digest or
            result.get("terminal_evidence_body_sha256") != body_digest or
            receipt.get("terminal_evidence_body_sha256") != body_digest):
        _fail(failure)
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
        _fail(failure)
    for field in TERMINAL_ACK_RECEIPT_KEYS:
        if field in {
                "schema", "version", "status",
                "terminal_evidence_file_sha256",
                "terminal_evidence_body_sha256"}:
            continue
        if field in TERMINAL_EVIDENCE_KEYS and receipt.get(field) != evidence.get(field):
            _fail(failure)
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
        _fail(failure)


def _receipt_unsigned(value: Any, failure: str) -> int:
    if (not isinstance(value, str) or
            re.fullmatch(r"0|[1-9][0-9]*", value) is None):
        _fail(failure)
    try:
        return int(value)
    except ValueError:
        _fail(failure)


def _validate_terminal_ack_receipt(
        receipt_text: Any, receipt_sha256: Any,
        handoff: Mapping[str, Any], finalization: Mapping[str, Any],
        preliminary: Mapping[str, Any],
) -> dict[str, str]:
    failure = "ROOT_FINALIZER_TERMINAL_ACK_RECEIPT_INVALID"
    receipt, raw = _parse_terminal_ack_receipt(receipt_text)
    owner = handoff["session_owner_reference"]
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
    if (not isinstance(receipt_sha256, str) or
            DIGEST.fullmatch(receipt_sha256) is None or
            sha(raw) != receipt_sha256 or
            receipt["schema"] !=
                "hepta.paper-session-terminal-ack-receipt.v3" or
            receipt["version"] != "3" or
            receipt["status"] != "TERMINAL_ACKED" or
            receipt["terminal_proof_kind"] !=
                "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" or
            receipt["recovery_id"] != finalization["recovery_id"] or
            receipt["finalization_id"] != finalization["finalization_id"] or
            receipt["campaign_id"] != handoff["campaign_id"] or
            receipt["cycle_id"] != handoff["cycle_id"] or
            receipt["expected_owner_set_sha256"] !=
                finalization["expected_owner_set_sha256"] or
            receipt["expected_owner_count"] != "1" or
            receipt["owner_set_canonical_hex"] !=
                finalization["owner_set_canonical_hex"] or
            receipt["preliminary_finalization_receipt_sha256"] !=
                preliminary["finalization_receipt_sha256"] or
            receipt["owner_agent_id"] != PEER_USER or
            receipt["owner_session_id"] != owner["session_id"] or
            receipt["owner_account"] != owner["owner_account"] or
            receipt["owner_execution_domain"] !=
                owner["owner_execution_domain"] or
            receipt["account_id_sha256"] != sha(
                owner["owner_account"].encode("ascii")) or
            receipt["execution_service_epoch"] !=
                handoff["execution_service_epoch"] or
            receipt["execution_service_fencing_generation"] != str(
                handoff["execution_service_fencing_generation"]) or
            receipt["recovery_ingress_fence"] != str(
                owner["lease_generation"]) or
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
        _fail(failure)
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
        _fail(failure)
    return receipt


def _validate_recovery_query_result(
        result: Any, handoff: Mapping[str, Any],
        finalization: Mapping[str, Any],
) -> dict[str, Any]:
    failure = "ROOT_FINALIZER_OWNER_RECOVERY_INVALID"
    value = _exact(result, RECOVERY_QUERY_RESULT_FIELDS, failure)
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
    if (any(type(value.get(field)) is not bool for field in boolean_fields) or
            any(type(value.get(field)) is not int for field in integer_fields) or
            any(not isinstance(value.get(field), str)
                for field in set(value) - boolean_fields - integer_fields)):
        _fail(failure)
    owner = handoff["session_owner_reference"]
    if (
            value["accepted"] is not True or
            value["reason_code"] !=
                "RECOVERY_QUERY_NOT_FOUND_PROVEN_RECOVERY_ONLY" or
            value["lease_generation"] != finalization["lease_generation"] or
            value["authoritative_command_status"] is not True or
            value["command_id"] != finalization["query_command_id"] or
            value["command_status"] != "not_found" or
            value["command_reason_code"] != "EXECUTION_COMMAND_NOT_FOUND" or
            value["order_id"] != -1 or value["recovery_only"] is not True or
            value["paper_finalization_required"] is not True or
            value["owner_fenced"] is not False or
            value["owner_audit_authoritative"] is not True or
            value["owner_audit_complete"] is not True or
            value["owner_active_order_count"] != 0 or
            value["owner_uncertain_command_count"] != 0 or
            value["execution_service_epoch"] !=
                handoff["execution_service_epoch"] or
            value["execution_service_fencing_generation"] !=
                handoff["execution_service_fencing_generation"] or
            value["recovery_expires_at_ms"] < 1 or
            value["broker_connection_epoch"] < 1 or
            value["broker_active_generation"] < 1 or
            value["broker_terminal_generation"] < 1 or
            value["owner_account"] != owner["owner_account"] or
            value["owner_execution_domain"] !=
                owner["owner_execution_domain"]):
        _fail(failure)
    return value


def _validate_finalization_result(
        result: Any, handoff: Mapping[str, Any],
        finalization: Mapping[str, Any], *, expected_state: str,
) -> dict[str, Any]:
    failure = "ROOT_FINALIZER_OWNER_FINALIZATION_INVALID"
    value = _exact(result, FINALIZATION_RESULT_FIELDS, failure)
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
    if (any(type(value.get(field)) is not bool for field in boolean_fields) or
            any(type(value.get(field)) is not int for field in integer_fields) or
            any(not isinstance(value.get(field), str)
                for field in set(value) - boolean_fields - integer_fields)):
        _fail(failure)
    if (
            expected_state != "AUDIT_SEALED" or
            value["accepted"] is not True or
            value["reason_code"] != "PAPER_FINALIZATION_AUDIT_SEALED" or
            value["paper_finalization_state"] != "AUDIT_SEALED" or
            value["paper_finalization_required"] is not True or
            value["recovery_id"] != finalization["recovery_id"] or
            value["finalization_id"] != finalization["finalization_id"] or
            value["expected_owner_set_sha256"] !=
                finalization["expected_owner_set_sha256"] or
            value["expected_owner_count"] != 1 or
            value["owner_token_sha256"] !=
                finalization["owner_token_sha256"] or
            value["lease_generation"] != finalization["lease_generation"]):
        _fail(failure)
    receipt, receipt_raw = _parse_finalization_receipt(
        value["finalization_receipt"])
    if (
            not isinstance(value["finalization_receipt_sha256"], str) or
            DIGEST.fullmatch(value["finalization_receipt_sha256"]) is None or
            sha(receipt_raw) != value["finalization_receipt_sha256"] or
            receipt["schema"] !=
                "hepta.paper-session-finalization-receipt.v1" or
            receipt["version"] != "1" or
            receipt["status"] != "AUDIT_SEALED" or
            receipt["recovery_id"] != finalization["recovery_id"] or
            receipt["finalization_id"] != finalization["finalization_id"] or
            receipt["expected_owner_set_sha256"] !=
                finalization["expected_owner_set_sha256"] or
            receipt["expected_owner_count"] != "1" or
            receipt["owner_set_canonical_hex"] !=
                finalization["owner_set_canonical_hex"] or
            receipt["owner_account"] !=
                handoff["session_owner_reference"]["owner_account"] or
            receipt["owner_execution_domain"] !=
                handoff["session_owner_reference"][
                    "owner_execution_domain"] or
            receipt["paper_only"] != "1" or
            receipt["live_authorized"] != "0"):
        _fail(failure)
    paired_integers = integer_fields - {
        "lease_generation", "expected_owner_count"}
    if any(receipt.get(field) != str(value[field])
           for field in paired_integers):
        _fail(failure)
    for field in (
            "owner_account", "owner_execution_domain",
            "execution_service_epoch", "broker_position_quantity",
            "broker_gross_absolute_position"):
        if receipt.get(field) != value[field]:
            _fail(failure)
    positive = {
        "execution_service_fencing_generation", "broker_connection_epoch",
        "broker_active_generation", "broker_terminal_generation",
        "broker_risk_generation", "broker_account_generation",
        "broker_position_generation", "broker_fx_cash_generation",
    }
    recovery = finalization.get("recovery_query_result")
    if (
            not isinstance(recovery, dict) or
            value["execution_service_epoch"] !=
                recovery.get("execution_service_epoch") or
            value["execution_service_fencing_generation"] !=
                recovery.get("execution_service_fencing_generation") or
            any(value[field] < 1 for field in positive) or
            value["broker_exposure_generation"] < 0 or
            value["broker_terminal_exposure_generation"] < 0 or
            value["broker_risk_absorbed_exposure_generation"] < 0 or
            value["broker_terminal_exposure_generation"] >
                value["broker_risk_absorbed_exposure_generation"] or
            value["broker_risk_absorbed_exposure_generation"] !=
                value["broker_exposure_generation"] or
            any(value[field] != 0 for field in (
                "owner_active_order_count", "owner_uncertain_command_count",
                "broker_global_active_order_count")) or
            value["owner_audit_authoritative"] is not True or
            value["owner_audit_complete"] is not True or
            value["broker_post_fill_risk_reconciliation_pending"] is not
                False or
            value["broker_recovery_audit_barrier_complete"] is not True or
            value["broker_recovery_audit_new_connection_epoch_required"] is
                not False or
            value["broker_position_quantity"] != "0" or
            value["broker_gross_absolute_position"] != "0" or
            receipt["broker_post_fill_risk_reconciliation_pending"] != "0" or
            receipt["broker_recovery_audit_barrier_complete"] != "1" or
            receipt[
                "broker_recovery_audit_new_connection_epoch_required"] !=
                "0"):
        _fail(failure)
    return value


def _validate_terminal_ack_result(
        result: Any, handoff: Mapping[str, Any],
        finalization: Mapping[str, Any], preliminary: Mapping[str, Any], *,
        require_replay: bool, terminal_evidence_raw: bytes | None = None,
) -> dict[str, Any]:
    failure = "ROOT_FINALIZER_OWNER_TERMINAL_ACK_INVALID"
    value = _exact(result, TERMINAL_ACK_RESULT_FIELDS, failure)
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
    if (type(require_replay) is not bool or
            any(type(value.get(field)) is not bool for field in boolean_fields) or
            any(type(value.get(field)) is not int for field in integer_fields) or
            any(not isinstance(value.get(field), str)
                for field in set(value) - boolean_fields - integer_fields)):
        _fail(failure)
    owner = handoff["session_owner_reference"]
    if (
            value["accepted"] is not True or
            value["reason_code"] != "PAPER_FINALIZATION_TERMINAL_ACKED" or
            value["paper_finalization_state"] != "ACKED" or
            value["paper_finalization_required"] is not True or
            value["recovery_id"] != finalization["recovery_id"] or
            value["finalization_id"] != finalization["finalization_id"] or
            value["expected_owner_set_sha256"] !=
                finalization["expected_owner_set_sha256"] or
            value["expected_owner_count"] != 1 or
            value["owner_token_sha256"] != finalization["owner_token_sha256"] or
            value["lease_generation"] != finalization["lease_generation"] or
            value["preliminary_finalization_receipt_sha256"] !=
                preliminary["finalization_receipt_sha256"] or
            value["finalization_receipt_sha256"] ==
                preliminary["finalization_receipt_sha256"] or
            value["owner_account"] != owner["owner_account"] or
            value["owner_execution_domain"] != owner["owner_execution_domain"] or
            value["terminalization_service_epoch"] !=
                handoff["execution_service_epoch"] or
            value["terminalization_service_fencing_generation"] !=
                handoff["execution_service_fencing_generation"] or
            value["terminalization_generation"] != 1 or
            value["execution_service_epoch"] !=
                handoff["execution_service_epoch"] or
            value["execution_service_fencing_generation"] !=
                handoff["execution_service_fencing_generation"] or
            not isinstance(value["execution_service_epoch"], str) or
            IDENTIFIER.fullmatch(value["execution_service_epoch"]) is None or
            value["owner_audit_authoritative"] is not True or
            value["owner_audit_complete"] is not True or
            value["execution_mutation_gate_closed"] is not True or
            value["broker_transport_connected"] is not False or
            value["broker_event_ingress_halted"] is not True or
            value["broker_callback_queue_drained"] is not False or
            value["broker_callbacks_in_flight"] != 0 or
            value["broker_reconnect_permitted"] is not False or
            value["terminal_latch_durable"] is not True or
            value["terminal_runtime_latch_loaded"] is not False or
            value["terminal_runtime_verified"] is not False or
            value["terminal_external_latch_loaded"] is not True or
            value["terminal_current_evidence_verified"] is not True or
            value["terminal_proof_kind"] !=
                "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" or
            any(value[field] != 0 for field in (
                "broker_connection_epoch", "broker_active_generation",
                "broker_terminal_generation", "broker_risk_generation",
                "broker_account_generation", "broker_position_generation",
                "broker_fx_cash_generation", "broker_exposure_generation",
                "broker_terminal_exposure_generation",
                "broker_risk_absorbed_exposure_generation",
                "broker_global_active_order_count", "owner_active_order_count",
                "owner_uncertain_command_count")) or
            value["broker_post_fill_risk_reconciliation_pending"] is not
                False or
            value["broker_recovery_audit_barrier_complete"] is not False or
            value["broker_recovery_audit_new_connection_epoch_required"] is
                not False or
            value["broker_position_quantity"] != "0" or
            value["broker_gross_absolute_position"] != "0" or
            value["egress_publisher_pid"] < 1 or
            value["egress_publisher_start_ticks"] < 1 or
            (require_replay and value["terminal_replay"] is not True)):
        _fail(failure)
    receipt = _validate_terminal_ack_receipt(
        value["finalization_receipt"], value["finalization_receipt_sha256"],
        handoff, finalization, preliminary)
    if (
            value["terminalization_generation"] != _receipt_unsigned(
                receipt["terminalization_generation"], failure) or
            value["terminal_latch_sha256"] !=
                receipt["terminalizing_latch_sha256"] or
            value["terminal_external_halt_latch_sha256"] !=
                receipt["terminal_external_halt_latch_sha256"] or
            value["transport_cutoff_receipt_file_sha256"] !=
                receipt["transport_cutoff_receipt_file_sha256"] or
            value["transport_cutoff_receipt_body_sha256"] !=
                receipt["transport_cutoff_receipt_body_sha256"] or
            value["post_cutoff_terminal_witness_file_sha256"] !=
                receipt["post_cutoff_terminal_witness_file_sha256"] or
            value["post_cutoff_terminal_witness_body_sha256"] !=
                receipt["post_cutoff_terminal_witness_body_sha256"] or
            value["terminal_evidence_sha256"] !=
                receipt["terminal_evidence_file_sha256"] or
            value["terminal_evidence_body_sha256"] !=
                receipt["terminal_evidence_body_sha256"] or
            value["egress_policy_sha256"] !=
                receipt["egress_policy_sha256"] or
            str(value["egress_publisher_pid"]) !=
                receipt["egress_publisher_pid"] or
            str(value["egress_publisher_start_ticks"]) !=
                receipt["egress_publisher_start_ticks"] or
            value["provider_trust_policy_body_sha256"] !=
                receipt["provider_trust_policy_body_sha256"] or
            value["signed_account_signature_sha256"] !=
                receipt["signed_account_signature_sha256"]):
        _fail(failure)
    if terminal_evidence_raw is None:
        _fail(failure)
    _validate_terminal_evidence_binding(
        terminal_evidence_raw, receipt=receipt, result=value,
        checkpoint={
            "recovery_id": finalization["recovery_id"],
            "finalization_id": finalization["finalization_id"],
            "campaign_id": handoff["campaign_id"],
            "cycle_id": handoff["cycle_id"],
        })
    return value


def _validate_owner_finalization(
        value: Any, handoff: Mapping[str, Any], command_id: str,
        *, terminal_evidence_raw: bytes | None = None,
) -> dict[str, Any]:
    failure = "ROOT_FINALIZER_OWNER_FINALIZATION_INVALID"
    finalization = _exact(value, OWNER_FINALIZATION_FIELDS, failure)
    expected = _owner_finalization_intent(handoff, command_id)
    immutable = OWNER_FINALIZATION_FIELDS - {
        "state", "recovery_query_result", "finalization_result",
        "terminal_ack_result"}
    if any(finalization.get(field) != expected[field] for field in immutable):
        _fail(failure)
    state = finalization.get("state")
    query = finalization.get("recovery_query_result")
    sealed_result = finalization.get("finalization_result")
    ack = finalization.get("terminal_ack_result")
    if state == "INTENT":
        if any(item is not None for item in (query, sealed_result, ack)):
            _fail(failure)
    elif state == "RECOVERY_ONLY":
        _validate_recovery_query_result(query, handoff, finalization)
        if sealed_result is not None or ack is not None:
            _fail(failure)
    elif state == "AUDIT_SEALED":
        _validate_recovery_query_result(query, handoff, finalization)
        _validate_finalization_result(
            sealed_result, handoff, finalization,
            expected_state="AUDIT_SEALED")
        if ack is not None:
            _fail(failure)
    elif state == "ACKED":
        _validate_recovery_query_result(query, handoff, finalization)
        sealed = _validate_finalization_result(
            sealed_result, handoff, finalization,
            expected_state="AUDIT_SEALED")
        if terminal_evidence_raw is None:
            _fail(failure)
        _validate_terminal_ack_result(
            ack, handoff, finalization, sealed, require_replay=False,
            terminal_evidence_raw=terminal_evidence_raw)
    else:
        _fail(failure)
    return finalization


def _retirement_finalization(
        document: Mapping[str, Any], handoff: Mapping[str, Any],
) -> dict[str, Any]:
    value = {
        "schema": OWNER_FINALIZATION_SCHEMA,
        "version": OWNER_FINALIZATION_VERSION, "state": "ACKED",
        "recovery_id": document.get("recovery_id"),
        "finalization_id": document.get("finalization_id"),
        "expected_owner_set_sha256": document.get(
            "expected_owner_set_sha256"),
        "expected_owner_count": document.get("expected_owner_count"),
        "owner_set_canonical_hex": document.get("owner_set_canonical_hex"),
        "owner_token_sha256": document.get("owner_token_sha256"),
        "lease_generation": document.get("lease_generation"),
        "query_command_id": document.get("query_command_id"),
        "recovery_query_result": document.get("recovery_query_result"),
        "finalization_result": document.get("finalization_result"),
        "terminal_ack_result": document.get("terminal_ack_result"),
    }
    command_id = document.get("cleanup_command_id")
    if not isinstance(command_id, str):
        _fail("ROOT_FINALIZER_OWNER_FINALIZATION_INVALID")
    finalized = _validate_owner_finalization(
        value, handoff, command_id,
        terminal_evidence_raw=_current_terminal_evidence())
    preliminary = finalized["finalization_result"]
    terminal = finalized["terminal_ack_result"]
    if (
            document.get("finalization_receipt_sha256") !=
                preliminary["finalization_receipt_sha256"] or
            document.get("finalization_receipt") !=
                preliminary["finalization_receipt"] or
            document.get("terminal_ack_receipt_sha256") !=
                terminal["finalization_receipt_sha256"] or
            document.get("terminal_ack_receipt") !=
                terminal["finalization_receipt"] or
            document.get("terminal_ack_receipt_sha256") ==
                document.get("finalization_receipt_sha256") or
            terminal["terminal_replay"] is not True):
        _fail("ROOT_FINALIZER_OWNER_FINALIZATION_INVALID")
    return finalized


def _validate_handoff(raw: bytes, path: Path, now_ms: int,
                      config: Config, require_fresh: bool) -> tuple[
                          dict[str, Any], str, dict[str, dict[str, Any]]]:
    value = _exact(strict_json(raw, "ROOT_FINALIZER_HANDOFF_INVALID"),
                   HANDOFF_FIELDS, "ROOT_FINALIZER_HANDOFF_FIELDS_INVALID")
    if (value["schema"] != HANDOFF_SCHEMA or value["version"] != VERSION or
            value["domain_id"] != DOMAIN or
            value["paper_only"] is not True or
            value["live_authorized"] is not False or
            value["direct_broker_access"] is not False or
            value["authority_granted"] is not False or
            value["one_order_only"] is not True or
            value["end_flat_required"] is not True):
        _fail("ROOT_FINALIZER_HANDOFF_BOUNDARY_INVALID")
    campaign = _identifier(value["campaign_id"], "ROOT_FINALIZER_CAMPAIGN_INVALID")
    cycle = _identifier(value["cycle_id"], "ROOT_FINALIZER_CYCLE_INVALID")
    if path != _control_cycle_dir(
            config, campaign, cycle) / "execution-handoff.v1.json":
        _fail("ROOT_FINALIZER_HANDOFF_PATH_INVALID")
    issued = _integer(value["issued_at_ms"], "ROOT_FINALIZER_HANDOFF_TIME_INVALID", 1)
    expires = _integer(value["expires_at_ms"], "ROOT_FINALIZER_HANDOFF_TIME_INVALID", 1)
    if expires <= issued or (require_fresh and not issued <= now_ms <= expires):
        _fail("ROOT_FINALIZER_HANDOFF_EXPIRED")
    for field in (
            "policy_sha256", "source_baseline_sha256", "p1_audit_receipt_sha256",
            "watch_handoff_receipt_file_sha256",
            "watch_handoff_receipt_body_sha256",
            "zero_exposure_attestation_sha256",
            "admission_finalization_receipt_sha256", "strategy_sha256",
            "decision_sha256", "intent_sha256", "tool_catalog_sha256",
            "tool_descriptor_set_sha256", "installed_images_sha256"):
        _digest(value[field], "ROOT_FINALIZER_HANDOFF_DIGEST_INVALID")
    if canonical_sha(value["intent"]) != value["intent_sha256"]:
        _fail("ROOT_FINALIZER_HANDOFF_INTENT_MISMATCH")
    owner = _validate_owner_reference(value["session_owner_reference"])
    cleanup = _exact(value["root_cleanup_call"], ROOT_CLEANUP_CALL_FIELDS,
                     "ROOT_FINALIZER_CLEANUP_CALL_INVALID")
    for field, expected in ROOT_CLEANUP_CALL.items():
        if cleanup[field] != expected:
            _fail("ROOT_FINALIZER_CLEANUP_CALL_INVALID")
    call_id = _identifier(cleanup["tool_call_id"],
                          "ROOT_FINALIZER_CLEANUP_CALL_INVALID")
    if cleanup["command_id"] != call_id:
        _fail("ROOT_FINALIZER_CLEANUP_CALL_INVALID")
    _digest(cleanup["tool_descriptor_sha256"],
            "ROOT_FINALIZER_CLEANUP_CALL_INVALID")
    if cleanup["tool_descriptor_sha256"] != canonical_sha(
            ROOT_CLEANUP_DESCRIPTOR):
        _fail("ROOT_FINALIZER_CLEANUP_CALL_INVALID")
    tool_calls = value["tool_calls"]
    if not isinstance(tool_calls, list) or not tool_calls:
        _fail("ROOT_FINALIZER_TOOL_CALLS_INVALID")
    seen: set[str] = {call_id}
    for item in tool_calls:
        call = _exact(item, PLANNED_CALL_FIELDS,
                      "ROOT_FINALIZER_TOOL_CALLS_INVALID")
        item_id = _identifier(call["tool_call_id"],
                              "ROOT_FINALIZER_TOOL_CALLS_INVALID")
        if item_id in seen or call.get("tool_name") == cleanup["tool_name"]:
            _fail("ROOT_FINALIZER_TOOL_CALLS_INVALID")
        seen.add(item_id)
    images_value = value["installed_images"]
    if not isinstance(images_value, list) or not images_value:
        _fail("ROOT_FINALIZER_IMAGES_INVALID")
    images: dict[str, dict[str, Any]] = {}
    for item in images_value:
        image = _exact(item, IMAGE_FIELDS, "ROOT_FINALIZER_IMAGES_INVALID")
        role = _identifier(image["role"], "ROOT_FINALIZER_IMAGES_INVALID")
        if role in images or image["mode"] != 0o755 or image["uid"] != 0 or \
                image["gid"] != 0 or image["nlink"] != 1:
            _fail("ROOT_FINALIZER_IMAGES_INVALID")
        _digest(image["file_sha256"], "ROOT_FINALIZER_IMAGES_INVALID")
        images[role] = image
    if (canonical_sha(images_value) != value["installed_images_sha256"] or
            set(("executor", "backend-adapter", "root-finalizer")) - set(images)):
        _fail("ROOT_FINALIZER_IMAGES_INVALID")
    finalizer = images["root-finalizer"]
    if finalizer["path"] != str(config.self_image_path):
        _fail("ROOT_FINALIZER_SELF_IMAGE_INVALID")
    self_raw = stable_read(
        config.self_image_path, uid=config.self_uid, gid=config.self_gid,
        mode=0o755)
    if sha(self_raw) != finalizer["file_sha256"]:
        _fail("ROOT_FINALIZER_SELF_IMAGE_INVALID")
    if value["backend_transform_version"] != BACKEND_TRANSFORM_VERSION:
        _fail("ROOT_FINALIZER_BACKEND_TRANSFORM_INVALID")
    _identifier(value["execution_service_epoch"],
                "ROOT_FINALIZER_EXECUTION_EPOCH_INVALID")
    _integer(value["execution_service_fencing_generation"],
             "ROOT_FINALIZER_EXECUTION_FENCE_INVALID", 1)
    body = sealed(value, "ROOT_FINALIZER_HANDOFF_SEAL_INVALID")
    return value, body, images


def _validate_journal(raw: bytes, handoff: Mapping[str, Any],
                      handoff_file_sha256: str, handoff_body_sha256: str,
                      expected_last: int, *, require_terminal: bool = True,
                      require_response_boundary: bool = True) -> tuple[
                          list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not raw.endswith(b"\n"):
        _fail("ROOT_FINALIZER_JOURNAL_INVALID")
    records = [strict_json(line, "ROOT_FINALIZER_JOURNAL_INVALID")
               for line in raw.splitlines(keepends=True)]
    if not records:
        _fail("ROOT_FINALIZER_JOURNAL_INVALID")
    header = _exact(records[0], JOURNAL_HEADER_FIELDS,
                    "ROOT_FINALIZER_JOURNAL_INVALID")
    if (header["record_type"] != "HEADER" or header["sequence"] != 0 or
            header["handoff_file_sha256"] != handoff_file_sha256 or
            header["handoff_body_sha256"] != handoff_body_sha256 or
            header["session_owner_reference_sha256"] !=
                canonical_sha(handoff["session_owner_reference"]) or
            header["authority_granted"] is not False):
        _fail("ROOT_FINALIZER_JOURNAL_INVALID")
    calls = {item["call_role"]: item for item in handoff["tool_calls"]}
    pending: dict[str, dict[str, Any]] = {}
    completed: set[str] = set()
    evidence: list[dict[str, Any]] = []
    response_records: dict[str, dict[str, Any]] = {}
    indexes = {item["call_role"]: index
               for index, item in enumerate(handoff["tool_calls"])}
    last_request_index = -1
    for sequence, value in enumerate(records[1:], 1):
        record = _exact(value, JOURNAL_CALL_FIELDS,
                        "ROOT_FINALIZER_JOURNAL_INVALID")
        role = record["call_role"]
        call = calls.get(role)
        if (record["record_type"] != "CALL" or record["sequence"] != sequence or
                call is None or record["tool_call_id"] != call["tool_call_id"] or
                record["tool_name"] != call["tool_name"] or
                record["phase"] != call["phase"] or
                record["command_id"] != call["command_id"] or
                record["adapter_image_sha256"] !=
                    next(item["file_sha256"] for item in handoff["installed_images"]
                         if item["role"] == "backend-adapter") or
                record["adapter_transform_version"] != BACKEND_TRANSFORM_VERSION):
            _fail("ROOT_FINALIZER_JOURNAL_INVALID")
        _digest(record["request_sha256"], "ROOT_FINALIZER_JOURNAL_INVALID")
        if record["event"] == "REQUEST":
            role_index = indexes[role]
            if (role in pending or role in completed or
                    role_index <= last_request_index or
                    record["response_sha256"] is not None or
                    record["status"] != "PENDING" or
                    record["reason_code"] != "" or
                    record["service_epoch"] is not None or
                    record["fencing_generation"] is not None or
                    record["raw_request_sha256"] is not None or
                    record["raw_response_sha256"] is not None or
                    record["normalized_payload_sha256"] is not None):
                _fail("ROOT_FINALIZER_JOURNAL_INVALID")
            pending[role] = record
            last_request_index = role_index
        elif record["event"] == "RESPONSE":
            request_record = pending.get(role)
            if (request_record is None or
                    request_record["request_sha256"] !=
                        record["request_sha256"]):
                _fail("ROOT_FINALIZER_JOURNAL_INVALID")
            _digest(record["response_sha256"],
                    "ROOT_FINALIZER_JOURNAL_INVALID")
            if record["status"] not in {
                    "OK", "REJECTED", "DUPLICATE", "UNCERTAIN", "ERROR",
                    "PERMISSION_DENIED", "INVALID_TOOL"}:
                _fail("ROOT_FINALIZER_JOURNAL_INVALID")
            if record["status"] == "OK":
                if record["reason_code"] not in {"", "OK"}:
                    _fail("ROOT_FINALIZER_JOURNAL_INVALID")
            elif (not isinstance(record["reason_code"], str) or
                  REASON.fullmatch(record["reason_code"]) is None):
                _fail("ROOT_FINALIZER_JOURNAL_INVALID")
            _identifier(record["service_epoch"],
                        "ROOT_FINALIZER_JOURNAL_INVALID")
            _integer(record["fencing_generation"],
                     "ROOT_FINALIZER_JOURNAL_INVALID", 1)
            if (require_response_boundary and
                    (record["service_epoch"] !=
                        handoff["execution_service_epoch"] or
                     record["fencing_generation"] !=
                        handoff["execution_service_fencing_generation"])):
                _fail("ROOT_FINALIZER_JOURNAL_INVALID")
            for field in ("raw_request_sha256", "raw_response_sha256",
                          "normalized_payload_sha256"):
                _digest(record[field], "ROOT_FINALIZER_JOURNAL_INVALID")
            item = {
                "tool_call_id": call["tool_call_id"],
                "tool_name": call["tool_name"],
                "tool_descriptor_sha256": call["tool_descriptor_sha256"],
                "effect": call["effect"], "phase": call["phase"],
                "request_sha256": record["request_sha256"],
                "response_sha256": record["response_sha256"],
                "status": record["status"],
                "reason_code": record["reason_code"],
            }
            _exact(item, TOOL_EVIDENCE_FIELDS,
                   "ROOT_FINALIZER_JOURNAL_INVALID")
            evidence.append(item)
            response_records[role] = record
            del pending[role]
            completed.add(role)
        else:
            _fail("ROOT_FINALIZER_JOURNAL_INVALID")
    last = records[-1]
    if (last["sequence"] != expected_last or
            any(item.get("tool_name") == ROOT_CLEANUP_CALL["tool_name"]
                for item in records)):
        _fail("ROOT_FINALIZER_JOURNAL_NOT_TERMINAL")
    if (require_terminal and
            (pending or last.get("event") != "RESPONSE" or
             last.get("call_role") != "cleanup-risk")):
        _fail("ROOT_FINALIZER_JOURNAL_NOT_TERMINAL")
    return evidence, response_records


def _validate_final_payload(
        role: str, payload: Any, owner: Mapping[str, Any]
) -> dict[str, Any]:
    if role == "final-health":
        value = _exact(payload, HEALTH_FIELDS,
                       "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        if value != {
                "execution_mode": "PAPER", "paper_account": True,
                "connected": True, "authorized_connector_count": 1,
                "complete": True}:
            _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        return value
    if role == "final-account":
        value = _exact(payload, ACCOUNT_FIELDS,
                       "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        _digest(value["account_id_sha256"],
                "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        if (value["account_kind"] != "PAPER" or
                value["authoritative"] is not True or
                value["account_complete"] is not True or
                value["gross_absolute_position"] != 0 or
                type(value["fx_cash_generation"]) is not int or
                value["fx_cash_generation"] < 1 or
                value["owner_account"] != owner["owner_account"] or
                value["owner_execution_domain"] !=
                    owner["owner_execution_domain"]):
            _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        return value
    if role == "final-positions":
        value = _exact(payload, POSITIONS_FIELDS,
                       "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        _digest(value["snapshot_sha256"],
                "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        if (value["authoritative"] is not True or value["complete"] is not True or
                value["positions"] != [] or
                value["gross_absolute_position"] != 0 or
                type(value["position_generation"]) is not int or
                value["position_generation"] < 1 or
                type(value["fx_cash_generation"]) is not int or
                value["fx_cash_generation"] < 1 or
                value["owner_account"] != owner["owner_account"] or
                value["owner_execution_domain"] !=
                    owner["owner_execution_domain"]):
            _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        return value
    if role == "final-orders":
        value = _exact(payload, ORDERS_FIELDS,
                       "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        _digest(value["snapshot_sha256"],
                "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        if (value["authoritative"] is not True or value["complete"] is not True or
                value["orders"] != [] or
                type(value["connection_epoch"]) is not int or
                value["connection_epoch"] < 1 or
                type(value["generation"]) is not int or
                value["generation"] < 1 or
                value["owner_account"] != owner["owner_account"] or
                value["owner_execution_domain"] !=
                    owner["owner_execution_domain"]):
            _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        return value
    if role == "cleanup-risk":
        value = _exact(payload, RISK_FIELDS,
                       "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        for field in (
                "connection_epoch", "orders_generation", "position_generation",
                "fx_cash_generation"):
            if type(value[field]) is not int or value[field] < 1:
                _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        boundary = dict(value)
        for field in (
                "connection_epoch", "orders_generation", "position_generation",
                "fx_cash_generation"):
            del boundary[field]
        if boundary != {
                "paper_only": True, "live_authorized": False,
                "max_order_quantity": "1", "max_order_notional": "5000",
                "max_orders_per_minute": 1, "max_active_orders": 1,
                "max_gross_position": "1", "gross_absolute_position": "0",
                "gross_scope": "PAPER_BASELINE_DELTA",
                "owner_account": owner["owner_account"],
                "owner_execution_domain": owner["owner_execution_domain"],
                "allowed_instruments": ["EUR.USD"],
                "order_types": ["LMT"], "tifs": ["DAY"],
                "complete": True}:
            _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        return value
    _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")


def _validate_response_bundle(
        raw: bytes, *, path: Path, evidence: Mapping[str, Any],
        handoff: Mapping[str, Any], handoff_file_sha256: str,
        handoff_body_sha256: str, journal_evidence: list[dict[str, Any]],
        response_records: Mapping[str, dict[str, Any]],
        images: Mapping[str, dict[str, Any]]) -> tuple[dict[str, Any], str,
                                                       dict[str, Any]]:
    bundle = _exact(strict_json(
        raw, "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID"),
        PRE_CLEANUP_RESPONSE_BUNDLE_FIELDS,
        "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
    body = sealed(bundle, "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
    if (bundle["schema"] != PRE_CLEANUP_RESPONSE_BUNDLE_SCHEMA or
            bundle["version"] != VERSION or
            bundle["campaign_id"] != handoff["campaign_id"] or
            bundle["domain_id"] != DOMAIN or
            bundle["cycle_id"] != handoff["cycle_id"] or
            bundle["handoff_file_sha256"] != handoff_file_sha256 or
            bundle["handoff_body_sha256"] != handoff_body_sha256 or
            bundle["journal_path"] != evidence["journal_path"] or
            bundle["journal_sha256"] != evidence["journal_sha256"] or
            bundle["journal_last_sequence"] !=
                evidence["journal_last_sequence"] or
            bundle["final_roles"] != list(PRE_CLEANUP_FINAL_ROLES) or
            bundle["tool_evidence_sha256"] != canonical_sha(journal_evidence) or
            bundle["authority_granted"] is not False):
        _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
    values = bundle["responses"]
    if not isinstance(values, list) or len(values) != len(PRE_CLEANUP_FINAL_ROLES):
        _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
    calls = {item["call_role"]: item for item in handoff["tool_calls"]}
    payloads: dict[str, dict[str, Any]] = {}
    for role, item in zip(PRE_CLEANUP_FINAL_ROLES, values, strict=True):
        wrapper = _exact(item, PRE_CLEANUP_BUNDLE_RESPONSE_FIELDS,
                         "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        call = calls[role]
        record = response_records.get(role)
        backend = _exact(wrapper["backend_response"], BACKEND_RESPONSE_FIELDS,
                         "ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        backend_raw = canonical_json(backend)
        if (record is None or wrapper != {
                "call_role": role, "tool_call_id": call["tool_call_id"],
                "tool_name": call["tool_name"],
                "tool_descriptor_sha256": call["tool_descriptor_sha256"],
                "effect": call["effect"], "phase": call["phase"],
                "request_sha256": record["request_sha256"],
                "response_sha256": record["response_sha256"],
                "status": record["status"], "reason_code": record["reason_code"],
                "backend_response": backend}):
            _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        if (sha(backend_raw) != record["response_sha256"] or
                backend["schema"] !=
                    "hepta.p1-paper-canary-backend-response.v1" or
                backend["version"] != VERSION or
                backend["tool_call_id"] != call["tool_call_id"] or
                backend["tool_name"] != call["tool_name"] or
                backend["command_id"] != call["command_id"] or
                backend["tool_catalog_sha256"] != handoff["tool_catalog_sha256"] or
                backend["tool_descriptor_sha256"] !=
                    call["tool_descriptor_sha256"] or
                backend["status"] != "OK" or
                backend["reason_code"] not in {"", "OK"} or
                backend["service_epoch"] != handoff["execution_service_epoch"] or
                backend["fencing_generation"] !=
                    handoff["execution_service_fencing_generation"] or
                backend["adapter_image_sha256"] !=
                    images["backend-adapter"]["file_sha256"] or
                backend["adapter_transform_version"] !=
                    BACKEND_TRANSFORM_VERSION or
                backend["raw_request_sha256"] != record["raw_request_sha256"] or
                backend["raw_response_sha256"] != record["raw_response_sha256"] or
                backend["normalized_payload_sha256"] !=
                    record["normalized_payload_sha256"] or
                canonical_sha(backend["payload"]) !=
                    backend["normalized_payload_sha256"]):
            _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_INVALID")
        payloads[role] = _validate_final_payload(
            role, backend["payload"], handoff["session_owner_reference"])
    health = payloads["final-health"]
    account = payloads["final-account"]
    positions = payloads["final-positions"]
    orders = payloads["final-orders"]
    risk = payloads["cleanup-risk"]
    if not (
            account["fx_cash_generation"] == positions["fx_cash_generation"] ==
                risk["fx_cash_generation"] and
            positions["position_generation"] == risk["position_generation"] and
            orders["connection_epoch"] == risk["connection_epoch"] and
            orders["generation"] == risk["orders_generation"]):
        _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_GENERATION_MISMATCH")
    snapshot = {
        "orders_snapshot_sha256": orders["snapshot_sha256"],
        "positions_snapshot_sha256": positions["snapshot_sha256"],
        "account": account, "health": health,
        "service_epoch": handoff["execution_service_epoch"],
        "fencing_generation": handoff["execution_service_fencing_generation"],
    }
    derived = {
        "authoritative": True, "account_complete": True,
        "snapshot_sha256": canonical_sha(snapshot),
        "service_epoch": handoff["execution_service_epoch"],
        "fencing_generation": handoff["execution_service_fencing_generation"],
        "active_order_id_sha256s": [], "positions": [],
        "gross_absolute_position": 0, "authorized_connector_count": 1,
        "end_flat": True,
    }
    if (bundle["claimed_broker_state"] != derived or
            bundle["claimed_broker_state_sha256"] != canonical_sha(derived)):
        _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_STATE_MISMATCH")
    return bundle, body, derived


def validate_request(raw: bytes, *, config: Config, now_ms: int,
                     require_fresh: bool = True) -> Validated:
    request = _exact(strict_json(raw, "ROOT_FINALIZER_REQUEST_INVALID"),
                     REQUEST_FIELDS, "ROOT_FINALIZER_REQUEST_FIELDS_INVALID")
    if (request["schema"] != REQUEST_SCHEMA or request["version"] != VERSION or
            request["domain_id"] != DOMAIN or request["paper_only"] is not True or
            request["live_authorized"] is not False or
            request["authority_granted"] is not False or
            request["required_actions"] != NORMAL_REQUIRED_ACTIONS):
        _fail("ROOT_FINALIZER_REQUEST_BOUNDARY_INVALID")
    campaign = _identifier(request["campaign_id"], "ROOT_FINALIZER_CAMPAIGN_INVALID")
    cycle = _identifier(request["cycle_id"], "ROOT_FINALIZER_CYCLE_INVALID")
    issued = _integer(request["issued_at_ms"], "ROOT_FINALIZER_REQUEST_TIME_INVALID", 1)
    expires = _integer(request["expires_at_ms"], "ROOT_FINALIZER_REQUEST_TIME_INVALID", 1)
    if (expires != issued + ROOT_CLEANUP_TIMEOUT_MS or
            (require_fresh and not issued <= now_ms <= expires)):
        _fail("ROOT_FINALIZER_REQUEST_EXPIRED")
    request_body = sealed(request, "ROOT_FINALIZER_REQUEST_SEAL_INVALID")
    directory = _artifact_cycle_dir(config, campaign, cycle)
    request_path = directory / "root-cleanup-request.v1.json"
    disk_request = stable_read(request_path, uid=PEER_UID, gid=PEER_GID, mode=0o600)
    if disk_request != raw:
        _fail("ROOT_FINALIZER_REQUEST_WIRE_DISK_MISMATCH")
    evidence_path = directory / "pre-cleanup-flat-evidence.v1.json"
    if request["pre_cleanup_evidence_path"] != str(evidence_path):
        _fail("ROOT_FINALIZER_EVIDENCE_PATH_INVALID")
    evidence_raw = stable_read(
        evidence_path, uid=PEER_UID, gid=PEER_GID, mode=0o600)
    if sha(evidence_raw) != request["pre_cleanup_evidence_file_sha256"]:
        _fail("ROOT_FINALIZER_EVIDENCE_DIGEST_MISMATCH")
    evidence = _exact(strict_json(
        evidence_raw, "ROOT_FINALIZER_EVIDENCE_INVALID"), PRE_CLEANUP_FIELDS,
        "ROOT_FINALIZER_EVIDENCE_FIELDS_INVALID")
    evidence_body = sealed(evidence, "ROOT_FINALIZER_EVIDENCE_SEAL_INVALID")
    if evidence_body != request["pre_cleanup_evidence_body_sha256"]:
        _fail("ROOT_FINALIZER_EVIDENCE_DIGEST_MISMATCH")
    handoff_path = _control_cycle_dir(
        config, campaign, cycle) / "execution-handoff.v1.json"
    handoff_raw = stable_read(handoff_path, uid=0, gid=0, mode=0o600)
    handoff, handoff_body, images = _validate_handoff(
        handoff_raw, handoff_path, now_ms, config, require_fresh)
    handoff_file = sha(handoff_raw)
    cleanup = handoff["root_cleanup_call"]
    if (request["cleanup_tool_call_id"] != cleanup["tool_call_id"] or
            request["cleanup_command_id"] != cleanup["command_id"] or
            request["cleanup_tool_call_id"] != request["cleanup_command_id"] or
            request["tool_descriptor_sha256"] !=
                cleanup["tool_descriptor_sha256"] or
            request["handoff_file_sha256"] != handoff_file or
            request["handoff_body_sha256"] != handoff_body or
            not handoff["issued_at_ms"] <= issued <= handoff["expires_at_ms"] or
            request["session_owner_reference_sha256"] !=
                canonical_sha(handoff["session_owner_reference"]) or
            request["execution_service_epoch"] !=
                handoff["execution_service_epoch"] or
            request["execution_service_fencing_generation"] !=
                handoff["execution_service_fencing_generation"]):
        _fail("ROOT_FINALIZER_REQUEST_HANDOFF_MISMATCH")
    if (evidence["schema"] != PRE_CLEANUP_SCHEMA or
            evidence["version"] != VERSION or
            evidence["campaign_id"] != campaign or evidence["cycle_id"] != cycle or
            evidence["domain_id"] != DOMAIN or
            evidence["handoff_path"] != str(handoff_path) or
            evidence["handoff_file_sha256"] != handoff_file or
            evidence["handoff_body_sha256"] != handoff_body or
            evidence["intent_sha256"] != handoff["intent_sha256"] or
            evidence["installed_images_sha256"] !=
                handoff["installed_images_sha256"] or
            evidence["executor_image_sha256"] !=
                images["executor"]["file_sha256"] or
            evidence["backend_adapter_image_sha256"] !=
                images["backend-adapter"]["file_sha256"] or
            evidence["backend_transform_version"] != BACKEND_TRANSFORM_VERSION or
            evidence["session_owner_reference_sha256"] !=
                canonical_sha(handoff["session_owner_reference"]) or
            evidence["execution_service_epoch"] !=
                handoff["execution_service_epoch"] or
            evidence["execution_service_fencing_generation"] !=
                handoff["execution_service_fencing_generation"] or
            evidence["authority_granted"] is not False or
            any(evidence[field] is not True for field in (
                "cycle_opened", "cycle_closed", "place_attempted",
                "close_attempted")) or
            evidence["close_outcome"] not in {
                "PREVIEW_REJECTED", "PLACE_REJECTED", "PLACE_ACCEPTED",
                "PLACE_UNCERTAIN", "OPERATOR_ABORT"}):
        _fail("ROOT_FINALIZER_EVIDENCE_BINDING_INVALID")
    journal_path = directory / "execution-journal.v1.jsonl"
    if evidence["journal_path"] != str(journal_path):
        _fail("ROOT_FINALIZER_JOURNAL_PATH_INVALID")
    journal_raw = stable_read(journal_path, uid=PEER_UID, gid=PEER_GID, mode=0o600)
    if (sha(journal_raw) != evidence["journal_sha256"] or
            len(journal_raw) != evidence["journal_size"]):
        _fail("ROOT_FINALIZER_JOURNAL_DIGEST_MISMATCH")
    journal_evidence, response_records = _validate_journal(
        journal_raw, handoff, handoff_file, handoff_body,
        evidence["journal_last_sequence"])
    if canonical_sha(journal_evidence) != evidence["tool_evidence_sha256"]:
        _fail("ROOT_FINALIZER_TOOL_EVIDENCE_MISMATCH")
    bundle_path = directory / "pre-cleanup-response-bundle.v1.json"
    if evidence["response_bundle_path"] != str(bundle_path):
        _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_PATH_INVALID")
    bundle_raw = stable_read(
        bundle_path, uid=PEER_UID, gid=PEER_GID, mode=0o600)
    if sha(bundle_raw) != evidence["response_bundle_file_sha256"]:
        _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_DIGEST_MISMATCH")
    bundle, bundle_body, derived_broker = _validate_response_bundle(
        bundle_raw, path=bundle_path, evidence=evidence, handoff=handoff,
        handoff_file_sha256=handoff_file,
        handoff_body_sha256=handoff_body,
        journal_evidence=journal_evidence,
        response_records=response_records, images=images)
    if bundle_body != evidence["response_bundle_body_sha256"]:
        _fail("ROOT_FINALIZER_RESPONSE_BUNDLE_DIGEST_MISMATCH")
    broker = _exact(evidence["broker_state"], BROKER_STATE_FIELDS,
                    "ROOT_FINALIZER_BROKER_STATE_INVALID")
    if (broker != derived_broker or
            canonical_sha(broker) != evidence["broker_state_sha256"]):
        _fail("ROOT_FINALIZER_PRE_CLEANUP_NOT_FLAT")
    return Validated(
        request, raw, request_path, request_body, evidence, evidence_raw,
        evidence_path, evidence_body, handoff, handoff_raw, handoff_path,
        handoff_body, journal_raw, images, bundle, bundle_raw, bundle_body)


def validate_emergency_request(
        raw: bytes, *, config: Config, now_ms: int,
        require_fresh: bool = True) -> Validated:
    request = _exact(
        strict_json(raw, "ROOT_FINALIZER_EMERGENCY_REQUEST_INVALID"),
        EMERGENCY_REQUEST_FIELDS,
        "ROOT_FINALIZER_EMERGENCY_REQUEST_FIELDS_INVALID")
    if (request["schema"] != EMERGENCY_REQUEST_SCHEMA or
            request["version"] != VERSION or request["domain_id"] != DOMAIN or
            request["paper_only"] is not True or
            request["live_authorized"] is not False or
            request["authority_granted"] is not False or
            request["broker_flat_proven"] is not False or
            request["required_actions"] != EMERGENCY_REQUIRED_ACTIONS):
        _fail("ROOT_FINALIZER_EMERGENCY_REQUEST_BOUNDARY_INVALID")
    campaign = _identifier(
        request["campaign_id"], "ROOT_FINALIZER_CAMPAIGN_INVALID")
    cycle = _identifier(request["cycle_id"], "ROOT_FINALIZER_CYCLE_INVALID")
    issued = _integer(
        request["issued_at_ms"], "ROOT_FINALIZER_REQUEST_TIME_INVALID", 1)
    expires = _integer(
        request["expires_at_ms"], "ROOT_FINALIZER_REQUEST_TIME_INVALID", 1)
    if expires <= issued or (require_fresh and not issued <= now_ms <= expires):
        _fail("ROOT_FINALIZER_EMERGENCY_REQUEST_EXPIRED")
    request_body = sealed(
        request, "ROOT_FINALIZER_EMERGENCY_REQUEST_SEAL_INVALID")
    directory = _artifact_cycle_dir(config, campaign, cycle)
    request_path = directory / "root-emergency-cleanup-request.v1.json"
    disk_request = stable_read(
        request_path, uid=PEER_UID, gid=PEER_GID, mode=0o600)
    if disk_request != raw:
        _fail("ROOT_FINALIZER_EMERGENCY_REQUEST_WIRE_DISK_MISMATCH")
    evidence_path = directory / "root-emergency-cleanup-evidence.v1.json"
    if request["emergency_evidence_path"] != str(evidence_path):
        _fail("ROOT_FINALIZER_EMERGENCY_EVIDENCE_PATH_INVALID")
    evidence_raw = stable_read(
        evidence_path, uid=PEER_UID, gid=PEER_GID, mode=0o600)
    if sha(evidence_raw) != request["emergency_evidence_file_sha256"]:
        _fail("ROOT_FINALIZER_EMERGENCY_EVIDENCE_DIGEST_MISMATCH")
    evidence = _exact(
        strict_json(evidence_raw, "ROOT_FINALIZER_EMERGENCY_EVIDENCE_INVALID"),
        EMERGENCY_EVIDENCE_FIELDS,
        "ROOT_FINALIZER_EMERGENCY_EVIDENCE_FIELDS_INVALID")
    evidence_body = sealed(
        evidence, "ROOT_FINALIZER_EMERGENCY_EVIDENCE_SEAL_INVALID")
    if evidence_body != request["emergency_evidence_body_sha256"]:
        _fail("ROOT_FINALIZER_EMERGENCY_EVIDENCE_DIGEST_MISMATCH")
    handoff_path = _control_cycle_dir(
        config, campaign, cycle) / "execution-handoff.v1.json"
    handoff_raw = stable_read(handoff_path, uid=0, gid=0, mode=0o600)
    handoff, handoff_body, images = _validate_handoff(
        handoff_raw, handoff_path, now_ms, config, require_fresh)
    handoff_file = sha(handoff_raw)
    cleanup = handoff["root_cleanup_call"]
    reasons = request["recovery_reason_codes"]
    if (not isinstance(reasons, list) or not reasons or
            reasons != list(dict.fromkeys(reasons)) or
            any(not isinstance(reason, str) or REASON.fullmatch(reason) is None
                for reason in reasons) or
            request["cleanup_tool_call_id"] != cleanup["tool_call_id"] or
            request["cleanup_command_id"] != cleanup["command_id"] or
            request["cleanup_tool_call_id"] != request["cleanup_command_id"] or
            request["tool_descriptor_sha256"] !=
                cleanup["tool_descriptor_sha256"] or
            request["handoff_file_sha256"] != handoff_file or
            request["handoff_body_sha256"] != handoff_body or
            expires > handoff["expires_at_ms"] or
            request["session_owner_reference_sha256"] !=
                canonical_sha(handoff["session_owner_reference"]) or
            request["execution_service_epoch"] !=
                handoff["execution_service_epoch"] or
            request["execution_service_fencing_generation"] !=
                handoff["execution_service_fencing_generation"]):
        _fail("ROOT_FINALIZER_EMERGENCY_REQUEST_HANDOFF_MISMATCH")
    if (evidence["schema"] !=
            "hepta.p1-paper-canary-root-emergency-cleanup-evidence.v1" or
            evidence["version"] != VERSION or
            evidence["campaign_id"] != campaign or
            evidence["domain_id"] != DOMAIN or evidence["cycle_id"] != cycle or
            evidence["handoff_path"] != str(handoff_path) or
            evidence["handoff_file_sha256"] != handoff_file or
            evidence["handoff_body_sha256"] != handoff_body or
            evidence["intent_sha256"] != handoff["intent_sha256"] or
            evidence["installed_images_sha256"] !=
                handoff["installed_images_sha256"] or
            evidence["executor_image_sha256"] !=
                images["executor"]["file_sha256"] or
            evidence["backend_adapter_image_sha256"] !=
                images["backend-adapter"]["file_sha256"] or
            evidence["root_finalizer_image_sha256"] !=
                images["root-finalizer"]["file_sha256"] or
            evidence["backend_transform_version"] != BACKEND_TRANSFORM_VERSION or
            evidence["session_owner_reference_sha256"] !=
                canonical_sha(handoff["session_owner_reference"]) or
            evidence["execution_service_epoch"] !=
                handoff["execution_service_epoch"] or
            evidence["execution_service_fencing_generation"] !=
                handoff["execution_service_fencing_generation"] or
            evidence["recovery_reason_codes"] != reasons or
            evidence["broker_flat_proven"] is not False or
            evidence["authority_granted"] is not False):
        _fail("ROOT_FINALIZER_EMERGENCY_EVIDENCE_BINDING_INVALID")
    created = _integer(
        evidence["created_at_ms"], "ROOT_FINALIZER_EMERGENCY_EVIDENCE_INVALID", 1)
    if created < handoff["issued_at_ms"] or created > request["issued_at_ms"]:
        _fail("ROOT_FINALIZER_EMERGENCY_EVIDENCE_INVALID")
    for field in ("cycle_opened", "cycle_closed", "place_attempted",
                  "close_attempted"):
        if type(evidence[field]) is not bool:
            _fail("ROOT_FINALIZER_EMERGENCY_EVIDENCE_INVALID")
    if (not isinstance(evidence["last_known_state"], dict) or
            canonical_sha(evidence["last_known_state"]) !=
                evidence["last_known_state_sha256"]):
        _fail("ROOT_FINALIZER_EMERGENCY_STATE_INVALID")
    _exact(evidence["last_known_state"], BROKER_STATE_FIELDS,
           "ROOT_FINALIZER_EMERGENCY_STATE_INVALID")
    valid_phases = {item["phase"] for item in handoff["tool_calls"]}
    if (evidence["last_completed_phase"] not in valid_phases | {"NONE"} or
            evidence["uncertainty_kind"] not in {"PRE_TOOL", "TOOL_CALL"} or
            not isinstance(evidence["close_outcome"], str) or
            not evidence["close_outcome"]):
        _fail("ROOT_FINALIZER_EMERGENCY_UNCERTAINTY_INVALID")
    if evidence["uncertainty_kind"] == "PRE_TOOL":
        if (evidence["uncertain_phase"] != "NOT_APPLICABLE" or
                evidence["uncertain_tool_call_id"] != "NOT_APPLICABLE"):
            _fail("ROOT_FINALIZER_EMERGENCY_UNCERTAINTY_INVALID")
    else:
        if (evidence["uncertain_phase"] not in valid_phases or
                not any(item["tool_call_id"] ==
                        evidence["uncertain_tool_call_id"]
                        for item in handoff["tool_calls"])):
            _fail("ROOT_FINALIZER_EMERGENCY_UNCERTAINTY_INVALID")
    journal_path = directory / "execution-journal.v1.jsonl"
    if evidence["journal_path"] != str(journal_path):
        _fail("ROOT_FINALIZER_JOURNAL_PATH_INVALID")
    journal_raw = stable_read(
        journal_path, uid=PEER_UID, gid=PEER_GID, mode=0o600)
    if (sha(journal_raw) != evidence["journal_sha256"] or
            len(journal_raw) != evidence["journal_size"]):
        _fail("ROOT_FINALIZER_JOURNAL_DIGEST_MISMATCH")
    journal_evidence, _responses = _validate_journal(
        journal_raw, handoff, handoff_file, handoff_body,
        evidence["journal_last_sequence"], require_terminal=False,
        require_response_boundary=False)
    if canonical_sha(journal_evidence) != evidence["tool_evidence_sha256"]:
        _fail("ROOT_FINALIZER_TOOL_EVIDENCE_MISMATCH")
    return Validated(
        request, raw, request_path, request_body, evidence, evidence_raw,
        evidence_path, evidence_body, handoff, handoff_raw, handoff_path,
        handoff_body, journal_raw, images, {}, b"", "")


def _stable_secret(
        path: Path, *, uid: int, gid: int, modes: frozenset[int],
        maximum: int = MAX_BYTES) -> bytes:
    last: FinalizerError | None = None
    for mode in sorted(modes):
        try:
            return stable_read(path, uid=uid, gid=gid, mode=mode, maximum=maximum)
        except FinalizerError as error:
            last = error
    assert last is not None
    raise last


def _require_owner_authority_root() -> None:
    try:
        metadata = os.lstat(OWNER_AUTHORITY_ROOT)
    except OSError:
        _fail("ROOT_FINALIZER_OWNER_AUTHORITY_ROOT_INVALID")
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or
            metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o700):
        _fail("ROOT_FINALIZER_OWNER_AUTHORITY_ROOT_INVALID")


def _owner_material(owner: Mapping[str, Any]) -> tuple[bytes, bytes, bytes]:
    _require_owner_authority_root()
    authority = _stable_secret(
        Path(owner["authority_path"]), uid=0, gid=0,
        modes=frozenset({0o600}))
    bearer = _stable_secret(
        Path(owner["revoke_bearer_path"]), uid=0, gid=0,
        modes=frozenset({0o600}), maximum=65)
    token = _stable_secret(
        Path(owner["token_path"]), uid=PEER_UID, gid=PEER_GID,
        modes=frozenset({0o400}), maximum=65)
    authority_document = strict_json(
        authority, "ROOT_FINALIZER_OWNER_AUTHORITY_INVALID")
    if (sha(authority) != owner["authority_file_sha256"] or
            sealed(authority_document,
                   "ROOT_FINALIZER_OWNER_AUTHORITY_INVALID") !=
                owner["authority_body_sha256"] or
            sha(bearer) != owner["revoke_bearer_sha256"] or
            sha(token) != owner["token_sha256"] or token != bearer or
            len(bearer) != 65 or
            re.fullmatch(rb"[0-9a-f]{64}\n", bearer) is None or
            authority_document.get("token_name") != owner["token_name"] or
            authority_document.get("session_id") != owner["session_id"] or
            authority_document.get("lease_generation") !=
                owner["lease_generation"] or
            authority_document.get("peer_uid") != owner["peer_uid"] or
            authority_document.get("peer_gid", owner["peer_gid"]) !=
                owner["peer_gid"] or
            authority_document.get("token_sha256") != owner["token_sha256"] or
            authority_document.get("owner_account") !=
                owner["owner_account"] or
            authority_document.get("owner_execution_domain") !=
                owner["owner_execution_domain"]):
        _fail("ROOT_FINALIZER_OWNER_MATERIAL_INVALID")
    return authority, bearer, token


def _owner_material_without_delivery(
        owner: Mapping[str, Any]) -> tuple[bytes, bytes]:
    _require_owner_authority_root()
    authority = _stable_secret(
        Path(owner["authority_path"]), uid=0, gid=0,
        modes=frozenset({0o600}))
    bearer = _stable_secret(
        Path(owner["revoke_bearer_path"]), uid=0, gid=0,
        modes=frozenset({0o600}), maximum=65)
    authority_document = strict_json(
        authority, "ROOT_FINALIZER_OWNER_AUTHORITY_INVALID")
    if (sha(authority) != owner["authority_file_sha256"] or
            sealed(authority_document,
                   "ROOT_FINALIZER_OWNER_AUTHORITY_INVALID") !=
                owner["authority_body_sha256"] or
            sha(bearer) != owner["revoke_bearer_sha256"] or
            len(bearer) != 65 or
            re.fullmatch(rb"[0-9a-f]{64}\n", bearer) is None or
            authority_document.get("token_name") != owner["token_name"] or
            authority_document.get("session_id") != owner["session_id"] or
            authority_document.get("lease_generation") !=
                owner["lease_generation"] or
            authority_document.get("peer_uid") != owner["peer_uid"] or
            authority_document.get("peer_gid", owner["peer_gid"]) !=
                owner["peer_gid"] or
            authority_document.get("token_sha256") != owner["token_sha256"] or
            authority_document.get("owner_account") !=
                owner["owner_account"] or
            authority_document.get("owner_execution_domain") !=
                owner["owner_execution_domain"]):
        _fail("ROOT_FINALIZER_OWNER_MATERIAL_INVALID")
    return authority, bearer


def _owner_terminal_bearer(owner: Mapping[str, Any]) -> bytes:
    _require_owner_authority_root()
    bearer = _stable_secret(
        Path(owner["revoke_bearer_path"]), uid=0, gid=0,
        modes=frozenset({0o600}), maximum=65)
    if (sha(bearer) != owner["revoke_bearer_sha256"] or
            len(bearer) != 65 or
            re.fullmatch(rb"[0-9a-f]{64}\n", bearer) is None):
        _fail("ROOT_FINALIZER_OWNER_MATERIAL_INVALID")
    return bearer


def _unlink_bound(
        path: Path, expected_sha256: str, *, uid: int, gid: int,
        modes: frozenset[int], maximum: int = MAX_BYTES,
        allow_absent: bool = False) -> None:
    try:
        raw = _stable_secret(
            path, uid=uid, gid=gid, modes=modes, maximum=maximum)
    except FinalizerError:
        if allow_absent and not path.exists() and not path.is_symlink():
            # Absence is only a completed retry after the containing
            # directory has been synchronized.  This closes the crash seam
            # where unlink(2) succeeded but the first fsync(dir) failed.
            try:
                _fsync_directory(path.parent)
            except OSError:
                _fail("ROOT_FINALIZER_OWNER_MATERIAL_DESTROY_FAILED")
            return
        raise
    if sha(raw) != expected_sha256:
        _fail("ROOT_FINALIZER_OWNER_MATERIAL_CHANGED")
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except OSError:
        _fail("ROOT_FINALIZER_OWNER_MATERIAL_DESTROY_FAILED")


def _publish_owner_transition(transition: Mapping[str, Any]) -> None:
    value = _exact(
        dict(transition), OWNER_TRANSITION_FIELDS,
        "ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
    document = value["document"]
    fields = OWNER_RETIREMENT_FIELDS if value["durable_owner_status"] == \
        "RETIRED" else OWNER_RECOVERY_FIELDS
    _exact(document, fields, "ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
    sealed(document, "ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
    if value["durable_owner_status"] == "RETIRED":
        if (document["schema"] != OWNER_RETIREMENT_SCHEMA or
                document["version"] != OWNER_RETIREMENT_VERSION or
                document["hsl_owner_purged"] is not True or
                document["broker_flat_proven"] is not True or
                document["durable_hsl_audit"] != HSL8_TERMINAL_PROOF):
            _fail("ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
    elif (document["schema"] != OWNER_RECOVERY_SCHEMA or
          document["version"] != VERSION):
        _fail("ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
    raw = canonical_json(document)
    path = Path(value["path"])
    if (sha(raw) != value["file_sha256"] or
            document["body_sha256"] != value["body_sha256"]):
        _fail("ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
    if path.exists() or path.is_symlink():
        if stable_read(path, uid=0, gid=0, mode=0o600) != raw:
            _fail("ROOT_FINALIZER_OWNER_TRANSITION_CONFLICT")
    else:
        atomic_write(path, raw, mode=0o600, exclusive=True)
    if stable_read(path, uid=0, gid=0, mode=0o600) != raw:
        _fail("ROOT_FINALIZER_OWNER_TRANSITION_REOPEN_MISMATCH")


class ProductionControl:
    def __init__(self, module: ModuleType):
        self.module = module

    def capture_authority(self, handoff: Mapping[str, Any]) -> dict[str, str]:
        c = self.module
        transaction_path = c._transaction_path(c.LOCAL_PAPER_STATE_ROOT)
        transaction = c._load_control_transaction(transaction_path)
        active = c._runtime_document(
            c.GUARDIAN_ACTIVE_PATH, fields=c.GUARDIAN_RUNTIME_FIELDS,
            schema=c.GUARDIAN_RUNTIME_SCHEMA)
        if (transaction is None or active is None or
                transaction.get("operation") != "ENABLE" or
                transaction.get("phase") != c.CONTROL_NORMAL_TERMINAL_PHASE or
                not c._runtime_binding_matches(
                    active, transaction, identities_path=c.DEFAULT_IDENTITIES,
                    drop_in_path=c.DEFAULT_DROP_IN)):
            _fail("ROOT_FINALIZER_GUARDIAN_BINDING_INVALID")
        request = transaction["request"]
        if (request.get("domain") != DOMAIN or
                request.get("external_p1_finalized") is not True or
                request.get("campaign_id") != handoff["campaign_id"] or
                request.get("source_baseline_sha256") !=
                    handoff["source_baseline_sha256"] or
                request.get("handoff_file_sha256") !=
                    handoff["watch_handoff_receipt_file_sha256"] or
                request.get("handoff_body_sha256") !=
                    handoff["watch_handoff_receipt_body_sha256"]):
            _fail("ROOT_FINALIZER_GUARDIAN_LINEAGE_INVALID")
        transaction_raw = stable_read(transaction_path, uid=0, gid=0, mode=0o600)
        active_raw = stable_read(c.GUARDIAN_ACTIVE_PATH, uid=0, gid=0, mode=0o600)
        return {
            "guardian_request_id": active["guardian_request_id"],
            "local_control_transaction_id": transaction["transaction_id"],
            "local_control_request_sha256": transaction["request_sha256"],
            "local_control_wal_file_sha256": sha(transaction_raw),
            "guardian_active_receipt_file_sha256": sha(active_raw),
            "guardian_active_receipt_body_sha256": active["body_sha256"],
        }

    def stop_guardian(self) -> None:
        self.module._run_systemctl(["stop", self.module.GUARDIAN_UNIT])

    @staticmethod
    def _sessionctl(
            arguments: list[str], *, timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                arguments, check=False, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds)
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            _fail("ROOT_FINALIZER_OWNER_TRANSITION_UNCERTAIN")

    @staticmethod
    def _sessionctl_response(
            completed: subprocess.CompletedProcess[str], reason: str,
    ) -> dict[str, Any]:
        raw = completed.stdout
        if not isinstance(raw, str):
            _fail(reason)
        try:
            raw_size = len(raw.encode("utf-8"))
        except UnicodeEncodeError:
            _fail(reason)
        if (not raw.endswith("\n") or raw.count("\n") != 1 or
                raw_size > MAX_BYTES):
            _fail(reason)
        try:
            response = json.loads(
                raw, object_pairs_hook=_unique, parse_float=_reject,
                parse_constant=_reject)
        except (json.JSONDecodeError, UnicodeEncodeError, ValueError):
            _fail(reason)
        if not isinstance(response, dict):
            _fail(reason)
        return response

    def advance_owner_finalization(
            self, handoff: Mapping[str, Any],
            finalization: Mapping[str, Any]) -> dict[str, Any]:
        command_id = finalization.get("query_command_id")
        if not isinstance(command_id, str):
            _fail("ROOT_FINALIZER_OWNER_FINALIZATION_INVALID")
        terminal_evidence_raw = _owner_finalization_terminal_evidence(
            finalization)
        current = _validate_owner_finalization(
            dict(finalization), handoff, command_id,
            terminal_evidence_raw=terminal_evidence_raw)
        owner = _validate_owner_reference(handoff["session_owner_reference"])
        # Every replay reopens and rebinds all three credentials.  A missing
        # bearer is not interpreted as evidence that the remote transition
        # completed.
        _owner_material(owner)
        base = ["/usr/bin/hepta-sessionctl", "--socket", SUPERVISOR_SOCKET]
        state = current["state"]
        if state == "INTENT":
            arguments = base + [
                "--io-timeout-ms", str(SESSIONCTL_QUERY_IO_TIMEOUT_MS),
                "recovery-query", "--token-file",
                owner["revoke_bearer_path"], "--generation",
                str(owner["lease_generation"]), "--command-id",
                current["query_command_id"], "--token-owner-uid", "0",
                "--require-paper-finalization",
            ]
            completed = self._sessionctl(
                arguments,
                timeout_seconds=SESSIONCTL_QUERY_SUBPROCESS_TIMEOUT_SECONDS)
            response = self._sessionctl_response(
                completed, "ROOT_FINALIZER_OWNER_RECOVERY_INVALID")
            if completed.returncode != 0:
                _fail("ROOT_FINALIZER_OWNER_RECOVERY_INVALID")
            updated = dict(current)
            updated["recovery_query_result"] = dict(
                _validate_recovery_query_result(response, handoff, current))
            updated["state"] = "RECOVERY_ONLY"
        elif state == "RECOVERY_ONLY":
            arguments = base + [
                "--io-timeout-ms", str(SESSIONCTL_IO_TIMEOUT_MS),
                "paper-finalize", "--token-file",
                owner["revoke_bearer_path"], "--generation",
                str(owner["lease_generation"]), "--recovery-id",
                current["recovery_id"], "--finalization-id",
                current["finalization_id"],
                "--expected-owner-set-sha256",
                current["expected_owner_set_sha256"],
                "--expected-owner-count",
                str(current["expected_owner_count"]),
                "--token-owner-uid", "0",
            ]
            completed = self._sessionctl(
                arguments,
                timeout_seconds=SESSIONCTL_SUBPROCESS_TIMEOUT_SECONDS)
            response = self._sessionctl_response(
                completed, "ROOT_FINALIZER_OWNER_FINALIZATION_INVALID")
            if completed.returncode != 0:
                _fail("ROOT_FINALIZER_OWNER_FINALIZATION_PENDING")
            updated = dict(current)
            updated["finalization_result"] = dict(
                _validate_finalization_result(
                    response, handoff, current,
                    expected_state="AUDIT_SEALED"))
            updated["state"] = "AUDIT_SEALED"
        elif state == "AUDIT_SEALED":
            sealed_result = _validate_finalization_result(
                current["finalization_result"], handoff, current,
                expected_state="AUDIT_SEALED")
            prepare_arguments = base + [
                "--io-timeout-ms", str(SESSIONCTL_IO_TIMEOUT_MS),
                "paper-terminal-witness-prepare", "--token-file",
                owner["revoke_bearer_path"], "--generation",
                str(owner["lease_generation"]), "--recovery-id",
                current["recovery_id"], "--finalization-id",
                current["finalization_id"],
                "--expected-owner-set-sha256",
                current["expected_owner_set_sha256"],
                "--expected-owner-count", str(current["expected_owner_count"]),
                "--receipt-sha256",
                sealed_result["finalization_receipt_sha256"],
                "--token-owner-uid", "0",
            ]
            prepared = self._sessionctl(
                prepare_arguments,
                timeout_seconds=SESSIONCTL_SUBPROCESS_TIMEOUT_SECONDS)
            prepare_response = self._sessionctl_response(
                prepared, "ROOT_FINALIZER_TERMINAL_WITNESS_PREPARE_INVALID")
            prepared_pair = (
                prepared.returncode, prepare_response.get("accepted"),
                prepare_response.get("reason_code"))
            if (prepared_pair not in {
                    (0, True, "PAPER_TERMINAL_WITNESS_PREPARED"),
                    (4, False,
                     "PAPER_TERMINAL_WITNESS_PREPARE_INTENT_PENDING")} or
                    prepare_response.get("paper_finalization_state") !=
                        "AUDIT_SEALED" or
                    prepare_response.get("paper_finalization_required") is not
                        True or
                    prepare_response.get("recovery_id") !=
                        current["recovery_id"] or
                    prepare_response.get("finalization_id") !=
                        current["finalization_id"] or
                    prepare_response.get("expected_owner_set_sha256") !=
                        current["expected_owner_set_sha256"] or
                    prepare_response.get("expected_owner_count") !=
                        current["expected_owner_count"] or
                    prepare_response.get("lease_generation") !=
                        owner["lease_generation"] or
                    prepare_response.get("finalization_receipt_sha256") !=
                        sealed_result["finalization_receipt_sha256"]):
                _fail("ROOT_FINALIZER_TERMINAL_WITNESS_PREPARE_INVALID")
            evidence_raw = stable_read(
                TERMINAL_EVIDENCE_PATH, uid=0, gid=0, mode=0o400,
                maximum=12288)
            arguments = base + [
                "--io-timeout-ms", str(SESSIONCTL_ACK_IO_TIMEOUT_MS),
                "paper-terminal-witness-ack", "--token-file",
                owner["revoke_bearer_path"], "--generation",
                str(owner["lease_generation"]), "--recovery-id",
                current["recovery_id"], "--finalization-id",
                current["finalization_id"],
                "--expected-owner-set-sha256",
                current["expected_owner_set_sha256"],
                "--expected-owner-count",
                str(current["expected_owner_count"]), "--receipt-sha256",
                sealed_result["finalization_receipt_sha256"],
                "--terminal-evidence-file", str(TERMINAL_EVIDENCE_PATH),
                "--terminal-evidence-sha256", sha(evidence_raw),
                "--token-owner-uid", "0",
            ]
            completed = self._sessionctl(
                arguments,
                timeout_seconds=SESSIONCTL_ACK_SUBPROCESS_TIMEOUT_SECONDS)
            response = self._sessionctl_response(
                completed, "ROOT_FINALIZER_OWNER_FINALIZATION_ACK_INVALID")
            if completed.returncode != 0:
                _fail("ROOT_FINALIZER_OWNER_FINALIZATION_ACK_PENDING")
            acknowledged = _validate_terminal_ack_result(
                response, handoff, current, sealed_result,
                require_replay=False, terminal_evidence_raw=evidence_raw)
            updated = dict(current)
            updated["terminal_ack_result"] = dict(acknowledged)
            updated["state"] = "ACKED"
        else:
            _fail("ROOT_FINALIZER_OWNER_FINALIZATION_ALREADY_ACKED")
        return _validate_owner_finalization(
            updated, handoff, command_id,
            terminal_evidence_raw=(
                evidence_raw if state == "AUDIT_SEALED" else None))

    def verify_owner_terminal_ack(
            self, handoff: Mapping[str, Any],
            finalization: Mapping[str, Any]) -> dict[str, Any]:
        command_id = finalization.get("query_command_id")
        if not isinstance(command_id, str):
            _fail("ROOT_FINALIZER_OWNER_FINALIZATION_INVALID")
        terminal_evidence_raw = _current_terminal_evidence()
        current = _validate_owner_finalization(
            dict(finalization), handoff, command_id,
            terminal_evidence_raw=terminal_evidence_raw)
        if current["state"] != "ACKED":
            _fail("ROOT_FINALIZER_OWNER_TERMINAL_ACK_REQUIRED")
        owner = _validate_owner_reference(handoff["session_owner_reference"])
        # The root revoke bearer deliberately outlives the delivered token and
        # authority document until the outer completion is durable.  This
        # keeps exact current-runtime replay possible across every WAL resume
        # that could still publish completion or purge the last credential.
        _owner_terminal_bearer(owner)
        preliminary = _validate_finalization_result(
            current["finalization_result"], handoff, current,
            expected_state="AUDIT_SEALED")
        stored = _validate_terminal_ack_result(
            current["terminal_ack_result"], handoff, current, preliminary,
            require_replay=False, terminal_evidence_raw=terminal_evidence_raw)
        arguments = [
            "/usr/bin/hepta-sessionctl", "--socket", SUPERVISOR_SOCKET,
            "--io-timeout-ms", str(SESSIONCTL_ACK_IO_TIMEOUT_MS),
            "paper-terminal-witness-ack", "--token-file",
            owner["revoke_bearer_path"], "--generation",
            str(owner["lease_generation"]), "--recovery-id",
            current["recovery_id"], "--finalization-id",
            current["finalization_id"], "--expected-owner-set-sha256",
            current["expected_owner_set_sha256"], "--expected-owner-count",
            str(current["expected_owner_count"]), "--receipt-sha256",
            preliminary["finalization_receipt_sha256"],
            "--terminal-evidence-file", str(TERMINAL_EVIDENCE_PATH),
            "--terminal-evidence-sha256", sha(terminal_evidence_raw),
            "--token-owner-uid", "0",
        ]
        completed = self._sessionctl(
            arguments,
            timeout_seconds=SESSIONCTL_ACK_SUBPROCESS_TIMEOUT_SECONDS)
        response = self._sessionctl_response(
            completed, "ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_INVALID")
        if completed.returncode != 0:
            _fail("ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_REQUIRED")
        replayed = _validate_terminal_ack_result(
            response, handoff, current, preliminary, require_replay=True,
            terminal_evidence_raw=terminal_evidence_raw)
        if (replayed["finalization_receipt_sha256"] !=
                stored["finalization_receipt_sha256"] or
                replayed["finalization_receipt"] !=
                stored["finalization_receipt"]):
            _fail("ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_INVALID")
        return dict(replayed)

    def owner_terminal_bearer_present(
            self, handoff: Mapping[str, Any]) -> bool:
        owner = _validate_owner_reference(handoff["session_owner_reference"])
        try:
            os.lstat(Path(owner["revoke_bearer_path"]))
        except FileNotFoundError:
            return False
        except OSError:
            _fail("ROOT_FINALIZER_OWNER_MATERIAL_INVALID")
        return True

    def prepare_owner_transition(
            self, handoff: Mapping[str, Any], *, command_id: str,
            emergency: bool, artifact_directory: Path,
            completed_at_ms: int,
            finalization: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        owner = _validate_owner_reference(handoff["session_owner_reference"])
        prior_retirement_raw: bytes | None = None
        terminal_evidence_raw: bytes | None = None
        base = [
            "/usr/bin/hepta-sessionctl", "--socket", SUPERVISOR_SOCKET,
            "--io-timeout-ms", str(SESSIONCTL_QUERY_IO_TIMEOUT_MS)]
        if emergency:
            authority_raw, bearer_raw, _token_raw = _owner_material(owner)
            if finalization is not None:
                _fail("ROOT_FINALIZER_OWNER_FINALIZATION_INVALID")
            arguments = base + [
                "recovery-query", "--token-file", owner["revoke_bearer_path"],
                "--generation", str(owner["lease_generation"]),
                "--command-id", command_id, "--token-owner-uid", "0",
                "--require-paper-finalization"]
            completed = self._sessionctl(
                arguments,
                timeout_seconds=SESSIONCTL_QUERY_SUBPROCESS_TIMEOUT_SECONDS)
            response = self._sessionctl_response(
                completed, "ROOT_FINALIZER_OWNER_RECOVERY_INVALID")
        else:
            try:
                _owner_material(owner)
            except FinalizerError:
                prior_path = artifact_directory / \
                    "durable-owner-retirement-receipt.v4.json"
                if not prior_path.exists() and not prior_path.is_symlink():
                    raise
                prior_retirement_raw = stable_read(
                    prior_path, uid=0, gid=0, mode=0o600)
            if finalization is None:
                _fail("ROOT_FINALIZER_OWNER_FINALIZATION_REQUIRED")
            terminal_evidence_raw = _current_terminal_evidence()
            finalized = _validate_owner_finalization(
                dict(finalization), handoff, command_id,
                terminal_evidence_raw=terminal_evidence_raw)
            if finalized["state"] != "ACKED":
                _fail("ROOT_FINALIZER_OWNER_FINALIZATION_REQUIRED")
        owner_sha = canonical_sha(owner)
        common: dict[str, Any] = {
            "version": VERSION if emergency else OWNER_RETIREMENT_VERSION,
            "completed_at_ms": completed_at_ms,
            "campaign_id": handoff["campaign_id"], "domain_id": DOMAIN,
            "cycle_id": handoff["cycle_id"],
            "cleanup_command_id": command_id,
            "session_owner_reference_sha256": owner_sha,
            "token_sha256": owner["token_sha256"],
            "lease_generation": owner["lease_generation"],
            "session_id": owner["session_id"],
        }
        if emergency:
            if set(response) != RECOVERY_QUERY_RESULT_FIELDS:
                _fail("ROOT_FINALIZER_OWNER_RECOVERY_INVALID")
            valid_reasons = {
                "RECOVERY_QUERY_CANNOT_FULL_FENCE",
                "RECOVERY_QUERY_PROVEN_RECOVERY_ONLY",
                "RECOVERY_QUERY_NOT_FOUND_PROVEN_RECOVERY_ONLY",
            }
            if (completed.returncode != 0 or response["accepted"] is not True or
                    response["reason_code"] not in valid_reasons or
                    response["lease_generation"] != owner["lease_generation"] or
                    response["authoritative_command_status"] is not True or
                    response["command_id"] != command_id or
                    response["command_status"] not in {
                        "accepted", "rejected", "uncertain", "not_found"} or
                    not isinstance(response["command_reason_code"], str) or
                    not response["command_reason_code"] or
                    type(response["order_id"]) is not int or
                    response["order_id"] < -1 or
                    response["recovery_only"] is not True or
                    response["paper_finalization_required"] is not True or
                    response["owner_fenced"] is not False or
                    response["owner_audit_authoritative"] is not True or
                    response["owner_audit_complete"] is not True or
                    response["execution_service_epoch"] !=
                        handoff["execution_service_epoch"] or
                    response["execution_service_fencing_generation"] !=
                        handoff["execution_service_fencing_generation"] or
                    any(type(response[field]) is not int or response[field] < 0
                        for field in (
                            "owner_active_order_count",
                            "owner_uncertain_command_count")) or
                    any(type(response[field]) is not int or response[field] < 1
                        for field in (
                            "recovery_expires_at_ms", "broker_connection_epoch",
                            "broker_active_generation",
                            "broker_terminal_generation")) or
                    response["owner_account"] != owner["owner_account"] or
                    response["owner_execution_domain"] !=
                        owner["owner_execution_domain"]):
                _fail("ROOT_FINALIZER_OWNER_RECOVERY_INVALID")
            document = {
                "schema": OWNER_RECOVERY_SCHEMA, **common,
                "query_reason_code": response["reason_code"],
                "command_status": response["command_status"],
                "command_reason_code": response["command_reason_code"],
                "order_id": response["order_id"],
                "authoritative_command_status": True, "recovery_only": True,
                "paper_finalization_required": True,
                "owner_fenced": False, "owner_audit_authoritative": True,
                "owner_audit_complete": True,
                "execution_service_epoch": response["execution_service_epoch"],
                "execution_service_fencing_generation": response[
                    "execution_service_fencing_generation"],
                "recovery_expires_at_ms": response["recovery_expires_at_ms"],
                "owner_active_order_count": response[
                    "owner_active_order_count"],
                "owner_uncertain_command_count": response[
                    "owner_uncertain_command_count"],
                "broker_connection_epoch": response["broker_connection_epoch"],
                "broker_active_generation": response["broker_active_generation"],
                "broker_terminal_generation": response[
                    "broker_terminal_generation"],
                "owner_account": response["owner_account"],
                "owner_execution_domain": response["owner_execution_domain"],
                "runtime_session_count": 0, "durable_owner_count": 1,
                "durable_owner_status": "RECOVERY_ONLY", "paper_only": True,
                "live_authorized": False, "authority_granted": False,
            }
            path = artifact_directory / "durable-recovery-owner-reference.v1.json"
        else:
            sealed_result = _validate_finalization_result(
                finalized["finalization_result"], handoff, finalized,
                expected_state="AUDIT_SEALED")
            acknowledged = _validate_terminal_ack_result(
                finalized["terminal_ack_result"], handoff, finalized,
                sealed_result, require_replay=False,
                terminal_evidence_raw=terminal_evidence_raw)
            if acknowledged["terminal_replay"] is not True:
                _fail("ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_REQUIRED")
            document = {
                "schema": OWNER_RETIREMENT_SCHEMA, **common,
                "paper_finalization_required": True,
                "recovery_id": finalized["recovery_id"],
                "finalization_id": finalized["finalization_id"],
                "expected_owner_set_sha256": finalized[
                    "expected_owner_set_sha256"],
                "expected_owner_count": finalized["expected_owner_count"],
                "owner_set_canonical_hex": finalized[
                    "owner_set_canonical_hex"],
                "owner_token_sha256": finalized["owner_token_sha256"],
                "query_command_id": finalized["query_command_id"],
                "recovery_query_result": dict(
                    finalized["recovery_query_result"]),
                "finalization_receipt_sha256": sealed_result[
                    "finalization_receipt_sha256"],
                "finalization_receipt": sealed_result[
                    "finalization_receipt"],
                "finalization_result": dict(sealed_result),
                "terminal_ack_receipt_sha256": acknowledged[
                    "finalization_receipt_sha256"],
                "terminal_ack_receipt": acknowledged[
                    "finalization_receipt"],
                "terminal_ack_result": dict(acknowledged),
                "terminal_acknowledged": True,
                "durable_hsl_audit": HSL8_TERMINAL_PROOF,
                "hsl_owner_purged": True, "broker_flat_proven": True,
                "terminal_flat_proof_kind": HSL8_TERMINAL_PROOF,
                "pre_cleanup_flat_evidence_role":
                    "DIAGNOSTIC_ONLY_NOT_TERMINAL_PROOF",
                "authority_path": owner["authority_path"],
                "authority_file_sha256": owner["authority_file_sha256"],
                "authority_body_sha256": owner["authority_body_sha256"],
                "revoke_bearer_path": owner["revoke_bearer_path"],
                "revoke_bearer_file_sha256": owner["revoke_bearer_sha256"],
                # `credentials_destroyed` is scoped explicitly: the delivered
                # mutation token and authority document are gone, while one
                # root-only replay/revoke bearer remains until outer join.
                "credentials_destroyed": True,
                "mutation_credentials_destroyed": True,
                "credentials_destroyed_scope":
                    "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY",
                "retained_root_recovery_bearer_count": 1,
                "retained_root_recovery_bearer_path":
                    owner["revoke_bearer_path"],
                "retained_root_recovery_bearer_sha256":
                    owner["revoke_bearer_sha256"],
                "retained_root_recovery_bearer_mutation_authority": False,
                "runtime_session_count": 0,
                "durable_owner_count": 0, "durable_owner_status": "RETIRED",
                "paper_only": True, "live_authorized": False,
                "authority_granted": False,
            }
            path = artifact_directory / "durable-owner-retirement-receipt.v4.json"
        document["body_sha256"] = canonical_sha(document)
        _exact(document, OWNER_RECOVERY_FIELDS if emergency else
               OWNER_RETIREMENT_FIELDS,
               "ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
        raw = canonical_json(document)
        if (prior_retirement_raw is not None and
                prior_retirement_raw != raw):
            _fail("ROOT_FINALIZER_OWNER_TRANSITION_CONFLICT")
        return {
            "path": str(path), "file_sha256": sha(raw),
            "body_sha256": document["body_sha256"],
            "durable_owner_count": 1 if emergency else 0,
            "durable_owner_status":
                "RECOVERY_ONLY" if emergency else "RETIRED",
            "document": document,
        }

    def commit_owner_transition(
            self, handoff: Mapping[str, Any], transition: Mapping[str, Any],
            *, emergency: bool) -> None:
        owner = _validate_owner_reference(handoff["session_owner_reference"])
        value = _exact(dict(transition), OWNER_TRANSITION_FIELDS,
                       "ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
        fields = OWNER_RECOVERY_FIELDS if emergency else OWNER_RETIREMENT_FIELDS
        document = _exact(
            value["document"], fields,
            "ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
        sealed(document, "ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
        if emergency:
            if (value["durable_owner_count"] != 1 or
                    value["durable_owner_status"] != "RECOVERY_ONLY" or
                    document["schema"] != OWNER_RECOVERY_SCHEMA or
                    document["version"] != VERSION):
                _fail("ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
        elif (
                value["durable_owner_count"] != 0 or
                value["durable_owner_status"] != "RETIRED" or
                document["schema"] != OWNER_RETIREMENT_SCHEMA or
                document["version"] != OWNER_RETIREMENT_VERSION or
                document["paper_finalization_required"] is not True or
                document["terminal_acknowledged"] is not True or
                document["hsl_owner_purged"] is not True or
                document["broker_flat_proven"] is not True or
                document["durable_hsl_audit"] != HSL8_TERMINAL_PROOF or
                document["terminal_flat_proof_kind"] != HSL8_TERMINAL_PROOF or
                document["pre_cleanup_flat_evidence_role"] !=
                    "DIAGNOSTIC_ONLY_NOT_TERMINAL_PROOF" or
                document["credentials_destroyed"] is not True or
                document["mutation_credentials_destroyed"] is not True or
                document["credentials_destroyed_scope"] !=
                    "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY" or
                document["retained_root_recovery_bearer_count"] != 1 or
                document["retained_root_recovery_bearer_path"] !=
                    owner["revoke_bearer_path"] or
                document["retained_root_recovery_bearer_sha256"] !=
                    owner["revoke_bearer_sha256"] or
                document[
                    "retained_root_recovery_bearer_mutation_authority"] is not
                    False):
            _fail("ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
        if not emergency:
            _retirement_finalization(document, handoff)
        if emergency:
            # The peer-delivered token is never retained past root cleanup.
            _unlink_bound(
                Path(owner["token_path"]), owner["token_sha256"],
                uid=PEER_UID, gid=PEER_GID, modes=frozenset({0o400}),
                maximum=65, allow_absent=True)
            # Recovery authority remains root-only and byte-identical; no new
            # bearer or durable owner is issued to the peer.
            _owner_material_without_delivery(owner)
            _publish_owner_transition(transition)
        else:
            # Publish the immutable HSL8 evidence before removing local owner
            # authority.  Keep only the root revoke bearer until the outer
            # completion has been durably published, so a crash always either
            # permits an exact current-runtime replay or leaves no unpublished
            # destructive work.
            _publish_owner_transition(transition)
            _unlink_bound(
                Path(owner["token_path"]), owner["token_sha256"],
                uid=PEER_UID, gid=PEER_GID, modes=frozenset({0o400}),
                maximum=65, allow_absent=True)
            intent_raw: bytes | None = None
            if OWNER_INTENT_PATH.exists() or OWNER_INTENT_PATH.is_symlink():
                intent_raw = _stable_secret(
                    OWNER_INTENT_PATH, uid=0, gid=0,
                    modes=frozenset({0o600}))
                intent = strict_json(
                    intent_raw, "ROOT_FINALIZER_OWNER_INTENT_INVALID")
                if (
                        sealed(intent, "ROOT_FINALIZER_OWNER_INTENT_INVALID") !=
                            intent.get("body_sha256") or
                        intent.get("schema") !=
                            "hepta.p1-paper-canary-owner-may-exist.v1" or
                        intent.get("campaign_id") != handoff["campaign_id"] or
                        intent.get("cycle_id") != handoff["cycle_id"] or
                        intent.get("expected_lease_generation") !=
                            owner["lease_generation"] or
                        intent.get("session_id") != owner["session_id"] or
                        intent.get("token_sha256") != owner["token_sha256"] or
                        intent.get("owner_account") != owner["owner_account"] or
                        intent.get("owner_execution_domain") !=
                            owner["owner_execution_domain"]):
                    _fail("ROOT_FINALIZER_OWNER_INTENT_INVALID")
            elif (Path(owner["authority_path"]).exists() or
                  Path(owner["authority_path"]).is_symlink()):
                _fail("ROOT_FINALIZER_OWNER_INTENT_INVALID")
            _unlink_bound(
                Path(owner["authority_path"]), owner["authority_file_sha256"],
                uid=0, gid=0, modes=frozenset({0o600}), allow_absent=True)
            if intent_raw is not None:
                _unlink_bound(
                    OWNER_INTENT_PATH, sha(intent_raw), uid=0, gid=0,
                    modes=frozenset({0o600}), allow_absent=True)
            _owner_terminal_bearer(owner)

    def fail_close(self) -> None:
        result = self.module.guardian_fail_close(
            identities_path=self.module.DEFAULT_IDENTITIES,
            drop_in_path=self.module.DEFAULT_DROP_IN)
        if result.get("mode") != "DENY_ALL":
            _fail("ROOT_FINALIZER_FAIL_CLOSE_FAILED")

    def prove_deny(self, handoff: Mapping[str, Any]) -> dict[str, Any]:
        c = self.module
        c._require_kill_switch(c.DOMAIN_KILL_SWITCH_PATH, c.PAPER_CONTROL_GID)
        c._require_kill_switch(
            c.GLOBAL_KILL_SWITCH_PATH, c.GLOBAL_PAPER_CONTROL_GID)
        broker_policy = c._require_external_p1_deny_all(
            identities_path=c.DEFAULT_IDENTITIES, command=c._run_command)
        c._require_external_p1_runtime_inactive(c._run_command)
        c._require_external_p1_residue_absent()
        for unit in (c.GUARDIAN_UNIT,):
            completed = c._run_command([
                "/usr/bin/systemctl", "show", unit,
                "-p", "LoadState", "-p", "ActiveState", "-p", "Job"])
            properties: dict[str, str] = {}
            for line in completed.stdout.splitlines():
                key, separator, item = line.partition("=")
                if not separator or key in properties:
                    _fail("ROOT_FINALIZER_RELEVANT_UNIT_ACTIVE")
                properties[key] = item
            if (completed.returncode != 0 or
                    properties != {"LoadState": "loaded",
                                   "ActiveState": "inactive", "Job": ""}):
                _fail("ROOT_FINALIZER_RELEVANT_UNIT_ACTIVE")
        active = c._runtime_document(
            c.GUARDIAN_ACTIVE_PATH, fields=c.GUARDIAN_RUNTIME_FIELDS,
            schema=c.GUARDIAN_RUNTIME_SCHEMA)
        permit = c._runtime_document(
            c.BROKER_START_PERMIT_PATH, fields=c.BROKER_START_PERMIT_FIELDS,
            schema=c.BROKER_START_PERMIT_SCHEMA)
        if active is not None or permit is not None:
            _fail("ROOT_FINALIZER_GUARDIAN_RUNTIME_PRESENT")
        owner = handoff["session_owner_reference"]
        token_path = Path(owner["token_path"])
        if token_path.exists() or token_path.is_symlink():
            _fail("ROOT_FINALIZER_RUNTIME_SESSION_PRESENT")
        status = c.status(c.DEFAULT_IDENTITIES)
        if (status.get("mode") != "DENY_ALL" or
                status.get("paper_authorized") is not False or
                status.get("live_authorized") is not False or
                status.get("identity_count") != 0 or
                status.get("effective_state_verified") is not True or
                status.get("wal_state") != "ABSENT" or
                status.get("egress_verified") is not True):
            _fail("ROOT_FINALIZER_DENY_PROOF_INVALID")
        units = list(c.EXTERNAL_P1_INERT_UNITS)
        return {
            "authorized_connector_count": 0,
            "identity_count": 0,
            "identity_manifest_sha256": status["identity_manifest_sha256"],
            "broker_policy_sha256": broker_policy,
            "runtime_session_count": 0,
            "broker_mutation_units": units,
            "broker_mutation_units_sha256": canonical_sha(units),
        }


def _load_control() -> ProductionControl:
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        _fail("ROOT_FINALIZER_CREDENTIAL_DIRECTORY_MISSING")
    path = Path(directory) / CONTROL_CREDENTIAL
    raw = stable_read(path, uid=0, gid=0, mode=0o400, maximum=4 * MAX_BYTES)
    installed = stable_read(
        CONTROL_IMAGE_PATH, uid=0, gid=0, mode=0o755,
        maximum=4 * MAX_BYTES)
    if (raw != installed or not raw.startswith(b"#!/usr/bin/env python3")):
        _fail("ROOT_FINALIZER_CONTROL_IMAGE_INVALID")
    module = ModuleType("hepta_finalizer_control")
    module.__file__ = "<systemd-credential:hepta-local-paper-control.py>"
    try:
        exec(compile(raw, module.__file__, "exec"), module.__dict__)
    except BaseException:
        _fail("ROOT_FINALIZER_CONTROL_IMAGE_INVALID")
    return ProductionControl(module)


def _engage_switch(path: Path, gid: int) -> None:
    atomic_write(path, b"engaged", uid=0, gid=gid, mode=0o440)


def _wal_document(validated: Validated, authority: Mapping[str, str],
                  now_ms: int) -> dict[str, Any]:
    transaction_id = hashlib.sha256(
        validated.request_raw + authority["local_control_transaction_id"].encode(
            "ascii")).hexdigest()[:32]
    document: dict[str, Any] = {
        "schema": WAL_SCHEMA, "version": WAL_VERSION,
        "transaction_id": transaction_id, "phase": "BEGIN",
        "created_at_ms": now_ms, "updated_at_ms": now_ms,
        "request_path": str(validated.request_path),
        "request_file_sha256": sha(validated.request_raw),
        "request_body_sha256": validated.request_body_sha256,
        "pre_cleanup_evidence_path": str(validated.evidence_path),
        "pre_cleanup_evidence_file_sha256": sha(validated.evidence_raw),
        "pre_cleanup_evidence_body_sha256": validated.evidence_body_sha256,
        "execution_handoff_path": str(validated.handoff_path),
        "execution_handoff_file_sha256": sha(validated.handoff_raw),
        "execution_handoff_body_sha256": validated.handoff_body_sha256,
        "guardian_request_id": authority["guardian_request_id"],
        "local_control_transaction_id": authority["local_control_transaction_id"],
        "local_control_request_sha256": authority["local_control_request_sha256"],
        "guardian_active_receipt_file_sha256":
            authority["guardian_active_receipt_file_sha256"],
        "guardian_active_receipt_body_sha256":
            authority["guardian_active_receipt_body_sha256"],
        "transaction_kind": (
            "EMERGENCY" if validated.request["schema"] ==
            EMERGENCY_REQUEST_SCHEMA else "NORMAL"),
        "owner_finalization": None, "owner_transition": None,
        "completion": None,
    }
    document["body_sha256"] = canonical_sha(document)
    return document


def _persist_wal(document: dict[str, Any], config: Config, phase: str,
                 now_ms: int, completion: dict[str, Any] | None = None) -> dict[str, Any]:
    updated = dict(document)
    updated["phase"] = phase
    updated["updated_at_ms"] = max(now_ms, updated["created_at_ms"])
    if completion is not None:
        updated["completion"] = completion
    updated.pop("body_sha256", None)
    updated["body_sha256"] = canonical_sha(updated)
    atomic_write(config.wal_path, canonical_json(updated), mode=0o600)
    return updated


def _persist_owner_transition(
        document: dict[str, Any], config: Config, transition: dict[str, Any],
        now_ms: int) -> dict[str, Any]:
    _exact(transition, OWNER_TRANSITION_FIELDS,
           "ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
    updated = dict(document)
    updated["phase"] = "OWNER_TRANSITION_PREPARED"
    updated["updated_at_ms"] = max(now_ms, updated["created_at_ms"])
    updated["owner_transition"] = transition
    updated.pop("body_sha256", None)
    updated["body_sha256"] = canonical_sha(updated)
    atomic_write(config.wal_path, canonical_json(updated), mode=0o600)
    return updated


def _persist_owner_finalization(
        document: dict[str, Any], config: Config,
        finalization: dict[str, Any], now_ms: int,
) -> dict[str, Any]:
    _exact(finalization, OWNER_FINALIZATION_FIELDS,
           "ROOT_FINALIZER_OWNER_FINALIZATION_INVALID")
    state = finalization.get("state")
    if state not in {"INTENT", "RECOVERY_ONLY", "AUDIT_SEALED", "ACKED"}:
        _fail("ROOT_FINALIZER_OWNER_FINALIZATION_INVALID")
    updated = dict(document)
    updated["phase"] = "OWNER_FINALIZATION_" + str(state)
    updated["updated_at_ms"] = max(now_ms, updated["created_at_ms"])
    updated["owner_finalization"] = finalization
    # Replaying HSL8 may refresh the stored current-runtime witness.  Never
    # leave a crash-recoverable WAL that pairs that refreshed ACK with an
    # older derived retirement document or completion.
    updated["owner_transition"] = None
    updated["completion"] = None
    updated.pop("body_sha256", None)
    updated["body_sha256"] = canonical_sha(updated)
    atomic_write(config.wal_path, canonical_json(updated), mode=0o600)
    return updated


def _load_wal(config: Config) -> dict[str, Any] | None:
    try:
        raw = stable_read(config.wal_path, uid=0, gid=0, mode=0o600)
    except FinalizerError:
        if not config.wal_path.exists() and not config.wal_path.is_symlink():
            return None
        raise
    value = _exact(strict_json(raw, "ROOT_FINALIZER_WAL_INVALID"), WAL_FIELDS,
                   "ROOT_FINALIZER_WAL_INVALID")
    if value["schema"] != WAL_SCHEMA or value["version"] != WAL_VERSION:
        _fail("ROOT_FINALIZER_WAL_INVALID")
    sealed(value, "ROOT_FINALIZER_WAL_INVALID")
    if value["transaction_kind"] not in {"NORMAL", "EMERGENCY"}:
        _fail("ROOT_FINALIZER_WAL_INVALID")
    if value["owner_finalization"] is not None:
        _exact(value["owner_finalization"], OWNER_FINALIZATION_FIELDS,
               "ROOT_FINALIZER_WAL_INVALID")
    if value["owner_transition"] is not None:
        _exact(value["owner_transition"], OWNER_TRANSITION_FIELDS,
               "ROOT_FINALIZER_WAL_INVALID")
    return value


def _wal_matches(wal: Mapping[str, Any], value: Validated) -> bool:
    matches = (wal["request_path"] == str(value.request_path) and
            wal["request_file_sha256"] == sha(value.request_raw) and
            wal["request_body_sha256"] == value.request_body_sha256 and
            wal["pre_cleanup_evidence_path"] == str(value.evidence_path) and
            wal["pre_cleanup_evidence_file_sha256"] == sha(value.evidence_raw) and
            wal["pre_cleanup_evidence_body_sha256"] == value.evidence_body_sha256 and
            wal["execution_handoff_path"] == str(value.handoff_path) and
            wal["execution_handoff_file_sha256"] == sha(value.handoff_raw) and
            wal["execution_handoff_body_sha256"] == value.handoff_body_sha256)
    if not matches:
        return False
    emergency = value.request["schema"] == EMERGENCY_REQUEST_SCHEMA
    if emergency:
        if wal["owner_finalization"] is not None:
            return False
        transition = wal["owner_transition"]
        if transition is not None:
            transition = _exact(
                transition, OWNER_TRANSITION_FIELDS,
                "ROOT_FINALIZER_WAL_INVALID")
            document = _exact(
                transition["document"], OWNER_RECOVERY_FIELDS,
                "ROOT_FINALIZER_WAL_INVALID")
            if (transition["durable_owner_count"] != 1 or
                    transition["durable_owner_status"] != "RECOVERY_ONLY" or
                    Path(transition["path"]) != value.handoff_path.parent /
                        "durable-recovery-owner-reference.v1.json" or
                    document["schema"] != OWNER_RECOVERY_SCHEMA or
                    document["version"] != VERSION):
                return False
            sealed(document, "ROOT_FINALIZER_WAL_INVALID")
        return True
    finalization = wal["owner_finalization"]
    if finalization is not None:
        finalization = _validate_owner_finalization(
            finalization, value.handoff,
            value.request["cleanup_command_id"],
            terminal_evidence_raw=_owner_finalization_terminal_evidence(
                finalization))
    transition = wal["owner_transition"]
    if transition is not None:
        if finalization is None or finalization["state"] != "ACKED":
            return False
        transition = _exact(
            transition, OWNER_TRANSITION_FIELDS, "ROOT_FINALIZER_WAL_INVALID")
        document = _exact(
            transition["document"], OWNER_RETIREMENT_FIELDS,
            "ROOT_FINALIZER_WAL_INVALID")
        if (transition["durable_owner_count"] != 0 or
                transition["durable_owner_status"] != "RETIRED" or
                Path(transition["path"]) != value.handoff_path.parent /
                    "durable-owner-retirement-receipt.v4.json" or
                document["schema"] != OWNER_RETIREMENT_SCHEMA or
                document["version"] != OWNER_RETIREMENT_VERSION or
                document["finalization_result"] !=
                    finalization["finalization_result"] or
                document["terminal_ack_result"] !=
                    finalization["terminal_ack_result"] or
                document["hsl_owner_purged"] is not True or
                document["broker_flat_proven"] is not True or
                document["terminal_flat_proof_kind"] != HSL8_TERMINAL_PROOF):
            return False
        sealed(document, "ROOT_FINALIZER_WAL_INVALID")
        if _retirement_finalization(document, value.handoff) != finalization:
            return False
    return True


def _receipt_path(value: Validated) -> Path:
    name = "root-emergency-cleanup-receipt.v1.json" if \
        value.request["schema"] == EMERGENCY_REQUEST_SCHEMA else \
        "root-cleanup-receipt.v4.json"
    return value.handoff_path.parent / name


def _conflicting_receipt_path(value: Validated) -> Path:
    name = "root-cleanup-receipt.v4.json" if \
        value.request["schema"] == EMERGENCY_REQUEST_SCHEMA else \
        "root-emergency-cleanup-receipt.v1.json"
    return value.handoff_path.parent / name


def _receipt(value: Validated, wal: Mapping[str, Any], proof: Mapping[str, Any],
             completed_at_ms: int) -> dict[str, Any]:
    h = value.handoff
    e = value.evidence
    r = value.request
    emergency = r["schema"] == EMERGENCY_REQUEST_SCHEMA
    transition = _exact(
        wal["owner_transition"], OWNER_TRANSITION_FIELDS,
        "ROOT_FINALIZER_OWNER_TRANSITION_MISSING")
    document: dict[str, Any] = {
        "schema": EMERGENCY_RECEIPT_SCHEMA if emergency else RECEIPT_SCHEMA,
        "version": VERSION if emergency else NORMAL_RECEIPT_VERSION,
        "status": EMERGENCY_SUCCESS_STATUS if emergency else SUCCESS_STATUS,
        "completed_at_ms": completed_at_ms, "campaign_id": r["campaign_id"],
        "domain_id": DOMAIN, "cycle_id": r["cycle_id"],
        "cleanup_tool_call_id": r["cleanup_tool_call_id"],
        "cleanup_command_id": r["cleanup_command_id"],
        "tool_descriptor_sha256": r["tool_descriptor_sha256"],
        "execution_handoff_path": str(value.handoff_path),
        "execution_handoff_file_sha256": sha(value.handoff_raw),
        "execution_handoff_body_sha256": value.handoff_body_sha256,
        "watch_handoff_file_sha256": h["watch_handoff_receipt_file_sha256"],
        "watch_handoff_body_sha256": h["watch_handoff_receipt_body_sha256"],
        "intent_sha256": h["intent_sha256"],
        "installed_images_sha256": h["installed_images_sha256"],
        "executor_image_sha256": value.images["executor"]["file_sha256"],
        "backend_adapter_image_sha256":
            value.images["backend-adapter"]["file_sha256"],
        "root_finalizer_image_sha256":
            value.images["root-finalizer"]["file_sha256"],
        "backend_transform_version": h["backend_transform_version"],
        "session_owner_reference_sha256": r["session_owner_reference_sha256"],
        "execution_service_epoch": r["execution_service_epoch"],
        "execution_service_fencing_generation":
            r["execution_service_fencing_generation"],
        "journal_path": e["journal_path"], "journal_sha256": e["journal_sha256"],
        "journal_size": e["journal_size"],
        "journal_last_sequence": e["journal_last_sequence"],
        "tool_evidence_sha256": e["tool_evidence_sha256"],
        "guardian_request_id": wal["guardian_request_id"],
        "local_control_transaction_id": wal["local_control_transaction_id"],
        "local_control_request_sha256": wal["local_control_request_sha256"],
        "guardian_active_receipt_file_sha256":
            wal["guardian_active_receipt_file_sha256"],
        "guardian_active_receipt_body_sha256":
            wal["guardian_active_receipt_body_sha256"],
        "completed_actions": (
            EMERGENCY_REQUIRED_ACTIONS if emergency else
            NORMAL_REQUIRED_ACTIONS),
        "guardian_stopped": True, "execution_control_disabled": True,
        "kill_switch_engaged": True, "global_kill_switch_engaged": True,
        "broker_deny_all": True, "broker_mutation_units_inactive": True,
        "broker_mutation_units": proof["broker_mutation_units"],
        "broker_mutation_units_sha256":
            proof["broker_mutation_units_sha256"],
        "permit_absent": True,
        "runtime_session_count": proof["runtime_session_count"],
        "guardian_runtime_absent": True,
        "durable_owner_reference_sha256": canonical_sha(
            h["session_owner_reference"]),
        "durable_owner_count": transition["durable_owner_count"],
        "durable_owner_status": transition["durable_owner_status"],
        "authorized_connector_count": proof["authorized_connector_count"],
        "identity_count": proof["identity_count"],
        "identity_manifest_sha256": proof["identity_manifest_sha256"],
        "broker_policy_sha256": proof["broker_policy_sha256"],
        "paper_only": True, "live_authorized": False,
        "authority_granted": False,
    }
    if emergency:
        document.update({
            "emergency_evidence_path": str(value.evidence_path),
            "emergency_evidence_file_sha256": sha(value.evidence_raw),
            "emergency_evidence_body_sha256": value.evidence_body_sha256,
            "root_emergency_cleanup_request_path": str(value.request_path),
            "root_emergency_cleanup_request_file_sha256": sha(value.request_raw),
            "root_emergency_cleanup_request_body_sha256":
                value.request_body_sha256,
            "recovery_reason_codes": r["recovery_reason_codes"],
            "broker_flat_proven": False, "recovery_required": True,
            "evidence_retained": True,
            "durable_recovery_owner_reference_path": transition["path"],
            "durable_recovery_owner_reference_file_sha256":
                transition["file_sha256"],
            "durable_recovery_owner_reference_body_sha256":
                transition["body_sha256"],
        })
    else:
        document.update({
            "pre_cleanup_evidence_path": str(value.evidence_path),
            "pre_cleanup_evidence_file_sha256": sha(value.evidence_raw),
            "pre_cleanup_evidence_body_sha256": value.evidence_body_sha256,
            "root_cleanup_request_path": str(value.request_path),
            "root_cleanup_request_file_sha256": sha(value.request_raw),
            "root_cleanup_request_body_sha256": value.request_body_sha256,
            "durable_owner_retirement_receipt_path": transition["path"],
            "durable_owner_retirement_receipt_file_sha256":
                transition["file_sha256"],
            "durable_owner_retirement_receipt_body_sha256":
                transition["body_sha256"],
            "mutation_credentials_destroyed": True,
            "credentials_destroyed_scope":
                "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY",
            "retained_root_recovery_bearer_count": 1,
            "retained_root_recovery_bearer_path":
                h["session_owner_reference"]["revoke_bearer_path"],
            "retained_root_recovery_bearer_sha256":
                h["session_owner_reference"]["revoke_bearer_sha256"],
            "retained_root_recovery_bearer_mutation_authority": False,
        })
    document["body_sha256"] = canonical_sha(document)
    return document


def _validate_receipt(raw: bytes, value: Validated) -> dict[str, Any]:
    emergency = value.request["schema"] == EMERGENCY_REQUEST_SCHEMA
    fields = EMERGENCY_RECEIPT_FIELDS if emergency else RECEIPT_FIELDS
    receipt = _exact(strict_json(raw, "ROOT_FINALIZER_RECEIPT_INVALID"),
                     fields, "ROOT_FINALIZER_RECEIPT_INVALID")
    r = value.request
    h = value.handoff
    if (receipt["schema"] != (
                EMERGENCY_RECEIPT_SCHEMA if emergency else RECEIPT_SCHEMA) or
            receipt["version"] != (
                VERSION if emergency else NORMAL_RECEIPT_VERSION) or
            receipt["status"] != (
                EMERGENCY_SUCCESS_STATUS if emergency else SUCCESS_STATUS) or
            receipt["campaign_id"] != r["campaign_id"] or
            receipt["domain_id"] != DOMAIN or
            receipt["cycle_id"] != r["cycle_id"] or
            receipt["cleanup_tool_call_id"] != r["cleanup_tool_call_id"] or
            receipt["cleanup_command_id"] != r["cleanup_command_id"] or
            receipt["tool_descriptor_sha256"] != r["tool_descriptor_sha256"] or
            receipt["execution_handoff_path"] != str(value.handoff_path) or
            receipt["execution_handoff_file_sha256"] != sha(value.handoff_raw) or
            receipt["execution_handoff_body_sha256"] !=
                value.handoff_body_sha256 or
            receipt["watch_handoff_file_sha256"] !=
                h["watch_handoff_receipt_file_sha256"] or
            receipt["watch_handoff_body_sha256"] !=
                h["watch_handoff_receipt_body_sha256"] or
            receipt["intent_sha256"] != h["intent_sha256"] or
            receipt["installed_images_sha256"] != h["installed_images_sha256"] or
            receipt["executor_image_sha256"] !=
                value.images["executor"]["file_sha256"] or
            receipt["backend_adapter_image_sha256"] !=
                value.images["backend-adapter"]["file_sha256"] or
            receipt["root_finalizer_image_sha256"] !=
                value.images["root-finalizer"]["file_sha256"] or
            receipt["session_owner_reference_sha256"] !=
                canonical_sha(h["session_owner_reference"]) or
            receipt["durable_owner_reference_sha256"] !=
                canonical_sha(h["session_owner_reference"]) or
            receipt["execution_service_epoch"] !=
                r["execution_service_epoch"] or
            receipt["execution_service_fencing_generation"] !=
                r["execution_service_fencing_generation"] or
            receipt["journal_path"] != value.evidence["journal_path"] or
            receipt["journal_sha256"] != value.evidence["journal_sha256"] or
            receipt["journal_size"] != value.evidence["journal_size"] or
            receipt["journal_last_sequence"] !=
                value.evidence["journal_last_sequence"] or
            receipt["tool_evidence_sha256"] !=
                value.evidence["tool_evidence_sha256"] or
            receipt["authorized_connector_count"] != 0 or
            receipt["identity_count"] != 0 or
            receipt["runtime_session_count"] != 0 or
            receipt["completed_actions"] != (
                EMERGENCY_REQUIRED_ACTIONS if emergency else
                NORMAL_REQUIRED_ACTIONS) or
            any(receipt[field] is not True for field in (
                "guardian_stopped", "execution_control_disabled",
                "kill_switch_engaged", "global_kill_switch_engaged",
                "broker_deny_all", "broker_mutation_units_inactive",
                "permit_absent", "guardian_runtime_absent")) or
            not isinstance(receipt["broker_mutation_units"], list) or
            canonical_sha(receipt["broker_mutation_units"]) !=
                receipt["broker_mutation_units_sha256"] or
            receipt["paper_only"] is not True or
            receipt["live_authorized"] is not False or
            receipt["authority_granted"] is not False):
        _fail("ROOT_FINALIZER_RECEIPT_INVALID")
    completed = _integer(
        receipt["completed_at_ms"], "ROOT_FINALIZER_RECEIPT_INVALID", 1)
    if not r["issued_at_ms"] <= completed <= r["expires_at_ms"]:
        _fail("ROOT_FINALIZER_RECEIPT_INVALID")
    for field in ("guardian_request_id", "local_control_transaction_id"):
        _identifier(receipt[field], "ROOT_FINALIZER_RECEIPT_INVALID")
    for field in (
            "local_control_request_sha256",
            "guardian_active_receipt_file_sha256",
            "guardian_active_receipt_body_sha256", "identity_manifest_sha256",
            "broker_policy_sha256"):
        _digest(receipt[field], "ROOT_FINALIZER_RECEIPT_INVALID")
    if emergency:
        if (receipt["emergency_evidence_path"] != str(value.evidence_path) or
                receipt["emergency_evidence_file_sha256"] !=
                    sha(value.evidence_raw) or
                receipt["emergency_evidence_body_sha256"] !=
                    value.evidence_body_sha256 or
                receipt["root_emergency_cleanup_request_path"] !=
                    str(value.request_path) or
                receipt["root_emergency_cleanup_request_file_sha256"] !=
                    sha(value.request_raw) or
                receipt["root_emergency_cleanup_request_body_sha256"] !=
                    value.request_body_sha256 or
                receipt["recovery_reason_codes"] !=
                    r["recovery_reason_codes"] or
                receipt["broker_flat_proven"] is not False or
                receipt["recovery_required"] is not True or
                receipt["evidence_retained"] is not True or
                receipt["durable_owner_count"] != 1 or
                receipt["durable_owner_status"] != "RECOVERY_ONLY"):
            _fail("ROOT_FINALIZER_RECEIPT_INVALID")
        owner_path = Path(receipt["durable_recovery_owner_reference_path"])
        owner_file = receipt["durable_recovery_owner_reference_file_sha256"]
        owner_body = receipt["durable_recovery_owner_reference_body_sha256"]
        owner_fields = OWNER_RECOVERY_FIELDS
    else:
        if (receipt["pre_cleanup_evidence_path"] != str(value.evidence_path) or
                receipt["pre_cleanup_evidence_file_sha256"] !=
                    sha(value.evidence_raw) or
                receipt["pre_cleanup_evidence_body_sha256"] !=
                    value.evidence_body_sha256 or
                receipt["root_cleanup_request_path"] != str(value.request_path) or
                receipt["root_cleanup_request_file_sha256"] !=
                    sha(value.request_raw) or
                receipt["root_cleanup_request_body_sha256"] !=
                    value.request_body_sha256 or
                receipt["durable_owner_count"] != 0 or
                receipt["durable_owner_status"] != "RETIRED" or
                receipt["mutation_credentials_destroyed"] is not True or
                receipt["credentials_destroyed_scope"] !=
                    "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY" or
                receipt["retained_root_recovery_bearer_count"] != 1 or
                receipt["retained_root_recovery_bearer_path"] !=
                    h["session_owner_reference"]["revoke_bearer_path"] or
                receipt["retained_root_recovery_bearer_sha256"] !=
                    h["session_owner_reference"]["revoke_bearer_sha256"] or
                receipt[
                    "retained_root_recovery_bearer_mutation_authority"] is not
                    False):
            _fail("ROOT_FINALIZER_RECEIPT_INVALID")
        owner_path = Path(receipt["durable_owner_retirement_receipt_path"])
        owner_file = receipt[
            "durable_owner_retirement_receipt_file_sha256"]
        owner_body = receipt[
            "durable_owner_retirement_receipt_body_sha256"]
        owner_fields = OWNER_RETIREMENT_FIELDS
    owner_raw = stable_read(owner_path, uid=0, gid=0, mode=0o600)
    owner_document = _exact(
        strict_json(owner_raw, "ROOT_FINALIZER_RECEIPT_INVALID"), owner_fields,
        "ROOT_FINALIZER_RECEIPT_INVALID")
    if (sha(owner_raw) != owner_file or
            sealed(owner_document, "ROOT_FINALIZER_RECEIPT_INVALID") !=
                owner_body or
            owner_document["session_owner_reference_sha256"] !=
                canonical_sha(h["session_owner_reference"]) or
            (emergency and
             (owner_path != value.handoff_path.parent /
                "durable-recovery-owner-reference.v1.json" or
              owner_document.get("schema") != OWNER_RECOVERY_SCHEMA or
              owner_document.get("version") != VERSION or
              owner_document.get("paper_finalization_required") is not True)) or
            (not emergency and
             (owner_path != value.handoff_path.parent /
                "durable-owner-retirement-receipt.v4.json" or
              owner_document.get("schema") != OWNER_RETIREMENT_SCHEMA or
              owner_document.get("version") != OWNER_RETIREMENT_VERSION or
              owner_document.get("paper_finalization_required") is not True or
              owner_document.get("terminal_acknowledged") is not
                True or
              owner_document.get("hsl_owner_purged") is not True or
              owner_document.get("broker_flat_proven") is not True or
              owner_document.get("durable_hsl_audit") != HSL8_TERMINAL_PROOF or
              owner_document.get("terminal_flat_proof_kind") !=
                HSL8_TERMINAL_PROOF or
              owner_document.get("pre_cleanup_flat_evidence_role") !=
                "DIAGNOSTIC_ONLY_NOT_TERMINAL_PROOF"))):
        _fail("ROOT_FINALIZER_RECEIPT_INVALID")
    if not emergency:
        try:
            _retirement_finalization(owner_document, h)
        except FinalizerError:
            _fail("ROOT_FINALIZER_RECEIPT_INVALID")
    sealed(receipt, "ROOT_FINALIZER_RECEIPT_INVALID")
    return receipt


def _validate_outer_purge_after_bearer_absence(
        receipt_path: Path, receipt_raw: bytes, receipt: Mapping[str, Any],
        handoff: Mapping[str, Any],
) -> None:
    failure = "ROOT_FINALIZER_OWNER_BEARER_ABSENCE_UNPROVEN"
    directory = receipt_path.parent
    purge_path = directory / "outer-owner-purge-receipt.v1.json"
    completion_path = directory / "cycle-completion-receipt.v4.json"
    intent_path = directory / "outer-owner-purge-intent.v1.json"
    if any(not (path.exists() or path.is_symlink()) for path in (
            purge_path, completion_path, intent_path)):
        _fail(failure)
    purge_raw = stable_read(purge_path, uid=0, gid=0, mode=0o600)
    purge = _exact(strict_json(purge_raw, failure), OUTER_OWNER_PURGE_FIELDS, failure)
    sealed(purge, failure)
    completion_raw = stable_read(completion_path, uid=0, gid=0, mode=0o600)
    completion = strict_json(completion_raw, failure)
    completion_body = sealed(completion, failure)
    intent_raw = stable_read(intent_path, uid=0, gid=0, mode=0o600)
    intent = _exact(
        strict_json(intent_raw, failure), OUTER_OWNER_PURGE_INTENT_FIELDS,
        failure)
    intent_body = sealed(intent, failure)
    owner = _validate_owner_reference(handoff["session_owner_reference"])
    if (
            purge["schema"] !=
                "hepta.p1-paper-canary-outer-owner-purge-receipt.v1" or
            purge["version"] != VERSION or
            purge["status"] != "OWNER_BEARER_PURGED" or
            purge["campaign_id"] != receipt["campaign_id"] or
            purge["domain_id"] != DOMAIN or
            purge["cycle_id"] != receipt["cycle_id"] or
            purge["owner_purge_intent_path"] != str(intent_path) or
            purge["owner_purge_intent_file_sha256"] != sha(intent_raw) or
            purge["owner_purge_intent_body_sha256"] != intent_body or
            purge["outer_completion_path"] != str(completion_path) or
            purge["outer_completion_file_sha256"] != sha(completion_raw) or
            purge["outer_completion_body_sha256"] != completion_body or
            completion.get("schema") !=
                "hepta.p1-paper-canary-cycle-completion-receipt.v4" or
            completion.get("version") != 4 or
            completion.get("campaign_id") != receipt["campaign_id"] or
            completion.get("cycle_id") != receipt["cycle_id"] or
            completion.get("durable_owner_status") != "RETIRED" or
            completion.get("broker_deny_all") is not True or
            completion.get("mutation_credentials_destroyed") is not True or
            completion.get("credentials_destroyed_scope") !=
                "PEER_MUTATION_TOKEN_AND_AUTHORITY_ONLY" or
            completion.get("retained_root_recovery_bearer_count") != 1 or
            completion.get("retained_root_recovery_bearer_path") !=
                owner["revoke_bearer_path"] or
            completion.get("retained_root_recovery_bearer_sha256") !=
                owner["revoke_bearer_sha256"] or
            completion.get(
                "retained_root_recovery_bearer_mutation_authority") is not
                False or
            completion.get("authority_granted") is not False or
            intent["schema"] !=
                "hepta.p1-paper-canary-outer-owner-purge-intent.v1" or
            intent["version"] != VERSION or
            intent["status"] != "CURRENT_RUNTIME_REPLAY_VERIFIED" or
            intent["campaign_id"] != receipt["campaign_id"] or
            intent["domain_id"] != DOMAIN or
            intent["cycle_id"] != receipt["cycle_id"] or
            intent["outer_completion_path"] != str(completion_path) or
            intent["outer_completion_file_sha256"] != sha(completion_raw) or
            intent["outer_completion_body_sha256"] != completion_body or
            intent["root_cleanup_receipt_path"] != str(receipt_path) or
            intent["root_cleanup_receipt_file_sha256"] != sha(receipt_raw) or
            intent["root_cleanup_receipt_body_sha256"] !=
                receipt["body_sha256"] or
            intent["owner_retirement_receipt_path"] !=
                receipt["durable_owner_retirement_receipt_path"] or
            intent["owner_retirement_receipt_file_sha256"] !=
                receipt["durable_owner_retirement_receipt_file_sha256"] or
            intent["owner_retirement_receipt_body_sha256"] !=
                receipt["durable_owner_retirement_receipt_body_sha256"] or
            intent["revoke_bearer_path"] != owner["revoke_bearer_path"] or
            intent["revoke_bearer_file_sha256"] !=
                owner["revoke_bearer_sha256"] or
            intent["current_runtime_replay_verified"] is not True or
            intent["paper_only"] is not True or
            intent["live_authorized"] is not False or
            intent["authority_granted"] is not False or
            purge["root_cleanup_receipt_path"] != str(receipt_path) or
            purge["root_cleanup_receipt_file_sha256"] != sha(receipt_raw) or
            purge["root_cleanup_receipt_body_sha256"] !=
                receipt["body_sha256"] or
            purge["owner_retirement_receipt_path"] !=
                receipt["durable_owner_retirement_receipt_path"] or
            purge["owner_retirement_receipt_file_sha256"] !=
                receipt["durable_owner_retirement_receipt_file_sha256"] or
            purge["owner_retirement_receipt_body_sha256"] !=
                receipt["durable_owner_retirement_receipt_body_sha256"] or
            purge["terminal_ack_receipt_sha256"] !=
                intent["terminal_ack_receipt_sha256"] or
            purge["revoke_bearer_file_sha256"] !=
                intent["revoke_bearer_file_sha256"] or
            purge["owner_bearer_purged"] is not True or
            purge["durable_owner_credential_count"] != 0 or
            purge["paper_only"] is not True or
            purge["live_authorized"] is not False or
            purge["authority_granted"] is not False):
        _fail(failure)
    try:
        residue = list(OWNER_AUTHORITY_ROOT.iterdir())
    except FileNotFoundError:
        residue = []
    except OSError:
        _fail(failure)
    if residue:
        _fail(failure)


class _Lock:
    def __init__(self, path: Path):
        self.path = path
        self.fd = -1

    def __enter__(self) -> "_Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.fd = os.open(
            self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.fchmod(self.fd, 0o600)
        os.fchown(self.fd, 0, 0)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = -1
            _fail("ROOT_FINALIZER_BUSY")
        return self

    def __exit__(self, *_args: Any) -> None:
        if self.fd >= 0:
            os.close(self.fd)


def finalize(raw: bytes, *, control: Control, config: Config = Config(),
             now_ms: int | None = None,
             peer_pid: int | None = None,
             peer_credentials: tuple[int, int, int] | None = None,
             crash_hook: Callable[[str], None] = lambda _phase: None) -> bytes:
    timestamp = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    with _Lock(config.lock_path):
        request_hint = strict_json(raw, "ROOT_FINALIZER_REQUEST_INVALID")
        schema = request_hint.get("schema")
        if schema not in {REQUEST_SCHEMA, EMERGENCY_REQUEST_SCHEMA}:
            _fail("ROOT_FINALIZER_REQUEST_SCHEMA_INVALID")
        campaign = request_hint.get("campaign_id")
        cycle = request_hint.get("cycle_id")
        if not isinstance(campaign, str) or not isinstance(cycle, str):
            _fail("ROOT_FINALIZER_REQUEST_IDENTITY_INVALID")
        directory = _control_cycle_dir(config, campaign, cycle)
        receipt_hint = directory / (
            "root-emergency-cleanup-receipt.v1.json" if
            schema == EMERGENCY_REQUEST_SCHEMA else
            "root-cleanup-receipt.v4.json")
        wal = _load_wal(config)
        idempotent = (receipt_hint.exists() or receipt_hint.is_symlink() or
                      wal is not None)
        validator = validate_emergency_request if \
            schema == EMERGENCY_REQUEST_SCHEMA else validate_request
        value = validator(
            raw, config=config, now_ms=timestamp, require_fresh=not idempotent)
        if peer_pid is not None and peer_credentials is not None:
            _fail("ROOT_FINALIZER_PEER_CREDENTIAL_INVALID")
        if peer_credentials is not None:
            attest_requester(peer_credentials, value)
        elif peer_pid is not None:
            # Compatibility for direct callers.  Socket activation always
            # supplies the complete SO_PEERCRED tuple.
            attest_requester((peer_pid, PEER_UID, PEER_GID), value)
        receipt_path = _receipt_path(value)
        conflict_path = _conflicting_receipt_path(value)
        legacy_normal_paths = (
            value.handoff_path.parent / "root-cleanup-receipt.v1.json",
            value.handoff_path.parent / "root-cleanup-receipt.v2.json",
            value.handoff_path.parent / "root-cleanup-receipt.v3.json",
            value.handoff_path.parent /
                "durable-owner-retirement-receipt.v1.json",
            value.handoff_path.parent /
                "durable-owner-retirement-receipt.v2.json",
            value.handoff_path.parent /
                "durable-owner-retirement-receipt.v3.json",
        )
        if (any(path.exists() or path.is_symlink()
                for path in legacy_normal_paths) or
                any(path.exists() or path.is_symlink()
                    for path in (
                        LEGACY_WAL_PATHS if config.wal_path == WAL_PATH else
                        ()))):
            _fail("ROOT_FINALIZER_LEGACY_NORMAL_ARTIFACT_PRESENT")
        if conflict_path.exists() or conflict_path.is_symlink():
            _fail("ROOT_FINALIZER_TERMINAL_MODE_CONFLICT")
        transaction_kind = (
            "EMERGENCY" if schema == EMERGENCY_REQUEST_SCHEMA else "NORMAL")
        if wal is not None and (
                wal["transaction_kind"] != transaction_kind or
                not _wal_matches(wal, value)):
            _fail("ROOT_FINALIZER_WAL_REQUEST_CONFLICT")
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt_raw = stable_read(receipt_path, uid=0, gid=0, mode=0o600)
            receipt = _validate_receipt(receipt_raw, value)
            if transaction_kind == "NORMAL":
                if control.owner_terminal_bearer_present(value.handoff):
                    # The inner receipt is durable before the coordinator may
                    # publish its outer completion.  The finalizer WAL is
                    # therefore allowed to be gone while the root-only revoke
                    # bearer is deliberately retained.  Reconstruct the exact
                    # ACK identity from the immutable retirement receipt and
                    # replay it against the current runtime; never require a
                    # stale inner WAL merely to answer an idempotent retry.
                    retirement_raw = stable_read(
                        Path(receipt[
                            "durable_owner_retirement_receipt_path"]),
                        uid=0, gid=0, mode=0o600)
                    retirement = _exact(
                        strict_json(
                            retirement_raw,
                            "ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_REQUIRED"),
                        OWNER_RETIREMENT_FIELDS,
                        "ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_REQUIRED")
                    if (
                            sha(retirement_raw) != receipt[
                                "durable_owner_retirement_receipt_file_sha256"] or
                            sealed(
                                retirement,
                                "ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_REQUIRED") !=
                            receipt[
                                "durable_owner_retirement_receipt_body_sha256"]):
                        _fail("ROOT_FINALIZER_OWNER_TERMINAL_REPLAY_REQUIRED")
                    finalization = _retirement_finalization(
                        retirement, value.handoff)
                    control.verify_owner_terminal_ack(
                        value.handoff, finalization)
                else:
                    # Bare absence is never accepted as terminal evidence.
                    # Only the outer coordinator's durable completion-bound
                    # purge transaction may explain why the retained bearer is
                    # gone.
                    _validate_outer_purge_after_bearer_absence(
                        receipt_path, receipt_raw, receipt, value.handoff)
            proof = control.prove_deny(value.handoff)
            if (proof.get("authorized_connector_count") != 0 or
                    proof.get("identity_count") != 0 or
                    proof.get("runtime_session_count") != 0 or
                    receipt["broker_mutation_units"] !=
                        proof.get("broker_mutation_units") or
                    receipt["broker_mutation_units_sha256"] !=
                        proof.get("broker_mutation_units_sha256")):
                _fail("ROOT_FINALIZER_DENY_PROOF_INVALID")
            if wal is not None:
                config.wal_path.unlink()
                _fsync_directory(config.wal_path.parent)
            return receipt_raw
        if wal is None:
            authority = control.capture_authority(value.handoff)
            wal = _wal_document(value, authority, timestamp)
            atomic_write(config.wal_path, canonical_json(wal), mode=0o600,
                         exclusive=True)
            crash_hook("WAL_PERSISTED")
        emergency = transaction_kind == "EMERGENCY"
        if not emergency:
            finalization = wal["owner_finalization"]
            if finalization is None:
                finalization = _owner_finalization_intent(
                    value.handoff, value.request["cleanup_command_id"])
                _validate_owner_finalization(
                    finalization, value.handoff,
                    value.request["cleanup_command_id"],
                    terminal_evidence_raw=_owner_finalization_terminal_evidence(
                        finalization))
                wal = _persist_owner_finalization(
                    wal, config, finalization, timestamp)
                crash_hook("OWNER_FINALIZATION_INTENT")
            transitions = {
                "INTENT": ("RECOVERY_ONLY", "OWNER_RECOVERY_ONLY"),
                "RECOVERY_ONLY": (
                    "AUDIT_SEALED", "OWNER_FINALIZATION_SEALED"),
                "AUDIT_SEALED": ("ACKED", "OWNER_FINALIZATION_ACKED"),
            }
            while finalization["state"] != "ACKED":
                previous_state = finalization["state"]
                expected_state, hook = transitions[previous_state]
                advanced = control.advance_owner_finalization(
                    value.handoff, finalization)
                finalization = _validate_owner_finalization(
                    advanced, value.handoff,
                    value.request["cleanup_command_id"],
                    terminal_evidence_raw=_owner_finalization_terminal_evidence(
                        advanced))
                if finalization["state"] != expected_state:
                    _fail("ROOT_FINALIZER_OWNER_FINALIZATION_SEQUENCE_INVALID")
                wal = _persist_owner_finalization(
                    wal, config, finalization, timestamp)
                crash_hook(hook)
        if not emergency:
            prior_transition = wal["owner_transition"]
            transition_completed_at = timestamp
            if prior_transition is not None:
                prior_transition = _exact(
                    prior_transition, OWNER_TRANSITION_FIELDS,
                    "ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
                prior_document = prior_transition.get("document")
                if not isinstance(prior_document, dict):
                    _fail("ROOT_FINALIZER_OWNER_TRANSITION_INVALID")
                transition_completed_at = _integer(
                    prior_document.get("completed_at_ms"),
                    "ROOT_FINALIZER_OWNER_TRANSITION_INVALID", 1)
            # A local ACKED/WAL checkpoint is never sufficient.  Every resume
            # that can still stop the replay-capable runtime, publish the outer
            # completion, or purge the retained bearer must replay the exact
            # terminal receipt against the current execution runtime first.
            replayed = control.verify_owner_terminal_ack(
                value.handoff, finalization)
            updated_finalization = dict(finalization)
            updated_finalization["terminal_ack_result"] = dict(replayed)
            finalization = _validate_owner_finalization(
                updated_finalization, value.handoff,
                value.request["cleanup_command_id"],
                terminal_evidence_raw=_owner_finalization_terminal_evidence(
                    updated_finalization))
            wal = _persist_owner_finalization(
                wal, config, finalization, timestamp)
            crash_hook("OWNER_TERMINAL_RUNTIME_VERIFIED")
        if not emergency or wal["owner_transition"] is None:
            transition = control.prepare_owner_transition(
                value.handoff,
                command_id=value.request["cleanup_command_id"],
                emergency=emergency,
                artifact_directory=value.handoff_path.parent,
                completed_at_ms=(
                    timestamp if emergency else transition_completed_at),
                finalization=None if emergency else finalization)
            wal = _persist_owner_transition(wal, config, transition, timestamp)
            crash_hook("OWNER_TRANSITION_PREPARED")
        wal = _persist_wal(wal, config, "BEFORE_STOP_GUARDIAN", timestamp)
        crash_hook("BEFORE_STOP_GUARDIAN")
        control.stop_guardian()
        wal = _persist_wal(wal, config, "GUARDIAN_STOPPED", timestamp)
        crash_hook("GUARDIAN_STOPPED")
        control.fail_close()
        wal = _persist_wal(wal, config, "EXECUTION_CONTROL_DISABLED", timestamp)
        crash_hook("EXECUTION_CONTROL_DISABLED")
        # These paths and gids are fixed by the local control contract.
        module = getattr(control, "module", None)
        if module is not None:
            _engage_switch(module.DOMAIN_KILL_SWITCH_PATH, module.PAPER_CONTROL_GID)
            _engage_switch(
                module.GLOBAL_KILL_SWITCH_PATH, module.GLOBAL_PAPER_CONTROL_GID)
        else:
            engage = getattr(control, "engage_kill_switches", None)
            if not callable(engage):
                _fail("ROOT_FINALIZER_KILL_SWITCH_INTERFACE_INVALID")
            engage()
        wal = _persist_wal(wal, config, "KILL_SWITCHES_ENGAGED", timestamp)
        crash_hook("KILL_SWITCHES_ENGAGED")
        control.fail_close()
        wal = _persist_wal(wal, config, "DENY_ALL_ENFORCED", timestamp)
        crash_hook("DENY_ALL_ENFORCED")
        transition = _exact(
            wal["owner_transition"], OWNER_TRANSITION_FIELDS,
            "ROOT_FINALIZER_OWNER_TRANSITION_MISSING")
        control.commit_owner_transition(
            value.handoff, transition, emergency=emergency)
        wal = _persist_wal(wal, config, "OWNER_TRANSITION_COMMITTED", timestamp)
        crash_hook("OWNER_TRANSITION_COMMITTED")
        proof = control.prove_deny(value.handoff)
        if (proof.get("authorized_connector_count") != 0 or
                proof.get("identity_count") != 0 or
                proof.get("runtime_session_count") != 0 or
                proof.get("broker_mutation_units_sha256") !=
                    canonical_sha(proof.get("broker_mutation_units"))):
            _fail("ROOT_FINALIZER_DENY_PROOF_INVALID")
        completed_at = max(timestamp, time.time_ns() // 1_000_000)
        if completed_at > value.request["expires_at_ms"]:
            _fail("ROOT_FINALIZER_COMPLETION_EXPIRED")
        completion = _receipt(value, wal, proof, completed_at)
        wal = _persist_wal(
            wal, config, "PROOF_READY", completed_at, completion=completion)
        crash_hook("PROOF_READY")
        receipt_raw = canonical_json(completion)
        if conflict_path.exists() or conflict_path.is_symlink():
            _fail("ROOT_FINALIZER_TERMINAL_MODE_CONFLICT")
        atomic_write(receipt_path, receipt_raw, mode=0o600, exclusive=True)
        reopened = stable_read(receipt_path, uid=0, gid=0, mode=0o600)
        if reopened != receipt_raw:
            _fail("ROOT_FINALIZER_RECEIPT_REOPEN_MISMATCH")
        _validate_receipt(reopened, value)
        # Normal cleanup destroys the delivered mutation token and authority
        # document, but intentionally retains the root revoke bearer.  Only
        # the coordinator may purge that final recovery credential after its
        # outer cycle-completion receipt is durable.
        crash_hook("RECEIPT_DURABLE_OWNER_BEARER_RETAINED")
        wal = _persist_wal(
            wal, config, "RECEIPT_PUBLISHED", completed_at,
            completion=completion)
        crash_hook("RECEIPT_PUBLISHED")
        config.wal_path.unlink()
        _fsync_directory(config.wal_path.parent)
        crash_hook("WAL_CLEARED")
        return reopened


def _peer(fd: int) -> tuple[int, int, int]:
    try:
        connection = socket.socket(fileno=fd)
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        pid, uid, gid = struct.unpack("3i", raw)
    except (OSError, struct.error):
        _fail("ROOT_FINALIZER_PEER_CREDENTIAL_INVALID")
    if pid <= 1 or (uid, gid) not in {(PEER_UID, PEER_GID), (0, 0)}:
        _fail("ROOT_FINALIZER_PEER_CREDENTIAL_INVALID")
    try:
        cgroup = Path(f"/proc/{pid}/cgroup").read_text(
            encoding="ascii", errors="strict")
    except (OSError, UnicodeError):
        _fail("ROOT_FINALIZER_PEER_CGROUP_INVALID")
    expected_cgroup = PEER_CGROUP if uid == PEER_UID else \
        ROOT_COORDINATOR_CGROUP
    if cgroup != f"0::{expected_cgroup}\n":
        _fail("ROOT_FINALIZER_PEER_CGROUP_INVALID")
    return pid, uid, gid


def _proc_bytes(path: Path, maximum: int) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError:
        _fail("ROOT_FINALIZER_PEER_PROCESS_INVALID")
    if not raw or len(raw) > maximum:
        _fail("ROOT_FINALIZER_PEER_PROCESS_INVALID")
    return raw


def _systemctl_properties(
        unit: str, names: tuple[str, ...], *,
        command: Callable[..., subprocess.CompletedProcess[str]],
        reason: str) -> dict[str, str]:
    try:
        completed = command(
            ["/usr/bin/systemctl", "show", unit,
             *[item for name in names for item in ("-p", name)]],
            check=False, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=15)
    except (OSError, subprocess.SubprocessError):
        _fail(reason)
    properties: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, item = line.partition("=")
        if not separator or key in properties or key not in names:
            _fail(reason)
        properties[key] = item
    if completed.returncode != 0 or set(properties) != set(names):
        _fail(reason)
    return properties


def _attest_process_identity(
        pid: int, *, uid: int, gid: int, cgroup: str,
        cmdline: bytes) -> None:
    cgroup_raw = _proc_bytes(Path(f"/proc/{pid}/cgroup"), 64 * 1024)
    if cgroup_raw != f"0::{cgroup}\n".encode("ascii"):
        _fail("ROOT_FINALIZER_PEER_CGROUP_INVALID")
    status_raw = _proc_bytes(Path(f"/proc/{pid}/status"), 64 * 1024)
    try:
        status_text = status_raw.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        _fail("ROOT_FINALIZER_PEER_PROCESS_INVALID")
    selected: dict[str, str] = {}
    for line in status_text.splitlines():
        key, separator, item = line.partition(":")
        if key in {"Uid", "Gid", "Groups"}:
            if not separator or key in selected:
                _fail("ROOT_FINALIZER_PEER_PROCESS_INVALID")
            selected[key] = item.strip()
    exact_uid = f"{uid}\t{uid}\t{uid}\t{uid}"
    exact_gid = f"{gid}\t{gid}\t{gid}\t{gid}"
    if selected != {"Uid": exact_uid, "Gid": exact_gid,
                    "Groups": str(gid)}:
        _fail("ROOT_FINALIZER_PEER_PROCESS_INVALID")
    if _proc_bytes(Path(f"/proc/{pid}/cmdline"), 4096) != cmdline:
        _fail("ROOT_FINALIZER_PEER_PROCESS_INVALID")


def _bound_image(value: Validated, role: str, path: str) -> dict[str, Any]:
    image = value.images.get(role)
    if (not isinstance(image, dict) or set(image) != IMAGE_FIELDS or
            image.get("role") != role or image.get("path") != path or
            image.get("mode") != 0o755 or image.get("uid") != 0 or
            image.get("gid") != 0 or image.get("nlink") != 1):
        _fail("ROOT_FINALIZER_PEER_IMAGE_INVALID")
    _digest(image.get("file_sha256"), "ROOT_FINALIZER_PEER_IMAGE_INVALID")
    return image


def _attest_credential(
        pid: int, credential_path: str, image: Mapping[str, Any], *,
        uid: int, gid: int) -> None:
    raw = stable_read(
        Path(f"/proc/{pid}/root{credential_path}"), uid=uid, gid=gid,
        mode=0o400, maximum=4 * MAX_BYTES)
    if sha(raw) != image["file_sha256"]:
        _fail("ROOT_FINALIZER_PEER_IMAGE_INVALID")


def _attest_executor_peer(
        pid: int, value: Validated, *,
        command: Callable[..., subprocess.CompletedProcess[str]]) -> None:
    properties = _systemctl_properties(
        PEER_UNIT,
        ("LoadState", "MainPID", "ControlPID", "User", "Group",
         "ActiveState", "SubState", "ControlGroup"),
        command=command, reason="ROOT_FINALIZER_PEER_UNIT_INVALID")
    if properties != {
            "LoadState": "loaded", "MainPID": str(pid), "ControlPID": "0",
            "User": PEER_USER, "Group": PEER_USER,
            "ActiveState": "active", "SubState": "running",
            "ControlGroup": PEER_CGROUP}:
        _fail("ROOT_FINALIZER_PEER_UNIT_INVALID")
    expected_cmdline = b"\0".join((
        b"/usr/bin/python3.12", b"-I", b"-S",
        PEER_CREDENTIAL_IMAGE.encode("ascii"))) + b"\0"
    _attest_process_identity(
        pid, uid=PEER_UID, gid=PEER_GID, cgroup=PEER_CGROUP,
        cmdline=expected_cmdline)
    image = _bound_image(value, "executor", PEER_IMAGE_PATH)
    _attest_credential(
        pid, PEER_CREDENTIAL_IMAGE, image, uid=PEER_UID, gid=PEER_GID)


def _attest_root_emergency_peer(
        pid: int, value: Validated, *,
        command: Callable[..., subprocess.CompletedProcess[str]]) -> None:
    coordinator = _bound_image(
        value, "root-coordinator", ROOT_COORDINATOR_IMAGE_PATH)
    closer = _bound_image(
        value, "crash-emergency-closer", ROOT_EMERGENCY_CLOSER_IMAGE_PATH)
    _bound_image(value, "terminal-prover", ROOT_TERMINAL_PROVER_IMAGE_PATH)
    properties = _systemctl_properties(
        ROOT_COORDINATOR_UNIT,
        ("LoadState", "ActiveState", "SubState", "MainPID", "ControlPID",
         "ControlGroup"), command=command,
        reason="ROOT_FINALIZER_PEER_UNIT_INVALID")
    main = {
        "LoadState": "loaded", "ActiveState": "active",
        "SubState": "running", "MainPID": str(pid), "ControlPID": "0",
        "ControlGroup": ROOT_COORDINATOR_CGROUP,
    }
    stop_post = {
        "LoadState": "loaded", "ActiveState": "deactivating",
        "SubState": "stop-post", "MainPID": "0", "ControlPID": str(pid),
        "ControlGroup": ROOT_COORDINATOR_CGROUP,
    }
    if properties == main:
        expected_cmdline = b"\0".join((
            b"/usr/bin/python3.12", b"-I", b"-S",
            ROOT_COORDINATOR_CREDENTIAL_IMAGE.encode("ascii"),
            b"--service-run")) + b"\0"
        _attest_process_identity(
            pid, uid=0, gid=0, cgroup=ROOT_COORDINATOR_CGROUP,
            cmdline=expected_cmdline)
        _attest_credential(
            pid, ROOT_COORDINATOR_CREDENTIAL_IMAGE, coordinator, uid=0, gid=0)
        _attest_credential(
            pid, ROOT_EMERGENCY_CLOSER_CREDENTIAL_IMAGE, closer, uid=0, gid=0)
        return
    if properties == stop_post:
        expected_cmdline = b"\0".join((
            b"/usr/bin/python3.12", b"-I", b"-S",
            ROOT_EMERGENCY_CLOSER_CREDENTIAL_IMAGE.encode("ascii"),
            b"--exec-stop-post")) + b"\0"
        _attest_process_identity(
            pid, uid=0, gid=0, cgroup=ROOT_COORDINATOR_CGROUP,
            cmdline=expected_cmdline)
        _attest_credential(
            pid, ROOT_EMERGENCY_CLOSER_CREDENTIAL_IMAGE, closer, uid=0, gid=0)
        return
    _fail("ROOT_FINALIZER_PEER_UNIT_INVALID")


def attest_requester(
        peer: tuple[int, int, int], value: Validated,
        command: Callable[..., subprocess.CompletedProcess[str]] =
            subprocess.run) -> None:
    pid, uid, gid = peer
    if pid <= 1 or (uid, gid) not in {(PEER_UID, PEER_GID), (0, 0)}:
        _fail("ROOT_FINALIZER_PEER_CREDENTIAL_INVALID")
    if (uid, gid) == (PEER_UID, PEER_GID):
        _attest_executor_peer(pid, value, command=command)
        return
    if value.request.get("schema") != EMERGENCY_REQUEST_SCHEMA:
        _fail("ROOT_FINALIZER_PEER_CREDENTIAL_INVALID")
    _attest_root_emergency_peer(pid, value, command=command)


def attest_peer(pid: int, value: Validated,
                command: Callable[..., subprocess.CompletedProcess[str]] =
                    subprocess.run) -> None:
    """Compatibility wrapper for the unprivileged executor requester."""
    attest_requester((pid, PEER_UID, PEER_GID), value, command=command)


def _read_one(fd: int) -> bytes:
    payload = bytearray()
    while len(payload) <= MAX_BYTES:
        chunk = os.read(fd, min(65536, MAX_BYTES + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
    if len(payload) > MAX_BYTES or not payload.endswith(b"\n"):
        _fail("ROOT_FINALIZER_WIRE_INVALID")
    # strict_json below also rejects concatenated objects and extra bytes.
    return bytes(payload)


def _write_all(fd: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        written = os.write(fd, raw[offset:])
        if written <= 0:
            _fail("ROOT_FINALIZER_RESPONSE_WRITE_FAILED")
        offset += written


def _error(reason: str) -> bytes:
    document: dict[str, Any] = {
        "schema": ERROR_SCHEMA, "version": VERSION, "status": "ERROR",
        "reason_code": reason if REASON.fullmatch(reason) else
            "ROOT_FINALIZER_INTERNAL_ERROR",
        "paper_only": True, "live_authorized": False,
        "authority_granted": False,
    }
    document["body_sha256"] = canonical_sha(document)
    return canonical_json(document)


def serve() -> int:
    try:
        if os.geteuid() != 0 or os.getegid() != 0:
            _fail("ROOT_FINALIZER_ROOT_REQUIRED")
        peer = _peer(0)
        request = _read_one(0)
        response = finalize(
            request, control=_load_control(), peer_credentials=peer)
        _write_all(1, response)
        return 0
    except FinalizerError as error:
        try:
            _write_all(1, _error(error.reason))
        except (FinalizerError, OSError):
            pass
        return 1
    except BaseException:
        try:
            _write_all(1, _error("ROOT_FINALIZER_INTERNAL_ERROR"))
        except (FinalizerError, OSError):
            pass
        return 1


def fail_close_on_exit(control: Control | None = None) -> int:
    """Request-independent monotonic cleanup for the service stop path."""
    try:
        if os.geteuid() != 0 or os.getegid() != 0:
            _fail("ROOT_FINALIZER_ROOT_REQUIRED")
        selected = _load_control() if control is None else control
        module = getattr(selected, "module", None)
        if module is not None:
            _engage_switch(
                module.DOMAIN_KILL_SWITCH_PATH, module.PAPER_CONTROL_GID)
            _engage_switch(
                module.GLOBAL_KILL_SWITCH_PATH,
                module.GLOBAL_PAPER_CONTROL_GID)
        else:
            engage = getattr(selected, "engage_kill_switches", None)
            if not callable(engage):
                _fail("ROOT_FINALIZER_KILL_SWITCH_INTERFACE_INVALID")
            engage()
        selected.stop_guardian()
        selected.fail_close()
        if module is not None:
            module._require_kill_switch(
                module.DOMAIN_KILL_SWITCH_PATH, module.PAPER_CONTROL_GID)
            module._require_kill_switch(
                module.GLOBAL_KILL_SWITCH_PATH,
                module.GLOBAL_PAPER_CONTROL_GID)
            module._require_external_p1_deny_all(
                identities_path=module.DEFAULT_IDENTITIES,
                command=module._run_command)
            status = module.status(module.DEFAULT_IDENTITIES)
            if (status.get("mode") != "DENY_ALL" or
                    status.get("paper_authorized") is not False or
                    status.get("live_authorized") is not False or
                    status.get("identity_count") != 0 or
                    status.get("effective_state_verified") is not True or
                    status.get("egress_verified") is not True):
                _fail("ROOT_FINALIZER_EXIT_DENY_PROOF_INVALID")
        else:
            prove = getattr(selected, "prove_exit_deny", None)
            if not callable(prove) or prove() is not True:
                _fail("ROOT_FINALIZER_EXIT_DENY_PROOF_INVALID")
        return 0
    except BaseException:
        return 1


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["serve"]:
        return serve()
    if arguments == ["fail-close-on-exit"]:
        return fail_close_on_exit()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
