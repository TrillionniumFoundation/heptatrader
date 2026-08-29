#!/usr/bin/env python3
"""Run bounded, evidence-settled PAPER FX repair and acceptance cycles."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import ctypes
import datetime as dt
from decimal import Decimal, InvalidOperation
import errno
import fcntl
from importlib.machinery import SourceFileLoader
import importlib.util
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys
import time
from types import ModuleType
import uuid


_LIBC = ctypes.CDLL(None, use_errno=True)
_RENAME_NOREPLACE = 1


REPOSITORY = Path(__file__).resolve().parents[1]
INSTALLED_AGENT_SOURCE = Path("/usr/libexec/hepta-local-ai-paper-agent")
AGENT_SOURCE = (
    INSTALLED_AGENT_SOURCE if INSTALLED_AGENT_SOURCE.exists()
    else REPOSITORY / "scripts/hepta_local_ai_paper_agent.py")
AGENT_ENV = Path("/etc/heptatrader/local-ai-paper-agent.env")
TOKEN_FILE = Path("/run/hepta-agent-alpha/sessions/local-paper.token")
RISK_RECOVERY_TOKEN_ROOT = TOKEN_FILE.parent
SUPERVISOR_SOCKET = "/run/hepta-tool-gateway-alpha/session-supervisor.sock"
TOOL_SOCKET = "/run/hepta-agent-alpha/tools.sock"
AGENT_USER = "hepta-agent-alpha"
OPENCLAW = "/home/qian-qi/.npm-global/bin/openclaw"
OPENCLAW_SESSION_ROOT = Path(
    "/home/qian-qi/.openclaw/agents/telegram-bot-8681289317/sessions")
RATE_WINDOW_SECONDS = 62.0
END_FLAT_RECEIPT_ROOT = Path("/var/lib/hepta-local-ai-paper-agent")
SESSION_AUTHORITY_ROOT = END_FLAT_RECEIPT_ROOT / "session-authority"
END_FLAT_LOCK = END_FLAT_RECEIPT_ROOT / "end-flat.lock"
CAMPAIGN_LIFECYCLE_LOCK = (
    END_FLAT_RECEIPT_ROOT / "safe-recovery-guard.lock")
AGENT_STATE = END_FLAT_RECEIPT_ROOT / "state.json"
AGENT_STATE_SNAPSHOT_MAX_BYTES = 1_048_576
STRATEGY_ACCEPTANCE_STATE = (
    END_FLAT_RECEIPT_ROOT / "strategy-acceptance-state.json")
STRATEGY_ACCEPTANCE_INTENT_SCHEMA = (
    "hepta.local-ai-paper-strategy-acceptance-intent.v1")
STRATEGY_ACCEPTANCE_RECEIPT_SCHEMA = (
    "hepta.local-ai-paper-strategy-acceptance-receipt.v1")
START_PERMIT_PENDING = END_FLAT_RECEIPT_ROOT / "start-permit.pending.json"
START_PERMIT_CLAIMED = END_FLAT_RECEIPT_ROOT / "start-permit.claimed.json"
START_PERMIT_CONSUMED = END_FLAT_RECEIPT_ROOT / "start-permit.consumed.json"
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
RISK_RECOVERY_LOCK = END_FLAT_RECEIPT_ROOT / "risk-recovery.lock"
BROKER_MUTATION_LOCK = END_FLAT_RECEIPT_ROOT / "broker-mutation.lock"
SAFETY_LATCH = END_FLAT_RECEIPT_ROOT / "safety-stop.pending.json"
AUTOMATIC_RISK_ATTEMPT = (
    END_FLAT_RECEIPT_ROOT / "safe-recovery-automatic-risk-attempt.json")
CAMPAIGN_POLICY = Path("/etc/heptatrader/paper-campaigns/alpha.json")
ACTIVE_POLICY_SCHEMA = "hepta.ib-paper-campaign-policy.v5"
ACTIVE_POLICY_MAX_CYCLES = 720
ACTIVE_POLICY_MAX_DURATION_MS = 24 * 60 * 60 * 1000
EXTERNAL_P1_POLICY_DURATION_MS = 5 * 60 * 1000
RECOVERY_POLICY_SCHEMAS = frozenset({
    "hepta.ib-paper-campaign-policy.v4",
    ACTIVE_POLICY_SCHEMA,
})
LOCAL_PAPER_CONTROL = "/usr/libexec/hepta-local-paper-control"
LOCAL_PAPER_CONTROL_STATE_ROOT = END_FLAT_RECEIPT_ROOT
EXTERNAL_RECOVERY_AUTHORITY = (
    LOCAL_PAPER_CONTROL_STATE_ROOT /
    "local-paper-control-recovery-authority.json")
EXTERNAL_RECOVERY_COMPLETION = (
    LOCAL_PAPER_CONTROL_STATE_ROOT /
    "local-paper-control-recovery-completion.json")
EXTERNAL_RECOVERY_AUTHORITY_SCHEMA = (
    "hepta.local-paper-control-recovery-authority.v1")
EXTERNAL_RECOVERY_AUTHORITY_STATUS = "REDUCE_ONLY_RECOVERY_REQUIRED"
EXTERNAL_RECOVERY_COMPLETION_SCHEMA = (
    "hepta.local-paper-control-recovery-completion.v4")
EXTERNAL_RECOVERY_COMPLETION_STATUS = (
    "REDUCE_ONLY_RECOVERY_TERMINAL_FLAT")
EXTERNAL_RECOVERY_TERMINAL_FLAT_SCHEMA = (
    "hepta.local-ai-paper-external-recovery-terminal-flat.v4")
EXTERNAL_RECOVERY_TERMINAL_FLAT_STATUS = (
    "EXTERNAL_RECOVERY_TERMINAL_FLAT")
EXTERNAL_RECOVERY_CHECKPOINT_SCHEMA = (
    "hepta.local-ai-paper-external-recovery-checkpoint.v4")
EXTERNAL_TERMINAL_EVIDENCE_PATH = Path(
    "/run/hepta/paper-terminal-witness/alpha/terminal-evidence.v1")
# HSL7 AUDIT_SEALED is deliberately only a preliminary fence/audit receipt.
# It is never sufficient to publish terminal-flat or a recovery completion.
EXTERNAL_PRELIMINARY_FINALIZATION_STDOUT_FIELDS = frozenset({
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
EXTERNAL_TERMINAL_ACK_STDOUT_FIELDS = frozenset({
    *EXTERNAL_PRELIMINARY_FINALIZATION_STDOUT_FIELDS,
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
EXTERNAL_PRELIMINARY_FINALIZATION_RECEIPT_KEYS = (
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
# The HSL8 terminal ACK receipt is emitted only after root has cut off the
# original mutation transport and an isolated, credential-free committer has
# durably bound a fresh signed post-cutoff account witness. Its ordered text
# wire is part of the safety contract; the opaque vendor callback queue is not
# represented as locally drained by this proof kind.
EXTERNAL_TERMINAL_ACK_RECEIPT_KEYS = (
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
# HPE1 is the independently produced, root-owned stable account witness.
# Keep this wire contract local to the repair validator: importing the
# privileged producer at runtime would make the validator's trust boundary
# depend on whichever source happens to be installed.  Every receipt field
# below (apart from its own schema/version/status and the HPE1 file digest)
# must be equal to the corresponding HPE1 value before an ACK is accepted.
EXTERNAL_TERMINAL_EVIDENCE_KEYS = (
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
EXTERNAL_RECOVERY_REFERENCE_SPECS = {
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
EXTERNAL_RECOVERY_REQUIRED_UNITS = (
    "hepta-ib-paper-domain-preflight@alpha.service",
    "hepta-execution-ib-paper@alpha.service",
    "hepta-tool-gateway@alpha.socket",
    "hepta-tool-session-supervisor@alpha.socket",
    "hepta-tool-gateway@alpha.service",
)
EXTERNAL_RECOVERY_FORBIDDEN_UNITS = (
    "hepta-local-ai-paper-agent.service",
    "hepta-ib-paper-campaign-operator@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.socket",
    "hepta-local-paper-session-renew.timer",
    "hepta-local-paper-session-renew.service",
    "hepta-local-paper-supervisor.timer",
    "hepta-local-paper-supervisor.service",
    "hepta-local-ai-paper-24h-stop.timer",
    "hepta-local-ai-paper-24h-stop.service",
    "hepta-local-ai-paper-24h-stop-retry.timer",
    "hepta-local-ai-paper-24h-stop-retry.service",
    "hepta-local-ai-paper-end-flat-retry.timer",
    "hepta-local-ai-paper-end-flat-retry.service",
)
SUPERVISOR_LEASE_STORE = Path(
    "/var/lib/hepta-tool-gateway-alpha/session-leases.hsl2")
SUPERVISOR_LEASE_KEY = Path(
    "/etc/heptatrader/credentials/trust-domains/alpha/"
    "hepta-supervisor-lease.key")
SUPERVISOR_LEASE_AAD = b"HeptaTrader supervisor lease store HSL2"
EXTERNAL_CANARY_ROOT = Path("/var/lib/hepta/p1-paper-canary")
EXTERNAL_CANARY_CONTROL_ROOT = Path("/var/lib/hepta/p1-paper-canary-control")
EXTERNAL_P1_RUNTIME_PROFILE = Path(
    "/etc/heptatrader/trust-domains/alpha.ib-paper.env")
EXTERNAL_P1_RUNTIME_PROFILE_BYTES = 851
EXTERNAL_P1_RUNTIME_PROFILE_SHA256 = (
    "sha256:7d8395db4f04d65310eb7ec8c87d4aae84b0ba259b4dc40196d7e19af3ed9e02")
EXTERNAL_P1_RUNTIME_PROFILE_KEYS = (
    "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY",
    "HEPTA_EXECUTION_MAX_ORDER_NOTIONAL",
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
EXTERNAL_CANONICAL_DECIMAL = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?")
EXTERNAL_BACKEND_TRANSFORM_VERSION = (
    "hepta.p1-paper-canary-backend-transform.v1")
EXTERNAL_CANARY_RECOVERY_SCHEMA = (
    "hepta.p1-paper-canary-recovery-record.v1")
EXTERNAL_CANARY_RECOVERY_STATUS = "SEALED_RECOVERY_REQUIRED"
EXTERNAL_CANARY_HANDOFF_SCHEMA = (
    "hepta.p1-paper-canary-execution-handoff.v1")
EXTERNAL_CANARY_RECOVERY_FIELDS = frozenset({
    "schema", "version", "status", "created_at_ms",
    "handoff_file_sha256", "handoff_body_sha256", "bindings_sha256",
    "installed_images_sha256", "runtime_profile_sha256",
    "backend_transform_version",
    "campaign_id", "domain_id", "policy_sha256", "strategy_sha256",
    "decision_sha256", "cycle_id", "intent_sha256", "journal_path",
    "journal_file_sha256", "tool_evidence_sha256",
    "last_authoritative_snapshot_sha256", "session_owner_reference",
    "last_completed_phase", "uncertain_phase", "uncertain_tool_call_id",
    "place_attempted", "place_call_id", "close_attempted",
    "owned_order_id_sha256", "service_epoch", "fencing_generation",
    "authoritative_state", "cleanup_complete", "recovery_required",
    "reason_codes", "authority_granted", "body_sha256",
})
EXTERNAL_CANARY_OWNER_FIELDS = frozenset({
    "token_name", "token_path", "authority_path", "authority_file_sha256",
    "authority_body_sha256", "lease_generation", "session_id", "peer_uid",
    "peer_gid", "token_sha256", "revoke_bearer_path",
    "revoke_bearer_sha256",
})
EXTERNAL_EMERGENCY_EVIDENCE_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-evidence.v1")
EXTERNAL_EMERGENCY_REQUEST_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-request.v1")
EXTERNAL_EMERGENCY_RECEIPT_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-receipt.v1")
EXTERNAL_DURABLE_RECOVERY_OWNER_SCHEMA = (
    "hepta.p1-paper-canary-durable-recovery-owner-reference.v1")
EXTERNAL_EMERGENCY_EVIDENCE_FIELDS = frozenset({
    "authority_granted", "backend_adapter_image_sha256",
    "backend_transform_version", "body_sha256", "broker_flat_proven",
    "campaign_id", "close_attempted", "close_outcome", "created_at_ms",
    "cycle_closed", "cycle_id", "cycle_opened", "domain_id",
    "execution_service_epoch", "execution_service_fencing_generation",
    "executor_image_sha256", "handoff_body_sha256", "handoff_file_sha256",
    "handoff_path", "installed_images_sha256", "intent_sha256",
    "journal_last_sequence", "journal_path", "journal_sha256",
    "journal_size", "last_completed_phase", "last_known_state",
    "last_known_state_sha256", "place_attempted", "recovery_reason_codes",
    "root_finalizer_image_sha256", "schema",
    "session_owner_reference_sha256", "tool_evidence_sha256",
    "uncertain_phase", "uncertain_tool_call_id", "uncertainty_kind",
    "version",
})
EXTERNAL_EMERGENCY_REQUEST_FIELDS = frozenset({
    "authority_granted", "body_sha256", "broker_flat_proven", "campaign_id",
    "cleanup_command_id", "cleanup_tool_call_id", "cycle_id", "domain_id",
    "emergency_evidence_body_sha256", "emergency_evidence_file_sha256",
    "emergency_evidence_path", "execution_service_epoch",
    "execution_service_fencing_generation", "expires_at_ms",
    "handoff_body_sha256", "handoff_file_sha256", "issued_at_ms",
    "live_authorized", "paper_only", "recovery_reason_codes",
    "required_actions", "schema", "session_owner_reference_sha256",
    "tool_descriptor_sha256", "version",
})
EXTERNAL_EMERGENCY_RECEIPT_FIELDS = frozenset({
    "authority_granted", "authorized_connector_count",
    "backend_adapter_image_sha256", "backend_transform_version",
    "body_sha256", "broker_deny_all", "broker_flat_proven",
    "broker_mutation_units", "broker_mutation_units_inactive",
    "broker_mutation_units_sha256", "broker_policy_sha256", "campaign_id",
    "cleanup_command_id", "cleanup_tool_call_id", "completed_actions",
    "completed_at_ms", "cycle_id", "domain_id", "durable_owner_count",
    "durable_owner_reference_sha256", "durable_owner_status",
    "durable_recovery_owner_reference_body_sha256",
    "durable_recovery_owner_reference_file_sha256",
    "durable_recovery_owner_reference_path", "emergency_evidence_body_sha256",
    "emergency_evidence_file_sha256", "emergency_evidence_path",
    "evidence_retained", "execution_control_disabled",
    "execution_handoff_body_sha256", "execution_handoff_file_sha256",
    "execution_handoff_path", "execution_service_epoch",
    "execution_service_fencing_generation", "executor_image_sha256",
    "global_kill_switch_engaged", "guardian_active_receipt_body_sha256",
    "guardian_active_receipt_file_sha256", "guardian_request_id",
    "guardian_runtime_absent", "guardian_stopped", "identity_count",
    "identity_manifest_sha256", "installed_images_sha256", "intent_sha256",
    "journal_last_sequence", "journal_path", "journal_sha256",
    "journal_size", "kill_switch_engaged", "live_authorized",
    "local_control_request_sha256", "local_control_transaction_id",
    "paper_only", "permit_absent", "recovery_reason_codes",
    "recovery_required", "root_emergency_cleanup_request_body_sha256",
    "root_emergency_cleanup_request_file_sha256",
    "root_emergency_cleanup_request_path", "root_finalizer_image_sha256",
    "runtime_session_count", "schema", "session_owner_reference_sha256",
    "status", "tool_descriptor_sha256", "tool_evidence_sha256", "version",
    "watch_handoff_body_sha256", "watch_handoff_file_sha256",
})
EXTERNAL_DURABLE_RECOVERY_OWNER_FIELDS = frozenset({
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
EXTERNAL_ROOT_CLEANUP_ACTIONS = (
    "STOP_GUARDIAN", "DISABLE_EXECUTION_CONTROL", "ENGAGE_KILL_SWITCH",
    "ENFORCE_DENY_ALL", "PROVE_CONNECTOR_ZERO",
)
EXTERNAL_BROKER_MUTATION_UNITS = (
    "hepta-p1-watch-activation-reconcile.timer",
    "hepta-p1-watch-activation-reconcile.service",
    "hepta-p1-watch-activation.service",
    "hepta-shadow-watch-collector@alpha.timer",
    "hepta-shadow-watch-collector@alpha.service",
    "hepta-shadow-watch-export@alpha.service",
    "hepta-shadow-watch-custodian-reconcile@alpha.timer",
    "hepta-shadow-watch-custodian-reconcile@alpha.service",
    "hepta-shadow-watch-custodian@alpha.service",
    "hepta-tool-gateway@alpha.service", "hepta-tool-gateway@alpha.socket",
    "hepta-tool-session-supervisor@alpha.socket",
    "hepta-execution-simulator@alpha.service",
    "hepta-execution-simulator@alpha.socket",
    "hepta-execution-events-simulator@alpha.socket",
    "hepta-execution-ib-paper@alpha.service",
    "hepta-execution-ib-paper@alpha.socket",
    "hepta-execution-events-ib-paper@alpha.socket",
    "hepta-execution-ib-paper.service", "hepta-execution-ib-paper.socket",
    "hepta-execution-events-ib-paper.socket",
    "hepta-ib-paper-domain-preflight@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.socket",
    "hepta-local-ai-paper-agent.service",
)
SAFE_RECOVERY_TIMER = "hepta-local-paper-safe-recover.timer"
SAFE_RECOVERY_SERVICE = "hepta-local-paper-safe-recover.service"
SESSION_RENEW_TIMER = "hepta-local-paper-session-renew.timer"
SESSION_RENEW_SERVICE = "hepta-local-paper-session-renew.service"
SUPERVISOR_TIMER = "hepta-local-paper-supervisor.timer"
SUPERVISOR_SERVICE = "hepta-local-paper-supervisor.service"
PERSISTENT_STOP_TIMER = "hepta-local-ai-paper-24h-stop.timer"
END_FLAT_RETRY_TIMER = "hepta-local-ai-paper-end-flat-retry.timer"
AGENT_SERVICE = "hepta-local-ai-paper-agent.service"
CAMPAIGN_TIMER_UNITS = (
    SAFE_RECOVERY_TIMER,
    SESSION_RENEW_TIMER,
    SUPERVISOR_TIMER,
    PERSISTENT_STOP_TIMER,
    END_FLAT_RETRY_TIMER,
)
MONOTONIC_CAMPAIGN_TIMER_UNITS = frozenset(
    unit for unit in CAMPAIGN_TIMER_UNITS if unit != PERSISTENT_STOP_TIMER)
CAMPAIGN_TIMER_SERVICES = (
    SAFE_RECOVERY_SERVICE,
    SESSION_RENEW_SERVICE,
    SUPERVISOR_SERVICE,
    "hepta-local-ai-paper-24h-stop.service",
)
END_FLAT_EXECUTION_UNITS = (
    "hepta-ib-paper-campaign-operator@alpha.socket",
    "hepta-execution-ib-paper@alpha.service",
    "hepta-execution-ib-paper@alpha.socket",
    "hepta-execution-events-ib-paper@alpha.socket",
    "hepta-ib-paper-domain-preflight@alpha.service",
)
END_FLAT_TOOL_UNITS = (
    "hepta-tool-gateway@alpha.service",
    "hepta-tool-gateway@alpha.socket",
    "hepta-tool-session-supervisor@alpha.socket",
)
END_FLAT_RUNTIME_SOCKETS = (
    Path(TOOL_SOCKET),
    Path(SUPERVISOR_SOCKET),
)
# ``systemctl start`` only waits for the service job to be queued for a
# ``Type=simple`` execution unit.  Keep the end-flat handoff bounded while
# allowing the three required units to finish that asynchronous transition.
# The readiness check remains a predicate over every required unit; this is
# only a wait, not a relaxation of the later control/status verification.
END_FLAT_RUNTIME_READY_TIMEOUT_SECONDS = 20.0
END_FLAT_RUNTIME_READY_POLL_SECONDS = 0.2
# A systemd ``ActiveState=active`` only means that a Type=simple process has
# been spawned.  The gateway's authenticated execution endpoint is published
# later (after its event-feed readiness barrier), so session selection must not
# race that publication.  Keep this a separate, bounded budget: it is an
# application-level readiness predicate, not an extension of the unit wait.
END_FLAT_AUTHENTICATED_READY_TIMEOUT_SECONDS = 30.0
END_FLAT_AUTHENTICATED_READY_POLL_SECONDS = 0.2
# The supervisor can publish its socket before the execution/event recovery
# barrier is ready.  A revoke issued in that handoff window must not turn a
# known, retryable transport/readiness observation into permanent authority
# residue.  Keep this budget separate from authenticated tool discovery: it
# retries the exact root bearer and generation only, and still fails closed
# when the bounded window expires.
SESSION_REVOKE_RETRY_TIMEOUT_SECONDS = 8.0
SESSION_REVOKE_RETRY_POLL_SECONDS = 0.2
SESSION_REVOKE_TRANSPORT_RETRY_REASONS = frozenset({
    "SUPERVISOR_SOCKET_CREATE_FAILED",
    "SUPERVISOR_SOCKET_CONNECT_FAILED",
    "SUPERVISOR_FRAME_WRITE_TIMEOUT",
    "SUPERVISOR_FRAME_HEADER_TIMEOUT",
    "SUPERVISOR_FRAME_BODY_TIMEOUT",
    "connect failed",
    "read failed",
    "response read failed",
})
SESSION_REVOKE_TRANSIENT_AUDIT_REASONS = frozenset({
    "IB_RECOVERY_AUDIT_NEW_CONNECTION_EPOCH_REQUIRED",
    "RECOVERY_OWNER_BROKER_BARRIER_INCOMPLETE",
    "EXECUTION_EVENT_SERVICE_NOT_READY",
    "EXECUTION_SERVICE_NOT_READY",
    "EXECUTION_SERVICE_EPOCH_CHANGED",
    "EXECUTION_SERVICE_CONNECT_FAILED",
    "EXECUTION_SERVICE_READ_FAILED",
    "EXECUTION_SERVICE_RESPONSE_READ_FAILED",
    "connect failed",
    "read failed",
    "response read failed",
})
# A failed ``rearm-stack`` can leave a freshly prepared policy enabled while
# no authority was ever granted.  These are the admission-side timers which
# must be fenced before that policy is handed to the ordinary end-flat path.
# The end-flat retry timer is deliberately not in this set: the durable
# request marker and this timer are the crash hand-off which finishes the
# terminal receipt if the synchronous hand-off is interrupted.
PREPARED_ABORT_TIMER_UNITS = (
    SAFE_RECOVERY_TIMER,
    SAFE_RECOVERY_SERVICE,
    SESSION_RENEW_TIMER,
    SESSION_RENEW_SERVICE,
    SUPERVISOR_TIMER,
    SUPERVISOR_SERVICE,
    PERSISTENT_STOP_TIMER,
    "hepta-local-ai-paper-24h-stop.service",
    AGENT_SERVICE,
)
PERSISTENT_STOP_TIMER_PATH = Path(
    "/etc/systemd/system/hepta-local-ai-paper-24h-stop.timer")
AUTH_PROFILE_ID_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:@+-]{2,255}")
AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN = re.compile(
    r"sha256:[0-9a-f]{64}")
AUTH_PROFILE_ALLOWLIST_ENV = (
    "HEPTA_LOCAL_AI_AUTH_PROFILE_ALLOWLIST_SHA256")


class _CampaignLifecycleLocks:
    """Serialize every admission mutation with recovery and end-flat."""

    def __init__(self) -> None:
        self.descriptors: list[int] = []

    def __enter__(self) -> "_CampaignLifecycleLocks":
        try:
            for path, failure in (
                    (CAMPAIGN_LIFECYCLE_LOCK,
                     "CAMPAIGN_LIFECYCLE_LOCK_UNSAFE"),
                    (RISK_RECOVERY_LOCK, "RISK_RECOVERY_LOCK_UNSAFE"),
                    (END_FLAT_LOCK, "END_FLAT_LOCK_UNSAFE")):
                descriptor = os.open(
                    path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
                    getattr(os, "O_NOFOLLOW", 0), 0o600)
                self.descriptors.append(descriptor)
                metadata = os.fstat(descriptor)
                if (not stat.S_ISREG(metadata.st_mode) or
                        metadata.st_nlink != 1 or metadata.st_uid != 0 or
                        metadata.st_gid != 0 or
                        stat.S_IMODE(metadata.st_mode) != 0o600):
                    raise RuntimeError(failure)
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


def _campaign_lifecycle_locks() -> _CampaignLifecycleLocks:
    return _CampaignLifecycleLocks()


class _BrokerMutationLock:
    """Coordinate root renewal with the Agent's place/settle critical section."""

    def __init__(self, *, blocking: bool) -> None:
        self.blocking = blocking
        self.descriptor: int | None = None
        self.acquired = False

    def __enter__(self) -> "_BrokerMutationLock":
        descriptor = os.open(
            BROKER_MUTATION_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            0o600)
        try:
            metadata = os.fstat(descriptor)
            if (not stat.S_ISREG(metadata.st_mode) or
                    metadata.st_nlink != 1 or metadata.st_uid != 0 or
                    metadata.st_gid != 0 or
                    stat.S_IMODE(metadata.st_mode) != 0o600):
                raise RuntimeError("BROKER_MUTATION_LOCK_UNSAFE")
            operation = fcntl.LOCK_EX
            if not self.blocking:
                operation |= fcntl.LOCK_NB
            try:
                fcntl.flock(descriptor, operation)
            except BlockingIOError:
                os.close(descriptor)
                return self
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        self.descriptor = descriptor
        self.acquired = True
        return self

    def __exit__(self, *_unused: object) -> None:
        descriptor = self.descriptor
        self.descriptor = None
        self.acquired = False
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _broker_mutation_lock(*, blocking: bool) -> _BrokerMutationLock:
    return _BrokerMutationLock(blocking=blocking)


def _automatic_risk_recovery_consumed() -> bool:
    """Validate the durable one-shot marker used only by timer recovery."""
    try:
        metadata = os.lstat(AUTOMATIC_RISK_ATTEMPT)
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("RISK_RECOVERY_AUTOMATIC_ATTEMPT_PATH_UNSAFE")
    try:
        value = json.loads(
            AUTOMATIC_RISK_ATTEMPT.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("RISK_RECOVERY_AUTOMATIC_ATTEMPT_INVALID") \
            from error
    if (not isinstance(value, dict) or
            value.get("schema") !=
                "hepta.local-paper-automatic-risk-attempt.v1" or
            value.get("automatic_attempt_consumed") is not True or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            not isinstance(value.get("attempted_at_ms"), int) or
            isinstance(value.get("attempted_at_ms"), bool) or
            value.get("attempted_at_ms", 0) <= 0 or
            not isinstance(value.get("state_sha256"), str) or
            not re.fullmatch(r"sha256:[0-9a-f]{64}",
                             value.get("state_sha256", ""))):
        raise RuntimeError("RISK_RECOVERY_AUTOMATIC_ATTEMPT_INVALID")
    return True


def _consume_automatic_risk_recovery_attempt(
        state: dict[str, object], mutation: str) -> None:
    """Durably consume the timer's one attempt just before broker mutation.

    All session selection, daemon identity checks, and authoritative reads run
    before this function.  Once the marker is fsynced a lost mutation response
    cannot make a later timer invocation repeat cancel/flatten automatically.
    Explicit operator recovery never calls this function.
    """
    if mutation not in {"cancel", "flatten"}:
        raise RuntimeError("RISK_RECOVERY_AUTOMATIC_MUTATION_INVALID")
    if _automatic_risk_recovery_consumed():
        raise RuntimeError(
            "RISK_RECOVERY_AUTOMATIC_ATTEMPT_ALREADY_CONSUMED")
    state_raw = (json.dumps(
        state, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False) + "\n").encode("ascii")
    marker = {
        "schema": "hepta.local-paper-automatic-risk-attempt.v1",
        "attempted_at_ms": time.time_ns() // 1_000_000,
        "state_sha256": "sha256:" + hashlib.sha256(state_raw).hexdigest(),
        "suspension_id": state.get("suspension_id"),
        "first_mutation": mutation,
        "automatic_attempt_consumed": True,
        "paper_only": True,
        "live_authorized": False,
    }
    try:
        _create_root_json_exclusive(AUTOMATIC_RISK_ATTEMPT, marker)
    except FileExistsError as error:
        if _automatic_risk_recovery_consumed():
            raise RuntimeError(
                "RISK_RECOVERY_AUTOMATIC_ATTEMPT_ALREADY_CONSUMED") \
                from error
        raise RuntimeError(
            "RISK_RECOVERY_AUTOMATIC_ATTEMPT_RACE_UNSAFE") from error


def session_lease_path(token_file: Path = TOKEN_FILE) -> Path:
    return token_file.with_name(token_file.name + ".lease.json")


def session_provision_intent_path(token_file: Path = TOKEN_FILE) -> Path:
    return SESSION_AUTHORITY_ROOT / (
        token_file.name + ".authority.json")


def session_authority_bearer_path(token_file: Path = TOKEN_FILE) -> Path:
    return SESSION_AUTHORITY_ROOT / (
        token_file.name + ".revoke-token")


def _ensure_session_authority_root() -> None:
    SESSION_AUTHORITY_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = os.lstat(SESSION_AUTHORITY_ROOT)
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or
            metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700):
        raise RuntimeError("REPAIR_SESSION_AUTHORITY_ROOT_UNSAFE")


def write_session_lease(
        token_file: Path, lease_generation: int, ttl_seconds: int,
        session_name: str, *, observed_at_ms: int | None = None,
        expires_at_ms: int | None = None) -> None:
    if (not isinstance(lease_generation, int) or
            isinstance(lease_generation, bool) or lease_generation < 1 or
            not isinstance(ttl_seconds, int) or
            isinstance(ttl_seconds, bool) or
            not 300 <= ttl_seconds <= 86_400):
        raise RuntimeError("REPAIR_SESSION_GENERATION_INVALID")
    token_sha256 = "sha256:" + hashlib.sha256(token_file.read_bytes()).hexdigest()
    if observed_at_ms is None:
        observed_at_ms = time.time_ns() // 1_000_000
    if expires_at_ms is None:
        expires_at_ms = observed_at_ms + ttl_seconds * 1000
    if (not isinstance(observed_at_ms, int) or
            isinstance(observed_at_ms, bool) or observed_at_ms <= 0 or
            not isinstance(expires_at_ms, int) or
            isinstance(expires_at_ms, bool) or
            expires_at_ms <= observed_at_ms or
            expires_at_ms > observed_at_ms + ttl_seconds * 1000):
        raise RuntimeError("REPAIR_SESSION_LEASE_TIME_INVALID")
    payload = {
        "schema": "hepta.local-paper-session-lease.v1",
        "session_name": session_name,
        "lease_generation": lease_generation,
        "ttl_seconds": ttl_seconds,
        "observed_at_ms": observed_at_ms,
        "expires_at_ms": expires_at_ms,
        "token_sha256": token_sha256,
    }
    destination = session_lease_path(token_file)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        rendered = (json.dumps(
            payload, ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
        offset = 0
        while offset < len(rendered):
            written = os.write(descriptor, rendered[offset:])
            if written <= 0:
                raise RuntimeError("REPAIR_SESSION_LEASE_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, destination)
    directory = os.open(
        destination.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _load_session_provision_intent(
        token_file: Path,
) -> dict[str, object] | None:
    path = session_provision_intent_path(token_file)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("REPAIR_SESSION_PROVISION_INTENT_PATH_UNSAFE")
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("REPAIR_SESSION_PROVISION_INTENT_INVALID") \
            from error
    phases = {
        "TOKEN_PENDING", "CALL_PENDING", "ACTIVE", "RENEW_PENDING",
        "REVOKE_PENDING", "REVOKED",
    }
    generation = value.get("lease_generation") \
        if isinstance(value, dict) else None
    if (not isinstance(value, dict) or
            value.get("schema") !=
                "hepta.local-paper-session-provision-intent.v1" or
            value.get("token_name") != token_file.name or
            value.get("authority_bearer_name") !=
                session_authority_bearer_path(token_file).name or
            not isinstance(value.get("token_sha256"), str) or
            not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                str(value.get("token_sha256"))) or
            value.get("phase") not in phases or
            value.get("expected_lease_generation") != 1 or
            not isinstance(value.get("session_name"), str) or
            not value.get("session_name") or
            not isinstance(value.get("session_id"), str) or
            not value.get("session_id") or
            not isinstance(value.get("peer_uid"), int) or
            isinstance(value.get("peer_uid"), bool) or
            value.get("peer_uid", -1) < 0 or
            not isinstance(value.get("ttl_seconds"), int) or
            isinstance(value.get("ttl_seconds"), bool) or
            not 300 <= value.get("ttl_seconds", 0) <= 86_400 or
            not isinstance(value.get("created_at_ms"), int) or
            isinstance(value.get("created_at_ms"), bool) or
            value.get("created_at_ms", 0) <= 0 or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            (value.get("phase") in {
                "ACTIVE", "RENEW_PENDING", "REVOKE_PENDING", "REVOKED"} and
             (not isinstance(generation, int) or
              isinstance(generation, bool) or generation < 1)) or
            (generation is not None and
             (not isinstance(generation, int) or
              isinstance(generation, bool)))):
        raise RuntimeError("REPAIR_SESSION_PROVISION_INTENT_INVALID")
    if value.get("phase") in {"ACTIVE", "RENEW_PENDING"}:
        expires_at_ms = value.get("expires_at_ms")
        if (not isinstance(expires_at_ms, int) or
                isinstance(expires_at_ms, bool) or expires_at_ms <= 0):
            raise RuntimeError("REPAIR_SESSION_PROVISION_INTENT_INVALID")
    if value.get("phase") == "REVOKED":
        if (value.get("revoke_outcome") not in {
                "ACCEPTED", "ALREADY_ABSENT"} or
                not isinstance(value.get("revoked_at_ms"), int) or
                isinstance(value.get("revoked_at_ms"), bool) or
                value.get("revoked_at_ms", 0) <= 0):
            raise RuntimeError("REPAIR_SESSION_PROVISION_INTENT_INVALID")
    if value.get("phase") == "RENEW_PENDING":
        renew_from = value.get("renew_from_generation")
        renew_candidate = value.get("renew_candidate_generation")
        if (not isinstance(renew_from, int) or
                isinstance(renew_from, bool) or renew_from < 1 or
                value.get("lease_generation") != renew_from or
                not isinstance(renew_candidate, int) or
                isinstance(renew_candidate, bool) or
                renew_candidate != renew_from + 1 or
                not isinstance(value.get("renew_started_at_ms"), int) or
                isinstance(value.get("renew_started_at_ms"), bool) or
                value.get("renew_started_at_ms", 0) <= 0):
            raise RuntimeError("REPAIR_SESSION_PROVISION_INTENT_INVALID")
    return value


def _session_authority_bearer_matches(
        token_file: Path, intent: dict[str, object], *,
        allow_missing: bool = False,
) -> os.stat_result | None:
    bearer = session_authority_bearer_path(token_file)
    try:
        metadata = os.lstat(bearer)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise RuntimeError("REPAIR_SESSION_AUTHORITY_BEARER_MISSING")
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_size != 65 or
            "sha256:" + hashlib.sha256(bearer.read_bytes()).hexdigest() !=
                intent.get("token_sha256")):
        raise RuntimeError("REPAIR_SESSION_AUTHORITY_BEARER_CHANGED")
    return metadata


def _session_provision_material_matches(
        token_file: Path, intent: dict[str, object], *,
        allow_missing: bool = False,
) -> os.stat_result | None:
    try:
        metadata = os.lstat(token_file)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise RuntimeError("REPAIR_SESSION_PROVISION_TOKEN_MISSING")
    identity = pwd.getpwnam(AGENT_USER)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid not in {0, identity.pw_uid} or
            metadata.st_gid not in {0, identity.pw_gid} or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_size != 65 or
            "sha256:" + hashlib.sha256(token_file.read_bytes()).hexdigest() !=
                intent.get("token_sha256")):
        raise RuntimeError("REPAIR_SESSION_PROVISION_TOKEN_CHANGED")
    return metadata


def _cleanup_resolved_session_provision(
        token_file: Path, intent: dict[str, object]) -> None:
    if intent.get("phase") != "REVOKED":
        raise RuntimeError("REPAIR_SESSION_PROVISION_NOT_REVOKED")
    _session_provision_material_matches(
        token_file, intent, allow_missing=True)
    _session_authority_bearer_matches(
        token_file, intent, allow_missing=True)
    lease_file = session_lease_path(token_file)
    if lease_file.exists() or lease_file.is_symlink():
        metadata = os.lstat(lease_file)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise RuntimeError("REPAIR_SESSION_PROVISION_LEASE_CHANGED")
        try:
            lease = json.loads(lease_file.read_text(encoding="ascii"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "REPAIR_SESSION_PROVISION_LEASE_CHANGED") from error
        if (not isinstance(lease, dict) or
                lease.get("schema") !=
                    "hepta.local-paper-session-lease.v1" or
                lease.get("token_sha256") != intent.get("token_sha256") or
                lease.get("lease_generation") not in {
                    intent.get("lease_generation"),
                    intent.get("renew_from_generation"),
                }):
            raise RuntimeError("REPAIR_SESSION_PROVISION_LEASE_CHANGED")
        os.unlink(lease_file)
    try:
        os.unlink(token_file)
    except FileNotFoundError:
        pass
    try:
        os.unlink(session_authority_bearer_path(token_file))
    except FileNotFoundError:
        pass
    intent_file = session_provision_intent_path(token_file)
    metadata = os.lstat(intent_file)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("REPAIR_SESSION_PROVISION_INTENT_PATH_UNSAFE")
    os.unlink(intent_file)
    parents = [token_file.parent]
    if intent_file.parent != token_file.parent:
        parents.append(intent_file.parent)
    for parent in parents:
        directory = os.open(
            parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _session_revoke_transport_retryable(
        completed: subprocess.CompletedProcess[str],
) -> bool:
    """Return whether sessionctl failed at the supervisor transport boundary.

    ``hepta-sessionctl`` writes no JSON and exits non-zero for a client/socket
    failure.  Match the complete stderr line, rather than a substring, so a
    malformed or otherwise unexplained command result remains fail-closed.
    """
    stdout = getattr(completed, "stdout", "")
    stderr = getattr(completed, "stderr", "")
    return (
        getattr(completed, "returncode", None) != 0 and
        isinstance(stdout, str) and not stdout.strip() and
        isinstance(stderr, str) and
        stderr.strip() in SESSION_REVOKE_TRANSPORT_RETRY_REASONS)


def _session_revoke_transient_response(
        completed: subprocess.CompletedProcess[str],
        response: object, generation: int,
) -> bool:
    """Return whether the supervisor explicitly reported startup/readiness.

    This mirrors the supervisor's deliberately narrow transient-recovery
    allow-list.  Position/order/identity and uncertain-command outcomes are
    intentionally excluded; those must not be retried by the Python fence.
    """
    return (
        getattr(completed, "returncode", None) == 4 and
        isinstance(response, dict) and
        response.get("accepted") is False and
        response.get("reason_code") in SESSION_REVOKE_TRANSIENT_AUDIT_REASONS
        and response.get("lease_generation") in {0, generation})


def _session_revoke_retry_or_raise(
        deadline: float, cause: BaseException,
) -> None:
    """Sleep once inside the bounded revoke retry window or fail closed."""
    if time.monotonic() >= deadline:
        raise RuntimeError("REPAIR_SESSION_REVOKE_UNCERTAIN") from cause
    time.sleep(min(
        SESSION_REVOKE_RETRY_POLL_SECONDS,
        max(0.0, deadline - time.monotonic())))
    if time.monotonic() >= deadline:
        raise RuntimeError("REPAIR_SESSION_REVOKE_UNCERTAIN") from cause


def _revoke_authority_generation(
        token_file: Path, intent: dict[str, object], generation: int,
) -> str:
    if (not isinstance(generation, int) or isinstance(generation, bool) or
            generation < 1):
        raise RuntimeError("REPAIR_SESSION_REVOKE_GENERATION_INVALID")
    command = [
        "/usr/bin/hepta-sessionctl", "--socket", SUPERVISOR_SOCKET,
        "revoke", "--token-file",
        str(session_authority_bearer_path(token_file)),
        "--generation", str(generation), "--token-owner-uid", "0",
    ]
    deadline = time.monotonic() + SESSION_REVOKE_RETRY_TIMEOUT_SECONDS
    while True:
        # Revalidate the root bearer before every attempt.  A concurrent
        # replacement is an authority-integrity failure, never a reason to
        # retry with a different credential or generation.
        _session_authority_bearer_matches(token_file, intent)
        try:
            completed = run(command, timeout=15)
        except subprocess.TimeoutExpired as error:
            _session_revoke_retry_or_raise(deadline, error)
            continue
        except OSError as error:
            raise RuntimeError("REPAIR_SESSION_REVOKE_UNCERTAIN") from error

        try:
            response = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            if _session_revoke_transport_retryable(completed):
                _session_revoke_retry_or_raise(deadline, error)
                continue
            raise RuntimeError("REPAIR_SESSION_REVOKE_UNCERTAIN") from error
        if (completed.returncode == 0 and isinstance(response, dict) and
                response.get("accepted") is True and
                response.get("reason_code") == "OK" and
                response.get("lease_generation") == generation):
            return "ACCEPTED"
        if (completed.returncode == 4 and isinstance(response, dict) and
                response.get("accepted") is False and
                response.get("reason_code") in {
                    "SESSION_NOT_FOUND", "SESSION_LEASE_NOT_FOUND"} and
                response.get("lease_generation") in {0, generation}):
            return "ALREADY_ABSENT"
        if (completed.returncode == 4 and isinstance(response, dict) and
                response.get("accepted") is False and
                response.get("reason_code") ==
                    "SESSION_LEASE_GENERATION_MISMATCH"):
            return "GENERATION_MISMATCH"
        if _session_revoke_transient_response(
                completed, response, generation):
            reason = str(response.get("reason_code"))
            _session_revoke_retry_or_raise(
                deadline, RuntimeError("SUPERVISOR_REVOKE_TRANSIENT:" + reason))
            continue
        raise RuntimeError("REPAIR_SESSION_REVOKE_UNCERTAIN")


def _resolve_uncertain_session_renew(
        token_file: Path, intent: dict[str, object], *, cleanup: bool,
) -> bool:
    current = intent.get("renew_from_generation")
    candidate = intent.get("renew_candidate_generation")
    if (intent.get("phase") != "RENEW_PENDING" or
            not isinstance(current, int) or isinstance(current, bool) or
            current < 1 or not isinstance(candidate, int) or
            isinstance(candidate, bool) or candidate != current + 1 or
            intent.get("lease_generation") != current):
        raise RuntimeError("REPAIR_SESSION_RENEW_INTENT_INVALID")
    outcome = _revoke_authority_generation(token_file, intent, candidate)
    closed_generation = candidate
    if outcome == "GENERATION_MISMATCH":
        outcome = _revoke_authority_generation(token_file, intent, current)
        closed_generation = current
    if outcome not in {"ACCEPTED", "ALREADY_ABSENT"}:
        raise RuntimeError("REPAIR_SESSION_RENEW_RECOVERY_REQUIRED")
    revoked = dict(intent)
    revoked["phase"] = "REVOKED"
    revoked["lease_generation"] = closed_generation
    revoked["revoke_outcome"] = outcome
    revoked["revoked_at_ms"] = time.time_ns() // 1_000_000
    _write_root_json(session_provision_intent_path(token_file), revoked)
    if cleanup:
        _cleanup_resolved_session_provision(token_file, revoked)
    return True


def _resolve_session_provision_intent(
        token_file: Path, *, allow_active_revoke: bool = False,
        cleanup: bool = True,
) -> bool:
    """Fence a crash-uncertain fresh provision before material cleanup."""
    intent = _load_session_provision_intent(token_file)
    if intent is None:
        return False
    phase = str(intent["phase"])
    if phase == "TOKEN_PENDING":
        # The remote boundary is crossed only after CALL_PENDING is durably
        # published.  This phase therefore contains local-only residue.
        if session_lease_path(token_file).exists() or \
                session_lease_path(token_file).is_symlink():
            raise RuntimeError("REPAIR_SESSION_PROVISION_LEASE_UNEXPECTED")
        _session_provision_material_matches(
            token_file, intent, allow_missing=True)
        _session_authority_bearer_matches(
            token_file, intent, allow_missing=True)
        revoked = dict(intent)
        revoked["phase"] = "REVOKED"
        revoked["lease_generation"] = 1
        revoked["revoke_outcome"] = "ALREADY_ABSENT"
        revoked["revoked_at_ms"] = time.time_ns() // 1_000_000
        _write_root_json(session_provision_intent_path(token_file), revoked)
        if cleanup:
            _cleanup_resolved_session_provision(token_file, revoked)
        return True
    if phase == "ACTIVE" and not allow_active_revoke:
        raise RuntimeError("REPAIR_SESSION_ACTIVE_AUTHORITY_RESIDUE")
    if phase == "RENEW_PENDING":
        return _resolve_uncertain_session_renew(
            token_file, intent, cleanup=cleanup)
    if phase != "REVOKED":
        _session_provision_material_matches(
            token_file, intent, allow_missing=True)
        bearer_metadata = _session_authority_bearer_matches(
            token_file, intent)
        assert bearer_metadata is not None
        generation = intent.get("lease_generation")
        if phase == "CALL_PENDING":
            generation = intent.get("expected_lease_generation")
        if (not isinstance(generation, int) or isinstance(generation, bool) or
                generation < 1):
            raise RuntimeError("REPAIR_SESSION_PROVISION_INTENT_INVALID")
        if phase != "REVOKE_PENDING":
            pending = dict(intent)
            pending["phase"] = "REVOKE_PENDING"
            pending["lease_generation"] = generation
            pending["revoke_started_at_ms"] = (
                time.time_ns() // 1_000_000)
            _write_root_json(
                session_provision_intent_path(token_file), pending)
            intent = pending
        outcome = _revoke_authority_generation(
            token_file, intent, generation)
        accepted = outcome == "ACCEPTED"
        absent = outcome == "ALREADY_ABSENT"
        if not accepted and not absent:
            raise RuntimeError("REPAIR_SESSION_PROVISION_REVOKE_UNCERTAIN")
        current = _session_authority_bearer_matches(token_file, intent)
        assert current is not None
        current_identity = (
            current.st_dev, current.st_ino, current.st_mode, current.st_nlink,
            current.st_uid, current.st_gid, current.st_size)
        original_identity = (
            bearer_metadata.st_dev, bearer_metadata.st_ino,
            bearer_metadata.st_mode, bearer_metadata.st_nlink,
            bearer_metadata.st_uid, bearer_metadata.st_gid,
            bearer_metadata.st_size)
        if current_identity != original_identity:
            raise RuntimeError("REPAIR_SESSION_PROVISION_TOKEN_CHANGED")
        revoked = dict(intent)
        revoked["phase"] = "REVOKED"
        revoked["lease_generation"] = generation
        revoked["revoke_outcome"] = (
            "ACCEPTED" if accepted else "ALREADY_ABSENT")
        revoked["revoked_at_ms"] = time.time_ns() // 1_000_000
        _write_root_json(session_provision_intent_path(token_file), revoked)
        intent = revoked
    if cleanup:
        _cleanup_resolved_session_provision(token_file, intent)
    return True


def load_agent() -> ModuleType:
    loader = SourceFileLoader("hepta_repair_agent", str(AGENT_SOURCE))
    spec = importlib.util.spec_from_loader("hepta_repair_agent", loader)
    if spec is None or spec.loader is None:
        raise RuntimeError("REPAIR_AGENT_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in AGENT_ENV.read_text(encoding="ascii").splitlines():
        if not raw or raw.startswith("#"):
            continue
        key, separator, value = raw.partition("=")
        if not separator or not key or key in values:
            raise RuntimeError("REPAIR_AGENT_ENV_INVALID")
        values[key] = value
    required = (
        "HEPTA_LOCAL_AI_CAMPAIGN_ID", "HEPTA_LOCAL_AI_STRATEGY_ID",
        "HEPTA_LOCAL_AI_STRATEGY_VERSION", "HEPTA_LOCAL_AI_STRATEGY_SHA256",
        "HEPTA_LOCAL_AI_AUTH_GENERATION",
        "HEPTA_LOCAL_AI_AUTH_PROFILE_ID")
    if any(not values.get(key) for key in required):
        raise RuntimeError("REPAIR_AGENT_ENV_INCOMPLETE")
    if not AUTH_PROFILE_ID_PATTERN.fullmatch(
            values["HEPTA_LOCAL_AI_AUTH_PROFILE_ID"]):
        raise RuntimeError("REPAIR_AGENT_ENV_AUTH_PROFILE_INVALID")
    allowlist_sha256 = values.get(AUTH_PROFILE_ALLOWLIST_ENV)
    if (allowlist_sha256 is not None and
            not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                allowlist_sha256)):
        raise RuntimeError("REPAIR_AGENT_ENV_AUTH_PROFILE_ALLOWLIST_INVALID")
    return values


def _set_agent_env_value(
        key: str, value: str, duplicate_error: str | None = None) -> None:
    metadata = os.lstat(AGENT_ENV)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0):
        raise RuntimeError("REPAIR_AGENT_ENV_PATH_UNSAFE")
    lines = AGENT_ENV.read_text(encoding="ascii").splitlines()
    rendered: list[str] = []
    matches = 0
    for line in lines:
        observed_key, separator, _ = line.partition("=")
        if separator and observed_key == key:
            matches += 1
            rendered.append(key + "=" + value)
        else:
            rendered.append(line)
    if matches == 0:
        rendered.append(key + "=" + value)
    elif matches != 1:
        raise RuntimeError(
            duplicate_error or "REPAIR_AGENT_ENV_VALUE_DUPLICATE: " + key)
    temporary = AGENT_ENV.with_name(
        "." + AGENT_ENV.name + "." + str(os.getpid()) + ".tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        stat.S_IMODE(metadata.st_mode))
    try:
        os.fchmod(descriptor, stat.S_IMODE(metadata.st_mode))
        os.fchown(descriptor, 0, 0)
        os.write(descriptor, ("\n".join(rendered) + "\n").encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, AGENT_ENV)
    directory = os.open(
        AGENT_ENV.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def set_auth_generation(auth_generation: str) -> None:
    if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", auth_generation):
        raise RuntimeError("REPAIR_AUTH_GENERATION_INVALID")
    _set_agent_env_value(
        "HEPTA_LOCAL_AI_AUTH_GENERATION", auth_generation,
        "REPAIR_AGENT_ENV_AUTH_GENERATION_DUPLICATE")
    print(
        "REPAIR_AUTH_GENERATION_SET auth_generation=" + auth_generation,
        flush=True)


def set_auth_profile_allowlist_sha256(allowlist_sha256: str) -> None:
    if not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(allowlist_sha256):
        raise RuntimeError("REPAIR_AUTH_PROFILE_ALLOWLIST_SHA256_INVALID")
    _set_agent_env_value(AUTH_PROFILE_ALLOWLIST_ENV, allowlist_sha256)


def _publish_auth_profile_allowlist_sha256(
        expected_env: dict[str, str], allowlist_sha256: str) -> None:
    set_auth_profile_allowlist_sha256(allowlist_sha256)
    expected = dict(expected_env)
    expected[AUTH_PROFILE_ALLOWLIST_ENV] = allowlist_sha256
    if read_env() != expected:
        raise RuntimeError("AUTH_REARM_AGENT_ENV_DRIFTED")


def run(command: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, text=True, capture_output=True, timeout=timeout, check=False)


def run_checked(command: list[str], timeout: int = 30) -> str:
    completed = run(command, timeout)
    if completed.returncode != 0:
        raise RuntimeError(
            "REPAIR_COMMAND_FAILED: " +
            (completed.stderr.strip() or completed.stdout.strip()))
    return completed.stdout


def _paper_control_enable_command(policy: dict[str, object]) -> list[str]:
    command = [LOCAL_PAPER_CONTROL, "enable", "--domain", "alpha"]
    admission_mode = policy.get("admission_mode")
    if admission_mode == "local-only":
        return command
    if (
            policy.get("schema") != ACTIVE_POLICY_SCHEMA or
            policy.get("version") != 5 or
            admission_mode != "external-p1-finalized" or
            policy.get("domain_id") != "alpha" or
            policy.get("paper_only") is not True or
            policy.get("live_authorized") is not False or
            policy.get("order_type") != "LMT" or policy.get("tif") != "DAY" or
            policy.get("max_cycles") != 1 or
            policy.get("max_quantity") != 1 or
            policy.get("max_active_orders") != 1 or
            policy.get("end_flat_required") is not True or
            not isinstance(policy.get("valid_after_ms"), int) or
            isinstance(policy.get("valid_after_ms"), bool) or
            not isinstance(policy.get("expires_at_ms"), int) or
            isinstance(policy.get("expires_at_ms"), bool) or
            policy.get("expires_at_ms", 0) -
                policy.get("valid_after_ms", 0) !=
                EXTERNAL_P1_POLICY_DURATION_MS):
        raise RuntimeError("EXTERNAL_P1_CONTROL_POLICY_INVALID")
    pins = {
        "--watch-handoff-receipt": policy.get("watch_handoff_receipt_path"),
        "--watch-handoff-receipt-file-sha256":
            policy.get("watch_handoff_receipt_file_sha256"),
        "--watch-handoff-receipt-body-sha256":
            policy.get("watch_handoff_receipt_body_sha256"),
        "--campaign-id": policy.get("campaign_id"),
        "--source-baseline-sha256": policy.get("source_baseline_sha256"),
    }
    for flag, value in pins.items():
        if not isinstance(value, str) or not value:
            raise RuntimeError("EXTERNAL_P1_CONTROL_POLICY_INVALID")
        if flag.endswith("sha256") and (
                AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(value) is None or
                value == "sha256:" + "0" * 64):
            raise RuntimeError("EXTERNAL_P1_CONTROL_POLICY_INVALID")
        command.extend((flag, value))
    command.append("--external-p1-finalized")
    return command


def _campaign_policy_for_control() -> dict[str, object]:
    metadata = os.lstat(CAMPAIGN_POLICY)
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        raise RuntimeError("PAPER_CONTROL_POLICY_PATH_UNSAFE")
    try:
        policy = json.loads(CAMPAIGN_POLICY.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("PAPER_CONTROL_POLICY_INVALID") from error
    if not isinstance(policy, dict):
        raise RuntimeError("PAPER_CONTROL_POLICY_INVALID")
    return policy


def _external_policy_for_dispatch() -> dict[str, object] | None:
    """Return external P1 policy while keeping non-root legacy tests isolated."""
    try:
        policy = _campaign_policy_for_control()
    except PermissionError:
        # Production entry points are root-only and must fail closed.  The
        # legacy unit harness intentionally runs unprivileged with an
        # inaccessible host /etc; preserve that local-only test boundary.
        if os.geteuid() == 0:
            raise
        return None
    if policy.get("admission_mode") == "external-p1-finalized":
        return policy
    return None


def _reset_agent_failure_state() -> None:
    """Clear stale exit status even if systemd GC'd the inactive unit."""
    unit = "hepta-local-ai-paper-agent.service"
    command = ["/usr/bin/systemctl", "reset-failed", unit]
    completed = run(command, timeout=15)
    if completed.returncode == 0:
        return
    detail = completed.stderr.strip() or completed.stdout.strip()
    expected = (
        "Failed to reset failed state of unit " + unit +
        ": Unit " + unit + " not loaded.")
    if detail != expected:
        raise RuntimeError("REPAIR_COMMAND_FAILED: " + detail)
    loaded = run([
        "/usr/bin/systemctl", "show", unit,
        "--property=LoadState", "--property=ActiveState",
        "--property=Result", "--property=ExecMainStatus", "--no-pager",
    ], timeout=15)
    fields: dict[str, str] = {}
    for line in loaded.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in fields:
            raise RuntimeError("AUTH_REARM_AGENT_UNIT_STATE_INVALID")
        fields[key] = value
    if (loaded.returncode != 0 or fields != {
            "LoadState": "loaded", "ActiveState": "inactive",
            "Result": "success", "ExecMainStatus": "0"}):
        raise RuntimeError("AUTH_REARM_AGENT_UNIT_STATE_INVALID")


def token_metadata_safe(
        uid: int, gid: int, token_file: Path = TOKEN_FILE) -> bool:
    try:
        metadata = os.lstat(token_file)
    except FileNotFoundError:
        return False
    return (stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == uid and metadata.st_gid == gid and
            stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_size == 65)


def provision_session(
        ttl_seconds: int, token_file: Path = TOKEN_FILE,
        session_name: str = "paper-repair") -> None:
    if (not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or
            not 300 <= ttl_seconds <= 86_400 or
            not isinstance(session_name, str) or not session_name or
            len(session_name) > 160):
        raise RuntimeError("REPAIR_SESSION_PROVISION_ARGUMENT_INVALID")
    identity = pwd.getpwnam(AGENT_USER)
    token_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ensure_session_authority_root()
    _resolve_session_provision_intent(token_file)
    if token_file.exists() or token_file.is_symlink():
        raise RuntimeError("REPAIR_SESSION_ALREADY_EXISTS")
    lease_file = session_lease_path(token_file)
    if lease_file.exists() or lease_file.is_symlink():
        raise RuntimeError("REPAIR_SESSION_LEASE_RESIDUE")
    token_value = secrets.token_hex(32) + "\n"
    session_id = f"{session_name}-{int(time.time())}-{uuid.uuid4().hex[:12]}"
    intent = {
        "schema": "hepta.local-paper-session-provision-intent.v1",
        "phase": "TOKEN_PENDING",
        "token_name": token_file.name,
        "authority_bearer_name":
            session_authority_bearer_path(token_file).name,
        "token_sha256": "sha256:" + hashlib.sha256(
            token_value.encode("ascii")).hexdigest(),
        "expected_lease_generation": 1,
        "lease_generation": None,
        "session_name": session_name,
        "session_id": session_id,
        "peer_uid": identity.pw_uid,
        "ttl_seconds": ttl_seconds,
        "created_at_ms": time.time_ns() // 1_000_000,
        "paper_only": True,
        "live_authorized": False,
    }
    _write_root_json(session_provision_intent_path(token_file), intent)
    authority_bearer = session_authority_bearer_path(token_file)
    rendered_token = token_value.encode("ascii")
    _create_private_bytes_exclusive(
        authority_bearer, rendered_token, uid=0, gid=0,
        failure_prefix="REPAIR_SESSION_AUTHORITY_BEARER")
    _session_authority_bearer_matches(token_file, intent)
    try:
        _create_private_bytes_exclusive(
            token_file, rendered_token, uid=0, gid=0,
            failure_prefix="REPAIR_SESSION_DELIVERY_TOKEN")
        intent["phase"] = "CALL_PENDING"
        _write_root_json(session_provision_intent_path(token_file), intent)
        completed = run([
            "/usr/bin/hepta-sessionctl", "--socket", SUPERVISOR_SOCKET,
            "provision", "--template", "paper",
            "--token-file", str(authority_bearer),
            "--agent-id", "alpha", "--session-id", session_id,
            "--peer-uid", str(identity.pw_uid),
            "--ttl-sec", str(ttl_seconds),
        ])
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("REPAIR_SESSION_PROVISION_INVALID") from error
        if (completed.returncode != 0 or not isinstance(response, dict) or
                response.get("accepted") is not True or
                response.get("reason_code") != "OK" or
                response.get("lease_generation") != 1):
            raise RuntimeError("REPAIR_SESSION_PROVISION_REJECTED")
        intent["phase"] = "ACTIVE"
        intent["lease_generation"] = 1
        intent["accepted_at_ms"] = time.time_ns() // 1_000_000
        intent["expires_at_ms"] = (
            int(intent["accepted_at_ms"]) + ttl_seconds * 1000)
        os.chown(token_file, identity.pw_uid, identity.pw_gid)
        os.chmod(token_file, 0o600)
        if not token_metadata_safe(identity.pw_uid, identity.pw_gid, token_file):
            raise RuntimeError("REPAIR_SESSION_TOKEN_UNSAFE")
        write_session_lease(
            token_file, 1, ttl_seconds, session_name,
            observed_at_ms=int(intent["accepted_at_ms"]),
            expires_at_ms=int(intent["expires_at_ms"]))
        # ACTIVE is the recovery commit point.  Publish it only after both
        # volatile delivery artifacts are safe; every earlier crash remains
        # CALL_PENDING and is exact-revoked with the durable root bearer.
        _write_root_json(session_provision_intent_path(token_file), intent)
        usable, absence = _session_tools_list(
            token_file, retry_until_ready=True, expect_present=True)
        if not usable:
            raise RuntimeError(
                "REPAIR_SESSION_UNUSABLE" +
                (":" + absence if absence else ""))
    except BaseException as error:
        try:
            _resolve_session_provision_intent(
                token_file, allow_active_revoke=True)
        except BaseException as recovery_error:
            raise RuntimeError(
                "REPAIR_SESSION_PROVISION_UNCERTAIN_RECOVERY_REQUIRED") \
                from recovery_error
        raise RuntimeError(
            "REPAIR_SESSION_PROVISION_FAILED_REVOKED") from error
    print(f"REPAIR_SESSION_READY ttl_seconds={ttl_seconds}", flush=True)


def _stable_session_authority_bearer(
        token_file: Path, authority: dict[str, object],
) -> bytes:
    """Read the durable root fence without following or racing a replacement."""
    bearer = session_authority_bearer_path(token_file)
    before = os.lstat(bearer)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            before.st_uid != 0 or before.st_gid != 0 or
            stat.S_IMODE(before.st_mode) != 0o600 or before.st_size != 65):
        raise RuntimeError("REPAIR_SESSION_AUTHORITY_BEARER_CHANGED")
    descriptor = os.open(
        bearer, os.O_RDONLY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_uid, value.st_gid, value.st_size)
        if identity(opened) != identity(before):
            raise RuntimeError("REPAIR_SESSION_AUTHORITY_BEARER_CHANGED")
        chunks: list[bytes] = []
        remaining = 66
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) != 65 or remaining == 0:
            raise RuntimeError("REPAIR_SESSION_AUTHORITY_BEARER_CHANGED")
        current = os.stat(bearer, follow_symlinks=False)
        if identity(current) != identity(opened):
            raise RuntimeError("REPAIR_SESSION_AUTHORITY_BEARER_CHANGED")
    finally:
        os.close(descriptor)
    if ("sha256:" + hashlib.sha256(raw).hexdigest() !=
            authority.get("token_sha256")):
        raise RuntimeError("REPAIR_SESSION_AUTHORITY_BEARER_CHANGED")
    return raw


def _active_authority_lease_times(
        authority: dict[str, object],
) -> tuple[int, int, int]:
    ttl_seconds = authority.get("ttl_seconds")
    observed_at_ms = authority.get("observed_at_ms")
    if observed_at_ms is None:
        observed_at_ms = authority.get("accepted_at_ms")
    expires_at_ms = authority.get("expires_at_ms")
    if (not isinstance(ttl_seconds, int) or
            isinstance(ttl_seconds, bool) or
            not 300 <= ttl_seconds <= 86_400 or
            not isinstance(observed_at_ms, int) or
            isinstance(observed_at_ms, bool) or observed_at_ms <= 0 or
            not isinstance(expires_at_ms, int) or
            isinstance(expires_at_ms, bool) or
            expires_at_ms <= observed_at_ms or
            expires_at_ms > observed_at_ms + ttl_seconds * 1000):
        raise RuntimeError("REPAIR_SESSION_ACTIVE_AUTHORITY_TIME_INVALID")
    return ttl_seconds, observed_at_ms, expires_at_ms


def _active_delivery_lease_matches(
        token_file: Path, authority: dict[str, object], *,
        allow_missing: bool = False,
) -> bool:
    lease_file = session_lease_path(token_file)
    try:
        metadata = os.lstat(lease_file)
    except FileNotFoundError:
        if allow_missing:
            return False
        raise RuntimeError("REPAIR_SESSION_DELIVERY_LEASE_MISSING")
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            not 1 <= metadata.st_size <= 4096):
        raise RuntimeError("REPAIR_SESSION_DELIVERY_LEASE_INVALID")
    descriptor = os.open(
        lease_file, os.O_RDONLY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_uid, value.st_gid, value.st_size)
        if identity(opened) != identity(metadata):
            raise RuntimeError("REPAIR_SESSION_DELIVERY_LEASE_INVALID")
        raw = os.read(descriptor, 4097)
        if not raw or len(raw) > 4096 or os.read(descriptor, 1):
            raise RuntimeError("REPAIR_SESSION_DELIVERY_LEASE_INVALID")
        current = os.stat(lease_file, follow_symlinks=False)
        if identity(current) != identity(opened):
            raise RuntimeError("REPAIR_SESSION_DELIVERY_LEASE_INVALID")
        lease = json.loads(raw.decode("ascii"))
    except (OSError, UnicodeDecodeError, ValueError,
            json.JSONDecodeError) as error:
        raise RuntimeError("REPAIR_SESSION_DELIVERY_LEASE_INVALID") from error
    finally:
        os.close(descriptor)
    ttl_seconds, observed_at_ms, expires_at_ms = (
        _active_authority_lease_times(authority))
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            not isinstance(lease, dict) or
            lease.get("schema") != "hepta.local-paper-session-lease.v1" or
            lease.get("session_name") != authority.get("session_name") or
            lease.get("lease_generation") !=
                authority.get("lease_generation") or
            lease.get("ttl_seconds") != ttl_seconds or
            lease.get("observed_at_ms") != observed_at_ms or
            lease.get("expires_at_ms") != expires_at_ms or
            lease.get("token_sha256") != authority.get("token_sha256")):
        raise RuntimeError("REPAIR_SESSION_DELIVERY_LEASE_INVALID")
    return True


def _stable_delivery_token(
        token_file: Path, *, uid: int, gid: int,
) -> bytes:
    before = os.lstat(token_file)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            before.st_uid != uid or before.st_gid != gid or
            stat.S_IMODE(before.st_mode) != 0o600 or before.st_size != 65):
        raise RuntimeError("REPAIR_SESSION_DELIVERY_TOKEN_INVALID")
    descriptor = os.open(
        token_file, os.O_RDONLY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_uid, value.st_gid, value.st_size)
        if identity(opened) != identity(before):
            raise RuntimeError("REPAIR_SESSION_DELIVERY_TOKEN_INVALID")
        raw = os.read(descriptor, 66)
        if len(raw) != 65 or os.read(descriptor, 1):
            raise RuntimeError("REPAIR_SESSION_DELIVERY_TOKEN_INVALID")
        current = os.stat(token_file, follow_symlinks=False)
        if identity(current) != identity(opened):
            raise RuntimeError("REPAIR_SESSION_DELIVERY_TOKEN_INVALID")
        return raw
    finally:
        os.close(descriptor)


def _write_rematerialized_delivery_token(
        token_file: Path, authority: dict[str, object], raw: bytes) -> None:
    del authority
    identity = pwd.getpwnam(AGENT_USER)
    _create_private_bytes_exclusive(
        token_file, raw, uid=identity.pw_uid, gid=identity.pw_gid,
        failure_prefix="REPAIR_SESSION_REMATERIALIZE_TOKEN")


def _write_rematerialized_delivery_lease(
        token_file: Path, authority: dict[str, object]) -> None:
    generation = authority.get("lease_generation")
    ttl_seconds, observed_at_ms, expires_at_ms = (
        _active_authority_lease_times(authority))
    payload = {
        "schema": "hepta.local-paper-session-lease.v1",
        "session_name": authority.get("session_name"),
        "lease_generation": generation,
        "ttl_seconds": ttl_seconds,
        "observed_at_ms": observed_at_ms,
        "expires_at_ms": expires_at_ms,
        "token_sha256": authority.get("token_sha256"),
    }
    rendered = (json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False) + "\n").encode("ascii")
    lease_file = session_lease_path(token_file)
    _create_private_bytes_exclusive(
        lease_file, rendered, uid=0, gid=0,
        failure_prefix="REPAIR_SESSION_REMATERIALIZE_LEASE")


def _session_tools_list(
        token_file: Path, *, retry_until_ready: bool = False,
        expect_present: bool = False,
) -> tuple[bool, str | None]:
    """Read the authenticated tool catalog without racing gateway startup.

    ``systemd`` reports a Type=simple Gateway active before its app-level
    execution/event identity barrier has opened the socket.  During that
    short interval ``heptactl`` can return an empty/non-JSON transport error.
    Retry only when the caller explicitly asks for startup tolerance; a
    definitive missing/expired lease remains terminal for an existing owner.
    A newly provisioned owner can opt into ``expect_present`` so the same
    bounded handoff also tolerates supervisor publication lag.
    """
    deadline = (time.monotonic() +
                END_FLAT_AUTHENTICATED_READY_TIMEOUT_SECONDS
                if retry_until_ready else time.monotonic())
    while True:
        try:
            completed = run([
                "/usr/sbin/runuser", "-u", AGENT_USER, "--",
                "/usr/bin/heptactl", "--socket", TOOL_SOCKET,
                "--token-file", str(token_file), "tools", "list",
            ], timeout=10)
            try:
                response = json.loads(completed.stdout)
            except json.JSONDecodeError as error:
                if (retry_until_ready and
                        time.monotonic() < deadline):
                    time.sleep(END_FLAT_AUTHENTICATED_READY_POLL_SECONDS)
                    continue
                raise RuntimeError(
                    "REPAIR_SESSION_USABILITY_RESULT_UNCERTAIN") from error
        except (OSError, subprocess.TimeoutExpired) as error:
            if retry_until_ready and time.monotonic() < deadline:
                time.sleep(END_FLAT_AUTHENTICATED_READY_POLL_SECONDS)
                continue
            raise RuntimeError(
                "REPAIR_SESSION_USABILITY_RESULT_UNCERTAIN") from error

        if (completed.returncode == 0 and isinstance(response, dict) and
                response.get("status") == "ok"):
            return True, None
        reason_code = response.get("reason_code") \
            if isinstance(response, dict) else None
        if reason_code in {
                "SESSION_NOT_FOUND", "SESSION_LEASE_NOT_FOUND",
                "SESSION_EXPIRED", "SESSION_ALREADY_EXPIRED",
                "SESSION_REVOKED"}:
            if (expect_present and retry_until_ready and
                    time.monotonic() < deadline):
                time.sleep(END_FLAT_AUTHENTICATED_READY_POLL_SECONDS)
                continue
            return False, str(reason_code)
        if retry_until_ready and time.monotonic() < deadline:
            time.sleep(END_FLAT_AUTHENTICATED_READY_POLL_SECONDS)
            continue
        raise RuntimeError("REPAIR_SESSION_USABILITY_RESULT_UNCERTAIN")


def _ensure_active_session_materialized(
        token_file: Path, authority: dict[str, object],
) -> bool:
    """Recover the original session owner after volatile /run loss.

    The remote generation is never replaced here.  A definitive remote
    absence/expiry is exact-fenced with the durable root bearer before the
    caller is allowed to provision any fallback recovery session.
    """
    if authority.get("phase") != "ACTIVE":
        return False
    generation = authority.get("lease_generation")
    identity = pwd.getpwnam(AGENT_USER)
    if (not isinstance(generation, int) or isinstance(generation, bool) or
            generation < 1 or authority.get("peer_uid") != identity.pw_uid):
        raise RuntimeError("REPAIR_SESSION_ACTIVE_AUTHORITY_INVALID")
    ttl_seconds, observed_at_ms, expires_at_ms = (
        _active_authority_lease_times(authority))
    if expires_at_ms <= time.time_ns() // 1_000_000:
        _resolve_session_provision_intent(
            token_file, allow_active_revoke=True, cleanup=True)
        return False
    raw = _stable_session_authority_bearer(token_file, authority)
    if token_file.exists() or token_file.is_symlink():
        if _stable_delivery_token(
                token_file, uid=identity.pw_uid, gid=identity.pw_gid) != raw:
            raise RuntimeError("REPAIR_SESSION_DELIVERY_TOKEN_INVALID")
    else:
        _write_rematerialized_delivery_token(token_file, authority, raw)
    if not _active_delivery_lease_matches(
            token_file, authority, allow_missing=True):
        _write_rematerialized_delivery_lease(token_file, authority)
    usable, definitive_absence = _session_tools_list(
        token_file, retry_until_ready=True)
    if usable:
        return True
    assert definitive_absence is not None
    _resolve_session_provision_intent(
        token_file, allow_active_revoke=True, cleanup=True)
    return False


def session_usable(token_file: Path = TOKEN_FILE) -> bool:
    authority = _load_session_provision_intent(token_file)
    if authority is not None:
        return _ensure_active_session_materialized(token_file, authority)
    identity = pwd.getpwnam(AGENT_USER)
    if not token_metadata_safe(identity.pw_uid, identity.pw_gid, token_file):
        return False
    usable, _definitive_absence = _session_tools_list(
        token_file, retry_until_ready=True)
    return usable


def _renewal_recovery_ttl_seconds(
        expires_at_ms: int, now_ms: int) -> int | None:
    if (not isinstance(expires_at_ms, int) or
            isinstance(expires_at_ms, bool) or
            not isinstance(now_ms, int) or isinstance(now_ms, bool) or
            expires_at_ms <= 0 or now_ms <= 0):
        raise RuntimeError("SESSION_RENEW_POLICY_TIME_INVALID")
    return None if expires_at_ms <= now_ms else 86_400


class _SessionRenewRecoveryHandoff(RuntimeError):
    """Renewal stopped before authority mutation so recovery can own lineage."""


def _load_session_renew_admission_state() -> dict[str, object]:
    """Read the root Agent state without accepting pathname replacement."""
    failure = "SESSION_RENEW_RECOVERY_HANDOFF_REQUIRED"
    descriptor: int | None = None
    try:
        metadata = os.lstat(AGENT_STATE)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                not 1 <= metadata.st_size <= AGENT_STATE_SNAPSHOT_MAX_BYTES):
            raise RuntimeError("AGENT_STATE_PATH_UNSAFE")
        descriptor = os.open(
            AGENT_STATE,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_uid != 0 or opened.st_gid != 0 or
                stat.S_IMODE(opened.st_mode) != 0o600 or
                not 1 <= opened.st_size <= AGENT_STATE_SNAPSHOT_MAX_BYTES or
                (hasattr(metadata, "st_dev") and
                 hasattr(metadata, "st_ino") and
                 (opened.st_dev != metadata.st_dev or
                  opened.st_ino != metadata.st_ino))):
            raise RuntimeError("AGENT_STATE_OPEN_UNSAFE")
        payload = bytearray()
        while len(payload) <= AGENT_STATE_SNAPSHOT_MAX_BYTES:
            chunk = os.read(
                descriptor,
                min(65_536, AGENT_STATE_SNAPSHOT_MAX_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (len(payload) > AGENT_STATE_SNAPSHOT_MAX_BYTES or
                after.st_dev != opened.st_dev or
                after.st_ino != opened.st_ino or after.st_nlink != 1 or
                after.st_uid != opened.st_uid or after.st_gid != opened.st_gid or
                stat.S_IMODE(after.st_mode) != stat.S_IMODE(opened.st_mode) or
                after.st_size != len(payload) or
                after.st_mtime_ns != opened.st_mtime_ns or
                after.st_ctime_ns != opened.st_ctime_ns):
            raise RuntimeError("AGENT_STATE_CHANGED_DURING_READ")
        current = os.stat(AGENT_STATE, follow_symlinks=False)
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or
                current.st_uid != 0 or current.st_gid != 0 or
                current.st_dev != opened.st_dev or
                current.st_ino != opened.st_ino or
                current.st_size != len(payload)):
            raise RuntimeError("AGENT_STATE_PATH_CHANGED")
        try:
            state = json.loads(payload.decode("ascii"))
        except (UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("AGENT_STATE_INVALID") from error
        required = {
            "schema", "recovery_required", "trading_suspended",
            "pending_order_id", "incident_pending_order_id",
            "pending_mutation_state_unproven", "pending_mutation_kind",
            "pending_mutation_command_id", "pending_mutation_recorded_at_ms",
            "pending_mutation_token_name", "pending_mutation_token_sha256",
        }
        if (not isinstance(state, dict) or
                state.get("schema") !=
                    "hepta.local-ai-paper-agent-state.v3" or
                not required.issubset(state) or
                not isinstance(state.get("recovery_required"), bool) or
                not isinstance(state.get("trading_suspended"), bool) or
                not isinstance(
                    state.get("pending_mutation_state_unproven"), bool)):
            raise RuntimeError("AGENT_STATE_INVALID")
        for name in ("pending_order_id", "incident_pending_order_id"):
            value = state.get(name)
            if (value is not None and
                    (not isinstance(value, int) or isinstance(value, bool) or
                     value < 0)):
                raise RuntimeError("AGENT_STATE_ORDER_ID_INVALID")
        mutation = _pending_mutation_identity(
            state, "SESSION_RENEW_AGENT_MUTATION_INVALID")
        if (state["pending_mutation_state_unproven"] is True or
                mutation is not None or state["pending_order_id"] is not None or
                state["incident_pending_order_id"] is not None or
                state["recovery_required"] is True or
                state["trading_suspended"] is True):
            raise RuntimeError("RECOVERY_LINEAGE_PRESENT")
        return state
    except _SessionRenewRecoveryHandoff:
        raise
    except BaseException as error:
        raise _SessionRenewRecoveryHandoff(failure) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _require_session_renew_order_boundary() -> None:
    """Prove fresh global zero before renewal can mutate session authority."""
    failure = "SESSION_RENEW_RECOVERY_HANDOFF_REQUIRED"
    try:
        agent = load_agent()
        arguments = agent_arguments(AGENT_STATE, TOKEN_FILE)
        _snapshot, projection = _owner_order_projection(
            agent, arguments, "SESSION_RENEW")
        global_ids = set(projection["global_active_order_ids"])
        owned_ids = set(projection["owned_active_order_ids"])
        if global_ids or owned_ids:
            raise RuntimeError("SESSION_RENEW_ACTIVE_ORDER_PRESENT")
    except _SessionRenewRecoveryHandoff:
        raise
    except BaseException as error:
        # This exception is intentionally raised before the generic renewal
        # cleanup scope.  The OnFailure recovery unit, not renewal, owns any
        # possible DAY-order lineage after this point.
        raise _SessionRenewRecoveryHandoff(failure) from error


def _renew_session_once() -> None:
    """Renew the primary PAPER lease or exact-revoke on ambiguity."""
    authority = _load_session_provision_intent(TOKEN_FILE)
    if authority is None:
        raise RuntimeError("SESSION_RENEW_AUTHORITY_MISSING")
    if authority.get("phase") == "RENEW_PENDING":
        try:
            _resolve_uncertain_session_renew(
                TOKEN_FILE, authority, cleanup=True)
        except BaseException as error:
            raise RuntimeError(
                "SESSION_RENEW_UNCERTAIN_RECOVERY_REQUIRED") from error
        raise RuntimeError("SESSION_RENEW_UNCERTAIN_REVOKED")
    if authority.get("phase") != "ACTIVE":
        raise RuntimeError("SESSION_RENEW_AUTHORITY_NOT_ACTIVE")
    if (not TOKEN_FILE.exists() or not session_lease_path(TOKEN_FILE).exists()):
        if not _ensure_active_session_materialized(TOKEN_FILE, authority):
            raise RuntimeError("SESSION_RENEW_REMOTE_AUTHORITY_ABSENT")
        authority = _load_session_provision_intent(TOKEN_FILE)
        if not isinstance(authority, dict):
            raise RuntimeError("SESSION_RENEW_AUTHORITY_MISSING")
    generation = authority.get("lease_generation")
    if (not isinstance(generation, int) or isinstance(generation, bool) or
            generation < 1):
        raise RuntimeError("SESSION_RENEW_GENERATION_INVALID")
    _session_authority_bearer_matches(TOKEN_FILE, authority)
    identity = pwd.getpwnam(AGENT_USER)
    if not token_metadata_safe(identity.pw_uid, identity.pw_gid, TOKEN_FILE):
        raise RuntimeError("SESSION_RENEW_TOKEN_INVALID")
    if ("sha256:" + hashlib.sha256(TOKEN_FILE.read_bytes()).hexdigest() !=
            authority.get("token_sha256")):
        raise RuntimeError("SESSION_RENEW_TOKEN_BINDING_INVALID")
    lease_file = session_lease_path(TOKEN_FILE)
    metadata = os.lstat(lease_file)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("SESSION_RENEW_LEASE_INVALID")
    try:
        lease = json.loads(lease_file.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("SESSION_RENEW_LEASE_INVALID") from error
    if (not isinstance(lease, dict) or
            lease.get("schema") != "hepta.local-paper-session-lease.v1" or
            lease.get("token_sha256") != authority.get("token_sha256") or
            lease.get("lease_generation") != generation):
        raise RuntimeError("SESSION_RENEW_LEASE_BINDING_INVALID")
    policy = json.loads(CAMPAIGN_POLICY.read_text(encoding="ascii"))
    values = read_env()
    now_ms = time.time_ns() // 1_000_000
    expires_at_ms = policy.get("expires_at_ms") \
        if isinstance(policy, dict) else None
    if (not isinstance(policy, dict) or
            policy.get("schema") not in RECOVERY_POLICY_SCHEMAS or
            policy.get("campaign_id") !=
                values["HEPTA_LOCAL_AI_CAMPAIGN_ID"] or
            policy.get("paper_only") is not True or
            policy.get("live_authorized") is not False or
            policy.get("enabled") is not True or
            policy.get("mutations_authorized") is not True or
            not isinstance(expires_at_ms, int) or
            isinstance(expires_at_ms, bool)):
        raise RuntimeError("SESSION_RENEW_POLICY_INVALID")
    ttl_seconds = _renewal_recovery_ttl_seconds(expires_at_ms, now_ms)
    if ttl_seconds is None:
        # Expiry has already removed entry authority.  Do not revoke the last
        # owner-scoped bearer in a timer race with forced end-flat; its most
        # recent 24-hour lease is deliberate post-deadline recovery runway.
        print(
            "SESSION_RENEW_SKIPPED campaign_expired_recovery_lease_preserved",
            flush=True)
        return
    # The session is also the only owner-scoped recovery credential for a DAY
    # order that may survive the Agent or host.  Keep the supervisor's maximum
    # 24-hour recovery runway even on the last renewal before campaign expiry;
    # the campaign policy and forced-flat timer remain the entry-authority
    # fence, while this lease remains usable only for bounded reconciliation.
    candidate_generation = generation + 1
    pending = dict(authority)
    pending["phase"] = "RENEW_PENDING"
    pending["renew_from_generation"] = generation
    pending["renew_candidate_generation"] = candidate_generation
    pending["renew_started_at_ms"] = time.time_ns() // 1_000_000
    pending["renew_ttl_seconds"] = ttl_seconds
    _write_root_json(session_provision_intent_path(TOKEN_FILE), pending)
    completed = run([
        "/usr/bin/hepta-sessionctl", "--socket", SUPERVISOR_SOCKET,
        "renew", "--token-file", str(session_authority_bearer_path(TOKEN_FILE)),
        "--generation", str(generation), "--ttl-sec", str(ttl_seconds),
        "--token-owner-uid", "0",
    ], timeout=30)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        try:
            _resolve_uncertain_session_renew(
                TOKEN_FILE, pending, cleanup=True)
        except BaseException as recovery_error:
            raise RuntimeError(
                "SESSION_RENEW_UNCERTAIN_RECOVERY_REQUIRED") \
                from recovery_error
        raise RuntimeError("SESSION_RENEW_INVALID_REVOKED") from error
    if (completed.returncode != 0 or not isinstance(result, dict) or
            result.get("accepted") is not True or
            result.get("reason_code") != "OK" or
            result.get("lease_generation") != candidate_generation):
        try:
            _resolve_uncertain_session_renew(
                TOKEN_FILE, pending, cleanup=True)
        except BaseException as recovery_error:
            raise RuntimeError(
                "SESSION_RENEW_UNCERTAIN_RECOVERY_REQUIRED") \
                from recovery_error
        raise RuntimeError("SESSION_RENEW_REJECTED_REVOKED")
    try:
        # Keep the durable record in RENEW_PENDING until the local delivery
        # lease for n+1 is durable.  A crash in this window is then resolved
        # by fencing candidate n+1 first, rather than publishing ACTIVE while
        # the local lease still claims n.
        observed_at_ms = time.time_ns() // 1_000_000
        expires_at_ms = observed_at_ms + ttl_seconds * 1000
        write_session_lease(
            TOKEN_FILE, candidate_generation, ttl_seconds,
            str(pending["session_name"]),
            observed_at_ms=observed_at_ms,
            expires_at_ms=expires_at_ms)
        active = dict(pending)
        active["phase"] = "ACTIVE"
        active["lease_generation"] = candidate_generation
        active["ttl_seconds"] = ttl_seconds
        active["observed_at_ms"] = observed_at_ms
        active["expires_at_ms"] = expires_at_ms
        for name in (
                "renew_from_generation", "renew_candidate_generation",
                "renew_started_at_ms", "renew_ttl_seconds"):
            active.pop(name, None)
        _write_root_json(session_provision_intent_path(TOKEN_FILE), active)
        if not session_usable(TOKEN_FILE):
            raise RuntimeError("SESSION_RENEW_UNUSABLE")
    except BaseException as error:
        try:
            _resolve_session_provision_intent(
                TOKEN_FILE, allow_active_revoke=True, cleanup=True)
        except BaseException as recovery_error:
            raise RuntimeError(
                "SESSION_RENEW_POSTCOMMIT_RECOVERY_REQUIRED") \
                from recovery_error
        raise RuntimeError("SESSION_RENEW_POSTCOMMIT_REVOKED") from error
    print(
        "SESSION_RENEWED "
        f"generation={candidate_generation} ttl_seconds={ttl_seconds} "
        f"campaign_id={policy.get('campaign_id')}",
        flush=True)


def renew_session() -> None:
    # Renewal changes durable session authority and therefore shares the same
    # lifecycle -> risk-recovery -> end-flat serialization as admission and
    # teardown.  The recurring timer must never renew while a recovery or
    # terminal fence is in flight.
    with _campaign_lifecycle_locks():
        with _broker_mutation_lock(blocking=False) as mutation_lock:
            if not mutation_lock.acquired:
                print("SESSION_RENEW_SKIPPED broker_mutation_busy", flush=True)
                return
            # Agent death releases the broker lock.  Re-read its durable command
            # handoff while all lifecycle locks are still held, before loading or
            # changing any session authority.  This dedicated exception must
            # reach systemd unchanged so OnFailure starts safe recovery; the
            # generic renewal cleanup below must not revoke the only order owner.
            _load_session_renew_admission_state()
            _require_session_renew_order_boundary()
            try:
                _renew_session_once()
            except BaseException as error:
                try:
                    authority = _load_session_provision_intent(TOKEN_FILE)
                    if (authority is not None and
                            authority.get("phase") != "REVOKED"):
                        _resolve_session_provision_intent(
                            TOKEN_FILE, allow_active_revoke=True, cleanup=True)
                except BaseException as recovery_error:
                    raise RuntimeError(
                        "SESSION_RENEW_FAILURE_RECOVERY_REQUIRED") \
                        from recovery_error
                raise error


def _revoke_recovery_session(
        token_file: Path, *, unlink: bool = True,
        allow_already_absent: bool = False,
) -> dict[str, object]:
    """Revoke the exact generation used for zero-risk recovery.

    Revocation intentionally happens only after two authoritative flat proofs.
    Before then the old bearer may be the sole credential capable of
    reconciling or cancelling an owned in-flight order.
    """
    authority = _load_session_provision_intent(token_file)
    if authority is not None:
        if authority.get("phase") in {
                "TOKEN_PENDING", "CALL_PENDING", "RENEW_PENDING"}:
            # A crash may leave the durable fence ahead of the delivery
            # token/lease material.  Reconcile that exact intent first; stale
            # local lease metadata must never block remote generation fencing.
            _resolve_session_provision_intent(
                token_file, allow_active_revoke=True, cleanup=False)
            authority = _load_session_provision_intent(token_file)
            if authority is None:
                raise RuntimeError(
                    "RISK_RECOVERY_SESSION_AUTHORITY_MISSING")
        generation = authority.get("lease_generation")
        token_sha256 = authority.get("token_sha256")
        if (authority.get("phase") not in {
                "ACTIVE", "REVOKE_PENDING", "REVOKED"} or
                not isinstance(generation, int) or
                isinstance(generation, bool) or generation < 1 or
                not isinstance(token_sha256, str)):
            raise RuntimeError("RISK_RECOVERY_SESSION_AUTHORITY_INVALID")
        # REVOKED is a durable remote-fence commit.  Cleanup deliberately
        # unlinks the bearer before the record, so a crash in that tiny window
        # must resume without demanding material that is no longer needed.
        _session_authority_bearer_matches(
            token_file, authority,
            allow_missing=authority.get("phase") == "REVOKED")
        _session_provision_material_matches(
            token_file, authority, allow_missing=True)
        lease_file = session_lease_path(token_file)
        if lease_file.exists() or lease_file.is_symlink():
            metadata = os.lstat(lease_file)
            if (not stat.S_ISREG(metadata.st_mode) or
                    metadata.st_nlink != 1 or metadata.st_uid != 0 or
                    metadata.st_gid != 0 or
                    stat.S_IMODE(metadata.st_mode) != 0o600):
                raise RuntimeError(
                    "RISK_RECOVERY_SESSION_AUTHORITY_LEASE_INVALID")
            try:
                lease = json.loads(lease_file.read_text(encoding="ascii"))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    "RISK_RECOVERY_SESSION_AUTHORITY_LEASE_INVALID") \
                    from error
            allowed_generations = {generation}
            renew_from = authority.get("renew_from_generation")
            if isinstance(renew_from, int) and not isinstance(
                    renew_from, bool):
                allowed_generations.add(renew_from)
            if (not isinstance(lease, dict) or
                    lease.get("token_sha256") != token_sha256 or
                    lease.get("lease_generation") not in
                        allowed_generations):
                raise RuntimeError(
                    "RISK_RECOVERY_SESSION_AUTHORITY_LEASE_INVALID")
        if authority.get("phase") != "REVOKED":
            _resolve_session_provision_intent(
                token_file, allow_active_revoke=True, cleanup=False)
        resolved = _load_session_provision_intent(token_file)
        if (not isinstance(resolved, dict) or
                resolved.get("phase") != "REVOKED" or
                resolved.get("lease_generation") != generation):
            raise RuntimeError("RISK_RECOVERY_SESSION_REVOKE_INVALID")
        already_absent = resolved.get("revoke_outcome") == "ALREADY_ABSENT"
        if unlink:
            _cleanup_resolved_session_provision(token_file, resolved)
        result: dict[str, object] = {
            "tool_session_revoked": True,
            "tool_session_lease_generation": generation,
            "tool_session_token_sha256": token_sha256,
        }
        if already_absent:
            result["tool_session_already_absent"] = True
        return result

    identity = pwd.getpwnam(AGENT_USER)
    if not token_metadata_safe(identity.pw_uid, identity.pw_gid, token_file):
        raise RuntimeError("RISK_RECOVERY_REVOKE_TOKEN_UNSAFE")
    token_metadata = os.lstat(token_file)
    token_raw = token_file.read_bytes()
    token_sha256 = "sha256:" + hashlib.sha256(token_raw).hexdigest()
    lease_file = session_lease_path(token_file)
    try:
        lease_metadata = os.lstat(lease_file)
    except FileNotFoundError as error:
        raise RuntimeError("RISK_RECOVERY_REVOKE_LEASE_MISSING") from error
    if (not stat.S_ISREG(lease_metadata.st_mode) or
            lease_metadata.st_nlink != 1 or lease_metadata.st_uid != 0 or
            lease_metadata.st_gid != 0 or
            stat.S_IMODE(lease_metadata.st_mode) != 0o600):
        raise RuntimeError("RISK_RECOVERY_REVOKE_LEASE_UNSAFE")
    try:
        lease = json.loads(lease_file.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("RISK_RECOVERY_REVOKE_LEASE_INVALID") from error
    generation = lease.get("lease_generation") if isinstance(lease, dict) else None
    if (not isinstance(lease, dict) or
            lease.get("schema") != "hepta.local-paper-session-lease.v1" or
            lease.get("token_sha256") != token_sha256 or
            not isinstance(generation, int) or isinstance(generation, bool) or
            generation < 1):
        raise RuntimeError("RISK_RECOVERY_REVOKE_LEASE_INVALID")
    completed = run([
        "/usr/bin/hepta-sessionctl", "--socket", SUPERVISOR_SOCKET,
        "revoke", "--token-file", str(token_file),
        "--generation", str(generation),
        "--token-owner-uid", str(identity.pw_uid),
    ], timeout=15)
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("RISK_RECOVERY_SESSION_REVOKE_INVALID") from error
    accepted = (
        completed.returncode == 0 and isinstance(response, dict) and
        response.get("accepted") is True and
        response.get("reason_code") == "OK" and
        response.get("lease_generation") == generation)
    already_absent = (
        allow_already_absent and isinstance(response, dict) and
        response.get("accepted") is False and
        response.get("reason_code") == "SESSION_LEASE_NOT_FOUND" and
        response.get("lease_generation") in {0, generation})
    if not accepted and not already_absent:
        raise RuntimeError("RISK_RECOVERY_SESSION_REVOKE_REJECTED")
    current_token = os.lstat(token_file)
    current_lease = os.lstat(lease_file)
    token_identity = (
        token_metadata.st_dev, token_metadata.st_ino, token_metadata.st_mode,
        token_metadata.st_nlink, token_metadata.st_uid, token_metadata.st_gid,
        token_metadata.st_size)
    current_token_identity = (
        current_token.st_dev, current_token.st_ino, current_token.st_mode,
        current_token.st_nlink, current_token.st_uid, current_token.st_gid,
        current_token.st_size)
    lease_identity = (
        lease_metadata.st_dev, lease_metadata.st_ino, lease_metadata.st_mode,
        lease_metadata.st_nlink, lease_metadata.st_uid, lease_metadata.st_gid,
        lease_metadata.st_size)
    current_lease_identity = (
        current_lease.st_dev, current_lease.st_ino, current_lease.st_mode,
        current_lease.st_nlink, current_lease.st_uid, current_lease.st_gid,
        current_lease.st_size)
    if (current_token_identity != token_identity or
            current_lease_identity != lease_identity or
            hashlib.sha256(token_file.read_bytes()).hexdigest() !=
                token_sha256.removeprefix("sha256:")):
        raise RuntimeError("RISK_RECOVERY_SESSION_CHANGED_DURING_REVOKE")
    if unlink:
        os.unlink(token_file)
        os.unlink(lease_file)
        directory = os.open(
            token_file.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    result: dict[str, object] = {
        "tool_session_revoked": True,
        "tool_session_lease_generation": generation,
        "tool_session_token_sha256": token_sha256,
    }
    if already_absent:
        result["tool_session_already_absent"] = True
    return result


def _session_revocation_descriptor(token_file: Path) -> dict[str, object]:
    """Bind one campaign token to its exact durable lease generation."""
    authority = _load_session_provision_intent(token_file)
    if authority is not None:
        if authority.get("phase") in {
                "TOKEN_PENDING", "CALL_PENDING", "RENEW_PENDING"}:
            _resolve_session_provision_intent(
                token_file, allow_active_revoke=True, cleanup=False)
            authority = _load_session_provision_intent(token_file)
            if authority is None:
                raise RuntimeError("END_FLAT_SESSION_AUTHORITY_INVALID")
        phase = authority.get("phase")
        generation = authority.get("lease_generation")
        token_sha256 = authority.get("token_sha256")
        if (phase not in {"ACTIVE", "REVOKE_PENDING", "REVOKED"} or
                not isinstance(generation, int) or
                isinstance(generation, bool) or generation < 1 or
                not isinstance(token_sha256, str)):
            raise RuntimeError("END_FLAT_SESSION_AUTHORITY_INVALID")
        _session_authority_bearer_matches(
            token_file, authority, allow_missing=phase == "REVOKED")
        _session_provision_material_matches(
            token_file, authority, allow_missing=True)
        result: dict[str, object] = {
            "token_name": token_file.name,
            "token_sha256": token_sha256,
            "lease_generation": generation,
            "revoked": phase == "REVOKED",
        }
        if authority.get("revoke_outcome") == "ALREADY_ABSENT":
            result["already_absent"] = True
        return result

    identity = pwd.getpwnam(AGENT_USER)
    if not token_metadata_safe(identity.pw_uid, identity.pw_gid, token_file):
        raise RuntimeError("END_FLAT_SESSION_TOKEN_UNSAFE")
    lease_file = session_lease_path(token_file)
    try:
        lease_metadata = os.lstat(lease_file)
    except FileNotFoundError as error:
        raise RuntimeError("END_FLAT_SESSION_LEASE_MISSING") from error
    if (not stat.S_ISREG(lease_metadata.st_mode) or
            lease_metadata.st_nlink != 1 or lease_metadata.st_uid != 0 or
            lease_metadata.st_gid != 0 or
            stat.S_IMODE(lease_metadata.st_mode) != 0o600):
        raise RuntimeError("END_FLAT_SESSION_LEASE_UNSAFE")
    token_sha256 = "sha256:" + hashlib.sha256(
        token_file.read_bytes()).hexdigest()
    try:
        lease = json.loads(lease_file.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("END_FLAT_SESSION_LEASE_INVALID") from error
    generation = lease.get("lease_generation") if isinstance(lease, dict) else None
    if (not isinstance(lease, dict) or
            lease.get("schema") != "hepta.local-paper-session-lease.v1" or
            lease.get("token_sha256") != token_sha256 or
            not isinstance(generation, int) or isinstance(generation, bool) or
            generation < 1):
        raise RuntimeError("END_FLAT_SESSION_LEASE_INVALID")
    return {
        "token_name": token_file.name,
        "token_sha256": token_sha256,
        "lease_generation": generation,
        "revoked": False,
    }


def _campaign_session_token_paths() -> list[Path]:
    """Return only token names owned by this bounded local PAPER workflow."""
    candidates: list[Path] = []
    if TOKEN_FILE.exists() or TOKEN_FILE.is_symlink():
        candidates.append(TOKEN_FILE)
    try:
        children = list(RISK_RECOVERY_TOKEN_ROOT.iterdir())
    except FileNotFoundError:
        children = []
    allowed = re.compile(
        r"(?:risk-recovery|end-flat)-[0-9a-f]{24}\.token")
    for child in children:
        if allowed.fullmatch(child.name):
            candidates.append(child)
    try:
        authority_children = list(SESSION_AUTHORITY_ROOT.iterdir())
    except FileNotFoundError:
        authority_children = []
    authority_record = re.compile(
        r"(?P<token>(?:local-paper|(?:risk-recovery|end-flat)-[0-9a-f]{24})"
        r"\.token)\.authority\.json")
    authority_bearer = re.compile(
        r"(?P<token>(?:local-paper|(?:risk-recovery|end-flat)-[0-9a-f]{24})"
        r"\.token)\.revoke-token")
    record_names: set[str] = set()
    bearer_names: set[str] = set()
    for child in authority_children:
        record_match = authority_record.fullmatch(child.name)
        bearer_match = authority_bearer.fullmatch(child.name)
        if record_match is not None:
            record_names.add(record_match.group("token"))
            candidates.append(RISK_RECOVERY_TOKEN_ROOT /
                              record_match.group("token"))
        elif bearer_match is not None:
            bearer_names.add(bearer_match.group("token"))
    # The record is committed before any bearer exists, while cleanup removes
    # the bearer before the terminal REVOKED record.  Those two record-only
    # crash states are locally/authoritatively resolvable.  A bearer without a
    # record, or a missing bearer in any remotely ambiguous phase, is not.
    if bearer_names - record_names:
        raise RuntimeError("END_FLAT_SESSION_AUTHORITY_RESIDUE")
    for token_name in record_names - bearer_names:
        authority = _load_session_provision_intent(
            RISK_RECOVERY_TOKEN_ROOT / token_name)
        if (not isinstance(authority, dict) or
                authority.get("phase") not in {"TOKEN_PENDING", "REVOKED"}):
            raise RuntimeError("END_FLAT_SESSION_AUTHORITY_RESIDUE")
    return sorted(set(candidates), key=lambda value: value.name)


def _end_flat_session_descriptors() -> list[dict[str, object]]:
    return [
        _session_revocation_descriptor(path)
        for path in _campaign_session_token_paths()
    ]


def _require_only_primary_session_authority() -> None:
    paths = _campaign_session_token_paths()
    if paths != [TOKEN_FILE]:
        raise RuntimeError("CAMPAIGN_PRIMARY_SESSION_AUTHORITY_NOT_EXCLUSIVE")
    authority = _load_session_provision_intent(TOKEN_FILE)
    if (not isinstance(authority, dict) or
            authority.get("phase") != "ACTIVE" or
            not session_usable(TOKEN_FILE)):
        raise RuntimeError("CAMPAIGN_PRIMARY_SESSION_AUTHORITY_INVALID")


def _revoke_all_managed_sessions_after_zero() -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for token_file in _campaign_session_token_paths():
        authority = _load_session_provision_intent(token_file)
        if (isinstance(authority, dict) and authority.get("phase") in {
                "TOKEN_PENDING", "CALL_PENDING", "RENEW_PENDING"}):
            _resolve_session_provision_intent(
                token_file, allow_active_revoke=True, cleanup=False)
        revoked = _revoke_recovery_session(
            token_file, allow_already_absent=True)
        revoked["token_name"] = token_file.name
        evidence.append(revoked)
    _validate_no_campaign_session_residue()
    return evidence


def _unlink_bound_session_files(descriptor: dict[str, object]) -> None:
    name = descriptor.get("token_name")
    if (not isinstance(name, str) or not re.fullmatch(
            r"(?:local-paper|(?:risk-recovery|end-flat)-[0-9a-f]{24})"
            r"\.token", name)):
        raise RuntimeError("END_FLAT_SESSION_DESCRIPTOR_INVALID")
    token_file = RISK_RECOVERY_TOKEN_ROOT / name
    authority = _load_session_provision_intent(token_file)
    if authority is not None:
        if (descriptor.get("revoked") is not True or
                authority.get("phase") != "REVOKED" or
                authority.get("token_sha256") !=
                    descriptor.get("token_sha256") or
                authority.get("lease_generation") !=
                    descriptor.get("lease_generation")):
            raise RuntimeError("END_FLAT_SESSION_CHANGED_AFTER_REVOKE")
        _cleanup_resolved_session_provision(token_file, authority)
        return
    lease_file = session_lease_path(token_file)
    token_exists = token_file.exists() or token_file.is_symlink()
    lease_exists = lease_file.exists() or lease_file.is_symlink()
    if not token_exists and not lease_exists:
        return
    if token_exists:
        identity = pwd.getpwnam(AGENT_USER)
        if not token_metadata_safe(
                identity.pw_uid, identity.pw_gid, token_file):
            raise RuntimeError("END_FLAT_SESSION_CHANGED_AFTER_REVOKE")
        if ("sha256:" + hashlib.sha256(token_file.read_bytes()).hexdigest() !=
                descriptor.get("token_sha256")):
            raise RuntimeError("END_FLAT_SESSION_CHANGED_AFTER_REVOKE")
    if lease_exists:
        lease_metadata = os.lstat(lease_file)
        if (not stat.S_ISREG(lease_metadata.st_mode) or
                lease_metadata.st_nlink != 1 or lease_metadata.st_uid != 0 or
                lease_metadata.st_gid != 0 or
                stat.S_IMODE(lease_metadata.st_mode) != 0o600):
            raise RuntimeError("END_FLAT_SESSION_CHANGED_AFTER_REVOKE")
        try:
            lease = json.loads(lease_file.read_text(encoding="ascii"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "END_FLAT_SESSION_CHANGED_AFTER_REVOKE") from error
        if (not isinstance(lease, dict) or
                lease.get("token_sha256") != descriptor.get("token_sha256") or
                lease.get("lease_generation") !=
                    descriptor.get("lease_generation")):
            raise RuntimeError("END_FLAT_SESSION_CHANGED_AFTER_REVOKE")
    if token_exists:
        os.unlink(token_file)
    if lease_exists:
        os.unlink(lease_file)
    directory = os.open(
        token_file.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _validate_no_campaign_session_residue() -> None:
    if _campaign_session_token_paths():
        raise RuntimeError("END_FLAT_SESSION_TOKEN_RESIDUE")
    try:
        children = list(RISK_RECOVERY_TOKEN_ROOT.iterdir())
    except FileNotFoundError:
        children = []
    allowed_lease = re.compile(
        r"(?:local-paper|(?:risk-recovery|end-flat)-[0-9a-f]{24})"
        r"\.token\.lease\.json")
    if any(allowed_lease.fullmatch(child.name) for child in children):
        raise RuntimeError("END_FLAT_SESSION_LEASE_RESIDUE")
    try:
        authority_children = list(SESSION_AUTHORITY_ROOT.iterdir())
    except FileNotFoundError:
        return
    managed = re.compile(
        r"(?:local-paper|(?:risk-recovery|end-flat)-[0-9a-f]{24})"
        r"\.token\.(?:authority\.json|revoke-token)")
    if any(managed.fullmatch(child.name) for child in authority_children):
        raise RuntimeError("END_FLAT_SESSION_AUTHORITY_RESIDUE")


def _managed_session_contexts(
        agent: ModuleType, state_path: Path, failure_prefix: str,
) -> dict[str, tuple[Path, argparse.Namespace]]:
    """Materialize every managed owner before observing/cancelling its orders."""
    contexts: dict[str, tuple[Path, argparse.Namespace]] = {}
    for token_file in _campaign_session_token_paths():
        if token_file.name in contexts:
            raise RuntimeError(failure_prefix + "_SESSION_OWNER_DUPLICATE")
        authority = _load_session_provision_intent(token_file)
        if (not isinstance(authority, dict) or
                authority.get("phase") != "ACTIVE"):
            raise RuntimeError(
                failure_prefix + "_SESSION_AUTHORITY_NOT_ACTIVE")
        if not session_usable(token_file):
            raise RuntimeError(failure_prefix + "_SESSION_OWNER_UNAVAILABLE")
        arguments = agent_arguments(state_path, token_file)
        # The projection is authenticated by the delivery token, while this
        # root-only record is the crash-durable identity used for revoke.  Bind
        # both views so two otherwise complete owner projections cannot be
        # swapped between token names.
        arguments.managed_owner_session_id = str(authority["session_id"])
        contexts[token_file.name] = (token_file, arguments)
    return contexts


def _owner_order_projection(
        agent: ModuleType, arguments: argparse.Namespace,
        failure_prefix: str,
) -> tuple[dict[str, object], dict[str, object]]:
    """Read one exact owner view without reinterpreting the global id set."""
    try:
        snapshot = agent.orders_snapshot(arguments, timeout=5)
    except BaseException as error:
        raise RuntimeError(
            failure_prefix + "_ORDER_PROJECTION_UNAVAILABLE:" + str(error)
        ) from error
    required = {
        "source", "authoritative", "active_orders_source",
        "active_orders_connection_epoch", "active_orders_generation",
        "global_active_orders_complete", "owner_projection_source",
        "owner_projection_connection_epoch", "owner_projection_generation",
        "owner_projection_complete", "owned_active_order_ids_authoritative",
        "owner_scope", "reason_code", "active_order_ids",
        "owned_active_order_ids", "unmapped_active_order_ids",
        "recent_orders",
    }
    if not isinstance(snapshot, dict) or not required.issubset(snapshot):
        raise RuntimeError(
            failure_prefix + "_ORDER_PROJECTION_INVALID")
    epoch = snapshot.get("active_orders_connection_epoch")
    generation = snapshot.get("active_orders_generation")
    scope = snapshot.get("owner_scope")
    raw_global = snapshot.get("active_order_ids")
    raw_owned = snapshot.get("owned_active_order_ids")
    raw_unmapped = snapshot.get("unmapped_active_order_ids")
    if (snapshot.get("source") != "IB" or
            snapshot.get("active_orders_source") != "IB_OPEN_ORDERS" or
            snapshot.get("owner_projection_source") !=
                "EXECUTION_COORDINATOR_ORDER_OWNERS" or
            not isinstance(epoch, int) or isinstance(epoch, bool) or epoch <= 0 or
            not isinstance(generation, int) or
            isinstance(generation, bool) or generation <= 0 or
            snapshot.get("owner_projection_connection_epoch") != epoch or
            snapshot.get("owner_projection_generation") != generation or
            not isinstance(scope, dict) or
            set(scope) != {
                "agent_id", "session_id", "execution_domain", "account"} or
            any(not isinstance(scope.get(name), str) or not scope.get(name)
                for name in scope) or
            not isinstance(raw_global, list) or
            not isinstance(raw_owned, list) or
            not isinstance(raw_unmapped, list) or
            any(not isinstance(value, int) or isinstance(value, bool) or
                value < 0 for values in (raw_global, raw_owned, raw_unmapped)
                for value in values) or
            raw_global != sorted(set(raw_global)) or
            raw_owned != sorted(set(raw_owned)) or
            raw_unmapped != sorted(set(raw_unmapped))):
        raise RuntimeError(
            failure_prefix + "_ORDER_PROJECTION_INVALID")
    global_ids = tuple(raw_global)
    owned_ids = tuple(raw_owned)
    unmapped_ids = set(raw_unmapped)
    if (not set(owned_ids).issubset(global_ids) or
            not unmapped_ids.issubset(global_ids) or
            set(owned_ids).intersection(unmapped_ids)):
        raise RuntimeError(failure_prefix + "_ORDER_PROJECTION_INVALID")
    if (snapshot.get("authoritative") is not True or
            snapshot.get("global_active_orders_complete") is not True or
            snapshot.get("owner_projection_complete") is not True or
            snapshot.get("owned_active_order_ids_authoritative") is not True or
            snapshot.get("reason_code") != "" or unmapped_ids):
        raise RuntimeError(
            failure_prefix + "_ORDER_PROJECTION_NOT_AUTHORITATIVE")
    expected_scope = {
        "session_id": getattr(arguments, "managed_owner_session_id", None),
        "account": getattr(arguments, "managed_owner_account", None),
        "execution_domain": getattr(
            arguments, "managed_owner_execution_domain", None),
    }
    if any(
            expected is not None and scope.get(name) != expected
            for name, expected in expected_scope.items()):
        raise RuntimeError(failure_prefix + "_ORDER_OWNER_SCOPE_MISMATCH")
    projection = {
        "connection_epoch": epoch,
        "generation": generation,
        "owner_scope": dict(scope),
        "global_active_order_ids": global_ids,
        "owned_active_order_ids": owned_ids,
    }
    return snapshot, projection


def _managed_owner_order_projection(
        agent: ModuleType,
        contexts: dict[str, tuple[Path, argparse.Namespace]],
        failure_prefix: str,
) -> tuple[set[int], dict[str, set[int]]]:
    """Prove managed owner sets are disjoint and cover the global IB set."""
    if not contexts:
        raise RuntimeError(failure_prefix + "_SESSION_OWNER_MISSING")
    common_boundary: tuple[int, int, tuple[int, ...], str, str, str] | None = None
    session_ids: set[str] = set()
    owned_by_token: dict[str, set[int]] = {}
    union: set[int] = set()
    for token_name, (_token_file, arguments) in sorted(contexts.items()):
        _snapshot, projection = _owner_order_projection(
            agent, arguments, failure_prefix)
        scope = projection["owner_scope"]
        assert isinstance(scope, dict)
        session_id = str(scope["session_id"])
        expected_session_id = getattr(
            arguments, "managed_owner_session_id", None)
        if (expected_session_id is not None and
                session_id != expected_session_id):
            raise RuntimeError(
                failure_prefix + "_ORDER_OWNER_SCOPE_MISMATCH")
        if session_id in session_ids:
            raise RuntimeError(
                failure_prefix + "_ORDER_OWNER_SCOPE_DUPLICATE")
        session_ids.add(session_id)
        boundary = (
            int(projection["connection_epoch"]),
            int(projection["generation"]),
            tuple(projection["global_active_order_ids"]),
            str(scope["agent_id"]), str(scope["execution_domain"]),
            str(scope["account"]),
        )
        if common_boundary is None:
            common_boundary = boundary
        elif boundary != common_boundary:
            raise RuntimeError(
                failure_prefix + "_ORDER_PROJECTION_BOUNDARY_MISMATCH")
        owned = set(projection["owned_active_order_ids"])
        if union.intersection(owned):
            raise RuntimeError(
                failure_prefix + "_ORDER_OWNER_SETS_OVERLAP")
        union.update(owned)
        owned_by_token[token_name] = owned
    assert common_boundary is not None
    global_ids = set(common_boundary[2])
    if union != global_ids:
        # This includes a mapped-but-unmanaged (foreign) owner.  An unmapped
        # order is rejected earlier because the server marks that projection
        # non-authoritative.  Both cases stop before any cancel mutation.
        raise RuntimeError(
            failure_prefix + "_UNMANAGED_ACTIVE_ORDER_PRESENT")
    return global_ids, owned_by_token


def _cancel_all_managed_session_orders(
        agent: ModuleType,
        contexts: dict[str, tuple[Path, argparse.Namespace]],
        terminal_targets: dict[str, set[int]],
) -> tuple[dict[str, list[int]], dict[str, set[int]]]:
    """Cancel exact per-owner subsets after proving complete global coverage."""
    unknown_owners = set(terminal_targets) - set(contexts)
    if unknown_owners:
        raise RuntimeError("END_FLAT_COMMAND_OWNER_SESSION_MISSING")
    cancelled: dict[str, set[int]] = {
        token_name: set() for token_name in contexts}
    deadline = time.monotonic() + 30.0
    while True:
        global_ids, owned_by_token = _managed_owner_order_projection(
            agent, contexts, "END_FLAT")
        if not global_ids:
            proven: dict[str, set[int]] = {}
            for token_name, (_token_file, arguments) in sorted(contexts.items()):
                targets = sorted(cancelled[token_name].union(
                    terminal_targets.get(token_name, set())))
                proven[token_name] = set(_risk_recovery_terminal_order_proof(
                    agent, arguments, targets, failure_prefix="END_FLAT"))
            return ({name: sorted(values)
                     for name, values in cancelled.items()}, proven)
        if time.monotonic() >= deadline:
            raise RuntimeError("END_FLAT_ACTIVE_ORDERS_UNRESOLVED")
        for token_name, order_ids in sorted(owned_by_token.items()):
            arguments = contexts[token_name][1]
            for order_id in sorted(order_ids):
                try:
                    agent.tool_response(
                        arguments, "trade.cancel_order", {"order_id": order_id},
                        f"end-flat-cancel-{order_id}", timeout=5)
                except Exception:
                    # Re-read all owner views before retrying the same exact
                    # idempotency key; a lost response never implies terminality.
                    pass
                cancelled[token_name].add(order_id)
        time.sleep(1.0)


def _require_managed_sessions_no_active_orders(
        agent: ModuleType,
        contexts: dict[str, tuple[Path, argparse.Namespace]],
) -> None:
    global_ids, _owned_by_token = _managed_owner_order_projection(
        agent, contexts, "END_FLAT")
    if global_ids:
        raise RuntimeError("END_FLAT_ACTIVE_ORDERS_REAPPEARED")


def _end_flat_state_records(
        agent: ModuleType, campaign_id: str, state_path: Path,
) -> list[tuple[Path, dict[str, object]]]:
    """Load every local command lineage that can belong to a managed owner."""
    optional: set[Path] = {AGENT_STATE, STRATEGY_ACCEPTANCE_STATE}
    _intent_path, acceptance_state, _receipt_path = (
        _strategy_acceptance_artifact_paths(campaign_id))
    optional.add(acceptance_state)
    try:
        children = list(END_FLAT_RECEIPT_ROOT.iterdir())
    except FileNotFoundError:
        children = []
    risk_state = re.compile(r"risk-recovery-[0-9a-f]{24}\.state\.json")
    for child in children:
        if risk_state.fullmatch(child.name):
            optional.add(child)
    records: list[tuple[Path, dict[str, object]]] = []
    for path in sorted(optional, key=str):
        if path == state_path:
            continue
        if path.exists() or path.is_symlink():
            state = _load_end_flat_state(agent, path)
            if risk_state.fullmatch(path.name):
                _require_risk_recovery_state_binding(
                    path, state, campaign_id, "END_FLAT")
            records.append((path, state))
    records.append((state_path, _load_end_flat_state(agent, state_path)))
    return records


def _select_end_flat_session(campaign_id: str) -> tuple[Path, bool]:
    """Preserve the order-owning token; never truncate it at deadline."""
    managed = sorted(
        _campaign_session_token_paths(),
        key=lambda value: (value != TOKEN_FILE, value.name))
    for token_file in managed:
        # A crash after the remote revoke call but before its durable commit
        # leaves the exact owner in REVOKE_PENDING.  It is not a usable
        # delivery token, but it is still an authoritative, root-bearer-bound
        # recovery transaction.  Finish that transaction through the normal
        # resolver before selecting a replacement; never unlink or overwrite
        # the pending lineage locally.
        authority = _load_session_provision_intent(token_file)
        if (isinstance(authority, dict) and
                authority.get("phase") == "REVOKE_PENDING"):
            _resolve_session_provision_intent(
                token_file, allow_active_revoke=True, cleanup=True)

    # Re-enumerate after resolving every pending lineage.  In particular, do
    # not return an earlier usable owner while a later managed token remains
    # REVOKE_PENDING; the subsequent owner-wide end-flat pass must see a
    # coherent set with no ambiguous authority records.
    managed = sorted(
        _campaign_session_token_paths(),
        key=lambda value: (value != TOKEN_FILE, value.name))
    for token_file in managed:
        if session_usable(token_file):
            return token_file, token_file == TOKEN_FILE
        raise RuntimeError("END_FLAT_SESSION_UNUSABLE_PRESERVED")
    digest = hashlib.sha256(campaign_id.encode("ascii")).hexdigest()[:24]
    token_file = RISK_RECOVERY_TOKEN_ROOT / ("end-flat-" + digest + ".token")
    if token_file.exists() or token_file.is_symlink():
        if session_usable(token_file):
            return token_file, False
        raise RuntimeError("END_FLAT_SESSION_UNUSABLE_PRESERVED")
    provision_session(86_400, token_file, "paper-end-flat-" + digest)
    return token_file, False


def _finalize_failed_end_flat_recovery_session(
        token_file: Path | None, retained_original_session: bool, *,
        risk_zero_proven: bool = False,
) -> str:
    """Finalize or preserve a recovery-only end-flat owner safely.

    The order-owning primary token is deliberately out of scope.  A recovery
    token may be closed only through the durable root-bearer resolver *after*
    an authoritative zero-risk proof.  On a read/projection failure the
    exposure is unknown, so the default path emits a durable-retry marker and
    preserves ACTIVE/REVOKE_PENDING authority.  Any missing/ambiguous
    authority or resolver failure is reported as ``UNCERTAIN`` and left for
    the normal recovery custodian.  In particular, an ACTIVE authority is
    never unlinked locally.
    """
    if token_file is None or retained_original_session:
        return "SKIPPED_ORIGINAL"

    # ``retained_original_session`` is an in-memory handoff bit.  Keep a
    # second path guard so a caller bug can never turn this finalizer into an
    # implicit primary-session revoke.  Do not resolve symlinks here: the
    # selection path is already rooted and the resolver performs its own
    # no-follow material checks.
    if token_file == TOKEN_FILE:
        print(
            "END_FLAT_RECOVERY_SESSION_FINALIZER_SKIPPED_ORIGINAL",
            flush=True)
        return "SKIPPED_ORIGINAL"
    if (token_file.parent != RISK_RECOVERY_TOKEN_ROOT or
            re.fullmatch(
                r"(?:risk-recovery|end-flat)-[0-9a-f]{24}\.token",
                token_file.name) is None):
        print(
            "END_FLAT_RECOVERY_SESSION_FINALIZER_UNCERTAIN "
            "reason=PATH_UNSAFE",
            flush=True)
        return "UNCERTAIN"

    try:
        authority = _load_session_provision_intent(token_file)
    except BaseException as error:
        # A malformed or concurrently changing authority cannot be classified
        # as absent.  Preserve every local artifact and let the retry
        # custodian perform the normal fail-closed handoff.
        print(
            "END_FLAT_RECOVERY_SESSION_FINALIZER_UNCERTAIN "
            f"reason={type(error).__name__}", flush=True)
        return "UNCERTAIN"
    if authority is None:
        # No root intent means there is no generation-bound credential with
        # which this function can prove remote absence.  Do not infer that a
        # token/lease is harmless and do not unlink it here.
        print(
            "END_FLAT_RECOVERY_SESSION_FINALIZER_UNCERTAIN "
            "reason=AUTHORITY_MISSING",
            flush=True)
        return "UNCERTAIN"

    phase = str(authority.get("phase"))
    if not risk_zero_proven:
        # A failed orders.list/read cannot prove that broker exposure is zero.
        # Keep the exact owner credential available for the next official
        # recovery attempt; do not turn a transport error into a revoke.
        print(
            "END_FLAT_RECOVERY_SESSION_FINALIZER_PRESERVED "
            f"authority_phase={phase} preserved=true "
            "reason=RISK_ZERO_NOT_PROVEN",
            flush=True)
        return "PRESERVED"

    try:
        resolved = _resolve_session_provision_intent(
            token_file, allow_active_revoke=True, cleanup=True)
        if resolved is not True:
            # The resolver returns False only when the intent disappeared
            # between reads.  Treat that race as uncertain rather than
            # claiming that this token was fenced.
            raise RuntimeError("REPAIR_SESSION_PROVISION_INTENT_DISAPPEARED")
    except BaseException as error:
        # Keep the original end-flat exception as the caller's failure.  This
        # marker is intentionally terse (no token/path/secret) and records
        # that the authority must remain available for a later exact retry.
        print(
            "END_FLAT_RECOVERY_SESSION_FINALIZER_UNCERTAIN "
            f"authority_phase={phase} preserved=true "
            f"reason={type(error).__name__}", flush=True)
        return "UNCERTAIN"
    print(
        "END_FLAT_RECOVERY_SESSION_FINALIZED "
        f"authority_phase={phase} outcome=REVOKED", flush=True)
    return "REVOKED"


def agent_arguments(
        state_file: Path, token_file: Path = TOKEN_FILE,
        auth_profile_allowlist_sha256: str | None = None,
) -> argparse.Namespace:
    values = read_env()
    return argparse.Namespace(
        domain="alpha",
        campaign_id=values["HEPTA_LOCAL_AI_CAMPAIGN_ID"],
        strategy_id=values["HEPTA_LOCAL_AI_STRATEGY_ID"],
        strategy_version=values["HEPTA_LOCAL_AI_STRATEGY_VERSION"],
        strategy_sha256=values["HEPTA_LOCAL_AI_STRATEGY_SHA256"],
        agent_user=AGENT_USER,
        heptactl="/usr/bin/heptactl",
        campaignctl="/usr/bin/hepta-campaignctl",
        tool_socket=TOOL_SOCKET,
        token_file=str(token_file),
        runtime_dir="/run/hepta-local-ai-paper-repair",
        state_file=state_file,
        fill_timeout_sec=30,
        confidence=0.62,
        max_holding_sec=0,
        auth_generation=values["HEPTA_LOCAL_AI_AUTH_GENERATION"],
        auth_profile_id=values["HEPTA_LOCAL_AI_AUTH_PROFILE_ID"],
        auth_profile_allowlist_sha256=(
            auth_profile_allowlist_sha256 or
            values.get(AUTH_PROFILE_ALLOWLIST_ENV, "")),
    )


def wait_rate_window(seconds: float = RATE_WINDOW_SECONDS) -> None:
    deadline = time.monotonic() + seconds
    next_report = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if time.monotonic() >= next_report:
            remaining = max(0, int(deadline - time.monotonic()))
            print(f"REPAIR_RATE_WINDOW_WAIT remaining_seconds={remaining}", flush=True)
            next_report += 20.0
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def authoritative_state(agent: ModuleType, arguments: argparse.Namespace) -> tuple[float, int, int]:
    position, position_generation, cash_generation = agent.position_snapshot(
        arguments, require_generation=True)
    if agent.active_orders(arguments):
        raise RuntimeError("REPAIR_ACTIVE_ORDER_PRESENT")
    return position, position_generation, cash_generation


def load_repair_state(agent: ModuleType, path: Path) -> dict[str, object]:
    state = agent.load_state(path) if path.exists() else agent.empty_state()
    if state.get("recovery_required") is True or state.get("pending_order_id") is not None:
        raise RuntimeError("REPAIR_STATE_REQUIRES_MANUAL_RECONCILIATION")
    return state


def flatten_all(initial_wait: float) -> None:
    agent = load_agent()
    # Preserve the original uncertain-cycle state as immutable incident
    # evidence. Recovery owns a fresh state record and never clears or
    # rewrites the interrupted order-20 handoff.
    state_path = Path(
        "/var/lib/hepta-local-ai-paper-agent/recovery-flatten-state.json")
    arguments = agent_arguments(state_path)
    state = load_repair_state(agent, state_path)
    if initial_wait > 0:
        wait_rate_window(initial_wait)
    position, _, _ = authoritative_state(agent, arguments)
    chunk = 0
    while not agent._quantity_equal(position, 0.0):
        before = position
        after = agent.flatten(arguments, state)
        chunk += 1
        expected_change = min(abs(before), float(agent.ORDER_QUANTITY))
        if (abs(abs(before) - abs(after) - expected_change) > 1e-6 or
                (before > 0 and after > before) or
                (before < 0 and after < before)):
            raise RuntimeError("REPAIR_FLATTEN_DELTA_MISMATCH")
        position, position_generation, cash_generation = authoritative_state(
            agent, arguments)
        if not agent._quantity_equal(position, after):
            raise RuntimeError("REPAIR_FLATTEN_POSTREAD_MISMATCH")
        print(
            "REPAIR_FLATTEN_CONFIRMED "
            f"chunk={chunk} position={position:g} "
            f"position_generation={position_generation} "
            f"fx_cash_generation={cash_generation}", flush=True)
        if not agent._quantity_equal(position, 0.0):
            wait_rate_window()
    risk = agent.tool(arguments, "risk.get_limits")
    if float(risk.get("gross_absolute_position", -1.0)) != 0.0:
        raise RuntimeError("REPAIR_FINAL_GROSS_NOT_ZERO")
    print(f"REPAIR_FLAT_CONFIRMED chunks={chunk} gross=0", flush=True)


def acceptance(initial_wait: float) -> None:
    del initial_wait
    # The legacy acceptance command could mutate the PAPER account without the
    # campaign/deadline/session gates or a crash-visible recovery latch.  Keep
    # the command name fail-closed for older operator scripts, but never trade.
    raise RuntimeError(
        "LEGACY_ACCEPTANCE_DISABLED_USE_STRATEGY_ACCEPTANCE")


def _strategy_acceptance_artifact_paths(
        campaign_id: str,
) -> tuple[Path, Path, Path]:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,255}", campaign_id) is None:
        raise RuntimeError("STRATEGY_ACCEPTANCE_CAMPAIGN_ID_INVALID")
    prefix = "strategy-acceptance-" + campaign_id
    return (
        END_FLAT_RECEIPT_ROOT / (prefix + ".intent.json"),
        END_FLAT_RECEIPT_ROOT / (prefix + ".state.json"),
        END_FLAT_RECEIPT_ROOT / (prefix + ".receipt.json"),
    )


def _load_strategy_acceptance_intent(
        campaign_id: str, expected_intent_id: str | None = None,
) -> dict[str, object]:
    intent_path, _, _ = _strategy_acceptance_artifact_paths(campaign_id)
    metadata = os.lstat(intent_path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            not 256 <= metadata.st_size <= 16_384):
        raise RuntimeError("STRATEGY_ACCEPTANCE_INTENT_PATH_UNSAFE")
    raw = intent_path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("STRATEGY_ACCEPTANCE_INTENT_INVALID") from error
    expected_keys = {
        "schema", "intent_id", "campaign_id", "auth_generation",
        "created_at_ms", "one_shot", "failure_requires_end_flat",
        "fresh_campaign_required_after_failure", "paper_only",
        "live_authorized",
    }
    now_ms = time.time_ns() // 1_000_000
    if (not isinstance(value, dict) or set(value) != expected_keys or
            (json.dumps(
                value, ensure_ascii=True, sort_keys=True,
                separators=(",", ":"), allow_nan=False) + "\n").encode(
                    "ascii") != raw or
            value.get("schema") != STRATEGY_ACCEPTANCE_INTENT_SCHEMA or
            value.get("campaign_id") != campaign_id or
            not isinstance(value.get("intent_id"), str) or
            re.fullmatch(r"[0-9a-f]{64}", str(value.get("intent_id"))) is None or
            (expected_intent_id is not None and
             value.get("intent_id") != expected_intent_id) or
            not isinstance(value.get("auth_generation"), str) or
            not value.get("auth_generation") or
            not isinstance(value.get("created_at_ms"), int) or
            isinstance(value.get("created_at_ms"), bool) or
            not 0 < value.get("created_at_ms", 0) <= now_ms or
            value.get("one_shot") is not True or
            value.get("failure_requires_end_flat") is not True or
            value.get("fresh_campaign_required_after_failure") is not True or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False):
        raise RuntimeError("STRATEGY_ACCEPTANCE_INTENT_INVALID")
    return value


def _strategy_acceptance_current_campaign_residue(
        campaign_id: str, main_state: dict[str, object],
) -> None:
    intent_path, state_path, receipt_path = (
        _strategy_acceptance_artifact_paths(campaign_id))
    for path in (intent_path, state_path, receipt_path):
        if path.exists() or path.is_symlink():
            raise RuntimeError("STRATEGY_ACCEPTANCE_ALREADY_ATTEMPTED")
    if (main_state.get("strategy_acceptance_intent_campaign_id") ==
            campaign_id or
            (main_state.get("campaign_id_at_suspend") == campaign_id and
             main_state.get("suspension_code") in {
                 "STRATEGY_ACCEPTANCE_ADMISSION_LATCHED",
                 "STRATEGY_ACCEPTANCE_IN_FLIGHT",
             })):
        raise RuntimeError("STRATEGY_ACCEPTANCE_ALREADY_ATTEMPTED")
    if STRATEGY_ACCEPTANCE_STATE.exists() or STRATEGY_ACCEPTANCE_STATE.is_symlink():
        metadata = os.lstat(STRATEGY_ACCEPTANCE_STATE)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise RuntimeError("STRATEGY_ACCEPTANCE_STATE_PATH_UNSAFE")
        try:
            cached = json.loads(
                STRATEGY_ACCEPTANCE_STATE.read_text(encoding="ascii"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("STRATEGY_ACCEPTANCE_STATE_INVALID") from error
        if (not isinstance(cached, dict) or
                not isinstance(
                    cached.get("strategy_acceptance_campaign_id"), str)):
            raise RuntimeError("STRATEGY_ACCEPTANCE_STATE_INVALID")
        if cached.get("strategy_acceptance_campaign_id") == campaign_id:
            raise RuntimeError("STRATEGY_ACCEPTANCE_ALREADY_ATTEMPTED")
    terminal_path = END_FLAT_RECEIPT_ROOT / (
        "end-flat-" + campaign_id + ".receipt.json")
    if terminal_path.exists() or terminal_path.is_symlink():
        raise RuntimeError("STRATEGY_ACCEPTANCE_CAMPAIGN_ALREADY_TERMINAL")


def _pending_mutation_identity(
        state: dict[str, object], failure: str,
) -> dict[str, object] | None:
    kind = state.get("pending_mutation_kind")
    command_id = state.get("pending_mutation_command_id")
    recorded_at_ms = state.get("pending_mutation_recorded_at_ms")
    token_name = state.get("pending_mutation_token_name")
    token_sha256 = state.get("pending_mutation_token_sha256")
    values = (kind, command_id, recorded_at_ms, token_name, token_sha256)
    if all(value is None for value in values):
        return None
    if (kind not in {"PLACE_ORDER", "FLATTEN_POSITION"} or
            not isinstance(command_id, str) or
            re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", command_id) is None or
            not isinstance(recorded_at_ms, int) or
            isinstance(recorded_at_ms, bool) or recorded_at_ms <= 0 or
            not isinstance(token_name, str) or
            re.fullmatch(
                r"(?:local-paper|(?:risk-recovery|end-flat)-[0-9a-f]{24})"
                r"\.token", token_name) is None or
            not isinstance(token_sha256, str) or
            AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                token_sha256) is None):
        raise RuntimeError(failure)
    return {
        "kind": kind,
        "command_id": command_id,
        "recorded_at_ms": recorded_at_ms,
        "token_name": token_name,
        "token_sha256": token_sha256,
    }


def _clear_pending_mutation_identity(state: dict[str, object]) -> None:
    for key in (
            "pending_mutation_kind", "pending_mutation_command_id",
            "pending_mutation_recorded_at_ms", "pending_mutation_token_name",
            "pending_mutation_token_sha256"):
        state[key] = None
    state["pending_mutation_state_unproven"] = False


def _pending_mutation_token_file(
        state: dict[str, object], failure: str,
) -> Path | None:
    mutation = _pending_mutation_identity(state, failure)
    if mutation is None:
        return None
    token_file = RISK_RECOVERY_TOKEN_ROOT / str(mutation["token_name"])
    authority = _load_session_provision_intent(token_file)
    if (not isinstance(authority, dict) or authority.get("phase") != "ACTIVE" or
            authority.get("token_sha256") != mutation["token_sha256"] or
            not session_usable(token_file)):
        raise RuntimeError(failure + "_OWNER_SESSION_UNAVAILABLE")
    return token_file


def _query_pending_mutation_status(
        agent: ModuleType, arguments: argparse.Namespace,
        state: dict[str, object], failure_prefix: str,
) -> dict[str, object] | None:
    mutation = _pending_mutation_identity(
        state, failure_prefix + "_MUTATION_INTENT_INVALID")
    if mutation is None:
        return None
    if Path(str(arguments.token_file)).name != mutation["token_name"]:
        raise RuntimeError(failure_prefix + "_COMMAND_OWNER_MISMATCH")
    token_file = RISK_RECOVERY_TOKEN_ROOT / str(mutation["token_name"])
    authority = _load_session_provision_intent(token_file)
    generation = authority.get("lease_generation") \
        if isinstance(authority, dict) else None
    if (not isinstance(authority, dict) or authority.get("phase") != "ACTIVE" or
            authority.get("token_sha256") != mutation["token_sha256"] or
            not isinstance(generation, int) or isinstance(generation, bool) or
            generation < 1):
        raise RuntimeError(failure_prefix + "_COMMAND_OWNER_MISMATCH")
    completed = run([
        "/usr/bin/hepta-sessionctl", "--socket", SUPERVISOR_SOCKET,
        "recovery-query", "--token-file",
        str(session_authority_bearer_path(token_file)),
        "--generation", str(generation),
        "--command-id", str(mutation["command_id"]),
        "--token-owner-uid", "0",
    ], timeout=15)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            failure_prefix + "_COMMAND_STATUS_UNAVAILABLE") from error
    status = result.get("command_status") if isinstance(result, dict) else None
    order_id = result.get("order_id") if isinstance(result, dict) else None
    reason_code = result.get("command_reason_code") \
        if isinstance(result, dict) else None
    service_epoch = result.get("execution_service_epoch") \
        if isinstance(result, dict) else None
    service_generation = result.get("execution_service_fencing_generation") \
        if isinstance(result, dict) else None
    fence_reason = result.get("reason_code") if isinstance(result, dict) else None
    valid_fence_reasons = {
        "RECOVERY_QUERY_CANNOT_FULL_FENCE",
        "RECOVERY_QUERY_PROVEN_RECOVERY_ONLY",
        "RECOVERY_QUERY_NOT_FOUND_PROVEN_RECOVERY_ONLY",
    }
    if (completed.returncode != 0 or not isinstance(result, dict) or
            result.get("accepted") is not True or
            result.get("authoritative_command_status") is not True or
            result.get("recovery_only") is not True or
            result.get("owner_fenced") is not False or
            fence_reason not in valid_fence_reasons or
            result.get("lease_generation") != generation or
            result.get("command_id") != mutation["command_id"] or
            status not in {"accepted", "rejected", "uncertain", "not_found"} or
            not isinstance(order_id, int) or isinstance(order_id, bool) or
            not isinstance(reason_code, str) or not reason_code or
            len(reason_code) > 256 or
            not isinstance(service_epoch, str) or not service_epoch or
            len(service_epoch) > 128 or
            not isinstance(service_generation, int) or
            isinstance(service_generation, bool) or service_generation < 1):
        raise RuntimeError(failure_prefix + "_COMMAND_STATUS_INVALID")
    if status == "not_found":
        if (fence_reason != "RECOVERY_QUERY_NOT_FOUND_PROVEN_RECOVERY_ONLY" or
                order_id != -1 or
                reason_code != "EXECUTION_COMMAND_NOT_FOUND"):
            raise RuntimeError(failure_prefix + "_COMMAND_STATUS_INVALID")
        status = "rejected"
    elif status == "uncertain" or order_id >= 0:
        if fence_reason != "RECOVERY_QUERY_CANNOT_FULL_FENCE":
            raise RuntimeError(failure_prefix + "_COMMAND_STATUS_INVALID")
    elif status == "accepted" and reason_code == "POSITION_ALREADY_FLAT":
        if fence_reason != "RECOVERY_QUERY_PROVEN_RECOVERY_ONLY":
            raise RuntimeError(failure_prefix + "_COMMAND_STATUS_INVALID")
    elif fence_reason != "RECOVERY_QUERY_PROVEN_RECOVERY_ONLY":
        raise RuntimeError(failure_prefix + "_COMMAND_STATUS_INVALID")
    if status == "uncertain":
        raise RuntimeError(failure_prefix + "_COMMAND_STATUS_UNCERTAIN")
    kind = mutation["kind"]
    if status == "accepted":
        if kind == "PLACE_ORDER" and order_id < 0:
            raise RuntimeError(failure_prefix + "_COMMAND_STATUS_INVALID")
        if (kind == "FLATTEN_POSITION" and order_id < 0 and
                reason_code != "POSITION_ALREADY_FLAT"):
            raise RuntimeError(failure_prefix + "_COMMAND_STATUS_INVALID")
    elif order_id >= 0:
        # A terminal rejection never names an accepted venue order.
        raise RuntimeError(failure_prefix + "_COMMAND_STATUS_INVALID")
    if (status == "rejected" and
            reason_code == "EXECUTION_COMMAND_NOT_FOUND"):
        status = "rejected"
    return {
        "kind": kind,
        "command_id": mutation["command_id"],
        "command_status": status,
        "order_id": order_id,
        "reason_code": reason_code,
        "execution_service_epoch": service_epoch,
        "execution_service_fencing_generation": service_generation,
        "tool_session_token_sha256": mutation["token_sha256"],
        "tool_session_lease_generation": generation,
        "recovery_only": True,
    }


def _apply_pending_mutation_status(
        state: dict[str, object], status: dict[str, object],
) -> int | None:
    existing_order_id = state.get("pending_order_id")
    if (existing_order_id is not None and
            (not isinstance(existing_order_id, int) or
             isinstance(existing_order_id, bool) or existing_order_id < 0)):
        raise RuntimeError("PENDING_MUTATION_ORDER_ID_CONFLICT")
    if status["command_status"] == "accepted" and int(status["order_id"]) >= 0:
        order_id = int(status["order_id"])
        if existing_order_id is not None and existing_order_id != order_id:
            raise RuntimeError("PENDING_MUTATION_ORDER_ID_CONFLICT")
        state["pending_order_id"] = order_id
        state["pending_order_since_ms"] = state.get(
            "pending_mutation_recorded_at_ms")
        state["incident_pending_order_id"] = order_id
        state["last_order_result"] = "COMMAND_STATUS_ACCEPTED"
        return order_id
    if (status["command_status"] == "rejected" or
            (status["command_status"] == "accepted" and
             status["reason_code"] == "POSITION_ALREADY_FLAT")):
        if existing_order_id is not None:
            raise RuntimeError("PENDING_MUTATION_ORDER_ID_CONFLICT")
        state["pending_order_id"] = None
        state["pending_order_since_ms"] = None
        state["last_order_result"] = (
            "COMMAND_STATUS_REJECTED" if
            status["command_status"] == "rejected" else
            "COMMAND_STATUS_FLAT_NOOP")
        _clear_pending_mutation_identity(state)
        return None
    raise RuntimeError("PENDING_MUTATION_STATUS_UNHANDLED")


def _reconcile_pending_mutation_records(
        agent: ModuleType,
        records: list[tuple[Path, dict[str, object]]],
        failure_prefix: str,
) -> tuple[
        list[dict[str, object]], dict[str, set[int]],
        list[tuple[Path, dict[str, object], int]],
]:
    """Query each exact command owner once and persist every state projection."""
    grouped: dict[
        tuple[str, str, str, str, int],
        list[tuple[Path, dict[str, object]]],
    ] = {}
    token_sha_by_name: dict[str, str] = {}
    lineages_by_owner: dict[
        str, set[tuple[str, str, int]],
    ] = {}
    pending_order_by_lineage: dict[
        tuple[str, str, str, str, int], int | None,
    ] = {}
    seen_paths: set[Path] = set()
    for path, state in records:
        if path in seen_paths:
            raise RuntimeError(
                failure_prefix + "_MUTATION_RECORD_PATH_DUPLICATE")
        seen_paths.add(path)
        unproven = state.get("pending_mutation_state_unproven")
        if unproven is not None and not isinstance(unproven, bool):
            raise RuntimeError(
                failure_prefix + "_MUTATION_IDENTITY_UNPROVEN_INVALID")
        if unproven is True:
            raise RuntimeError(
                failure_prefix + "_MUTATION_IDENTITY_UNPROVEN")
        mutation = _pending_mutation_identity(
            state, failure_prefix + "_MUTATION_INTENT_INVALID")
        if mutation is None:
            continue
        pending_order_id = state.get("pending_order_id")
        if (pending_order_id is not None and
                (not isinstance(pending_order_id, int) or
                 isinstance(pending_order_id, bool) or pending_order_id < 0)):
            raise RuntimeError(
                failure_prefix + "_MUTATION_PENDING_ORDER_ID_INVALID")
        token_name = str(mutation["token_name"])
        token_sha256 = str(mutation["token_sha256"])
        previous_sha = token_sha_by_name.setdefault(token_name, token_sha256)
        if previous_sha != token_sha256:
            raise RuntimeError(
                failure_prefix + "_COMMAND_OWNER_TOKEN_SHA_CONFLICT")
        lineage = (
            str(mutation["kind"]), str(mutation["command_id"]),
            int(mutation["recorded_at_ms"]),
        )
        lineages_by_owner.setdefault(token_name, set()).add(lineage)
        key = (
            str(mutation["kind"]), str(mutation["command_id"]),
            token_name, token_sha256, int(mutation["recorded_at_ms"]),
        )
        if key in pending_order_by_lineage:
            if pending_order_by_lineage[key] != pending_order_id:
                raise RuntimeError(
                    failure_prefix +
                    "_COMMAND_OWNER_DUPLICATE_PROJECTION_CONFLICT")
        else:
            pending_order_by_lineage[key] = pending_order_id
        grouped.setdefault(key, []).append((path, state))

    # One session owner cannot safely reconcile multiple independently pending
    # mutations: querying one and persisting its projection before discovering
    # the other would make retry outcome depend on record order. Reject the
    # entire set before resolving a token, issuing a query, or writing state.
    if any(len(lineages) > 1 for lineages in lineages_by_owner.values()):
        raise RuntimeError(
            failure_prefix + "_COMMAND_OWNER_MULTIPLE_PENDING_MUTATIONS")

    # Complete every owner query before mutating any local projection. A later
    # unavailable owner must not leave an earlier record rewritten and make the
    # next retry depend on iteration order.
    queried: list[tuple[
        str, list[tuple[Path, dict[str, object]]], dict[str, object],
    ]] = []
    for (_kind, _command_id, token_name, _token_sha256,
         _recorded_at_ms), owners in sorted(grouped.items()):
        representative_path, representative_state = owners[0]
        token_file = _pending_mutation_token_file(
            representative_state,
            failure_prefix + "_PENDING_MUTATION")
        if token_file is None or token_file.name != token_name:
            raise RuntimeError(
                failure_prefix + "_COMMAND_OWNER_UNAVAILABLE")
        arguments = agent_arguments(representative_path, token_file)
        status = _query_pending_mutation_status(
            agent, arguments, representative_state, failure_prefix)
        assert status is not None
        queried.append((token_name, owners, status))

    # Apply every authoritative result to detached copies first. Duplicate
    # projections were proven identical above, so a status/order conflict is
    # detected for the full set before the first durable write.
    planned: list[tuple[
        Path, dict[str, object], dict[str, object], int | None, str,
    ]] = []
    evidence: list[dict[str, object]] = []
    for token_name, owners, status in queried:
        evidence.append(status)
        for path, state in owners:
            projected = dict(state)
            reconciled_order_id = _apply_pending_mutation_status(
                projected, status)
            planned.append((
                path, state, projected, reconciled_order_id, token_name))

    terminal_by_owner: dict[str, set[int]] = {}
    accepted_records: list[tuple[Path, dict[str, object], int]] = []
    for path, state, projected, reconciled_order_id, token_name in planned:
        _write_root_json(path, projected)
        state.clear()
        state.update(projected)
        if reconciled_order_id is not None:
            terminal_by_owner.setdefault(token_name, set()).add(
                reconciled_order_id)
            accepted_records.append((path, state, reconciled_order_id))
    return evidence, terminal_by_owner, accepted_records


def _clear_terminal_pending_mutation_records(
        records: list[tuple[Path, dict[str, object], int]],
        terminal_by_owner: dict[str, set[int]],
) -> None:
    """Clear mutation intent only after its exact owner proved broker terminal."""
    for path, state, order_id in records:
        mutation = _pending_mutation_identity(
            state, "RECOVERY_TERMINAL_MUTATION_INTENT_INVALID")
        if mutation is None:
            continue
        proven = terminal_by_owner.get(str(mutation["token_name"]), set())
        if order_id not in proven:
            raise RuntimeError(
                "RECOVERY_COMMAND_ORDER_TERMINAL_EVIDENCE_MISSING")
        state["pending_order_id"] = None
        state["pending_order_since_ms"] = None
        state["incident_pending_order_id"] = order_id
        if mutation["kind"] == "FLATTEN_POSITION":
            state["last_flatten_order_id"] = order_id
        state["last_order_result"] = "COMMAND_STATUS_TERMINAL_CONFIRMED"
        _clear_pending_mutation_identity(state)
        _write_root_json(path, state)


def _merge_repair_owned_agent_state_fields(
        state: dict[str, object],
) -> dict[str, object]:
    """Restore root-custodian fields intentionally unknown to the agent.

    The agent normalizer must not silently delete admission evidence between
    separate repair CLI invocations. Only tightly validated, non-secret
    custodian fields are merged from the same root-owned state document.
    """
    try:
        raw = json.loads(AGENT_STATE.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("REPAIR_OWNED_AGENT_STATE_INVALID") from error
    if not isinstance(raw, dict):
        raise RuntimeError("REPAIR_OWNED_AGENT_STATE_INVALID")
    rearm_hash = raw.get("rearm_stack_receipt_sha256")
    if rearm_hash is not None:
        if (not isinstance(rearm_hash, str) or
                AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                    rearm_hash) is None):
            raise RuntimeError("REPAIR_OWNED_AGENT_STATE_INVALID")
        state["rearm_stack_receipt_sha256"] = rearm_hash
    intent_id = raw.get("strategy_acceptance_intent_id")
    if intent_id is not None:
        if (not isinstance(intent_id, str) or
                re.fullmatch(r"[0-9a-f]{64}", intent_id) is None):
            raise RuntimeError("REPAIR_OWNED_AGENT_STATE_INVALID")
        state["strategy_acceptance_intent_id"] = intent_id
    intent_campaign = raw.get("strategy_acceptance_intent_campaign_id")
    if intent_campaign is not None:
        if (not isinstance(intent_campaign, str) or
                re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._:-]{2,255}",
                    intent_campaign) is None):
            raise RuntimeError("REPAIR_OWNED_AGENT_STATE_INVALID")
        state["strategy_acceptance_intent_campaign_id"] = intent_campaign
    mutation_unproven = raw.get("pending_mutation_state_unproven")
    mutation = _pending_mutation_identity(
        raw, "REPAIR_OWNED_AGENT_STATE_INVALID")
    if (mutation_unproven is not None and
            not isinstance(mutation_unproven, bool)):
        raise RuntimeError("REPAIR_OWNED_AGENT_STATE_INVALID")
    for key in (
            "pending_mutation_kind", "pending_mutation_command_id",
            "pending_mutation_recorded_at_ms", "pending_mutation_token_name",
            "pending_mutation_token_sha256"):
        state[key] = raw.get(key) if mutation is not None else None
    state["pending_mutation_state_unproven"] = (
        mutation_unproven is True)
    return state


def _raw_strategy_acceptance_rearm_state() -> dict[str, object]:
    metadata = os.lstat(AGENT_STATE)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            not 256 <= metadata.st_size <= AGENT_STATE_SNAPSHOT_MAX_BYTES):
        raise RuntimeError("STRATEGY_ACCEPTANCE_MAIN_STATE_PATH_UNSAFE")
    try:
        value = json.loads(AGENT_STATE.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("STRATEGY_ACCEPTANCE_MAIN_STATE_INVALID") from error
    if (not isinstance(value, dict) or
            value.get("schema") != "hepta.local-ai-paper-agent-state.v3"):
        raise RuntimeError("STRATEGY_ACCEPTANCE_MAIN_STATE_INVALID")
    return value


def _strategy_acceptance_recovery_projection(
        agent: ModuleType, state_path: Path, campaign_id: str,
        intent_id: str,
) -> dict[str, object]:
    """Load the crash-durable acceptance order handoff, fail-closed."""
    metadata = os.lstat(state_path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            not 256 <= metadata.st_size <= AGENT_STATE_SNAPSHOT_MAX_BYTES):
        raise RuntimeError("STRATEGY_ACCEPTANCE_ORDER_STATE_PATH_UNSAFE")
    try:
        state = agent.load_state(state_path)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("STRATEGY_ACCEPTANCE_ORDER_STATE_INVALID") from error
    if (state.get("schema") != "hepta.local-ai-paper-agent-state.v3" or
            state.get("strategy_acceptance_campaign_id") != campaign_id or
            state.get("strategy_acceptance_intent_id") != intent_id):
        raise RuntimeError("STRATEGY_ACCEPTANCE_ORDER_STATE_INVALID")
    _pending_mutation_identity(
        state, "STRATEGY_ACCEPTANCE_MUTATION_INTENT_INVALID")
    for key in ("pending_order_id", "entry_order_id",
                "last_flatten_order_id"):
        value = state.get(key)
        if (value is not None and
                (not isinstance(value, int) or isinstance(value, bool) or
                 value < 0)):
            raise RuntimeError("STRATEGY_ACCEPTANCE_ORDER_ID_INVALID")
    return state


def _project_strategy_acceptance_recovery_state(
        agent: ModuleType, state_path: Path,
        main_state: dict[str, object], intent: dict[str, object],
        values: dict[str, str],
) -> dict[str, object]:
    """Publish acceptance's durable command/order identity to recovery."""
    campaign_id = values["HEPTA_LOCAL_AI_CAMPAIGN_ID"]
    intent_id = str(intent["intent_id"])
    projected = dict(main_state)
    projected.update({
        "recovery_required": True,
        "trading_suspended": True,
        "suspension_id": "strategy-acceptance-" + intent_id,
        "suspension_code": "STRATEGY_ACCEPTANCE_IN_FLIGHT",
        "suspended_at_ms": intent["created_at_ms"],
        "auth_generation_at_suspend":
            values["HEPTA_LOCAL_AI_AUTH_GENERATION"],
        "campaign_id_at_suspend": campaign_id,
        "recovery_complete": False,
        "recovery_phase": "STRATEGY_ACCEPTANCE_IN_FLIGHT",
        "recovery_reason": "strategy acceptance broker cycle in progress",
        "manual_start_required": True,
        "paper_only": True,
        "live_authorized": False,
    })
    try:
        acceptance = _strategy_acceptance_recovery_projection(
            agent, state_path, campaign_id, intent_id)
    except BaseException:
        projected["pending_mutation_state_unproven"] = True
        _write_root_json(AGENT_STATE, projected)
        raise
    projected["pending_mutation_state_unproven"] = False
    for key in (
            "entry_order_id", "entry_quantity", "entry_at_ms", "entry_mid",
            "last_flatten_order_id", "pending_order_id",
            "pending_order_since_ms", "pending_mutation_kind",
            "pending_mutation_command_id", "pending_mutation_recorded_at_ms",
            "pending_mutation_token_name",
            "pending_mutation_token_sha256"):
        value = acceptance.get(key)
        if value is not None or key not in projected:
            projected[key] = value
    pending_order_id = acceptance.get("pending_order_id")
    if pending_order_id is not None:
        projected["incident_pending_order_id"] = pending_order_id
    _write_root_json(AGENT_STATE, projected)
    return projected


def _require_legacy_strategy_acceptance_policy(
        policy: dict[str, object] | None = None,
) -> None:
    """Keep the legacy 25k MKT acceptance path local-only."""
    observed = _campaign_policy_for_control() if policy is None else policy
    if observed.get("admission_mode") == "external-p1-finalized":
        raise RuntimeError("STRATEGY_ACCEPTANCE_EXTERNAL_P1_FORBIDDEN")


def _begin_strategy_acceptance() -> tuple[
        ModuleType, dict[str, str], dict[str, object], dict[str, object],
        Path, Path]:
    """Publish the one-shot campaign intent and crash latch before admission."""
    # This preflight is deliberately before state loading, latch publication,
    # intent creation, or executable strategy loading.  External P1 has a
    # separate one-unit LMT path and must never inherit this legacy 25k MKT
    # acceptance transaction.
    _require_legacy_strategy_acceptance_policy()
    try:
        rearmed_state = _raw_strategy_acceptance_rearm_state()
    except BaseException as error:
        # Even a broken agent module cannot get ahead of the durable recovery
        # boundary: build a campaign-bound latch directly from the root env.
        values = read_env()
        _write_root_json(AGENT_STATE, {
            "schema": "hepta.local-ai-paper-agent-state.v3",
            "recovery_required": True,
            "trading_suspended": True,
            "suspension_id":
                "strategy-acceptance-unreadable-" + uuid.uuid4().hex,
            "suspension_code": "STRATEGY_ACCEPTANCE_STATE_UNREADABLE",
            "suspended_at_ms": time.time_ns() // 1_000_000,
            "auth_generation_at_suspend":
                values["HEPTA_LOCAL_AI_AUTH_GENERATION"],
            "campaign_id_at_suspend":
                values["HEPTA_LOCAL_AI_CAMPAIGN_ID"],
            "recovery_complete": False,
            "recovery_phase": "STRATEGY_ACCEPTANCE_STATE_UNREADABLE",
            "recovery_reason":
                "strategy acceptance main state unreadable before admission",
            "manual_start_required": True,
            "pending_order_id": None,
            "paper_only": True,
            "live_authorized": False,
        })
        raise RuntimeError("STRATEGY_ACCEPTANCE_STATE_UNREADABLE") from error
    runtime_binding = rearmed_state.get("runtime_binding")
    campaign_id = runtime_binding.get("campaign_id") \
        if isinstance(runtime_binding, dict) else None
    auth_generation = rearmed_state.get("auth_generation_rearmed")
    if (not isinstance(campaign_id, str) or
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{2,255}", campaign_id) is None or
            not isinstance(auth_generation, str) or not auth_generation):
        # A missing rearm binding cannot safely carry acceptance authority.
        # Use the prepared environment only to name the durable failure; all
        # campaign-policy/timer/session admission remains after the latch.
        fallback = read_env()
        campaign_id = fallback["HEPTA_LOCAL_AI_CAMPAIGN_ID"]
        auth_generation = fallback["HEPTA_LOCAL_AI_AUTH_GENERATION"]
    intent_path, state_path, receipt_path = (
        _strategy_acceptance_artifact_paths(campaign_id))
    already_latched = (
        rearmed_state.get("recovery_required") is True or
        rearmed_state.get("trading_suspended") is True)
    try:
        _strategy_acceptance_current_campaign_residue(
            campaign_id, rearmed_state)
    except BaseException:
        if not already_latched:
            rejected_at_ms = time.time_ns() // 1_000_000
            replay_latch = dict(rearmed_state)
            replay_latch.update({
                "recovery_required": True,
                "trading_suspended": True,
                "suspension_id":
                    "strategy-acceptance-replay-" + uuid.uuid4().hex,
                "suspension_code": "STRATEGY_ACCEPTANCE_REPLAY_REJECTED",
                "suspended_at_ms": rejected_at_ms,
                "auth_generation_at_suspend": auth_generation,
                "campaign_id_at_suspend": campaign_id,
                "recovery_complete": False,
                "recovery_phase": "STRATEGY_ACCEPTANCE_REPLAY_REJECTED",
                "recovery_reason":
                    "strategy acceptance current-campaign residue present",
                "manual_start_required": True,
                "pending_order_id": None,
                "strategy_acceptance_intent_campaign_id": campaign_id,
                "paper_only": True,
                "live_authorized": False,
            })
            _write_root_json(AGENT_STATE, replay_latch)
        raise
    intent_id = secrets.token_hex(32)
    created_at_ms = time.time_ns() // 1_000_000
    intent = {
        "schema": STRATEGY_ACCEPTANCE_INTENT_SCHEMA,
        "intent_id": intent_id,
        "campaign_id": campaign_id,
        "auth_generation": auth_generation,
        "created_at_ms": created_at_ms,
        "one_shot": True,
        "failure_requires_end_flat": True,
        "fresh_campaign_required_after_failure": True,
        "paper_only": True,
        "live_authorized": False,
    }
    # If the campaign is already latched for another cause, that state is
    # itself crash-durable.  Preserve it byte-for-byte and let the boundary
    # reject the captured state after the immutable intent is published.
    if not already_latched:
        latch = dict(rearmed_state)
        latch.update({
            "recovery_required": True,
            "trading_suspended": True,
            "suspension_id": "strategy-acceptance-" + intent_id,
            "suspension_code": "STRATEGY_ACCEPTANCE_ADMISSION_LATCHED",
            "suspended_at_ms": created_at_ms,
            "auth_generation_at_suspend":
                auth_generation,
            "campaign_id_at_suspend": campaign_id,
            "recovery_complete": False,
            "recovery_phase": "STRATEGY_ACCEPTANCE_ADMISSION_LATCHED",
            "recovery_reason":
                "strategy acceptance one-shot admission in progress",
            "manual_start_required": True,
            "pending_order_id": None,
            "strategy_acceptance_intent_id": intent_id,
            "strategy_acceptance_intent_campaign_id": campaign_id,
            "paper_only": True,
            "live_authorized": False,
        })
        # This atomic state publication is the first durable intent. A crash
        # before the dedicated intent file still leaves the safety timer a
        # normal recovery latch and makes replay fail closed.
        _write_root_json(AGENT_STATE, latch)
    _create_root_json_exclusive(intent_path, intent)
    _load_strategy_acceptance_intent(campaign_id, intent_id)
    # Full environment parsing is deliberately after the durable one-shot and
    # recovery latch. A malformed/drifting pre-boundary cannot leave an armed
    # authority without terminal recovery.
    values = read_env()
    if (values["HEPTA_LOCAL_AI_CAMPAIGN_ID"] != campaign_id or
            values["HEPTA_LOCAL_AI_AUTH_GENERATION"] != auth_generation):
        raise RuntimeError("STRATEGY_ACCEPTANCE_ENV_BINDING_INVALID")
    # Loading executable strategy code is also after the crash-durable intent.
    agent = load_agent()
    return agent, values, rearmed_state, intent, state_path, receipt_path


def _validate_strategy_acceptance_boundary(
        agent: ModuleType, expected_values: dict[str, str],
        rearmed_state: dict[str, object], intent: dict[str, object],
) -> tuple[dict[str, str], dict[str, object], dict[str, object]]:
    if _unit_is_active(AGENT_SERVICE):
        raise RuntimeError("CAMPAIGN_START_AGENT_ALREADY_ACTIVE")
    values, policy = _validated_prepared_campaign()
    # Recheck the fully validated policy at the last boundary before the
    # acceptance runtime can create state or issue either legacy MKT order.
    _require_legacy_strategy_acceptance_policy(policy)
    if values != expected_values:
        raise RuntimeError("STRATEGY_ACCEPTANCE_ENV_DRIFTED")
    if (rearmed_state.get("recovery_required") is not False or
            rearmed_state.get("trading_suspended") is not False or
            rearmed_state.get("pending_order_id") is not None or
            rearmed_state.get("manual_start_required") is not True or
            not isinstance(rearmed_state.get("runtime_binding"), dict)):
        raise RuntimeError("CAMPAIGN_START_STATE_NOT_REARMED")
    if any(rearmed_state.get(key) != 0 for key in (
            "decisions", "entries", "exits")) or float(
                rearmed_state.get("realized_pnl_estimate", 0.0)) != 0.0:
        raise RuntimeError("STRATEGY_ACCEPTANCE_PERFORMANCE_STATE_NOT_FRESH")
    allowlist = values.get(AUTH_PROFILE_ALLOWLIST_ENV)
    profile_sha256 = "sha256:" + hashlib.sha256(
        values["HEPTA_LOCAL_AI_AUTH_PROFILE_ID"].encode("utf-8")).hexdigest()
    if (rearmed_state.get("auth_generation_rearmed") !=
            values["HEPTA_LOCAL_AI_AUTH_GENERATION"] or
            rearmed_state.get("auth_profile_sha256_rearmed") !=
                profile_sha256 or
            not isinstance(allowlist, str) or
            rearmed_state.get("auth_profile_allowlist_sha256_rearmed") !=
                allowlist):
        raise RuntimeError("CAMPAIGN_START_AUTH_BINDING_INVALID")
    _verified_rearm_stack_receipt(
        rearmed_state, values, require_active_authority=True)
    auth_rearm_receipt = _verified_auth_rearm_receipt(
        rearmed_state, values, policy)
    _require_only_primary_session_authority()
    observed_intent = _load_strategy_acceptance_intent(
        values["HEPTA_LOCAL_AI_CAMPAIGN_ID"], str(intent["intent_id"]))
    if observed_intent != intent:
        raise RuntimeError("STRATEGY_ACCEPTANCE_INTENT_DRIFTED")
    for marker in (SAFETY_LATCH, AUTOMATIC_RISK_ATTEMPT):
        if marker.exists() or marker.is_symlink():
            raise RuntimeError("CAMPAIGN_START_SAFETY_MARKER_PRESENT")
    return values, policy, auth_rearm_receipt


def _strategy_acceptance_locked(
        initial_wait: float, agent: ModuleType,
        expected_values: dict[str, str], rearmed_state: dict[str, object],
        intent: dict[str, object], state_path: Path, receipt_path: Path,
) -> None:
    """Prove one real reversal while a crash-visible recovery latch is set."""
    values, policy, auth_rearm_receipt = (
        _validate_strategy_acceptance_boundary(
            agent, expected_values, rearmed_state, intent))
    _verify_waiting_timer(SAFE_RECOVERY_TIMER)
    remaining_ms = int(policy["expires_at_ms"]) - time.time_ns() // 1_000_000
    minimum_ms = int((max(0.0, initial_wait) + RATE_WINDOW_SECONDS) * 1000)
    if remaining_ms <= minimum_ms + 180_000:
        raise RuntimeError("STRATEGY_ACCEPTANCE_DEADLINE_TOO_CLOSE")
    arguments = agent_arguments(state_path)
    state = agent.empty_state()
    state["strategy_acceptance_intent_id"] = intent["intent_id"]
    state["strategy_acceptance_campaign_id"] = values[
        "HEPTA_LOCAL_AI_CAMPAIGN_ID"]
    state["strategy_acceptance_cycle_consumed"] = 1
    state["strategy_cycle_budget"] = int(policy["max_cycles"]) - 1
    state["strategy_acceptance_performance_included"] = False
    _create_root_json_exclusive(state_path, state)
    if initial_wait > 0:
        wait_rate_window(initial_wait)
    position, _, _ = authoritative_state(agent, arguments)
    if not agent._quantity_equal(position, 0.0):
        raise RuntimeError("STRATEGY_ACCEPTANCE_PREFLIGHT_NOT_FLAT")
    agent.fresh_quote(arguments)
    decision = {
        "action": "SELL", "confidence": 1.0,
        "rationale": "bounded strategy-exit PAPER MKT acceptance",
    }
    try:
        agent.apply_decision(arguments, state, position, decision, False)
    except BaseException:
        # apply_decision persists the command intent before mutation dispatch.
        # Project it even when the call dies before returning an order id.
        try:
            _write_root_json(state_path, state)
        except BaseException:
            pass
        try:
            _project_strategy_acceptance_recovery_state(
                agent, state_path, rearmed_state, intent, values)
        except BaseException:
            pass
        raise
    _write_root_json(state_path, state)
    _project_strategy_acceptance_recovery_state(
        agent, state_path, rearmed_state, intent, values)
    entered, _, _ = authoritative_state(agent, arguments)
    if (int(state.get("entries", 0)) != 1 or
            state.get("last_order_result") != "ECONOMIC_FILL_CONFIRMED" or
            not agent._quantity_equal(entered, -float(agent.ORDER_QUANTITY))):
        raise RuntimeError("STRATEGY_ACCEPTANCE_ENTRY_NOT_CONFIRMED")
    print(
        "STRATEGY_ACCEPTANCE_ENTRY_CONFIRMED side=SELL quantity=25000 "
        f"position={entered:g}", flush=True)

    wait_rate_window()
    reversal = {
        "action": "BUY", "confidence": 1.0,
        "rationale": "bounded model-reversal PAPER MKT acceptance",
    }
    try:
        agent.apply_decision(arguments, state, entered, reversal, False)
    except BaseException:
        try:
            _write_root_json(state_path, state)
        except BaseException:
            pass
        try:
            _project_strategy_acceptance_recovery_state(
                agent, state_path, rearmed_state, intent, values)
        except BaseException:
            pass
        raise
    _write_root_json(state_path, state)
    _project_strategy_acceptance_recovery_state(
        agent, state_path, rearmed_state, intent, values)
    trigger = state.get("last_exit_trigger")
    if (not isinstance(trigger, dict) or
            trigger.get("trigger") != "MODEL_REVERSAL" or
            trigger.get("result") != "ECONOMIC_FLATTEN_CONFIRMED" or
            int(state.get("exits", 0)) != 1):
        raise RuntimeError("STRATEGY_ACCEPTANCE_REVERSAL_EVIDENCE_INVALID")
    final_position, position_generation, cash_generation = authoritative_state(
        agent, arguments)
    risk = agent.tool(arguments, "risk.get_limits")
    gross = float(risk.get("gross_absolute_position", -1.0))
    if not agent._quantity_equal(final_position, 0.0) or gross != 0.0:
        raise RuntimeError("STRATEGY_ACCEPTANCE_FINAL_NOT_FLAT")
    campaign_status = agent.campaign(
        arguments, "status", "acceptance-status-" + uuid.uuid4().hex)
    campaign_state = campaign_status.get("state") \
        if isinstance(campaign_status, dict) else None
    if (not isinstance(campaign_status, dict) or
            campaign_status.get("status") != "ok" or
            not isinstance(campaign_state, dict) or
            campaign_state.get("status") != "idle" or
            campaign_state.get("cycles_opened") != 1 or
            campaign_state.get("cycles_closed") != 1 or
            campaign_state.get("active_cycle_id") is not None or
            campaign_state.get("halt_reason") is not None):
        raise RuntimeError("STRATEGY_ACCEPTANCE_CYCLE_BUDGET_INVALID")
    current_binding = agent.current_runtime_binding(arguments)
    if current_binding != rearmed_state.get("runtime_binding"):
        raise RuntimeError("STRATEGY_ACCEPTANCE_RUNTIME_BINDING_DRIFTED")
    state["strategy_acceptance_campaign_id"] = values[
        "HEPTA_LOCAL_AI_CAMPAIGN_ID"]
    state["strategy_acceptance_runtime_binding"] = current_binding
    state["strategy_acceptance_completed_at_ms"] = (
        time.time_ns() // 1_000_000)
    state["strategy_acceptance_position_generation"] = position_generation
    state["strategy_acceptance_fx_cash_generation"] = cash_generation
    state["strategy_acceptance_gross_absolute_position"] = 0
    state["strategy_acceptance_campaign_cycles_opened"] = 1
    state["strategy_acceptance_campaign_cycles_closed"] = 1
    state["strategy_acceptance_paper_only"] = True
    state["strategy_acceptance_live_authorized"] = False
    # Revalidate the absolute deadline and immutable campaign immediately
    # before clearing the crash latch.
    final_values, final_policy = _validated_prepared_campaign()
    if final_values != values or final_policy != policy:
        raise RuntimeError("STRATEGY_ACCEPTANCE_CAMPAIGN_DRIFTED")
    _require_only_primary_session_authority()
    _write_root_json(state_path, state)
    state_raw = state_path.read_bytes()
    receipt = {
        "schema": STRATEGY_ACCEPTANCE_RECEIPT_SCHEMA,
        "intent_id": intent["intent_id"],
        "campaign_id": values["HEPTA_LOCAL_AI_CAMPAIGN_ID"],
        "completed_at_ms": state["strategy_acceptance_completed_at_ms"],
        "runtime_binding": current_binding,
        "state_sha256": "sha256:" + hashlib.sha256(state_raw).hexdigest(),
        "policy_max_cycles": policy["max_cycles"],
        "acceptance_cycle_consumed": 1,
        "strategy_cycle_budget": int(policy["max_cycles"]) - 1,
        "campaign_cycles_opened": 1,
        "campaign_cycles_closed": 1,
        "acceptance_performance_included": False,
        "position": 0,
        "active_orders": 0,
        "gross_absolute_position": 0,
        "paper_only": True,
        "live_authorized": False,
    }
    _create_root_json_exclusive(receipt_path, receipt)
    # This stable path is a read-only current-campaign cache for the agent and
    # supervisor. The per-campaign state and receipt above remain immutable
    # evidence and make every same-campaign replay fail closed.
    _write_root_json(STRATEGY_ACCEPTANCE_STATE, state)
    _verified_strategy_acceptance(policy, rearmed_state, auth_rearm_receipt)
    _write_root_json(AGENT_STATE, rearmed_state)
    print(
        "STRATEGY_ACCEPTANCE_EXIT_CONFIRMED "
        "trigger=MODEL_REVERSAL side=BUY quantity=25000 "
        f"position=0 position_generation={position_generation} "
        f"fx_cash_generation={cash_generation} gross=0", flush=True)


def _force_safe_recovery_after_admission_failure() -> None:
    run_checked([
        "/usr/bin/systemctl", "start", SAFE_RECOVERY_SERVICE,
    ], timeout=330)
    agent = load_agent()
    state = _load_root_agent_state(agent, allow_safety_exit=True)
    if (state.get("recovery_complete") is not True or
            state.get("recovery_phase") != "FLAT_CONFIRMED"):
        raise RuntimeError("ADMISSION_FAILURE_RECOVERY_NOT_PROVEN")
    _validate_no_campaign_session_residue()
    campaign_id = read_env()["HEPTA_LOCAL_AI_CAMPAIGN_ID"]
    end_flat()
    if _validated_end_flat_receipt(campaign_id) is None:
        raise RuntimeError("ADMISSION_FAILURE_END_FLAT_NOT_PROVEN")


def strategy_acceptance(initial_wait: float) -> None:
    failure: BaseException | None = None
    with _campaign_lifecycle_locks():
        try:
            admission = _begin_strategy_acceptance()
            _strategy_acceptance_locked(initial_wait, *admission)
        except BaseException as error:
            failure = error
    if failure is not None:
        try:
            _force_safe_recovery_after_admission_failure()
        except BaseException as recovery_error:
            raise RuntimeError(
                "STRATEGY_ACCEPTANCE_FAILED_RECOVERY_REQUIRED") \
                from recovery_error
        raise failure


def snapshot() -> None:
    agent = load_agent()
    state_path = Path(
        "/var/lib/hepta-local-ai-paper-agent/recovery-flatten-state.json")
    arguments = agent_arguments(state_path)
    position, position_generation, cash_generation = authoritative_state(
        agent, arguments)
    risk = agent.tool(arguments, "risk.get_limits")
    gross = float(risk.get("gross_absolute_position", -1.0))
    if (gross < 0.0 or gross > float(agent.ORDER_QUANTITY) or
            abs(position) > float(agent.ORDER_QUANTITY)):
        raise RuntimeError("REPAIR_RISK_SNAPSHOT_INVALID")
    print(
        "REPAIR_AUTHORITATIVE_SNAPSHOT "
        f"position={position:g} active_orders=0 gross={gross:g} "
        f"position_generation={position_generation} "
        f"fx_cash_generation={cash_generation}", flush=True)


def bring_up_rearm_stack() -> None:
    """Open only reviewed LOCAL_PAPER authority for explicit auth rearm."""
    lifecycle_descriptor = os.open(
        CAMPAIGN_LIFECYCLE_LOCK,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0), 0o600)
    risk_descriptor: int | None = None
    end_flat_descriptor: int | None = None
    try:
        for descriptor, failure in ((
                lifecycle_descriptor,
                "CAMPAIGN_LIFECYCLE_LOCK_UNSAFE"),):
            metadata = os.fstat(descriptor)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                    metadata.st_uid != 0 or metadata.st_gid != 0 or
                    stat.S_IMODE(metadata.st_mode) != 0o600):
                raise RuntimeError(failure)
        fcntl.flock(lifecycle_descriptor, fcntl.LOCK_EX)
        risk_descriptor = os.open(
            RISK_RECOVERY_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600)
        risk_metadata = os.fstat(risk_descriptor)
        if (not stat.S_ISREG(risk_metadata.st_mode) or
                risk_metadata.st_nlink != 1 or risk_metadata.st_uid != 0 or
                risk_metadata.st_gid != 0 or
                stat.S_IMODE(risk_metadata.st_mode) != 0o600):
            raise RuntimeError("RISK_RECOVERY_LOCK_UNSAFE")
        fcntl.flock(risk_descriptor, fcntl.LOCK_EX)
        end_flat_descriptor = os.open(
            END_FLAT_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600)
        end_flat_metadata = os.fstat(end_flat_descriptor)
        if (not stat.S_ISREG(end_flat_metadata.st_mode) or
                end_flat_metadata.st_nlink != 1 or
                end_flat_metadata.st_uid != 0 or
                end_flat_metadata.st_gid != 0 or
                stat.S_IMODE(end_flat_metadata.st_mode) != 0o600):
            raise RuntimeError("END_FLAT_LOCK_UNSAFE")
        fcntl.flock(end_flat_descriptor, fcntl.LOCK_EX)
        if _unit_is_active(AGENT_SERVICE):
            raise RuntimeError("REARM_STACK_REQUIRES_AGENT_STOPPED")
        prepared_values, prepared_policy = _validated_prepared_campaign()
        if (prepared_policy.get("campaign_id") !=
                prepared_values.get("HEPTA_LOCAL_AI_CAMPAIGN_ID")):
            raise RuntimeError("REARM_STACK_CAMPAIGN_BINDING_INVALID")
        authority = _load_session_provision_intent(TOKEN_FILE)
        if authority is not None:
            _resolve_session_provision_intent(
                TOKEN_FILE, allow_active_revoke=True)
        try:
            _validate_no_campaign_session_residue()
        except RuntimeError as error:
            raise RuntimeError("REARM_STACK_SESSION_RESIDUE") from error
        control_attempted = False
        session_ready = False
        receipt_path: Path | None = None
        agent_for_rollback: ModuleType | None = None
        state_snapshot: dict[str, object] | None = None
        state_update_attempted = False
        try:
            # Keep one crash-closed custodian armed throughout authority
            # bring-up.  The shared lifecycle lock makes an overlapping tick
            # a no-op; after a crash it can revoke newly durable authority.
            run_checked([
                "/usr/bin/systemctl", "enable", SAFE_RECOVERY_TIMER,
            ], timeout=30)
            run_checked([
                "/usr/bin/systemctl", "start", SAFE_RECOVERY_TIMER,
            ], timeout=30)
            _verify_waiting_timer(SAFE_RECOVERY_TIMER)
            control_attempted = True
            rendered = run_checked(
                _paper_control_enable_command(prepared_policy), timeout=120)
            try:
                control = json.loads(rendered)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    "REARM_STACK_CONTROL_RESPONSE_INVALID") from error
            manifest_sha256 = control.get("identity_manifest_sha256") \
                if isinstance(control, dict) else None
            if (not isinstance(control, dict) or
                    control.get("mode") != "LOCAL_PAPER" or
                    control.get("domain") != "alpha" or
                    control.get("paper_authorized") is not True or
                    control.get("live_authorized") is not False or
                    control.get("admission_mode") !=
                        prepared_policy.get("admission_mode") or
                    not isinstance(manifest_sha256, str) or
                    not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                        manifest_sha256)):
                raise RuntimeError("REARM_STACK_CONTROL_RESPONSE_INVALID")
            provision_session(86_400, TOKEN_FILE, "paper-campaign-rearm")
            session_ready = True
            _require_only_primary_session_authority()
            agent = load_agent()
            state = _load_root_agent_state(agent)
            agent_for_rollback = agent
            state_snapshot = json.loads(json.dumps(
                state, ensure_ascii=True, allow_nan=False))
            values = read_env()
            arguments = agent_arguments(AGENT_STATE)
            first_position, first_cash = _current_zero_proof(agent, arguments)
            first_binding = agent.current_runtime_binding(arguments)
            time.sleep(2.0)
            second_position, second_cash = _current_zero_proof(agent, arguments)
            second_binding = agent.current_runtime_binding(arguments)
            if first_binding != second_binding:
                raise RuntimeError("REARM_STACK_RUNTIME_BINDING_DRIFTED")
            authority = _load_session_provision_intent(TOKEN_FILE)
            if (not isinstance(authority, dict) or
                    authority.get("phase") != "ACTIVE" or
                    authority.get("token_sha256") !=
                        second_binding.get("tool_session_token_sha256") or
                    not isinstance(authority.get("lease_generation"), int) or
                    isinstance(authority.get("lease_generation"), bool) or
                    authority.get("lease_generation", 0) < 1):
                raise RuntimeError("REARM_STACK_SESSION_BINDING_INVALID")
            authority_raw = session_provision_intent_path(
                TOKEN_FILE).read_bytes()
            receipt = {
                "schema": "hepta.local-ai-paper-rearm-stack-receipt.v1",
                "campaign_id": values["HEPTA_LOCAL_AI_CAMPAIGN_ID"],
                "completed_at_ms": time.time_ns() // 1_000_000,
                "identity_manifest_sha256": manifest_sha256,
                "session_authority_sha256": "sha256:" + hashlib.sha256(
                    authority_raw).hexdigest(),
                "session_token_sha256": authority["token_sha256"],
                "session_lease_generation": authority["lease_generation"],
                "first_position_generation": first_position,
                "first_fx_cash_generation": first_cash,
                "second_position_generation": second_position,
                "second_fx_cash_generation": second_cash,
                "runtime_binding": second_binding,
                "position": 0,
                "active_orders": 0,
                "gross_absolute_position": 0,
                "agent_still_stopped": True,
                "paper_only": True,
                "live_authorized": False,
            }
            receipt_path = END_FLAT_RECEIPT_ROOT / (
                "rearm-stack-" + values["HEPTA_LOCAL_AI_CAMPAIGN_ID"] +
                ".receipt.json")
            _write_root_json(receipt_path, receipt)
            receipt_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            state["rearm_stack_receipt_sha256"] = "sha256:" + receipt_hash
            state["runtime_binding"] = second_binding
            state_update_attempted = True
            agent.write_json(AGENT_STATE, state)
            os.chown(AGENT_STATE, 0, 0)
            os.chmod(AGENT_STATE, 0o600)
        except BaseException as error:
            rollback_failures: list[str] = []
            if session_ready or _load_session_provision_intent(
                    TOKEN_FILE) is not None:
                try:
                    authority = _load_session_provision_intent(TOKEN_FILE)
                    if authority is not None:
                        _resolve_session_provision_intent(
                            TOKEN_FILE, allow_active_revoke=True)
                except BaseException as observed:
                    rollback_failures.append(
                        "revoke-session:" + type(observed).__name__ + ":" +
                        str(observed))
            if control_attempted:
                try:
                    _end_flat_revoke_local_paper_control()
                    _end_flat_verify_deny_all()
                except BaseException as observed:
                    rollback_failures.append(
                        "deny-all:" + type(observed).__name__ + ":" +
                        str(observed))
                try:
                    run_checked([
                        "/usr/bin/systemctl", "stop",
                        *END_FLAT_EXECUTION_UNITS,
                        *END_FLAT_TOOL_UNITS,
                    ], timeout=60)
                    _end_flat_verify_runtime_stopped()
                except BaseException as observed:
                    rollback_failures.append(
                        "stop-stack:" + type(observed).__name__ + ":" +
                        str(observed))
            if receipt_path is not None and receipt_path.exists():
                try:
                    os.unlink(receipt_path)
                except BaseException as observed:
                    rollback_failures.append(
                        "remove-receipt:" + type(observed).__name__ + ":" +
                        str(observed))
            if (state_update_attempted and agent_for_rollback is not None and
                    state_snapshot is not None):
                try:
                    agent_for_rollback.write_json(AGENT_STATE, state_snapshot)
                    os.chown(AGENT_STATE, 0, 0)
                    os.chmod(AGENT_STATE, 0o600)
                except BaseException as observed:
                    rollback_failures.append(
                        "restore-state:" + type(observed).__name__ + ":" +
                        str(observed))
            if rollback_failures:
                raise RuntimeError(
                    "REARM_STACK_ROLLBACK_FAILED: " +
                    "; ".join(rollback_failures)) from error
            raise error
        print(
            "REARM_STACK_READY "
            f"campaign_id={values['HEPTA_LOCAL_AI_CAMPAIGN_ID']} "
            f"receipt_sha256={receipt_hash} position=0 active_orders=0 gross=0 "
            "agent_still_stopped=true paper_only=true live_authorized=false",
            flush=True)
    finally:
        if end_flat_descriptor is not None:
            try:
                fcntl.flock(end_flat_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(end_flat_descriptor)
        if risk_descriptor is not None:
            try:
                fcntl.flock(risk_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(risk_descriptor)
        try:
            fcntl.flock(lifecycle_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(lifecycle_descriptor)


def _campaign_policy_expired(
        campaign_id: str, failure_prefix: str) -> bool:
    """Prove the exact PAPER policy itself has fenced new entry authority.

    A durably disabled policy is already a stronger entry fence than clock
    expiry.  Recognizing both states also makes end-flat crash-resumable when
    the process stops after sealing policy but before writing its checkpoint.
    """
    metadata = os.lstat(CAMPAIGN_POLICY)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        raise RuntimeError(failure_prefix + "_POLICY_PATH_UNSAFE")
    try:
        policy = json.loads(CAMPAIGN_POLICY.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(failure_prefix + "_POLICY_INVALID") from error
    expires_at_ms = policy.get("expires_at_ms") \
        if isinstance(policy, dict) else None
    if (not isinstance(policy, dict) or
            policy.get("schema") not in RECOVERY_POLICY_SCHEMAS or
            policy.get("campaign_id") != campaign_id or
            policy.get("domain_id") != "alpha" or
            policy.get("paper_only") is not True or
            policy.get("live_authorized") is not False or
            not isinstance(policy.get("enabled"), bool) or
            not isinstance(policy.get("mutations_authorized"), bool) or
            not isinstance(expires_at_ms, int) or
            isinstance(expires_at_ms, bool)):
        raise RuntimeError(failure_prefix + "_POLICY_BOUNDARY_INVALID")
    disabled = (policy["enabled"] is False and
                policy["mutations_authorized"] is False)
    inconsistent = (policy["enabled"] is False) != (
        policy["mutations_authorized"] is False)
    if inconsistent:
        raise RuntimeError(failure_prefix + "_POLICY_BOUNDARY_INVALID")
    return disabled or expires_at_ms <= time.time_ns() // 1_000_000


def _end_flat_halt_campaign(
        agent: ModuleType, arguments: argparse.Namespace) -> str:

    try:
        response = agent.campaign(
            arguments, "halt", "end-flat-halt-" + uuid.uuid4().hex,
            ["--reason-code", "CAMPAIGN_TIME_LIMIT"], timeout=5)
    except Exception as error:
        # At the exact absolute deadline the policy can become inactive before
        # the halt request is serviced. The agent is already stopped and the
        # policy itself then rejects every new mutation, so risk reduction may
        # continue. Preserve the exact warning in the final receipt.
        if not _campaign_policy_expired(
                arguments.campaign_id, "END_FLAT"):
            raise RuntimeError(
                "END_FLAT_HALT_UNCONFIRMED_BEFORE_EXPIRY") from error
        return ("halt_unconfirmed_after_expiry:" + str(error))[:512]
    if response.get("status") != "ok":
        if not _campaign_policy_expired(
                arguments.campaign_id, "END_FLAT"):
            raise RuntimeError(
                "END_FLAT_HALT_REJECTED_BEFORE_EXPIRY")
        return ("halt_rejected_after_expiry:" + json.dumps(
            response, sort_keys=True, separators=(",", ":")))[:512]
    return "halt_confirmed"


def _end_flat_cancel_orders(
        agent: ModuleType, arguments: argparse.Namespace,
        terminal_order_ids: tuple[int, ...] | list[int] = (),
) -> tuple[list[int], list[int]]:
    """Single-owner adapter for the globally-covered cancellation workflow."""
    owner = "single-owner"
    token_path = Path(getattr(arguments, "token_file", owner + ".token"))
    cancelled, proven = _cancel_all_managed_session_orders(
        agent, {owner: (token_path, arguments)},
        {owner: set(terminal_order_ids)})
    return cancelled[owner], sorted(proven[owner])


def _load_end_flat_state(
        agent: ModuleType, state_path: Path,
) -> dict[str, object]:
    """Load the crash-durable end-flat mutation lineage without resetting it."""
    try:
        before = os.lstat(state_path)
    except FileNotFoundError:
        state = agent.empty_state()
        if not isinstance(state, dict):
            raise RuntimeError("END_FLAT_STATE_INVALID")
        return state
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            before.st_uid != 0 or before.st_gid != 0 or
            stat.S_IMODE(before.st_mode) != 0o600 or
            not 1 <= int(getattr(before, "st_size", 1)) <=
                AGENT_STATE_SNAPSHOT_MAX_BYTES):
        raise RuntimeError("END_FLAT_STATE_PATH_UNSAFE")
    try:
        state = agent.load_state(state_path)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("END_FLAT_STATE_INVALID") from error
    if not isinstance(state, dict):
        raise RuntimeError("END_FLAT_STATE_INVALID")
    # A normal lstat result includes inode identity.  Tests may provide a
    # reduced metadata object; production never does.  Rechecking closes the
    # pathname replacement window around the agent normalizer's read.
    if hasattr(before, "st_dev") and hasattr(before, "st_ino"):
        current = os.stat(state_path, follow_symlinks=False)
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or
                current.st_dev != before.st_dev or
                current.st_ino != before.st_ino or
                current.st_uid != before.st_uid or
                current.st_gid != before.st_gid or
                stat.S_IMODE(current.st_mode) !=
                    stat.S_IMODE(before.st_mode) or
                current.st_size != before.st_size or
                current.st_mtime_ns != before.st_mtime_ns or
                current.st_ctime_ns != before.st_ctime_ns):
            raise RuntimeError("END_FLAT_STATE_CHANGED_DURING_READ")
    _pending_mutation_identity(
        state, "END_FLAT_MUTATION_INTENT_INVALID")
    if state.get("pending_mutation_state_unproven") is True:
        raise RuntimeError("END_FLAT_MUTATION_IDENTITY_UNPROVEN")
    return state


def _require_risk_recovery_state_binding(
        state_path: Path, state: dict[str, object], campaign_id: str,
        failure_prefix: str,
) -> None:
    """Bind a scanned recovery projection to its campaign and owner lineage."""
    matched = re.fullmatch(
        r"risk-recovery-(?P<digest>[0-9a-f]{24})\.state\.json",
        state_path.name)
    if matched is None:
        raise RuntimeError(
            failure_prefix + "_RISK_RECOVERY_STATE_NAME_INVALID")
    if (not isinstance(campaign_id, str) or
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{2,255}", campaign_id) is None):
        raise RuntimeError(
            failure_prefix + "_RISK_RECOVERY_CAMPAIGN_INVALID")
    bound_campaign = state.get("campaign_id_at_suspend")
    suspension_id = state.get("suspension_id")
    if (not isinstance(bound_campaign, str) or
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{2,255}",
                bound_campaign) is None or
            not isinstance(suspension_id, str) or not suspension_id or
            len(suspension_id) > 512):
        raise RuntimeError(
            failure_prefix + "_RISK_RECOVERY_OWNERSHIP_AMBIGUOUS")
    if bound_campaign != campaign_id:
        # Historical files are retained for operator/audit action, but are never
        # silently folded into the current campaign's owner-scoped queries.
        raise RuntimeError(
            failure_prefix + "_FOREIGN_RISK_RECOVERY_STATE_PRESENT")
    digest = hashlib.sha256(
        suspension_id.encode("utf-8")).hexdigest()[:24]
    if digest != matched.group("digest"):
        raise RuntimeError(
            failure_prefix + "_RISK_RECOVERY_OWNERSHIP_AMBIGUOUS")
    mutation = _pending_mutation_identity(
        state, failure_prefix + "_RISK_RECOVERY_MUTATION_INVALID")
    pending_order_id = state.get("pending_order_id")
    if (pending_order_id is not None and
            (not isinstance(pending_order_id, int) or
             isinstance(pending_order_id, bool) or pending_order_id < 0)):
        raise RuntimeError(
            failure_prefix + "_RISK_RECOVERY_SESSION_LINEAGE_INVALID")
    if pending_order_id is not None and mutation is None:
        raise RuntimeError(
            failure_prefix + "_RISK_RECOVERY_SESSION_LINEAGE_INVALID")
    if mutation is not None:
        allowed_tokens = {
            "local-paper.token",
            "risk-recovery-" + digest + ".token",
        }
        if mutation["token_name"] not in allowed_tokens:
            raise RuntimeError(
                failure_prefix + "_RISK_RECOVERY_SESSION_LINEAGE_INVALID")


def _require_owned_active_order(
        agent: ModuleType, arguments: argparse.Namespace, order_id: int,
        failure_prefix: str,
) -> dict[str, object]:
    _snapshot, projection = _owner_order_projection(
        agent, arguments, failure_prefix)
    global_ids = set(projection["global_active_order_ids"])
    owned_ids = set(projection["owned_active_order_ids"])
    if order_id not in global_ids or order_id not in owned_ids:
        raise RuntimeError(failure_prefix + "_ORDER_OWNERSHIP_MISMATCH")
    return projection


def _end_flat_authoritative_proof(
        agent: ModuleType, arguments: argparse.Namespace,
) -> tuple[int, int]:
    position, position_generation, cash_generation = authoritative_state(
        agent, arguments)
    risk = agent.tool(arguments, "risk.get_limits", timeout=5)
    if (not isinstance(risk, dict) or risk.get("source") != "IB" or
            risk.get("authoritative") is not True or
            risk.get("gross_scope") != "PAPER_BASELINE_DELTA"):
        raise RuntimeError("END_FLAT_FINAL_RISK_NOT_AUTHORITATIVE")
    try:
        gross = float(risk.get("gross_absolute_position"))
    except (TypeError, ValueError) as error:
        raise RuntimeError("END_FLAT_FINAL_RISK_NOT_AUTHORITATIVE") from error
    if not math.isfinite(gross):
        raise RuntimeError("END_FLAT_FINAL_RISK_NOT_AUTHORITATIVE")
    if not agent._quantity_equal(position, 0.0) or gross != 0.0:
        raise RuntimeError("END_FLAT_FINAL_RISK_NOT_ZERO")
    return position_generation, cash_generation


def _end_flat_persist_policy_disabled(campaign_id: str) -> str:
    """Atomically revoke this PAPER campaign without touching LIVE policy."""
    metadata = os.lstat(CAMPAIGN_POLICY)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        raise RuntimeError("END_FLAT_POLICY_PATH_UNSAFE")
    try:
        policy = json.loads(CAMPAIGN_POLICY.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("END_FLAT_POLICY_INVALID") from error
    if (not isinstance(policy, dict) or
            policy.get("schema") not in RECOVERY_POLICY_SCHEMAS or
            policy.get("domain_id") != "alpha" or
            policy.get("campaign_id") != campaign_id or
            policy.get("paper_only") is not True or
            policy.get("live_authorized") is not False or
            not isinstance(policy.get("enabled"), bool) or
            not isinstance(policy.get("mutations_authorized"), bool)):
        raise RuntimeError("END_FLAT_POLICY_BOUNDARY_INVALID")
    sealed = dict(policy)
    sealed["enabled"] = False
    sealed["mutations_authorized"] = False
    payload = (json.dumps(
        sealed, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False) + "\n").encode("ascii")
    temporary = CAMPAIGN_POLICY.with_name(
        "." + CAMPAIGN_POLICY.name + "." + str(os.getpid()) + ".tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        stat.S_IMODE(metadata.st_mode))
    try:
        os.fchmod(descriptor, stat.S_IMODE(metadata.st_mode))
        os.fchown(descriptor, 0, 0)
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, CAMPAIGN_POLICY)
        directory = os.open(
            CAMPAIGN_POLICY.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    persisted = json.loads(CAMPAIGN_POLICY.read_text(encoding="ascii"))
    if persisted != sealed:
        raise RuntimeError("END_FLAT_POLICY_PERSISTENCE_INVALID")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _end_flat_revoke_local_paper_control() -> dict[str, object]:
    """Replace LOCAL_PAPER authority with verified DENY_ALL authority."""
    rendered = run_checked([
        LOCAL_PAPER_CONTROL, "disable", "--domain", "alpha",
    ], timeout=60)
    try:
        result = json.loads(rendered)
    except json.JSONDecodeError as error:
        raise RuntimeError("END_FLAT_CONTROL_RESPONSE_INVALID") from error
    if not isinstance(result, dict):
        raise RuntimeError("END_FLAT_CONTROL_RESPONSE_INVALID")
    manifest_sha256 = result.get("identity_manifest_sha256")
    if (result.get("mode") != "DENY_ALL" or
            result.get("paper_authorized") is not False or
            result.get("live_authorized") is not False or
            result.get("identity_count") != 0 or
            not isinstance(manifest_sha256, str) or
            not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                manifest_sha256)):
        raise RuntimeError("END_FLAT_CONTROL_RESPONSE_INVALID")
    return {
        "identity_manifest_sha256": manifest_sha256,
        "identity_count": 0,
    }


def _end_flat_verify_deny_all() -> dict[str, object]:
    rendered = run_checked([
        "/usr/libexec/hepta-broker-egress-policy",
        "--policy",
        "/usr/share/heptatrader/hepta-broker-network-policy-v1.json",
        "--identity-manifest",
        "/usr/share/heptatrader/hepta-service-identities-v1.json",
        "--check-deny-all",
    ], timeout=15)
    matched = re.fullmatch(
        r"hepta_broker_egress_policy: PASS policy_sha256="
        r"(?P<sha>[0-9a-f]{64}) authorized_connectors=0 "
        r"authorized_uids= protected_ports=4\s*", rendered)
    if matched is None:
        raise RuntimeError("END_FLAT_DENY_ALL_EVIDENCE_INVALID")
    return {
        "broker_policy_sha256": "sha256:" + matched.group("sha"),
        "authorized_connector_count": 0,
        "authorized_uids": [],
        "protected_port_count": 4,
    }


def _end_flat_verify_runtime_stopped() -> None:
    for unit in (*END_FLAT_EXECUTION_UNITS, *END_FLAT_TOOL_UNITS):
        if run([
                "/usr/bin/systemctl", "is-active", unit,
        ], timeout=10).returncode == 0:
            raise RuntimeError("END_FLAT_RUNTIME_UNIT_ACTIVE:" + unit)
    for path in END_FLAT_RUNTIME_SOCKETS:
        if path.exists() or path.is_symlink():
            raise RuntimeError("END_FLAT_RUNTIME_SOCKET_PRESENT:" + str(path))


def _write_root_json(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        metadata = os.lstat(path)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise RuntimeError("END_FLAT_CHECKPOINT_PATH_UNSAFE")
    temporary = path.with_name(
        "." + path.name + "." + str(os.getpid()) + ".tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        payload = (json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False) + "\n").encode("ascii")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise RuntimeError("ROOT_JSON_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
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


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_root_state_metadata(
        metadata: object, failure: str, *, expected_mode: int | None = None,
) -> int:
    mode = stat.S_IMODE(int(getattr(metadata, "st_mode")))
    size = int(getattr(metadata, "st_size", 0))
    if (not stat.S_ISREG(int(getattr(metadata, "st_mode"))) or
            int(getattr(metadata, "st_nlink")) != 1 or
            int(getattr(metadata, "st_uid")) != 0 or
            int(getattr(metadata, "st_gid")) != 0 or
            mode & 0o077 or
            (expected_mode is not None and mode != expected_mode) or
            size < 0 or size > AGENT_STATE_SNAPSHOT_MAX_BYTES):
        raise RuntimeError(failure)
    return mode


def _copy_root_state_snapshot(
        source: Path, destination: Path, source_metadata: object,
) -> None:
    """Create one durable byte-copy without changing source link count."""
    failure = "REPAIR_STATE_SNAPSHOT_SOURCE_UNSAFE"
    source_mode = _validate_root_state_metadata(source_metadata, failure)
    source_descriptor = os.open(
        source,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(source_descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != 0 or before.st_gid != 0 or
                stat.S_IMODE(before.st_mode) != source_mode or
                before.st_size < 0 or
                before.st_size > AGENT_STATE_SNAPSHOT_MAX_BYTES):
            raise RuntimeError(failure)
        # A real lstat result always has identity fields.  Comparing them to
        # the opened descriptor closes the lstat/open replacement window.
        if (hasattr(source_metadata, "st_dev") and
                hasattr(source_metadata, "st_ino") and
                (before.st_dev != int(getattr(source_metadata, "st_dev")) or
                 before.st_ino != int(getattr(source_metadata, "st_ino")) or
                 before.st_uid != 0 or before.st_gid != 0)):
            raise RuntimeError(failure)
        payload = bytearray()
        while len(payload) <= AGENT_STATE_SNAPSHOT_MAX_BYTES:
            chunk = os.read(
                source_descriptor,
                min(65_536, AGENT_STATE_SNAPSHOT_MAX_BYTES + 1 -
                    len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > AGENT_STATE_SNAPSHOT_MAX_BYTES:
            raise RuntimeError(failure)
        after = os.fstat(source_descriptor)
        if (after.st_dev != before.st_dev or after.st_ino != before.st_ino or
                after.st_nlink != 1 or after.st_uid != before.st_uid or
                after.st_gid != before.st_gid or
                stat.S_IMODE(after.st_mode) != source_mode or
                after.st_size != len(payload) or
                after.st_mtime_ns != before.st_mtime_ns or
                after.st_ctime_ns != before.st_ctime_ns):
            raise RuntimeError(failure)
        current = os.stat(source, follow_symlinks=False)
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or
                current.st_dev != before.st_dev or
                current.st_ino != before.st_ino or
                stat.S_IMODE(current.st_mode) != source_mode or
                current.st_size != len(payload)):
            raise RuntimeError(failure)
    finally:
        os.close(source_descriptor)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    destination_descriptor: int | None = None
    destination_created = False
    try:
        destination_descriptor = os.open(destination, flags, source_mode)
        destination_created = True
        os.fchmod(destination_descriptor, source_mode)
        os.fchown(destination_descriptor, 0, 0)
        offset = 0
        while offset < len(payload):
            written = os.write(destination_descriptor, payload[offset:])
            if written <= 0:
                raise RuntimeError("REPAIR_STATE_SNAPSHOT_WRITE_FAILED")
            offset += written
        copied = os.fstat(destination_descriptor)
        if (not stat.S_ISREG(copied.st_mode) or copied.st_nlink != 1 or
                copied.st_uid != 0 or copied.st_gid != 0 or
                stat.S_IMODE(copied.st_mode) != source_mode or
                copied.st_size != len(payload)):
            raise RuntimeError("REPAIR_STATE_SNAPSHOT_CREATE_UNSAFE")
        os.fsync(destination_descriptor)
    except BaseException:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
            destination_descriptor = None
        if destination_created:
            try:
                os.unlink(destination)
            except FileNotFoundError:
                pass
            _fsync_parent(destination)
        raise
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
    _fsync_parent(destination)


def _restore_root_state_snapshot(
        backup: Path, state_path: Path, source_mode: int,
) -> None:
    """Atomically restore a durable independent snapshot and sync its name."""
    failure = "REPAIR_STATE_SNAPSHOT_RESTORE_UNSAFE"
    metadata = os.lstat(backup)
    _validate_root_state_metadata(
        metadata, failure, expected_mode=source_mode)
    descriptor = os.open(
        backup,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_uid != 0 or opened.st_gid != 0 or
                stat.S_IMODE(opened.st_mode) != source_mode or
                opened.st_size < 0 or
                opened.st_size > AGENT_STATE_SNAPSHOT_MAX_BYTES):
            raise RuntimeError(failure)
        if (hasattr(metadata, "st_dev") and hasattr(metadata, "st_ino") and
                (opened.st_dev != int(getattr(metadata, "st_dev")) or
                 opened.st_ino != int(getattr(metadata, "st_ino")) or
                 opened.st_uid != 0 or opened.st_gid != 0)):
            raise RuntimeError(failure)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(backup, state_path)
    os.chown(state_path, 0, 0)
    os.chmod(state_path, source_mode)
    descriptor = os.open(
        state_path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        restored = os.fstat(descriptor)
        if (not stat.S_ISREG(restored.st_mode) or restored.st_nlink != 1 or
                restored.st_uid != 0 or restored.st_gid != 0 or
                stat.S_IMODE(restored.st_mode) != source_mode or
                restored.st_size != opened.st_size):
            raise RuntimeError(failure)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_parent(state_path)


def _rename_noreplace_at(
        directory_descriptor: int, source_name: str,
        destination_name: str) -> None:
    function = getattr(_LIBC, "renameat2", None)
    if function is None:
        raise RuntimeError("ROOT_JSON_NOREPLACE_UNAVAILABLE")
    function.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint)
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = function(
        directory_descriptor, os.fsencode(source_name),
        directory_descriptor, os.fsencode(destination_name),
        _RENAME_NOREPLACE)
    if result == 0:
        return
    number = ctypes.get_errno()
    if number == errno.EEXIST:
        raise FileExistsError(destination_name)
    raise RuntimeError("ROOT_JSON_NOREPLACE_FAILED") from OSError(
        number, os.strerror(number))


def _create_root_json_exclusive(path: Path, value: object) -> None:
    """Publish a complete root-only JSON file atomically without replacement."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = (json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False) + "\n").encode("ascii")
    directory_descriptor = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    temporary_name = "." + path.name + "." + secrets.token_hex(16) + ".tmp"
    descriptor: int | None = None
    temporary_created = False
    published = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            temporary_name, flags, 0o600, dir_fd=directory_descriptor)
        temporary_created = True
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise RuntimeError("ROOT_JSON_EXCLUSIVE_WRITE_FAILED")
            offset += written
        temporary_metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(temporary_metadata.st_mode) or
                temporary_metadata.st_nlink != 1 or
                (os.geteuid() == 0 and (
                    temporary_metadata.st_uid != 0 or
                    temporary_metadata.st_gid != 0)) or
                stat.S_IMODE(temporary_metadata.st_mode) != 0o600 or
                int(getattr(
                    temporary_metadata, "st_size", len(payload))) !=
                    len(payload)):
            raise RuntimeError("ROOT_JSON_EXCLUSIVE_TEMP_UNSAFE")
        os.fsync(descriptor)
        _rename_noreplace_at(
            directory_descriptor, temporary_name, path.name)
        published = True
        final_descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_descriptor)
        try:
            final_metadata = os.fstat(final_descriptor)
            if (not stat.S_ISREG(final_metadata.st_mode) or
                    final_metadata.st_nlink != 1 or
                    (os.geteuid() == 0 and (
                        final_metadata.st_uid != 0 or
                        final_metadata.st_gid != 0)) or
                    stat.S_IMODE(final_metadata.st_mode) != 0o600 or
                    int(getattr(final_metadata, "st_size", len(payload))) !=
                        len(payload) or
                    (hasattr(temporary_metadata, "st_dev") and
                     hasattr(temporary_metadata, "st_ino") and
                     (not hasattr(final_metadata, "st_dev") or
                      not hasattr(final_metadata, "st_ino") or
                      final_metadata.st_dev != temporary_metadata.st_dev or
                      final_metadata.st_ino != temporary_metadata.st_ino))):
                raise RuntimeError("ROOT_JSON_EXCLUSIVE_PUBLISH_UNSAFE")
            os.fsync(final_descriptor)
        finally:
            os.close(final_descriptor)
        os.fsync(directory_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_created and not published:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            os.fsync(directory_descriptor)
        os.close(directory_descriptor)


def _create_private_bytes_exclusive(
        path: Path, payload: bytes, *, uid: int, gid: int,
        failure_prefix: str, mode: int = 0o600,
) -> None:
    """Atomically publish complete private bytes without replacing residue."""
    if (not payload or not isinstance(uid, int) or isinstance(uid, bool) or
            uid < 0 or not isinstance(gid, int) or isinstance(gid, bool) or
            gid < 0 or not re.fullmatch(r"[A-Z0-9_]{3,96}", failure_prefix)):
        raise RuntimeError("PRIVATE_BYTES_EXCLUSIVE_ARGUMENT_INVALID")
    if mode not in {0o400, 0o600}:
        raise RuntimeError("PRIVATE_BYTES_EXCLUSIVE_ARGUMENT_INVALID")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_descriptor = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    temporary_name = "." + path.name + "." + secrets.token_hex(16) + ".tmp"
    descriptor: int | None = None
    temporary_created = False
    published = False
    try:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            temporary_name, flags, mode, dir_fd=directory_descriptor)
        temporary_created = True
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, uid, gid)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise RuntimeError(failure_prefix + "_WRITE_FAILED")
            offset += written
        temporary_metadata = os.fstat(descriptor)
        enforce_owner = os.geteuid() == 0
        if (not stat.S_ISREG(temporary_metadata.st_mode) or
                temporary_metadata.st_nlink != 1 or
                (enforce_owner and (
                    temporary_metadata.st_uid != uid or
                    temporary_metadata.st_gid != gid)) or
                stat.S_IMODE(temporary_metadata.st_mode) != mode or
                temporary_metadata.st_size != len(payload)):
            raise RuntimeError(failure_prefix + "_TEMP_UNSAFE")
        os.fsync(descriptor)
        _rename_noreplace_at(
            directory_descriptor, temporary_name, path.name)
        published = True
        final_descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_descriptor)
        try:
            final_metadata = os.fstat(final_descriptor)
            if (not stat.S_ISREG(final_metadata.st_mode) or
                    final_metadata.st_nlink != 1 or
                    (enforce_owner and (
                        final_metadata.st_uid != uid or
                        final_metadata.st_gid != gid)) or
                    stat.S_IMODE(final_metadata.st_mode) != mode or
                    final_metadata.st_size != len(payload) or
                    final_metadata.st_dev != temporary_metadata.st_dev or
                    final_metadata.st_ino != temporary_metadata.st_ino or
                    os.read(final_descriptor, len(payload) + 1) != payload or
                    os.read(final_descriptor, 1)):
                raise RuntimeError(failure_prefix + "_PUBLISH_UNSAFE")
            os.fsync(final_descriptor)
        finally:
            os.close(final_descriptor)
        os.fsync(directory_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_created and not published:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            os.fsync(directory_descriptor)
        os.close(directory_descriptor)


def _rename_root_file_noreplace(source: Path, destination: Path) -> None:
    if source.parent != destination.parent:
        raise RuntimeError("ROOT_JSON_RENAME_PARENT_MISMATCH")
    directory_descriptor = os.open(
        source.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        _rename_noreplace_at(
            directory_descriptor, source.name, destination.name)
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False) + "\n").encode("ascii")


def _sealed_json_document(body: dict[str, object]) -> dict[str, object]:
    """Return the canonical, self-hashed form used by recovery evidence."""
    if "body_sha256" in body:
        raise RuntimeError("EXTERNAL_RECOVERY_SEAL_BODY_ALREADY_PRESENT")
    return {
        **body,
        "body_sha256": "sha256:" + hashlib.sha256(
            _canonical_json_bytes(body)).hexdigest(),
    }


def _stable_file_bytes(
        path: Path, failure: str, *, expected_uid: int | None = None,
        expected_gid: int | None = None,
        allowed_modes: frozenset[int] = frozenset({0o600}),
        maximum_bytes: int = 16 * 1024 * 1024,
) -> tuple[bytes, os.stat_result]:
    """Read one exact regular-file inode without following any link."""
    before = os.lstat(path)
    mode = stat.S_IMODE(before.st_mode)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            (expected_uid is not None and before.st_uid != expected_uid) or
            (expected_gid is not None and before.st_gid != expected_gid) or
            mode not in allowed_modes or
            not 1 <= before.st_size <= maximum_bytes):
        raise RuntimeError(failure)
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        identity = (
            opened.st_dev, opened.st_ino, opened.st_mode, opened.st_nlink,
            opened.st_uid, opened.st_gid, opened.st_size,
            opened.st_mtime_ns, opened.st_ctime_ns)
        if identity != (
                before.st_dev, before.st_ino, before.st_mode,
                before.st_nlink, before.st_uid, before.st_gid,
                before.st_size, before.st_mtime_ns, before.st_ctime_ns):
            raise RuntimeError(failure)
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(
                descriptor, min(65_536, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        if (len(payload) != opened.st_size or len(payload) > maximum_bytes or
                (after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
                 after.st_uid, after.st_gid, after.st_size,
                 after.st_mtime_ns, after.st_ctime_ns) != identity or
                (current.st_dev, current.st_ino, current.st_mode,
                 current.st_nlink, current.st_uid, current.st_gid,
                 current.st_size, current.st_mtime_ns,
                 current.st_ctime_ns) != identity):
            raise RuntimeError(failure)
        return bytes(payload), opened
    finally:
        os.close(descriptor)


def _validate_canonical_sealed_document(
        raw: bytes, failure: str,
) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(failure) from error
    if not isinstance(value, dict) or not isinstance(
            value.get("body_sha256"), str):
        raise RuntimeError(failure)
    body = dict(value)
    claimed = body.pop("body_sha256")
    if (AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(claimed) is None or
            claimed == "sha256:" + "0" * 64 or
            claimed != "sha256:" + hashlib.sha256(
                _canonical_json_bytes(body)).hexdigest() or
            raw != _canonical_json_bytes(value)):
        raise RuntimeError(failure)
    return value


def _publish_immutable_sealed_json(
        path: Path, body: dict[str, object], failure: str,
) -> tuple[dict[str, object], bytes, os.stat_result]:
    """Create once, or prove that a prior crash published the exact value."""
    document = _sealed_json_document(body)
    try:
        _create_root_json_exclusive(path, document)
    except FileExistsError:
        pass
    raw, metadata = _stable_file_bytes(
        path, failure, expected_uid=0, expected_gid=0)
    persisted = _validate_canonical_sealed_document(raw, failure)
    if persisted != document:
        raise RuntimeError(failure + "_COLLISION")
    return document, raw, metadata


def _external_recovery_reference(
        path: Path, document: dict[str, object], raw: bytes,
        metadata: os.stat_result,
) -> dict[str, object]:
    return {
        "path": str(path),
        "file_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "body_sha256": document["body_sha256"],
        "schema": document["schema"],
        "status": document["status"],
        "bytes": len(raw),
        "mode": metadata.st_mode,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "nlink": metadata.st_nlink,
    }


def _decode_hsl7_text(value: str, failure: str) -> str:
    try:
        if re.fullmatch(r"(?:[0-9a-f][0-9a-f])*", value) is None:
            raise ValueError("noncanonical hex")
        return bytes.fromhex(value).decode("utf-8")
    except (UnicodeError, ValueError) as error:
        raise RuntimeError(failure) from error


def _external_hsl7_token_sha256(token: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", token) is None:
        raise RuntimeError("EXTERNAL_RECOVERY_HSL8_STORE_INVALID")
    return "sha256:" + hashlib.sha256(
        token.encode("ascii") + b"\n").hexdigest()


def _external_hsl8_unsigned(
        value: str, *, maximum: int = (1 << 64) - 1,
) -> int:
    failure = "EXTERNAL_RECOVERY_HSL8_STORE_INVALID"
    if (re.fullmatch(r"(?:0|[1-9][0-9]*)", value) is None or
            int(value) > maximum):
        raise RuntimeError(failure)
    return int(value)


def _external_hsl8_owner_set(
        canonical_hex: str, *, owner_set_sha256: str, owner_count: int,
        owner_account: str, owner_domain: str,
        member_token_sha256: str | None = None,
        member_generation: int | None = None,
        deterministic_member: bool = False,
) -> bytes:
    failure = "EXTERNAL_RECOVERY_HSL8_STORE_INVALID"
    try:
        if (not canonical_hex or
                re.fullmatch(r"(?:[0-9a-f][0-9a-f])+", canonical_hex) is None):
            raise ValueError("owner set hex")
        canonical = bytes.fromhex(canonical_hex)
        text = canonical.decode("ascii")
    except (UnicodeError, ValueError) as error:
        raise RuntimeError(failure) from error
    if (not canonical.endswith(b"\n") or "\r" in text or "\x00" in text or
            "sha256:" + hashlib.sha256(canonical).hexdigest() !=
                owner_set_sha256):
        raise RuntimeError(failure)
    lines = text[:-1].split("\n")
    if len(lines) != owner_count or not lines:
        raise RuntimeError(failure)
    rows: list[tuple[str, int, str, str]] = []
    for line in lines:
        fields = line.split("\t")
        if len(fields) != 4:
            raise RuntimeError(failure)
        token_sha256 = fields[0]
        generation = _external_hsl8_unsigned(fields[1])
        try:
            if (not fields[2] or not fields[3] or
                    re.fullmatch(r"(?:[0-9a-f][0-9a-f])+", fields[2]) is None or
                    re.fullmatch(r"(?:[0-9a-f][0-9a-f])+", fields[3]) is None):
                raise ValueError("owner scope hex")
            account = bytes.fromhex(fields[2]).decode("utf-8")
            domain = bytes.fromhex(fields[3]).decode("utf-8")
        except (UnicodeError, ValueError) as error:
            raise RuntimeError(failure) from error
        if (AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                token_sha256) is None or generation < 1 or
                account != owner_account or domain != owner_domain):
            raise RuntimeError(failure)
        rows.append((token_sha256, generation, account, domain))
    if ([row[0] for row in rows] != sorted(row[0] for row in rows) or
            len({row[0] for row in rows}) != len(rows)):
        raise RuntimeError(failure)
    if member_token_sha256 is not None:
        matches = [row for row in rows if row[0] == member_token_sha256]
        if (member_generation is None or len(matches) != 1 or
                matches[0][1] != member_generation or
                (deterministic_member and rows[0][0] != member_token_sha256)):
            raise RuntimeError(failure)
    return canonical


def _external_validate_hsl8_preliminary_receipt(
        receipt: str, *, recovery_id: str, finalization_id: str,
        owner_set_sha256: str, owner_count: int, owner_account: str,
        owner_domain: str, member_token_sha256: str | None = None,
        member_generation: int | None = None,
        deterministic_member: bool = False,
) -> tuple[dict[str, str], bytes, bytes]:
    failure = "EXTERNAL_RECOVERY_HSL8_STORE_INVALID"
    try:
        values, raw = _external_parse_finalization_receipt(receipt)
    except RuntimeError as error:
        raise RuntimeError(failure) from error
    if (
            values.get("schema") !=
                "hepta.paper-session-finalization-receipt.v1" or
            values.get("version") != "1" or
            values.get("status") != "AUDIT_SEALED" or
            values.get("recovery_id") != recovery_id or
            values.get("finalization_id") != finalization_id or
            values.get("expected_owner_set_sha256") != owner_set_sha256 or
            _external_hsl8_unsigned(
                values.get("expected_owner_count", ""), maximum=4096) !=
                owner_count or
            values.get("owner_account") != owner_account or
            values.get("owner_execution_domain") != owner_domain or
            re.fullmatch(r"DU[0-9]{1,16}", owner_account) is None or
            owner_domain != "PAPER:alpha" or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}",
                         values.get("execution_service_epoch", "")) is None or
            values.get("broker_post_fill_risk_reconciliation_pending") !=
                "0" or
            values.get("broker_recovery_audit_barrier_complete") != "1" or
            values.get("broker_recovery_audit_new_connection_epoch_required")
                != "0" or
            values.get("broker_position_quantity") != "0" or
            values.get("broker_gross_absolute_position") != "0" or
            values.get("paper_only") != "1" or
            values.get("live_authorized") != "0"):
        raise RuntimeError(failure)
    positive = (
        "execution_service_fencing_generation", "broker_connection_epoch",
        "broker_active_generation", "broker_terminal_generation",
        "broker_risk_generation", "broker_account_generation",
        "broker_position_generation", "broker_fx_cash_generation",
    )
    numeric = {
        field: _external_hsl8_unsigned(values.get(field, ""))
        for field in (
            *positive, "broker_exposure_generation",
            "broker_terminal_exposure_generation",
            "broker_risk_absorbed_exposure_generation",
            "broker_global_active_order_count", "owner_active_order_count",
            "owner_uncertain_command_count")
    }
    if (any(numeric[field] < 1 for field in positive) or
            numeric["broker_terminal_exposure_generation"] >
                numeric["broker_risk_absorbed_exposure_generation"] or
            numeric["broker_risk_absorbed_exposure_generation"] !=
                numeric["broker_exposure_generation"] or
            any(numeric[field] != 0 for field in (
                "broker_global_active_order_count", "owner_active_order_count",
                "owner_uncertain_command_count"))):
        raise RuntimeError(failure)
    canonical = _external_hsl8_owner_set(
        values.get("owner_set_canonical_hex", ""),
        owner_set_sha256=owner_set_sha256, owner_count=owner_count,
        owner_account=owner_account, owner_domain=owner_domain,
        member_token_sha256=member_token_sha256,
        member_generation=member_generation,
        deterministic_member=deterministic_member)
    return values, raw, canonical


def _external_validate_hsl8_terminal_receipt(
        receipt: str, *, preliminary: dict[str, str],
        preliminary_receipt_sha256: str, recovery_id: str,
        finalization_id: str, owner_set_sha256: str, owner_count: int,
        owner_account: str, owner_domain: str,
        member_token_sha256: str, member_generation: int,
        member_agent_id: str, member_session_id: str,
) -> tuple[dict[str, str], bytes, bytes]:
    failure = "EXTERNAL_RECOVERY_HSL8_STORE_INVALID"
    try:
        values, raw = _external_parse_terminal_ack_receipt(receipt)
    except RuntimeError as error:
        raise RuntimeError(failure) from error
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
    if (
            values.get("schema") !=
                "hepta.paper-session-terminal-ack-receipt.v3" or
            values.get("version") != "3" or
            values.get("status") != "TERMINAL_ACKED" or
            values.get("terminal_proof_kind") !=
                "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" or
            values.get("recovery_id") != recovery_id or
            values.get("finalization_id") != finalization_id or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,255}",
                         values.get("campaign_id", "")) is None or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}",
                         values.get("cycle_id", "")) is None or
            values.get("expected_owner_set_sha256") != owner_set_sha256 or
            _external_hsl8_unsigned(
                values.get("expected_owner_count", ""), maximum=4096) !=
                owner_count or
            values.get("preliminary_finalization_receipt_sha256") !=
                preliminary_receipt_sha256 or
            values.get("owner_agent_id") != member_agent_id or
            values.get("owner_session_id") != member_session_id or
            values.get("owner_account") != owner_account or
            values.get("owner_execution_domain") != owner_domain or
            values.get("account_id_sha256") != "sha256:" + hashlib.sha256(
                owner_account.encode("ascii")).hexdigest() or
            values.get("execution_service_epoch") !=
                preliminary.get("execution_service_epoch") or
            values.get("execution_service_fencing_generation") !=
                preliminary.get("execution_service_fencing_generation") or
            _external_hsl8_unsigned(
                values.get("recovery_ingress_fence", "")) !=
                member_generation or
            _external_hsl8_unsigned(
                values.get("terminalization_generation", "")) != 1 or
            values.get("provider_capability") !=
                "ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1" or
            values.get("snapshot_consistency") not in {
                "ATOMIC_ACCOUNT", "CAUSAL_WATERMARK"} or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
                         values.get("provider_id", "")) is None or
            re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}",
                values.get("host_boot_id", "")) is None or
            any(AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                    values.get(field, "")) is None or
                values.get(field) == "sha256:" + "0" * 64
                for field in digest_fields) or
            any(values.get(field) != "1" for field in truth_fields) or
            any(values.get(field) != "0"
                for field in false_fields | zero_fields)):
        raise RuntimeError(failure)
    positive = {
        "execution_service_fencing_generation", "recovery_ingress_fence",
        "terminalization_generation", "egress_publisher_pid",
        "egress_publisher_start_ticks", "egress_policy_generation",
    }
    numeric = {
        field: _external_hsl8_unsigned(values.get(field, ""))
        for field in positive | {
            "known_mutation_command_count", "known_correlation_count",
            "settled_mutation_command_count"}
    }
    if (any(numeric[field] < 1 for field in positive) or
            numeric["known_mutation_command_count"] > 4096 or
            numeric["known_correlation_count"] > 4096 or
            numeric["settled_mutation_command_count"] !=
                numeric["known_mutation_command_count"]):
        raise RuntimeError(failure)
    canonical = _external_hsl8_owner_set(
        values.get("owner_set_canonical_hex", ""),
        owner_set_sha256=owner_set_sha256, owner_count=owner_count,
        owner_account=owner_account, owner_domain=owner_domain,
        member_token_sha256=member_token_sha256,
        member_generation=member_generation, deterministic_member=True)
    return values, raw, canonical


def _external_hsl7_records(
        *, require_paper_owner: bool = True,
) -> tuple[list[dict[str, object]], str]:
    """Decrypt and snapshot exact HSL8 PAPER owner/terminal ledgers.

    The encrypted store is authoritative for the complete owner set.  Root
    bearer records are checked separately before any recovery runtime is
    enabled, so a missing local projection can never silently narrow it.  The
    historical helper name is retained to avoid widening the migration diff.
    """
    failure = "EXTERNAL_RECOVERY_HSL8_STORE_INVALID"
    gateway = pwd.getpwnam("hepta-gw-alpha")
    encoded, _store_metadata = _stable_file_bytes(
        SUPERVISOR_LEASE_STORE, failure,
        expected_uid=gateway.pw_uid, expected_gid=gateway.pw_gid,
        allowed_modes=frozenset({0o600}), maximum_bytes=2 * 1024 * 1024)
    key_raw, _key_metadata = _stable_file_bytes(
        SUPERVISOR_LEASE_KEY, "EXTERNAL_RECOVERY_HSL8_KEY_INVALID",
        expected_uid=0, expected_gid=0,
        allowed_modes=frozenset({0o400, 0o600}), maximum_bytes=65)
    key_value = key_raw.rstrip(b"\r\n")
    if len(key_value) == 64 and re.fullmatch(rb"[0-9a-f]{64}", key_value):
        key_value = bytes.fromhex(key_value.decode("ascii"))
    if len(key_value) != 32:
        raise RuntimeError("EXTERNAL_RECOVERY_HSL8_KEY_INVALID")
    try:
        lines = encoded.decode("ascii").splitlines()
        if len(lines) != 4 or lines[0] != "HSL2":
            raise ValueError("envelope")
        nonce = bytes.fromhex(lines[1])
        tag = bytes.fromhex(lines[2])
        ciphertext = bytes.fromhex(lines[3])
        if len(nonce) != 12 or len(tag) != 16:
            raise ValueError("envelope")
        # Keep this import local: recovery fails closed on minimal Python
        # installations rather than weakening the authenticated store format.
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        plaintext = AESGCM(key_value).decrypt(
            nonce, ciphertext + tag, SUPERVISOR_LEASE_AAD)
        rows = plaintext.decode("utf-8").splitlines()
    except (ImportError, UnicodeError, ValueError) as error:
        raise RuntimeError(failure) from error
    if not rows or rows[0] != "HSL8":
        raise RuntimeError(failure)
    records: list[dict[str, object]] = []
    tokens: set[str] = set()
    ack_bindings: set[tuple[str, str, str, int]] = set()
    ack_finalization_ids: set[str] = set()
    for row in rows[1:]:
        if not row:
            continue
        fields = row.split("\t")
        if fields[0] == "A":
            if len(fields) != 16:
                raise RuntimeError(failure)
            recovery_id = _decode_hsl7_text(fields[1], failure)
            finalization_id = _decode_hsl7_text(fields[2], failure)
            owner_set_sha256 = _decode_hsl7_text(fields[3], failure)
            owner_count = _external_hsl8_unsigned(fields[4], maximum=4096)
            receipt_sha256 = _decode_hsl7_text(fields[5], failure)
            receipt = _decode_hsl7_text(fields[6], failure)
            terminal_receipt_sha256 = _decode_hsl7_text(fields[7], failure)
            terminal_receipt = _decode_hsl7_text(fields[8], failure)
            ack_owner_sha256 = _decode_hsl7_text(fields[9], failure)
            ack_owner_generation = _external_hsl8_unsigned(fields[10])
            ack_owner_issuer = _decode_hsl7_text(fields[11], failure)
            ack_owner_agent_id = _decode_hsl7_text(fields[12], failure)
            ack_owner_session_id = _decode_hsl7_text(fields[13], failure)
            ack_owner_account = _decode_hsl7_text(fields[14], failure)
            ack_owner_domain = _decode_hsl7_text(fields[15], failure)
            binding = (
                recovery_id, finalization_id, owner_set_sha256, owner_count)
            if (
                    binding in ack_bindings or
                    finalization_id in ack_finalization_ids or
                    re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}",
                        recovery_id) is None or
                    re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}",
                        finalization_id) is None or owner_count < 1 or
                    AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                        owner_set_sha256) is None or
                    AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                        receipt_sha256) is None or
                    AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                        terminal_receipt_sha256) is None or
                    receipt_sha256 == terminal_receipt_sha256 or
                    AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                        ack_owner_sha256) is None or
                    ack_owner_generation < 1 or
                    any(re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", item) is None
                        for item in (
                            ack_owner_issuer, ack_owner_agent_id,
                            ack_owner_session_id)) or
                    re.fullmatch(r"DU[0-9]{1,16}", ack_owner_account) is None or
                    ack_owner_domain != "PAPER:alpha"):
                raise RuntimeError(failure)
            preliminary, receipt_raw, preliminary_canonical = (
                _external_validate_hsl8_preliminary_receipt(
                    receipt, recovery_id=recovery_id,
                    finalization_id=finalization_id,
                    owner_set_sha256=owner_set_sha256,
                    owner_count=owner_count, owner_account=ack_owner_account,
                    owner_domain=ack_owner_domain,
                    member_token_sha256=ack_owner_sha256,
                    member_generation=ack_owner_generation,
                    deterministic_member=True))
            _terminal, terminal_raw, terminal_canonical = (
                _external_validate_hsl8_terminal_receipt(
                    terminal_receipt, preliminary=preliminary,
                    preliminary_receipt_sha256=receipt_sha256,
                    recovery_id=recovery_id,
                    finalization_id=finalization_id,
                    owner_set_sha256=owner_set_sha256,
                    owner_count=owner_count, owner_account=ack_owner_account,
                    owner_domain=ack_owner_domain,
                    member_token_sha256=ack_owner_sha256,
                    member_generation=ack_owner_generation,
                    member_agent_id=ack_owner_agent_id,
                    member_session_id=ack_owner_session_id))
            if (
                    "sha256:" + hashlib.sha256(receipt_raw).hexdigest() !=
                        receipt_sha256 or
                    "sha256:" + hashlib.sha256(terminal_raw).hexdigest() !=
                        terminal_receipt_sha256 or
                    preliminary_canonical != terminal_canonical):
                raise RuntimeError(failure)
            ack_bindings.add(binding)
            ack_finalization_ids.add(finalization_id)
            continue
        if len(fields) != 27 or fields[0] != "R":
            raise RuntimeError(failure)
        try:
            template_id = _decode_hsl7_text(fields[1], failure)
            issuer = _decode_hsl7_text(fields[2], failure)
            token = _decode_hsl7_text(fields[3], failure)
            agent_id = _decode_hsl7_text(fields[4], failure)
            session_id = _decode_hsl7_text(fields[5], failure)
            peer_uid = _external_hsl8_unsigned(
                fields[6], maximum=(1 << 32) - 1)
            expires_at_ms = _external_hsl8_unsigned(fields[7])
            generation = _external_hsl8_unsigned(fields[8])
            predecessor_token = _decode_hsl7_text(fields[9], failure)
            predecessor_generation = _external_hsl8_unsigned(fields[10])
            fence_pending = _external_hsl8_unsigned(fields[11], maximum=1)
            fence_complete = _external_hsl8_unsigned(fields[12], maximum=1)
            fence_reason = _decode_hsl7_text(fields[13], failure)
            recovery_only = _external_hsl8_unsigned(fields[14], maximum=1)
            recovery_command_id = _decode_hsl7_text(fields[15], failure)
            paper_finalization_required = _external_hsl8_unsigned(
                fields[16], maximum=1)
            owner_account = _decode_hsl7_text(fields[17], failure)
            owner_execution_domain = _decode_hsl7_text(fields[18], failure)
            finalization_state = _external_hsl8_unsigned(
                fields[19], maximum=3)
            recovery_id = _decode_hsl7_text(fields[20], failure)
            finalization_id = _decode_hsl7_text(fields[21], failure)
            expected_owner_set_sha256 = _decode_hsl7_text(
                fields[22], failure)
            expected_owner_count = _external_hsl8_unsigned(
                fields[23], maximum=4096)
            owner_token_sha256 = _decode_hsl7_text(fields[24], failure)
            finalization_receipt_sha256 = _decode_hsl7_text(
                fields[25], failure)
            finalization_receipt = _decode_hsl7_text(fields[26], failure)
        except (TypeError, ValueError) as error:
            raise RuntimeError(failure) from error
        finalization_empty = (
            not recovery_id and not finalization_id and
            not expected_owner_set_sha256 and expected_owner_count == 0 and
            not owner_token_sha256 and not finalization_receipt_sha256 and
            not finalization_receipt)
        finalization_bound = (
            bool(recovery_id) and bool(finalization_id) and
            expected_owner_count > 0 and
            AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                expected_owner_set_sha256) is not None and
            AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                owner_token_sha256) is not None)
        canonical_token_sha256 = _external_hsl7_token_sha256(token)
        if finalization_state == 3:
            try:
                _receipt_fields, receipt_raw, _canonical = (
                    _external_validate_hsl8_preliminary_receipt(
                        finalization_receipt, recovery_id=recovery_id,
                        finalization_id=finalization_id,
                        owner_set_sha256=expected_owner_set_sha256,
                        owner_count=expected_owner_count,
                        owner_account=owner_account,
                        owner_domain=owner_execution_domain,
                        member_token_sha256=canonical_token_sha256,
                        member_generation=generation))
            except RuntimeError:
                finalization_bound = False
            else:
                finalization_bound = bool(
                    finalization_bound and
                    AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                        finalization_receipt_sha256) is not None and
                    "sha256:" + hashlib.sha256(receipt_raw).hexdigest() ==
                        finalization_receipt_sha256)
        elif finalization_receipt_sha256 or finalization_receipt:
            finalization_bound = False
        if (len(token) < 24 or token in tokens or
                template_id not in {"watch", "paper"} or not issuer or
                not agent_id or not session_id or peer_uid < 0 or
                expires_at_ms <= 0 or generation < 1 or
                fence_pending not in {0, 1} or fence_complete not in {0, 1} or
                recovery_only not in {0, 1} or
                paper_finalization_required not in {0, 1} or
                (bool(predecessor_token) != (predecessor_generation > 0)) or
                (template_id == "paper" and
                 (not owner_account or not owner_execution_domain)) or
                (template_id == "watch" and
                 (owner_account or owner_execution_domain or recovery_only or
                  paper_finalization_required)) or
                len(recovery_command_id) > 128 or
                finalization_state not in {0, 1, 2, 3} or
                (finalization_state == 0 and not finalization_empty) or
                (finalization_state != 0 and (
                    template_id != "paper" or recovery_only != 1 or
                    paper_finalization_required != 1 or
                    fence_pending != 0 or fence_complete != 0 or
                    not finalization_bound or
                    owner_token_sha256 != canonical_token_sha256 or
                    re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}",
                        recovery_id) is None or
                    re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}",
                        finalization_id) is None))):
            raise RuntimeError(failure)
        tokens.add(token)
        if template_id != "paper":
            continue
        records.append({
            "template_id": template_id,
            "issuer": issuer,
            "token_sha256": canonical_token_sha256,
            "agent_id": agent_id,
            "session_id": session_id,
            "peer_uid": peer_uid,
            "expires_at_ms": expires_at_ms,
            "lease_generation": generation,
            "predecessor_token_sha256": (
                "sha256:" + hashlib.sha256(
                    predecessor_token.encode("utf-8")).hexdigest()
                if predecessor_token else None),
            "predecessor_generation": predecessor_generation,
            "fence_pending": bool(fence_pending),
            "fence_complete": bool(fence_complete),
            "fence_reason": fence_reason,
            "recovery_only": bool(recovery_only),
            "recovery_command_id": recovery_command_id or None,
            "paper_finalization_required": bool(
                paper_finalization_required),
            "owner_account": owner_account,
            "owner_execution_domain": owner_execution_domain,
            "paper_finalization_state": (
                "NONE", "FENCE_PENDING", "FENCE_COMPLETE", "AUDIT_SEALED",
            )[finalization_state],
            "finalization_recovery_id": recovery_id or None,
            "finalization_id": finalization_id or None,
            "expected_owner_set_sha256": expected_owner_set_sha256 or None,
            "expected_owner_count": expected_owner_count,
            "owner_token_sha256": owner_token_sha256 or None,
            "finalization_receipt_sha256": (
                finalization_receipt_sha256 or None),
        })
    if any(str(record.get("finalization_id")) in ack_finalization_ids
           for record in records if record.get("finalization_id") is not None):
        raise RuntimeError(failure)
    if require_paper_owner and not records:
        raise RuntimeError("EXTERNAL_RECOVERY_SESSION_OWNER_MISSING")
    canonical = sorted(records, key=_canonical_json_bytes)
    if len({str(item["token_sha256"]) for item in canonical}) != len(canonical):
        raise RuntimeError(failure)
    return canonical, "sha256:" + hashlib.sha256(encoded).hexdigest()


def _external_validate_durable_owner_set(
        owners: list[dict[str, object]],
) -> tuple[list[dict[str, object]], str]:
    """Bind every recovery owner to the complete durable HSL8 projection."""
    durable, store_sha256 = _external_hsl7_records()
    if len(durable) != len(owners):
        raise RuntimeError("EXTERNAL_RECOVERY_DURABLE_OWNER_SET_MISMATCH")
    by_token = {str(item["token_sha256"]): item for item in durable}
    if len(by_token) != len(durable):
        raise RuntimeError("EXTERNAL_RECOVERY_DURABLE_OWNER_SET_MISMATCH")
    for owner in owners:
        item = by_token.get(str(owner["token_sha256"]))
        command_ids = owner.get("recovery_command_ids")
        if (item is None or not isinstance(command_ids, list) or
                item.get("agent_id") != "hepta-agent-alpha" or
                item.get("session_id") != owner.get("session_id") or
                item.get("peer_uid") != owner.get("peer_uid") or
                item.get("lease_generation") !=
                    owner.get("lease_generation") or
                item.get("owner_execution_domain") != "PAPER:alpha" or
                not isinstance(item.get("owner_account"), str) or
                re.fullmatch(r"DU[0-9]{1,16}", str(
                    item.get("owner_account"))) is None or
                item.get("fence_pending") is not False or
                item.get("fence_complete") is not False or
                item.get("paper_finalization_required") is not True or
                item.get("paper_finalization_state") != "NONE" or
                (item.get("recovery_command_id") is not None and
                 item.get("recovery_command_id") not in command_ids)):
            raise RuntimeError("EXTERNAL_RECOVERY_DURABLE_OWNER_SET_MISMATCH")
        # The immutable owner reference names the session, while HSL8 is the
        # authority for its exact broker scope.  Carry that scope into the
        # sealed checkpoint so neither recovery-query nor a Tool projection can
        # substitute another otherwise-valid DU/PAPER boundary on resume.
        owner["owner_account"] = item["owner_account"]
        owner["owner_execution_domain"] = item["owner_execution_domain"]
        owner["agent_id"] = item["agent_id"]
    return durable, store_sha256


def _external_p1_recovery_policy() -> tuple[dict[str, object], bytes]:
    raw, _metadata = _stable_file_bytes(
        CAMPAIGN_POLICY, "EXTERNAL_RECOVERY_POLICY_PATH_UNSAFE",
        expected_uid=0, expected_gid=0,
        allowed_modes=frozenset({0o600, 0o640, 0o644}))
    try:
        policy = json.loads(raw)
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("EXTERNAL_RECOVERY_POLICY_INVALID") from error
    duration = None
    if isinstance(policy, dict):
        after = policy.get("valid_after_ms")
        expires = policy.get("expires_at_ms")
        if type(after) is int and type(expires) is int:
            duration = expires - after
    if (not isinstance(policy, dict) or
            policy.get("schema") != ACTIVE_POLICY_SCHEMA or
            policy.get("version") != 5 or
            policy.get("admission_mode") != "external-p1-finalized" or
            policy.get("domain_id") != "alpha" or
            policy.get("paper_only") is not True or
            policy.get("live_authorized") is not False or
            type(policy.get("enabled")) is not bool or
            type(policy.get("mutations_authorized")) is not bool or
            (policy["enabled"] is False) !=
                (policy["mutations_authorized"] is False) or
            policy.get("order_type") != "LMT" or
            policy.get("tif") != "DAY" or
            policy.get("max_cycles") != 1 or
            policy.get("max_quantity") != 1 or
            policy.get("max_active_orders") != 1 or
            policy.get("end_flat_required") is not True or
            duration != EXTERNAL_P1_POLICY_DURATION_MS or
            policy.get("watch_handoff_receipt_path") !=
                "/var/lib/hepta/p1-admission/"
                "p1-watch-to-paper-handoff-receipt-v2.json" or
            any(not isinstance(policy.get(field), str) or
                AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                    str(policy[field])) is None or
                policy[field] == "sha256:" + "0" * 64
                for field in (
                    "source_baseline_sha256",
                    "watch_handoff_receipt_file_sha256",
                    "watch_handoff_receipt_body_sha256")) or
            not isinstance(policy.get("campaign_id"), str) or
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{2,255}",
                str(policy.get("campaign_id"))) is None):
        raise RuntimeError("EXTERNAL_RECOVERY_POLICY_INVALID")
    return policy, raw


def _external_reassert_disabled_policy(
        policy: dict[str, object],
) -> tuple[dict[str, object], bytes]:
    """Revoke entry on every recovery invocation before any broker access."""
    expected = dict(policy)
    expected["enabled"] = False
    expected["mutations_authorized"] = False
    _end_flat_persist_policy_disabled(str(policy["campaign_id"]))
    disabled, raw = _external_p1_recovery_policy()
    if disabled != expected:
        raise RuntimeError("EXTERNAL_RECOVERY_POLICY_NOT_TERMINAL")
    return disabled, raw


def _external_p1_runtime_profile() -> tuple[dict[str, str], bytes]:
    raw, _metadata = _stable_file_bytes(
        EXTERNAL_P1_RUNTIME_PROFILE,
        "EXTERNAL_RECOVERY_RUNTIME_PROFILE_INVALID",
        expected_uid=0, expected_gid=0,
        allowed_modes=frozenset({0o644}),
        maximum_bytes=EXTERNAL_P1_RUNTIME_PROFILE_BYTES)
    if (len(raw) != EXTERNAL_P1_RUNTIME_PROFILE_BYTES or
            "sha256:" + hashlib.sha256(raw).hexdigest() !=
                EXTERNAL_P1_RUNTIME_PROFILE_SHA256 or
            not raw.endswith(b"\n") or b"\r" in raw or b"\x00" in raw):
        raise RuntimeError("EXTERNAL_RECOVERY_RUNTIME_PROFILE_INVALID")
    try:
        lines = raw[:-1].decode("ascii", errors="strict").split("\n")
    except UnicodeError as error:
        raise RuntimeError(
            "EXTERNAL_RECOVERY_RUNTIME_PROFILE_INVALID") from error
    values: dict[str, str] = {}
    ordered_keys: list[str] = []
    for line in lines:
        if line.count("=") != 1:
            raise RuntimeError("EXTERNAL_RECOVERY_RUNTIME_PROFILE_INVALID")
        key, separator, value = line.partition("=")
        if (not separator or key in values or re.fullmatch(
                r"[A-Z][A-Z0-9_]{1,95}", key) is None or not value):
            raise RuntimeError("EXTERNAL_RECOVERY_RUNTIME_PROFILE_INVALID")
        values[key] = value
        ordered_keys.append(key)
    exact = {
        "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY": "1",
        "HEPTA_EXECUTION_MAX_ORDER_NOTIONAL": "5000",
        "HEPTA_IB_PAPER_MAX_ORDER_QTY": "1",
        "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL": "5000",
        "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE": "1",
        "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS": "1",
        "HEPTA_IB_PAPER_MAX_GROSS_POSITION": "1",
        "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS": "5000",
    }
    if (tuple(ordered_keys) != EXTERNAL_P1_RUNTIME_PROFILE_KEYS or
            set(values) != set(EXTERNAL_P1_RUNTIME_PROFILE_KEYS) or
            any(values.get(key) != value for key, value in exact.items())):
        raise RuntimeError("EXTERNAL_RECOVERY_RUNTIME_PROFILE_INVALID")
    return values, raw


def _external_recovery_owner_material(
        owner: object,
) -> tuple[dict[str, object], bytes, bytes]:
    failure = "EXTERNAL_RECOVERY_SESSION_OWNER_INVALID"
    try:
        authority_root_metadata = os.lstat(SESSION_AUTHORITY_ROOT)
    except OSError as error:
        raise RuntimeError(failure) from error
    if (not stat.S_ISDIR(authority_root_metadata.st_mode) or
            authority_root_metadata.st_uid != 0 or
            authority_root_metadata.st_gid != 0 or
            stat.S_IMODE(authority_root_metadata.st_mode) != 0o700):
        raise RuntimeError(failure)
    if (not isinstance(owner, dict) or
            set(owner) != EXTERNAL_CANARY_OWNER_FIELDS or
            owner.get("token_name") != "session.token" or
            owner.get("token_path") !=
                "/run/hepta-agent-alpha/sessions/session.token" or
            owner.get("authority_path") != str(
                SESSION_AUTHORITY_ROOT / "session.token.authority.json") or
            owner.get("revoke_bearer_path") != str(
                SESSION_AUTHORITY_ROOT / "session.token.revoke-token") or
            any(not isinstance(owner.get(field), str) or
                AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                    str(owner[field])) is None or
                owner[field] == "sha256:" + "0" * 64
                for field in (
                    "authority_file_sha256", "authority_body_sha256",
                    "token_sha256", "revoke_bearer_sha256")) or
            type(owner.get("lease_generation")) is not int or
            owner.get("lease_generation", 0) < 1 or
            not isinstance(owner.get("session_id"), str) or
            not owner["session_id"] or
            owner.get("peer_uid") != 2104 or
            owner.get("peer_gid") != 2104):
        raise RuntimeError(failure)
    authority_path = Path(str(owner["authority_path"]))
    authority_raw, _authority_metadata = _stable_file_bytes(
        authority_path, failure, expected_uid=0, expected_gid=0,
        allowed_modes=frozenset({0o600}))
    authority = _validate_canonical_sealed_document(authority_raw, failure)
    bearer_path = Path(str(owner["revoke_bearer_path"]))
    bearer_raw, _bearer_metadata = _stable_file_bytes(
        bearer_path, failure, expected_uid=0, expected_gid=0,
        allowed_modes=frozenset({0o600}), maximum_bytes=65)
    if (len(bearer_raw) != 65 or not bearer_raw.endswith(b"\n") or
            re.fullmatch(rb"[0-9a-f]{64}\n", bearer_raw) is None or
            "sha256:" + hashlib.sha256(authority_raw).hexdigest() !=
                owner["authority_file_sha256"] or
            authority.get("body_sha256") != owner["authority_body_sha256"] or
            "sha256:" + hashlib.sha256(bearer_raw).hexdigest() !=
                owner["revoke_bearer_sha256"] or
            owner["revoke_bearer_sha256"] != owner["token_sha256"] or
            authority.get("token_name") != owner["token_name"] or
            authority.get("session_id") != owner["session_id"] or
            authority.get("lease_generation") != owner["lease_generation"] or
            authority.get("peer_uid") != owner["peer_uid"] or
            authority.get("peer_gid", owner["peer_gid"]) !=
                owner["peer_gid"] or
            authority.get("token_sha256") != owner["token_sha256"]):
        raise RuntimeError(failure)
    return dict(owner), authority_raw, bearer_raw


def _external_recovery_journal(
        path: Path, expected_sha256: str, *, expected_uid: int,
        expected_gid: int,
) -> tuple[list[dict[str, object]], list[str], list[str], list[str]]:
    failure = "EXTERNAL_RECOVERY_EXECUTION_JOURNAL_INVALID"
    raw, _metadata = _stable_file_bytes(
        path, failure, expected_uid=expected_uid, expected_gid=expected_gid)
    if ("sha256:" + hashlib.sha256(raw).hexdigest() != expected_sha256 or
            not raw.endswith(b"\n")):
        raise RuntimeError(failure)
    entries: list[dict[str, object]] = []
    request_sha256s: list[str] = []
    response_sha256s: list[str] = []
    pending: dict[str, dict[str, object]] = {}
    previous_sequence: int | None = None
    for raw_line in raw.splitlines(keepends=True):
        try:
            value = json.loads(raw_line)
        except (UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(failure) from error
        if (not isinstance(value, dict) or
                raw_line != _canonical_json_bytes(value)):
            raise RuntimeError(failure)
        sequence = value.get("sequence", value.get("seq"))
        if type(sequence) is not int or sequence < 0:
            raise RuntimeError(failure)
        if previous_sequence is not None and sequence != previous_sequence + 1:
            raise RuntimeError(failure)
        previous_sequence = sequence
        record_type = str(value.get(
            "record_type", value.get("kind", value.get("type", "")))).upper()
        event = str(value.get("event", "")).upper()
        kind = event if record_type == "CALL" else record_type
        if not entries and record_type != "HEADER":
            raise RuntimeError(failure)
        if entries and record_type == "HEADER":
            raise RuntimeError(failure)
        if kind in {"REQUEST", "RESPONSE"}:
            call_id = value.get("tool_call_id", value.get("call_id"))
            if not isinstance(call_id, str) or not call_id:
                raise RuntimeError(failure)
            digest = "sha256:" + hashlib.sha256(raw_line).hexdigest()
            if kind == "REQUEST":
                if call_id in pending:
                    raise RuntimeError(failure)
                pending[call_id] = value
                request_sha256s.append(digest)
            else:
                if call_id not in pending:
                    raise RuntimeError(failure)
                pending.pop(call_id)
                response_sha256s.append(digest)
        elif record_type != "HEADER":
            raise RuntimeError(failure)
        entries.append(value)
    if not entries or len(pending) > 1:
        raise RuntimeError(failure)
    return entries, request_sha256s, response_sha256s, [expected_sha256]


def _external_canary_handoff(
        policy: dict[str, object], record: dict[str, object],
        cycle_directory: Path,
) -> tuple[dict[str, object], bytes]:
    failure = "EXTERNAL_RECOVERY_EXECUTION_HANDOFF_INVALID"
    path = (EXTERNAL_CANARY_CONTROL_ROOT / str(policy["campaign_id"]) /
            cycle_directory.name / "execution-handoff.v1.json")
    raw, _metadata = _stable_file_bytes(
        path, failure, expected_uid=0, expected_gid=0)
    handoff = _validate_canonical_sealed_document(raw, failure)
    owner = handoff.get("session_owner_reference") \
        if isinstance(handoff, dict) else None
    profile = handoff.get("runtime_profile_reference") \
        if isinstance(handoff, dict) else None
    root_cleanup = handoff.get("root_cleanup_call") \
        if isinstance(handoff, dict) else None
    issued_at_ms = handoff.get("issued_at_ms") \
        if isinstance(handoff, dict) else None
    expires_at_ms = handoff.get("expires_at_ms") \
        if isinstance(handoff, dict) else None
    _profile_values, profile_raw = _external_p1_runtime_profile()
    if (handoff.get("schema") != EXTERNAL_CANARY_HANDOFF_SCHEMA or
            handoff.get("version") != 1 or
            handoff.get("campaign_id") != policy.get("campaign_id") or
            handoff.get("domain_id") != "alpha" or
            handoff.get("cycle_id") != record.get("cycle_id") or
            handoff.get("policy_sha256") != record.get("policy_sha256") or
            handoff.get("strategy_sha256") != record.get("strategy_sha256") or
            handoff.get("decision_sha256") != record.get("decision_sha256") or
            handoff.get("intent_sha256") != record.get("intent_sha256") or
            handoff.get("watch_handoff_receipt_file_sha256") !=
                policy.get("watch_handoff_receipt_file_sha256") or
            handoff.get("watch_handoff_receipt_body_sha256") !=
                policy.get("watch_handoff_receipt_body_sha256") or
            handoff.get("installed_images_sha256") !=
                record.get("installed_images_sha256") or
            handoff.get("backend_transform_version") !=
                EXTERNAL_BACKEND_TRANSFORM_VERSION or
            owner != record.get("session_owner_reference") or
            not isinstance(profile, dict) or
            profile.get("path") != str(EXTERNAL_P1_RUNTIME_PROFILE) or
            profile.get("file_sha256") != record.get(
                "runtime_profile_sha256") or
            profile.get("file_sha256") != "sha256:" + hashlib.sha256(
                profile_raw).hexdigest() or
            not isinstance(root_cleanup, dict) or
            root_cleanup.get("effect") != "CONTROL" or
            root_cleanup.get("phase") != "ROOT_CLEANUP" or
            root_cleanup.get("operation") != "FINALIZE_EXTERNAL_P1" or
            root_cleanup.get("command_id") !=
                root_cleanup.get("tool_call_id") or
            type(issued_at_ms) is not int or type(expires_at_ms) is not int or
            not 0 < issued_at_ms < expires_at_ms or
            issued_at_ms > time.time_ns() // 1_000_000 or
            handoff.get("paper_only") is not True or
            handoff.get("live_authorized") is not False or
            handoff.get("direct_broker_access") is not False or
            handoff.get("authority_granted") is not False or
            handoff.get("one_order_only") is not True or
            handoff.get("end_flat_required") is not True or
            "sha256:" + hashlib.sha256(raw).hexdigest() !=
                record.get("handoff_file_sha256") or
            handoff.get("body_sha256") != record.get("handoff_body_sha256")):
        raise RuntimeError(failure)
    return handoff, raw


def _external_emergency_cleanup_bundle(
        record: dict[str, object], handoff: dict[str, object],
        handoff_raw: bytes, cycle_directory: Path,
        journal: list[dict[str, object]], *, peer_uid: int, peer_gid: int,
) -> dict[str, object] | None:
    """Validate the DENY_ALL/non-flat handoff without treating it as flat."""
    failure = "EXTERNAL_RECOVERY_EMERGENCY_CLEANUP_INVALID"
    paths = {
        "evidence": cycle_directory /
            "root-emergency-cleanup-evidence.v1.json",
        "request": cycle_directory /
            "root-emergency-cleanup-request.v1.json",
        "receipt": cycle_directory /
            "root-emergency-cleanup-receipt.v1.json",
        "owner": cycle_directory /
            "durable-recovery-owner-reference.v1.json",
    }
    present = {
        name: path.exists() or path.is_symlink()
        for name, path in paths.items()}
    if not any(present.values()):
        return None
    if not all(present.values()):
        raise RuntimeError(failure)
    evidence_raw, _ = _stable_file_bytes(
        paths["evidence"], failure, expected_uid=peer_uid,
        expected_gid=peer_gid)
    request_raw, _ = _stable_file_bytes(
        paths["request"], failure, expected_uid=peer_uid,
        expected_gid=peer_gid)
    receipt_raw, _ = _stable_file_bytes(
        paths["receipt"], failure, expected_uid=0, expected_gid=0)
    owner_raw, _ = _stable_file_bytes(
        paths["owner"], failure, expected_uid=0, expected_gid=0)
    evidence = _validate_canonical_sealed_document(evidence_raw, failure)
    request = _validate_canonical_sealed_document(request_raw, failure)
    receipt = _validate_canonical_sealed_document(receipt_raw, failure)
    owner_reference = _validate_canonical_sealed_document(owner_raw, failure)
    if (set(evidence) != EXTERNAL_EMERGENCY_EVIDENCE_FIELDS or
            set(request) != EXTERNAL_EMERGENCY_REQUEST_FIELDS or
            set(receipt) != EXTERNAL_EMERGENCY_RECEIPT_FIELDS or
            set(owner_reference) !=
                EXTERNAL_DURABLE_RECOVERY_OWNER_FIELDS):
        raise RuntimeError(failure)
    owner = handoff["session_owner_reference"]
    root_call = handoff["root_cleanup_call"]
    owner_sha256 = "sha256:" + hashlib.sha256(
        _canonical_json_bytes(owner)).hexdigest()
    handoff_file_sha256 = "sha256:" + hashlib.sha256(
        handoff_raw).hexdigest()
    journal_raw, _ = _stable_file_bytes(
        Path(str(record["journal_path"])), failure,
        expected_uid=peer_uid, expected_gid=peer_gid)
    last_sequence = journal[-1].get(
        "sequence", journal[-1].get("seq")) if journal else None
    images = handoff.get("installed_images")
    if not isinstance(images, list):
        raise RuntimeError(failure)
    image_by_role = {
        str(value.get("role")): value.get("file_sha256")
        for value in images if isinstance(value, dict)}
    common = {
        "campaign_id": handoff["campaign_id"], "domain_id": "alpha",
        "cycle_id": handoff["cycle_id"],
        "handoff_file_sha256": handoff_file_sha256,
        "handoff_body_sha256": handoff["body_sha256"],
        "session_owner_reference_sha256": owner_sha256,
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": handoff[
            "execution_service_fencing_generation"],
    }
    reasons = evidence.get("recovery_reason_codes")
    if (evidence.get("schema") != EXTERNAL_EMERGENCY_EVIDENCE_SCHEMA or
            evidence.get("version") != 1 or
            any(evidence.get(name) != value for name, value in common.items()) or
            evidence.get("handoff_path") != str(
                EXTERNAL_CANARY_CONTROL_ROOT / str(record["campaign_id"]) /
                str(record["cycle_id"]) / "execution-handoff.v1.json") or
            evidence.get("intent_sha256") != handoff.get("intent_sha256") or
            evidence.get("installed_images_sha256") !=
                handoff.get("installed_images_sha256") or
            evidence.get("executor_image_sha256") !=
                image_by_role.get("executor") or
            evidence.get("backend_adapter_image_sha256") !=
                image_by_role.get("backend-adapter") or
            evidence.get("root_finalizer_image_sha256") !=
                image_by_role.get("root-finalizer") or
            evidence.get("backend_transform_version") !=
                EXTERNAL_BACKEND_TRANSFORM_VERSION or
            evidence.get("journal_path") != record.get("journal_path") or
            evidence.get("journal_sha256") !=
                "sha256:" + hashlib.sha256(journal_raw).hexdigest() or
            evidence.get("journal_size") != len(journal_raw) or
            evidence.get("journal_last_sequence") != last_sequence or
            evidence.get("tool_evidence_sha256") !=
                record.get("tool_evidence_sha256") or
            not isinstance(reasons, list) or not reasons or
            reasons != record.get("reason_codes") or
            evidence.get("last_known_state_sha256") !=
                "sha256:" + hashlib.sha256(_canonical_json_bytes(
                    evidence.get("last_known_state"))).hexdigest() or
            evidence.get("broker_flat_proven") is not False or
            evidence.get("authority_granted") is not False):
        raise RuntimeError(failure)
    expected_evidence_ref = {
        "emergency_evidence_path": str(paths["evidence"]),
        "emergency_evidence_file_sha256": "sha256:" + hashlib.sha256(
            evidence_raw).hexdigest(),
        "emergency_evidence_body_sha256": evidence["body_sha256"],
    }
    if (request.get("schema") != EXTERNAL_EMERGENCY_REQUEST_SCHEMA or
            request.get("version") != 1 or
            any(request.get(name) != value for name, value in common.items()
                if name not in {"execution_handoff_path"}) or
            request.get("cleanup_tool_call_id") !=
                root_call["tool_call_id"] or
            request.get("cleanup_command_id") != root_call["command_id"] or
            request.get("tool_descriptor_sha256") !=
                root_call["tool_descriptor_sha256"] or
            any(request.get(name) != value for name, value in
                expected_evidence_ref.items()) or
            request.get("recovery_reason_codes") != reasons or
            request.get("required_actions") !=
                list(EXTERNAL_ROOT_CLEANUP_ACTIONS) or
            type(request.get("issued_at_ms")) is not int or
            type(request.get("expires_at_ms")) is not int or
            not 0 < request["issued_at_ms"] < request["expires_at_ms"] or
            request.get("broker_flat_proven") is not False or
            request.get("paper_only") is not True or
            request.get("live_authorized") is not False or
            request.get("authority_granted") is not False):
        raise RuntimeError(failure)
    expected_receipt = {
        "campaign_id": handoff["campaign_id"], "domain_id": "alpha",
        "cycle_id": handoff["cycle_id"],
        "cleanup_tool_call_id": root_call["tool_call_id"],
        "cleanup_command_id": root_call["command_id"],
        "tool_descriptor_sha256": root_call["tool_descriptor_sha256"],
        "execution_handoff_path": str(
            EXTERNAL_CANARY_CONTROL_ROOT / str(record["campaign_id"]) /
            str(record["cycle_id"]) / "execution-handoff.v1.json"),
        "execution_handoff_file_sha256": handoff_file_sha256,
        "execution_handoff_body_sha256": handoff["body_sha256"],
        "watch_handoff_file_sha256": handoff[
            "watch_handoff_receipt_file_sha256"],
        "watch_handoff_body_sha256": handoff[
            "watch_handoff_receipt_body_sha256"],
        "intent_sha256": handoff["intent_sha256"],
        "installed_images_sha256": handoff["installed_images_sha256"],
        "executor_image_sha256": image_by_role["executor"],
        "backend_adapter_image_sha256": image_by_role["backend-adapter"],
        "root_finalizer_image_sha256": image_by_role["root-finalizer"],
        "backend_transform_version": EXTERNAL_BACKEND_TRANSFORM_VERSION,
        "session_owner_reference_sha256": owner_sha256,
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": handoff[
            "execution_service_fencing_generation"],
        "journal_path": record["journal_path"],
        "journal_sha256": "sha256:" + hashlib.sha256(journal_raw).hexdigest(),
        "journal_size": len(journal_raw),
        "journal_last_sequence": last_sequence,
        "tool_evidence_sha256": record["tool_evidence_sha256"],
        **expected_evidence_ref,
        "root_emergency_cleanup_request_path": str(paths["request"]),
        "root_emergency_cleanup_request_file_sha256":
            "sha256:" + hashlib.sha256(request_raw).hexdigest(),
        "root_emergency_cleanup_request_body_sha256":
            request["body_sha256"],
        "recovery_reason_codes": reasons,
        "durable_owner_reference_sha256": owner_sha256,
        "durable_recovery_owner_reference_path": str(paths["owner"]),
        "durable_recovery_owner_reference_file_sha256":
            "sha256:" + hashlib.sha256(owner_raw).hexdigest(),
        "durable_recovery_owner_reference_body_sha256":
            owner_reference["body_sha256"],
    }
    if (receipt.get("schema") != EXTERNAL_EMERGENCY_RECEIPT_SCHEMA or
            receipt.get("version") != 1 or receipt.get("status") !=
                "ROOT_EMERGENCY_CLEANUP_COMPLETE_DENY_ALL" or
            any(receipt.get(name) != value
                for name, value in expected_receipt.items()) or
            receipt.get("completed_actions") !=
                list(EXTERNAL_ROOT_CLEANUP_ACTIONS) or
            receipt.get("broker_mutation_units") !=
                list(EXTERNAL_BROKER_MUTATION_UNITS) or
            receipt.get("broker_mutation_units_sha256") !=
                "sha256:" + hashlib.sha256(_canonical_json_bytes(
                    list(EXTERNAL_BROKER_MUTATION_UNITS))).hexdigest() or
            any(receipt.get(name) is not True for name in (
                "guardian_stopped", "execution_control_disabled",
                "kill_switch_engaged", "global_kill_switch_engaged",
                "broker_deny_all", "broker_mutation_units_inactive",
                "permit_absent", "guardian_runtime_absent",
                "recovery_required", "evidence_retained")) or
            any(receipt.get(name) != 0 for name in (
                "runtime_session_count", "authorized_connector_count",
                "identity_count")) or
            receipt.get("durable_owner_count") != 1 or
            receipt.get("durable_owner_status") != "RECOVERY_ONLY" or
            receipt.get("broker_flat_proven") is not False or
            receipt.get("paper_only") is not True or
            receipt.get("live_authorized") is not False or
            receipt.get("authority_granted") is not False or
            type(receipt.get("completed_at_ms")) is not int or
            not request["issued_at_ms"] <= receipt["completed_at_ms"] <=
                request["expires_at_ms"]):
        raise RuntimeError(failure)
    owner_material = handoff["session_owner_reference"]
    if (owner_reference.get("schema") !=
            EXTERNAL_DURABLE_RECOVERY_OWNER_SCHEMA or
            owner_reference.get("version") != 1 or
            owner_reference.get("campaign_id") != handoff["campaign_id"] or
            owner_reference.get("domain_id") != "alpha" or
            owner_reference.get("cycle_id") != handoff["cycle_id"] or
            owner_reference.get("cleanup_command_id") !=
                root_call["command_id"] or
            owner_reference.get("session_owner_reference_sha256") !=
                owner_sha256 or
            owner_reference.get("token_sha256") !=
                owner_material["token_sha256"] or
            owner_reference.get("lease_generation") !=
                owner_material["lease_generation"] or
            owner_reference.get("session_id") != owner_material["session_id"] or
            owner_reference.get("authoritative_command_status") is not True or
            owner_reference.get("recovery_only") is not True or
            owner_reference.get("paper_finalization_required") is not True or
            owner_reference.get("owner_fenced") is not False or
            owner_reference.get("owner_audit_authoritative") is not True or
            owner_reference.get("owner_audit_complete") is not True or
            owner_reference.get("execution_service_epoch") !=
                handoff["execution_service_epoch"] or
            owner_reference.get("execution_service_fencing_generation") !=
                handoff["execution_service_fencing_generation"] or
            type(owner_reference.get("completed_at_ms")) is not int or
            type(owner_reference.get("recovery_expires_at_ms")) is not int or
            owner_reference["recovery_expires_at_ms"] <=
                owner_reference["completed_at_ms"] or
            owner_reference.get("query_reason_code") not in {
                "RECOVERY_QUERY_CANNOT_FULL_FENCE",
                "RECOVERY_QUERY_PROVEN_RECOVERY_ONLY",
                "RECOVERY_QUERY_NOT_FOUND_PROVEN_RECOVERY_ONLY"} or
            owner_reference.get("command_status") not in {
                "accepted", "rejected", "uncertain", "not_found"} or
            not isinstance(owner_reference.get("command_reason_code"), str) or
            not owner_reference["command_reason_code"] or
            type(owner_reference.get("order_id")) is not int or
            owner_reference["order_id"] < -1 or
            any(type(owner_reference.get(name)) is not int or
                owner_reference[name] < 0 for name in (
                    "owner_active_order_count",
                    "owner_uncertain_command_count")) or
            any(type(owner_reference.get(name)) is not int or
                owner_reference[name] < 1 for name in (
                    "broker_connection_epoch", "broker_active_generation",
                    "broker_terminal_generation")) or
            not isinstance(owner_reference.get("owner_account"), str) or
            re.fullmatch(r"DU[0-9]{1,16}", str(
                owner_reference["owner_account"])) is None or
            not isinstance(owner_reference.get("owner_execution_domain"), str) or
            re.fullmatch(r"PAPER(?::[A-Za-z0-9._:-]{1,128})?", str(
                owner_reference["owner_execution_domain"])) is None or
            owner_reference.get("runtime_session_count") != 0 or
            owner_reference.get("durable_owner_count") != 1 or
            owner_reference.get("durable_owner_status") != "RECOVERY_ONLY" or
            owner_reference.get("paper_only") is not True or
            owner_reference.get("live_authorized") is not False or
            owner_reference.get("authority_granted") is not False):
        raise RuntimeError(failure)
    return {
        "evidence_path": str(paths["evidence"]),
        "evidence_file_sha256": expected_evidence_ref[
            "emergency_evidence_file_sha256"],
        "evidence_body_sha256": evidence["body_sha256"],
        "request_path": str(paths["request"]),
        "request_file_sha256": expected_receipt[
            "root_emergency_cleanup_request_file_sha256"],
        "request_body_sha256": request["body_sha256"],
        "receipt_path": str(paths["receipt"]),
        "receipt_file_sha256": "sha256:" + hashlib.sha256(
            receipt_raw).hexdigest(),
        "receipt_body_sha256": receipt["body_sha256"],
        "durable_owner_reference_path": str(paths["owner"]),
        "durable_owner_reference_file_sha256": expected_receipt[
            "durable_recovery_owner_reference_file_sha256"],
        "durable_owner_reference_body_sha256": owner_reference[
            "body_sha256"],
        "broker_flat_proven": False, "recovery_required": True,
        "durable_owner_count": 1,
        "durable_owner_status": "RECOVERY_ONLY",
    }


def _external_canary_recovery_records(
        policy: dict[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]],
           dict[str, list[str]]]:
    """Load every sealed executor incident and its immutable HSL8 owner."""
    campaign_id = str(policy["campaign_id"])
    root = EXTERNAL_CANARY_ROOT / campaign_id
    try:
        candidates = sorted({
            *root.glob("*/recovery-record.v1.json"),
            *root.glob("*/recovery-record-v1.json"),
        })
    except OSError as error:
        raise RuntimeError("EXTERNAL_RECOVERY_RECORD_SCAN_FAILED") from error
    if not candidates:
        raise RuntimeError("EXTERNAL_RECOVERY_RECORD_MISSING")
    records: list[dict[str, object]] = []
    owners_by_token: dict[str, dict[str, object]] = {}
    lineage = {
        "place_call_ids": [], "cancel_call_ids": [],
        "flatten_call_ids": [], "request_sha256s": [],
        "response_sha256s": [], "journal_sha256s": [],
    }
    for path in candidates:
        if (path.name not in {
                "recovery-record.v1.json", "recovery-record-v1.json"} or
                path.parent.parent != root or any(
                    candidate.parent == path.parent and candidate != path
                    for candidate in candidates)):
            raise RuntimeError("EXTERNAL_RECOVERY_RECORD_PATH_INVALID")
        raw, record_metadata = _stable_file_bytes(
            path, "EXTERNAL_RECOVERY_RECORD_INVALID")
        record = _validate_canonical_sealed_document(
            raw, "EXTERNAL_RECOVERY_RECORD_INVALID")
        journal_path = path.parent / "execution-journal.v1.jsonl"
        if (set(record) != EXTERNAL_CANARY_RECOVERY_FIELDS or
                record.get("schema") != EXTERNAL_CANARY_RECOVERY_SCHEMA or
                record.get("version") != 1 or
                record.get("status") != EXTERNAL_CANARY_RECOVERY_STATUS or
                record.get("campaign_id") != campaign_id or
                record.get("domain_id") != "alpha" or
                record.get("journal_path") != str(journal_path) or
                type(record.get("created_at_ms")) is not int or
                record.get("created_at_ms", 0) <= 0 or
                record.get("recovery_required") is not True or
                record.get("cleanup_complete") is not False or
                record.get("authority_granted") is not False or
                record.get("backend_transform_version") !=
                    EXTERNAL_BACKEND_TRANSFORM_VERSION or
                not isinstance(record.get("reason_codes"), list) or
                not record["reason_codes"] or
                any(not isinstance(reason, str) or
                    re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", reason) is None
                    for reason in record["reason_codes"]) or
                any(not isinstance(record.get(field), str) or
                    AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                        str(record[field])) is None or
                    record[field] == "sha256:" + "0" * 64
                    for field in (
                        "policy_sha256", "strategy_sha256",
                        "decision_sha256", "intent_sha256",
                        "installed_images_sha256", "runtime_profile_sha256",
                        "journal_file_sha256", "tool_evidence_sha256",
                        "last_authoritative_snapshot_sha256"))):
            raise RuntimeError("EXTERNAL_RECOVERY_RECORD_INVALID")
        handoff, _handoff_raw = _external_canary_handoff(
            policy, record, path.parent)
        planned_calls = handoff.get("tool_calls")
        if not isinstance(planned_calls, list):
            raise RuntimeError("EXTERNAL_RECOVERY_EXECUTION_HANDOFF_INVALID")
        planned_mutations: dict[str, str] = {}
        for role in ("place", "cancel-order", "flatten-position"):
            matching_planned = [
                value for value in planned_calls
                if isinstance(value, dict) and value.get("call_role") == role]
            if len(matching_planned) != 1:
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_EXECUTION_HANDOFF_INVALID")
            planned_call = matching_planned[0]
            planned_id = planned_call.get("command_id")
            if (planned_call.get("tool_call_id") != planned_id or
                    planned_call.get("effect") != "MUTATION" or
                    not isinstance(planned_id, str) or re.fullmatch(
                        r"[A-Za-z0-9._:-]{8,128}", planned_id) is None):
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_EXECUTION_HANDOFF_INVALID")
            planned_mutations[role] = planned_id
        owner, _authority_raw, _bearer_raw = (
            _external_recovery_owner_material(
                record.get("session_owner_reference")))
        if (record_metadata.st_uid != owner["peer_uid"] or
                record_metadata.st_gid != owner["peer_gid"]):
            raise RuntimeError("EXTERNAL_RECOVERY_RECORD_INVALID")
        (journal, requests, responses, journals) = (
            _external_recovery_journal(
                journal_path, str(record["journal_file_sha256"]),
                expected_uid=int(owner["peer_uid"]),
                expected_gid=int(owner["peer_gid"])))
        emergency = _external_emergency_cleanup_bundle(
            record, handoff, _handoff_raw, path.parent, journal,
            peer_uid=int(owner["peer_uid"]),
            peer_gid=int(owner["peer_gid"]))
        uncertain_call = record.get("uncertain_tool_call_id")
        if not isinstance(uncertain_call, str) or not uncertain_call:
            raise RuntimeError("EXTERNAL_RECOVERY_RECORD_INVALID")
        matching = [
            entry for entry in journal
            if entry.get("tool_call_id", entry.get("call_id")) ==
                uncertain_call and (
                    str(entry.get("event", "")).upper() == "REQUEST" or
                    str(entry.get(
                        "record_type", entry.get(
                            "kind", entry.get("type", "")))).upper() ==
                        "REQUEST")]
        if len(matching) > 1:
            raise RuntimeError("EXTERNAL_RECOVERY_RECORD_INVALID")
        # READ_ONLY uncertainty has no command identity.  In that case use the
        # newest earlier effectful request for this owner.  If the incident
        # occurred before any CONTROL/MUTATION request, recovery cannot prove
        # the mandatory owner fence and therefore remains DENY_ALL.
        query_request = matching[0] if matching else None
        effectful_roles = {"place", "cancel-order", "flatten-position"}
        if (query_request is None or
                query_request.get("call_role") not in effectful_roles or
                query_request.get("command_id") is None):
            prior_effectful = [
                entry for entry in journal
                if entry.get("call_role") in effectful_roles and
                isinstance(entry.get("command_id"), str) and (
                    str(entry.get("event", "")).upper() == "REQUEST" or
                    str(entry.get(
                        "record_type", entry.get(
                            "kind", entry.get("type", "")))).upper() ==
                        "REQUEST")]
            if not prior_effectful:
                command_id = planned_mutations["place"]
            else:
                query_request = prior_effectful[-1]
                command_id = query_request.get("command_id")
        else:
            command_id = query_request.get("command_id")
        if (not isinstance(command_id, str) or
                re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", command_id) is None):
            raise RuntimeError("EXTERNAL_RECOVERY_COMMAND_ID_MISSING")
        root_cleanup_command_id = handoff["root_cleanup_call"]["command_id"]
        owner["recovery_command_id"] = command_id
        owner["recovery_command_ids"] = sorted(set([
            command_id, root_cleanup_command_id,
            *planned_mutations.values(),
        ]))
        owner["recovery_cancel_command_id"] = planned_mutations[
            "cancel-order"]
        owner["recovery_flatten_command_id"] = planned_mutations[
            "flatten-position"]
        owner["recovery_record_path"] = str(path)
        owner["recovery_record_file_sha256"] = (
            "sha256:" + hashlib.sha256(raw).hexdigest())
        owner["recovery_record_body_sha256"] = record["body_sha256"]
        token_sha256 = str(owner["token_sha256"])
        prior = owners_by_token.get(token_sha256)
        owner["recovery_record_created_at_ms"] = record["created_at_ms"]
        comparison = {
                key: value for key, value in owner.items()
                if key not in {"recovery_command_id", "recovery_command_ids"} and
            not key.startswith("recovery_record_")}
        if prior is not None:
            prior_comparison = {
                key: value for key, value in prior.items()
                if key not in {
                    "recovery_command_id", "recovery_command_ids"} and
                not key.startswith("recovery_record_")}
            if prior_comparison != comparison:
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_SESSION_OWNER_LINEAGE_CONFLICT")
            command_ids = prior.get("recovery_command_ids")
            if not isinstance(command_ids, list):
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_SESSION_OWNER_LINEAGE_CONFLICT")
            if command_id not in command_ids:
                command_ids.append(command_id)
                command_ids.sort()
            # The newest sealed incident command is the one that must be
            # resolved.  Ordering by created_at/cycle is deterministic.
            if int(record["created_at_ms"]) > int(
                    prior.get("recovery_record_created_at_ms", 0)):
                owner["recovery_command_ids"] = list(command_ids)
                owners_by_token[token_sha256] = owner
        else:
            owners_by_token[token_sha256] = owner
        place_id = record.get("place_call_id")
        if record.get("place_attempted") is True:
            if not isinstance(place_id, str) or not place_id:
                raise RuntimeError("EXTERNAL_RECOVERY_RECORD_INVALID")
            lineage["place_call_ids"].append(place_id)
        tool_name = (str(matching[0].get(
            "tool_name", matching[0].get("name", ""))) if matching else "")
        if "cancel" in tool_name:
            lineage["cancel_call_ids"].append(uncertain_call)
        elif "flatten" in tool_name or record.get("close_attempted") is True:
            lineage["flatten_call_ids"].append(uncertain_call)
        elif uncertain_call not in lineage["place_call_ids"]:
            lineage["place_call_ids"].append(uncertain_call)
        lineage["request_sha256s"].extend(requests)
        lineage["response_sha256s"].extend(responses)
        lineage["journal_sha256s"].extend(journals)
        lineage["place_call_ids"].append(planned_mutations["place"])
        lineage["cancel_call_ids"].append(
            planned_mutations["cancel-order"])
        lineage["flatten_call_ids"].append(
            planned_mutations["flatten-position"])
        record["_path"] = str(path)
        record["_file_sha256"] = (
            "sha256:" + hashlib.sha256(raw).hexdigest())
        record["_handoff_path"] = str(
            EXTERNAL_CANARY_CONTROL_ROOT / campaign_id /
            str(record["cycle_id"]) / "execution-handoff.v1.json")
        record["_root_cleanup_call"] = handoff["root_cleanup_call"]
        record["_emergency_cleanup"] = emergency
        records.append(record)
    owners = sorted(
        owners_by_token.values(),
        key=lambda value: _canonical_json_bytes({
            key: item for key, item in value.items()
            if not key.startswith("recovery_record_")}))
    if not owners:
        raise RuntimeError("EXTERNAL_RECOVERY_SESSION_OWNER_MISSING")
    for values in lineage.values():
        values[:] = sorted(set(values))
    if not any(lineage[name] for name in (
            "place_call_ids", "cancel_call_ids", "flatten_call_ids")):
        raise RuntimeError("EXTERNAL_RECOVERY_MUTATION_LINEAGE_EMPTY")
    return records, owners, lineage


def _external_recovery_artifact_paths(
        suspension_id: str,
) -> dict[str, Path]:
    digest = hashlib.sha256(
        suspension_id.encode("utf-8")).hexdigest()[:24]
    prefix = END_FLAT_RECEIPT_ROOT / ("external-recovery-" + digest)
    return {
        "policy_preimage_reference": prefix.with_name(
            prefix.name + ".policy-preimage.json"),
        "incident_state_reference": prefix.with_name(
            prefix.name + ".incident-state.json"),
        "mutation_lineage_reference": prefix.with_name(
            prefix.name + ".mutation-lineage.json"),
        "session_owner_set_reference": prefix.with_name(
            prefix.name + ".session-owners.json"),
        "checkpoint": prefix.with_name(prefix.name + ".checkpoint.json"),
        "terminal_flat": prefix.with_name(
            prefix.name + ".terminal-flat.json"),
    }


def _external_recovery_cycle_id(authority: dict[str, object]) -> str:
    """Return the one immutable external-P1 cycle bound to this recovery."""
    failure = "EXTERNAL_RECOVERY_CYCLE_LINEAGE_INVALID"
    reference = authority.get("mutation_lineage_reference")
    if not isinstance(reference, dict):
        raise RuntimeError(failure)
    path_value = reference.get("path")
    if not isinstance(path_value, str):
        raise RuntimeError(failure)
    raw, metadata = _stable_file_bytes(
        Path(path_value), failure, expected_uid=0, expected_gid=0)
    artifact = _validate_canonical_sealed_document(raw, failure)
    cycle_ids = artifact.get("cycle_ids")
    recovery_records = artifact.get("executor_recovery_records")
    if (_external_recovery_reference(
            Path(path_value), artifact, raw, metadata) != reference or
            artifact.get("campaign_id") != authority.get("campaign_id") or
            artifact.get("suspension_id") != authority.get("suspension_id") or
            artifact.get("source_baseline_sha256") !=
                authority.get("source_baseline_sha256") or
            not isinstance(cycle_ids, list) or len(cycle_ids) != 1 or
            not isinstance(cycle_ids[0], str) or
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", cycle_ids[0]) is None or
            not isinstance(recovery_records, list) or not recovery_records or
            any(not isinstance(record, dict) or
                record.get("cycle_id") != cycle_ids[0]
                for record in recovery_records)):
        raise RuntimeError(failure)
    return cycle_ids[0]


def _load_external_recovery_authority(
        policy: dict[str, object], *, required: bool = False,
) -> tuple[dict[str, object], bytes, os.stat_result] | None:
    try:
        raw, metadata = _stable_file_bytes(
            EXTERNAL_RECOVERY_AUTHORITY,
            "EXTERNAL_RECOVERY_AUTHORITY_INVALID",
            expected_uid=0, expected_gid=0)
    except FileNotFoundError:
        if required:
            raise RuntimeError("EXTERNAL_RECOVERY_AUTHORITY_MISSING")
        return None
    authority = _validate_canonical_sealed_document(
        raw, "EXTERNAL_RECOVERY_AUTHORITY_INVALID")
    references = tuple(EXTERNAL_RECOVERY_REFERENCE_SPECS)
    if (authority.get("schema") != EXTERNAL_RECOVERY_AUTHORITY_SCHEMA or
            authority.get("version") != 1 or
            authority.get("status") != EXTERNAL_RECOVERY_AUTHORITY_STATUS or
            authority.get("domain") != "alpha" or
            authority.get("campaign_id") != policy.get("campaign_id") or
            authority.get("source_baseline_sha256") !=
                policy.get("source_baseline_sha256") or
            authority.get("watch_handoff_receipt_path") !=
                policy.get("watch_handoff_receipt_path") or
            authority.get("watch_handoff_receipt_file_sha256") !=
                policy.get("watch_handoff_receipt_file_sha256") or
            authority.get("watch_handoff_receipt_body_sha256") !=
                policy.get("watch_handoff_receipt_body_sha256") or
            authority.get("recovery_required") is not True or
            authority.get("reduce_only") is not True or
            authority.get("paper_only") is not True or
            any(authority.get(field) is not False for field in (
                "live_authorized", "entry_authorized",
                "order_submission_authorized",
                "session_provision_authorized")) or
            type(authority.get("session_owner_count")) is not int or
            authority.get("session_owner_count", 0) < 1 or
            authority.get("all_original_session_owners_bound") is not True or
            not isinstance(authority.get("suspension_id"), str) or
            not authority["suspension_id"] or
            not isinstance(authority.get("recovery_id"), str) or
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}",
                str(authority["recovery_id"])) is None or
            any(not isinstance(authority.get(name), dict)
                for name in references)):
        raise RuntimeError("EXTERNAL_RECOVERY_AUTHORITY_INVALID")
    seen_paths: set[str] = set()
    for name, (schema, status_value) in (
            EXTERNAL_RECOVERY_REFERENCE_SPECS.items()):
        reference = authority[name]
        assert isinstance(reference, dict)
        path_value = reference.get("path")
        if (not isinstance(path_value, str) or path_value in seen_paths or
                reference.get("schema") != schema or
                reference.get("status") != status_value):
            raise RuntimeError("EXTERNAL_RECOVERY_AUTHORITY_INVALID")
        seen_paths.add(path_value)
        artifact_raw, artifact_metadata = _stable_file_bytes(
            Path(path_value), "EXTERNAL_RECOVERY_REFERENCE_DRIFTED",
            expected_uid=0, expected_gid=0)
        artifact = _validate_canonical_sealed_document(
            artifact_raw, "EXTERNAL_RECOVERY_REFERENCE_DRIFTED")
        if _external_recovery_reference(
                Path(path_value), artifact, artifact_raw,
                artifact_metadata) != reference:
            raise RuntimeError("EXTERNAL_RECOVERY_REFERENCE_DRIFTED")
        if name == "mutation_lineage_reference":
            cycle_ids = artifact.get("cycle_ids")
            recovery_records = artifact.get("executor_recovery_records")
            if (not isinstance(cycle_ids, list) or len(cycle_ids) != 1 or
                    not isinstance(cycle_ids[0], str) or
                    re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}",
                        cycle_ids[0]) is None or
                    not isinstance(recovery_records, list) or
                    not recovery_records or
                    any(not isinstance(record, dict) or
                        record.get("cycle_id") != cycle_ids[0]
                        for record in recovery_records)):
                raise RuntimeError("EXTERNAL_RECOVERY_REFERENCE_DRIFTED")
        elif name == "session_owner_set_reference":
            owners = artifact.get("owners")
            durable_owners = artifact.get("durable_owners")
            owner_count = authority["session_owner_count"]
            if (
                    artifact.get("lease_store_schema") != "HSL8" or
                    AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(str(
                        artifact.get("lease_store_file_sha256", ""))) is
                        None or
                    artifact.get("lease_store_file_sha256") ==
                        "sha256:" + "0" * 64 or
                    type(artifact.get("owner_count")) is not int or
                    artifact.get("owner_count") != owner_count or
                    not isinstance(owners, list) or
                    len(owners) != owner_count or
                    not isinstance(durable_owners, list) or
                    len(durable_owners) != owner_count or
                    any(not isinstance(owner, dict) or not owner
                        for owner in (*owners, *durable_owners)) or
                    owners != sorted(owners, key=_canonical_json_bytes) or
                    durable_owners != sorted(
                        durable_owners, key=_canonical_json_bytes) or
                    len({_canonical_json_bytes(owner) for owner in owners}) !=
                        owner_count or
                    len({_canonical_json_bytes(owner)
                         for owner in durable_owners}) != owner_count or
                    artifact.get("paper_only") is not True or
                    artifact.get("live_authorized") is not False):
                raise RuntimeError("EXTERNAL_RECOVERY_REFERENCE_DRIFTED")
    return authority, raw, metadata


def _external_recovery_snapshot_bundle(
        agent: ModuleType, policy: dict[str, object],
        main_state: dict[str, object], records: list[dict[str, object]],
        owners: list[dict[str, object]], lineage: dict[str, list[str]],
) -> tuple[dict[str, object], bytes, list[dict[str, object]]]:
    existing = _load_external_recovery_authority(policy)
    if existing is not None:
        authority, raw, _metadata = existing
        return authority, raw, owners
    durable_owners, lease_store_sha256 = (
        _external_validate_durable_owner_set(owners))
    runtime_profile, runtime_profile_raw = _external_p1_runtime_profile()
    record_hashes = sorted(str(record["body_sha256"]) for record in records)
    suspension_seed = _canonical_json_bytes({
        "campaign_id": policy["campaign_id"],
        "record_body_sha256s": record_hashes,
    })
    suspension_id = main_state.get("suspension_id")
    if not isinstance(suspension_id, str) or not suspension_id:
        suspension_id = "suspension-external-" + hashlib.sha256(
            suspension_seed).hexdigest()[:32]
    values = read_env()
    if values.get("HEPTA_LOCAL_AI_CAMPAIGN_ID") != policy["campaign_id"]:
        raise RuntimeError("EXTERNAL_RECOVERY_AGENT_CAMPAIGN_MISMATCH")
    main_state["suspension_id"] = suspension_id
    main_state["trading_suspended"] = True
    main_state["recovery_required"] = True
    main_state["recovery_complete"] = False
    main_state["recovery_phase"] = "EXTERNAL_P1_RECOVERY_REQUIRED"
    _ensure_suspension_metadata(main_state, values)
    reason_codes = sorted({
        str(reason) for record in records
        for reason in record["reason_codes"]})
    main_state["suspension_code"] = reason_codes[0]
    main_state["recovery_reason"] = (
        "RECOVERY_REQUIRED: sealed external P1 executor incident; "
        "fresh campaign and automatic resume forbidden")
    agent.write_json(AGENT_STATE, main_state)
    os.chown(AGENT_STATE, 0, 0)
    os.chmod(AGENT_STATE, 0o600)
    state_raw, _state_metadata = _stable_file_bytes(
        AGENT_STATE, "EXTERNAL_RECOVERY_INCIDENT_STATE_UNSAFE",
        expected_uid=0, expected_gid=0)

    # New entry is permanently fenced before the recovery authority can be
    # published or any broker-facing Tool call can occur.
    _end_flat_persist_policy_disabled(str(policy["campaign_id"]))
    disabled_policy, disabled_raw = _external_p1_recovery_policy()
    if (disabled_policy.get("enabled") is not False or
            disabled_policy.get("mutations_authorized") is not False):
        raise RuntimeError("EXTERNAL_RECOVERY_POLICY_NOT_TERMINAL")
    paths = _external_recovery_artifact_paths(suspension_id)
    suspended_at_ms = main_state.get("suspended_at_ms")
    if type(suspended_at_ms) is not int or suspended_at_ms < 1:
        raise RuntimeError("EXTERNAL_RECOVERY_INCIDENT_STATE_UNSAFE")
    # This timestamp is derived only from already-sealed incident state.  A
    # crash between the four create-once snapshots can therefore replay the
    # exact same bytes rather than colliding on wall-clock time.
    recorded_at_ms = max(
        suspended_at_ms,
        *(int(record["created_at_ms"]) for record in records))
    common = {
        "version": 1,
        "recorded_at_ms": recorded_at_ms,
        "domain": "alpha",
        "campaign_id": policy["campaign_id"],
        "suspension_id": suspension_id,
        "source_baseline_sha256": policy["source_baseline_sha256"],
    }
    artifacts: dict[str, tuple[dict[str, object], bytes, os.stat_result]] = {}
    policy_schema, policy_status = (
        EXTERNAL_RECOVERY_REFERENCE_SPECS["policy_preimage_reference"])
    artifacts["policy_preimage_reference"] = (
        _publish_immutable_sealed_json(
            paths["policy_preimage_reference"], {
                **common, "schema": policy_schema, "status": policy_status,
                "policy_terminal": True, "policy_enabled": False,
                "policy_mutations_authorized": False,
                "policy_file_sha256": "sha256:" + hashlib.sha256(
                    disabled_raw).hexdigest(),
                "policy_document": disabled_policy,
                "runtime_profile_path": str(EXTERNAL_P1_RUNTIME_PROFILE),
                "runtime_profile_file_sha256": "sha256:" + hashlib.sha256(
                    runtime_profile_raw).hexdigest(),
                "runtime_profile": runtime_profile,
                "watch_handoff_receipt_path":
                    policy["watch_handoff_receipt_path"],
                "watch_handoff_receipt_file_sha256":
                    policy["watch_handoff_receipt_file_sha256"],
                "watch_handoff_receipt_body_sha256":
                    policy["watch_handoff_receipt_body_sha256"],
                "paper_only": True, "live_authorized": False,
            }, "EXTERNAL_RECOVERY_POLICY_SNAPSHOT_INVALID"))
    incident_schema, incident_status = (
        EXTERNAL_RECOVERY_REFERENCE_SPECS["incident_state_reference"])
    artifacts["incident_state_reference"] = (
        _publish_immutable_sealed_json(
            paths["incident_state_reference"], {
                **common, "schema": incident_schema,
                "status": incident_status,
                "agent_state_file_sha256": "sha256:" + hashlib.sha256(
                    state_raw).hexdigest(),
                "agent_state": main_state,
                "recovery_record_file_sha256s": sorted(
                    str(record["_file_sha256"]) for record in records),
                "recovery_record_body_sha256s": record_hashes,
                "emergency_cleanup_receipts": [
                    record["_emergency_cleanup"] for record in records
                    if record.get("_emergency_cleanup") is not None],
                "reason_codes": reason_codes,
                "recovery_required": True, "trading_suspended": True,
                "automatic_resume": False, "fresh_enable_authorized": False,
                "paper_only": True, "live_authorized": False,
            }, "EXTERNAL_RECOVERY_INCIDENT_SNAPSHOT_INVALID"))
    mutation_schema, mutation_status = (
        EXTERNAL_RECOVERY_REFERENCE_SPECS["mutation_lineage_reference"])
    cycle_ids = sorted({str(record["cycle_id"]) for record in records})
    if (len(cycle_ids) != 1 or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", cycle_ids[0]) is None):
        raise RuntimeError("EXTERNAL_RECOVERY_CYCLE_LINEAGE_INVALID")
    artifacts["mutation_lineage_reference"] = (
        _publish_immutable_sealed_json(
            paths["mutation_lineage_reference"], {
                **common, "schema": mutation_schema,
                "status": mutation_status,
                "place_call_ids": lineage["place_call_ids"],
                "cancel_call_ids": lineage["cancel_call_ids"],
                "flatten_call_ids": lineage["flatten_call_ids"],
                "request_sha256s": lineage["request_sha256s"],
                "response_sha256s": lineage["response_sha256s"],
                "journal_sha256s": lineage["journal_sha256s"],
                "cycle_ids": cycle_ids,
                "executor_recovery_records": [{
                    "path": record["_path"],
                    "cycle_id": record["cycle_id"],
                    "file_sha256": record["_file_sha256"],
                    "body_sha256": record["body_sha256"],
                    "journal_path": record["journal_path"],
                    "journal_file_sha256": record["journal_file_sha256"],
                    "uncertain_tool_call_id":
                        record["uncertain_tool_call_id"],
                    "emergency_cleanup": record.get(
                        "_emergency_cleanup"),
                } for record in records],
                "paper_only": True, "live_authorized": False,
            }, "EXTERNAL_RECOVERY_MUTATION_SNAPSHOT_INVALID"))
    owner_schema, owner_status = (
        EXTERNAL_RECOVERY_REFERENCE_SPECS["session_owner_set_reference"])
    owner_documents = sorted([{
        key: owner[key] for key in EXTERNAL_CANARY_OWNER_FIELDS
    } for owner in owners], key=_canonical_json_bytes)
    artifacts["session_owner_set_reference"] = (
        _publish_immutable_sealed_json(
            paths["session_owner_set_reference"], {
                **common, "schema": owner_schema, "status": owner_status,
                "lease_store_schema": "HSL8",
                "lease_store_file_sha256": lease_store_sha256,
                "owner_count": len(owner_documents),
                "owners": owner_documents,
                "durable_owners": durable_owners,
                "paper_only": True, "live_authorized": False,
            }, "EXTERNAL_RECOVERY_OWNER_SNAPSHOT_INVALID"))
    references = {
        name: _external_recovery_reference(
            paths[name], document, raw, metadata)
        for name, (document, raw, metadata) in artifacts.items()}
    recovery_id = "external-recovery-" + hashlib.sha256(
        _canonical_json_bytes({
            "campaign_id": policy["campaign_id"],
            "suspension_id": suspension_id,
            "references": references,
        })).hexdigest()[:32]
    authority_body = {
        "schema": EXTERNAL_RECOVERY_AUTHORITY_SCHEMA,
        "version": 1,
        "status": EXTERNAL_RECOVERY_AUTHORITY_STATUS,
        "recovery_id": recovery_id,
        "recorded_at_ms": recorded_at_ms,
        "domain": "alpha",
        "campaign_id": policy["campaign_id"],
        "suspension_id": suspension_id,
        "reason_code": reason_codes[0],
        "source_baseline_sha256": policy["source_baseline_sha256"],
        "watch_handoff_receipt_path": policy[
            "watch_handoff_receipt_path"],
        "watch_handoff_receipt_file_sha256": policy[
            "watch_handoff_receipt_file_sha256"],
        "watch_handoff_receipt_body_sha256": policy[
            "watch_handoff_receipt_body_sha256"],
        "recovery_required": True,
        "reduce_only": True,
        "paper_only": True,
        "live_authorized": False,
        "entry_authorized": False,
        "order_submission_authorized": False,
        "session_provision_authorized": False,
        **references,
        "session_owner_count": len(owner_documents),
        "all_original_session_owners_bound": True,
    }
    authority, authority_raw, _authority_metadata = (
        _publish_immutable_sealed_json(
            EXTERNAL_RECOVERY_AUTHORITY, authority_body,
            "EXTERNAL_RECOVERY_AUTHORITY_INVALID"))
    return authority, authority_raw, owners


def _external_checkpoint_owner(owner: dict[str, object]) -> dict[str, object]:
    command_ids = owner.get("recovery_command_ids")
    cancel_command_id = owner.get("recovery_cancel_command_id")
    flatten_command_id = owner.get("recovery_flatten_command_id")
    owner_account = owner.get("owner_account")
    owner_execution_domain = owner.get("owner_execution_domain")
    agent_id = owner.get("agent_id")
    if (not isinstance(command_ids, list) or not command_ids or
            command_ids != sorted(set(command_ids)) or
            any(not isinstance(value, str) or re.fullmatch(
                r"[A-Za-z0-9._:-]{8,128}", value) is None
                for value in command_ids) or
            not isinstance(cancel_command_id, str) or
            cancel_command_id not in command_ids or
            not isinstance(flatten_command_id, str) or
            flatten_command_id not in command_ids or
            not isinstance(owner_account, str) or
            re.fullmatch(r"DU[0-9]{1,16}", owner_account) is None or
            owner_execution_domain != "PAPER:alpha" or
            agent_id != "hepta-agent-alpha"):
        raise RuntimeError("EXTERNAL_RECOVERY_COMMAND_ID_MISSING")
    return {
        **{name: owner[name] for name in EXTERNAL_CANARY_OWNER_FIELDS},
        "recovery_command_ids": list(command_ids),
        "recovery_cancel_command_id": cancel_command_id,
        "recovery_flatten_command_id": flatten_command_id,
        "owner_account": owner_account,
        "owner_execution_domain": owner_execution_domain,
        "agent_id": agent_id,
    }


def _external_finalization_owner_binding(
        owners: list[dict[str, object]],
) -> tuple[str, int, bytes, str, str]:
    """Return the exact supervisor owner-set commitment and common scope."""
    if not owners:
        raise RuntimeError("EXTERNAL_RECOVERY_FINALIZATION_BINDING_INVALID")
    rows: list[tuple[str, bytes]] = []
    accounts: set[str] = set()
    domains: set[str] = set()
    for owner in owners:
        if not isinstance(owner, dict):
            raise RuntimeError(
                "EXTERNAL_RECOVERY_FINALIZATION_BINDING_INVALID")
        token_sha256 = owner.get("token_sha256")
        generation = owner.get("lease_generation")
        account = owner.get("owner_account")
        domain = owner.get("owner_execution_domain")
        if (
                not isinstance(token_sha256, str) or
                AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                    token_sha256) is None or
                type(generation) is not int or generation < 1 or
                not isinstance(account, str) or
                re.fullmatch(r"DU[0-9]{1,16}", account) is None or
                domain != "PAPER:alpha"):
            raise RuntimeError(
                "EXTERNAL_RECOVERY_FINALIZATION_BINDING_INVALID")
        accounts.add(account)
        domains.add(domain)
        rows.append((token_sha256, (
            f"{token_sha256}\t{generation}\t"
            f"{account.encode('utf-8').hex()}\t"
            f"{domain.encode('utf-8').hex()}\n").encode("ascii")))
    rows.sort(key=lambda item: item[0])
    if (len({token for token, _raw in rows}) != len(rows) or
            len(accounts) != 1 or len(domains) != 1):
        raise RuntimeError("EXTERNAL_RECOVERY_FINALIZATION_BINDING_INVALID")
    canonical = b"".join(raw for _token, raw in rows)
    return (
        "sha256:" + hashlib.sha256(canonical).hexdigest(), len(rows),
        canonical, next(iter(accounts)), next(iter(domains)))


def _external_finalization_id(
        recovery_id: str, owner_set_sha256: str, owner_count: int,
) -> str:
    if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}",
                         recovery_id) is None or
            AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                owner_set_sha256) is None or owner_count < 1):
        raise RuntimeError("EXTERNAL_RECOVERY_FINALIZATION_BINDING_INVALID")
    seed = (
        recovery_id + "\n" + owner_set_sha256 + "\n" +
        str(owner_count) + "\n").encode("ascii")
    return "paper-finalization-" + hashlib.sha256(seed).hexdigest()[:32]


def _external_parse_ordered_receipt(
        receipt: object, keys: tuple[str, ...], failure: str, *,
        maximum: int = 4096,
) -> tuple[dict[str, str], bytes]:
    if not isinstance(receipt, str):
        raise RuntimeError(failure)
    try:
        raw = receipt.encode("ascii")
    except UnicodeEncodeError as error:
        raise RuntimeError(failure) from error
    if (not 1 <= len(raw) <= maximum or not raw.endswith(b"\n") or
            b"\r" in raw or b"\x00" in raw):
        raise RuntimeError(failure)
    rows = raw[:-1].split(b"\n")
    if len(rows) != len(keys):
        raise RuntimeError(failure)
    values: dict[str, str] = {}
    for row, expected_key in zip(rows, keys, strict=True):
        key, separator, value = row.partition(b"=")
        try:
            decoded_key = key.decode("ascii")
            decoded_value = value.decode("ascii")
        except UnicodeDecodeError as error:
            raise RuntimeError(failure) from error
        if separator != b"=" or decoded_key != expected_key:
            raise RuntimeError(failure)
        values[expected_key] = decoded_value
    if tuple(values) != keys:
        raise RuntimeError(failure)
    return values, raw


def _external_parse_finalization_receipt(
        receipt: object,
) -> tuple[dict[str, str], bytes]:
    return _external_parse_ordered_receipt(
        receipt, EXTERNAL_PRELIMINARY_FINALIZATION_RECEIPT_KEYS,
        "EXTERNAL_RECOVERY_FINALIZATION_RECEIPT_INVALID")


def _external_parse_terminal_ack_receipt(
        receipt: object,
) -> tuple[dict[str, str], bytes]:
    return _external_parse_ordered_receipt(
        receipt, EXTERNAL_TERMINAL_ACK_RECEIPT_KEYS,
        "EXTERNAL_RECOVERY_TERMINAL_ACK_RECEIPT_INVALID", maximum=12288)


def _external_parse_terminal_evidence(
        evidence: object,
) -> tuple[dict[str, str], bytes, bytes]:
    """Parse HPE1 and return (fields, exact bytes, body prefix).

    HPE1 is a line-oriented stable witness, not JSON.  The final
    ``evidence_body_sha256`` line covers the complete prefix (including the
    ``HPE1`` header), so a receipt cannot be made self-consistent by merely
    changing its own hash.  Keep this parser deliberately independent of the
    privileged producer module.
    """
    failure = "EXTERNAL_RECOVERY_TERMINAL_EVIDENCE_INVALID"
    if not isinstance(evidence, (bytes, bytearray, memoryview)):
        raise RuntimeError(failure)
    raw = bytes(evidence)
    if not 1 <= len(raw) <= 12288:
        raise RuntimeError(failure)
    try:
        raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimeError(failure) from error
    rows = raw.splitlines(keepends=True)
    if (len(rows) != len(EXTERNAL_TERMINAL_EVIDENCE_KEYS) + 1 or
            rows[0] != b"HPE1\n" or
            any(not row.endswith(b"\n") or b"\r" in row
                for row in rows)):
        raise RuntimeError(failure)
    values: dict[str, str] = {}
    for row, expected_key in zip(
            rows[1:], EXTERNAL_TERMINAL_EVIDENCE_KEYS, strict=True):
        prefix = (expected_key + "=").encode("ascii")
        if (not row.startswith(prefix) or row == prefix + b"\n" or
                b"=" in row[len(prefix):-1] or expected_key in values):
            raise RuntimeError(failure)
        try:
            value = row[len(prefix):-1].decode("ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise RuntimeError(failure) from error
        if not value:
            raise RuntimeError(failure)
        values[expected_key] = value
    if tuple(values) != EXTERNAL_TERMINAL_EVIDENCE_KEYS:
        raise RuntimeError(failure)
    prefix = b"".join(rows[:-1])
    body_sha256 = values.get("evidence_body_sha256", "")
    if (AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(body_sha256) is None
            or body_sha256 != "sha256:" + hashlib.sha256(
                prefix).hexdigest()):
        raise RuntimeError(failure)
    return values, raw, prefix


def _external_validate_terminal_evidence_binding(
        evidence_raw: bytes, *, receipt: dict[str, str],
        result: dict[str, object], checkpoint: dict[str, object],
) -> None:
    """Cross-bind HSL8 to the independent HPE1 stable provenance.

    HSL8 intentionally repeats the provider, egress, and known-command-set
    provenance.  Those repeats are useful only when checked against the
    independently committed HPE1 bytes.  In particular, do not replace this
    with a receipt-hash check: an attacker who can rewrite all receipt fields
    can also recompute that hash.
    """
    failure = "EXTERNAL_RECOVERY_TERMINAL_ACK_RESPONSE_INVALID"
    try:
        evidence, exact, _prefix = _external_parse_terminal_evidence(
            evidence_raw)
    except (RuntimeError, TypeError, ValueError) as error:
        raise RuntimeError(failure) from error
    file_sha256 = "sha256:" + hashlib.sha256(exact).hexdigest()
    body_sha256 = evidence["evidence_body_sha256"]
    if (
            result.get("terminal_evidence_sha256") != file_sha256 or
            receipt.get("terminal_evidence_file_sha256") != file_sha256 or
            result.get("terminal_evidence_body_sha256") != body_sha256 or
            receipt.get("terminal_evidence_body_sha256") != body_sha256):
        raise RuntimeError(failure)

    # These identity fields are already checked for HSL8 below, but checking
    # them against HPE1 makes the independent witness part of the binding
    # rather than merely an untrusted hash-bearing attachment.
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
    if any(evidence.get(field) != expected
           for field, expected in identity.items()):
        raise RuntimeError(failure)

    # Every repeated receipt field is compared byte-for-byte as text.  The
    # receipt and HPE1 use different schema/version/status labels, and HPE1
    # names its final body digest ``evidence_body_sha256``; handle those
    # intentional wire differences explicitly.
    for field in EXTERNAL_TERMINAL_ACK_RECEIPT_KEYS:
        if field in {"schema", "version", "status",
                     "terminal_evidence_file_sha256",
                     "terminal_evidence_body_sha256"}:
            continue
        if field not in EXTERNAL_TERMINAL_EVIDENCE_KEYS:
            continue
        if receipt.get(field) != evidence.get(field):
            raise RuntimeError(failure)
    if receipt.get("terminal_evidence_body_sha256") != body_sha256:
        raise RuntimeError(failure)

    # Retain the producer's essential fail-closed boundary independently of
    # the receipt.  The more detailed HSL8 validator still checks all result
    # fields; these checks ensure a jointly rewritten HPE1/receipt cannot turn
    # this validator into an authorization surface.
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
        raise RuntimeError(failure)


def _external_current_terminal_evidence() -> bytes:
    """Read and syntactically validate the current root-owned HPE1 file."""
    try:
        raw, _metadata = _stable_file_bytes(
            EXTERNAL_TERMINAL_EVIDENCE_PATH,
            "EXTERNAL_RECOVERY_TERMINAL_EVIDENCE_INVALID",
            expected_uid=0, expected_gid=0,
            allowed_modes=frozenset({0o400}), maximum_bytes=12288)
    except FileNotFoundError as error:
        raise RuntimeError(
            "EXTERNAL_RECOVERY_TERMINAL_WITNESS_REQUIRED") from error
    _external_parse_terminal_evidence(raw)
    return raw


def _external_validate_finalization_result_types(
        result: object,
) -> dict[str, object]:
    failure = "EXTERNAL_RECOVERY_FINALIZATION_RESPONSE_INVALID"
    if not isinstance(result, dict) or set(result) != \
            EXTERNAL_PRELIMINARY_FINALIZATION_STDOUT_FIELDS:
        raise RuntimeError(failure)
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
        "broker_exposure_generation",
        "broker_terminal_exposure_generation",
        "broker_risk_absorbed_exposure_generation",
        "broker_global_active_order_count",
    }
    for field in boolean_fields:
        if type(result.get(field)) is not bool:
            raise RuntimeError(failure)
    for field in integer_fields:
        if type(result.get(field)) is not int:
            raise RuntimeError(failure)
    for field in set(result) - boolean_fields - integer_fields:
        if not isinstance(result.get(field), str):
            raise RuntimeError(failure)
    return result


def _external_validate_finalization_result(
        result: object, owner: dict[str, object],
        checkpoint: dict[str, object], *, expected_state: str,
) -> dict[str, object]:
    value = _external_validate_finalization_result_types(result)
    failure = "EXTERNAL_RECOVERY_FINALIZATION_RESPONSE_INVALID"
    owners = checkpoint.get("owners")
    if not isinstance(owners, list):
        raise RuntimeError(failure)
    (owner_set_sha256, owner_count, canonical_owner_set,
     common_account, common_domain) = _external_finalization_owner_binding(
         owners)
    if (
            value.get("paper_finalization_state") != expected_state or
            value.get("paper_finalization_required") is not True or
            value.get("recovery_id") != checkpoint.get("recovery_id") or
            value.get("finalization_id") !=
                checkpoint.get("finalization_id") or
            value.get("expected_owner_set_sha256") != owner_set_sha256 or
            value.get("expected_owner_count") != owner_count or
            value.get("owner_token_sha256") != owner.get("token_sha256") or
            value.get("lease_generation") != owner.get("lease_generation")):
        raise RuntimeError(failure)
    if expected_state == "FENCE_COMPLETE":
        if (
                value.get("accepted") is not False or
                value.get("reason_code") !=
                    "PAPER_FINALIZATION_GROUP_PENDING" or
                value.get("finalization_receipt_sha256") != "" or
                value.get("finalization_receipt") != "" or
                any(value.get(field) is not False for field in (
                    "owner_audit_authoritative", "owner_audit_complete",
                    "broker_post_fill_risk_reconciliation_pending",
                    "broker_recovery_audit_barrier_complete",
                    "broker_recovery_audit_new_connection_epoch_required")) or
                any(value.get(field) != 0 for field in (
                    "owner_active_order_count",
                    "owner_uncertain_command_count",
                    "execution_service_fencing_generation",
                    "broker_connection_epoch", "broker_active_generation",
                    "broker_terminal_generation", "broker_risk_generation",
                    "broker_account_generation", "broker_position_generation",
                    "broker_fx_cash_generation", "broker_exposure_generation",
                    "broker_terminal_exposure_generation",
                    "broker_risk_absorbed_exposure_generation",
                    "broker_global_active_order_count")) or
                any(value.get(field) != "" for field in (
                    "owner_account", "owner_execution_domain",
                    "execution_service_epoch", "broker_position_quantity",
                    "broker_gross_absolute_position"))):
            raise RuntimeError(failure)
        return value
    if expected_state != "AUDIT_SEALED":
        raise RuntimeError(failure)
    receipt_sha256 = value.get("finalization_receipt_sha256")
    if (
            value.get("accepted") is not True or
            value.get("reason_code") !=
                "PAPER_FINALIZATION_AUDIT_SEALED" or
            not isinstance(receipt_sha256, str) or
            AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                receipt_sha256) is None):
        raise RuntimeError(failure)
    receipt, receipt_raw = _external_parse_finalization_receipt(
        value.get("finalization_receipt"))
    if (
            "sha256:" + hashlib.sha256(receipt_raw).hexdigest() !=
                receipt_sha256 or
            receipt.get("schema") !=
                "hepta.paper-session-finalization-receipt.v1" or
            receipt.get("version") != "1" or
            receipt.get("status") != "AUDIT_SEALED" or
            receipt.get("recovery_id") != checkpoint.get("recovery_id") or
            receipt.get("finalization_id") !=
                checkpoint.get("finalization_id") or
            receipt.get("expected_owner_set_sha256") != owner_set_sha256 or
            receipt.get("expected_owner_count") != str(owner_count) or
            receipt.get("owner_set_canonical_hex") !=
                canonical_owner_set.hex() or
            receipt.get("owner_account") != common_account or
            receipt.get("owner_execution_domain") != common_domain or
            receipt.get("paper_only") != "1" or
            receipt.get("live_authorized") != "0"):
        raise RuntimeError(failure)
    integer_pairs = {
        "execution_service_fencing_generation",
        "broker_connection_epoch", "broker_active_generation",
        "broker_terminal_generation", "broker_risk_generation",
        "broker_account_generation", "broker_position_generation",
        "broker_fx_cash_generation", "broker_exposure_generation",
        "broker_terminal_exposure_generation",
        "broker_risk_absorbed_exposure_generation",
        "broker_global_active_order_count", "owner_active_order_count",
        "owner_uncertain_command_count",
    }
    for field in integer_pairs:
        if receipt.get(field) != str(value[field]):
            raise RuntimeError(failure)
    string_pairs = {
        "owner_account", "owner_execution_domain", "execution_service_epoch",
        "broker_position_quantity", "broker_gross_absolute_position",
    }
    for field in string_pairs:
        if receipt.get(field) != value[field]:
            raise RuntimeError(failure)
    bool_pairs = {
        "broker_post_fill_risk_reconciliation_pending": "0",
        "broker_recovery_audit_barrier_complete": "1",
        "broker_recovery_audit_new_connection_epoch_required": "0",
    }
    for field, expected in bool_pairs.items():
        if receipt.get(field) != expected:
            raise RuntimeError(failure)
    positive_generations = (
        "execution_service_fencing_generation", "broker_connection_epoch",
        "broker_active_generation", "broker_terminal_generation",
        "broker_risk_generation", "broker_account_generation",
        "broker_position_generation", "broker_fx_cash_generation",
    )
    if (
            not isinstance(value.get("execution_service_epoch"), str) or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", str(
                value["execution_service_epoch"])) is None or
            any(int(value[field]) < 1 for field in positive_generations) or
            int(value["broker_exposure_generation"]) < 0 or
            int(value["broker_risk_absorbed_exposure_generation"]) < 0 or
            int(value["broker_terminal_exposure_generation"]) < 0 or
            int(value["broker_terminal_exposure_generation"]) >
                int(value["broker_risk_absorbed_exposure_generation"]) or
            value["broker_risk_absorbed_exposure_generation"] !=
                value["broker_exposure_generation"] or
            any(value[field] != 0 for field in (
                "broker_global_active_order_count", "owner_active_order_count",
                "owner_uncertain_command_count")) or
            value.get("owner_audit_authoritative") is not True or
            value.get("owner_audit_complete") is not True or
            value.get("broker_post_fill_risk_reconciliation_pending") is not
                False or
            value.get("broker_recovery_audit_barrier_complete") is not True or
            value.get("broker_recovery_audit_new_connection_epoch_required")
                is not False or
            value.get("broker_position_quantity") != "0" or
            value.get("broker_gross_absolute_position") != "0" or
            value.get("owner_account") != common_account or
            value.get("owner_execution_domain") != common_domain):
        raise RuntimeError(failure)
    return value


def _external_validate_terminal_ack_result(
        result: object, checkpoint: dict[str, object], *,
        expected_replay: bool | None = None,
        terminal_evidence_raw: bytes | None = None,
) -> dict[str, object]:
    failure = "EXTERNAL_RECOVERY_TERMINAL_ACK_RESPONSE_INVALID"
    if not isinstance(result, dict) or set(result) != \
            EXTERNAL_TERMINAL_ACK_STDOUT_FIELDS:
        raise RuntimeError(failure)
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
        "broker_exposure_generation",
        "broker_terminal_exposure_generation",
        "broker_risk_absorbed_exposure_generation",
        "broker_global_active_order_count",
        "terminalization_service_fencing_generation",
        "terminalization_generation", "broker_callbacks_in_flight",
        "egress_publisher_pid", "egress_publisher_start_ticks",
    }
    if (any(type(result.get(field)) is not bool for field in boolean_fields) or
            any(type(result.get(field)) is not int
                for field in integer_fields) or
            any(not isinstance(result.get(field), str)
                for field in set(result) - boolean_fields - integer_fields)):
        raise RuntimeError(failure)
    owners = checkpoint.get("owners")
    preliminary = checkpoint.get("preliminary_finalization_result")
    if (not isinstance(owners, list) or not owners or
            not isinstance(preliminary, dict)):
        raise RuntimeError(failure)
    owner = min(owners, key=lambda item: str(item["token_sha256"]))
    (owner_set_sha256, owner_count, canonical_owner_set,
     common_account, common_domain) = _external_finalization_owner_binding(
         owners)
    preliminary_sha256 = preliminary.get("finalization_receipt_sha256")
    terminal_sha256 = result.get("finalization_receipt_sha256")
    if (
            result.get("accepted") is not True or
            result.get("reason_code") !=
                "PAPER_FINALIZATION_TERMINAL_ACKED" or
            result.get("paper_finalization_state") != "ACKED" or
            result.get("paper_finalization_required") is not True or
            result.get("recovery_id") != checkpoint.get("recovery_id") or
            result.get("finalization_id") != checkpoint.get(
                "finalization_id") or
            result.get("expected_owner_set_sha256") != owner_set_sha256 or
            result.get("expected_owner_count") != owner_count or
            result.get("owner_token_sha256") != owner.get("token_sha256") or
            result.get("lease_generation") != owner.get("lease_generation") or
            result.get("preliminary_finalization_receipt_sha256") !=
                preliminary_sha256 or
            not isinstance(preliminary_sha256, str) or
            AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                preliminary_sha256) is None or
            not isinstance(terminal_sha256, str) or
            AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                terminal_sha256) is None or
            terminal_sha256 == preliminary_sha256 or
            (expected_replay is not None and
             result.get("terminal_replay") is not expected_replay)):
        raise RuntimeError(failure)
    try:
        receipt, receipt_raw, receipt_canonical = (
            _external_validate_hsl8_terminal_receipt(
                str(result.get("finalization_receipt")),
                preliminary={
                    "execution_service_epoch": str(preliminary.get(
                        "execution_service_epoch", "")),
                    "execution_service_fencing_generation": str(
                        preliminary.get(
                            "execution_service_fencing_generation", "")),
                },
                preliminary_receipt_sha256=str(preliminary_sha256),
                recovery_id=str(checkpoint.get("recovery_id")),
                finalization_id=str(checkpoint.get("finalization_id")),
                owner_set_sha256=owner_set_sha256,
                owner_count=owner_count, owner_account=common_account,
                owner_domain=common_domain,
                member_token_sha256=str(owner.get("token_sha256")),
                member_generation=int(owner.get("lease_generation", 0)),
                member_agent_id=str(owner.get("agent_id", "")),
                member_session_id=str(owner.get("session_id", ""))))
    except (RuntimeError, TypeError, ValueError) as error:
        raise RuntimeError(failure) from error
    if ("sha256:" + hashlib.sha256(receipt_raw).hexdigest() !=
            terminal_sha256 or receipt_canonical != canonical_owner_set or
            receipt.get("campaign_id") != checkpoint.get("campaign_id") or
            receipt.get("cycle_id") != checkpoint.get("cycle_id")):
        raise RuntimeError(failure)
    if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", str(
                result.get("execution_service_epoch"))) is None or
            result.get("execution_service_epoch") !=
                preliminary.get("execution_service_epoch") or
            result.get("execution_service_fencing_generation") !=
                preliminary.get("execution_service_fencing_generation") or
            result.get("terminalization_service_epoch") !=
                result.get("execution_service_epoch") or
            result.get("terminalization_service_fencing_generation") !=
                result.get("execution_service_fencing_generation") or
            result.get("execution_service_fencing_generation", 0) < 1 or
            result.get("terminalization_generation") != 1 or
            result.get("terminal_latch_sha256") !=
                receipt.get("terminalizing_latch_sha256") or
            result.get("terminal_external_halt_latch_sha256") !=
                receipt.get("terminal_external_halt_latch_sha256") or
            result.get("terminal_proof_kind") !=
                "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" or
            result.get("owner_account") != common_account or
            result.get("owner_execution_domain") != common_domain or
            result.get("owner_audit_authoritative") is not True or
            result.get("owner_audit_complete") is not True or
            result.get("execution_mutation_gate_closed") is not True or
            result.get("broker_transport_connected") is not False or
            result.get("broker_event_ingress_halted") is not True or
            result.get("broker_callback_queue_drained") is not False or
            result.get("broker_callbacks_in_flight") != 0 or
            result.get("broker_reconnect_permitted") is not False or
            result.get("terminal_latch_durable") is not True or
            result.get("terminal_runtime_latch_loaded") is not False or
            result.get("terminal_runtime_verified") is not False or
            result.get("terminal_external_latch_loaded") is not True or
            result.get("terminal_current_evidence_verified") is not True or
            result.get("broker_post_fill_risk_reconciliation_pending") is not
                False or
            result.get("broker_recovery_audit_barrier_complete") is not False or
            result.get("broker_recovery_audit_new_connection_epoch_required")
                is not False or
            result.get("broker_position_quantity") != "0" or
            result.get("broker_gross_absolute_position") != "0" or
            any(result[field] != 0 for field in (
                "broker_connection_epoch", "broker_active_generation",
                "broker_terminal_generation", "broker_risk_generation",
                "broker_account_generation", "broker_position_generation",
                "broker_fx_cash_generation", "broker_exposure_generation",
                "broker_terminal_exposure_generation",
                "broker_risk_absorbed_exposure_generation",
                "broker_global_active_order_count", "owner_active_order_count",
                "owner_uncertain_command_count")) or
            result.get("egress_publisher_pid", 0) < 1 or
            result.get("egress_publisher_start_ticks", 0) < 1):
        raise RuntimeError(failure)
    result_receipt_pairs = {
        "transport_cutoff_receipt_file_sha256":
            "transport_cutoff_receipt_file_sha256",
        "transport_cutoff_receipt_body_sha256":
            "transport_cutoff_receipt_body_sha256",
        "post_cutoff_terminal_witness_file_sha256":
            "post_cutoff_terminal_witness_file_sha256",
        "post_cutoff_terminal_witness_body_sha256":
            "post_cutoff_terminal_witness_body_sha256",
        "terminal_evidence_sha256": "terminal_evidence_file_sha256",
        "terminal_evidence_body_sha256": "terminal_evidence_body_sha256",
        "egress_policy_sha256": "egress_policy_sha256",
        "provider_trust_policy_body_sha256":
            "provider_trust_policy_body_sha256",
        "signed_account_signature_sha256":
            "signed_account_signature_sha256",
    }
    if (any(result.get(result_field) != receipt.get(receipt_field)
            for result_field, receipt_field in result_receipt_pairs.items()) or
            str(result["egress_publisher_pid"]) !=
                receipt.get("egress_publisher_pid") or
            str(result["egress_publisher_start_ticks"]) !=
                receipt.get("egress_publisher_start_ticks")):
        raise RuntimeError(failure)
    if terminal_evidence_raw is not None:
        _external_validate_terminal_evidence_binding(
            terminal_evidence_raw, receipt=receipt, result=result,
            checkpoint=checkpoint)
    return result


def _external_recovery_checkpoint_path(
        authority: dict[str, object]) -> Path:
    return _external_recovery_artifact_paths(
        str(authority["suspension_id"]))["checkpoint"]


def _persist_external_recovery_checkpoint(
        checkpoint: dict[str, object]) -> None:
    body = dict(checkpoint)
    body.pop("body_sha256", None)
    now_ms = time.time_ns() // 1_000_000
    body["updated_at_ms"] = max(
        int(body.get("created_at_ms", now_ms)), now_ms)
    document = _sealed_json_document(body)
    path = _external_recovery_artifact_paths(
        str(document["suspension_id"]))["checkpoint"]
    _write_root_json(path, document)
    raw, _metadata = _stable_file_bytes(
        path, "EXTERNAL_RECOVERY_CHECKPOINT_INVALID",
        expected_uid=0, expected_gid=0)
    observed = _validate_canonical_sealed_document(
        raw, "EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    if observed != document:
        raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_DRIFTED")
    checkpoint.clear()
    checkpoint.update(document)


def _external_recovery_checkpoint(
        authority: dict[str, object], authority_raw: bytes,
        owners: list[dict[str, object]],
) -> dict[str, object]:
    authority_file_sha256 = "sha256:" + hashlib.sha256(
        authority_raw).hexdigest()
    # Inspect an already-published checkpoint generation before projecting
    # owner command lineage.  Legacy v1/v2 checkpoints intentionally lack
    # those fields; attempting to bind them first would produce a misleading
    # COMMAND_ID_MISSING error instead of rejecting the artifact as invalid
    # evidence.
    path = _external_recovery_checkpoint_path(authority)
    try:
        existing_raw, _existing_metadata = _stable_file_bytes(
            path, "EXTERNAL_RECOVERY_CHECKPOINT_INVALID",
            expected_uid=0, expected_gid=0)
    except FileNotFoundError:
        existing_raw = None
    else:
        existing_checkpoint = _validate_canonical_sealed_document(
            existing_raw, "EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
        if (existing_checkpoint.get("schema") !=
                EXTERNAL_RECOVERY_CHECKPOINT_SCHEMA or
                existing_checkpoint.get("version") != 4):
            raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    bound_owners = sorted(
        [_external_checkpoint_owner(owner) for owner in owners],
        key=_canonical_json_bytes)
    (owner_set_sha256, owner_count, _owner_set_canonical,
     _owner_account, _owner_domain) = _external_finalization_owner_binding(
         bound_owners)
    finalization_id = _external_finalization_id(
        str(authority["recovery_id"]), owner_set_sha256, owner_count)
    cycle_id = _external_recovery_cycle_id(authority)
    path = _external_recovery_checkpoint_path(authority)
    try:
        raw, _metadata = _stable_file_bytes(
            path, "EXTERNAL_RECOVERY_CHECKPOINT_INVALID",
            expected_uid=0, expected_gid=0)
    except FileNotFoundError:
        now_ms = time.time_ns() // 1_000_000
        checkpoint: dict[str, object] = {
            "schema": EXTERNAL_RECOVERY_CHECKPOINT_SCHEMA,
            "version": 4,
            "recovery_id": authority["recovery_id"],
            "finalization_id": finalization_id,
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": owner_count,
            "campaign_id": authority["campaign_id"],
            "cycle_id": cycle_id,
            "suspension_id": authority["suspension_id"],
            "source_baseline_sha256": authority["source_baseline_sha256"],
            "recovery_authority_file_sha256": authority_file_sha256,
            "recovery_authority_body_sha256": authority["body_sha256"],
            "phase": "AUTHORITY_SEALED",
            "created_at_ms": now_ms,
            "updated_at_ms": now_ms,
            "owners": bound_owners,
            "owner_queries": [],
            "pending_mutation": None,
            "cancelled_order_ids": [],
            "terminal_order_ids": [],
            "flatten_order_ids": [],
            "zero_exposure_proofs": [],
            "preliminary_owner_token_sha256s": [],
            "preliminary_finalization_result": None,
            "terminal_ack_result": None,
            "paper_only": True,
            "live_authorized": False,
        }
        _persist_external_recovery_checkpoint(checkpoint)
        return checkpoint
    checkpoint = _validate_canonical_sealed_document(
        raw, "EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    # Reject legacy checkpoint generations before interpreting any owner or
    # mutation fields.  Older v1/v2 documents intentionally do not carry the
    # v4 command-id and witness bindings; validating those fields first would
    # leak a misleading secondary error (for example COMMAND_ID_MISSING)
    # instead of treating the legacy artifact as non-evidence.
    if (checkpoint.get("schema") != EXTERNAL_RECOVERY_CHECKPOINT_SCHEMA or
            checkpoint.get("version") != 4):
        raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    expected_fields = {
        "schema", "version", "recovery_id", "finalization_id",
        "expected_owner_set_sha256", "expected_owner_count", "campaign_id",
        "cycle_id",
        "suspension_id", "source_baseline_sha256",
        "recovery_authority_file_sha256",
        "recovery_authority_body_sha256", "phase", "created_at_ms",
        "updated_at_ms", "owners", "owner_queries", "pending_mutation",
        "cancelled_order_ids", "terminal_order_ids", "flatten_order_ids",
        "zero_exposure_proofs", "preliminary_owner_token_sha256s",
        "preliminary_finalization_result", "terminal_ack_result", "paper_only",
        "live_authorized", "body_sha256",
    }
    phases = {
        "AUTHORITY_SEALED", "OWNERS_RECOVERY_ONLY", "RISK_ZERO_SEALED",
        "PRELIMINARY_SEALED", "TERMINAL_WITNESS_REQUIRED",
        "TERMINAL_ACKED", "COMPLETE",
    }
    if (set(checkpoint) != expected_fields or
            checkpoint.get("schema") != EXTERNAL_RECOVERY_CHECKPOINT_SCHEMA or
            checkpoint.get("version") != 4 or
            checkpoint.get("recovery_id") != authority.get("recovery_id") or
            checkpoint.get("finalization_id") != finalization_id or
            checkpoint.get("expected_owner_set_sha256") !=
                owner_set_sha256 or
            checkpoint.get("expected_owner_count") != owner_count or
            checkpoint.get("campaign_id") != authority.get("campaign_id") or
            checkpoint.get("cycle_id") != cycle_id or
            checkpoint.get("suspension_id") !=
                authority.get("suspension_id") or
            checkpoint.get("source_baseline_sha256") !=
                authority.get("source_baseline_sha256") or
            checkpoint.get("recovery_authority_file_sha256") !=
                authority_file_sha256 or
            checkpoint.get("recovery_authority_body_sha256") !=
                authority.get("body_sha256") or
            checkpoint.get("phase") not in phases or
            checkpoint.get("owners") != bound_owners or
            type(checkpoint.get("created_at_ms")) is not int or
            type(checkpoint.get("updated_at_ms")) is not int or
            checkpoint.get("paper_only") is not True or
            checkpoint.get("live_authorized") is not False or
            any(not isinstance(checkpoint.get(name), list) for name in (
                "owner_queries", "cancelled_order_ids",
                "terminal_order_ids", "flatten_order_ids",
                "zero_exposure_proofs",
                "preliminary_owner_token_sha256s"))):
        raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    for name in (
            "cancelled_order_ids", "terminal_order_ids",
            "flatten_order_ids"):
        values = checkpoint[name]
        assert isinstance(values, list)
        if (values != sorted(set(values)) or any(
                type(value) is not int or value < 0 for value in values)):
            raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    preliminary_owners = checkpoint["preliminary_owner_token_sha256s"]
    assert isinstance(preliminary_owners, list)
    owner_hashes = {str(owner["token_sha256"]) for owner in bound_owners}
    if (preliminary_owners != sorted(set(preliminary_owners)) or
            not set(preliminary_owners).issubset(owner_hashes)):
        raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    phase = checkpoint["phase"]
    preliminary_result = checkpoint.get("preliminary_finalization_result")
    terminal_ack = checkpoint.get("terminal_ack_result")
    if preliminary_result is not None:
        if not isinstance(preliminary_result, dict):
            raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
        result_owner = next((
            owner for owner in bound_owners
            if owner["token_sha256"] ==
                preliminary_result.get("owner_token_sha256")), None)
        if result_owner is None:
            raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
        _external_validate_finalization_result(
            preliminary_result, result_owner, checkpoint,
            expected_state="AUDIT_SEALED")
    if terminal_ack is not None:
        if not isinstance(terminal_ack, dict):
            raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
        try:
            evidence_raw = _external_current_terminal_evidence()
            _external_validate_terminal_ack_result(
                terminal_ack, checkpoint, terminal_evidence_raw=evidence_raw)
        except RuntimeError as error:
            if str(error) == "EXTERNAL_RECOVERY_TERMINAL_WITNESS_REQUIRED":
                raise
            raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID") from error
    if (
            phase in {"AUTHORITY_SEALED", "OWNERS_RECOVERY_ONLY"} and
            (preliminary_owners or preliminary_result is not None or
             terminal_ack is not None) or
            phase == "RISK_ZERO_SEALED" and
            (preliminary_result is not None or terminal_ack is not None) or
            phase in {"PRELIMINARY_SEALED", "TERMINAL_WITNESS_REQUIRED"} and
            (set(preliminary_owners) != owner_hashes or
             preliminary_result is None or terminal_ack is not None) or
            phase in {"TERMINAL_ACKED", "COMPLETE"} and
            (set(preliminary_owners) != owner_hashes or
             preliminary_result is None or terminal_ack is None)):
        raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    return checkpoint


def _external_existing_checkpoint_owners(
        authority: dict[str, object],
) -> list[dict[str, object]] | None:
    path = _external_recovery_checkpoint_path(authority)
    try:
        raw, _metadata = _stable_file_bytes(
            path, "EXTERNAL_RECOVERY_CHECKPOINT_INVALID",
            expected_uid=0, expected_gid=0)
    except FileNotFoundError:
        return None
    value = _validate_canonical_sealed_document(
        raw, "EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    owners = value.get("owners")
    if not isinstance(owners, list) or not owners:
        raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    if any(not isinstance(owner, dict) for owner in owners):
        raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    return [dict(owner) for owner in owners]


def _external_recovery_query_owner(
        owner: dict[str, object], command_id: str,
) -> dict[str, object]:
    exact_owner = {
        name: owner[name] for name in EXTERNAL_CANARY_OWNER_FIELDS}
    _validated, _authority_raw, _bearer_raw = (
        _external_recovery_owner_material(exact_owner))
    if re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", command_id) is None:
        raise RuntimeError("EXTERNAL_RECOVERY_COMMAND_ID_INVALID")
    completed = run([
        "/usr/bin/hepta-sessionctl", "--socket", SUPERVISOR_SOCKET,
        "recovery-query", "--token-file",
        str(owner["revoke_bearer_path"]),
        "--generation", str(owner["lease_generation"]),
        "--command-id", command_id, "--token-owner-uid", "0",
        "--require-paper-finalization",
    ], timeout=15)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "EXTERNAL_RECOVERY_COMMAND_STATUS_UNAVAILABLE") from error
    status = result.get("command_status") \
        if isinstance(result, dict) else None
    reason = result.get("reason_code") if isinstance(result, dict) else None
    command_reason = result.get("command_reason_code") \
        if isinstance(result, dict) else None
    order_id = result.get("order_id") if isinstance(result, dict) else None
    valid_reasons = {
        "RECOVERY_QUERY_CANNOT_FULL_FENCE",
        "RECOVERY_QUERY_PROVEN_RECOVERY_ONLY",
        "RECOVERY_QUERY_NOT_FOUND_PROVEN_RECOVERY_ONLY",
    }
    integer_fields = (
        "execution_service_fencing_generation", "recovery_expires_at_ms",
        "owner_active_order_count", "owner_uncertain_command_count",
        "broker_connection_epoch", "broker_active_generation",
        "broker_terminal_generation",
    )
    if (completed.returncode != 0 or not isinstance(result, dict) or
            result.get("accepted") is not True or
            result.get("authoritative_command_status") is not True or
            result.get("recovery_only") is not True or
            result.get("paper_finalization_required") is not True or
            result.get("owner_fenced") is not False or
            result.get("owner_audit_authoritative") is not True or
            result.get("owner_audit_complete") is not True or
            result.get("lease_generation") != owner["lease_generation"] or
            result.get("command_id") != command_id or
            status not in {"accepted", "rejected", "uncertain", "not_found"} or
            reason not in valid_reasons or
            type(order_id) is not int or order_id < -1 or
            not isinstance(command_reason, str) or not command_reason or
            len(command_reason) > 256 or
            not isinstance(result.get("execution_service_epoch"), str) or
            result.get("execution_service_epoch") in {"", "unavailable"} or
            any(type(result.get(name)) is not int or result.get(name, 0) <
                (0 if name.startswith("owner_") else 1)
                for name in integer_fields) or
            result.get("owner_account") != owner.get("owner_account") or
            result.get("owner_execution_domain") !=
                owner.get("owner_execution_domain")):
        raise RuntimeError("EXTERNAL_RECOVERY_COMMAND_STATUS_INVALID")
    if status == "not_found":
        if (reason != "RECOVERY_QUERY_NOT_FOUND_PROVEN_RECOVERY_ONLY" or
                order_id != -1 or
                command_reason != "EXECUTION_COMMAND_NOT_FOUND"):
            raise RuntimeError("EXTERNAL_RECOVERY_COMMAND_STATUS_INVALID")
    elif status == "uncertain" or order_id >= 0:
        if reason != "RECOVERY_QUERY_CANNOT_FULL_FENCE":
            raise RuntimeError("EXTERNAL_RECOVERY_COMMAND_STATUS_INVALID")
    elif reason != "RECOVERY_QUERY_PROVEN_RECOVERY_ONLY":
        raise RuntimeError("EXTERNAL_RECOVERY_COMMAND_STATUS_INVALID")
    return {
        "token_sha256": owner["token_sha256"],
        "command_id": command_id,
        "command_status": status,
        "command_reason_code": command_reason,
        "order_id": order_id,
        "lease_generation": owner["lease_generation"],
        "execution_service_epoch": result["execution_service_epoch"],
        "execution_service_fencing_generation": result[
            "execution_service_fencing_generation"],
        "recovery_expires_at_ms": result["recovery_expires_at_ms"],
        "owner_active_order_count": result["owner_active_order_count"],
        "owner_uncertain_command_count": result[
            "owner_uncertain_command_count"],
        "broker_connection_epoch": result["broker_connection_epoch"],
        "broker_active_generation": result["broker_active_generation"],
        "broker_terminal_generation": result["broker_terminal_generation"],
        "owner_account": result["owner_account"],
        "owner_execution_domain": result["owner_execution_domain"],
        "recovery_only": True,
    }


def _external_recovery_fence_all_owners(
        checkpoint: dict[str, object],
) -> list[dict[str, object]]:
    owners = checkpoint["owners"]
    assert isinstance(owners, list)
    queries: list[dict[str, object]] = []
    common: tuple[object, ...] | None = None
    for owner in owners:
        assert isinstance(owner, dict)
        command_ids = owner["recovery_command_ids"]
        assert isinstance(command_ids, list)
        for command_id in command_ids:
            assert isinstance(command_id, str)
            observed = _external_recovery_query_owner(owner, command_id)
            boundary = (
                observed["broker_connection_epoch"],
                observed["broker_active_generation"],
                observed["broker_terminal_generation"],
                observed["owner_account"],
                observed["owner_execution_domain"],
            )
            if common is None:
                common = boundary
            elif boundary != common:
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_OWNER_BOUNDARY_INCONSISTENT")
            queries.append(observed)
    if not queries:
        raise RuntimeError("EXTERNAL_RECOVERY_SESSION_OWNER_MISSING")
    checkpoint["owner_queries"] = queries
    checkpoint["phase"] = "OWNERS_RECOVERY_ONLY"
    _persist_external_recovery_checkpoint(checkpoint)
    return queries


def _external_materialize_owner_contexts(
        checkpoint: dict[str, object],
) -> dict[str, tuple[Path, argparse.Namespace]]:
    identity = pwd.getpwnam(AGENT_USER)
    contexts: dict[str, tuple[Path, argparse.Namespace]] = {}
    owners = checkpoint["owners"]
    assert isinstance(owners, list)
    for owner in owners:
        assert isinstance(owner, dict)
        exact_owner = {
            name: owner[name] for name in EXTERNAL_CANARY_OWNER_FIELDS}
        _material, _authority_raw, bearer_raw = (
            _external_recovery_owner_material(exact_owner))
        if (owner["peer_uid"] != identity.pw_uid or
                owner["peer_gid"] != identity.pw_gid or
                owner["token_sha256"] != owner["revoke_bearer_sha256"] or
                "sha256:" + hashlib.sha256(bearer_raw).hexdigest() !=
                    owner["token_sha256"]):
            raise RuntimeError("EXTERNAL_RECOVERY_SESSION_OWNER_INVALID")
        token_path = Path(str(owner["token_path"]))
        try:
            current, _metadata = _stable_file_bytes(
                token_path, "EXTERNAL_RECOVERY_DELIVERY_TOKEN_INVALID",
                expected_uid=identity.pw_uid, expected_gid=identity.pw_gid,
                allowed_modes=frozenset({0o400}),
                maximum_bytes=65)
            if current != bearer_raw:
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_DELIVERY_TOKEN_COLLISION")
        except FileNotFoundError:
            _create_private_bytes_exclusive(
                token_path, bearer_raw, uid=identity.pw_uid,
                gid=identity.pw_gid,
                failure_prefix="EXTERNAL_RECOVERY_DELIVERY_TOKEN",
                mode=0o400)
        arguments = agent_arguments(AGENT_STATE, token_path)
        arguments.managed_owner_session_id = str(owner["session_id"])
        arguments.managed_owner_account = str(owner["owner_account"])
        arguments.managed_owner_execution_domain = str(
            owner["owner_execution_domain"])
        contexts[str(owner["token_name"])] = (token_path, arguments)
    if len(contexts) != len(owners):
        raise RuntimeError("EXTERNAL_RECOVERY_SESSION_OWNER_DUPLICATE")
    return contexts


def _external_owner_by_token(
        checkpoint: dict[str, object], token_name: str,
) -> dict[str, object]:
    owners = checkpoint["owners"]
    assert isinstance(owners, list)
    matching = [
        owner for owner in owners if isinstance(owner, dict) and
        owner.get("token_name") == token_name]
    if len(matching) != 1:
        raise RuntimeError("EXTERNAL_RECOVERY_SESSION_OWNER_MISSING")
    return matching[0]


def _external_stable_command_id(
        authority: dict[str, object], purpose: str, *values: object,
) -> str:
    digest = hashlib.sha256(_canonical_json_bytes({
        "recovery_id": authority["recovery_id"],
        "purpose": purpose,
        "values": values,
    })).hexdigest()[:40]
    return "ext-recovery-" + purpose + "-" + digest


def _external_set_pending_mutation(
        checkpoint: dict[str, object], value: dict[str, object],
) -> None:
    current = checkpoint.get("pending_mutation")
    if current is not None and current != value:
        raise RuntimeError("EXTERNAL_RECOVERY_PENDING_MUTATION_CONFLICT")
    checkpoint["pending_mutation"] = value
    _persist_external_recovery_checkpoint(checkpoint)


def _external_clear_pending_mutation(
        checkpoint: dict[str, object], expected: dict[str, object],
) -> None:
    if checkpoint.get("pending_mutation") != expected:
        raise RuntimeError("EXTERNAL_RECOVERY_PENDING_MUTATION_CONFLICT")
    checkpoint["pending_mutation"] = None
    _persist_external_recovery_checkpoint(checkpoint)


def _external_decimal(
        value: object, failure: str, *, positive: bool = False,
        signed: bool = True,
) -> Decimal:
    """Parse one canonical base-10 string without binary coercion."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 64 or
            EXTERNAL_CANONICAL_DECIMAL.fullmatch(value) is None or
            (not signed and value.startswith("-"))):
        raise RuntimeError(failure)
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise RuntimeError(failure) from error
    if (not result.is_finite() or (result == 0 and value.startswith("-")) or
            (positive and result <= 0)):
        raise RuntimeError(failure)
    return result


def _external_decimal_text(value: Decimal, failure: str) -> str:
    rendered = format(value, "f")
    if (_external_decimal(rendered, failure, signed=True) != value or
            Decimal(rendered).as_tuple().exponent > 0):
        raise RuntimeError(failure)
    return rendered


def _external_validate_risk_snapshot(value: object) -> dict[str, object]:
    failure = "EXTERNAL_RECOVERY_RISK_BOUNDARY_INVALID"
    if not isinstance(value, dict):
        raise RuntimeError(failure)
    numeric: dict[str, Decimal] = {}
    for name in (
            "max_order_quantity", "max_order_notional",
            "max_gross_position", "gross_absolute_position"):
        numeric[name] = _external_decimal(
            value.get(name), failure, signed=False)
    if (value.get("source") != "IB" or
            value.get("authoritative") is not True or
            value.get("gross_scope") != "PAPER_BASELINE_DELTA" or
            type(value.get("max_active_orders")) is not int or
            value.get("max_active_orders") != 1 or
            numeric["max_order_quantity"] != Decimal("1") or
            numeric["max_order_notional"] != Decimal("5000") or
            numeric["max_gross_position"] != Decimal("1") or
            not Decimal("0") <= numeric["gross_absolute_position"] <=
                Decimal("1")):
        raise RuntimeError(failure)
    return {**value, **numeric}


def _external_position_boundary(
        agent: ModuleType,
        contexts: dict[str, tuple[Path, argparse.Namespace]],
) -> tuple[Decimal, int, int]:
    common: tuple[Decimal, int, int] | None = None
    for _token_name, (_token_path, arguments) in sorted(contexts.items()):
        try:
            observed = agent.tool(
                arguments, "portfolio.list_positions", timeout=5)
        except BaseException as error:
            raise RuntimeError(
                "EXTERNAL_RECOVERY_POSITION_NOT_AUTHORITATIVE") from error
        if (not isinstance(observed, dict) or
                observed.get("source") != "IB" or
                observed.get("authoritative") is not True or
                type(observed.get("position_generation")) is not int or
                observed.get("position_generation", 0) < 1 or
                type(observed.get("fx_cash_generation")) is not int or
                observed.get("fx_cash_generation", 0) < 1 or
                not isinstance(observed.get("positions"), list)):
            raise RuntimeError("EXTERNAL_RECOVERY_POSITION_BOUNDARY_INVALID")
        position = Decimal("0")
        seen_instrument = False
        for item in observed["positions"]:
            if not isinstance(item, dict) or not isinstance(
                    item.get("instrument"), str):
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_POSITION_BOUNDARY_INVALID")
            quantity = _external_decimal(
                item.get("quantity"),
                "EXTERNAL_RECOVERY_POSITION_BOUNDARY_INVALID")
            if item["instrument"] == agent.INSTRUMENT:
                if seen_instrument:
                    raise RuntimeError(
                        "EXTERNAL_RECOVERY_POSITION_BOUNDARY_INVALID")
                seen_instrument = True
                position = quantity
            elif quantity != 0:
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_POSITION_BOUNDARY_INVALID")
        if abs(position) > Decimal("1"):
            raise RuntimeError("EXTERNAL_RECOVERY_POSITION_BOUNDARY_INVALID")
        normalized = (
            position, observed["position_generation"],
            observed["fx_cash_generation"])
        if common is None:
            common = normalized
        elif normalized != common:
            raise RuntimeError(
                "EXTERNAL_RECOVERY_POSITION_BOUNDARY_INCONSISTENT")
    if common is None:
        raise RuntimeError("EXTERNAL_RECOVERY_SESSION_OWNER_MISSING")
    return common


def _external_risk_boundary(
        agent: ModuleType,
        contexts: dict[str, tuple[Path, argparse.Namespace]],
) -> dict[str, object]:
    common: dict[str, object] | None = None
    compared = (
        "max_order_quantity", "max_order_notional", "max_active_orders",
        "max_gross_position", "gross_absolute_position", "gross_scope",
    )
    for _token_name, (_token_path, arguments) in sorted(contexts.items()):
        try:
            observed = _external_validate_risk_snapshot(
                agent.tool(arguments, "risk.get_limits", timeout=5))
        except BaseException as error:
            if isinstance(error, RuntimeError) and str(error).startswith(
                    "EXTERNAL_RECOVERY_RISK_BOUNDARY_INVALID"):
                raise
            raise RuntimeError(
                "EXTERNAL_RECOVERY_RISK_NOT_AUTHORITATIVE") from error
        if common is None:
            common = observed
        elif any(observed.get(name) != common.get(name) for name in compared):
            raise RuntimeError(
                "EXTERNAL_RECOVERY_RISK_BOUNDARY_INCONSISTENT")
    if common is None:
        raise RuntimeError("EXTERNAL_RECOVERY_SESSION_OWNER_MISSING")
    return common


def _external_quote_boundary(
        agent: ModuleType, arguments: argparse.Namespace,
) -> dict[str, object]:
    try:
        quote = agent.tool(
            arguments, "market.get_quote",
            {"instrument": agent.INSTRUMENT}, timeout=5)
    except BaseException as error:
        raise RuntimeError(
            "EXTERNAL_RECOVERY_QUOTE_NOT_AUTHORITATIVE") from error
    failure = "EXTERNAL_RECOVERY_QUOTE_BOUNDARY_INVALID"
    if not isinstance(quote, dict):
        raise RuntimeError(failure)
    expected_fields = {
        "source", "authoritative", "instrument", "subscription_id",
        "subscription_state", "observed_at_ms", "stale_after_ms", "stale",
        "bid", "ask",
    }
    bid = _external_decimal(
        quote.get("bid"), failure, positive=True, signed=False)
    ask = _external_decimal(
        quote.get("ask"), failure, positive=True, signed=False)
    now_ms = time.time_ns() // 1_000_000
    if (set(quote) != expected_fields or quote.get("source") != "IB" or
            quote.get("authoritative") is not True or
            quote.get("instrument") != agent.INSTRUMENT or
            quote.get("subscription_state") != "active" or
            not isinstance(quote.get("subscription_id"), str) or
            re.fullmatch(
                r"IB:[1-9][0-9]*:[1-9][0-9]*:[1-9][0-9]*",
                str(quote["subscription_id"])) is None or
            quote.get("stale") is not False or
            type(quote.get("observed_at_ms")) is not int or
            type(quote.get("stale_after_ms")) is not int or
            not 0 <= now_ms - quote["observed_at_ms"] <= 5000 or
            not quote["observed_at_ms"] <= now_ms <=
                quote["stale_after_ms"] or
            quote["stale_after_ms"] - quote["observed_at_ms"] > 5000 or
            not Decimal("0") < bid <= ask <= Decimal("5000")):
        raise RuntimeError(failure)
    return quote


def _external_mutation_query(
        checkpoint: dict[str, object], mutation: dict[str, object],
) -> dict[str, object]:
    owner = _external_owner_by_token(
        checkpoint, str(mutation["token_name"]))
    return _external_recovery_query_owner(
        owner, str(mutation["command_id"]))


EXTERNAL_MUTATION_DISPATCH_BOUNDARY = {
    "execution_service_epoch": "dispatch_execution_service_epoch",
    "execution_service_fencing_generation":
        "dispatch_execution_service_fencing_generation",
    "broker_connection_epoch": "dispatch_broker_connection_epoch",
    "broker_active_generation": "dispatch_broker_active_generation",
    "broker_terminal_generation": "dispatch_broker_terminal_generation",
}


def _external_bind_dispatch_boundary(
        mutation: dict[str, object], status: dict[str, object],
) -> None:
    for source, target in EXTERNAL_MUTATION_DISPATCH_BOUNDARY.items():
        value = status.get(source)
        if ((source == "execution_service_epoch" and
             (not isinstance(value, str) or not value)) or
                (source != "execution_service_epoch" and
                 (type(value) is not int or value < 1))):
            raise RuntimeError(
                "EXTERNAL_RECOVERY_MUTATION_BOUNDARY_INVALID")
        mutation[target] = value


def _external_query_boundary(
        status: dict[str, object],
) -> tuple[object, ...]:
    return tuple(status.get(name) for name in (
        *EXTERNAL_MUTATION_DISPATCH_BOUNDARY,
        "owner_active_order_count", "owner_uncertain_command_count",
        "command_status", "command_reason_code", "order_id",
    ))


def _external_new_service_fence(
        mutation: dict[str, object], status: dict[str, object],
) -> bool:
    old_epoch = mutation.get("dispatch_execution_service_epoch")
    old_generation = mutation.get(
        "dispatch_execution_service_fencing_generation")
    new_epoch = status.get("execution_service_epoch")
    new_generation = status.get("execution_service_fencing_generation")
    if (not isinstance(old_epoch, str) or not old_epoch or
            type(old_generation) is not int or old_generation < 1 or
            not isinstance(new_epoch, str) or not new_epoch or
            type(new_generation) is not int or new_generation < 1):
        return False
    return new_epoch != old_epoch or new_generation > old_generation


def _external_resume_pending_before_tools(
        agent: ModuleType,
        contexts: dict[str, tuple[Path, argparse.Namespace]],
        checkpoint: dict[str, object],
) -> None:
    mutation = checkpoint.get("pending_mutation")
    if mutation is None:
        return
    if not isinstance(mutation, dict):
        raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    status = _external_mutation_query(checkpoint, mutation)
    observed = status["command_status"]
    if observed == "uncertain":
        raise RuntimeError("EXTERNAL_RECOVERY_PENDING_STATUS_UNCERTAIN")
    if observed == "not_found":
        # A pre-dispatch crash may resume the exact durable intent.  Once the
        # dispatch boundary was crossed, NOT_FOUND permits the same stable id
        # only after a new service fence and a complete, stable broker
        # reconciliation.  No replacement command identity is ever minted.
        if mutation.get("dispatch_attempted") is False:
            return
        if (mutation.get("dispatch_attempted") is not True or
                not _external_new_service_fence(mutation, status) or
                status.get("owner_uncertain_command_count") != 0):
            raise RuntimeError(
                "EXTERNAL_RECOVERY_PENDING_OUTCOME_UNRESOLVED")
        token_name = mutation.get("token_name")
        if not isinstance(token_name, str) or token_name not in contexts:
            raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
        arguments = contexts[token_name][1]
        active, owned = _managed_owner_order_projection(
            agent, contexts, "EXTERNAL_RECOVERY")
        token_owned = owned.get(token_name)
        if (not isinstance(token_owned, set) or
                status.get("owner_active_order_count") != len(token_owned)):
            raise RuntimeError(
                "EXTERNAL_RECOVERY_PENDING_RECONCILIATION_INCOMPLETE")
        _snapshot, projection = _owner_order_projection(
            agent, arguments, "EXTERNAL_RECOVERY")
        if (projection.get("connection_epoch") !=
                status.get("broker_connection_epoch") or
                projection.get("generation") !=
                status.get("broker_active_generation") or
                set(projection.get("global_active_order_ids", ())) != active or
                set(projection.get("owned_active_order_ids", ())) !=
                    token_owned):
            raise RuntimeError(
                "EXTERNAL_RECOVERY_PENDING_RECONCILIATION_DRIFTED")
        position, position_generation, _cash_generation = (
            _external_position_boundary(agent, contexts))
        risk = _external_risk_boundary(agent, contexts)
        if (risk.get("gross_absolute_position") != abs(position) or
                _external_query_boundary(
                    _external_mutation_query(checkpoint, mutation)) !=
                _external_query_boundary(status)):
            raise RuntimeError(
                "EXTERNAL_RECOVERY_PENDING_RECONCILIATION_DRIFTED")
        kind = mutation.get("kind")
        if kind == "CANCEL_ORDER":
            order_id = mutation.get("order_id")
            if type(order_id) is not int or order_id < 0:
                raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
            if order_id not in active:
                if active:
                    raise RuntimeError(
                        "EXTERNAL_RECOVERY_PENDING_RECONCILIATION_DRIFTED")
                proven = _external_terminal_order_proof(
                    agent, arguments, [order_id])
                terminal = checkpoint["terminal_order_ids"]
                assert isinstance(terminal, list)
                terminal[:] = sorted(set([*terminal, *proven]))
                checkpoint["pending_mutation"] = None
                _persist_external_recovery_checkpoint(checkpoint)
                return
            if active != {order_id} or token_owned != {order_id}:
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_PENDING_RECONCILIATION_DRIFTED")
            checkpoint["pending_mutation"] = {
                "kind": "CANCEL_ORDER", "token_name": token_name,
                "command_id": mutation["command_id"],
                "order_id": order_id, "dispatch_attempted": False,
            }
            _persist_external_recovery_checkpoint(checkpoint)
            return
        if kind == "FLATTEN_POSITION":
            if active or token_owned:
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_PENDING_RECONCILIATION_DRIFTED")
            prior_position = _external_decimal(
                mutation.get("position_quantity"),
                "EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
            if position == 0:
                checkpoint["pending_mutation"] = None
                _persist_external_recovery_checkpoint(checkpoint)
                return
            if position != prior_position:
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_PENDING_RECONCILIATION_DRIFTED")
            checkpoint["pending_mutation"] = {
                "kind": "FLATTEN_POSITION", "token_name": token_name,
                "command_id": mutation["command_id"],
                "position_generation": position_generation,
                "position_quantity": _external_decimal_text(
                    position, "EXTERNAL_RECOVERY_CHECKPOINT_INVALID"),
                "dispatch_attempted": False,
            }
            _persist_external_recovery_checkpoint(checkpoint)
            return
        raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    if observed != "accepted":
        raise RuntimeError("EXTERNAL_RECOVERY_PENDING_MUTATION_REJECTED")
    order_id = status["order_id"]
    if mutation.get("kind") == "CANCEL_ORDER":
        cancelled = checkpoint["cancelled_order_ids"]
        assert isinstance(cancelled, list)
        target = mutation.get("order_id")
        if type(target) is int and target >= 0 and target not in cancelled:
            cancelled.append(target)
            cancelled.sort()
    elif mutation.get("kind") == "FLATTEN_POSITION":
        flattened = checkpoint["flatten_order_ids"]
        assert isinstance(flattened, list)
        if type(order_id) is int and order_id >= 0 and order_id not in flattened:
            flattened.append(order_id)
            flattened.sort()
        elif status["command_reason_code"] != "POSITION_ALREADY_FLAT":
            raise RuntimeError("EXTERNAL_RECOVERY_PENDING_STATUS_INVALID")
    else:
        raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    checkpoint["pending_mutation"] = None
    _persist_external_recovery_checkpoint(checkpoint)


def _external_dispatch_cancel(
        agent: ModuleType, arguments: argparse.Namespace,
        checkpoint: dict[str, object], token_name: str, order_id: int,
        authority: dict[str, object],
) -> None:
    del authority
    owner = _external_owner_by_token(checkpoint, token_name)
    command_id = owner.get("recovery_cancel_command_id")
    if not isinstance(command_id, str):
        raise RuntimeError("EXTERNAL_RECOVERY_COMMAND_ID_MISSING")
    mutation: dict[str, object] = {
        "kind": "CANCEL_ORDER", "token_name": token_name,
        "command_id": command_id, "order_id": order_id,
        "dispatch_attempted": False,
    }
    _external_set_pending_mutation(checkpoint, mutation)
    status = _external_mutation_query(checkpoint, mutation)
    if status["command_status"] == "uncertain":
        raise RuntimeError("EXTERNAL_RECOVERY_CANCEL_STATUS_UNCERTAIN")
    if status["command_status"] == "accepted":
        _external_clear_pending_mutation(checkpoint, mutation)
        return
    if status["command_status"] != "not_found":
        raise RuntimeError("EXTERNAL_RECOVERY_CANCEL_REJECTED")
    mutation["dispatch_attempted"] = True
    _external_bind_dispatch_boundary(mutation, status)
    checkpoint["pending_mutation"] = mutation
    _persist_external_recovery_checkpoint(checkpoint)
    try:
        response = agent.tool_response(
            arguments, "trade.cancel_order", {"order_id": order_id},
            command_id, timeout=16)
        if (response.get("command_id") not in {None, command_id} or
                response.get("order_id") not in {None, order_id}):
            raise RuntimeError("EXTERNAL_RECOVERY_CANCEL_RESPONSE_INVALID")
        _external_clear_pending_mutation(checkpoint, mutation)
        return
    except BaseException as error:
        settled = _external_mutation_query(checkpoint, mutation)
        if settled["command_status"] == "accepted":
            _external_clear_pending_mutation(checkpoint, mutation)
            return
        raise RuntimeError(
            "EXTERNAL_RECOVERY_CANCEL_OUTCOME_UNRESOLVED") from error


def _external_terminal_order_proof(
        agent: ModuleType, arguments: argparse.Namespace,
        order_ids: list[int],
) -> list[int]:
    """Prove terminal orders without binary quantity/price coercion."""
    failure = "EXTERNAL_RECOVERY"
    targets = sorted(set(order_ids))
    if not targets:
        return []
    snapshot, projection = _owner_order_projection(agent, arguments, failure)
    if projection["global_active_order_ids"]:
        raise RuntimeError(failure + "_ORDER_STILL_ACTIVE")
    recent = snapshot.get("recent_orders")
    if not isinstance(recent, list):
        raise RuntimeError(failure + "_ORDER_PROJECTION_INVALID")
    proven: list[int] = []
    for order_id in targets:
        matching = [
            item for item in recent if isinstance(item, dict) and
            item.get("order_id") == order_id and item.get("terminal") is True]
        if len(matching) != 1:
            raise RuntimeError(
                failure + "_ORDER_TERMINAL_EVIDENCE_MISSING:" +
                str(order_id))
        item = matching[0]
        instrument = item.get("instrument")
        if (isinstance(instrument, str) and instrument and
                instrument != agent.INSTRUMENT):
            raise RuntimeError(failure + "_ORDER_INSTRUMENT_MISMATCH")
        normalized_status = "".join(
            character for character in str(item.get("status", "")).lower()
            if character.isalnum())
        if normalized_status == "filled":
            if item.get("economic_fill") is not True:
                raise RuntimeError(
                    failure + "_FILLED_ORDER_LACKS_ECONOMIC_EVIDENCE")
            filled = _external_decimal(
                item.get("filled_quantity"),
                failure + "_ORDER_ECONOMIC_EVIDENCE_INVALID",
                positive=True, signed=False)
            remaining = _external_decimal(
                item.get("remaining_quantity"),
                failure + "_ORDER_ECONOMIC_EVIDENCE_INVALID",
                signed=False)
            average = _external_decimal(
                item.get("average_fill_price"),
                failure + "_ORDER_ECONOMIC_EVIDENCE_INVALID",
                positive=True, signed=False)
            if (filled > Decimal("1") or remaining != 0 or
                    average > Decimal("5000") or
                    filled * average > Decimal("5000")):
                raise RuntimeError(
                    failure + "_ORDER_ECONOMIC_EVIDENCE_INVALID")
        elif normalized_status not in agent.TERMINAL_NON_FILL_STATUSES:
            raise RuntimeError(
                failure + "_ORDER_TERMINAL_EVIDENCE_MISSING:" +
                str(order_id))
        proven.append(order_id)
    return proven


def _external_cancel_all_owned_orders(
        agent: ModuleType,
        contexts: dict[str, tuple[Path, argparse.Namespace]],
        checkpoint: dict[str, object], authority: dict[str, object],
) -> dict[str, set[int]]:
    targets: dict[str, set[int]] = {name: set() for name in contexts}
    queries = checkpoint.get("owner_queries")
    if not isinstance(queries, list):
        raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
    token_by_hash = {
        str(owner["token_sha256"]): str(owner["token_name"])
        for owner in checkpoint["owners"] if isinstance(owner, dict)}
    for query in queries:
        if not isinstance(query, dict):
            raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
        order_id = query.get("order_id")
        token_name = token_by_hash.get(str(query.get("token_sha256")))
        if token_name is not None and type(order_id) is int and order_id >= 0:
            targets[token_name].add(order_id)
    deadline = time.monotonic() + 45.0
    while True:
        active, owned = _managed_owner_order_projection(
            agent, contexts, "EXTERNAL_RECOVERY")
        for token_name, order_ids in owned.items():
            targets[token_name].update(order_ids)
        if not active:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("EXTERNAL_RECOVERY_ACTIVE_ORDERS_UNRESOLVED")
        for token_name, order_ids in sorted(owned.items()):
            arguments = contexts[token_name][1]
            for order_id in sorted(order_ids):
                _external_dispatch_cancel(
                    agent, arguments, checkpoint, token_name, order_id,
                    authority)
                cancelled = checkpoint["cancelled_order_ids"]
                assert isinstance(cancelled, list)
                if order_id not in cancelled:
                    cancelled.append(order_id)
                    cancelled.sort()
                    _persist_external_recovery_checkpoint(checkpoint)
        time.sleep(1.0)
    terminal: set[int] = set()
    for token_name, order_ids in sorted(targets.items()):
        proven = _external_terminal_order_proof(
            agent, contexts[token_name][1], sorted(order_ids))
        terminal.update(proven)
    checkpoint["terminal_order_ids"] = sorted(terminal)
    checkpoint["pending_mutation"] = None
    _persist_external_recovery_checkpoint(checkpoint)
    return targets


def _external_validate_flatten_preview(
        agent: ModuleType, preview: object, command_id: str,
        position: Decimal, position_generation: int,
        quote: dict[str, object],
) -> tuple[str, Decimal, str]:
    failure = "EXTERNAL_RECOVERY_FLATTEN_PREVIEW_INVALID"
    if not isinstance(preview, dict):
        raise RuntimeError(failure)
    outer_fields = {
        "approved", "preview_permit", "command_id",
        "permit_expires_at_ms", "single_use", "service_epoch",
        "service_fencing_generation", "authoritative_preview",
    }
    authoritative_fields = {
        "source", "authoritative", "position_connection_epoch",
        "position_generation", "position_quantity", "side", "quantity",
        "order_type", "tif", "limit_price", "reference_price",
        "quote_bid", "quote_ask", "quote_subscription_id",
        "quote_observed_at_ms", "reduce_only", "atomic", "risk_approved",
    }
    if set(preview) != outer_fields:
        raise RuntimeError(failure)
    authoritative = preview.get("authoritative_preview")
    permit = preview.get("preview_permit")
    if not isinstance(authoritative, dict):
        raise RuntimeError(failure)
    quantity = _external_decimal(
        authoritative.get("quantity"), failure, positive=True, signed=False)
    preview_position = _external_decimal(
        authoritative.get("position_quantity"), failure)
    reference_price = _external_decimal(
        authoritative.get("reference_price"), failure,
        positive=True, signed=False)
    limit_price = _external_decimal(
        authoritative.get("limit_price"), failure,
        positive=True, signed=False)
    quote_bid = _external_decimal(
        authoritative.get("quote_bid"), failure,
        positive=True, signed=False)
    quote_ask = _external_decimal(
        authoritative.get("quote_ask"), failure,
        positive=True, signed=False)
    boundary_bid = _external_decimal(
        quote.get("bid"), failure, positive=True, signed=False)
    boundary_ask = _external_decimal(
        quote.get("ask"), failure, positive=True, signed=False)
    side = str(authoritative.get("side", "")).upper()
    tif = authoritative.get("tif")
    now_ms = time.time_ns() // 1_000_000
    expected_price = quote_bid if side == "SELL" else quote_ask
    if (set(authoritative) != authoritative_fields or
            preview.get("approved") is not True or
            preview.get("command_id") != command_id or
            preview.get("single_use") is not True or
            not isinstance(permit, str) or len(permit) < 32 or
            type(preview.get("permit_expires_at_ms")) is not int or
            preview["permit_expires_at_ms"] < now_ms or
            not isinstance(preview.get("service_epoch"), str) or
            not preview["service_epoch"] or
            type(preview.get("service_fencing_generation")) is not int or
            preview["service_fencing_generation"] < 1 or
            authoritative.get("source") != "IB" or
            authoritative.get("authoritative") is not True or
            authoritative.get("reduce_only") is not True or
            authoritative.get("risk_approved") is not True or
            authoritative.get("atomic") is not True or
            authoritative.get("order_type") != "LMT" or tif != "DAY" or
            type(authoritative.get("position_connection_epoch")) is not int or
            authoritative["position_connection_epoch"] < 1 or
            authoritative.get("position_generation") != position_generation or
            not isinstance(authoritative.get("quote_subscription_id"), str) or
            not authoritative["quote_subscription_id"] or
            authoritative.get("quote_subscription_id") !=
                quote.get("subscription_id") or
            type(authoritative.get("quote_observed_at_ms")) is not int or
            not 0 <= now_ms - authoritative["quote_observed_at_ms"] <= 5000 or
            authoritative.get("quote_observed_at_ms") !=
                quote.get("observed_at_ms") or
            preview_position != position or
            quantity != abs(position) or
            not Decimal("0") < quantity <= Decimal("1") or
            not Decimal("0") < quote_bid <= quote_ask <= Decimal("5000") or
            quote_bid != boundary_bid or quote_ask != boundary_ask or
            not Decimal("0") < reference_price <= Decimal("5000") or
            not Decimal("0") < limit_price <= Decimal("5000") or
            reference_price != limit_price or
            limit_price != expected_price or
            quantity * limit_price > Decimal("5000") or
            side not in {"BUY", "SELL"} or
            (position > 0 and side != "SELL") or
            (position < 0 and side != "BUY")):
        raise RuntimeError(failure)
    return str(permit), quantity, side


def _external_flatten_position(
        agent: ModuleType,
        contexts: dict[str, tuple[Path, argparse.Namespace]],
        checkpoint: dict[str, object], authority: dict[str, object],
) -> None:
    position, position_generation, _cash_generation = (
        _external_position_boundary(agent, contexts))
    risk = _external_risk_boundary(agent, contexts)
    gross = risk["gross_absolute_position"]
    if not isinstance(gross, Decimal) or gross != abs(position):
        raise RuntimeError("EXTERNAL_RECOVERY_RISK_BOUNDARY_INVALID")
    if position == 0:
        if gross != 0:
            raise RuntimeError("EXTERNAL_RECOVERY_POSITION_RISK_MISMATCH")
        return
    token_name = sorted(contexts)[0]
    arguments = contexts[token_name][1]
    quote = _external_quote_boundary(agent, arguments)
    del authority
    owner = _external_owner_by_token(checkpoint, token_name)
    command_id = owner.get("recovery_flatten_command_id")
    if not isinstance(command_id, str):
        raise RuntimeError("EXTERNAL_RECOVERY_COMMAND_ID_MISSING")
    mutation: dict[str, object] = {
        "kind": "FLATTEN_POSITION", "token_name": token_name,
        "command_id": command_id, "position_generation":
            position_generation, "position_quantity":
            _external_decimal_text(
                position, "EXTERNAL_RECOVERY_POSITION_BOUNDARY_INVALID"),
        "dispatch_attempted": False,
    }
    _external_set_pending_mutation(checkpoint, mutation)
    status = _external_mutation_query(checkpoint, mutation)
    flatten_order_id: int | None = None
    if status["command_status"] == "uncertain":
        raise RuntimeError("EXTERNAL_RECOVERY_FLATTEN_STATUS_UNCERTAIN")
    if status["command_status"] == "accepted":
        if type(status["order_id"]) is int and status["order_id"] >= 0:
            flatten_order_id = int(status["order_id"])
        elif status["command_reason_code"] != "POSITION_ALREADY_FLAT":
            raise RuntimeError("EXTERNAL_RECOVERY_FLATTEN_STATUS_INVALID")
    elif status["command_status"] == "not_found":
        preview = agent.tool(
            arguments, "risk.preview_flatten",
            {"instrument": agent.INSTRUMENT}, command_id, timeout=16)
        if (not isinstance(preview, dict) or
                preview.get("service_epoch") !=
                    status.get("execution_service_epoch") or
                preview.get("service_fencing_generation") !=
                    status.get("execution_service_fencing_generation")):
            raise RuntimeError(
                "EXTERNAL_RECOVERY_FLATTEN_PREVIEW_INVALID")
        permit, _quantity, _side = _external_validate_flatten_preview(
            agent, preview, command_id, position, position_generation, quote)
        mutation["dispatch_attempted"] = True
        _external_bind_dispatch_boundary(mutation, status)
        checkpoint["pending_mutation"] = mutation
        _persist_external_recovery_checkpoint(checkpoint)
        try:
            response = agent.tool_response(
                arguments, "trade.flatten_position", {
                    "instrument": agent.INSTRUMENT,
                    "preview_permit": permit,
                }, command_id, timeout=16)
            raw_order_id = response.get("order_id")
            if (response.get("command_id") not in {None, command_id} or
                    type(raw_order_id) is not int or raw_order_id < 0):
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_FLATTEN_RESPONSE_INVALID")
            flatten_order_id = raw_order_id
        except BaseException as error:
            settled = _external_mutation_query(checkpoint, mutation)
            if (settled["command_status"] == "accepted" and
                    type(settled["order_id"]) is int and
                    settled["order_id"] >= 0):
                flatten_order_id = int(settled["order_id"])
            elif (settled["command_status"] == "accepted" and
                    settled["command_reason_code"] ==
                        "POSITION_ALREADY_FLAT"):
                flatten_order_id = None
            else:
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_FLATTEN_OUTCOME_UNRESOLVED") from error
    else:
        raise RuntimeError("EXTERNAL_RECOVERY_FLATTEN_REJECTED")
    if flatten_order_id is not None:
        order_ids = checkpoint["flatten_order_ids"]
        assert isinstance(order_ids, list)
        if flatten_order_id not in order_ids:
            order_ids.append(flatten_order_id)
            order_ids.sort()
            _persist_external_recovery_checkpoint(checkpoint)
    deadline = time.monotonic() + 45.0
    while True:
        after, _generation, _cash = _external_position_boundary(
            agent, contexts)
        if after == 0:
            break
        if abs(after) >= abs(position) or time.monotonic() >= deadline:
            raise RuntimeError("EXTERNAL_RECOVERY_FLATTEN_DID_NOT_REDUCE")
        time.sleep(1.0)
    active, _owned = _managed_owner_order_projection(
        agent, contexts, "EXTERNAL_RECOVERY")
    if active:
        raise RuntimeError("EXTERNAL_RECOVERY_FLATTEN_ORDER_STILL_ACTIVE")
    if flatten_order_id is not None:
        _external_terminal_order_proof(
            agent, arguments, [flatten_order_id])
    _external_clear_pending_mutation(checkpoint, mutation)


def _external_zero_exposure_proof(
        agent: ModuleType,
        contexts: dict[str, tuple[Path, argparse.Namespace]],
        proof_index: int,
) -> dict[str, object]:
    active, _owned = _managed_owner_order_projection(
        agent, contexts, "EXTERNAL_RECOVERY")
    if active:
        raise RuntimeError("EXTERNAL_RECOVERY_ZERO_PROOF_ACTIVE_ORDERS")
    first_arguments = contexts[sorted(contexts)[0]][1]
    _snapshot, order_boundary = _owner_order_projection(
        agent, first_arguments, "EXTERNAL_RECOVERY")
    if order_boundary["global_active_order_ids"]:
        # The second projection is the generation carried by this proof.  It
        # must therefore be independently flat too; relying only on the first
        # all-owner projection would let an order appearing between the two
        # reads be sealed as active_order_count=0.
        raise RuntimeError("EXTERNAL_RECOVERY_ZERO_PROOF_ACTIVE_ORDERS")
    position, position_generation, cash_generation = (
        _external_position_boundary(agent, contexts))
    risk = _external_risk_boundary(agent, contexts)
    if (not isinstance(risk["gross_absolute_position"], Decimal) or
            position != 0 or risk["gross_absolute_position"] != 0):
        raise RuntimeError("EXTERNAL_RECOVERY_ZERO_PROOF_NOT_FLAT")
    return _sealed_json_document({
        "schema": "hepta.local-ai-paper-external-recovery-zero-proof.v1",
        "version": 1,
        "proof_index": proof_index,
        "observed_at_ms": time.time_ns() // 1_000_000,
        "position_quantity": 0,
        "gross_absolute_position": 0,
        "active_order_count": 0,
        "position_generation": position_generation,
        "fx_cash_generation": cash_generation,
        "orders_connection_epoch": order_boundary["connection_epoch"],
        "orders_generation": order_boundary["generation"],
        "owner_count": len(contexts),
        "paper_only": True,
        "live_authorized": False,
    })


def _external_require_historic_commands_settled(
        checkpoint: dict[str, object],
) -> list[dict[str, object]]:
    """Prove every pre-recovery command is terminal on its exact owner."""
    owners = checkpoint.get("owners")
    if not isinstance(owners, list) or not owners:
        raise RuntimeError("EXTERNAL_RECOVERY_SESSION_OWNER_MISSING")
    audits: list[dict[str, object]] = []
    for owner in owners:
        if not isinstance(owner, dict):
            raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
        command_ids = owner.get("recovery_command_ids")
        if not isinstance(command_ids, list) or not command_ids:
            raise RuntimeError("EXTERNAL_RECOVERY_COMMAND_ID_MISSING")
        for command_id in command_ids:
            if not isinstance(command_id, str):
                raise RuntimeError("EXTERNAL_RECOVERY_COMMAND_ID_MISSING")
            audit = _external_recovery_query_owner(owner, command_id)
            if (audit["command_status"] == "uncertain" or
                    audit["owner_active_order_count"] != 0 or
                    audit["owner_uncertain_command_count"] != 0):
                raise RuntimeError(
                    "EXTERNAL_RECOVERY_OWNER_NOT_TERMINALLY_AUDITED")
            audits.append(audit)
    return audits


def _external_final_zero_proof_is_sealed(
        checkpoint: dict[str, object]) -> bool:
    proofs = checkpoint.get("zero_exposure_proofs")
    if not isinstance(proofs, list) or len(proofs) != 2:
        return False
    expected_fields = {
        "schema", "version", "proof_index", "observed_at_ms",
        "position_quantity", "gross_absolute_position",
        "active_order_count", "position_generation", "fx_cash_generation",
        "orders_connection_epoch", "orders_generation", "owner_count",
        "paper_only", "live_authorized", "body_sha256",
    }
    for proof, expected_index in zip(proofs, (1, 3), strict=True):
        if (not isinstance(proof, dict) or set(proof) != expected_fields or
                proof.get("schema") !=
                    "hepta.local-ai-paper-external-recovery-zero-proof.v1" or
                proof.get("version") != 1 or
                proof.get("proof_index") != expected_index or
                type(proof.get("observed_at_ms")) is not int or
                any(type(proof.get(name)) is not int or proof.get(name, 0) < 1
                    for name in (
                        "position_generation", "fx_cash_generation",
                        "orders_connection_epoch", "orders_generation",
                        "owner_count")) or
                any(proof.get(name) != 0 for name in (
                    "position_quantity", "gross_absolute_position",
                    "active_order_count")) or
                proof.get("paper_only") is not True or
                proof.get("live_authorized") is not False or
                _sealed_json_document({
                    key: value for key, value in proof.items()
                    if key != "body_sha256"}) != proof):
            return False
    return (
        int(proofs[0]["observed_at_ms"]) <
            int(proofs[1]["observed_at_ms"]) and
        proofs[0]["body_sha256"] != proofs[1]["body_sha256"] and
        proofs[0]["orders_connection_epoch"] ==
            proofs[1]["orders_connection_epoch"] and
        all(int(proofs[0][name]) < int(proofs[1][name]) for name in (
            "position_generation", "fx_cash_generation",
            "orders_generation")))


def _external_remove_delivery_token(owner: dict[str, object]) -> None:
    identity = pwd.getpwnam(AGENT_USER)
    path = Path(str(owner["token_path"]))
    try:
        raw, _metadata = _stable_file_bytes(
            path, "EXTERNAL_RECOVERY_DELIVERY_TOKEN_INVALID",
            expected_uid=identity.pw_uid, expected_gid=identity.pw_gid,
            allowed_modes=frozenset({0o400}),
            maximum_bytes=65)
    except FileNotFoundError:
        return
    if "sha256:" + hashlib.sha256(raw).hexdigest() != owner["token_sha256"]:
        raise RuntimeError("EXTERNAL_RECOVERY_DELIVERY_TOKEN_INVALID")
    os.unlink(path)
    _fsync_parent(path)


def _external_destroy_root_owner_material(
        owner: dict[str, object], *, allow_absent: bool) -> None:
    for path_key, expected_key, maximum in (
            ("revoke_bearer_path", "revoke_bearer_sha256", 65),
            ("authority_path", "authority_file_sha256", 16 * 1024 * 1024)):
        path = Path(str(owner[path_key]))
        try:
            raw, _metadata = _stable_file_bytes(
                path, "EXTERNAL_RECOVERY_ROOT_BEARER_INVALID",
                expected_uid=0, expected_gid=0, maximum_bytes=maximum)
        except FileNotFoundError:
            if allow_absent:
                continue
            raise RuntimeError("EXTERNAL_RECOVERY_ROOT_BEARER_MISSING")
        if ("sha256:" + hashlib.sha256(raw).hexdigest() !=
                owner[expected_key]):
            raise RuntimeError("EXTERNAL_RECOVERY_ROOT_BEARER_INVALID")
        os.unlink(path)
        _fsync_parent(path)


def _external_finalize_all_owners(
        checkpoint: dict[str, object], *, agent: ModuleType | None = None,
        contexts: dict[str, tuple[Path, argparse.Namespace]] | None = None,
) -> None:
    owners = checkpoint["owners"]
    preliminary_owners = checkpoint["preliminary_owner_token_sha256s"]
    assert isinstance(owners, list) and isinstance(preliminary_owners, list)
    pending = checkpoint.get("pending_mutation")
    if not preliminary_owners and pending is None:
        if agent is None or contexts is None:
            raise RuntimeError("EXTERNAL_RECOVERY_FINAL_ZERO_PROOF_REQUIRED")
        # This remains a diagnostic/precondition only.  Terminal-flat is
        # established exclusively by the supervisor's post-fence composite
        # broker audit and durable AUDIT_SEALED receipt below.
        _external_require_historic_commands_settled(checkpoint)
        proofs = checkpoint.get("zero_exposure_proofs")
        if not isinstance(proofs, list) or len(proofs) != 2:
            raise RuntimeError("EXTERNAL_RECOVERY_ZERO_PROOF_INVALID")
        final = _external_zero_exposure_proof(agent, contexts, 3)
        proofs[:] = [proofs[0], final]
        _persist_external_recovery_checkpoint(checkpoint)
        if not _external_final_zero_proof_is_sealed(checkpoint):
            raise RuntimeError("EXTERNAL_RECOVERY_FINAL_ZERO_PROOF_REQUIRED")
    elif not _external_final_zero_proof_is_sealed(checkpoint):
        # A durable revoke intent (or a completed earlier revoke) is the crash
        # seam proving that the final post-audit proof was already persisted.
        raise RuntimeError("EXTERNAL_RECOVERY_FINAL_ZERO_PROOF_REQUIRED")
    owner_set_sha256, owner_count, _canonical, _account, _domain = (
        _external_finalization_owner_binding(owners))
    if (
            checkpoint.get("expected_owner_set_sha256") != owner_set_sha256 or
            checkpoint.get("expected_owner_count") != owner_count or
            checkpoint.get("finalization_id") != _external_finalization_id(
                str(checkpoint["recovery_id"]), owner_set_sha256,
                owner_count)):
        raise RuntimeError("EXTERNAL_RECOVERY_FINALIZATION_BINDING_INVALID")
    for owner in sorted(owners, key=lambda item: str(item["token_sha256"])):
        assert isinstance(owner, dict)
        token_sha256 = str(owner["token_sha256"])
        if token_sha256 in preliminary_owners:
            continue
        mutation: dict[str, object] = {
            "kind": "PAPER_FINALIZE",
            "token_name": owner["token_name"],
            "token_sha256": token_sha256,
            "lease_generation": owner["lease_generation"],
            "recovery_id": checkpoint["recovery_id"],
            "finalization_id": checkpoint["finalization_id"],
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": owner_count,
        }
        current = checkpoint.get("pending_mutation")
        if current is None:
            # The exact intent is durable before the remote fence transition.
            # Lost responses are recovered only by replaying this same binding;
            # bare session absence is never accepted as evidence.
            checkpoint["pending_mutation"] = mutation
            _persist_external_recovery_checkpoint(checkpoint)
        elif current != mutation:
            raise RuntimeError("EXTERNAL_RECOVERY_PENDING_MUTATION_CONFLICT")
        exact_owner = {
            name: owner[name] for name in EXTERNAL_CANARY_OWNER_FIELDS}
        _external_recovery_owner_material(exact_owner)
        completed = run([
            "/usr/bin/hepta-sessionctl", "--socket", SUPERVISOR_SOCKET,
            "paper-finalize", "--token-file",
            str(owner["revoke_bearer_path"]),
            "--generation", str(owner["lease_generation"]),
            "--recovery-id", str(checkpoint["recovery_id"]),
            "--finalization-id", str(checkpoint["finalization_id"]),
            "--expected-owner-set-sha256", owner_set_sha256,
            "--expected-owner-count", str(owner_count),
            "--token-owner-uid", "0",
        ], timeout=120)
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "EXTERNAL_RECOVERY_FINALIZATION_UNAVAILABLE") from error
        if (
                completed.returncode == 4 and isinstance(result, dict) and
                result.get("paper_finalization_state") == "FENCE_COMPLETE" and
                result.get("reason_code") ==
                    "PAPER_FINALIZATION_GROUP_PENDING"):
            _external_validate_finalization_result(
                result, owner, checkpoint, expected_state="FENCE_COMPLETE")
            preliminary_owners.append(token_sha256)
            preliminary_owners.sort()
            checkpoint["pending_mutation"] = None
            _persist_external_recovery_checkpoint(checkpoint)
            continue
        if completed.returncode != 0:
            # Keep the exact durable intent for a bounded exact replay.  This
            # includes the execution adapter's reconnect/barrier-in-progress
            # response; no new ID or naked revoke fallback is permitted.
            raise RuntimeError("EXTERNAL_RECOVERY_FINALIZATION_PENDING")
        sealed = _external_validate_finalization_result(
            result, owner, checkpoint, expected_state="AUDIT_SEALED")
        preliminary_owners[:] = sorted(
            str(item["token_sha256"]) for item in owners)
        checkpoint["preliminary_finalization_result"] = dict(sealed)
        checkpoint["pending_mutation"] = None
        checkpoint["phase"] = "PRELIMINARY_SEALED"
        _persist_external_recovery_checkpoint(checkpoint)
        break
    if (
            checkpoint.get("phase") != "PRELIMINARY_SEALED" or
            checkpoint.get("preliminary_finalization_result") is None or
            set(preliminary_owners) != {
                str(owner["token_sha256"]) for owner in owners}):
        raise RuntimeError("EXTERNAL_RECOVERY_FINALIZATION_INCOMPLETE")


def _external_terminalize_and_ack(checkpoint: dict[str, object]) -> None:
    owners = checkpoint.get("owners")
    if not isinstance(owners, list) or not owners:
        raise RuntimeError("EXTERNAL_RECOVERY_FINALIZATION_BINDING_INVALID")
    owner = min(owners, key=lambda item: str(item["token_sha256"]))
    if not isinstance(owner, dict):
        raise RuntimeError("EXTERNAL_RECOVERY_FINALIZATION_BINDING_INVALID")
    owner_set_sha256, owner_count, _canonical, _account, _domain = (
        _external_finalization_owner_binding(owners))
    sealed = checkpoint.get("preliminary_finalization_result")
    if not isinstance(sealed, dict):
        raise RuntimeError("EXTERNAL_RECOVERY_FINALIZATION_RECEIPT_MISSING")
    _external_validate_finalization_result(
        sealed, next(item for item in owners if item["token_sha256"] ==
                     sealed["owner_token_sha256"]), checkpoint,
        expected_state="AUDIT_SEALED")
    receipt_sha256 = sealed["finalization_receipt_sha256"]
    if checkpoint.get("phase") not in {
            "PRELIMINARY_SEALED", "TERMINAL_WITNESS_REQUIRED",
            "TERMINAL_ACKED"}:
        raise RuntimeError("EXTERNAL_RECOVERY_TERMINAL_ACK_PHASE_INVALID")
    prepare_mutation: dict[str, object] = {
        "kind": "PAPER_TERMINAL_WITNESS_PREPARE",
        "token_name": owner["token_name"],
        "token_sha256": owner["token_sha256"],
        "lease_generation": owner["lease_generation"],
        "recovery_id": checkpoint["recovery_id"],
        "finalization_id": checkpoint["finalization_id"],
        "expected_owner_set_sha256": owner_set_sha256,
        "expected_owner_count": owner_count,
        "preliminary_receipt_sha256": receipt_sha256,
    }
    if checkpoint.get("phase") == "PRELIMINARY_SEALED":
        current = checkpoint.get("pending_mutation")
        if current is None:
            checkpoint["pending_mutation"] = prepare_mutation
            _persist_external_recovery_checkpoint(checkpoint)
        elif current != prepare_mutation:
            raise RuntimeError("EXTERNAL_RECOVERY_PENDING_MUTATION_CONFLICT")
        exact_owner = {
            name: owner[name] for name in EXTERNAL_CANARY_OWNER_FIELDS}
        _external_recovery_owner_material(exact_owner)
        prepared = run([
            "/usr/bin/hepta-sessionctl", "--socket", SUPERVISOR_SOCKET,
            "paper-terminal-witness-prepare", "--token-file",
            str(owner["revoke_bearer_path"]),
            "--generation", str(owner["lease_generation"]),
            "--recovery-id", str(checkpoint["recovery_id"]),
            "--finalization-id", str(checkpoint["finalization_id"]),
            "--expected-owner-set-sha256", owner_set_sha256,
            "--expected-owner-count", str(owner_count),
            "--receipt-sha256", str(receipt_sha256),
            "--token-owner-uid", "0",
        ], timeout=120)
        try:
            prepare_result = json.loads(prepared.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "EXTERNAL_RECOVERY_TERMINAL_WITNESS_PREPARE_UNAVAILABLE") \
                from error
        pair = (
            prepared.returncode,
            prepare_result.get("accepted") if isinstance(
                prepare_result, dict) else None,
            prepare_result.get("reason_code") if isinstance(
                prepare_result, dict) else None,
        )
        if (pair not in {
                (0, True, "PAPER_TERMINAL_WITNESS_PREPARED"),
                (4, False,
                 "PAPER_TERMINAL_WITNESS_PREPARE_INTENT_PENDING")} or
                not isinstance(prepare_result, dict) or
                prepare_result.get("paper_finalization_state") !=
                    "AUDIT_SEALED" or
                prepare_result.get("paper_finalization_required") is not
                    True or
                prepare_result.get("recovery_id") !=
                    checkpoint.get("recovery_id") or
                prepare_result.get("finalization_id") !=
                    checkpoint.get("finalization_id") or
                prepare_result.get("expected_owner_set_sha256") !=
                    owner_set_sha256 or
                prepare_result.get("expected_owner_count") != owner_count or
                prepare_result.get("lease_generation") !=
                    owner.get("lease_generation") or
                prepare_result.get("owner_token_sha256") !=
                    owner.get("token_sha256") or
                prepare_result.get("finalization_receipt_sha256") !=
                    receipt_sha256):
            raise RuntimeError(
                "EXTERNAL_RECOVERY_TERMINAL_WITNESS_PREPARE_INVALID")
        checkpoint["pending_mutation"] = None
        checkpoint["phase"] = "TERMINAL_WITNESS_REQUIRED"
        _persist_external_recovery_checkpoint(checkpoint)

    try:
        evidence_raw, _evidence_metadata = _stable_file_bytes(
            EXTERNAL_TERMINAL_EVIDENCE_PATH,
            "EXTERNAL_RECOVERY_TERMINAL_EVIDENCE_INVALID",
            expected_uid=0, expected_gid=0,
            allowed_modes=frozenset({0o400}), maximum_bytes=12288)
    except FileNotFoundError as error:
        raise RuntimeError(
            "EXTERNAL_RECOVERY_TERMINAL_WITNESS_REQUIRED") from error
    evidence_sha256 = "sha256:" + hashlib.sha256(evidence_raw).hexdigest()
    mutation: dict[str, object] = {
        "kind": "PAPER_TERMINAL_WITNESS_ACK",
        "token_name": owner["token_name"],
        "token_sha256": owner["token_sha256"],
        "lease_generation": owner["lease_generation"],
        "recovery_id": checkpoint["recovery_id"],
        "finalization_id": checkpoint["finalization_id"],
        "expected_owner_set_sha256": owner_set_sha256,
        "expected_owner_count": owner_count,
        "preliminary_receipt_sha256": receipt_sha256,
        "terminal_evidence_file_sha256": evidence_sha256,
    }
    # A successful first transition is checkpointed, then immediately replayed
    # with the same HPE1 bytes. Every later resume also reopens that evidence
    # and asks the supervisor to revalidate the current DENY_ALL boundary.
    for _attempt in range(2):
        require_replay = checkpoint.get("phase") == "TERMINAL_ACKED"
        current = checkpoint.get("pending_mutation")
        if current is None:
            checkpoint["pending_mutation"] = mutation
            _persist_external_recovery_checkpoint(checkpoint)
        elif current != mutation:
            raise RuntimeError("EXTERNAL_RECOVERY_PENDING_MUTATION_CONFLICT")
        exact_owner = {
            name: owner[name] for name in EXTERNAL_CANARY_OWNER_FIELDS}
        _external_recovery_owner_material(exact_owner)
        completed = run([
            "/usr/bin/hepta-sessionctl", "--socket", SUPERVISOR_SOCKET,
            "paper-terminal-witness-ack", "--token-file",
            str(owner["revoke_bearer_path"]),
            "--generation", str(owner["lease_generation"]),
            "--recovery-id", str(checkpoint["recovery_id"]),
            "--finalization-id", str(checkpoint["finalization_id"]),
            "--expected-owner-set-sha256", owner_set_sha256,
            "--expected-owner-count", str(owner_count),
            "--receipt-sha256", str(receipt_sha256),
            "--terminal-evidence-file",
            str(EXTERNAL_TERMINAL_EVIDENCE_PATH),
            "--terminal-evidence-sha256", evidence_sha256,
            "--token-owner-uid", "0",
        ], timeout=120)
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "EXTERNAL_RECOVERY_TERMINAL_ACK_UNAVAILABLE") from error
        if completed.returncode != 0:
            raise RuntimeError("EXTERNAL_RECOVERY_TERMINAL_ACK_PENDING")
        acknowledged = _external_validate_terminal_ack_result(
            result, checkpoint,
            expected_replay=True if require_replay else None,
            terminal_evidence_raw=evidence_raw)
        prior = checkpoint.get("terminal_ack_result")
        if isinstance(prior, dict):
            for field in set(prior) - {"terminal_replay"}:
                if prior.get(field) != acknowledged.get(field):
                    raise RuntimeError(
                        "EXTERNAL_RECOVERY_TERMINAL_ACK_RECEIPT_DRIFTED")
        checkpoint["terminal_ack_result"] = dict(acknowledged)
        checkpoint["pending_mutation"] = None
        checkpoint["phase"] = "TERMINAL_ACKED"
        # Fsync the exact HSL8 result before asking the current root witness
        # boundary to replay it. A crash never converts owner absence into
        # economic proof.
        _persist_external_recovery_checkpoint(checkpoint)
        if acknowledged.get("terminal_replay") is True:
            break
    else:
        raise RuntimeError("EXTERNAL_RECOVERY_TERMINAL_REPLAY_REQUIRED")
    durable, _store_sha256 = _external_hsl7_records(
        require_paper_owner=False)
    if durable:
        raise RuntimeError("EXTERNAL_RECOVERY_SESSION_RESIDUE")


def _external_cleanup_terminal_owner_material(
        checkpoint: dict[str, object],
) -> None:
    """Delete local credentials only after immutable v4 completion exists."""
    try:
        completion_raw, _completion_metadata = _stable_file_bytes(
            EXTERNAL_RECOVERY_COMPLETION,
            "EXTERNAL_RECOVERY_COMPLETION_INVALID",
            expected_uid=0, expected_gid=0)
    except FileNotFoundError as error:
        raise RuntimeError("EXTERNAL_RECOVERY_COMPLETION_MISSING") from error
    completion = _validate_canonical_sealed_document(
        completion_raw, "EXTERNAL_RECOVERY_COMPLETION_INVALID")
    owners = checkpoint.get("owners")
    terminal_ack = checkpoint.get("terminal_ack_result")
    if (checkpoint.get("phase") not in {"TERMINAL_ACKED", "COMPLETE"} or
            not isinstance(owners, list) or not owners or
            not isinstance(terminal_ack, dict) or
            completion.get("schema") != EXTERNAL_RECOVERY_COMPLETION_SCHEMA or
            completion.get("version") != 4 or
            completion.get("status") != EXTERNAL_RECOVERY_COMPLETION_STATUS or
            completion.get("recovery_id") != checkpoint.get("recovery_id") or
            completion.get("finalization_id") !=
                checkpoint.get("finalization_id") or
            completion.get("terminal_ack_receipt_sha256") !=
                terminal_ack.get("finalization_receipt_sha256") or
            completion.get("terminal_evidence_file_sha256") !=
                terminal_ack.get("terminal_evidence_sha256") or
            completion.get(
                "terminal_current_evidence_replay_verified") is not True):
        raise RuntimeError("EXTERNAL_RECOVERY_TERMINAL_ACK_RECEIPT_MISSING")
    evidence_raw = _external_current_terminal_evidence()
    _external_validate_terminal_ack_result(
        terminal_ack, checkpoint, expected_replay=True,
        terminal_evidence_raw=evidence_raw)
    durable, _store_sha256 = _external_hsl7_records(
        require_paper_owner=False)
    if durable:
        raise RuntimeError("EXTERNAL_RECOVERY_SESSION_RESIDUE")
    for owner in sorted(owners, key=lambda item: str(item["token_sha256"])):
        assert isinstance(owner, dict)
        _external_remove_delivery_token(owner)
        _external_destroy_root_owner_material(owner, allow_absent=True)


def _external_publish_completion(
        authority: dict[str, object], authority_raw: bytes,
        checkpoint: dict[str, object],
) -> tuple[dict[str, object], bytes, dict[str, object], bytes]:
    proofs = checkpoint["zero_exposure_proofs"]
    owners = checkpoint["owners"]
    sealed = checkpoint.get("preliminary_finalization_result")
    terminal_ack = checkpoint.get("terminal_ack_result")
    if (
            checkpoint.get("phase") != "TERMINAL_ACKED" or
            not isinstance(owners, list) or not owners or
            not isinstance(sealed, dict) or
            not isinstance(terminal_ack, dict)):
        raise RuntimeError("EXTERNAL_RECOVERY_TERMINAL_ACK_RECEIPT_MISSING")
    owner_set_sha256, owner_count, _canonical, _account, _domain = (
        _external_finalization_owner_binding(owners))
    sealed_owner = next((
        owner for owner in owners if owner["token_sha256"] ==
            sealed.get("owner_token_sha256")), None)
    if sealed_owner is None:
        raise RuntimeError("EXTERNAL_RECOVERY_FINALIZATION_RECEIPT_INVALID")
    _external_validate_finalization_result(
        sealed, sealed_owner, checkpoint, expected_state="AUDIT_SEALED")
    evidence_raw = _external_current_terminal_evidence()
    _external_validate_terminal_ack_result(
        terminal_ack, checkpoint, expected_replay=True,
        terminal_evidence_raw=evidence_raw)
    if (
            sealed.get("finalization_receipt_sha256") != terminal_ack.get(
                "preliminary_finalization_receipt_sha256")):
        raise RuntimeError("EXTERNAL_RECOVERY_TERMINAL_ACK_RECEIPT_DRIFTED")
    terminal_path = _external_recovery_artifact_paths(
        str(authority["suspension_id"]))["terminal_flat"]
    terminal_completed_at_ms = max(
        int(authority["recorded_at_ms"]),
        int(checkpoint["updated_at_ms"]))
    token_sha256s = sorted(
        str(owner["token_sha256"]) for owner in owners)
    terminal, terminal_raw, terminal_metadata = (
        _publish_immutable_sealed_json(terminal_path, {
            "schema": EXTERNAL_RECOVERY_TERMINAL_FLAT_SCHEMA,
            "version": 4,
            "status": EXTERNAL_RECOVERY_TERMINAL_FLAT_STATUS,
            "completed_at_ms": terminal_completed_at_ms,
            "recovery_id": authority["recovery_id"],
            "domain": "alpha",
            "campaign_id": authority["campaign_id"],
            "suspension_id": authority["suspension_id"],
            "source_baseline_sha256": authority["source_baseline_sha256"],
            "finalization_id": checkpoint["finalization_id"],
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": owner_count,
            "preliminary_finalization_receipt_sha256": sealed[
                "finalization_receipt_sha256"],
            "preliminary_finalization_receipt": sealed[
                "finalization_receipt"],
            "preliminary_finalization_result": sealed,
            "terminal_ack_receipt_sha256": terminal_ack[
                "finalization_receipt_sha256"],
            "terminal_ack_receipt": terminal_ack["finalization_receipt"],
            "terminal_ack_result": terminal_ack,
            "terminal_latch_sha256": terminal_ack["terminal_latch_sha256"],
            "terminal_external_halt_latch_sha256": terminal_ack[
                "terminal_external_halt_latch_sha256"],
            "terminal_evidence_file_sha256": terminal_ack[
                "terminal_evidence_sha256"],
            "terminal_evidence_body_sha256": terminal_ack[
                "terminal_evidence_body_sha256"],
            "terminal_proof_kind": terminal_ack["terminal_proof_kind"],
            "session_owner_count": len(owners),
            "session_owner_token_sha256s": token_sha256s,
            "all_original_session_owners_closed": True,
            "terminal_acknowledged": True,
            "terminal_current_evidence_replay_verified": True,
            "hsl_owner_purged": True,
            "position_quantity": "0",
            "gross_absolute_position": "0",
            "active_order_count": 0,
            "pre_finalization_diagnostic_zero_exposure_proofs": proofs,
            "paper_only": True,
            "live_authorized": False,
        }, "EXTERNAL_RECOVERY_TERMINAL_FLAT_INVALID"))
    terminal_reference = _external_recovery_reference(
        terminal_path, terminal, terminal_raw, terminal_metadata)
    authority_file_sha256, authority_body_sha256 = (
        _external_recovery_control_pins(authority, authority_raw))
    completion, completion_raw, _completion_metadata = (
        _publish_immutable_sealed_json(EXTERNAL_RECOVERY_COMPLETION, {
            "schema": EXTERNAL_RECOVERY_COMPLETION_SCHEMA,
            "version": 4,
            "status": EXTERNAL_RECOVERY_COMPLETION_STATUS,
            "recovery_id": authority["recovery_id"],
            "completed_at_ms": terminal_completed_at_ms,
            "domain": "alpha",
            "campaign_id": authority["campaign_id"],
            "suspension_id": authority["suspension_id"],
            "source_baseline_sha256": authority["source_baseline_sha256"],
            "recovery_authority_file_sha256": authority_file_sha256,
            "recovery_authority_body_sha256": authority_body_sha256,
            "authoritative_flat_receipt_reference": terminal_reference,
            "finalization_id": checkpoint["finalization_id"],
            "expected_owner_set_sha256": owner_set_sha256,
            "expected_owner_count": owner_count,
            "preliminary_finalization_receipt_sha256": sealed[
                "finalization_receipt_sha256"],
            "terminal_ack_receipt_sha256": terminal_ack[
                "finalization_receipt_sha256"],
            "terminal_latch_sha256": terminal_ack["terminal_latch_sha256"],
            "terminal_external_halt_latch_sha256": terminal_ack[
                "terminal_external_halt_latch_sha256"],
            "terminal_evidence_file_sha256": terminal_ack[
                "terminal_evidence_sha256"],
            "terminal_evidence_body_sha256": terminal_ack[
                "terminal_evidence_body_sha256"],
            "terminal_proof_kind": terminal_ack["terminal_proof_kind"],
            "session_owner_count": len(owners),
            "all_original_session_owners_closed": True,
            "terminal_acknowledged": True,
            "terminal_current_evidence_replay_verified": True,
            "hsl_owner_purged": True,
            "position_quantity": "0",
            "gross_absolute_position": "0",
            "active_order_count": 0,
            "paper_only": True,
            "live_authorized": False,
        }, "EXTERNAL_RECOVERY_COMPLETION_INVALID"))
    return terminal, terminal_raw, completion, completion_raw


def _load_external_recovery_completion(
        authority: dict[str, object], authority_raw: bytes,
) -> tuple[dict[str, object], bytes] | None:
    try:
        raw, _metadata = _stable_file_bytes(
            EXTERNAL_RECOVERY_COMPLETION,
            "EXTERNAL_RECOVERY_COMPLETION_INVALID",
            expected_uid=0, expected_gid=0)
    except FileNotFoundError:
        return None
    completion = _validate_canonical_sealed_document(
        raw, "EXTERNAL_RECOVERY_COMPLETION_INVALID")
    expected_completion_fields = {
        "schema", "version", "status", "recovery_id", "completed_at_ms",
        "domain", "campaign_id", "suspension_id", "source_baseline_sha256",
        "recovery_authority_file_sha256",
        "recovery_authority_body_sha256",
        "authoritative_flat_receipt_reference", "finalization_id",
        "expected_owner_set_sha256", "expected_owner_count",
        "preliminary_finalization_receipt_sha256",
        "terminal_ack_receipt_sha256", "terminal_latch_sha256",
        "terminal_external_halt_latch_sha256",
        "terminal_evidence_file_sha256", "terminal_evidence_body_sha256",
        "terminal_proof_kind",
        "session_owner_count", "all_original_session_owners_closed",
        "terminal_acknowledged",
        "terminal_current_evidence_replay_verified",
        "hsl_owner_purged", "position_quantity",
        "gross_absolute_position", "active_order_count", "paper_only",
        "live_authorized", "body_sha256",
    }
    if (set(completion) != expected_completion_fields or
            completion.get("schema") != EXTERNAL_RECOVERY_COMPLETION_SCHEMA or
            completion.get("version") != 4 or
            completion.get("status") != EXTERNAL_RECOVERY_COMPLETION_STATUS):
        raise RuntimeError("EXTERNAL_RECOVERY_COMPLETION_INVALID")
    owners = _external_existing_checkpoint_owners(authority)
    if owners is None:
        raise RuntimeError("EXTERNAL_RECOVERY_COMPLETION_INVALID")
    checkpoint = _external_recovery_checkpoint(
        authority, authority_raw, owners)
    if checkpoint.get("phase") not in {"TERMINAL_ACKED", "COMPLETE"}:
        raise RuntimeError("EXTERNAL_RECOVERY_COMPLETION_INVALID")
    sealed = checkpoint.get("preliminary_finalization_result")
    terminal_ack = checkpoint.get("terminal_ack_result")
    if not isinstance(sealed, dict) or not isinstance(terminal_ack, dict):
        raise RuntimeError("EXTERNAL_RECOVERY_COMPLETION_INVALID")
    evidence_raw = _external_current_terminal_evidence()
    _external_validate_terminal_ack_result(
        terminal_ack, checkpoint, expected_replay=True,
        terminal_evidence_raw=evidence_raw)
    owner_set_sha256, owner_count, _canonical, _account, _domain = (
        _external_finalization_owner_binding(owners))
    authority_file_sha256, authority_body_sha256 = (
        _external_recovery_control_pins(authority, authority_raw))
    reference = completion.get("authoritative_flat_receipt_reference")
    completed_at_ms = completion.get("completed_at_ms")
    if (type(completed_at_ms) is not int or
            completed_at_ms < int(authority.get("recorded_at_ms", -1)) or
            completion.get("recovery_id") != authority.get("recovery_id") or
            completion.get("domain") != "alpha" or
            completion.get("campaign_id") != authority.get("campaign_id") or
            completion.get("suspension_id") !=
                authority.get("suspension_id") or
            completion.get("source_baseline_sha256") !=
                authority.get("source_baseline_sha256") or
            completion.get("recovery_authority_file_sha256") !=
                authority_file_sha256 or
            completion.get("recovery_authority_body_sha256") !=
                authority_body_sha256 or
            completion.get("finalization_id") !=
                checkpoint.get("finalization_id") or
            completion.get("expected_owner_set_sha256") !=
                owner_set_sha256 or
            type(completion.get("expected_owner_count")) is not int or
            completion.get("expected_owner_count") != owner_count or
            completion.get("preliminary_finalization_receipt_sha256") !=
                sealed.get("finalization_receipt_sha256") or
            completion.get("terminal_ack_receipt_sha256") !=
                terminal_ack.get("finalization_receipt_sha256") or
            completion.get("terminal_latch_sha256") !=
                terminal_ack.get("terminal_latch_sha256") or
            completion.get("terminal_external_halt_latch_sha256") !=
                terminal_ack.get("terminal_external_halt_latch_sha256") or
            completion.get("terminal_evidence_file_sha256") !=
                terminal_ack.get("terminal_evidence_sha256") or
            completion.get("terminal_evidence_body_sha256") !=
                terminal_ack.get("terminal_evidence_body_sha256") or
            completion.get("terminal_proof_kind") !=
                "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" or
            type(completion.get("session_owner_count")) is not int or
            completion.get("session_owner_count") != owner_count or
            authority.get("session_owner_count") != owner_count or
            completion.get("all_original_session_owners_closed") is not True or
            completion.get("terminal_acknowledged") is not True or
            completion.get(
                "terminal_current_evidence_replay_verified") is not True or
            completion.get("hsl_owner_purged") is not True or
            completion.get("position_quantity") != "0" or
            completion.get("gross_absolute_position") != "0" or
            type(completion.get("active_order_count")) is not int or
            completion.get("active_order_count") != 0 or
            completion.get("paper_only") is not True or
            completion.get("live_authorized") is not False or
            not isinstance(reference, dict) or
            set(reference) != {
                "path", "file_sha256", "body_sha256", "schema", "status",
                "bytes", "mode", "uid", "gid", "nlink"}):
        raise RuntimeError("EXTERNAL_RECOVERY_COMPLETION_INVALID")
    path = Path(str(reference["path"]))
    expected_path = _external_recovery_artifact_paths(
        str(authority["suspension_id"]))["terminal_flat"]
    if path != expected_path:
        raise RuntimeError("EXTERNAL_RECOVERY_COMPLETION_INVALID")
    terminal_raw, terminal_metadata = _stable_file_bytes(
        path, "EXTERNAL_RECOVERY_TERMINAL_FLAT_INVALID",
        expected_uid=0, expected_gid=0)
    terminal = _validate_canonical_sealed_document(
        terminal_raw, "EXTERNAL_RECOVERY_TERMINAL_FLAT_INVALID")
    expected_terminal_fields = {
        "schema", "version", "status", "completed_at_ms", "recovery_id",
        "domain", "campaign_id", "suspension_id", "source_baseline_sha256",
        "finalization_id", "expected_owner_set_sha256",
        "expected_owner_count",
        "preliminary_finalization_receipt_sha256",
        "preliminary_finalization_receipt",
        "preliminary_finalization_result", "terminal_ack_receipt_sha256",
        "terminal_ack_receipt", "terminal_ack_result",
        "terminal_latch_sha256", "terminal_external_halt_latch_sha256",
        "terminal_evidence_file_sha256", "terminal_evidence_body_sha256",
        "terminal_proof_kind",
        "session_owner_count", "session_owner_token_sha256s",
        "all_original_session_owners_closed",
        "terminal_acknowledged",
        "terminal_current_evidence_replay_verified",
        "hsl_owner_purged", "position_quantity",
        "gross_absolute_position", "active_order_count",
        "pre_finalization_diagnostic_zero_exposure_proofs", "paper_only",
        "live_authorized", "body_sha256",
    }
    if (_external_recovery_reference(
            path, terminal, terminal_raw, terminal_metadata) != reference or
            set(terminal) != expected_terminal_fields or
            terminal.get("schema") != EXTERNAL_RECOVERY_TERMINAL_FLAT_SCHEMA or
            terminal.get("version") != 4 or
            terminal.get("status") != EXTERNAL_RECOVERY_TERMINAL_FLAT_STATUS or
            terminal.get("completed_at_ms") != completed_at_ms or
            terminal.get("recovery_id") != authority.get("recovery_id") or
            terminal.get("domain") != "alpha" or
            terminal.get("campaign_id") != authority.get("campaign_id") or
            terminal.get("suspension_id") != authority.get("suspension_id") or
            terminal.get("source_baseline_sha256") !=
                authority.get("source_baseline_sha256") or
            terminal.get("finalization_id") !=
                checkpoint.get("finalization_id") or
            terminal.get("expected_owner_set_sha256") != owner_set_sha256 or
            type(terminal.get("expected_owner_count")) is not int or
            terminal.get("expected_owner_count") != owner_count or
            terminal.get("preliminary_finalization_receipt_sha256") !=
                sealed.get("finalization_receipt_sha256") or
            terminal.get("preliminary_finalization_receipt") !=
                sealed.get("finalization_receipt") or
            terminal.get("preliminary_finalization_result") != sealed or
            terminal.get("terminal_ack_receipt_sha256") !=
                terminal_ack.get("finalization_receipt_sha256") or
            terminal.get("terminal_ack_receipt") !=
                terminal_ack.get("finalization_receipt") or
            terminal.get("terminal_ack_result") != terminal_ack or
            terminal.get("terminal_latch_sha256") !=
                terminal_ack.get("terminal_latch_sha256") or
            terminal.get("terminal_external_halt_latch_sha256") !=
                terminal_ack.get("terminal_external_halt_latch_sha256") or
            terminal.get("terminal_evidence_file_sha256") !=
                terminal_ack.get("terminal_evidence_sha256") or
            terminal.get("terminal_evidence_body_sha256") !=
                terminal_ack.get("terminal_evidence_body_sha256") or
            terminal.get("terminal_proof_kind") !=
                "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1" or
            type(terminal.get("session_owner_count")) is not int or
            terminal.get("session_owner_count") != owner_count or
            terminal.get("session_owner_token_sha256s") != sorted(
                str(owner["token_sha256"]) for owner in owners) or
            terminal.get("all_original_session_owners_closed") is not True or
            terminal.get("terminal_acknowledged") is not True or
            terminal.get(
                "terminal_current_evidence_replay_verified") is not True or
            terminal.get("hsl_owner_purged") is not True or
            terminal.get("position_quantity") != "0" or
            terminal.get("gross_absolute_position") != "0" or
            type(terminal.get("active_order_count")) is not int or
            terminal.get("active_order_count") != 0 or
            terminal.get("pre_finalization_diagnostic_zero_exposure_proofs") !=
                checkpoint.get("zero_exposure_proofs") or
            terminal.get("paper_only") is not True or
            terminal.get("live_authorized") is not False):
        raise RuntimeError("EXTERNAL_RECOVERY_TERMINAL_FLAT_INVALID")
    return completion, raw


def _external_complete_control(
        authority: dict[str, object], authority_raw: bytes,
        completion: dict[str, object], completion_raw: bytes,
) -> dict[str, object]:
    authority_file_sha256, authority_body_sha256 = (
        _external_recovery_control_pins(authority, authority_raw))
    completion_file_sha256 = "sha256:" + hashlib.sha256(
        completion_raw).hexdigest()
    rendered = run_checked([
        LOCAL_PAPER_CONTROL, "complete-recovery", "--domain", "alpha",
        "--recovery-authority", str(EXTERNAL_RECOVERY_AUTHORITY),
        "--recovery-authority-file-sha256", authority_file_sha256,
        "--recovery-authority-body-sha256", authority_body_sha256,
        "--recovery-completion", str(EXTERNAL_RECOVERY_COMPLETION),
        "--recovery-completion-file-sha256", completion_file_sha256,
        "--recovery-completion-body-sha256",
        str(completion["body_sha256"]),
    ], timeout=120)
    try:
        result = json.loads(rendered)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "EXTERNAL_RECOVERY_CONTROL_COMPLETION_INVALID") from error
    if (not isinstance(result, dict) or result.get("mode") != "DENY_ALL" or
            result.get("paper_authorized") is not False or
            result.get("live_authorized") is not False or
            result.get("identity_count") != 0 or
            result.get("recovery_completed") is not True or
            result.get("recovery_id") != authority.get("recovery_id") or
            result.get("campaign_id") != authority.get("campaign_id") or
            result.get("suspension_id") != authority.get("suspension_id") or
            result.get("completion_file_sha256") != completion_file_sha256 or
            result.get("transaction_retained") is not False):
        raise RuntimeError("EXTERNAL_RECOVERY_CONTROL_COMPLETION_INVALID")
    _end_flat_verify_deny_all()
    for unit in EXTERNAL_RECOVERY_REQUIRED_UNITS:
        if _unit_is_active(unit):
            raise RuntimeError(
                "EXTERNAL_RECOVERY_RUNTIME_NOT_STOPPED:" + unit)
    rendered_status = run_checked([
        LOCAL_PAPER_CONTROL, "status", "--domain", "alpha",
    ], timeout=30)
    try:
        status_value = json.loads(rendered_status)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "EXTERNAL_RECOVERY_CONTROL_FINAL_STATUS_INVALID") from error
    if (not isinstance(status_value, dict) or
            status_value.get("mode") != "DENY_ALL" or
            status_value.get("paper_authorized") is not False or
            status_value.get("live_authorized") is not False or
            status_value.get("identity_count") != 0 or
            status_value.get("wal_state") != "ABSENT" or
            status_value.get("effective_state_verified") is not True):
        raise RuntimeError("EXTERNAL_RECOVERY_CONTROL_FINAL_STATUS_INVALID")
    return result


def _end_flat_checkpoint_path(campaign_id: str) -> Path:
    return END_FLAT_RECEIPT_ROOT / (
        "end-flat-" + campaign_id + ".checkpoint.json")


def _load_end_flat_checkpoint(campaign_id: str) -> dict[str, object] | None:
    path = _end_flat_checkpoint_path(campaign_id)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("END_FLAT_CHECKPOINT_PATH_UNSAFE")
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("END_FLAT_CHECKPOINT_INVALID") from error
    phases = {"RISK_ZERO_SEALED", "SESSIONS_REVOKED", "EGRESS_REVOKED"}
    sessions = value.get("sessions") if isinstance(value, dict) else None
    if (not isinstance(value, dict) or
            value.get("schema") !=
                "hepta.local-ai-paper-end-flat-checkpoint.v1" or
            value.get("campaign_id") != campaign_id or
            value.get("phase") not in phases or
            value.get("position") != 0 or
            value.get("active_orders") != 0 or
            value.get("gross_absolute_position") != 0 or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            not isinstance(sessions, list) or not sessions):
        raise RuntimeError("END_FLAT_CHECKPOINT_INVALID")
    seen_names: set[str] = set()
    for raw in sessions:
        if (not isinstance(raw, dict) or
                not isinstance(raw.get("token_name"), str) or
                not re.fullmatch(
                    r"(?:local-paper|(?:risk-recovery|end-flat)-[0-9a-f]{24})"
                    r"\.token", str(raw.get("token_name"))) or
                raw.get("token_name") in seen_names or
                not isinstance(raw.get("token_sha256"), str) or
                not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                    str(raw.get("token_sha256"))) or
                not isinstance(raw.get("lease_generation"), int) or
                isinstance(raw.get("lease_generation"), bool) or
                raw.get("lease_generation", 0) < 1 or
                not isinstance(raw.get("revoked"), bool)):
            raise RuntimeError("END_FLAT_CHECKPOINT_SESSION_INVALID")
        seen_names.add(str(raw["token_name"]))
    if (value.get("phase") in {"SESSIONS_REVOKED", "EGRESS_REVOKED"} and
            not all(raw.get("revoked") is True for raw in sessions)):
        raise RuntimeError("END_FLAT_CHECKPOINT_SESSION_INVALID")
    raw_policy = CAMPAIGN_POLICY.read_bytes()
    if value.get("campaign_policy_sha256") != (
            "sha256:" + hashlib.sha256(raw_policy).hexdigest()):
        raise RuntimeError("END_FLAT_CHECKPOINT_POLICY_DRIFTED")
    return value


def _persist_end_flat_checkpoint(value: dict[str, object]) -> None:
    value["updated_at_ms"] = time.time_ns() // 1_000_000
    _write_root_json(
        _end_flat_checkpoint_path(str(value["campaign_id"])), value)


def _revoke_checkpoint_sessions(checkpoint: dict[str, object]) -> None:
    sessions = checkpoint.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise RuntimeError("END_FLAT_CHECKPOINT_INVALID")
    for raw in sessions:
        if not isinstance(raw, dict):
            raise RuntimeError("END_FLAT_SESSION_DESCRIPTOR_INVALID")
        name = raw.get("token_name")
        if (not isinstance(name, str) or not re.fullmatch(
                r"(?:local-paper|(?:risk-recovery|end-flat)-[0-9a-f]{24})"
                r"\.token", name)):
            raise RuntimeError("END_FLAT_SESSION_DESCRIPTOR_INVALID")
        token_file = RISK_RECOVERY_TOKEN_ROOT / name
        if raw.get("revoked") is not True:
            allow_absent = raw.get("revoke_retry_intent") is True
            if not allow_absent:
                # Persist the exact digest/generation before crossing the
                # remote revoke boundary.  A retry may accept authoritative
                # LEASE_NOT_FOUND only when this intent already exists.
                raw["revoke_retry_intent"] = True
                _persist_end_flat_checkpoint(checkpoint)
            evidence = _revoke_recovery_session(
                token_file, unlink=False,
                allow_already_absent=allow_absent)
            if (evidence.get("tool_session_token_sha256") !=
                    raw.get("token_sha256") or
                    evidence.get("tool_session_lease_generation") !=
                    raw.get("lease_generation")):
                raise RuntimeError("END_FLAT_SESSION_REVOKE_DRIFTED")
            raw["revoked"] = True
            raw["already_absent"] = (
                evidence.get("tool_session_already_absent") is True)
            raw.pop("revoke_retry_intent", None)
            _persist_end_flat_checkpoint(checkpoint)
        _unlink_bound_session_files(raw)
    _validate_no_campaign_session_residue()
    checkpoint["phase"] = "SESSIONS_REVOKED"
    _persist_end_flat_checkpoint(checkpoint)


def _risk_recovery_checkpoint_path(suspension_id: str) -> Path:
    if not isinstance(suspension_id, str) or not suspension_id:
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_SUSPENSION_INVALID")
    digest = hashlib.sha256(
        suspension_id.encode("utf-8")).hexdigest()[:24]
    return END_FLAT_RECEIPT_ROOT / (
        "risk-recovery-" + digest + ".checkpoint.json")


def _risk_recovery_policy_sha256(campaign_id: str) -> str:
    """Bind post-zero recovery to the exact PAPER policy being sealed."""
    try:
        metadata = os.lstat(CAMPAIGN_POLICY)
    except FileNotFoundError as error:
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_POLICY_MISSING") \
            from error
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_POLICY_UNSAFE")
    try:
        raw = CAMPAIGN_POLICY.read_bytes()
        policy = json.loads(raw)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_POLICY_INVALID") \
            from error
    if (not isinstance(policy, dict) or
            policy.get("schema") not in RECOVERY_POLICY_SCHEMAS or
            policy.get("campaign_id") != campaign_id or
            policy.get("domain_id") != "alpha" or
            not isinstance(policy.get("enabled"), bool) or
            not isinstance(policy.get("mutations_authorized"), bool) or
            policy.get("paper_only") is not True or
            policy.get("live_authorized") is not False):
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_POLICY_INVALID")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _risk_recovery_session_descriptors_before_revoke(
) -> list[dict[str, object]]:
    """Snapshot every exact owner without crossing a remote revoke boundary."""
    descriptors: list[dict[str, object]] = []
    for token_file in _campaign_session_token_paths():
        authority = _load_session_provision_intent(token_file)
        if (isinstance(authority, dict) and authority.get("phase") in {
                "TOKEN_PENDING", "CALL_PENDING", "RENEW_PENDING"}):
            # Resolving these phases can itself revoke a generation.  They must
            # first be handled by their own durable authority transaction,
            # never while constructing the post-zero checkpoint.
            raise RuntimeError(
                "RISK_RECOVERY_SESSION_PHASE_AMBIGUOUS_BEFORE_CHECKPOINT")
        descriptor = _session_revocation_descriptor(token_file)
        descriptor["revoke_retry_intent"] = (
            authority is not None and
            authority.get("phase") == "REVOKE_PENDING")
        descriptors.append(descriptor)
    if not descriptors:
        raise RuntimeError("RISK_RECOVERY_SESSION_DESCRIPTOR_MISSING")
    return descriptors


def _validate_risk_recovery_checkpoint_sessions(
        value: dict[str, object]) -> None:
    sessions = value.get("sessions")
    selected = value.get("selected_token_name")
    if (not isinstance(sessions, list) or not sessions or
            not isinstance(selected, str)):
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_SESSION_INVALID")
    seen_names: set[str] = set()
    for raw in sessions:
        if not isinstance(raw, dict):
            raise RuntimeError("RISK_RECOVERY_CHECKPOINT_SESSION_INVALID")
        name = raw.get("token_name")
        generation = raw.get("lease_generation")
        token_sha256 = raw.get("token_sha256")
        revoked = raw.get("revoked")
        retry_intent = raw.get("revoke_retry_intent")
        already_absent = raw.get("already_absent")
        if (not isinstance(name, str) or not re.fullmatch(
                r"(?:local-paper|(?:risk-recovery|end-flat)-[0-9a-f]{24})"
                r"\.token", name) or
                name in seen_names or
                not isinstance(token_sha256, str) or
                not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                    token_sha256) or
                not isinstance(generation, int) or
                isinstance(generation, bool) or generation < 1 or
                not isinstance(revoked, bool) or
                (retry_intent is not None and
                 not isinstance(retry_intent, bool)) or
                (already_absent is not None and
                 not isinstance(already_absent, bool)) or
                (revoked and retry_intent is not None) or
                (not revoked and already_absent is not None)):
            raise RuntimeError("RISK_RECOVERY_CHECKPOINT_SESSION_INVALID")
        seen_names.add(name)
    if selected not in seen_names:
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_SESSION_INVALID")
    if (value.get("phase") == "SESSIONS_REVOKED" and
            not all(isinstance(raw, dict) and raw.get("revoked") is True
                    for raw in sessions)):
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_SESSION_INVALID")


def _load_risk_recovery_checkpoint(
        campaign_id: str, suspension_id: str,
        main_state: dict[str, object],
) -> dict[str, object] | None:
    path = _risk_recovery_checkpoint_path(suspension_id)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_PATH_UNSAFE")
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_INVALID") from error
    proof_names = (
        "first_position_generation", "first_fx_cash_generation",
        "second_position_generation", "second_fx_cash_generation",
    )
    if (not isinstance(value, dict) or
            value.get("schema") !=
                "hepta.local-ai-paper-risk-recovery-checkpoint.v1" or
            value.get("campaign_id") != campaign_id or
            value.get("campaign_id_at_suspend") != campaign_id or
            value.get("suspension_id") != suspension_id or
            value.get("phase") not in {
                "RISK_ZERO_SEALED", "SESSIONS_REVOKED"} or
            value.get("position") != 0 or
            value.get("active_orders") != 0 or
            value.get("gross_absolute_position") != 0 or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            not isinstance(value.get("retained_original_session"), bool) or
            not isinstance(value.get("halt_result"), str) or
            not isinstance(value.get("suspension_code"), str) or
            not isinstance(value.get("suspended_at_ms"), int) or
            isinstance(value.get("suspended_at_ms"), bool) or
            value.get("suspended_at_ms", 0) <= 0 or
            not isinstance(value.get("auth_generation_at_suspend"), str) or
            not value.get("auth_generation_at_suspend") or
            any(not isinstance(value.get(name), int) or
                isinstance(value.get(name), bool) or value.get(name, 0) <= 0
                for name in proof_names) or
            not isinstance(value.get("cancel_attempted_order_ids"), list) or
            not isinstance(
                value.get("terminally_reconciled_order_ids"), list) or
            not isinstance(value.get("command_reconciliation"), list) or
            value.get("campaign_policy_sha256") !=
                _risk_recovery_policy_sha256(campaign_id) or
            value.get("suspension_code") !=
                main_state.get("suspension_code") or
            value.get("suspended_at_ms") !=
                main_state.get("suspended_at_ms") or
            value.get("auth_generation_at_suspend") !=
                main_state.get("auth_generation_at_suspend") or
            value.get("campaign_id_at_suspend") !=
                main_state.get("campaign_id_at_suspend")):
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_INVALID")
    for name in (
            "incident_pending_order_id", "last_flatten_order_id"):
        raw = value.get(name)
        if (raw is not None and
                (not isinstance(raw, int) or isinstance(raw, bool) or
                 raw < 0)):
            raise RuntimeError("RISK_RECOVERY_CHECKPOINT_INVALID")
    for name in (
            "cancel_attempted_order_ids",
            "terminally_reconciled_order_ids"):
        raw_ids = value.get(name)
        assert isinstance(raw_ids, list)
        if (any(not isinstance(raw, int) or isinstance(raw, bool) or raw < 0
                for raw in raw_ids) or raw_ids != sorted(set(raw_ids))):
            raise RuntimeError("RISK_RECOVERY_CHECKPOINT_INVALID")
    raw_pnl = value.get("recovery_raw_price_pnl_evidence")
    if raw_pnl is not None:
        amount = raw_pnl.get("amount") if isinstance(raw_pnl, dict) else None
        currency = raw_pnl.get("quote_currency") \
            if isinstance(raw_pnl, dict) else None
        if (not isinstance(amount, (int, float)) or
                isinstance(amount, bool) or not math.isfinite(float(amount)) or
                not isinstance(currency, str) or not currency or
                raw_pnl.get("commission_included") is not False):
            raise RuntimeError("RISK_RECOVERY_CHECKPOINT_INVALID")
    if value.get("phase") == "SESSIONS_REVOKED":
        completed_at_ms = value.get("completed_at_ms")
        if (not isinstance(completed_at_ms, int) or
                isinstance(completed_at_ms, bool) or completed_at_ms <= 0):
            raise RuntimeError("RISK_RECOVERY_CHECKPOINT_INVALID")
    _validate_risk_recovery_checkpoint_sessions(value)
    return value


def _persist_risk_recovery_checkpoint(
        checkpoint: dict[str, object], *, create: bool = False) -> None:
    checkpoint["updated_at_ms"] = time.time_ns() // 1_000_000
    path = _risk_recovery_checkpoint_path(str(checkpoint["suspension_id"]))
    if create:
        _create_root_json_exclusive(path, checkpoint)
    else:
        _write_root_json(path, checkpoint)


def _revoke_risk_recovery_checkpoint_sessions(
        checkpoint: dict[str, object],
) -> list[dict[str, object]]:
    """Resume exact revocation while retaining bearers until durable proof."""
    _validate_risk_recovery_checkpoint_sessions(checkpoint)
    sessions = checkpoint["sessions"]
    assert isinstance(sessions, list)
    for raw in sessions:
        assert isinstance(raw, dict)
        name = str(raw["token_name"])
        token_file = RISK_RECOVERY_TOKEN_ROOT / name
        if raw.get("revoked") is not True:
            allow_absent = raw.get("revoke_retry_intent") is True
            if not allow_absent:
                # This durable intent is the sole authority to treat a later
                # exact LEASE_NOT_FOUND as evidence that the prior call crossed
                # the remote boundary before the process died.
                raw["revoke_retry_intent"] = True
                _persist_risk_recovery_checkpoint(checkpoint)
            evidence = _revoke_recovery_session(
                token_file, unlink=False,
                allow_already_absent=allow_absent)
            if (evidence.get("tool_session_token_sha256") !=
                    raw.get("token_sha256") or
                    evidence.get("tool_session_lease_generation") !=
                    raw.get("lease_generation") or
                    evidence.get("tool_session_revoked") is not True):
                raise RuntimeError("RISK_RECOVERY_SESSION_REVOKE_DRIFTED")
            raw["revoked"] = True
            raw["already_absent"] = (
                evidence.get("tool_session_already_absent") is True)
            raw.pop("revoke_retry_intent", None)
            # Preserve the root-only bearer until this exact remote result is
            # durably reflected in the checkpoint.
            _persist_risk_recovery_checkpoint(checkpoint)
        _unlink_bound_session_files(raw)
    _validate_no_campaign_session_residue()
    checkpoint["phase"] = "SESSIONS_REVOKED"
    if "completed_at_ms" not in checkpoint:
        checkpoint["completed_at_ms"] = time.time_ns() // 1_000_000
    _persist_risk_recovery_checkpoint(checkpoint)
    evidence_list: list[dict[str, object]] = []
    for raw in sessions:
        assert isinstance(raw, dict)
        evidence: dict[str, object] = {
            "token_name": raw["token_name"],
            "tool_session_revoked": True,
            "tool_session_lease_generation": raw["lease_generation"],
            "tool_session_token_sha256": raw["token_sha256"],
        }
        if raw.get("already_absent") is True:
            evidence["tool_session_already_absent"] = True
        evidence_list.append(evidence)
    return evidence_list


def _load_root_agent_state(
        agent: ModuleType, allow_safety_exit: bool = False,
) -> dict[str, object]:
    unreadable_archive: Path | None = None
    try:
        metadata = os.lstat(AGENT_STATE)
    except FileNotFoundError as error:
        if not allow_safety_exit:
            raise RuntimeError("RISK_RECOVERY_STATE_MISSING") from error
        state = agent.empty_state()
        state_mode = 0o600
    else:
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0):
            raise RuntimeError("RISK_RECOVERY_STATE_PATH_UNSAFE")
        state_mode = stat.S_IMODE(metadata.st_mode)
        try:
            state = agent.load_state(AGENT_STATE)
            _merge_repair_owned_agent_state_fields(state)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            if not allow_safety_exit:
                raise
            unreadable_archive = AGENT_STATE.with_name(
                "state.unreadable-safety-exit-" + str(int(time.time())) +
                "-" + uuid.uuid4().hex[:8] + ".json")
            _copy_root_state_snapshot(
                AGENT_STATE, unreadable_archive, metadata)
            state = agent.empty_state()
    suspension_code = state.get("suspension_code")
    acceptance_campaign = state.get("strategy_acceptance_intent_campaign_id")
    acceptance_intent = state.get("strategy_acceptance_intent_id")
    if (suspension_code in {
            "STRATEGY_ACCEPTANCE_ADMISSION_LATCHED",
            "STRATEGY_ACCEPTANCE_IN_FLIGHT",
        } and isinstance(acceptance_campaign, str) and
            isinstance(acceptance_intent, str)):
        _intent_path, acceptance_state_path, _receipt_path = (
            _strategy_acceptance_artifact_paths(acceptance_campaign))
        try:
            acceptance = _strategy_acceptance_recovery_projection(
                agent, acceptance_state_path,
                acceptance_campaign, acceptance_intent)
        except (FileNotFoundError, RuntimeError):
            # Before the immutable acceptance state is fully published no
            # broker mutation can be attempted. Once the main latch advances,
            # however, loss of that handoff is terminally uncertain.
            if suspension_code == "STRATEGY_ACCEPTANCE_IN_FLIGHT":
                state["pending_mutation_state_unproven"] = True
                _write_root_json(AGENT_STATE, state)
        else:
            state["pending_mutation_state_unproven"] = False
            for key in (
                    "entry_order_id", "entry_quantity", "entry_at_ms",
                    "entry_mid", "last_flatten_order_id",
                    "pending_order_id", "pending_order_since_ms",
                    "pending_mutation_kind", "pending_mutation_command_id",
                    "pending_mutation_recorded_at_ms",
                    "pending_mutation_token_name",
                    "pending_mutation_token_sha256"):
                value = acceptance.get(key)
                if value is not None or key not in state:
                    state[key] = value
            pending_order_id = acceptance.get("pending_order_id")
            if pending_order_id is not None:
                state["incident_pending_order_id"] = pending_order_id
            _write_root_json(AGENT_STATE, state)
    if (state.get("recovery_required") is not True and
            state.get("trading_suspended") is not True):
        if not allow_safety_exit:
            raise RuntimeError("RISK_RECOVERY_LATCH_REQUIRED")
        values = read_env()
        state["schema"] = agent.SCHEMA
        state["recovery_required"] = True
        state["trading_suspended"] = True
        state["suspension_code"] = "ORDER_STATE_UNCERTAIN"
        state["suspension_id"] = "suspension-" + uuid.uuid4().hex
        state["suspended_at_ms"] = time.time_ns() // 1_000_000
        state["auth_generation_at_suspend"] = values[
            "HEPTA_LOCAL_AI_AUTH_GENERATION"]
        state["campaign_id_at_suspend"] = values[
            "HEPTA_LOCAL_AI_CAMPAIGN_ID"]
        state["recovery_phase"] = "REQUESTED"
        state["recovery_complete"] = False
        state["recovery_reason"] = (
            "RECOVERY_REQUIRED: agent safety exit state unreadable; "
            "original archived for audit"
            if unreadable_archive is not None else
            "RECOVERY_REQUIRED: agent safety exit before latch persisted")
        try:
            agent.write_json(AGENT_STATE, state)
        except Exception:
            if unreadable_archive is not None:
                _restore_root_state_snapshot(
                    unreadable_archive, AGENT_STATE, state_mode)
            raise
        os.chown(AGENT_STATE, 0, 0)
        os.chmod(AGENT_STATE, state_mode)
    return state


def _remove_safety_exit_latch() -> None:
    for path, failure in (
            (SAFETY_LATCH, "AUTH_REARM_SAFETY_LATCH_PATH_UNSAFE"),
            (AUTOMATIC_RISK_ATTEMPT,
             "AUTH_REARM_AUTOMATIC_RISK_ATTEMPT_PATH_UNSAFE")):
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise RuntimeError(failure)
        try:
            os.unlink(path)
        except FileNotFoundError:
            # A concurrent cleanup may remove an already-validated marker.
            # The desired postcondition (marker absent) is still satisfied.
            pass


def _ensure_suspension_metadata(
        state: dict[str, object], values: dict[str, str],
) -> None:
    """Complete legacy latch metadata required by the explicit rearm gate."""
    if not isinstance(state.get("suspension_id"), str) or not state.get(
            "suspension_id"):
        state["suspension_id"] = "suspension-" + uuid.uuid4().hex
    suspended_at_ms = state.get("suspended_at_ms")
    if (not isinstance(suspended_at_ms, int) or
            isinstance(suspended_at_ms, bool) or suspended_at_ms <= 0):
        incident_at_ms = state.get("pending_order_since_ms")
        state["suspended_at_ms"] = (
            incident_at_ms
            if isinstance(incident_at_ms, int) and
            not isinstance(incident_at_ms, bool) and incident_at_ms > 0
            else time.time_ns() // 1_000_000)
    if not isinstance(state.get("suspension_code"), str) or not state.get(
            "suspension_code"):
        state["suspension_code"] = "ORDER_STATE_UNCERTAIN"
    if not isinstance(state.get("auth_generation_at_suspend"), str) or not \
            state.get("auth_generation_at_suspend"):
        state["auth_generation_at_suspend"] = values[
            "HEPTA_LOCAL_AI_AUTH_GENERATION"]
    if not isinstance(state.get("campaign_id_at_suspend"), str) or not \
            state.get("campaign_id_at_suspend"):
        state["campaign_id_at_suspend"] = values[
            "HEPTA_LOCAL_AI_CAMPAIGN_ID"]


def _verified_risk_recovery_receipt(
        state: dict[str, object]) -> tuple[Path, dict[str, object], str]:
    suspension_id = state.get("suspension_id")
    expected_hash = state.get("recovery_receipt_sha256")
    if (not isinstance(suspension_id, str) or not suspension_id or
            not isinstance(expected_hash, str) or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_hash)):
        raise RuntimeError("AUTH_REARM_RECOVERY_RECEIPT_REFERENCE_INVALID")
    digest = hashlib.sha256(
        suspension_id.encode("utf-8")).hexdigest()[:24]
    path = END_FLAT_RECEIPT_ROOT / (
        "risk-recovery-" + digest + ".receipt.json")
    metadata = os.lstat(path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("AUTH_REARM_RECOVERY_RECEIPT_PATH_UNSAFE")
    raw = path.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if expected_hash != "sha256:" + actual_hash:
        raise RuntimeError("AUTH_REARM_RECOVERY_RECEIPT_HASH_MISMATCH")
    try:
        receipt = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("AUTH_REARM_RECOVERY_RECEIPT_INVALID") from error
    if (not isinstance(receipt, dict) or
            receipt.get("schema") !=
                "hepta.local-ai-paper-risk-recovery-receipt.v1" or
            receipt.get("suspension_id") != suspension_id or
            receipt.get("position") != 0 or
            receipt.get("active_orders") != 0 or
            receipt.get("gross_absolute_position") != 0 or
            receipt.get("trading_resumed") is not False or
            receipt.get("paper_only") is not True or
            receipt.get("live_authorized") is not False):
        raise RuntimeError("AUTH_REARM_RECOVERY_RECEIPT_BOUNDARY_INVALID")
    return path, receipt, actual_hash


AUTH_CANARY_MODEL = "codex/gpt-5.3-codex-spark"
AUTH_CANARY_PROMPT = (
    "AUTH_CANARY: Do not call any tools. Reply with exactly AUTH_OK and "
    "nothing else.")
AUTH_CANARY_MODEL_API = "openai-chatgpt-responses"
AUTH_CANARY_MAX_TRAJECTORY_BYTES = 1_048_576


def _read_auth_canary_trajectory(
        trajectory: Path, failure_prefix: str,
) -> list[dict[str, object]] | None:
    """Read one bounded, owner-only trajectory without following links."""
    try:
        descriptor = os.open(
            trajectory,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RuntimeError(failure_prefix + "_TRAJECTORY_UNSAFE") from error
    try:
        metadata = os.fstat(descriptor)
        account = pwd.getpwnam("qian-qi")
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != account.pw_uid or
                metadata.st_gid != account.pw_gid or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                metadata.st_size > AUTH_CANARY_MAX_TRAJECTORY_BYTES):
            raise RuntimeError(failure_prefix + "_TRAJECTORY_UNSAFE")
        raw = bytearray()
        while len(raw) <= AUTH_CANARY_MAX_TRAJECTORY_BYTES:
            chunk = os.read(
                descriptor,
                min(65_536, AUTH_CANARY_MAX_TRAJECTORY_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > AUTH_CANARY_MAX_TRAJECTORY_BYTES:
            raise RuntimeError(failure_prefix + "_TRAJECTORY_UNSAFE")
    finally:
        os.close(descriptor)
    if not raw:
        return []
    try:
        parsed = [
            json.loads(line) for line in
            bytes(raw).decode("utf-8").splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if any(not isinstance(item, dict) for item in parsed):
        return []
    return parsed


def _trajectory_timestamp_ms(event: dict[str, object]) -> int | None:
    raw = event.get("ts")
    if not isinstance(raw, str) or not raw.endswith("Z"):
        return None
    try:
        observed = dt.datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError:
        return None
    return int(observed.timestamp() * 1000)


def _auth_canary_trajectory_ok(
        events: list[dict[str, object]], *, session_id: str,
        invoked_at_ms: int, profile_ids: tuple[str, ...],
        required_profile_id: str | None,
) -> tuple[bool, int, str | None]:
    """Validate a single exact model attempt and return its durable proof."""
    required_types = (
        "session.started", "context.compiled", "prompt.submitted",
        "model.completed", "session.ended")
    selected: list[dict[str, object]] = []
    for event_type in required_types:
        matching = [item for item in events if item.get("type") == event_type]
        if len(matching) != 1:
            return False, 0, None
        selected.append(matching[0])
    if any(item.get("type") in {"tool.call", "tool.result", "tool.error"}
           for item in events):
        return False, 0, None

    expected_session_key = (
        "agent:telegram-bot-8681289317:explicit:" + session_id)
    expected_model_id = AUTH_CANARY_MODEL.split("/", 1)[1]
    run_ids = [item.get("runId") for item in selected]
    sequences = [item.get("seq") for item in selected]
    source_sequences = [item.get("sourceSeq") for item in selected]
    timestamps = [_trajectory_timestamp_ms(item) for item in selected]
    if (any(not isinstance(value, str) or not value for value in run_ids) or
            len(set(run_ids)) != 1 or
            any(item.get("traceSchema") != "openclaw-trajectory" or
                not isinstance(item.get("schemaVersion"), int) or
                isinstance(item.get("schemaVersion"), bool) or
                item.get("schemaVersion") != 1 or
                item.get("traceId") != session_id or
                item.get("source") != "runtime" or
                item.get("sessionId") != session_id or
                item.get("sessionKey") != expected_session_key or
                item.get("provider") != "codex" or
                item.get("modelId") != expected_model_id or
                item.get("modelApi") != AUTH_CANARY_MODEL_API
                for item in selected) or
            any(not isinstance(value, int) or isinstance(value, bool)
                for value in sequences + source_sequences) or
            sequences != sorted(sequences) or
            len(set(sequences)) != len(sequences) or
            source_sequences != sorted(source_sequences) or
            len(set(source_sequences)) != len(source_sequences) or
            any(value is None for value in timestamps) or
            timestamps != sorted(timestamps) or
            timestamps[0] < invoked_at_ms):
        return False, 0, None

    started_data = selected[0].get("data")
    context_data = selected[1].get("data")
    submitted_data = selected[2].get("data")
    completed_data = selected[3].get("data")
    ended_data = selected[4].get("data")
    started_profile_id = (
        started_data.get("authProfileId")
        if isinstance(started_data, dict) else None)
    if (not isinstance(started_profile_id, str) or
            started_profile_id not in profile_ids or
            (required_profile_id is not None and
             started_profile_id != required_profile_id) or
            not isinstance(context_data, dict) or
            context_data.get("prompt") != AUTH_CANARY_PROMPT or
            context_data.get("imagesCount") != 0 or
            not isinstance(submitted_data, dict) or
            submitted_data.get("prompt") != AUTH_CANARY_PROMPT or
            submitted_data.get("imagesCount") != 0 or
            not isinstance(completed_data, dict) or
            completed_data.get("timedOut") is not False or
            completed_data.get("aborted") is not False or
            completed_data.get("promptError") is not None or
            completed_data.get("assistantTexts") != ["AUTH_OK"] or
            not isinstance(ended_data, dict) or
            ended_data.get("status") != "success" or
            ended_data.get("timedOut") is not False or
            ended_data.get("promptError") is not None):
        return False, 0, None
    assert timestamps[-1] is not None
    return True, timestamps[-1], started_profile_id


def _run_model_auth_canary(
        profile_ids: tuple[str, ...], *, required_profile_id: str | None,
        failure_prefix: str,
) -> tuple[int, str, str]:
    """Run the campaign model and bind success to its trajectory profile."""
    session_id = str(uuid.uuid4())
    trajectory = OPENCLAW_SESSION_ROOT / (session_id + ".trajectory.jsonl")
    invoked_at_ms = time.time_ns() // 1_000_000
    completed = run([
        "/usr/sbin/runuser", "-u", "qian-qi", "--",
        OPENCLAW, "agent",
        "--agent", "telegram-bot-8681289317",
        "--session-id", session_id,
        "--model", AUTH_CANARY_MODEL,
        "--thinking", "off", "--timeout", "90", "--json",
        "--message", AUTH_CANARY_PROMPT,
    ], timeout=120)
    if completed.returncode != 0:
        raise RuntimeError(failure_prefix + "_FAILED")
    deadline = time.monotonic() + 10.0
    events: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        observed = _read_auth_canary_trajectory(trajectory, failure_prefix)
        if observed is not None:
            events = observed
            if any(item.get("type") == "session.ended" for item in events):
                break
        time.sleep(0.25)
    event_counts = {
        event_type: sum(item.get("type") == event_type for item in events)
        for event_type in (
            "session.started", "context.compiled", "prompt.submitted",
            "model.completed", "session.ended")}
    if any(count != 1 for count in event_counts.values()):
        raise RuntimeError(failure_prefix + "_INCOMPLETE")
    valid, completed_at_ms, started_profile_id = (
        _auth_canary_trajectory_ok(
            events, session_id=session_id, invoked_at_ms=invoked_at_ms,
            profile_ids=profile_ids,
            required_profile_id=required_profile_id))
    if not valid or started_profile_id is None:
        raise RuntimeError(failure_prefix + "_NOT_OK")
    return (
        completed_at_ms, session_id,
        "sha256:" + hashlib.sha256(
            started_profile_id.encode("utf-8")).hexdigest())


def _probe_auth_profile(profile_id: str) -> tuple[int, str]:
    """Prove the selected profile using the exact production model path."""
    completed_at_ms, _, _ = _run_model_auth_canary(
        (profile_id,), required_profile_id=profile_id,
        failure_prefix="AUTH_REARM_PROFILE_PROBE")
    return completed_at_ms, AUTH_CANARY_MODEL


def _auth_profile_allowlist_sha256(profile_ids: list[str]) -> str:
    if (not profile_ids or len(profile_ids) != len(set(profile_ids)) or
            any(not isinstance(item, str) or
                AUTH_PROFILE_ID_PATTERN.fullmatch(item) is None
                for item in profile_ids)):
        raise RuntimeError("AUTH_REARM_PROFILE_ALLOWLIST_INVALID")
    canonical = (json.dumps(
        sorted(profile_ids), ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _verify_effective_auth_order(profile_id: str) -> tuple[tuple[str, ...], str]:
    completed = run([
        "/usr/sbin/runuser", "-u", "qian-qi", "--",
        OPENCLAW, "models", "auth", "order", "get",
        "--agent", "telegram-bot-8681289317",
        "--provider", "openai", "--json",
    ], timeout=60)
    try:
        document = json.loads(completed.stdout)
        order = document.get("order") if isinstance(document, dict) else None
        valid = (
            completed.returncode == 0 and isinstance(document, dict) and
            document.get("agentId") == "telegram-bot-8681289317" and
            document.get("provider") == "openai" and
            isinstance(order, list) and len(order) >= 1 and
            all(isinstance(item, str) and
                AUTH_PROFILE_ID_PATTERN.fullmatch(item) is not None
                for item in order) and
            len(order) == len(set(order)) and
            profile_id in order)
    except (json.JSONDecodeError, TypeError, UnicodeEncodeError, ValueError):
        valid = False
    if not valid:
        raise RuntimeError("AUTH_REARM_EFFECTIVE_PROFILE_ORDER_INVALID")
    assert isinstance(order, list)
    normalized = tuple(sorted(order))
    return normalized, _auth_profile_allowlist_sha256(list(normalized))


def _production_auth_canary(
        profile_ids: tuple[str, ...]) -> tuple[int, str, str]:
    return _run_model_auth_canary(
        profile_ids, required_profile_id=None,
        failure_prefix="AUTH_REARM_PRODUCTION_CANARY")


def _current_zero_proof(
        agent: ModuleType, arguments: argparse.Namespace,
) -> tuple[int, int]:
    if agent.active_orders(arguments, timeout=5):
        raise RuntimeError("AUTH_REARM_ACTIVE_ORDERS_NOT_ZERO")
    position, position_generation, cash_generation = authoritative_state(
        agent, arguments)
    risk = agent.tool(arguments, "risk.get_limits", timeout=5)
    if (not isinstance(risk, dict) or risk.get("source") != "IB" or
            risk.get("authoritative") is not True or
            risk.get("gross_scope") != "PAPER_BASELINE_DELTA"):
        raise RuntimeError("AUTH_REARM_CURRENT_RISK_NOT_AUTHORITATIVE")
    try:
        gross = float(risk.get("gross_absolute_position"))
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            "AUTH_REARM_CURRENT_RISK_NOT_AUTHORITATIVE") from error
    if not math.isfinite(gross):
        raise RuntimeError("AUTH_REARM_CURRENT_RISK_NOT_AUTHORITATIVE")
    if not agent._quantity_equal(position, 0.0) or gross != 0.0:
        raise RuntimeError("AUTH_REARM_CURRENT_RISK_NOT_ZERO")
    return position_generation, cash_generation


def _risk_recovery_halt_campaign(
        agent: ModuleType, arguments: argparse.Namespace,
        state: dict[str, object]) -> str:
    if state.get("recovery_halt_confirmed") is True:
        return "halt_previously_confirmed"
    try:
        response = agent.campaign(
            arguments, "halt", "risk-stop-halt-" + uuid.uuid4().hex,
            ["--reason-code", "AUTH_RATE_LIMIT_SAFETY_STOP"], timeout=5)
    except Exception as error:
        if not _campaign_policy_expired(
                arguments.campaign_id, "RISK_RECOVERY"):
            raise RuntimeError(
                "RISK_RECOVERY_HALT_UNCONFIRMED_BEFORE_EXPIRY") from error
        return ("halt_unconfirmed_after_expiry:" + str(error))[:512]
    if response.get("status") != "ok":
        if not _campaign_policy_expired(
                arguments.campaign_id, "RISK_RECOVERY"):
            raise RuntimeError(
                "RISK_RECOVERY_HALT_REJECTED_BEFORE_EXPIRY")
        return ("halt_rejected_after_expiry:" + json.dumps(
            response, sort_keys=True, separators=(",", ":")))[:512]
    return "halt_confirmed"


def _require_active_local_paper_control(failure: str) -> dict[str, object]:
    rendered = run_checked([
        LOCAL_PAPER_CONTROL, "status", "--domain", "alpha",
    ], timeout=30)
    try:
        result = json.loads(rendered)
    except json.JSONDecodeError as error:
        raise RuntimeError(failure) from error
    manifest = result.get("identity_manifest_sha256") \
        if isinstance(result, dict) else None
    if (not isinstance(result, dict) or
            result.get("mode") != "LOCAL_PAPER" or
            result.get("paper_authorized") is not True or
            result.get("live_authorized") is not False or
            result.get("identity_count") != 1 or
            not isinstance(manifest, str) or
            AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(manifest) is None):
        raise RuntimeError(failure)
    live_policy = run_checked([
        "/usr/libexec/hepta-broker-egress-policy",
        "--policy",
        "/usr/share/heptatrader/hepta-broker-network-policy-v1.json",
        "--identity-manifest",
        "/usr/share/heptatrader/hepta-service-identities-v1.json",
        "--paper-identities",
        "/etc/heptatrader/"
        "hepta-agent-trust-domain-paper-identities-v1.json",
        "--check-active",
    ], timeout=15)
    live_match = re.fullmatch(
        r"hepta_broker_egress_policy: PASS policy_sha256="
        r"(?P<sha>[0-9a-f]{64}) authorized_connectors=1 "
        r"authorized_uids=(?P<uid>[1-9][0-9]*) protected_ports=4\s*",
        live_policy)
    if live_match is None:
        raise RuntimeError(failure)
    result["broker_policy_sha256"] = "sha256:" + live_match.group("sha")
    result["authorized_connector_count"] = 1
    result["authorized_uids"] = [int(live_match.group("uid"))]
    result["protected_port_count"] = 4
    return result


def _external_recovery_control_pins(
        authority: dict[str, object], raw: bytes,
) -> tuple[str, str]:
    file_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    body_sha256 = authority.get("body_sha256")
    if (not isinstance(body_sha256, str) or
            AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                body_sha256) is None or
            authority.get("schema") !=
                EXTERNAL_RECOVERY_AUTHORITY_SCHEMA):
        raise RuntimeError("EXTERNAL_RECOVERY_AUTHORITY_INVALID")
    return file_sha256, body_sha256


def _require_external_recovery_control(
        authority: dict[str, object], raw: bytes, failure: str,
) -> dict[str, object]:
    file_sha256, _body_sha256 = _external_recovery_control_pins(
        authority, raw)
    rendered = run_checked([
        LOCAL_PAPER_CONTROL, "status", "--domain", "alpha",
    ], timeout=30)
    try:
        result = json.loads(rendered)
    except json.JSONDecodeError as error:
        raise RuntimeError(failure) from error
    if (not isinstance(result, dict) or
            result.get("mode") != "RECOVERY_PAPER" or
            result.get("paper_authorized") is not True or
            result.get("live_authorized") is not False or
            result.get("effective_state_verified") is not True or
            result.get("wal_state") != "ENABLE_RECOVERY" or
            result.get("identity_count") != 1):
        raise RuntimeError(failure)
    for unit in EXTERNAL_RECOVERY_REQUIRED_UNITS:
        if not _unit_is_active(unit):
            raise RuntimeError(failure + ":" + unit)
    for unit in EXTERNAL_RECOVERY_FORBIDDEN_UNITS:
        if _unit_is_active(unit):
            raise RuntimeError(failure + ":FORBIDDEN:" + unit)
    result["recovery_record_file_sha256"] = file_sha256
    return result


def _enable_external_recovery_runtime(
        authority: dict[str, object], raw: bytes,
) -> dict[str, object]:
    file_sha256, body_sha256 = _external_recovery_control_pins(
        authority, raw)
    command = [
        LOCAL_PAPER_CONTROL, "enable-recovery", "--domain", "alpha",
        "--recovery-authority", str(EXTERNAL_RECOVERY_AUTHORITY),
        "--recovery-authority-file-sha256", file_sha256,
        "--recovery-authority-body-sha256", body_sha256,
    ]
    # This command is deliberately assembled independently from fresh enable;
    # no policy expiry check, operator socket, Agent, or session provision is
    # part of the recovery authority transition.
    rendered = run_checked(command, timeout=120)
    try:
        result = json.loads(rendered)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "EXTERNAL_RECOVERY_CONTROL_RESPONSE_INVALID") from error
    manifest = result.get("identity_manifest_sha256") \
        if isinstance(result, dict) else None
    if (not isinstance(result, dict) or
            result.get("mode") != "RECOVERY_PAPER" or
            result.get("domain") != "alpha" or
            result.get("admission_mode") != "external-p1-recovery" or
            result.get("paper_authorized") is not True or
            result.get("live_authorized") is not False or
            result.get("entry_authorized") is not False or
            result.get("reduce_only") is not True or
            result.get("session_provision_authorized") is not False or
            result.get("campaign_id") != authority.get("campaign_id") or
            result.get("suspension_id") != authority.get("suspension_id") or
            result.get("session_owner_count") !=
                authority.get("session_owner_count") or
            result.get("recovery_record_file_sha256") != file_sha256 or
            result.get("historical_handoff_file_sha256") !=
                authority.get("watch_handoff_receipt_file_sha256") or
            result.get("transaction_retained") is not True or
            not isinstance(manifest, str) or
            AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(manifest) is None):
        raise RuntimeError("EXTERNAL_RECOVERY_CONTROL_RESPONSE_INVALID")
    _require_external_recovery_control(
        authority, raw, "EXTERNAL_RECOVERY_CONTROL_STATUS_INVALID")
    return result


def _ensure_risk_recovery_runtime(
        external_authority: tuple[dict[str, object], bytes] | None = None,
) -> None:
    external_policy = _external_policy_for_dispatch()
    policy = external_policy or {"admission_mode": "local-only"}
    if external_policy is not None:
        if external_authority is None:
            loaded = _load_external_recovery_authority(
                policy, required=True)
            assert loaded is not None
            authority, raw, _metadata = loaded
        else:
            authority, raw = external_authority
        try:
            _require_external_recovery_control(
                authority, raw,
                "EXTERNAL_RECOVERY_CONTROL_STATUS_INVALID")
            return
        except RuntimeError:
            _enable_external_recovery_runtime(authority, raw)
            return
    required = (
        "hepta-execution-ib-paper@alpha.service",
        "hepta-tool-gateway@alpha.service",
        "hepta-ib-paper-campaign-operator@alpha.socket",
    )
    if all(_unit_is_active(unit) for unit in required):
        _require_active_local_paper_control(
            "RISK_RECOVERY_CONTROL_STATUS_INVALID")
        return
    rendered = run_checked(
        _paper_control_enable_command(policy), timeout=120)
    try:
        result = json.loads(rendered)
    except json.JSONDecodeError as error:
        raise RuntimeError("RISK_RECOVERY_CONTROL_RESPONSE_INVALID") from error
    manifest = result.get("identity_manifest_sha256") \
        if isinstance(result, dict) else None
    if (not isinstance(result, dict) or
            result.get("mode") != "LOCAL_PAPER" or
            result.get("domain") != "alpha" or
            result.get("paper_authorized") is not True or
            result.get("live_authorized") is not False or
            result.get("admission_mode") != policy.get("admission_mode") or
            not isinstance(manifest, str) or
            AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(manifest) is None):
        raise RuntimeError("RISK_RECOVERY_CONTROL_RESPONSE_INVALID")
    _require_active_local_paper_control(
        "RISK_RECOVERY_CONTROL_STATUS_INVALID")
    for unit in required:
        if not _unit_is_active(unit):
            raise RuntimeError("RISK_RECOVERY_RUNTIME_UNAVAILABLE:" + unit)


def _select_risk_recovery_session(
        suspension_digest: str,
        recovery_state: dict[str, object],
        main_state: dict[str, object] | None = None) -> tuple[Path, bool]:
    for state in (main_state, recovery_state):
        if isinstance(state, dict):
            bound = _pending_mutation_token_file(
                state, "RISK_RECOVERY_PENDING_MUTATION")
            if bound is not None:
                return bound, bound == TOKEN_FILE
    if session_usable(TOKEN_FILE):
        return TOKEN_FILE, True
    recovery_token_file = RISK_RECOVERY_TOKEN_ROOT / (
        "risk-recovery-" + suspension_digest + ".token")
    if recovery_token_file.exists() or recovery_token_file.is_symlink():
        if session_usable(recovery_token_file):
            return recovery_token_file, False
        # An unusable bearer may still bind a durable supervisor generation.
        # Never archive it or provision over its lease lineage.
        raise RuntimeError("RISK_RECOVERY_SESSION_UNUSABLE_PRESERVED")
    if (recovery_state.get("pending_order_id") is not None or
            (isinstance(main_state, dict) and
             (main_state.get("pending_order_id") is not None or
              main_state.get("incident_pending_order_id") is not None))):
        raise RuntimeError("RISK_RECOVERY_SESSION_REQUIRED_FOR_PENDING_ORDER")
    provision_session(
        86_400, recovery_token_file,
        "paper-risk-recovery-" + suspension_digest)
    return recovery_token_file, False


def _risk_recovery_terminal_order_proof(
        agent: ModuleType, arguments: argparse.Namespace,
        order_ids: list[int], *, failure_prefix: str = "RISK_RECOVERY",
) -> list[int]:
    """Require durable broker-terminal evidence for every incident order."""
    targets = sorted(set(order_ids))
    if not targets:
        return []
    snapshot, projection = _owner_order_projection(
        agent, arguments, failure_prefix)
    active_values = projection["global_active_order_ids"]
    recent_values = snapshot.get("recent_orders", [])
    if not isinstance(active_values, tuple) or not isinstance(
            recent_values, list):
        raise RuntimeError(failure_prefix + "_ORDER_PROJECTION_INVALID")
    active = {
        value for value in active_values
        if isinstance(value, int) and not isinstance(value, bool)}
    if active:
        raise RuntimeError(failure_prefix + "_ORDER_STILL_ACTIVE")
    proven: list[int] = []
    for order_id in targets:
        resolved = False
        for raw in recent_values:
            if not isinstance(raw, dict) or raw.get("order_id") != order_id:
                continue
            if raw.get("terminal") is not True:
                continue
            instrument = raw.get("instrument")
            if (isinstance(instrument, str) and instrument and
                    instrument != agent.INSTRUMENT):
                raise RuntimeError(
                    failure_prefix + "_ORDER_INSTRUMENT_MISMATCH")
            normalized_status = "".join(
                character for character in str(raw.get("status", "")).lower()
                if character.isalnum())
            economic_fill = raw.get("economic_fill") is True
            if normalized_status == "filled":
                if not economic_fill:
                    raise RuntimeError(
                        failure_prefix +
                        "_FILLED_ORDER_LACKS_ECONOMIC_EVIDENCE")
                try:
                    filled = float(raw.get("filled_quantity"))
                    remaining = float(raw.get("remaining_quantity"))
                    average_price = float(raw.get("average_fill_price"))
                except (TypeError, ValueError) as error:
                    raise RuntimeError(
                        failure_prefix +
                        "_ORDER_ECONOMIC_EVIDENCE_INVALID") \
                        from error
                if (not all(math.isfinite(value) for value in (
                            filled, remaining, average_price)) or
                        filled <= 0.0 or remaining != 0.0 or
                        average_price <= 0.0):
                    raise RuntimeError(
                        failure_prefix +
                        "_ORDER_ECONOMIC_EVIDENCE_INVALID")
            elif normalized_status not in agent.TERMINAL_NON_FILL_STATUSES:
                continue
            resolved = True
            break
        if not resolved:
            raise RuntimeError(
                failure_prefix + "_ORDER_TERMINAL_EVIDENCE_MISSING:" +
                str(order_id))
        proven.append(order_id)
    return proven


def _risk_recovery_raw_price_pnl_evidence(
        agent: ModuleType, arguments: argparse.Namespace,
        main_state: dict[str, object], recovery_state: dict[str, object],
        position_before: float, retained_original_session: bool,
) -> dict[str, object] | None:
    """Return exact owner-bound fill-to-fill PnL, or None when unproven.

    Performance evidence is deliberately non-blocking: recovery's safety
    postcondition is zero exposure, not PnL availability.  An amount is
    published only when the original session can read the exact confirmed
    entry and recovery-close order ids from authoritative recent_orders.
    """
    entry_order_id = main_state.get("entry_order_id")
    close_order_id = recovery_state.get("last_flatten_order_id")
    entry_quantity = main_state.get("entry_quantity")
    entry_at_ms = main_state.get("entry_at_ms")
    incident_order_id = main_state.get("incident_pending_order_id")
    if (retained_original_session is not True or
            incident_order_id is not None or
            not isinstance(entry_order_id, int) or
            isinstance(entry_order_id, bool) or entry_order_id < 0 or
            not isinstance(close_order_id, int) or
            isinstance(close_order_id, bool) or close_order_id <= entry_order_id or
            not isinstance(entry_at_ms, int) or
            isinstance(entry_at_ms, bool) or entry_at_ms <= 0 or
            not isinstance(entry_quantity, (int, float)) or
            isinstance(entry_quantity, bool)):
        return None
    try:
        normalized_position = float(position_before)
        normalized_entry_quantity = float(entry_quantity)
        maximum_chunk = float(getattr(agent, "ORDER_QUANTITY", 25_000))
    except (TypeError, ValueError):
        return None
    if (not all(math.isfinite(value) for value in (
                normalized_position, normalized_entry_quantity,
                maximum_chunk)) or
            normalized_position == 0.0 or maximum_chunk <= 0.0 or
            abs(normalized_position) > maximum_chunk or
            not agent._quantity_equal(
                normalized_entry_quantity, normalized_position)):
        return None
    try:
        snapshot = agent.orders_snapshot(arguments, timeout=5)
    except Exception:
        return None
    if (not isinstance(snapshot, dict) or
            snapshot.get("authoritative") is not True or
            snapshot.get("active_order_ids") != [] or
            not isinstance(snapshot.get("recent_orders"), list)):
        return None
    recent_orders = snapshot["recent_orders"]
    owner_scope = snapshot.get("owner_scope")
    if (not isinstance(owner_scope, dict) or
            not isinstance(owner_scope.get("account"), str) or
            not owner_scope.get("account") or
            not isinstance(owner_scope.get("execution_domain"), str) or
            not owner_scope.get("execution_domain")):
        return None
    entry_matches = [
        value for value in recent_orders
        if isinstance(value, dict) and
        value.get("order_id") == entry_order_id]
    close_matches = [
        value for value in recent_orders
        if isinstance(value, dict) and
        value.get("order_id") == close_order_id]
    if len(entry_matches) != 1 or len(close_matches) != 1:
        return None

    def verified_fill(
            raw: dict[str, object], expected_side: str,
    ) -> dict[str, object] | None:
        side = str(raw.get("side") or "").upper()
        if side in {"BOT", "BUY"}:
            side = "BUY"
        elif side in {"SLD", "SELL"}:
            side = "SELL"
        else:
            return None
        status = "".join(
            character for character in str(raw.get("status") or "").lower()
            if character.isalnum())
        observed_at_ms = raw.get("observed_at_ms")
        service_epoch = raw.get("evidence_service_epoch")
        connection_epoch = raw.get("evidence_connection_epoch")
        broker_execution_id = raw.get("broker_execution_id")
        broker_execution_ambiguous = raw.get(
            "broker_execution_ambiguous")
        account = raw.get("account")
        execution_domain = raw.get("execution_domain")
        try:
            filled_quantity = float(raw.get("filled_quantity"))
            remaining_quantity = float(raw.get("remaining_quantity"))
            average_fill_price = float(raw.get("average_fill_price"))
            broker_execution_quantity = float(
                raw.get("broker_execution_quantity"))
            broker_execution_price = float(raw.get("broker_execution_price"))
        except (TypeError, ValueError):
            return None
        if (raw.get("terminal") is not True or
                raw.get("economic_fill") is not True or status != "filled" or
                raw.get("instrument") != agent.INSTRUMENT or
                side != expected_side or
                not all(math.isfinite(value) for value in (
                    filled_quantity, remaining_quantity,
                    average_fill_price)) or
                not agent._quantity_equal(
                    filled_quantity, abs(normalized_position)) or
                not agent._quantity_equal(remaining_quantity, 0.0) or
                average_fill_price <= 0.0 or
                not isinstance(observed_at_ms, int) or
                isinstance(observed_at_ms, bool) or observed_at_ms <= 0 or
                not isinstance(service_epoch, str) or not service_epoch or
                not isinstance(connection_epoch, int) or
                isinstance(connection_epoch, bool) or connection_epoch <= 0 or
                not isinstance(broker_execution_id, str) or
                re.fullmatch(
                    r"[A-Za-z0-9._:-]{1,256}", broker_execution_id) is None or
                broker_execution_ambiguous is not False or
                not agent._quantity_equal(
                    broker_execution_quantity, filled_quantity) or
                not agent._quantity_equal(
                    broker_execution_price, average_fill_price) or
                account != owner_scope.get("account") or
                execution_domain != owner_scope.get("execution_domain")):
            return None
        return {
            "broker_execution_id": broker_execution_id,
            "account": account,
            "execution_domain": execution_domain,
            "instrument": agent.INSTRUMENT,
            "order_id": int(raw["order_id"]),
            "side": side,
            "filled_quantity": filled_quantity,
            "average_fill_price": average_fill_price,
            "observed_at_ms": observed_at_ms,
            "evidence_service_epoch": service_epoch,
            "evidence_connection_epoch": connection_epoch,
        }

    entry_side = "BUY" if normalized_position > 0 else "SELL"
    close_side = "SELL" if normalized_position > 0 else "BUY"
    entry_fill = verified_fill(entry_matches[0], entry_side)
    close_fill = verified_fill(close_matches[0], close_side)
    if entry_fill is None or close_fill is None or int(
            entry_fill["observed_at_ms"]) > int(close_fill["observed_at_ms"]):
        return None
    amount = round(
        (float(close_fill["average_fill_price"]) -
         float(entry_fill["average_fill_price"])) * normalized_position,
        10)
    if not math.isfinite(amount):
        return None
    instrument_parts = str(agent.INSTRUMENT).split(".")
    if len(instrument_parts) != 2 or not instrument_parts[1]:
        return None
    return {
        "schema": "hepta.local-ai-paper-recovery-raw-price-pnl-evidence.v1",
        "amount": amount,
        "quote_currency": instrument_parts[1],
        "pnl_basis": "broker_average_fill_price_difference_only",
        "commission_included": False,
        "position_before": normalized_position,
        "entry_fill": entry_fill,
        "recovery_close_fill": close_fill,
    }


def _complete_risk_recovery_checkpoint(
        agent: ModuleType, main_state: dict[str, object],
        checkpoint: dict[str, object]) -> str:
    """Seal the deterministic receipt/state from a session-free checkpoint."""
    if checkpoint.get("phase") != "SESSIONS_REVOKED":
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_PHASE_INVALID")
    _validate_risk_recovery_checkpoint_sessions(checkpoint)
    sessions = checkpoint["sessions"]
    assert isinstance(sessions, list)
    selected_name = checkpoint.get("selected_token_name")
    selected = next((
        raw for raw in sessions
        if isinstance(raw, dict) and raw.get("token_name") == selected_name
    ), None)
    if not isinstance(selected, dict) or selected.get("revoked") is not True:
        raise RuntimeError("RISK_RECOVERY_SELECTED_SESSION_NOT_REVOKED")
    all_session_revocations: list[dict[str, object]] = []
    for raw in sessions:
        assert isinstance(raw, dict)
        evidence: dict[str, object] = {
            "token_name": raw["token_name"],
            "tool_session_revoked": True,
            "tool_session_lease_generation": raw["lease_generation"],
            "tool_session_token_sha256": raw["token_sha256"],
        }
        if raw.get("already_absent") is True:
            evidence["tool_session_already_absent"] = True
        all_session_revocations.append(evidence)
    checkpoint_path = _risk_recovery_checkpoint_path(
        str(checkpoint["suspension_id"]))
    try:
        checkpoint_raw = checkpoint_path.read_bytes()
        persisted_checkpoint = json.loads(checkpoint_raw)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_INVALID") from error
    if persisted_checkpoint != checkpoint:
        raise RuntimeError("RISK_RECOVERY_CHECKPOINT_NOT_DURABLE")
    checkpoint_sha256 = "sha256:" + hashlib.sha256(
        checkpoint_raw).hexdigest()
    raw_pnl_evidence = checkpoint.get("recovery_raw_price_pnl_evidence")
    raw_pnl = None if raw_pnl_evidence is None else raw_pnl_evidence["amount"]
    raw_pnl_currency = None if raw_pnl_evidence is None else \
        raw_pnl_evidence["quote_currency"]
    receipt = {
        "schema": "hepta.local-ai-paper-risk-recovery-receipt.v1",
        "campaign_id": checkpoint["campaign_id"],
        "suspension_id": checkpoint["suspension_id"],
        "suspension_code": checkpoint["suspension_code"],
        "incident_pending_order_id": checkpoint.get(
            "incident_pending_order_id"),
        "completed_at_ms": checkpoint["completed_at_ms"],
        "halt_result": checkpoint["halt_result"],
        "retained_original_session": checkpoint[
            "retained_original_session"],
        "cancel_attempted_order_ids": checkpoint[
            "cancel_attempted_order_ids"],
        "terminally_reconciled_order_ids": checkpoint[
            "terminally_reconciled_order_ids"],
        "command_reconciliation": checkpoint["command_reconciliation"],
        "position": 0,
        "active_orders": 0,
        "gross_absolute_position": 0,
        "first_position_generation": checkpoint[
            "first_position_generation"],
        "first_fx_cash_generation": checkpoint[
            "first_fx_cash_generation"],
        "second_position_generation": checkpoint[
            "second_position_generation"],
        "second_fx_cash_generation": checkpoint[
            "second_fx_cash_generation"],
        "campaign_policy_sha256": checkpoint["campaign_policy_sha256"],
        "risk_recovery_checkpoint_sha256": checkpoint_sha256,
        "recovery_raw_price_pnl": raw_pnl,
        "recovery_raw_price_pnl_quote_currency": raw_pnl_currency,
        "recovery_raw_price_pnl_commission_included": False,
        "recovery_raw_price_pnl_evidence": raw_pnl_evidence,
        "tool_session_revoked": True,
        "tool_session_token_sha256": selected["token_sha256"],
        "tool_session_lease_generation": selected["lease_generation"],
        "all_managed_sessions_revoked": True,
        "managed_session_revocations": all_session_revocations,
        "trading_resumed": False,
        "paper_only": True,
        "live_authorized": False,
    }
    receipt_path = END_FLAT_RECEIPT_ROOT / (
        "risk-recovery-" + hashlib.sha256(
            str(checkpoint["suspension_id"]).encode("utf-8")
        ).hexdigest()[:24] + ".receipt.json")
    try:
        metadata = os.lstat(receipt_path)
    except FileNotFoundError:
        _create_root_json_exclusive(receipt_path, receipt)
    else:
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise RuntimeError("RISK_RECOVERY_RECEIPT_PATH_UNSAFE")
        try:
            existing = json.loads(receipt_path.read_text(encoding="ascii"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("RISK_RECOVERY_RECEIPT_INVALID") from error
        if existing != receipt:
            raise RuntimeError("RISK_RECOVERY_RECEIPT_COLLISION")
    receipt_raw = receipt_path.read_bytes()
    receipt_sha256 = hashlib.sha256(receipt_raw).hexdigest()
    main_state["schema"] = agent.SCHEMA
    main_state["recovery_required"] = True
    main_state["trading_suspended"] = True
    main_state["recovery_phase"] = "FLAT_CONFIRMED"
    main_state["recovery_complete"] = True
    main_state["recovery_receipt_sha256"] = "sha256:" + receipt_sha256
    main_state["pending_order_id"] = None
    main_state["pending_order_since_ms"] = None
    _clear_pending_mutation_identity(main_state)
    main_state["last_order_result"] = "RISK_RECOVERY_FLAT_CONFIRMED"
    main_state["position"] = 0.0
    main_state["unrealized_pnl_estimate"] = 0.0
    main_state["unrealized_gross_pnl_estimate"] = 0.0
    if raw_pnl_evidence is not None:
        try:
            added = agent.record_broker_close(
                main_state,
                raw_pnl_evidence["recovery_close_fill"],
                recovery=True,
                entry_fill=raw_pnl_evidence["entry_fill"])
        except Exception as error:
            raise RuntimeError(
                "RISK_RECOVERY_PERFORMANCE_ACCOUNTING_INVALID") from error
        if added:
            main_state["last_exit_kind"] = "RECOVERY_BROKER_FILL"
    main_state["entry_mid"] = None
    main_state["entry_fill_price"] = None
    main_state["entry_price_basis"] = None
    main_state["entry_quantity"] = 0.0
    main_state["entry_at_ms"] = None
    main_state["entry_order_id"] = None
    main_state["last_flatten_order_id"] = checkpoint.get(
        "last_flatten_order_id")
    main_state["recovery_raw_price_pnl"] = raw_pnl
    main_state["recovery_raw_price_pnl_quote_currency"] = raw_pnl_currency
    main_state["recovery_raw_price_pnl_commission_included"] = False
    main_state["recovery_raw_price_pnl_evidence"] = raw_pnl_evidence
    main_state["active_order_ids"] = []
    main_state["gross_absolute_position"] = 0.0
    agent.write_json(AGENT_STATE, main_state)
    os.chown(AGENT_STATE, 0, 0)
    os.chmod(AGENT_STATE, 0o600)
    print(
        "RISK_RECOVERY_COMPLETE "
        f"suspension_id={checkpoint['suspension_id']} "
        "position=0 active_orders=0 gross=0 "
        f"receipt_sha256={receipt_sha256} "
        "trading_suspended=true auth_rearm_required=true",
        flush=True)
    return receipt_sha256


def _external_verify_completed_control(
        authority: dict[str, object]) -> dict[str, object]:
    _end_flat_verify_deny_all()
    rendered = run_checked([
        LOCAL_PAPER_CONTROL, "status", "--domain", "alpha",
    ], timeout=30)
    try:
        result = json.loads(rendered)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "EXTERNAL_RECOVERY_CONTROL_FINAL_STATUS_INVALID") from error
    if (not isinstance(result, dict) or result.get("mode") != "DENY_ALL" or
            result.get("paper_authorized") is not False or
            result.get("live_authorized") is not False or
            result.get("identity_count") != 0 or
            result.get("wal_state") != "ABSENT" or
            result.get("effective_state_verified") is not True or
            result.get("recovery_id") not in {
                None, authority.get("recovery_id")}):
        raise RuntimeError("EXTERNAL_RECOVERY_CONTROL_FINAL_STATUS_INVALID")
    for unit in EXTERNAL_RECOVERY_REQUIRED_UNITS:
        if _unit_is_active(unit):
            raise RuntimeError(
                "EXTERNAL_RECOVERY_RUNTIME_NOT_STOPPED:" + unit)
    return result


def _external_mark_recovery_complete(
        agent: ModuleType, main_state: dict[str, object],
        authority: dict[str, object], completion_raw: bytes,
) -> None:
    main_state["schema"] = agent.SCHEMA
    main_state["campaign_id_at_suspend"] = authority["campaign_id"]
    main_state["suspension_id"] = authority["suspension_id"]
    main_state["recovery_required"] = True
    main_state["trading_suspended"] = True
    main_state["recovery_phase"] = "EXTERNAL_P1_TERMINAL_FLAT"
    main_state["recovery_complete"] = True
    main_state["pending_order_id"] = None
    main_state["pending_order_since_ms"] = None
    _clear_pending_mutation_identity(main_state)
    main_state["position"] = 0.0
    main_state["gross_absolute_position"] = 0.0
    main_state["active_order_ids"] = []
    main_state["last_order_result"] = (
        "EXTERNAL_P1_RECOVERY_TERMINAL_FLAT")
    main_state["recovery_receipt_sha256"] = (
        "sha256:" + hashlib.sha256(completion_raw).hexdigest())
    agent.write_json(AGENT_STATE, main_state)
    os.chown(AGENT_STATE, 0, 0)
    os.chmod(AGENT_STATE, 0o600)
    print(
        "RISK_RECOVERY_COMPLETE "
        f"suspension_id={authority['suspension_id']} "
        "position=0 active_orders=0 gross=0 "
        f"receipt_sha256={hashlib.sha256(completion_raw).hexdigest()} "
        "trading_suspended=true auth_rearm_required=true "
        "external_p1=true",
        flush=True)


def _external_risk_recover_locked(
        *, safety_exit: bool, automatic: bool,
) -> None:
    """Resume only the sealed external-P1 reduce-only recovery authority."""
    del automatic  # retry authority is encoded only in the durable checkpoint
    policy, _policy_raw = _external_p1_recovery_policy()
    # Reassert the exact disabled image on every invocation, including an
    # existing-authority or completed-control resume, before any session or
    # broker recovery surface is opened.
    policy, _policy_raw = _external_reassert_disabled_policy(policy)
    _external_p1_runtime_profile()
    agent = load_agent()
    main_state = _load_root_agent_state(
        agent, allow_safety_exit=True if not safety_exit else safety_exit)

    loaded = _load_external_recovery_authority(policy)
    if loaded is not None:
        authority, authority_raw, _metadata = loaded
        completed = _load_external_recovery_completion(
            authority, authority_raw)
        if completed is not None:
            completion, completion_raw = completed
            completed_owners = _external_existing_checkpoint_owners(authority)
            if completed_owners is None:
                raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
            completed_checkpoint = _external_recovery_checkpoint(
                authority, authority_raw, completed_owners)
            if completed_checkpoint.get("phase") == "TERMINAL_ACKED":
                # The completion may have been published immediately before a
                # crash.  Revalidate the current Execution runtime's durable
                # no-connect latch before deleting any surviving bearer.
                _external_terminalize_and_ack(completed_checkpoint)
            _external_cleanup_terminal_owner_material(completed_checkpoint)
            try:
                _external_verify_completed_control(authority)
            except RuntimeError:
                _external_complete_control(
                    authority, authority_raw, completion, completion_raw)
            completed_checkpoint["phase"] = "COMPLETE"
            completed_checkpoint["pending_mutation"] = None
            _persist_external_recovery_checkpoint(completed_checkpoint)
            _external_mark_recovery_complete(
                agent, main_state, authority, completion_raw)
            return

    if loaded is None:
        records, owners, lineage = _external_canary_recovery_records(policy)
        # This complete durable owner comparison and the four immutable root
        # snapshots happen before enable-recovery or any broker-facing Tool.
        _external_validate_durable_owner_set(owners)
        authority, authority_raw, owners = (
            _external_recovery_snapshot_bundle(
                agent, policy, main_state, records, owners, lineage))
    else:
        authority, authority_raw, _metadata = loaded
        owners = _external_existing_checkpoint_owners(authority) or []
        if not owners:
            _records, owners, _lineage = (
                _external_canary_recovery_records(policy))
            _external_validate_durable_owner_set(owners)

    checkpoint = _external_recovery_checkpoint(
        authority, authority_raw, owners)
    phase = checkpoint["phase"]
    if phase in {"AUTHORITY_SEALED", "OWNERS_RECOVERY_ONLY"}:
        _ensure_risk_recovery_runtime((authority, authority_raw))
        # recovery-query is the sole transition of each exact historic owner
        # into recoveryOnly.  All owner command ids are queried before Tools.
        _external_recovery_fence_all_owners(checkpoint)
        contexts = _external_materialize_owner_contexts(checkpoint)
        _external_resume_pending_before_tools(agent, contexts, checkpoint)
        _external_cancel_all_owned_orders(
            agent, contexts, checkpoint, authority)
        _external_flatten_position(
            agent, contexts, checkpoint, authority)
        # These observations are diagnostic preconditions only.  The later
        # post-fence composite supervisor receipt is the sole terminal-flat
        # authority.
        _external_require_historic_commands_settled(checkpoint)
        first = _external_zero_exposure_proof(agent, contexts, 1)
        time.sleep(2.0)
        second = _external_zero_exposure_proof(agent, contexts, 2)
        proofs = checkpoint["zero_exposure_proofs"]
        assert isinstance(proofs, list)
        proofs[:] = [first, second]
        checkpoint["phase"] = "RISK_ZERO_SEALED"
        checkpoint["pending_mutation"] = None
        _persist_external_recovery_checkpoint(checkpoint)
        phase = "RISK_ZERO_SEALED"

    if phase == "RISK_ZERO_SEALED":
        _ensure_risk_recovery_runtime((authority, authority_raw))
        finalized = checkpoint.get("preliminary_owner_token_sha256s")
        pending = checkpoint.get("pending_mutation")
        if not isinstance(finalized, list):
            raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")
        final_contexts = None
        if not finalized and pending is None:
            final_contexts = _external_materialize_owner_contexts(checkpoint)
        _external_finalize_all_owners(
            checkpoint, agent=agent, contexts=final_contexts)
        phase = str(checkpoint["phase"])

    if phase == "PRELIMINARY_SEALED":
        _external_terminalize_and_ack(checkpoint)
        phase = str(checkpoint["phase"])
    elif phase == "TERMINAL_ACKED":
        # Resume only by revalidating the current durable Execution terminal
        # latch through the same exact remote binding.  Bare owner/session
        # absence is never terminal evidence.
        _external_terminalize_and_ack(checkpoint)

    if phase == "TERMINAL_ACKED":
        completed = _load_external_recovery_completion(
            authority, authority_raw)
        if completed is None:
            _terminal, _terminal_raw, completion, completion_raw = (
                _external_publish_completion(
                    authority, authority_raw, checkpoint))
        else:
            completion, completion_raw = completed
        _external_cleanup_terminal_owner_material(checkpoint)
        try:
            _external_verify_completed_control(authority)
        except RuntimeError:
            _external_complete_control(
                authority, authority_raw, completion, completion_raw)
        checkpoint["phase"] = "COMPLETE"
        checkpoint["pending_mutation"] = None
        _persist_external_recovery_checkpoint(checkpoint)
        _external_mark_recovery_complete(
            agent, main_state, authority, completion_raw)
        return

    if phase == "COMPLETE":
        completed = _load_external_recovery_completion(
            authority, authority_raw)
        if completed is None:
            raise RuntimeError("EXTERNAL_RECOVERY_COMPLETION_MISSING")
        _completion, completion_raw = completed
        _external_verify_completed_control(authority)
        _external_mark_recovery_complete(
            agent, main_state, authority, completion_raw)
        return
    raise RuntimeError("EXTERNAL_RECOVERY_CHECKPOINT_INVALID")


def risk_recover(
        safety_exit: bool = False, automatic: bool = False) -> None:
    """Reduce an auth/rate-limit incident to proven zero risk and stay off."""
    # All callers, including the recurring guard, enter through the same lock
    # order as admission, renewal, strategy acceptance, and end-flat.
    with _campaign_lifecycle_locks():
        _risk_recover_locked(
            safety_exit=safety_exit, automatic=automatic)


def _risk_recover_locked(
        safety_exit: bool = False, automatic: bool = False) -> None:
    policy = _external_policy_for_dispatch()
    if policy is not None:
        _external_risk_recover_locked(
            safety_exit=safety_exit, automatic=automatic)
        return
    try:
        run_checked([
            "/usr/bin/systemctl", "stop",
            SESSION_RENEW_TIMER,
            SESSION_RENEW_SERVICE,
            SUPERVISOR_TIMER,
            SUPERVISOR_SERVICE,
            AGENT_SERVICE,
        ], timeout=30)
        _ensure_risk_recovery_runtime()
        agent = load_agent()
        main_state = _load_root_agent_state(
            agent, allow_safety_exit=safety_exit)
        values = read_env()
        suspension_fields = (
            "suspension_id", "suspended_at_ms", "suspension_code",
            "auth_generation_at_suspend", "campaign_id_at_suspend",
        )
        suspension_before = {
            name: main_state.get(name) for name in suspension_fields}
        _ensure_suspension_metadata(main_state, values)
        if any(main_state.get(name) != suspension_before[name]
               for name in suspension_fields):
            # Legacy latch completion is itself part of the checkpoint key.
            # Publish it before any broker observation so a crash cannot mint a
            # different suspension identity on the next retry.
            agent.write_json(AGENT_STATE, main_state)
            os.chown(AGENT_STATE, 0, 0)
            os.chmod(AGENT_STATE, 0o600)
        suspension_id = str(main_state["suspension_id"])
        suspension_digest = hashlib.sha256(
            suspension_id.encode("utf-8")).hexdigest()[:24]
        recovery_checkpoint = _load_risk_recovery_checkpoint(
            values["HEPTA_LOCAL_AI_CAMPAIGN_ID"], suspension_id,
            main_state)
        if recovery_checkpoint is not None:
            # The checkpoint is published only after terminal order proof and
            # two authoritative 0/0/0 observations.  Resume from it without a
            # delivery token, session query, or fresh broker mutation.
            if recovery_checkpoint.get("phase") == "RISK_ZERO_SEALED":
                _revoke_risk_recovery_checkpoint_sessions(
                    recovery_checkpoint)
            _complete_risk_recovery_checkpoint(
                agent, main_state, recovery_checkpoint)
            return
        if main_state.get("pending_mutation_state_unproven") is True:
            raise RuntimeError(
                "RISK_RECOVERY_MUTATION_IDENTITY_UNPROVEN")
        incident_order_id = main_state.get("incident_pending_order_id")
        if incident_order_id is None:
            incident_order_id = main_state.get("pending_order_id")
        recovery_state_path = END_FLAT_RECEIPT_ROOT / (
            "risk-recovery-" + suspension_digest + ".state.json")
        new_recovery_state = not (
            recovery_state_path.exists() or recovery_state_path.is_symlink())
        if not new_recovery_state:
            recovery_state = agent.load_state(recovery_state_path)
            _require_risk_recovery_state_binding(
                recovery_state_path, recovery_state,
                values["HEPTA_LOCAL_AI_CAMPAIGN_ID"], "RISK_RECOVERY")
        else:
            recovery_state = agent.empty_state()
            recovery_state["schema"] = agent.SCHEMA
            recovery_state["campaign_id_at_suspend"] = values[
                "HEPTA_LOCAL_AI_CAMPAIGN_ID"]
            recovery_state["suspension_id"] = suspension_id
            _require_risk_recovery_state_binding(
                recovery_state_path, recovery_state,
                values["HEPTA_LOCAL_AI_CAMPAIGN_ID"], "RISK_RECOVERY")
        (command_reconciliation, command_terminal_by_owner,
         accepted_command_records) = _reconcile_pending_mutation_records(
            agent,
            [(AGENT_STATE, main_state),
             (recovery_state_path, recovery_state)],
            "RISK_RECOVERY")
        # For a new state, keep this binding in memory until the Agent's
        # pre-dispatch mutation write publishes the whole projection atomically.
        # An already-flat recovery creates no otherwise-empty scan artifact.
        accepted_order_ids = sorted({
            order_id for _path, _state, order_id in
            accepted_command_records})
        if incident_order_id is None and len(accepted_order_ids) == 1:
            incident_order_id = accepted_order_ids[0]
        elif accepted_order_ids and (
                not isinstance(incident_order_id, int) or
                isinstance(incident_order_id, bool) or
                any(order_id != incident_order_id
                    for order_id in accepted_order_ids)):
            raise RuntimeError("RISK_RECOVERY_COMMAND_ORDER_ID_CONFLICT")
        # Never overwrite the only credential that can own/cancel an
        # unresolved order. A separate suspension-specific recovery session
        # is reused across retries and is never truncated while uncertain.
        recovery_token_file, retained_original_session = (
            _select_risk_recovery_session(
                suspension_digest, recovery_state, main_state))
        active_session_owners: set[str] = set()
        for token_file in _campaign_session_token_paths():
            authority = _load_session_provision_intent(token_file)
            if (isinstance(authority, dict) and
                    authority.get("phase") == "ACTIVE" and
                    session_usable(token_file)):
                active_session_owners.add(token_file.name)
        other_active_session_owners = (
            active_session_owners - {recovery_token_file.name})
        if other_active_session_owners:
            _ensure_end_flat_request_marker(
                values["HEPTA_LOCAL_AI_CAMPAIGN_ID"])
            raise RuntimeError(
                "RISK_RECOVERY_MULTIPLE_SESSION_OWNERS_REQUIRE_END_FLAT")
        arguments = agent_arguments(
            recovery_state_path, recovery_token_file)
        foreign_command_owners = (
            set(command_terminal_by_owner) - {recovery_token_file.name})
        if foreign_command_owners:
            # Never observe/cancel a command through another session's filtered
            # projection. Forced end-flat enumerates every owner independently.
            _ensure_end_flat_request_marker(
                values["HEPTA_LOCAL_AI_CAMPAIGN_ID"])
            raise RuntimeError(
                "RISK_RECOVERY_MULTIPLE_COMMAND_OWNERS_REQUIRE_END_FLAT")
        halt_result = _risk_recovery_halt_campaign(
            agent, arguments, main_state)
        recovery_contexts = {
            recovery_token_file.name: (recovery_token_file, arguments)}
        active_set, owned_by_token = _managed_owner_order_projection(
            agent, recovery_contexts, "RISK_RECOVERY")
        active = sorted(active_set)
        if owned_by_token[recovery_token_file.name] != active_set:
            raise RuntimeError(
                "RISK_RECOVERY_UNMANAGED_ACTIVE_ORDER_PRESENT")
        if active and not retained_original_session:
            raise RuntimeError(
                "RISK_RECOVERY_ORIGINAL_SESSION_REQUIRED_FOR_ACTIVE_ORDER")
        cancel_attempted: list[int] = []
        automatic_attempt_consumed = False

        def consume_automatic_attempt(mutation: str) -> None:
            nonlocal automatic_attempt_consumed
            if not automatic or automatic_attempt_consumed:
                return
            _consume_automatic_risk_recovery_attempt(main_state, mutation)
            automatic_attempt_consumed = True

        initially_active = list(active)
        deadline = time.monotonic() + 30.0
        retry_at = time.monotonic() + 15.0
        retried = False
        for order_id in active:
            consume_automatic_attempt("cancel")
            call_id = "risk-cancel-" + hashlib.sha256(
                f"{suspension_id}:{order_id}".encode("utf-8")
            ).hexdigest()[:32]
            try:
                agent.tool_response(
                    arguments, "trade.cancel_order",
                    {"order_id": order_id}, call_id, timeout=5)
            except Exception:
                # The response may be lost after broker acceptance. Reconcile
                # through authoritative reads before one bounded retry.
                pass
            cancel_attempted.append(order_id)
        while active:
            if time.monotonic() >= deadline:
                raise RuntimeError("RISK_RECOVERY_ACTIVE_ORDERS_UNRESOLVED")
            time.sleep(1.0)
            active_set, owned_by_token = _managed_owner_order_projection(
                agent, recovery_contexts, "RISK_RECOVERY")
            if owned_by_token[recovery_token_file.name] != active_set:
                raise RuntimeError(
                    "RISK_RECOVERY_UNMANAGED_ACTIVE_ORDER_PRESENT")
            active = sorted(active_set)
            if active and not retried and time.monotonic() >= retry_at:
                retried = True
                for order_id in active:
                    consume_automatic_attempt("cancel")
                    call_id = "risk-cancel-" + hashlib.sha256(
                        f"{suspension_id}:{order_id}".encode("utf-8")
                    ).hexdigest()[:32]
                    try:
                        agent.tool_response(
                            arguments, "trade.cancel_order",
                            {"order_id": order_id}, call_id, timeout=5)
                    except Exception:
                        pass
        terminal_targets = list(initially_active)
        terminal_targets.extend(sorted(
            command_terminal_by_owner.get(recovery_token_file.name, set())))
        if (isinstance(incident_order_id, int) and
                not isinstance(incident_order_id, bool) and
                incident_order_id >= 0 and
                incident_order_id not in terminal_targets):
            terminal_targets.append(incident_order_id)
        terminally_reconciled = _risk_recovery_terminal_order_proof(
            agent, arguments, terminal_targets)
        _clear_terminal_pending_mutation_records(
            accepted_command_records,
            {recovery_token_file.name: set(terminally_reconciled)})
        position, _, _ = authoritative_state(agent, arguments)
        recovery_position_before = position
        while not agent._quantity_equal(position, 0.0):
            consume_automatic_attempt("flatten")
            after = agent.flatten(arguments, recovery_state)
            if abs(after) >= abs(position) and not agent._quantity_equal(
                    after, 0.0):
                raise RuntimeError("RISK_RECOVERY_DID_NOT_REDUCE_EXPOSURE")
            position = after
        first_position_generation, first_cash_generation = (
            _end_flat_authoritative_proof(agent, arguments))
        time.sleep(2.0)
        second_position_generation, second_cash_generation = (
            _end_flat_authoritative_proof(agent, arguments))
        try:
            raw_pnl_evidence = _risk_recovery_raw_price_pnl_evidence(
                agent, arguments, main_state, recovery_state,
                recovery_position_before, retained_original_session)
        except Exception:
            # Accounting evidence must never weaken or delay the already-flat
            # recovery postcondition.  Any malformed/absent projection is
            # represented as unavailable rather than as a fabricated zero.
            raw_pnl_evidence = None
        session_descriptors = (
            _risk_recovery_session_descriptors_before_revoke())
        if recovery_token_file.name not in {
                str(raw.get("token_name")) for raw in session_descriptors}:
            raise RuntimeError(
                "RISK_RECOVERY_SELECTED_SESSION_DESCRIPTOR_MISSING")
        recovery_checkpoint = {
            "schema":
                "hepta.local-ai-paper-risk-recovery-checkpoint.v1",
            "campaign_id": values["HEPTA_LOCAL_AI_CAMPAIGN_ID"],
            "campaign_id_at_suspend": main_state[
                "campaign_id_at_suspend"],
            "suspension_id": suspension_id,
            "suspension_code": main_state["suspension_code"],
            "suspended_at_ms": main_state["suspended_at_ms"],
            "auth_generation_at_suspend": main_state[
                "auth_generation_at_suspend"],
            "phase": "RISK_ZERO_SEALED",
            "halt_result": halt_result,
            "incident_pending_order_id": incident_order_id,
            "retained_original_session": retained_original_session,
            "selected_token_name": recovery_token_file.name,
            "cancel_attempted_order_ids": sorted(set(cancel_attempted)),
            "terminally_reconciled_order_ids": sorted(set(
                terminally_reconciled)),
            "command_reconciliation": command_reconciliation,
            "last_flatten_order_id": recovery_state.get(
                "last_flatten_order_id"),
            "position": 0,
            "active_orders": 0,
            "gross_absolute_position": 0,
            "first_position_generation": first_position_generation,
            "first_fx_cash_generation": first_cash_generation,
            "second_position_generation": second_position_generation,
            "second_fx_cash_generation": second_cash_generation,
            "campaign_policy_sha256": _risk_recovery_policy_sha256(
                values["HEPTA_LOCAL_AI_CAMPAIGN_ID"]),
            "recovery_raw_price_pnl_evidence": raw_pnl_evidence,
            "sessions": session_descriptors,
            "paper_only": True,
            "live_authorized": False,
        }
        # This is the irreversible handoff: only after the two broker proofs
        # and the exact owner generations are durable may the first authority
        # revoke begin.
        _persist_risk_recovery_checkpoint(
            recovery_checkpoint, create=True)
        _revoke_risk_recovery_checkpoint_sessions(recovery_checkpoint)
        _complete_risk_recovery_checkpoint(
            agent, main_state, recovery_checkpoint)
    finally:
        # Lock release is owned by _CampaignLifecycleLocks so every recovery
        # exit, including BaseException paths, unwinds in reverse order.
        pass


def _unit_properties(unit: str, *names: str) -> dict[str, str]:
    completed = run([
        "/usr/bin/systemctl", "show", unit,
        *[value for name in names for value in ("--property", name)],
        "--no-pager",
    ], timeout=15)
    if completed.returncode != 0:
        raise RuntimeError("CAMPAIGN_UNIT_STATE_UNAVAILABLE:" + unit)
    result: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in names or key in result:
            raise RuntimeError("CAMPAIGN_UNIT_STATE_INVALID:" + unit)
        result[key] = value
    if set(result) != set(names):
        raise RuntimeError("CAMPAIGN_UNIT_STATE_INVALID:" + unit)
    return result


def _unit_is_active(unit: str) -> bool:
    return run([
        "/usr/bin/systemctl", "is-active", unit,
    ], timeout=10).returncode == 0


def _capture_campaign_timer_states() -> dict[str, dict[str, bool]]:
    result: dict[str, dict[str, bool]] = {}
    for unit in CAMPAIGN_TIMER_UNITS:
        enabled = run([
            "/usr/bin/systemctl", "is-enabled", unit,
        ], timeout=10)
        result[unit] = {
            "enabled": enabled.returncode == 0,
            "active": _unit_is_active(unit),
        }
    return result


def _restore_campaign_timer_states(
        snapshots: dict[str, dict[str, bool]],
) -> None:
    for unit in CAMPAIGN_TIMER_UNITS:
        snapshot = snapshots.get(unit)
        if not isinstance(snapshot, dict):
            raise RuntimeError("CAMPAIGN_TIMER_SNAPSHOT_INVALID")
        if snapshot.get("active") is True:
            run_checked(["/usr/bin/systemctl", "start", unit], timeout=30)
        else:
            run_checked(["/usr/bin/systemctl", "stop", unit], timeout=30)
        operation = "enable" if snapshot.get("enabled") is True else "disable"
        run_checked(["/usr/bin/systemctl", operation, unit], timeout=30)
        if _unit_is_active(unit) != (snapshot.get("active") is True):
            raise RuntimeError("CAMPAIGN_TIMER_ROLLBACK_INVALID:" + unit)


def _verify_waiting_timer(unit: str) -> dict[str, str]:
    state = _unit_properties(
        unit, "LoadState", "ActiveState", "SubState",
        "NextElapseUSecRealtime", "NextElapseUSecMonotonic",
        "UnitFileState", "Job")
    # The campaign deadline is an OnCalendar timer and therefore exposes a
    # realtime next-elapse value.  The recurring recovery/renewal/supervisor
    # timers deliberately use OnBootSec/OnActiveSec/OnUnitInactiveSec and
    # expose only a monotonic next-elapse value.  Requiring realtime for every
    # timer made rearm fail on a correctly-installed host (systemd leaves the
    # realtime field empty for monotonic timers).
    if unit == PERSISTENT_STOP_TIMER:
        deadline_property = "NextElapseUSecRealtime"
    elif unit in MONOTONIC_CAMPAIGN_TIMER_UNITS:
        deadline_property = "NextElapseUSecMonotonic"
    else:
        raise RuntimeError("CAMPAIGN_TIMER_NOT_WAITING:" + unit)
    if (state.get("LoadState") != "loaded" or
            state.get("ActiveState") != "active" or
            state.get("SubState") != "waiting" or
            state.get("UnitFileState") != "enabled" or
            state.get("Job") != "" or
            state.get(deadline_property) in {None, "", "0", "n/a"}):
        raise RuntimeError("CAMPAIGN_TIMER_NOT_WAITING:" + unit)
    return state


def _verify_deadline_timer(policy_expires_at_ms: int) -> None:
    metadata = os.lstat(PERSISTENT_STOP_TIMER_PATH)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        raise RuntimeError("CAMPAIGN_DEADLINE_TIMER_PATH_UNSAFE")
    matches = [
        line.partition("=")[2]
        for line in PERSISTENT_STOP_TIMER_PATH.read_text(
            encoding="ascii").splitlines()
        if line.startswith("OnCalendar=")]
    if len(matches) != 1:
        raise RuntimeError("CAMPAIGN_DEADLINE_TIMER_INVALID")
    try:
        deadline = dt.datetime.strptime(
            matches[0], "%Y-%m-%d %H:%M:%S UTC").replace(
                tzinfo=dt.timezone.utc)
    except ValueError as error:
        raise RuntimeError("CAMPAIGN_DEADLINE_TIMER_INVALID") from error
    if int(deadline.timestamp()) * 1000 != policy_expires_at_ms:
        raise RuntimeError("CAMPAIGN_DEADLINE_TIMER_MISMATCH")
    _verify_waiting_timer(PERSISTENT_STOP_TIMER)


def _load_manual_start_state(agent: ModuleType) -> dict[str, object]:
    metadata = os.lstat(AGENT_STATE)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("CAMPAIGN_START_STATE_PATH_UNSAFE")
    state = agent.load_state(AGENT_STATE)
    _merge_repair_owned_agent_state_fields(state)
    if (state.get("recovery_required") is not False or
            state.get("trading_suspended") is not False or
            state.get("pending_order_id") is not None or
            state.get("manual_start_required") is not True or
            not isinstance(state.get("runtime_binding"), dict)):
        raise RuntimeError("CAMPAIGN_START_STATE_NOT_REARMED")
    return state


def _verified_auth_rearm_receipt(
        state: dict[str, object], values: dict[str, str],
        policy: dict[str, object],
) -> dict[str, object]:
    expected_hash = state.get("auth_rearm_receipt_sha256")
    if (not isinstance(expected_hash, str) or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_hash)):
        raise RuntimeError("CAMPAIGN_START_REARM_RECEIPT_REFERENCE_INVALID")
    matches: list[dict[str, object]] = []
    for path in END_FLAT_RECEIPT_ROOT.glob("auth-rearm-*.receipt.json"):
        metadata = os.lstat(path)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise RuntimeError("CAMPAIGN_START_REARM_RECEIPT_PATH_UNSAFE")
        raw = path.read_bytes()
        if "sha256:" + hashlib.sha256(raw).hexdigest() != expected_hash:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError("CAMPAIGN_START_REARM_RECEIPT_INVALID") from error
        if isinstance(value, dict):
            matches.append(value)
    if len(matches) != 1:
        raise RuntimeError("CAMPAIGN_START_REARM_RECEIPT_NOT_UNIQUE")
    receipt = matches[0]
    profile_sha256 = "sha256:" + hashlib.sha256(
        values["HEPTA_LOCAL_AI_AUTH_PROFILE_ID"].encode("utf-8")).hexdigest()
    if (receipt.get("schema") !=
            "hepta.local-ai-paper-auth-rearm-receipt.v1" or
            receipt.get("new_campaign_id") != policy.get("campaign_id") or
            receipt.get("new_auth_generation") !=
                values["HEPTA_LOCAL_AI_AUTH_GENERATION"] or
            receipt.get("auth_profile_sha256") != profile_sha256 or
            receipt.get("auth_profile_allowlist_sha256") !=
                values.get(AUTH_PROFILE_ALLOWLIST_ENV) or
            receipt.get("rearm_stack_receipt_sha256") !=
                state.get("rearm_stack_receipt_sha256") or
            receipt.get("runtime_binding") != state.get("runtime_binding") or
            receipt.get("position") != 0 or
            receipt.get("active_orders") != 0 or
            receipt.get("gross_absolute_position") != 0 or
            receipt.get("manual_start_required") is not True or
            receipt.get("paper_only") is not True or
            receipt.get("live_authorized") is not False):
        raise RuntimeError("CAMPAIGN_START_REARM_RECEIPT_INVALID")
    return receipt


def _verified_strategy_acceptance(
        policy: dict[str, object], state: dict[str, object],
        auth_rearm_receipt: dict[str, object],
) -> dict[str, object]:
    campaign_id = policy.get("campaign_id")
    if not isinstance(campaign_id, str):
        raise RuntimeError("CAMPAIGN_START_STRATEGY_ACCEPTANCE_INVALID")
    intent_path, state_path, receipt_path = (
        _strategy_acceptance_artifact_paths(campaign_id))
    intent = _load_strategy_acceptance_intent(campaign_id)
    metadata = os.lstat(STRATEGY_ACCEPTANCE_STATE)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            not 256 <= metadata.st_size <= 262_144):
        raise RuntimeError("CAMPAIGN_START_STRATEGY_ACCEPTANCE_PATH_UNSAFE")
    raw = STRATEGY_ACCEPTANCE_STATE.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "CAMPAIGN_START_STRATEGY_ACCEPTANCE_INVALID") from error
    if (not isinstance(value, dict) or
            (json.dumps(
                value, ensure_ascii=True, sort_keys=True,
                separators=(",", ":"), allow_nan=False) + "\n").encode(
                    "ascii") != raw):
        raise RuntimeError("CAMPAIGN_START_STRATEGY_ACCEPTANCE_INVALID")
    state_metadata = os.lstat(state_path)
    receipt_metadata = os.lstat(receipt_path)
    if (not stat.S_ISREG(state_metadata.st_mode) or
            state_metadata.st_nlink != 1 or state_metadata.st_uid != 0 or
            state_metadata.st_gid != 0 or
            stat.S_IMODE(state_metadata.st_mode) != 0o600 or
            not 256 <= state_metadata.st_size <= 262_144 or
            not stat.S_ISREG(receipt_metadata.st_mode) or
            receipt_metadata.st_nlink != 1 or receipt_metadata.st_uid != 0 or
            receipt_metadata.st_gid != 0 or
            stat.S_IMODE(receipt_metadata.st_mode) != 0o600 or
            not 256 <= receipt_metadata.st_size <= 16_384):
        raise RuntimeError("CAMPAIGN_START_STRATEGY_ACCEPTANCE_PATH_UNSAFE")
    immutable_state_raw = state_path.read_bytes()
    receipt_raw = receipt_path.read_bytes()
    try:
        immutable_state = json.loads(immutable_state_raw)
        receipt = json.loads(receipt_raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "CAMPAIGN_START_STRATEGY_ACCEPTANCE_INVALID") from error
    receipt_keys = {
        "schema", "intent_id", "campaign_id", "completed_at_ms",
        "runtime_binding", "state_sha256", "policy_max_cycles",
        "acceptance_cycle_consumed", "strategy_cycle_budget",
        "campaign_cycles_opened", "campaign_cycles_closed",
        "acceptance_performance_included", "position", "active_orders",
        "gross_absolute_position", "paper_only", "live_authorized",
    }
    if (not isinstance(immutable_state, dict) or immutable_state != value or
            not isinstance(receipt, dict) or set(receipt) != receipt_keys or
            (json.dumps(
                receipt, ensure_ascii=True, sort_keys=True,
                separators=(",", ":"), allow_nan=False) + "\n").encode(
                    "ascii") != receipt_raw or
            receipt.get("schema") != STRATEGY_ACCEPTANCE_RECEIPT_SCHEMA or
            receipt.get("intent_id") != intent.get("intent_id") or
            receipt.get("campaign_id") != campaign_id or
            receipt.get("runtime_binding") != state.get("runtime_binding") or
            receipt.get("state_sha256") != "sha256:" + hashlib.sha256(
                immutable_state_raw).hexdigest() or
            receipt.get("policy_max_cycles") != policy.get("max_cycles") or
            receipt.get("acceptance_cycle_consumed") != 1 or
            receipt.get("strategy_cycle_budget") !=
                int(policy.get("max_cycles", 0)) - 1 or
            receipt.get("campaign_cycles_opened") != 1 or
            receipt.get("campaign_cycles_closed") != 1 or
            receipt.get("acceptance_performance_included") is not False or
            receipt.get("position") != 0 or
            receipt.get("active_orders") != 0 or
            receipt.get("gross_absolute_position") != 0 or
            receipt.get("paper_only") is not True or
            receipt.get("live_authorized") is not False or
            intent_path.name !=
                "strategy-acceptance-" + campaign_id + ".intent.json"):
        raise RuntimeError("CAMPAIGN_START_STRATEGY_ACCEPTANCE_INVALID")
    trigger = value.get("last_exit_trigger")
    completed_at_ms = value.get("strategy_acceptance_completed_at_ms")
    auth_completed_at_ms = auth_rearm_receipt.get("completed_at_ms")
    valid_after_ms = policy.get("valid_after_ms")
    now_ms = time.time_ns() // 1_000_000
    position_after = trigger.get("position_after") \
        if isinstance(trigger, dict) else None
    if (value.get("schema") != "hepta.local-ai-paper-agent-state.v3" or
            value.get("entries") != 1 or value.get("exits") != 1 or
            value.get("last_order_result") !=
                "ECONOMIC_FLATTEN_CONFIRMED" or
            value.get("recovery_required") is not False or
            value.get("trading_suspended") is not False or
            value.get("pending_order_id") is not None or
            not isinstance(trigger, dict) or
            trigger.get("trigger") != "MODEL_REVERSAL" or
            trigger.get("result") != "ECONOMIC_FLATTEN_CONFIRMED" or
            not isinstance(position_after, (int, float)) or
            isinstance(position_after, bool) or
            float(position_after) != 0.0 or
            value.get("strategy_acceptance_campaign_id") !=
                policy.get("campaign_id") or
            value.get("strategy_acceptance_intent_id") !=
                intent.get("intent_id") or
            value.get("strategy_acceptance_cycle_consumed") != 1 or
            value.get("strategy_cycle_budget") !=
                int(policy.get("max_cycles", 0)) - 1 or
            value.get("strategy_acceptance_campaign_cycles_opened") != 1 or
            value.get("strategy_acceptance_campaign_cycles_closed") != 1 or
            value.get("strategy_acceptance_performance_included") is not False or
            value.get("strategy_acceptance_runtime_binding") !=
                state.get("runtime_binding") or
            not isinstance(completed_at_ms, int) or
            isinstance(completed_at_ms, bool) or
            not isinstance(auth_completed_at_ms, int) or
            isinstance(auth_completed_at_ms, bool) or
            not isinstance(valid_after_ms, int) or
            isinstance(valid_after_ms, bool) or
            not max(valid_after_ms, auth_completed_at_ms) <=
                completed_at_ms <= now_ms or
            any(not isinstance(value.get(key), int) or
                isinstance(value.get(key), bool) or value.get(key, -1) < 0
                for key in (
                    "strategy_acceptance_position_generation",
                    "strategy_acceptance_fx_cash_generation")) or
            value.get("strategy_acceptance_gross_absolute_position") != 0 or
            value.get("strategy_acceptance_paper_only") is not True or
            value.get("strategy_acceptance_live_authorized") is not False or
            receipt.get("completed_at_ms") != completed_at_ms):
        raise RuntimeError("CAMPAIGN_START_STRATEGY_ACCEPTANCE_INVALID")
    return value


def _validated_prepared_campaign() -> tuple[
        dict[str, str], dict[str, object]]:
    policy_metadata = os.lstat(CAMPAIGN_POLICY)
    if (not stat.S_ISREG(policy_metadata.st_mode) or
            policy_metadata.st_nlink != 1 or policy_metadata.st_uid != 0 or
            policy_metadata.st_gid != 0 or
            stat.S_IMODE(policy_metadata.st_mode) & 0o022):
        raise RuntimeError("CAMPAIGN_START_POLICY_PATH_UNSAFE")
    policy = json.loads(CAMPAIGN_POLICY.read_text(encoding="ascii"))
    now = time.time_ns() // 1_000_000
    local_only = isinstance(policy, dict) and policy.get(
        "admission_mode") == "local-only"
    external_p1 = isinstance(policy, dict) and policy.get(
        "admission_mode") == "external-p1-finalized"
    if (not isinstance(policy, dict) or
            policy.get("schema") != ACTIVE_POLICY_SCHEMA or
            policy.get("version") != 5 or
            policy.get("domain_id") != "alpha" or
            policy.get("paper_only") is not True or
            policy.get("live_authorized") is not False or
            not (local_only or external_p1) or
            policy.get("enabled") is not True or
            policy.get("mutations_authorized") is not True or
            policy.get("order_type") != ("MKT" if local_only else "LMT") or
            policy.get("tif") != "DAY" or
            policy.get("allowed_instruments") != ["EUR.USD"] or
            policy.get("max_quantity") != (25_000 if local_only else 1) or
            policy.get("max_active_orders") != 1 or
            policy.get("end_flat_required") is not True or
            not isinstance(policy.get("max_cycles"), int) or
            isinstance(policy.get("max_cycles"), bool) or
            not ((2 <= policy.get("max_cycles", 0) <= ACTIVE_POLICY_MAX_CYCLES)
                 if local_only else policy.get("max_cycles") == 1) or
            not isinstance(policy.get("valid_after_ms"), int) or
            isinstance(policy.get("valid_after_ms"), bool) or
            policy.get("valid_after_ms", now + 1) > now or
            not isinstance(policy.get("expires_at_ms"), int) or
            isinstance(policy.get("expires_at_ms"), bool) or
            policy.get("expires_at_ms", 0) <= now or
            policy.get("expires_at_ms", 0) -
                policy.get("valid_after_ms", 0) >
                ACTIVE_POLICY_MAX_DURATION_MS or
            (external_p1 and
             policy.get("expires_at_ms", 0) -
                policy.get("valid_after_ms", 0) !=
                EXTERNAL_P1_POLICY_DURATION_MS) or
            any(not isinstance(policy.get(field), str) or
                AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                    str(policy.get(field))) is None or
                policy.get(field) == "sha256:" + "0" * 64
                for field in (
                    "source_baseline_sha256", "strategy_sha256",
                    "deployment_evidence_file_sha256",
                    "deployment_evidence_body_sha256")) or
            not isinstance(
                policy.get("deployment_install_transaction_id"), str) or
            not policy.get("deployment_install_transaction_id")):
        raise RuntimeError("CAMPAIGN_START_POLICY_BOUNDARY_INVALID")
    if external_p1:
        if (
                policy.get("watch_handoff_receipt_path") !=
                    "/var/lib/hepta/p1-admission/"
                    "p1-watch-to-paper-handoff-receipt-v2.json" or
                any(
                    not isinstance(policy.get(field), str) or
                    AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                        str(policy.get(field))) is None or
                    policy.get(field) == "sha256:" + "0" * 64
                    for field in (
                        "watch_handoff_receipt_file_sha256",
                        "watch_handoff_receipt_body_sha256",
                        "p1_audit_receipt_file_sha256",
                        "p1_audit_receipt_body_sha256"))):
            raise RuntimeError("CAMPAIGN_START_EXTERNAL_P1_BOUNDARY_INVALID")
    values = read_env()
    if (values.get("HEPTA_LOCAL_AI_CAMPAIGN_ID") !=
            policy.get("campaign_id") or
            values.get("HEPTA_LOCAL_AI_STRATEGY_ID") !=
                policy.get("strategy_id") or
            values.get("HEPTA_LOCAL_AI_STRATEGY_VERSION") !=
                str(policy.get("strategy_version")) or
            values.get("HEPTA_LOCAL_AI_STRATEGY_SHA256") !=
                policy.get("strategy_sha256")):
        raise RuntimeError("CAMPAIGN_START_ENV_POLICY_MISMATCH")
    _verify_deadline_timer(int(policy["expires_at_ms"]))
    return values, policy


def _validate_campaign_start_boundary(
        *, require_session_authority: bool = True,
        require_strategy_acceptance: bool = True) -> tuple[
        ModuleType, dict[str, str], dict[str, object], dict[str, object]]:
    if _unit_is_active(AGENT_SERVICE):
        raise RuntimeError("CAMPAIGN_START_AGENT_ALREADY_ACTIVE")
    values, policy = _validated_prepared_campaign()
    if policy.get("admission_mode") == "external-p1-finalized":
        raise RuntimeError("CAMPAIGN_START_EXTERNAL_P1_MANUAL_START_FORBIDDEN")
    agent = load_agent()
    state = _load_manual_start_state(agent)
    allowlist = values.get(AUTH_PROFILE_ALLOWLIST_ENV)
    profile_sha256 = "sha256:" + hashlib.sha256(
        values["HEPTA_LOCAL_AI_AUTH_PROFILE_ID"].encode("utf-8")).hexdigest()
    if (state.get("auth_generation_rearmed") !=
            values["HEPTA_LOCAL_AI_AUTH_GENERATION"] or
            state.get("auth_profile_sha256_rearmed") != profile_sha256 or
            not isinstance(allowlist, str) or
            state.get("auth_profile_allowlist_sha256_rearmed") != allowlist):
        raise RuntimeError("CAMPAIGN_START_AUTH_BINDING_INVALID")
    _verified_rearm_stack_receipt(
        state, values,
        require_active_authority=require_session_authority)
    auth_rearm_receipt = _verified_auth_rearm_receipt(state, values, policy)
    if require_strategy_acceptance:
        _verified_strategy_acceptance(policy, state, auth_rearm_receipt)
    if require_session_authority:
        _require_only_primary_session_authority()
    for marker in (SAFETY_LATCH, AUTOMATIC_RISK_ATTEMPT):
        if marker.exists() or marker.is_symlink():
            raise RuntimeError("CAMPAIGN_START_SAFETY_MARKER_PRESENT")
    return agent, values, policy, state


def _fresh_prelaunch_zero_proof(
        agent: ModuleType, values: dict[str, str],
        state: dict[str, object],
) -> tuple[int, int, int, int]:
    arguments = agent_arguments(
        AGENT_STATE,
        auth_profile_allowlist_sha256=values.get(
            AUTH_PROFILE_ALLOWLIST_ENV, ""))
    first_position, first_cash = _current_zero_proof(agent, arguments)
    first_binding = agent.current_runtime_binding(arguments)
    if first_binding != state.get("runtime_binding"):
        raise RuntimeError("CAMPAIGN_START_RUNTIME_BINDING_DRIFTED")
    time.sleep(2.0)
    second_position, second_cash = _current_zero_proof(agent, arguments)
    second_binding = agent.current_runtime_binding(arguments)
    if second_binding != first_binding:
        raise RuntimeError("CAMPAIGN_START_RUNTIME_BINDING_DRIFTED")
    return first_position, first_cash, second_position, second_cash


def _secure_file_sha256(
        path: Path, failure: str, *, allowed_modes: frozenset[int],
) -> str:
    metadata = os.lstat(path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) not in allowed_modes):
        raise RuntimeError(failure)
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _boot_id() -> str:
    value = BOOT_ID_PATH.read_text(encoding="ascii").strip()
    if re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{12}", value) is None:
        raise RuntimeError("CAMPAIGN_START_BOOT_ID_INVALID")
    return value


def _verify_start_dependencies() -> None:
    units = (
        "hepta-tool-gateway@alpha.service",
        "hepta-execution-ib-paper@alpha.service",
        "hepta-ib-paper-campaign-operator@alpha.socket",
    )
    for unit in units:
        state = _unit_properties(
            unit, "LoadState", "ActiveState", "SubState", "Job")
        if (state.get("LoadState") != "loaded" or
                state.get("ActiveState") != "active" or
                state.get("Job") != ""):
            raise RuntimeError("CAMPAIGN_START_DEPENDENCY_NOT_READY:" + unit)


def _start_boundary_hashes() -> dict[str, str]:
    return {
        "policy_sha256": _secure_file_sha256(
            CAMPAIGN_POLICY, "CAMPAIGN_START_POLICY_PATH_UNSAFE",
            allowed_modes=frozenset({0o600, 0o640, 0o644})),
        "agent_env_sha256": _secure_file_sha256(
            AGENT_ENV, "CAMPAIGN_START_ENV_PATH_UNSAFE",
            allowed_modes=frozenset({0o600, 0o640, 0o644})),
        "state_sha256": _secure_file_sha256(
            AGENT_STATE, "CAMPAIGN_START_STATE_PATH_UNSAFE",
            allowed_modes=frozenset({0o600})),
        "deadline_timer_sha256": _secure_file_sha256(
            PERSISTENT_STOP_TIMER_PATH,
            "CAMPAIGN_DEADLINE_TIMER_PATH_UNSAFE",
            allowed_modes=frozenset({0o600, 0o640, 0o644})),
        "strategy_acceptance_sha256": _secure_file_sha256(
            STRATEGY_ACCEPTANCE_STATE,
            "CAMPAIGN_START_STRATEGY_ACCEPTANCE_PATH_UNSAFE",
            allowed_modes=frozenset({0o600})),
    }


def _start_permit_paths_absent() -> None:
    for path in (
            START_PERMIT_PENDING, START_PERMIT_CLAIMED,
            START_PERMIT_CONSUMED):
        if path.exists() or path.is_symlink():
            raise RuntimeError("CAMPAIGN_START_PERMIT_RESIDUE:" + path.name)


def _terminal_orphan_start_permit(campaign_id: str) -> bool:
    """Revalidate orphan evidence while holding all campaign locks."""
    observed: list[tuple[Path, dict[str, object]]] = []
    for path in (
            START_PERMIT_PENDING, START_PERMIT_CLAIMED,
            START_PERMIT_CONSUMED):
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                not 0 <= metadata.st_size <= 16_384):
            raise RuntimeError("ORPHAN_START_PERMIT_PATH_UNSAFE")
        try:
            value = json.loads(path.read_bytes())
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return True
        issued_at_ms = value.get("issued_at_ms") \
            if isinstance(value, dict) else None
        not_after_ms = value.get("not_after_ms") \
            if isinstance(value, dict) else None
        if (not isinstance(value, dict) or
                value.get("schema") !=
                    "hepta.local-ai-paper-start-permit.v1" or
                value.get("campaign_id") != campaign_id or
                not isinstance(value.get("permit_id"), str) or
                re.fullmatch(
                    r"[0-9a-f]{64}", str(value.get("permit_id"))) is None or
                not isinstance(value.get("boot_id"), str) or
                not isinstance(issued_at_ms, int) or
                isinstance(issued_at_ms, bool) or
                not isinstance(not_after_ms, int) or
                isinstance(not_after_ms, bool) or
                not 0 < not_after_ms - issued_at_ms <= 30_000 or
                value.get("paper_only") is not True or
                value.get("live_authorized") is not False):
            return True
        observed.append((path, value))
    if not observed:
        return False
    paths = {path for path, _value in observed}
    if START_PERMIT_PENDING in paths and len(paths) != 1:
        return True
    permit_ids = {str(value["permit_id"]) for _path, value in observed}
    if len(permit_ids) != 1:
        return True
    if (START_PERMIT_CLAIMED in paths or
            START_PERMIT_CONSUMED in paths):
        # ExecCondition has crossed the process boundary.  The lifecycle lock
        # ensures a successful launcher has already made the agent active;
        # an inactive agent here therefore requires a fresh campaign.
        return True
    now = time.time_ns() // 1_000_000
    boot_id = _boot_id()
    return any(
        value.get("boot_id") != boot_id or
        int(value["issued_at_ms"]) > now or
        int(value["not_after_ms"]) < now
        for _path, value in observed)


def _write_prelaunch_zero_receipt(
        values: dict[str, str], policy: dict[str, object],
        state: dict[str, object], proofs: tuple[int, int, int, int],
) -> tuple[Path, str]:
    hashes = _start_boundary_hashes()
    path = END_FLAT_RECEIPT_ROOT / (
        "prelaunch-zero-" + values["HEPTA_LOCAL_AI_CAMPAIGN_ID"] +
        ".receipt.json")
    value = {
        "schema": "hepta.local-ai-paper-prelaunch-zero-receipt.v1",
        "campaign_id": values["HEPTA_LOCAL_AI_CAMPAIGN_ID"],
        "completed_at_ms": time.time_ns() // 1_000_000,
        "runtime_binding": state["runtime_binding"],
        "first_position_generation": proofs[0],
        "first_fx_cash_generation": proofs[1],
        "second_position_generation": proofs[2],
        "second_fx_cash_generation": proofs[3],
        "policy_expires_at_ms": policy["expires_at_ms"],
        **hashes,
        "position": 0,
        "active_orders": 0,
        "gross_absolute_position": 0,
        "paper_only": True,
        "live_authorized": False,
    }
    _write_root_json(path, value)
    return path, "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _publish_start_permit(
        values: dict[str, str], policy: dict[str, object],
        state: dict[str, object], prelaunch_receipt_sha256: str,
) -> dict[str, object]:
    _start_permit_paths_absent()
    hashes = _start_boundary_hashes()
    now_ms = time.time_ns() // 1_000_000
    permit = {
        "schema": "hepta.local-ai-paper-start-permit.v1",
        "permit_id": secrets.token_hex(32),
        "unit": AGENT_SERVICE,
        "boot_id": _boot_id(),
        "issued_at_ms": now_ms,
        "not_after_ms": now_ms + 30_000,
        "campaign_id": values["HEPTA_LOCAL_AI_CAMPAIGN_ID"],
        **hashes,
        "auth_rearm_receipt_sha256": state["auth_rearm_receipt_sha256"],
        "prelaunch_zero_receipt_sha256": prelaunch_receipt_sha256,
        "runtime_binding": state["runtime_binding"],
        "policy_expires_at_ms": policy["expires_at_ms"],
        "manual_start_required": True,
        "paper_only": True,
        "live_authorized": False,
    }
    _create_root_json_exclusive(START_PERMIT_PENDING, permit)
    return permit


def _load_start_permit(path: Path) -> dict[str, object]:
    metadata = os.lstat(path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            not 256 <= metadata.st_size <= 16_384):
        raise RuntimeError("CAMPAIGN_START_PERMIT_PATH_UNSAFE")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("CAMPAIGN_START_PERMIT_INVALID") from error
    if (not isinstance(value, dict) or
            (json.dumps(
                value, ensure_ascii=True, sort_keys=True,
                separators=(",", ":"), allow_nan=False) + "\n").encode(
                    "ascii") != raw):
        raise RuntimeError("CAMPAIGN_START_PERMIT_INVALID")
    expected = {
        "schema", "permit_id", "unit", "boot_id", "issued_at_ms",
        "not_after_ms", "campaign_id", "policy_sha256",
        "agent_env_sha256", "state_sha256", "deadline_timer_sha256",
        "strategy_acceptance_sha256",
        "auth_rearm_receipt_sha256", "prelaunch_zero_receipt_sha256",
        "runtime_binding", "policy_expires_at_ms",
        "manual_start_required", "paper_only", "live_authorized",
    }
    now_ms = time.time_ns() // 1_000_000
    if (set(value) != expected or
            value.get("schema") != "hepta.local-ai-paper-start-permit.v1" or
            not isinstance(value.get("permit_id"), str) or
            re.fullmatch(r"[0-9a-f]{64}", str(value.get("permit_id"))) is None or
            value.get("unit") != AGENT_SERVICE or
            value.get("boot_id") != _boot_id() or
            not isinstance(value.get("issued_at_ms"), int) or
            isinstance(value.get("issued_at_ms"), bool) or
            not isinstance(value.get("not_after_ms"), int) or
            isinstance(value.get("not_after_ms"), bool) or
            not value.get("issued_at_ms", now_ms + 1) <= now_ms <=
                value.get("not_after_ms", now_ms - 1) or
            value.get("not_after_ms", 0) -
                value.get("issued_at_ms", 0) > 30_000 or
            value.get("manual_start_required") is not True or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False or
            not isinstance(value.get("runtime_binding"), dict)):
        raise RuntimeError("CAMPAIGN_START_PERMIT_INVALID")
    for key in (
            "policy_sha256", "agent_env_sha256", "state_sha256",
            "deadline_timer_sha256", "strategy_acceptance_sha256",
            "auth_rearm_receipt_sha256",
            "prelaunch_zero_receipt_sha256"):
        if (not isinstance(value.get(key), str) or
                not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                    str(value.get(key)))):
            raise RuntimeError("CAMPAIGN_START_PERMIT_INVALID")
    return value


def _verified_prelaunch_zero_receipt(
        permit: dict[str, object], policy: dict[str, object],
        state: dict[str, object], hashes: dict[str, str],
) -> dict[str, object]:
    matches: list[dict[str, object]] = []
    expected_keys = {
        "schema", "campaign_id", "completed_at_ms", "runtime_binding",
        "first_position_generation", "first_fx_cash_generation",
        "second_position_generation", "second_fx_cash_generation",
        "policy_expires_at_ms", "policy_sha256", "agent_env_sha256",
        "state_sha256", "deadline_timer_sha256",
        "strategy_acceptance_sha256", "position",
        "active_orders", "gross_absolute_position", "paper_only",
        "live_authorized",
    }
    for path in END_FLAT_RECEIPT_ROOT.glob(
            "prelaunch-zero-*.receipt.json"):
        metadata = os.lstat(path)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                not 256 <= metadata.st_size <= 16_384):
            raise RuntimeError("CAMPAIGN_START_PRELAUNCH_RECEIPT_PATH_UNSAFE")
        raw = path.read_bytes()
        if ("sha256:" + hashlib.sha256(raw).hexdigest() !=
                permit.get("prelaunch_zero_receipt_sha256")):
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "CAMPAIGN_START_PRELAUNCH_RECEIPT_INVALID") from error
        if (not isinstance(value, dict) or set(value) != expected_keys or
                (json.dumps(
                    value, ensure_ascii=True, sort_keys=True,
                    separators=(",", ":"), allow_nan=False) + "\n").encode(
                        "ascii") != raw):
            raise RuntimeError("CAMPAIGN_START_PRELAUNCH_RECEIPT_INVALID")
        matches.append(value)
    if len(matches) != 1:
        raise RuntimeError("CAMPAIGN_START_PRELAUNCH_RECEIPT_INVALID")
    receipt = matches[0]
    completed_at_ms = receipt.get("completed_at_ms")
    issued_at_ms = permit.get("issued_at_ms")
    generations = (
        receipt.get("first_position_generation"),
        receipt.get("first_fx_cash_generation"),
        receipt.get("second_position_generation"),
        receipt.get("second_fx_cash_generation"),
    )
    if (receipt.get("schema") !=
            "hepta.local-ai-paper-prelaunch-zero-receipt.v1" or
            receipt.get("campaign_id") != permit.get("campaign_id") or
            receipt.get("runtime_binding") != state.get("runtime_binding") or
            receipt.get("policy_expires_at_ms") != policy.get("expires_at_ms") or
            any(receipt.get(key) != value for key, value in hashes.items()) or
            not isinstance(completed_at_ms, int) or
            isinstance(completed_at_ms, bool) or
            not isinstance(issued_at_ms, int) or
            isinstance(issued_at_ms, bool) or
            not issued_at_ms - 30_000 <= completed_at_ms <= issued_at_ms or
            any(not isinstance(value, int) or isinstance(value, bool) or
                value < 0 for value in generations) or
            receipt.get("position") != 0 or
            receipt.get("active_orders") != 0 or
            receipt.get("gross_absolute_position") != 0 or
            receipt.get("paper_only") is not True or
            receipt.get("live_authorized") is not False):
        raise RuntimeError("CAMPAIGN_START_PRELAUNCH_RECEIPT_INVALID")
    return receipt


def pre_start_guard() -> None:
    permit = _load_start_permit(START_PERMIT_PENDING)
    # The agent unit deliberately makes the durable revocation bearer
    # inaccessible.  The root-only launcher validated that live authority
    # immediately before publishing this one-shot, boot-bound permit; the
    # ExecCondition therefore revalidates the receipt and all boundary hashes
    # without reopening the bearer that ExecStart must never be able to read.
    agent, values, policy, state = _validate_campaign_start_boundary(
        require_session_authority=False)
    if policy.get("admission_mode") == "external-p1-finalized":
        raise RuntimeError("CAMPAIGN_START_EXTERNAL_P1_MANUAL_START_FORBIDDEN")
    del agent
    for unit in CAMPAIGN_TIMER_UNITS:
        _verify_waiting_timer(unit)
    _verify_deadline_timer(int(policy["expires_at_ms"]))
    _verify_start_dependencies()
    hashes = _start_boundary_hashes()
    if (permit.get("campaign_id") !=
            values["HEPTA_LOCAL_AI_CAMPAIGN_ID"] or
            permit.get("runtime_binding") != state.get("runtime_binding") or
            permit.get("policy_expires_at_ms") != policy.get("expires_at_ms") or
            any(permit.get(key) != value for key, value in hashes.items()) or
            permit.get("auth_rearm_receipt_sha256") !=
                state.get("auth_rearm_receipt_sha256")):
        raise RuntimeError("CAMPAIGN_START_PERMIT_BOUNDARY_DRIFTED")
    _verified_prelaunch_zero_receipt(permit, policy, state, hashes)
    invocation_id = os.environ.get("INVOCATION_ID", "")
    if re.fullmatch(r"[0-9a-f]{32}", invocation_id) is None:
        raise RuntimeError("CAMPAIGN_START_INVOCATION_ID_INVALID")
    _rename_root_file_noreplace(
        START_PERMIT_PENDING, START_PERMIT_CLAIMED)
    consumed = {
        **permit,
        "phase": "CONSUMED",
        "invocation_id": invocation_id,
        "consumed_at_ms": time.time_ns() // 1_000_000,
    }
    _create_root_json_exclusive(START_PERMIT_CONSUMED, consumed)
    print(
        "CAMPAIGN_START_PERMIT_CONSUMED "
        f"permit_id={permit['permit_id']} campaign_id={permit['campaign_id']}",
        flush=True)


def _remove_start_permit_file(path: Path, permit_id: str) -> None:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except FileNotFoundError:
        return
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("CAMPAIGN_START_PERMIT_CLEANUP_INVALID") from error
    metadata = os.lstat(path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            not isinstance(value, dict) or
            value.get("permit_id") != permit_id):
        raise RuntimeError("CAMPAIGN_START_PERMIT_CLEANUP_INVALID")
    os.unlink(path)
    directory = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _verified_consumed_start_permit(
        permit: dict[str, object],
) -> dict[str, object]:
    claimed = _load_start_permit(START_PERMIT_CLAIMED)
    if claimed != permit:
        raise RuntimeError("CAMPAIGN_START_CLAIMED_PERMIT_DRIFTED")
    metadata = os.lstat(START_PERMIT_CONSUMED)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("CAMPAIGN_START_CONSUMED_PERMIT_PATH_UNSAFE")
    try:
        consumed = json.loads(
            START_PERMIT_CONSUMED.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("CAMPAIGN_START_CONSUMED_PERMIT_INVALID") \
            from error
    if (not isinstance(consumed, dict) or
            consumed.get("phase") != "CONSUMED" or
            re.fullmatch(
                r"[0-9a-f]{32}", str(consumed.get("invocation_id"))) is None or
            not isinstance(consumed.get("consumed_at_ms"), int) or
            isinstance(consumed.get("consumed_at_ms"), bool) or
            consumed.get("consumed_at_ms", 0) < permit.get("issued_at_ms", 0)):
        raise RuntimeError("CAMPAIGN_START_CONSUMED_PERMIT_INVALID")
    base = dict(consumed)
    base.pop("phase", None)
    base.pop("invocation_id", None)
    base.pop("consumed_at_ms", None)
    if base != permit:
        raise RuntimeError("CAMPAIGN_START_CONSUMED_PERMIT_DRIFTED")
    return consumed


def _verify_active_agent_invocation(invocation_id: str) -> None:
    state = _unit_properties(
        AGENT_SERVICE, "LoadState", "ActiveState", "SubState",
        "UnitFileState", "Job", "InvocationID")
    if (state.get("LoadState") != "loaded" or
            state.get("ActiveState") != "active" or
            state.get("SubState") != "running" or
            state.get("UnitFileState") != "static" or
            state.get("Job") != "" or
            state.get("InvocationID") != invocation_id):
        raise RuntimeError("CAMPAIGN_START_AGENT_INVOCATION_INVALID")


def manual_start_campaign() -> None:
    descriptor = os.open(
        CAMPAIGN_LIFECYCLE_LOCK,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0), 0o600)
    risk_descriptor: int | None = None
    end_flat_descriptor: int | None = None
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise RuntimeError("CAMPAIGN_LIFECYCLE_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        risk_descriptor = os.open(
            RISK_RECOVERY_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600)
        risk_metadata = os.fstat(risk_descriptor)
        if (not stat.S_ISREG(risk_metadata.st_mode) or
                risk_metadata.st_nlink != 1 or risk_metadata.st_uid != 0 or
                risk_metadata.st_gid != 0 or
                stat.S_IMODE(risk_metadata.st_mode) != 0o600):
            raise RuntimeError("RISK_RECOVERY_LOCK_UNSAFE")
        fcntl.flock(risk_descriptor, fcntl.LOCK_EX)
        end_flat_descriptor = os.open(
            END_FLAT_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600)
        end_flat_metadata = os.fstat(end_flat_descriptor)
        if (not stat.S_ISREG(end_flat_metadata.st_mode) or
                end_flat_metadata.st_nlink != 1 or
                end_flat_metadata.st_uid != 0 or
                end_flat_metadata.st_gid != 0 or
                stat.S_IMODE(end_flat_metadata.st_mode) != 0o600):
            raise RuntimeError("END_FLAT_LOCK_UNSAFE")
        fcntl.flock(end_flat_descriptor, fcntl.LOCK_EX)
        agent, values, policy, state_before = _validate_campaign_start_boundary()
        if policy.get("admission_mode") == "external-p1-finalized":
            raise RuntimeError(
                "CAMPAIGN_START_EXTERNAL_P1_MANUAL_START_FORBIDDEN")
        _start_permit_paths_absent()
        snapshots = _capture_campaign_timer_states()
        started_at_ms = time.time_ns() // 1_000_000
        agent_start_attempted = False
        permit: dict[str, object] | None = None
        prelaunch_receipt_path: Path | None = None
        try:
            run_checked([
                "/usr/bin/systemctl", "reset-failed",
                AGENT_SERVICE, *CAMPAIGN_TIMER_SERVICES,
            ], timeout=30)
            run_checked([
                "/usr/bin/systemctl", "enable", *CAMPAIGN_TIMER_UNITS,
            ], timeout=30)
            run_checked([
                "/usr/bin/systemctl", "start", *CAMPAIGN_TIMER_UNITS,
            ], timeout=30)
            for unit in CAMPAIGN_TIMER_UNITS:
                _verify_waiting_timer(unit)
            _verify_deadline_timer(int(policy["expires_at_ms"]))
            _verify_start_dependencies()
            proofs = _fresh_prelaunch_zero_proof(
                agent, values, state_before)
            (prelaunch_receipt_path,
             prelaunch_receipt_sha256) = _write_prelaunch_zero_receipt(
                values, policy, state_before, proofs)
            permit = _publish_start_permit(
                values, policy, state_before, prelaunch_receipt_sha256)
            agent_start_attempted = True
            run_checked([
                "/usr/bin/systemctl", "start", AGENT_SERVICE,
            ], timeout=45)
            deadline = time.monotonic() + 20.0
            state_after: dict[str, object] | None = None
            while time.monotonic() < deadline:
                if _unit_is_active(AGENT_SERVICE):
                    candidate = agent.load_state(AGENT_STATE)
                    if candidate.get("manual_start_required") is False:
                        state_after = candidate
                        break
                time.sleep(0.2)
            if (state_after is None or
                    not isinstance(state_after.get("manual_started_at_ms"), int) or
                    isinstance(state_after.get("manual_started_at_ms"), bool) or
                    state_after.get("manual_started_at_ms", 0) < started_at_ms or
                    state_after.get("manual_start_permit_id") !=
                        permit.get("permit_id") or
                    state_after.get("runtime_binding") !=
                        state_before.get("runtime_binding") or
                    state_after.get("recovery_required") is not False or
                    state_after.get("trading_suspended") is not False):
                raise RuntimeError("CAMPAIGN_START_MARKER_NOT_CONSUMED")
            consumed = _verified_consumed_start_permit(permit)
            invocation_id = str(consumed["invocation_id"])
            if state_after.get("manual_start_invocation_id") != invocation_id:
                raise RuntimeError("CAMPAIGN_START_INVOCATION_MARKER_INVALID")
            _verify_active_agent_invocation(invocation_id)
            for unit in CAMPAIGN_TIMER_UNITS:
                _verify_waiting_timer(unit)
            _verify_deadline_timer(int(policy["expires_at_ms"]))
            _verify_start_dependencies()
            start_receipt_path = END_FLAT_RECEIPT_ROOT / (
                "start-" + values["HEPTA_LOCAL_AI_CAMPAIGN_ID"] +
                ".receipt.json")
            _write_root_json(start_receipt_path, {
                "schema": "hepta.local-ai-paper-start-receipt.v1",
                "campaign_id": values["HEPTA_LOCAL_AI_CAMPAIGN_ID"],
                "permit_id": permit["permit_id"],
                "invocation_id": invocation_id,
                "prelaunch_zero_receipt_sha256":
                    permit["prelaunch_zero_receipt_sha256"],
                "runtime_binding": state_after["runtime_binding"],
                "started_at_ms": state_after["manual_started_at_ms"],
                "completed_at_ms": time.time_ns() // 1_000_000,
                "deadline_expires_at_ms": policy["expires_at_ms"],
                "policy_max_cycles": policy["max_cycles"],
                "acceptance_cycles_consumed": 1,
                "strategy_cycle_budget": int(policy["max_cycles"]) - 1,
                "acceptance_performance_included": False,
                "paper_only": True,
                "live_authorized": False,
            })
            _remove_start_permit_file(
                START_PERMIT_CLAIMED, str(permit["permit_id"]))
            _remove_start_permit_file(
                START_PERMIT_CONSUMED, str(permit["permit_id"]))
        except BaseException as error:
            rollback_failures: list[str] = []

            def rollback_attempt(
                    label: str, operation: Callable[[], object]) -> None:
                try:
                    operation()
                except BaseException as observed:
                    rollback_failures.append(
                        label + ":" + type(observed).__name__ + ":" +
                        str(observed))

            rollback_attempt("stop-agent", lambda: run_checked([
                "/usr/bin/systemctl", "stop", AGENT_SERVICE,
            ], timeout=45))
            if _unit_is_active(AGENT_SERVICE):
                rollback_failures.append(
                    "verify-agent:CAMPAIGN_START_ROLLBACK_AGENT_ACTIVE")
            rollback_attempt(
                "restore-timers",
                lambda: _restore_campaign_timer_states(snapshots))
            if permit is not None and not (
                    START_PERMIT_CLAIMED.exists() or
                    START_PERMIT_CLAIMED.is_symlink() or
                    START_PERMIT_CONSUMED.exists() or
                    START_PERMIT_CONSUMED.is_symlink()):
                rollback_attempt(
                    "remove-pending-permit",
                    lambda: _remove_start_permit_file(
                        START_PERMIT_PENDING, str(permit["permit_id"])))
            if (not agent_start_attempted and
                    prelaunch_receipt_path is not None):
                rollback_attempt(
                    "remove-prelaunch-receipt",
                    lambda: os.unlink(prelaunch_receipt_path))
            if agent_start_attempted:
                # Always attempt this independently of the exact-state timer
                # rollback. A timed-out start may already have submitted risk.
                rollback_attempt("force-safe-recovery", lambda: run_checked([
                    "/usr/bin/systemctl", "enable", "--now",
                    SAFE_RECOVERY_TIMER,
                ], timeout=30))
            if rollback_failures:
                raise RuntimeError(
                    "CAMPAIGN_START_ROLLBACK_FAILED: " +
                    "; ".join(rollback_failures)) from error
            raise error
        print(
            "CAMPAIGN_START_COMPLETE "
            f"campaign_id={values['HEPTA_LOCAL_AI_CAMPAIGN_ID']} "
            "agent=active deadline_timer=active "
            "safe_recovery_timer=active session_renew_timer=active "
            "supervisor_timer=active end_flat_retry_timer=active "
            "paper_only=true live_authorized=false",
            flush=True)
    finally:
        if end_flat_descriptor is not None:
            try:
                fcntl.flock(end_flat_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(end_flat_descriptor)
        if risk_descriptor is not None:
            try:
                fcntl.flock(risk_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(risk_descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _end_flat_request_path(campaign_id: str) -> Path:
    return END_FLAT_RECEIPT_ROOT / (
        "end-flat-" + campaign_id + ".requested.json")


def _end_flat_trigger_file(
        path: Path, campaign_id: str, schema: str) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("END_FLAT_TRIGGER_PATH_UNSAFE")
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("END_FLAT_TRIGGER_INVALID") from error
    if (not isinstance(value, dict) or value.get("schema") != schema or
            value.get("campaign_id") != campaign_id or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False):
        raise RuntimeError("END_FLAT_TRIGGER_INVALID")
    return True


def _ensure_end_flat_request_marker(campaign_id: str) -> Path:
    path = _end_flat_request_path(campaign_id)
    if _end_flat_trigger_file(
            path, campaign_id,
            "hepta.local-ai-paper-end-flat-request.v1"):
        return path
    _write_root_json(path, {
        "schema": "hepta.local-ai-paper-end-flat-request.v1",
        "campaign_id": campaign_id,
        "requested_at_ms": time.time_ns() // 1_000_000,
        "paper_only": True,
        "live_authorized": False,
    })
    if not _end_flat_trigger_file(
            path, campaign_id,
            "hepta.local-ai-paper-end-flat-request.v1"):
        raise RuntimeError("END_FLAT_REQUEST_NOT_DURABLE")
    return path


def _prepared_abort_artifact_present(path: Path, failure: str) -> bool:
    """Treat any current-campaign handoff artifact as authoritative residue."""
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError(failure + "_PATH_UNSAFE")
    return True


def _prepared_abort_require_enabled_policy(
        campaign_id: str, *, expected_sha256: str | None = None) -> str:
    """Re-read the policy immediately before the irreversible fence."""
    metadata = os.lstat(CAMPAIGN_POLICY)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        raise RuntimeError("PREPARED_ABORT_POLICY_PATH_UNSAFE")
    try:
        raw = CAMPAIGN_POLICY.read_bytes()
        policy = json.loads(raw)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("PREPARED_ABORT_POLICY_INVALID") from error
    if (not isinstance(policy, dict) or
            policy.get("campaign_id") != campaign_id or
            policy.get("domain_id") != "alpha" or
            policy.get("paper_only") is not True or
            policy.get("live_authorized") is not False or
            policy.get("enabled") is not True or
            policy.get("mutations_authorized") is not True):
        raise RuntimeError("PREPARED_ABORT_POLICY_NOT_ENABLED")
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise RuntimeError("PREPARED_ABORT_POLICY_DRIFTED")
    return digest


def _prepared_abort_require_never_rearmed(campaign_id: str) -> None:
    """Reject every durable artifact that proves this campaign crossed rearm."""
    if _prepared_abort_artifact_present(
            END_FLAT_RECEIPT_ROOT / (
                "end-flat-" + campaign_id + ".receipt.json"),
            "PREPARED_ABORT_END_FLAT_RECEIPT"):
        raise RuntimeError("PREPARED_ABORT_TERMINAL_RECEIPT_PRESENT")
    if _prepared_abort_artifact_present(
            _end_flat_checkpoint_path(campaign_id),
            "PREPARED_ABORT_END_FLAT_CHECKPOINT"):
        raise RuntimeError("PREPARED_ABORT_TERMINAL_CHECKPOINT_PRESENT")
    if _prepared_abort_artifact_present(
            END_FLAT_RECEIPT_ROOT / (
                "rearm-stack-" + campaign_id + ".receipt.json"),
            "PREPARED_ABORT_REARM_RECEIPT"):
        raise RuntimeError("PREPARED_ABORT_ALREADY_REARMED")

    # Strategy acceptance and start permits are generation-bound global
    # handoffs.  Even a malformed one is ambiguous and therefore blocks this
    # narrow prepared-only abort instead of being silently consumed.
    for path in _strategy_acceptance_artifact_paths(campaign_id):
        if _prepared_abort_artifact_present(
                path, "PREPARED_ABORT_STRATEGY_ARTIFACT"):
            raise RuntimeError("PREPARED_ABORT_ADMISSION_RESIDUE")
    for path in (
            START_PERMIT_PENDING, START_PERMIT_CLAIMED,
            START_PERMIT_CONSUMED):
        if _prepared_abort_artifact_present(
                path, "PREPARED_ABORT_START_PERMIT"):
            raise RuntimeError("PREPARED_ABORT_START_PERMIT_RESIDUE")

    # auth-rearm receipts have a digest-derived filename, so bind by their
    # signed campaign field rather than by a guessed path.  Old receipts for
    # other campaigns are retained and do not block a fresh prepared campaign.
    try:
        auth_receipts = list(
            END_FLAT_RECEIPT_ROOT.glob("auth-rearm-*.receipt.json"))
    except OSError as error:
        raise RuntimeError("PREPARED_ABORT_REARM_ARTIFACT_UNAVAILABLE") \
            from error
    for path in auth_receipts:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600 or
                metadata.st_size > 16_384):
            raise RuntimeError("PREPARED_ABORT_REARM_ARTIFACT_PATH_UNSAFE")
        try:
            value = json.loads(path.read_text(encoding="ascii"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("PREPARED_ABORT_REARM_ARTIFACT_INVALID") \
                from error
        if (isinstance(value, dict) and
                value.get("new_campaign_id") == campaign_id):
            raise RuntimeError("PREPARED_ABORT_ALREADY_REARMED")

    # A state record can outlive its receipt.  Only a runtime binding to the
    # *current* campaign is relevant; historical bindings are intentionally
    # retained for audit and must not make a fresh campaign unrecoverable.
    try:
        metadata = os.lstat(AGENT_STATE)
    except FileNotFoundError:
        return
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_size > AGENT_STATE_SNAPSHOT_MAX_BYTES):
        raise RuntimeError("PREPARED_ABORT_STATE_PATH_UNSAFE")
    try:
        state = json.loads(AGENT_STATE.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("PREPARED_ABORT_STATE_INVALID") from error
    if not isinstance(state, dict):
        raise RuntimeError("PREPARED_ABORT_STATE_INVALID")
    binding = state.get("runtime_binding")
    if (isinstance(binding, dict) and
            binding.get("campaign_id") == campaign_id and
            any(state.get(key) is not None for key in (
                "rearm_stack_receipt_sha256", "auth_rearm_receipt_sha256",
                "auth_generation_rearmed",
                "auth_profile_sha256_rearmed",
                "auth_profile_allowlist_sha256_rearmed"))):
        raise RuntimeError("PREPARED_ABORT_ALREADY_REARMED")


def _prepared_abort_verify_local_deny_all() -> dict[str, object]:
    """Read, rather than mutate, the local control's exact DENY_ALL state."""
    rendered = run_checked([
        LOCAL_PAPER_CONTROL, "status", "--domain", "alpha",
    ], timeout=30)
    try:
        result = json.loads(rendered)
    except json.JSONDecodeError as error:
        raise RuntimeError("PREPARED_ABORT_CONTROL_STATUS_INVALID") from error
    manifest = result.get("identity_manifest_sha256") \
        if isinstance(result, dict) else None
    if (not isinstance(result, dict) or result.get("mode") != "DENY_ALL" or
            result.get("paper_authorized") is not False or
            result.get("live_authorized") is not False or
            result.get("identity_count") != 0 or
            not isinstance(manifest, str) or
            not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(manifest) or
            result.get("effective_state_verified") is not True or
            result.get("wal_state") != "ABSENT" or
            result.get("egress_verified") is not True):
        raise RuntimeError("PREPARED_ABORT_CONTROL_STATUS_INVALID")
    return result


def _prepared_abort_stop_timers() -> None:
    """Fence admission/deadline timers while retaining end-flat retry."""
    run_checked([
        "/usr/bin/systemctl", "disable", "--now",
        *PREPARED_ABORT_TIMER_UNITS,
    ], timeout=60)
    _verify_end_flat_units_disabled(PREPARED_ABORT_TIMER_UNITS)


def abort_prepared() -> None:
    """Fence a prepared-but-never-rearmed campaign, then run normal end-flat.

    This is intentionally a root CLI operation (``main`` enforces the root
    boundary).  It never grants authority and never performs broker mutation
    while holding the lifecycle locks.  The request marker and disabled policy
    are the crash-durable handoff to the existing, audited ``end_flat`` path.
    """
    with _campaign_lifecycle_locks():
        values, policy = _validated_prepared_campaign()
        campaign_id = values.get("HEPTA_LOCAL_AI_CAMPAIGN_ID")
        if (not isinstance(campaign_id, str) or
                re.fullmatch(r"[A-Za-z0-9_-]+", campaign_id) is None or
                policy.get("campaign_id") != campaign_id):
            raise RuntimeError("PREPARED_ABORT_CAMPAIGN_BINDING_INVALID")
        policy_sha256_before = _prepared_abort_require_enabled_policy(
            campaign_id)
        _prepared_abort_require_never_rearmed(campaign_id)
        if _unit_is_active(AGENT_SERVICE):
            raise RuntimeError("PREPARED_ABORT_AGENT_ACTIVE")
        _end_flat_verify_runtime_stopped()
        _validate_no_campaign_session_residue()
        _prepared_abort_verify_local_deny_all()
        _end_flat_verify_deny_all()

        # The marker is written before the policy fence so a crash in the
        # fence itself remains eligible for the ordinary retry custodian.
        request_path = _ensure_end_flat_request_marker(campaign_id)
        # Re-read the enabled policy immediately before the irreversible
        # transition.  The policy is the stronger admission fence: if a
        # timer-stop operation is interrupted (or this process dies after the
        # write), no new PAPER mutation can be admitted even while a prepared
        # timer remains armed.  Stopping timers first would leave the inverse
        # crash window (enabled policy with no custodian) and is therefore
        # intentionally forbidden.
        _prepared_abort_require_enabled_policy(
            campaign_id, expected_sha256=policy_sha256_before)
        policy_sha256 = _end_flat_persist_policy_disabled(campaign_id)
        if not _campaign_policy_expired(campaign_id, "PREPARED_ABORT"):
            raise RuntimeError("PREPARED_ABORT_POLICY_FENCE_NOT_DURABLE")
        _prepared_abort_stop_timers()
        if not _end_flat_trigger_file(
                request_path, campaign_id,
                "hepta.local-ai-paper-end-flat-request.v1"):
            raise RuntimeError("PREPARED_ABORT_REQUEST_NOT_DURABLE")
        print(
            "PREPARED_ABORT_FENCED "
            f"campaign_id={campaign_id} policy_sha256={policy_sha256} "
            "paper_authorized=false live_authorized=false",
            flush=True)

    # Do not call end_flat while any admission lock is held: end_flat owns
    # the same lock set and must be able to establish its normal checkpoint.
    end_flat()


# Keep the terminology used by the recovery runbook and operator tooling
# interchangeable without creating a second implementation or state machine.
fence_prepared = abort_prepared


def end_flat_condition() -> bool:
    """Gate the boot-persistent retry timer until closure is required."""
    values = read_env()
    campaign_id = values["HEPTA_LOCAL_AI_CAMPAIGN_ID"]
    if not campaign_id or re.fullmatch(r"[A-Za-z0-9_-]+", campaign_id) is None:
        raise RuntimeError("END_FLAT_CAMPAIGN_ID_INVALID")
    requested = _end_flat_trigger_file(
        _end_flat_request_path(campaign_id), campaign_id,
        "hepta.local-ai-paper-end-flat-request.v1")
    receipt = _end_flat_trigger_file(
        END_FLAT_RECEIPT_ROOT /
        ("end-flat-" + campaign_id + ".receipt.json"),
        campaign_id, "hepta.local-ai-paper-end-flat-receipt.v1")
    expired = _campaign_policy_expired(campaign_id, "END_FLAT")
    eligible = requested or receipt or expired
    print(
        "END_FLAT_CONDITION "
        f"campaign_id={campaign_id} eligible={str(eligible).lower()} "
        f"requested={str(requested).lower()} "
        f"receipt={str(receipt).lower()} expired={str(expired).lower()}",
        flush=True)
    return eligible


def request_end_flat() -> None:
    """Durably make terminal end-flat eligible before queueing its service."""
    with _campaign_lifecycle_locks():
        campaign_id = read_env()["HEPTA_LOCAL_AI_CAMPAIGN_ID"]
        if (not campaign_id or
                re.fullmatch(r"[A-Za-z0-9_-]+", campaign_id) is None):
            raise RuntimeError("END_FLAT_CAMPAIGN_ID_INVALID")
        path = _ensure_end_flat_request_marker(campaign_id)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(
        "END_FLAT_REQUESTED "
        f"campaign_id={campaign_id} request_sha256={digest}", flush=True)


def request_end_flat_if_orphan_start() -> bool:
    """Atomically distinguish a crashed launcher from an in-flight start."""
    with _campaign_lifecycle_locks():
        campaign_id = read_env()["HEPTA_LOCAL_AI_CAMPAIGN_ID"]
        if (not campaign_id or
                re.fullmatch(r"[A-Za-z0-9_-]+", campaign_id) is None):
            raise RuntimeError("END_FLAT_CAMPAIGN_ID_INVALID")
        if (_unit_is_active(AGENT_SERVICE) or
                not _terminal_orphan_start_permit(campaign_id)):
            print(
                "ORPHAN_START_RECHECK_DEFERRED "
                f"campaign_id={campaign_id}", flush=True)
            return False
        path = _ensure_end_flat_request_marker(campaign_id)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(
        "END_FLAT_REQUESTED orphan_start=true "
        f"campaign_id={campaign_id} request_sha256={digest}", flush=True)
    return True


def _wait_for_end_flat_runtime_units(
        required: tuple[str, ...], *,
        timeout_seconds: float | None = None,
        poll_seconds: float | None = None,
) -> None:
    """Wait for the minimum recovery stack's asynchronous unit activation.

    ``hepta-local-paper-control enable`` returns after the guardian's READY
    notification, while ``systemctl start`` for the execution/gateway units
    may still be completing their jobs.  Poll all predicates on each pass so
    a timeout identifies every unit that remained inactive.  A timeout is
    deliberately an error and is handled by the caller's existing deny-all
    rollback path.
    """
    if timeout_seconds is None:
        timeout_seconds = END_FLAT_RUNTIME_READY_TIMEOUT_SECONDS
    if poll_seconds is None:
        poll_seconds = END_FLAT_RUNTIME_READY_POLL_SECONDS
    if (not required or timeout_seconds < 0 or poll_seconds <= 0):
        raise RuntimeError("END_FLAT_RUNTIME_READY_BUDGET_INVALID")
    deadline = time.monotonic() + timeout_seconds
    while True:
        inactive = tuple(unit for unit in required
                         if not _unit_is_active(unit))
        if not inactive:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                "END_FLAT_RUNTIME_UNITS_NOT_READY:" + ",".join(inactive))
        time.sleep(min(poll_seconds, remaining))


def _ensure_end_flat_recovery_runtime(campaign_id: str) -> None:
    """Bring up only the minimum PAPER stack needed to prove/reduce risk."""
    required = (
        "hepta-execution-ib-paper@alpha.service",
        "hepta-tool-gateway@alpha.service",
        "hepta-ib-paper-campaign-operator@alpha.socket",
    )
    if not _end_flat_trigger_file(
            _end_flat_request_path(campaign_id), campaign_id,
            "hepta.local-ai-paper-end-flat-request.v1"):
        raise RuntimeError("END_FLAT_RUNTIME_BRINGUP_NOT_REQUESTED")
    # Validate the exact PAPER policy even after expiry; expiry is the desired
    # entry fence, while this temporary stack exists only for risk reduction.
    _campaign_policy_expired(campaign_id, "END_FLAT")
    if all(_unit_is_active(unit) for unit in required):
        _require_active_local_paper_control(
            "END_FLAT_RUNTIME_CONTROL_STATUS_INVALID")
        return
    try:
        policy = _campaign_policy_for_control()
        if policy.get("campaign_id") != campaign_id:
            raise RuntimeError("END_FLAT_RUNTIME_POLICY_MISMATCH")
        rendered = run_checked(
            _paper_control_enable_command(policy), timeout=120)
        try:
            result = json.loads(rendered)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "END_FLAT_RUNTIME_CONTROL_RESPONSE_INVALID") from error
        manifest = result.get("identity_manifest_sha256") \
            if isinstance(result, dict) else None
        if (not isinstance(result, dict) or
                result.get("mode") != "LOCAL_PAPER" or
                result.get("domain") != "alpha" or
                result.get("paper_authorized") is not True or
                result.get("live_authorized") is not False or
                result.get("admission_mode") != policy.get("admission_mode") or
                not isinstance(manifest, str) or
                AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                    manifest) is None):
            raise RuntimeError("END_FLAT_RUNTIME_CONTROL_RESPONSE_INVALID")
        # The guardian's READY response does not make Type=simple child unit
        # jobs synchronous.  Wait for the exact required set before asking
        # the stricter control/status verifier to classify the runtime.
        _wait_for_end_flat_runtime_units(required)
        _require_active_local_paper_control(
            "END_FLAT_RUNTIME_CONTROL_STATUS_INVALID")
    except BaseException as error:
        rollback: list[str] = []
        try:
            _end_flat_revoke_local_paper_control()
            _end_flat_verify_deny_all()
        except BaseException as observed:
            rollback.append("deny-all:" + type(observed).__name__)
        try:
            run_checked([
                "/usr/bin/systemctl", "stop",
                *END_FLAT_EXECUTION_UNITS, *END_FLAT_TOOL_UNITS,
            ], timeout=60)
            _end_flat_verify_runtime_stopped()
        except BaseException as observed:
            rollback.append("stop-runtime:" + type(observed).__name__)
        if rollback:
            raise RuntimeError(
                "END_FLAT_RUNTIME_BRINGUP_ROLLBACK_FAILED:" +
                ";".join(rollback)) from error
        raise RuntimeError("END_FLAT_RUNTIME_BRINGUP_FAILED") from error


def _end_flat_runtime_fail_closed_rollback() -> None:
    """Close a recovery runtime when no usable owner can be selected.

    ``_ensure_end_flat_recovery_runtime`` deliberately enables the minimum
    PAPER stack before session selection so a recovery owner can be resolved.
    Selection can still fail (for example while a supervisor revoke remains
    pending).  That failure must not leave LOCAL_PAPER authority or the
    runtime units active while the durable owner transaction is retried.  Each
    cleanup operation is attempted independently; a failure to prove either
    DENY_ALL or a stopped runtime is itself reported as a rollback failure.
    Session authorities are intentionally untouched here and remain owned by
    the generation-bound supervisor resolver.
    """
    rollback_failures: list[str] = []

    def rollback_attempt(label: str, operation: Callable[[], object]) -> None:
        try:
            operation()
        except BaseException as observed:
            rollback_failures.append(
                label + ":" + type(observed).__name__)

    # Try the control transition even if it has already failed; the verifier
    # and runtime stop below are independent fences and must still run.
    rollback_attempt(
        "deny-all",
        lambda: _end_flat_revoke_local_paper_control())
    rollback_attempt("deny-all-verify", _end_flat_verify_deny_all)
    rollback_attempt(
        "stop-runtime",
        lambda: run_checked([
            "/usr/bin/systemctl", "stop",
            *END_FLAT_EXECUTION_UNITS, *END_FLAT_TOOL_UNITS,
        ], timeout=60))
    rollback_attempt("runtime-verify", _end_flat_verify_runtime_stopped)
    if rollback_failures:
        raise RuntimeError(
            "END_FLAT_SESSION_SELECTION_ROLLBACK_FAILED: " +
            "; ".join(rollback_failures))


def _validated_end_flat_receipt(campaign_id: str) -> tuple[Path, dict[str, object]] | None:
    path = END_FLAT_RECEIPT_ROOT / (
        "end-flat-" + campaign_id + ".receipt.json")
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise RuntimeError("END_FLAT_RECEIPT_PATH_UNSAFE")
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("END_FLAT_RECEIPT_INVALID") from error
    revoked_sessions = value.get("revoked_sessions") \
        if isinstance(value, dict) else None
    if (not isinstance(value, dict) or
            value.get("schema") !=
                "hepta.local-ai-paper-end-flat-receipt.v1" or
            value.get("campaign_id") != campaign_id or
            value.get("position") != 0 or
            value.get("active_orders") != 0 or
            value.get("gross_absolute_position") != 0 or
            value.get("campaign_enabled") is not False or
            value.get("mutations_authorized") is not False or
            value.get("local_paper_authorized") is not False or
            value.get("authorized_connector_count") != 0 or
            value.get("authorized_uids") != [] or
            value.get("identity_count") != 0 or
            value.get("protected_port_count") != 4 or
            value.get("deny_all_verified") is not True or
            not isinstance(value.get("broker_policy_sha256"), str) or
            not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                str(value.get("broker_policy_sha256"))) or
            not isinstance(value.get("identity_manifest_sha256"), str) or
            not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                str(value.get("identity_manifest_sha256"))) or
            value.get("tool_gateway_stopped") is not True or
            value.get("execution_runtime_stopped") is not True or
            value.get("start_permits_cleared") is not True or
            value.get("known_campaign_sessions_revoked") is not True or
            not isinstance(revoked_sessions, list) or not revoked_sessions or
            not all(isinstance(raw, dict) and raw.get("revoked") is True
                    for raw in revoked_sessions) or
            value.get("reboot_durable") is not True or
            value.get("paper_only") is not True or
            value.get("live_authorized") is not False):
        raise RuntimeError("END_FLAT_RECEIPT_INVALID")
    if value.get("campaign_policy_sha256") != (
            "sha256:" + hashlib.sha256(CAMPAIGN_POLICY.read_bytes()).hexdigest()):
        raise RuntimeError("END_FLAT_RECEIPT_POLICY_DRIFTED")
    return path, value


def _verify_end_flat_units_disabled(units: tuple[str, ...]) -> None:
    for unit in units:
        state = _unit_properties(
            unit, "LoadState", "ActiveState", "UnitFileState", "Job")
        if (state.get("LoadState") != "loaded" or
                state.get("ActiveState") not in {"inactive", "failed"} or
                state.get("Job") != "" or
                state.get("UnitFileState") in {
                    "enabled", "enabled-runtime"}):
            raise RuntimeError("END_FLAT_UNIT_NOT_DISABLED:" + unit)


def _seal_end_flat_runtime_units() -> None:
    units = (
        AGENT_SERVICE,
        SAFE_RECOVERY_TIMER,
        SESSION_RENEW_TIMER,
        SUPERVISOR_TIMER,
        PERSISTENT_STOP_TIMER,
        *END_FLAT_EXECUTION_UNITS,
        *END_FLAT_TOOL_UNITS,
    )
    run_checked([
        "/usr/bin/systemctl", "disable", "--now",
        *units,
    ], timeout=45)
    _end_flat_verify_runtime_stopped()
    _verify_end_flat_units_disabled(units)


def _seal_end_flat_retry_timer() -> None:
    run_checked([
        "/usr/bin/systemctl", "disable", "--now",
        END_FLAT_RETRY_TIMER,
    ], timeout=30)
    _verify_end_flat_units_disabled((END_FLAT_RETRY_TIMER,))


def _prove_start_permit_cleanup_boundary(campaign_id: str) -> None:
    """Re-prove every terminal fence before deleting ambiguous permits."""
    metadata = os.lstat(CAMPAIGN_POLICY)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        raise RuntimeError("END_FLAT_PERMIT_CLEANUP_POLICY_PATH_UNSAFE")
    try:
        policy = json.loads(CAMPAIGN_POLICY.read_text(encoding="ascii"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("END_FLAT_PERMIT_CLEANUP_POLICY_INVALID") from error
    if (not isinstance(policy, dict) or
            policy.get("schema") not in RECOVERY_POLICY_SCHEMAS or
            policy.get("campaign_id") != campaign_id or
            policy.get("domain_id") != "alpha" or
            policy.get("enabled") is not False or
            policy.get("mutations_authorized") is not False or
            policy.get("paper_only") is not True or
            policy.get("live_authorized") is not False):
        raise RuntimeError("END_FLAT_PERMIT_CLEANUP_POLICY_NOT_DISABLED")
    checkpoint = _load_end_flat_checkpoint(campaign_id)
    receipt = _validated_end_flat_receipt(campaign_id)
    sessions_proven = bool(
        checkpoint is not None and
        checkpoint.get("phase") == "EGRESS_REVOKED" and
        all(isinstance(raw, dict) and raw.get("revoked") is True
            for raw in checkpoint.get("sessions", [])))
    evidence: dict[str, object] | None = checkpoint
    if not sessions_proven and receipt is not None:
        evidence = receipt[1]
        sessions_proven = bool(
            evidence.get("known_campaign_sessions_revoked") is True and
            all(isinstance(raw, dict) and raw.get("revoked") is True
                for raw in evidence.get("revoked_sessions", [])))
    if not sessions_proven or evidence is None:
        raise RuntimeError("END_FLAT_PERMIT_CLEANUP_SESSIONS_UNPROVEN")
    _validate_no_campaign_session_residue()
    deny_all = _end_flat_verify_deny_all()
    if (evidence.get("broker_policy_sha256") !=
            deny_all.get("broker_policy_sha256") or
            deny_all.get("authorized_connector_count") != 0):
        raise RuntimeError("END_FLAT_PERMIT_CLEANUP_DENY_ALL_DRIFTED")
    _end_flat_verify_runtime_stopped()


def _seal_start_permit_residue(campaign_id: str) -> int:
    _prove_start_permit_cleanup_boundary(campaign_id)
    removed = 0
    changed_parents: set[Path] = set()
    for path in (
            START_PERMIT_PENDING, START_PERMIT_CLAIMED,
            START_PERMIT_CONSUMED):
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            continue
        try:
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                    opened.st_uid != 0 or opened.st_gid != 0 or
                    stat.S_IMODE(opened.st_mode) != 0o600 or
                    not 0 <= opened.st_size <= 16_384):
                raise RuntimeError("END_FLAT_START_PERMIT_PATH_UNSAFE")
            payload = bytearray()
            while len(payload) <= 16_384:
                chunk = os.read(descriptor, 16_385 - len(payload))
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) > 16_384:
                raise RuntimeError("END_FLAT_START_PERMIT_PATH_UNSAFE")
            after = os.fstat(descriptor)
            current = os.stat(path, follow_symlinks=False)
            if (after.st_dev != opened.st_dev or
                    after.st_ino != opened.st_ino or
                    after.st_size != opened.st_size or
                    current.st_dev != opened.st_dev or
                    current.st_ino != opened.st_ino or
                    current.st_nlink != 1):
                raise RuntimeError("END_FLAT_START_PERMIT_PATH_DRIFTED")
        finally:
            os.close(descriptor)
        try:
            value = json.loads(payload)
        except (UnicodeError, ValueError, json.JSONDecodeError):
            # Torn legacy artifacts contain no trustworthy authority binding.
            # They are removable only because the terminal boundary above was
            # freshly re-proven before this loop.
            value = None
        if (value is not None and
                (not isinstance(value, dict) or
                 value.get("schema") !=
                    "hepta.local-ai-paper-start-permit.v1" or
                 value.get("campaign_id") != campaign_id or
                 value.get("paper_only") is not True or
                 value.get("live_authorized") is not False)):
            raise RuntimeError("END_FLAT_START_PERMIT_INVALID")
        os.unlink(path)
        removed += 1
        changed_parents.add(path.parent)
    for parent in changed_parents:
        directory = os.open(
            parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return removed


def end_flat() -> None:
    selected_token: Path | None = None
    retained_original_session = True
    checkpoint_persist_started = False
    lifecycle_descriptor = os.open(
        CAMPAIGN_LIFECYCLE_LOCK,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o600)
    risk_descriptor: int | None = None
    descriptor: int | None = None
    try:
        lifecycle_metadata = os.fstat(lifecycle_descriptor)
        if (not stat.S_ISREG(lifecycle_metadata.st_mode) or
                lifecycle_metadata.st_nlink != 1 or
                lifecycle_metadata.st_uid != 0 or
                lifecycle_metadata.st_gid != 0 or
                stat.S_IMODE(lifecycle_metadata.st_mode) != 0o600):
            raise RuntimeError("CAMPAIGN_LIFECYCLE_LOCK_UNSAFE")
        fcntl.flock(lifecycle_descriptor, fcntl.LOCK_EX)
        risk_descriptor = os.open(
            RISK_RECOVERY_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600)
        risk_metadata = os.fstat(risk_descriptor)
        if (not stat.S_ISREG(risk_metadata.st_mode) or
                risk_metadata.st_nlink != 1 or risk_metadata.st_uid != 0 or
                risk_metadata.st_gid != 0 or
                stat.S_IMODE(risk_metadata.st_mode) != 0o600):
            raise RuntimeError("RISK_RECOVERY_LOCK_UNSAFE")
        fcntl.flock(risk_descriptor, fcntl.LOCK_EX)
        descriptor = os.open(
            END_FLAT_LOCK,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0), 0o600)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise RuntimeError("END_FLAT_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        values = read_env()
        campaign_id = values["HEPTA_LOCAL_AI_CAMPAIGN_ID"]
        if not campaign_id or any(
                character not in
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                for character in campaign_id):
            raise RuntimeError("END_FLAT_CAMPAIGN_ID_INVALID")
        policy = _external_policy_for_dispatch()
        if policy is not None:
            if policy.get("campaign_id") != campaign_id:
                raise RuntimeError("END_FLAT_RUNTIME_POLICY_MISMATCH")
            _external_risk_recover_locked(
                safety_exit=True, automatic=False)
            return
        # This durable marker is the reboot handoff for every pre-receipt
        # phase.  The boot-enabled retry timer is sealed only after the
        # terminal receipt and DENY_ALL closure are both durable.
        _ensure_end_flat_request_marker(campaign_id)
        run_checked([
            "/usr/bin/systemctl", "stop",
            SAFE_RECOVERY_TIMER,
            SAFE_RECOVERY_SERVICE,
            SESSION_RENEW_TIMER,
            SESSION_RENEW_SERVICE,
            SUPERVISOR_TIMER,
            SUPERVISOR_SERVICE,
            AGENT_SERVICE,
        ], timeout=45)
        existing_receipt = _validated_end_flat_receipt(campaign_id)
        if existing_receipt is not None:
            _validate_no_campaign_session_residue()
            run_checked([
                "/usr/bin/systemctl", "stop",
                *END_FLAT_EXECUTION_UNITS,
                *END_FLAT_TOOL_UNITS,
            ], timeout=60)
            _end_flat_verify_runtime_stopped()
            current_deny_all = _end_flat_verify_deny_all()
            if (current_deny_all.get("broker_policy_sha256") !=
                    existing_receipt[1].get("broker_policy_sha256")):
                raise RuntimeError("END_FLAT_DENY_ALL_POLICY_DRIFTED")
            _seal_end_flat_runtime_units()
            _seal_start_permit_residue(campaign_id)
            _seal_end_flat_retry_timer()
            receipt_hash = hashlib.sha256(
                existing_receipt[0].read_bytes()).hexdigest()
            print(
                "END_FLAT_COMPLETE "
                f"campaign_id={campaign_id} position=0 active_orders=0 "
                f"gross=0 receipt_sha256={receipt_hash} resumed=true",
                flush=True)
            return
        agent = load_agent()
        checkpoint = _load_end_flat_checkpoint(campaign_id)
        if checkpoint is None:
            _ensure_end_flat_recovery_runtime(campaign_id)
            try:
                selected_token, retained_original_session = (
                    _select_end_flat_session(campaign_id))
            except BaseException:
                try:
                    _end_flat_runtime_fail_closed_rollback()
                except BaseException as rollback_error:
                    raise RuntimeError(
                        "END_FLAT_SESSION_SELECTION_ROLLBACK_FAILED") \
                        from rollback_error
                raise
            if not retained_original_session:
                metadata = os.lstat(AGENT_STATE)
                if (not stat.S_ISREG(metadata.st_mode) or
                        metadata.st_nlink != 1 or metadata.st_uid != 0 or
                        metadata.st_gid != 0):
                    raise RuntimeError("END_FLAT_STATE_PATH_UNSAFE")
                durable_state = agent.load_state(AGENT_STATE)
                durable_active = durable_state.get("active_order_ids")
                if (durable_state.get("pending_order_id") is not None or
                        durable_active not in (None, [], ())):
                    raise RuntimeError(
                        "END_FLAT_ORIGINAL_SESSION_REQUIRED_FOR_ACTIVE_ORDER")
        else:
            selected_token = TOKEN_FILE
            retained_original_session = True
        state_path = END_FLAT_RECEIPT_ROOT / (
            "end-flat-" + campaign_id + ".state.json")
        if checkpoint is None:
            records = _end_flat_state_records(agent, campaign_id, state_path)
            state = next(
                value for path, value in records if path == state_path)
            (command_reconciliation, command_terminal_by_owner,
             accepted_command_records) = _reconcile_pending_mutation_records(
                agent, records, "END_FLAT")
            contexts = _managed_session_contexts(
                agent, state_path, "END_FLAT")
            selected_context = contexts.get(selected_token.name)
            if selected_context is None:
                raise RuntimeError("END_FLAT_SELECTED_SESSION_MISSING")
            arguments = selected_context[1]
            halt_result = _end_flat_halt_campaign(agent, arguments)
            (cancelled_by_owner, terminal_by_owner) = (
                _cancel_all_managed_session_orders(
                    agent, contexts, command_terminal_by_owner))
            _clear_terminal_pending_mutation_records(
                accepted_command_records, terminal_by_owner)
            cancelled = sorted({
                order_id for owner_ids in cancelled_by_owner.values()
                for order_id in owner_ids})
            terminally_reconciled = sorted({
                order_id for owner_ids in terminal_by_owner.values()
                for order_id in owner_ids})
            position, _, _ = authoritative_state(agent, arguments)
            while not agent._quantity_equal(position, 0.0):
                after = agent.flatten(arguments, state)
                if abs(after) >= abs(position) and not agent._quantity_equal(
                        after, 0.0):
                    raise RuntimeError("END_FLAT_DID_NOT_REDUCE_EXPOSURE")
                position = after
                if not agent._quantity_equal(position, 0.0):
                    wait_rate_window()
            # flatten() persists its exact command/order lineage to state_path.
            # Reload it before the final owner-wide proofs so a lost response or
            # terminal projection can never be erased by this process.
            state = _load_end_flat_state(agent, state_path)
            if _pending_mutation_identity(
                    state, "END_FLAT_MUTATION_INTENT_INVALID") is not None:
                (flatten_reconciliation, flatten_targets,
                 flatten_records) = _reconcile_pending_mutation_records(
                    agent, [(state_path, state)], "END_FLAT")
                command_reconciliation.extend(flatten_reconciliation)
                _more_cancelled, flatten_terminal = (
                    _cancel_all_managed_session_orders(
                        agent, contexts, flatten_targets))
                _clear_terminal_pending_mutation_records(
                    flatten_records, flatten_terminal)
                terminally_reconciled = sorted(set(
                    terminally_reconciled).union(*flatten_terminal.values()))
            _require_managed_sessions_no_active_orders(agent, contexts)
            first_position_generation, first_cash_generation = (
                _end_flat_authoritative_proof(agent, arguments))
            time.sleep(2.0)
            _require_managed_sessions_no_active_orders(agent, contexts)
            second_position_generation, second_cash_generation = (
                _end_flat_authoritative_proof(agent, arguments))
            # Publish a durable risk-zero checkpoint before revoking the only
            # credentials able to prove or reduce broker exposure.
            policy_sha256 = _end_flat_persist_policy_disabled(campaign_id)
            checkpoint = {
                "schema": "hepta.local-ai-paper-end-flat-checkpoint.v1",
                "campaign_id": campaign_id,
                "phase": "RISK_ZERO_SEALED",
                "halt_result": halt_result,
                "cancelled_order_ids": cancelled,
                "cancelled_order_ids_by_owner": cancelled_by_owner,
                "terminally_reconciled_order_ids": terminally_reconciled,
                "command_reconciliation": command_reconciliation,
                "position": 0,
                "active_orders": 0,
                "gross_absolute_position": 0,
                "first_position_generation": first_position_generation,
                "first_fx_cash_generation": first_cash_generation,
                "second_position_generation": second_position_generation,
                "second_fx_cash_generation": second_cash_generation,
                "campaign_policy_sha256": policy_sha256,
                "sessions": _end_flat_session_descriptors(),
                "paper_only": True,
                "live_authorized": False,
            }
            # Once checkpoint persistence starts, leave the selected recovery
            # owner to the generation-bound checkpoint resolver.  A failed
            # write is itself ambiguous; the finalizer below must not race a
            # potentially durable RISK_ZERO_SEALED checkpoint.
            checkpoint_persist_started = True
            _persist_end_flat_checkpoint(checkpoint)
        if checkpoint.get("phase") == "RISK_ZERO_SEALED":
            _ensure_end_flat_recovery_runtime(campaign_id)
            _revoke_checkpoint_sessions(checkpoint)
        if checkpoint.get("phase") == "SESSIONS_REVOKED":
            control_evidence = _end_flat_revoke_local_paper_control()
            deny_all_evidence = _end_flat_verify_deny_all()
            checkpoint.update(control_evidence)
            checkpoint.update(deny_all_evidence)
            checkpoint["phase"] = "EGRESS_REVOKED"
            _persist_end_flat_checkpoint(checkpoint)
        if checkpoint.get("phase") != "EGRESS_REVOKED":
            raise RuntimeError("END_FLAT_CHECKPOINT_PHASE_INVALID")
        run_checked([
            "/usr/bin/systemctl", "stop",
            *END_FLAT_EXECUTION_UNITS,
            *END_FLAT_TOOL_UNITS,
        ], timeout=60)
        _end_flat_verify_runtime_stopped()
        post_stop_deny_all = _end_flat_verify_deny_all()
        if (post_stop_deny_all.get("broker_policy_sha256") !=
                checkpoint.get("broker_policy_sha256")):
            raise RuntimeError("END_FLAT_DENY_ALL_POLICY_DRIFTED")
        receipt = {
            "schema": "hepta.local-ai-paper-end-flat-receipt.v1",
            "campaign_id": campaign_id,
            "completed_at_ms": time.time_ns() // 1_000_000,
            "halt_result": checkpoint.get("halt_result"),
            "cancelled_order_ids": checkpoint.get("cancelled_order_ids"),
            "cancelled_order_ids_by_owner": checkpoint.get(
                "cancelled_order_ids_by_owner", {}),
            "terminally_reconciled_order_ids": checkpoint.get(
                "terminally_reconciled_order_ids", []),
            "command_reconciliation": checkpoint.get(
                "command_reconciliation", []),
            "position": 0,
            "active_orders": 0,
            "gross_absolute_position": 0,
            "first_position_generation": checkpoint.get(
                "first_position_generation"),
            "first_fx_cash_generation": checkpoint.get(
                "first_fx_cash_generation"),
            "second_position_generation": checkpoint.get(
                "second_position_generation"),
            "second_fx_cash_generation": checkpoint.get(
                "second_fx_cash_generation"),
            "campaign_policy_sha256": checkpoint.get(
                "campaign_policy_sha256"),
            "campaign_enabled": False,
            "mutations_authorized": False,
            "local_paper_authorized": False,
            "identity_manifest_sha256": checkpoint.get(
                "identity_manifest_sha256"),
            "identity_count": 0,
            "deny_all_verified": True,
            "broker_policy_sha256": checkpoint.get("broker_policy_sha256"),
            "authorized_connector_count": 0,
            "authorized_uids": [],
            "protected_port_count": 4,
            "known_campaign_sessions_revoked": bool(
                checkpoint.get("sessions")) and all(
                    isinstance(raw, dict) and raw.get("revoked") is True
                    for raw in checkpoint.get("sessions", [])),
            "revoked_sessions": checkpoint.get("sessions"),
            "tool_gateway_stopped": True,
            "execution_runtime_stopped": True,
            "start_permits_cleared": True,
            "reboot_durable": True,
            "paper_only": True,
            "live_authorized": False,
        }
        receipt_path = END_FLAT_RECEIPT_ROOT / (
            "end-flat-" + campaign_id + ".receipt.json")
        # Keep the retry timer armed until both the runtime closure and the
        # terminal receipt are durable. A crash in either window therefore
        # resumes from the generation-bound checkpoint.
        _seal_end_flat_runtime_units()
        _seal_start_permit_residue(campaign_id)
        _write_root_json(receipt_path, receipt)
        receipt_hash = hashlib.sha256(
            receipt_path.read_bytes()).hexdigest()
        _seal_end_flat_retry_timer()
        print(
            "END_FLAT_COMPLETE "
            f"campaign_id={campaign_id} position=0 active_orders=0 gross=0 "
            f"receipt_sha256={receipt_hash}", flush=True)
    finally:
        # A pre-checkpoint read/projection failure can otherwise strand a
        # freshly provisioned recovery authority (the exact failure seen when
        # orders.list returned READ_TOOL_FAILED).  Finalize only a
        # recovery-only selection, and only before checkpoint persistence has
        # begun.  The helper catches resolver uncertainty and never masks the
        # original end-flat exception; unresolved ACTIVE/REVOKE_PENDING
        # lineage remains durable for the next official retry.
        if (sys.exc_info()[1] is not None and selected_token is not None and
                not retained_original_session and
                not checkpoint_persist_started):
            _finalize_failed_end_flat_recovery_session(
                selected_token, retained_original_session)
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        if risk_descriptor is not None:
            try:
                fcntl.flock(risk_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(risk_descriptor)
        try:
            fcntl.flock(lifecycle_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(lifecycle_descriptor)


def _verified_rearm_stack_receipt(
        state: dict[str, object], values: dict[str, str],
        *, require_active_authority: bool = True,
) -> tuple[dict[str, object], str]:
    expected_hash = state.get("rearm_stack_receipt_sha256")
    if (not isinstance(expected_hash, str) or
            not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                expected_hash)):
        raise RuntimeError("AUTH_REARM_STACK_RECEIPT_REFERENCE_INVALID")
    matches: list[tuple[dict[str, object], str]] = []
    for path in END_FLAT_RECEIPT_ROOT.glob("rearm-stack-*.receipt.json"):
        metadata = os.lstat(path)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise RuntimeError("AUTH_REARM_STACK_RECEIPT_PATH_UNSAFE")
        raw = path.read_bytes()
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        if digest != expected_hash:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError("AUTH_REARM_STACK_RECEIPT_INVALID") from error
        if isinstance(value, dict):
            matches.append((value, digest))
    if len(matches) != 1:
        raise RuntimeError("AUTH_REARM_STACK_RECEIPT_NOT_UNIQUE")
    receipt, digest = matches[0]
    if (receipt.get("schema") !=
            "hepta.local-ai-paper-rearm-stack-receipt.v1" or
            receipt.get("campaign_id") !=
                values["HEPTA_LOCAL_AI_CAMPAIGN_ID"] or
            not isinstance(receipt.get("session_authority_sha256"), str) or
            not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                str(receipt.get("session_authority_sha256"))) or
            not isinstance(receipt.get("session_token_sha256"), str) or
            not AUTH_PROFILE_ALLOWLIST_SHA256_PATTERN.fullmatch(
                str(receipt.get("session_token_sha256"))) or
            not isinstance(receipt.get("session_lease_generation"), int) or
            isinstance(receipt.get("session_lease_generation"), bool) or
            receipt.get("session_lease_generation", 0) < 1 or
            receipt.get("runtime_binding") != state.get("runtime_binding") or
            receipt.get("position") != 0 or
            receipt.get("active_orders") != 0 or
            receipt.get("gross_absolute_position") != 0 or
            receipt.get("agent_still_stopped") is not True or
            receipt.get("paper_only") is not True or
            receipt.get("live_authorized") is not False):
        raise RuntimeError("AUTH_REARM_STACK_RECEIPT_INVALID")
    if require_active_authority:
        authority = _load_session_provision_intent(TOKEN_FILE)
        if authority is None:
            raise RuntimeError("AUTH_REARM_STACK_AUTHORITY_MISSING")
        authority_raw = session_provision_intent_path(TOKEN_FILE).read_bytes()
        authority_sha256 = (
            "sha256:" + hashlib.sha256(authority_raw).hexdigest())
        if (receipt.get("session_authority_sha256") != authority_sha256 or
                receipt.get("session_token_sha256") !=
                    authority.get("token_sha256") or
                receipt.get("session_lease_generation") !=
                    authority.get("lease_generation") or
                authority.get("phase") != "ACTIVE"):
            raise RuntimeError("AUTH_REARM_STACK_RECEIPT_INVALID")
        if not session_usable(TOKEN_FILE):
            raise RuntimeError("AUTH_REARM_STACK_SESSION_UNUSABLE")
    return receipt, digest


def _auth_rearm_locked(profile_id: str, auth_generation: str) -> None:
    """Rearm only after new auth, a new campaign, and current zero risk."""
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{2,255}",
                         profile_id) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}",
                             auth_generation)):
        raise RuntimeError("AUTH_REARM_ARGUMENT_INVALID")
    if run([
            "/usr/bin/systemctl", "is-active",
            "hepta-local-ai-paper-agent.service"], 5).returncode == 0:
        raise RuntimeError("AUTH_REARM_REQUIRES_AGENT_STOPPED")
    _validated_prepared_campaign()
    _require_only_primary_session_authority()
    _verify_waiting_timer(SAFE_RECOVERY_TIMER)
    agent = load_agent()
    state = _load_root_agent_state(agent)
    if (state.get("recovery_complete") is not True or
            state.get("recovery_phase") != "FLAT_CONFIRMED"):
        raise RuntimeError("AUTH_REARM_REQUIRES_FLAT_CONFIRMED")
    old_generation = state.get("auth_generation_at_suspend")
    suspended_at_ms = state.get("suspended_at_ms")
    old_campaign_id = state.get("campaign_id_at_suspend")
    values = read_env()
    if (not isinstance(old_generation, str) or not old_generation or
            auth_generation == old_generation or
            values["HEPTA_LOCAL_AI_AUTH_GENERATION"] != auth_generation):
        raise RuntimeError("AUTH_REARM_GENERATION_NOT_CHANGED")
    if (not isinstance(old_campaign_id, str) or not old_campaign_id or
            values["HEPTA_LOCAL_AI_CAMPAIGN_ID"] == old_campaign_id):
        raise RuntimeError("AUTH_REARM_NEW_CAMPAIGN_REQUIRED")
    if values["HEPTA_LOCAL_AI_AUTH_PROFILE_ID"] != profile_id:
        raise RuntimeError("AUTH_REARM_PROFILE_NOT_CONFIGURED")
    if (not isinstance(suspended_at_ms, int) or
            isinstance(suspended_at_ms, bool) or suspended_at_ms <= 0):
        raise RuntimeError("AUTH_REARM_SUSPENSION_TIME_INVALID")
    _, recovery_receipt, recovery_receipt_hash = (
        _verified_risk_recovery_receipt(state))
    if int(recovery_receipt.get("completed_at_ms", 0)) < suspended_at_ms:
        raise RuntimeError("AUTH_REARM_RECOVERY_RECEIPT_TOO_OLD")
    rearm_stack_receipt, rearm_stack_receipt_sha256 = (
        _verified_rearm_stack_receipt(state, values))
    auth_profile_allowlist, auth_profile_allowlist_sha256 = (
        _verify_effective_auth_order(profile_id))
    probe_finished_at_ms, probe_model = _probe_auth_profile(profile_id)
    if probe_finished_at_ms < suspended_at_ms:
        raise RuntimeError("AUTH_REARM_PROFILE_PROBE_TOO_OLD")
    (canary_completed_at_ms, canary_session_id,
     canary_auth_profile_sha256) = _production_auth_canary(
        auth_profile_allowlist)
    if canary_completed_at_ms < suspended_at_ms:
        raise RuntimeError("AUTH_REARM_PRODUCTION_CANARY_TOO_OLD")
    (post_canary_allowlist,
     post_canary_allowlist_sha256) = _verify_effective_auth_order(profile_id)
    if (post_canary_allowlist != auth_profile_allowlist or
            post_canary_allowlist_sha256 !=
                auth_profile_allowlist_sha256):
        raise RuntimeError("AUTH_REARM_EFFECTIVE_PROFILE_ALLOWLIST_DRIFTED")
    arguments = agent_arguments(
        AGENT_STATE,
        auth_profile_allowlist_sha256=auth_profile_allowlist_sha256)
    first_position_generation, first_cash_generation = (
        _current_zero_proof(agent, arguments))
    first_runtime_binding = agent.current_runtime_binding(arguments)
    time.sleep(2.0)
    second_position_generation, second_cash_generation = (
        _current_zero_proof(agent, arguments))
    second_runtime_binding = agent.current_runtime_binding(arguments)
    if first_runtime_binding != second_runtime_binding:
        raise RuntimeError("AUTH_REARM_RUNTIME_BINDING_DRIFTED")
    # Clear the stopped service's historical exit=75 before publishing the
    # unlatched generation-bound state. If the timer runs before this point,
    # the old latch still blocks restart; after this point the old status can
    # no longer be mistaken for a fresh safety incident.
    _reset_agent_failure_state()
    if run([
            "/usr/bin/systemctl", "is-active",
            "hepta-local-ai-paper-agent.service"], 5).returncode == 0:
        raise RuntimeError("AUTH_REARM_AGENT_REACTIVATED_UNEXPECTEDLY")
    _publish_auth_profile_allowlist_sha256(
        values, auth_profile_allowlist_sha256)
    suspension_id = str(state["suspension_id"])
    receipt = {
        "schema": "hepta.local-ai-paper-auth-rearm-receipt.v1",
        "suspension_id": suspension_id,
        "suspension_code": state.get("suspension_code"),
        "prior_recovery_receipt_sha256":
            "sha256:" + recovery_receipt_hash,
        "rearm_stack_receipt_sha256": rearm_stack_receipt_sha256,
        "rearm_stack_completed_at_ms":
            rearm_stack_receipt.get("completed_at_ms"),
        "old_auth_generation": old_generation,
        "new_auth_generation": auth_generation,
        "old_campaign_id": old_campaign_id,
        "new_campaign_id": values["HEPTA_LOCAL_AI_CAMPAIGN_ID"],
        "auth_profile_sha256": "sha256:" + hashlib.sha256(
            profile_id.encode("utf-8")).hexdigest(),
        "auth_profile_allowlist_sha256": auth_profile_allowlist_sha256,
        "profile_probe_model": probe_model,
        "profile_probe_finished_at_ms": probe_finished_at_ms,
        "production_canary_model": AUTH_CANARY_MODEL,
        "production_canary_session_id": canary_session_id,
        "production_canary_auth_profile_sha256":
            canary_auth_profile_sha256,
        "production_canary_completed_at_ms": canary_completed_at_ms,
        "first_position_generation": first_position_generation,
        "first_fx_cash_generation": first_cash_generation,
        "second_position_generation": second_position_generation,
        "second_fx_cash_generation": second_cash_generation,
        "runtime_binding": second_runtime_binding,
        "position": 0,
        "active_orders": 0,
        "gross_absolute_position": 0,
        "manual_start_required": True,
        "paper_only": True,
        "live_authorized": False,
        "completed_at_ms": time.time_ns() // 1_000_000,
    }
    suspension_digest = hashlib.sha256(
        suspension_id.encode("utf-8")).hexdigest()[:24]
    receipt_path = END_FLAT_RECEIPT_ROOT / (
        "auth-rearm-" + suspension_digest + ".receipt.json")
    agent.write_json(receipt_path, receipt)
    os.chown(receipt_path, 0, 0)
    os.chmod(receipt_path, 0o600)
    receipt_sha256 = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    metadata = os.lstat(AGENT_STATE)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0):
        raise RuntimeError("AUTH_REARM_STATE_PATH_UNSAFE")
    backup = AGENT_STATE.with_name(
        "state.pre-auth-rearm-" + str(int(time.time())) + "-" +
        uuid.uuid4().hex[:8] + ".json")
    _copy_root_state_snapshot(AGENT_STATE, backup, metadata)
    rearmed_state = agent.empty_state()
    rearmed_state["auth_generation_rearmed"] = auth_generation
    rearmed_state["auth_profile_sha256_rearmed"] = (
        "sha256:" + hashlib.sha256(profile_id.encode("utf-8")).hexdigest())
    rearmed_state["auth_profile_allowlist_sha256_rearmed"] = (
        auth_profile_allowlist_sha256)
    rearmed_state["auth_rearm_receipt_sha256"] = (
        "sha256:" + receipt_sha256)
    rearmed_state["rearm_stack_receipt_sha256"] = (
        rearm_stack_receipt_sha256)
    rearmed_state["runtime_binding"] = second_runtime_binding
    rearmed_state["last_order_result"] = "AUTH_REARMED_FLAT"
    rearmed_state["manual_start_required"] = True
    rearmed_state["updated_at"] = dt.datetime.now(
        dt.timezone.utc).isoformat()
    try:
        # Remove the independent exit-75 sentinel while the old state is still
        # latched. If the following atomic state write fails, the restored old
        # state remains sufficient to keep every recovery path fail-closed.
        _remove_safety_exit_latch()
        _verify_waiting_timer(SAFE_RECOVERY_TIMER)
        agent.write_json(AGENT_STATE, rearmed_state)
        os.chown(AGENT_STATE, 0, 0)
        os.chmod(AGENT_STATE, stat.S_IMODE(metadata.st_mode))
    except Exception:
        _restore_root_state_snapshot(
            backup, AGENT_STATE, stat.S_IMODE(metadata.st_mode))
        raise
    print(
        "AUTH_REARM_COMPLETE position=0 active_orders=0 gross=0 "
        f"receipt_sha256={receipt_sha256} auth_generation={auth_generation} "
        "production_canary=ok agent_still_stopped=true",
        flush=True)


def auth_rearm(profile_id: str, auth_generation: str) -> None:
    failure: BaseException | None = None
    with _campaign_lifecycle_locks():
        try:
            _auth_rearm_locked(profile_id, auth_generation)
        except BaseException as error:
            failure = error
    if failure is not None:
        try:
            # A failed admission is terminal for this fresh campaign. Route
            # through the single-flight custodian so it owns the lifecycle
            # lock and can prove zero before revoking every authority.
            _force_safe_recovery_after_admission_failure()
        except BaseException as recovery_error:
            raise RuntimeError(
                "AUTH_REARM_FAILED_RECOVERY_REQUIRED") from recovery_error
        raise failure


def reset_main_state() -> None:
    """Archive a stale recovery record only after authoritative flat proof."""
    agent = load_agent()
    state_path = AGENT_STATE
    current_state = agent.load_state(state_path)
    if (current_state.get("recovery_required") is True or
            current_state.get("trading_suspended") is True or
            current_state.get("pending_order_id") is not None):
        raise RuntimeError("REPAIR_MAIN_STATE_LATCH_REQUIRES_AUTH_REARM")
    arguments = agent_arguments(state_path)
    position, position_generation, cash_generation = authoritative_state(
        agent, arguments)
    risk = agent.tool(arguments, "risk.get_limits")
    gross = float(risk.get("gross_absolute_position", -1.0))
    if not agent._quantity_equal(position, 0.0) or gross != 0.0:
        raise RuntimeError("REPAIR_MAIN_STATE_RESET_REQUIRES_FLAT")
    metadata = os.lstat(state_path)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != 0 or metadata.st_gid != 0):
        raise RuntimeError("REPAIR_MAIN_STATE_PATH_UNSAFE")
    backup = state_path.with_name(
        f"state.pre-strategy-repair-{int(time.time())}.json")
    _copy_root_state_snapshot(state_path, backup, metadata)
    try:
        reset_state = agent.empty_state()
        for key in (
                "auth_generation_rearmed", "auth_profile_sha256_rearmed",
                "auth_profile_allowlist_sha256_rearmed",
                "auth_rearm_receipt_sha256", "runtime_binding"):
            reset_state[key] = current_state.get(key)
        agent.write_json(state_path, reset_state)
        os.chown(state_path, 0, 0)
        os.chmod(state_path, stat.S_IMODE(metadata.st_mode))
    except Exception:
        _restore_root_state_snapshot(
            backup, state_path, stat.S_IMODE(metadata.st_mode))
        raise
    print(
        "REPAIR_MAIN_STATE_RESET "
        f"backup={backup} position=0 active_orders=0 gross=0 "
        f"position_generation={position_generation} "
        f"fx_cash_generation={cash_generation}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    flatten = subparsers.add_parser("flatten-all")
    flatten.add_argument("--initial-wait-seconds", type=float, default=0.0)
    accept = subparsers.add_parser("acceptance")
    accept.add_argument("--initial-wait-seconds", type=float, default=RATE_WINDOW_SECONDS)
    strategy_accept = subparsers.add_parser("strategy-acceptance")
    strategy_accept.add_argument(
        "--initial-wait-seconds", type=float, default=RATE_WINDOW_SECONDS)
    subparsers.add_parser("snapshot")
    subparsers.add_parser("renew-session")
    subparsers.add_parser("rearm-stack")
    subparsers.add_parser("pre-start-guard")
    subparsers.add_parser("reset-main-state")
    risk_recovery = subparsers.add_parser("risk-recover")
    risk_recovery.add_argument("--safety-exit", action="store_true")
    risk_recovery.add_argument("--automatic", action="store_true")
    rearm = subparsers.add_parser("auth-rearm")
    rearm.add_argument("--profile-id", required=True)
    rearm.add_argument("--auth-generation", required=True)
    set_generation = subparsers.add_parser("set-auth-generation")
    set_generation.add_argument("--auth-generation", required=True)
    subparsers.add_parser("start-campaign")
    subparsers.add_parser("request-end-flat")
    subparsers.add_parser("request-end-flat-if-orphan-start")
    # Recovery-only fence for a prepared campaign whose rearm failed before
    # any authority was granted.  Both names are aliases for one state path.
    subparsers.add_parser("abort-prepared")
    subparsers.add_parser("fence-prepared")
    subparsers.add_parser("end-flat-condition")
    subparsers.add_parser("end-flat")
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("REPAIR_ROOT_REQUIRED")
    if arguments.operation == "set-auth-generation":
        set_auth_generation(arguments.auth_generation)
    elif arguments.operation == "flatten-all":
        flatten_all(arguments.initial_wait_seconds)
    elif arguments.operation == "acceptance":
        acceptance(arguments.initial_wait_seconds)
    elif arguments.operation == "strategy-acceptance":
        strategy_acceptance(arguments.initial_wait_seconds)
    elif arguments.operation == "snapshot":
        snapshot()
    elif arguments.operation == "renew-session":
        renew_session()
    elif arguments.operation == "rearm-stack":
        bring_up_rearm_stack()
    elif arguments.operation == "pre-start-guard":
        try:
            pre_start_guard()
        except Exception as error:
            print(str(error), file=sys.stderr, flush=True)
            return 255
    elif arguments.operation == "risk-recover":
        risk_recover(
            safety_exit=arguments.safety_exit,
            automatic=arguments.automatic)
    elif arguments.operation == "auth-rearm":
        auth_rearm(arguments.profile_id, arguments.auth_generation)
    elif arguments.operation == "start-campaign":
        manual_start_campaign()
    elif arguments.operation == "request-end-flat":
        request_end_flat()
    elif arguments.operation == "request-end-flat-if-orphan-start":
        return 0 if request_end_flat_if_orphan_start() else 3
    elif arguments.operation in {"abort-prepared", "fence-prepared"}:
        abort_prepared()
    elif arguments.operation == "end-flat-condition":
        return 0 if end_flat_condition() else 1
    elif arguments.operation == "end-flat":
        end_flat()
    else:
        reset_main_state()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
