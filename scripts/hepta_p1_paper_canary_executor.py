#!/usr/bin/env -S /usr/bin/python3.12 -I -S

"""Pure one-shot executor for the external-P1 EUR.USD PAPER canary.

The executor owns no transport, secret, strategy, or host-control surface.  A
caller injects a backend that already owns the reviewed PAPER session and all
durable artifact I/O.  This module only validates one immutable handoff,
drives its pre-bound call IDs once, and emits non-authorizing evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time
from types import ModuleType
from typing import Any, Mapping, Optional, Protocol


HANDOFF_SCHEMA = "hepta.p1-paper-canary-execution-handoff.v1"
BACKEND_RESPONSE_SCHEMA = "hepta.p1-paper-canary-backend-response.v1"
JOURNAL_SCHEMA = "hepta.p1-paper-canary-execution-journal.v1"
RECOVERY_SCHEMA = "hepta.p1-paper-canary-recovery-record.v1"
CROSS_BINDING_SCHEMA = "hepta.paper-receipt-v2-v3-cross-binding.v1"
RESULT_SCHEMA = "hepta.p1-paper-canary-execution-result.v1"
BACKEND_TRANSFORM_VERSION = "hepta.p1-paper-canary-backend-transform.v1"
VERSION = 1
MAX_BYTES = 1024 * 1024
MAX_QUOTE_AGE_MS = 5_000
MAX_INTENT_HORIZON_MS = 60_000
MAX_NOTIONAL = Decimal("5000")
POLICY_WINDOW_MS = 300_000
ARTIFACT_ROOT = PurePosixPath("/var/lib/hepta/p1-paper-canary")
CONTROL_ROOT = PurePosixPath("/var/lib/hepta/p1-paper-canary-control")
ROOT_FINALIZER_SOCKET = "/run/hepta-p1-paper-canary-finalizer.sock"
PRE_CLEANUP_EVIDENCE_SCHEMA = (
    "hepta.p1-paper-canary-pre-cleanup-flat-evidence.v1")
PRE_CLEANUP_RESPONSE_BUNDLE_SCHEMA = (
    "hepta.p1-paper-canary-pre-cleanup-response-bundle.v1")
ROOT_CLEANUP_REQUEST_SCHEMA = (
    "hepta.p1-paper-canary-root-cleanup-request.v1")
ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-request.v1")
ROOT_EMERGENCY_EVIDENCE_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-evidence.v1")
ROOT_CLEANUP_RECEIPT_SCHEMA = (
    "hepta.p1-paper-canary-root-cleanup-receipt.v4")
ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-receipt.v1")
EXTERNAL_EVENT_SUMMARY_SCHEMA = (
    "hepta.p1-paper-canary-external-p1-event-summary.v1")
COMPOSITE_SNAPSHOT_SCHEMA = (
    "hepta.p1-paper-canary-composite-snapshot.v1")
ROOT_CLEANUP_OPERATION = "FINALIZE_EXTERNAL_P1"
ROOT_CLEANUP_ACTIONS = (
    "FINALIZE_DURABLE_OWNER_POST_FENCE",
    "ACK_PURGE_DURABLE_OWNER",
    "STOP_GUARDIAN", "DISABLE_EXECUTION_CONTROL", "ENGAGE_KILL_SWITCH",
    "ENFORCE_DENY_ALL", "DESTROY_OWNER_CREDENTIALS",
    "PROVE_CONNECTOR_ZERO",
)
ROOT_EMERGENCY_CLEANUP_ACTIONS = (
    "STOP_GUARDIAN", "DISABLE_EXECUTION_CONTROL", "ENGAGE_KILL_SWITCH",
    "ENFORCE_DENY_ALL", "PROVE_CONNECTOR_ZERO",
)
ROOT_CLEANUP_RECEIPT_VERSION = 4
ROOT_CLEANUP_TIMEOUT_MS = 240_000
ROOT_EMERGENCY_CLEANUP_TIMEOUT_MS = 45_000
BROKER_MUTATION_UNITS = (
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
HANDOFF_CREDENTIAL_PATH = Path(
    "/run/credentials/hepta-p1-paper-canary-executor/execution-handoff.v1.json")
EXECUTOR_CREDENTIAL_IMAGE_PATH = Path(
    "/run/credentials/hepta-p1-paper-canary-executor/"
    "hepta-p1-paper-canary-executor.py")
INSTALLED_IMAGE_PATHS = (
    ("executor", "/usr/libexec/hepta-p1-paper-canary-executor"),
    ("receipt-validator-v3", "/usr/libexec/hepta-paper-receipt-contracts"),
    ("receipt-validator-v2", "/usr/libexec/hepta-paper-receipt-contracts-v2-compat"),
    ("backend-adapter", "/usr/libexec/hepta-p1-paper-canary-backend-adapter"),
    ("handoff-producer", "/usr/libexec/hepta-p1-paper-canary-handoff-producer"),
    ("native-tool-client", "/usr/bin/heptactl"),
    ("campaign-operator", "/usr/libexec/hepta-ib-paper-campaign-operator"),
    ("root-finalizer", "/usr/libexec/hepta-p1-paper-canary-finalizer"),
    ("launch-joiner", "/usr/libexec/hepta-p1-paper-canary-launch-joiner"),
    ("owner-provisioner",
     "/usr/libexec/hepta-p1-paper-canary-owner-provisioner"),
    ("root-coordinator",
     "/usr/libexec/hepta-p1-paper-canary-root-coordinator"),
    ("crash-emergency-closer",
     "/usr/libexec/hepta-p1-paper-canary-crash-emergency-closer"),
    ("terminal-prover",
     "/usr/libexec/hepta-p1-paper-canary-terminal-prover"),
)
HISTORICAL_V2_BLOB = "b854aa90eab1cabe8742c99d09253bd337c09613"
HISTORICAL_V2_RAW_SHA256 = (
    "944757976e1a86c2a39f4b800f7987d3b0382e086d90d90b5f3ba6d204692817"
)

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
DOMAIN = re.compile(r"[a-z][a-z0-9-]{0,31}")
TOOL_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,127}")
REASON = re.compile(r"[A-Z][A-Z0-9_]{1,95}")
DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,8})?")

HANDOFF_FIELDS = frozenset({
    "schema", "version", "issued_at_ms", "expires_at_ms",
    "campaign_id", "domain_id", "policy_sha256",
    "source_baseline_sha256", "p1_audit_receipt_sha256",
    "watch_handoff_receipt_file_sha256",
    "watch_handoff_receipt_body_sha256",
    "zero_exposure_attestation_sha256",
    "admission_finalization_receipt_sha256",
    "strategy_id", "strategy_version", "strategy_sha256",
    "decision_id", "decision_sha256", "cycle_id",
    "intent", "intent_sha256", "tool_catalog_sha256",
    "tool_descriptor_set_sha256", "tool_calls", "root_cleanup_call",
    "installed_images", "installed_images_sha256",
    "runtime_profile_reference",
    "backend_transform_version", "execution_service_epoch",
    "execution_service_fencing_generation",
    "session_owner_reference", "paper_only", "live_authorized",
    "direct_broker_access", "authority_granted", "one_order_only",
    "end_flat_required", "body_sha256",
})
INSTALLED_IMAGE_FIELDS = frozenset({
    "role", "path", "file_sha256", "mode", "uid", "gid", "nlink",
})
RUNTIME_PROFILE_FIELDS = frozenset({
    "path", "file_sha256", "size", "mode", "uid", "gid", "nlink",
})
SESSION_OWNER_FIELDS = frozenset({
    "token_name", "token_path", "authority_path", "authority_file_sha256",
    "authority_body_sha256", "lease_generation", "session_id",
    "peer_uid", "peer_gid", "token_sha256", "revoke_bearer_path",
    "revoke_bearer_sha256", "owner_account", "owner_execution_domain",
})
INTENT_FIELDS = frozenset({
    "schema", "paper_only", "strategy_id", "strategy_version",
    "strategy_sha256", "intent_id", "instrument", "symbol", "currency",
    "sec_type", "exchange", "side", "quantity", "order_type", "tif",
    "observed_bid", "observed_ask", "observed_at_ms", "expires_at_ms",
    "entry_thesis", "invalidation_condition", "max_holding_ms",
    "max_adverse_move", "expected_slippage", "exit_plan", "limit_price",
})
PLANNED_CALL_FIELDS = frozenset({
    "call_role", "tool_call_id", "tool_name", "tool_descriptor_sha256",
    "effect", "phase", "command_id",
})
ROOT_CLEANUP_CALL_FIELDS = frozenset({
    "call_role", "tool_name", "operation", "effect", "phase",
    "socket_path", "request_schema", "emergency_request_schema",
    "response_schema", "emergency_response_schema", "tool_call_id", "command_id",
    "tool_descriptor_sha256",
})
BACKEND_RESPONSE_FIELDS = frozenset({
    "schema", "version", "tool_call_id", "tool_name",
    "tool_catalog_sha256", "tool_descriptor_sha256", "status",
    "reason_code", "service_epoch", "fencing_generation", "command_id",
    "adapter_image_sha256", "adapter_transform_version",
    "raw_request_sha256", "raw_response_sha256",
    "normalized_payload_sha256", "payload",
})
BACKEND_STATUSES = frozenset({
    "OK", "REJECTED", "DUPLICATE", "UNCERTAIN", "ERROR",
    "PERMISSION_DENIED", "INVALID_TOOL",
})
TOOL_BINDING_FIELDS = (
    "tool_call_id", "tool_name", "tool_descriptor_sha256", "effect")
TOOL_EVIDENCE_FIELDS = frozenset({
    *TOOL_BINDING_FIELDS, "phase", "request_sha256", "response_sha256",
    "status", "reason_code",
})
FINAL_STATE_FIELDS = frozenset({
    "authoritative", "account_complete", "snapshot_sha256",
    "service_epoch", "fencing_generation", "active_order_id_sha256s",
    "positions", "gross_absolute_position", "authorized_connector_count",
    "end_flat",
})
POSITION_FIELDS = frozenset({"instrument", "quantity"})
PRE_CLEANUP_EVIDENCE_FIELDS = frozenset({
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
ROOT_CLEANUP_REQUEST_FIELDS = frozenset({
    "schema", "version", "issued_at_ms", "expires_at_ms", "campaign_id",
    "domain_id", "cycle_id", "cleanup_tool_call_id", "cleanup_command_id",
    "tool_descriptor_sha256", "handoff_file_sha256", "handoff_body_sha256",
    "session_owner_reference_sha256", "execution_service_epoch",
    "execution_service_fencing_generation", "pre_cleanup_evidence_path",
    "pre_cleanup_evidence_file_sha256",
    "pre_cleanup_evidence_body_sha256", "required_actions", "paper_only",
    "live_authorized", "authority_granted", "body_sha256",
})
ROOT_EMERGENCY_EVIDENCE_FIELDS = frozenset({
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
ROOT_EMERGENCY_CLEANUP_REQUEST_FIELDS = frozenset({
    "schema", "version", "issued_at_ms", "expires_at_ms", "campaign_id",
    "domain_id", "cycle_id", "cleanup_tool_call_id", "cleanup_command_id",
    "tool_descriptor_sha256", "handoff_file_sha256", "handoff_body_sha256",
    "session_owner_reference_sha256", "execution_service_epoch",
    "execution_service_fencing_generation", "emergency_evidence_path",
    "emergency_evidence_file_sha256", "emergency_evidence_body_sha256",
    "recovery_reason_codes", "required_actions", "broker_flat_proven",
    "paper_only", "live_authorized", "authority_granted", "body_sha256",
})
ROOT_CLEANUP_RECEIPT_FIELDS = frozenset({
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
    "guardian_stopped", "execution_control_disabled", "kill_switch_engaged",
    "global_kill_switch_engaged", "broker_deny_all",
    "broker_mutation_units_inactive", "broker_mutation_units",
    "broker_mutation_units_sha256", "permit_absent", "runtime_session_count",
    "guardian_runtime_absent", "authorized_connector_count",
    "identity_count", "identity_manifest_sha256", "broker_policy_sha256",
    "durable_owner_reference_sha256", "durable_owner_count",
    "durable_owner_status", "durable_owner_retirement_receipt_path",
    "durable_owner_retirement_receipt_file_sha256",
    "durable_owner_retirement_receipt_body_sha256",
    "paper_only", "live_authorized", "authority_granted", "body_sha256",
})
ROOT_EMERGENCY_CLEANUP_RECEIPT_FIELDS = frozenset(
    (ROOT_CLEANUP_RECEIPT_FIELDS - {
        "pre_cleanup_evidence_path", "pre_cleanup_evidence_file_sha256",
        "pre_cleanup_evidence_body_sha256",
        "root_cleanup_request_path", "root_cleanup_request_file_sha256",
        "root_cleanup_request_body_sha256",
        "durable_owner_retirement_receipt_path",
        "durable_owner_retirement_receipt_file_sha256",
        "durable_owner_retirement_receipt_body_sha256",
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
    })
EXTERNAL_EVENT_SUMMARY_FIELDS = frozenset({
    "schema", "version", "created_at_ms", "campaign_id", "domain_id",
    "cycle_id", "cycle_opened", "cycle_closed", "place_attempted",
    "close_attempted", "close_outcome", "final_outcome", "reason_codes",
    "pre_cleanup_evidence_file_sha256",
    "pre_cleanup_evidence_body_sha256", "root_cleanup_request_file_sha256",
    "root_cleanup_request_body_sha256", "root_cleanup_receipt_file_sha256",
    "root_cleanup_receipt_body_sha256", "authority_granted", "body_sha256",
})
COMPOSITE_SNAPSHOT_FIELDS = frozenset({
    "schema", "version", "created_at_ms", "campaign_id", "domain_id",
    "cycle_id", "pre_cleanup_broker_state_sha256",
    "pre_cleanup_evidence_file_sha256",
    "pre_cleanup_evidence_body_sha256", "root_cleanup_receipt_file_sha256",
    "root_cleanup_receipt_body_sha256", "authoritative",
    "account_complete", "active_order_id_sha256s", "positions",
    "gross_absolute_position", "authorized_connector_count", "end_flat",
    "service_epoch", "fencing_generation", "authority_granted",
    "body_sha256",
})

JOURNAL_HEADER_FIELDS = frozenset({
    "schema", "version", "record_type", "sequence", "created_at_ms",
    "handoff_file_sha256", "handoff_body_sha256",
    "session_owner_reference_sha256", "authority_granted",
})
JOURNAL_CALL_FIELDS = frozenset({
    "schema", "version", "record_type", "sequence", "recorded_at_ms",
    "call_role", "phase", "event", "tool_call_id", "tool_name",
    "command_id",
    "request_sha256", "response_sha256", "status", "reason_code",
    "service_epoch", "fencing_generation",
    "adapter_image_sha256", "adapter_transform_version",
    "raw_request_sha256", "raw_response_sha256",
    "normalized_payload_sha256",
})
RECOVERY_FIELDS = frozenset({
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
    "root_cleanup_call_id", "root_cleanup_command_id",
    "root_cleanup_attempted", "root_cleanup_mode",
    "root_deny_all_proven", "broker_flat_proven",
    "root_cleanup_evidence_file_sha256",
    "root_cleanup_evidence_body_sha256",
    "root_cleanup_request_file_sha256", "root_cleanup_request_body_sha256",
    "root_cleanup_receipt_schema", "root_cleanup_receipt_status",
    "root_cleanup_receipt_file_sha256", "root_cleanup_receipt_body_sha256",
    "reason_codes", "authority_granted", "body_sha256",
})
CROSS_BINDING_FIELDS = frozenset({
    "schema", "version", "status", "handoff_file_sha256",
    "handoff_body_sha256", "planned_tool_descriptor_set_sha256",
    "installed_images_sha256", "runtime_profile_sha256",
    "backend_transform_version",
    "shared_bindings_sha256", "shared_payload_sha256", "v2", "v3",
    "external_p1_evidence", "v2_compatibility_only",
    "authorization_requirements", "mechanical_transform",
    "authority_granted", "body_sha256",
})
CROSS_VERSION_FIELDS = frozenset({
    "receipt_schema", "receipt_file_sha256", "receipt_payload_sha256",
    "bindings_file_sha256", "bindings_sha256",
    "evidence_bindings_file_sha256", "evidence_bindings_sha256",
})
EXTERNAL_CROSS_FIELDS = frozenset({
    "response_bundle_file_sha256", "response_bundle_body_sha256",
    "pre_cleanup_evidence_file_sha256",
    "pre_cleanup_evidence_body_sha256", "root_cleanup_request_file_sha256",
    "root_cleanup_request_body_sha256", "root_cleanup_receipt_file_sha256",
    "root_cleanup_receipt_body_sha256", "event_summary_file_sha256",
    "event_summary_body_sha256", "composite_snapshot_file_sha256",
    "composite_snapshot_body_sha256",
})

ROLE_PLAN = (
    ("preflight-health", "system.get_health", "READ_ONLY", "PREFLIGHT"),
    ("preflight-quote", "market.get_quote", "READ_ONLY", "PREFLIGHT"),
    ("preflight-account", "account.get_summary", "READ_ONLY", "PREFLIGHT"),
    ("preflight-positions", "portfolio.list_positions", "READ_ONLY", "PREFLIGHT"),
    ("preflight-orders", "orders.list", "READ_ONLY", "PREFLIGHT"),
    ("preflight-risk", "risk.get_limits", "READ_ONLY", "PREFLIGHT"),
    ("preflight-campaign", "campaign.status", "READ_ONLY", "PREFLIGHT"),
    ("open", "campaign.open_cycle", "CONTROL", "OPEN"),
    ("preview-order", "risk.preview_order", "READ_ONLY", "PREVIEW"),
    ("place", "trade.place_order", "MUTATION", "PLACE"),
    ("close", "campaign.close_cycle", "CONTROL", "CLOSE"),
    # These are the authoritative post-CLOSE settlement snapshots used only to
    # choose bounded risk reduction.  Final closure is the RECONCILE phase
    # below, after every possible effectful call.
    ("reconcile-orders", "orders.list", "READ_ONLY", "SNAPSHOT"),
    ("reconcile-positions", "portfolio.list_positions", "READ_ONLY", "SNAPSHOT"),
    ("cancel-order", "trade.cancel_order", "MUTATION", "CANCEL"),
    ("preview-flatten", "risk.preview_flatten", "READ_ONLY", "PREVIEW"),
    ("flatten-position", "trade.flatten_position", "MUTATION", "FLATTEN"),
    ("final-health", "system.get_health", "READ_ONLY", "RECONCILE"),
    ("final-orders", "orders.list", "READ_ONLY", "RECONCILE"),
    ("final-account", "account.get_summary", "READ_ONLY", "RECONCILE"),
    ("final-positions", "portfolio.list_positions", "READ_ONLY", "RECONCILE"),
    ("cleanup-risk", "risk.get_limits", "READ_ONLY", "CLEANUP"),
)
ROLE_PLAN_BY_NAME = {item[0]: item for item in ROLE_PLAN}

HEALTH_FIELDS = frozenset({
    "execution_mode", "paper_account", "connected",
    "authorized_connector_count", "complete",
})
QUOTE_FIELDS = frozenset({
    "instrument", "symbol", "currency", "sec_type", "exchange", "bid",
    "ask", "observed_at_ms", "authoritative", "complete",
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
CAMPAIGN_FIELDS = frozenset({
    "state", "cycle_id", "remaining_cycles", "authority_granted",
})
OPEN_FIELDS = frozenset({
    "opened", "cycle_id", "intent_sha256", "deadline_at_ms",
    "authority_granted",
})
PREVIEW_ORDER_FIELDS = frozenset({
    "approved", "cycle_id", "intent_sha256", "order_request_sha256",
    "authority_granted",
})
PLACE_FIELDS = frozenset({
    "accepted", "cycle_id", "intent_sha256", "order_id_sha256", "owned",
    "authority_granted",
})
CLOSE_FIELDS = frozenset({
    "closed", "cycle_id", "intent_sha256", "outcome", "authority_granted",
})
CANCEL_FIELDS = frozenset({
    "cancelled", "order_id_sha256", "stable_cancel", "authority_granted",
})
PREVIEW_FLATTEN_FIELDS = frozenset({
    "approved", "instrument", "position_quantity", "side", "quantity",
    "order_type", "tif", "limit_price", "observed_bid", "observed_ask",
    "quote_observed_at_ms", "expires_at_ms", "reduce_only", "atomic",
    "authority_granted",
})
FLATTEN_FIELDS = frozenset({
    "flattened", "instrument", "position_quantity", "side", "quantity",
    "order_type", "tif", "limit_price", "quote_observed_at_ms",
    "expires_at_ms", "reduce_only", "atomic", "authority_granted",
})


class CanaryContractError(RuntimeError):
    """The immutable executor contract failed closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class InjectedBackend(Protocol):
    """Operational I/O supplied by the embedding, already-authorized owner."""

    def now_ms(self) -> int: ...
    def read_handoff(self) -> bytes: ...
    def invoke(self, tool_name: str, tool_call_id: str, request: bytes) -> bytes: ...
    def append_journal(self, record: bytes) -> None: ...
    def reopen_journal(self) -> Mapping[str, Any]: ...
    def publish_checkpoint(self, artifacts: Mapping[str, bytes]) -> None: ...
    def finalize_root_cleanup(self, request: bytes) -> bytes: ...
    def publish_artifacts(self, artifacts: Mapping[str, bytes]) -> None: ...


@dataclass(frozen=True)
class Handoff:
    raw: bytes
    document: dict[str, Any]
    file_sha256: str
    body_sha256: str
    calls: dict[str, dict[str, Any]]
    images: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class JournalSnapshot:
    path: str
    raw: bytes
    secure_reopen: bool
    mode: int
    nlink: int


@dataclass(frozen=True)
class BackendResponse:
    document: dict[str, Any]
    raw: bytes
    sha256: str


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    authority_granted: bool
    v2_compatible: bool
    artifacts: Mapping[str, bytes]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _reject_float(_value: str) -> None:
    raise ValueError("JSON floats are forbidden")


def canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise CanaryContractError("NON_CANONICAL_VALUE") from error


def sha256_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


ROOT_CLEANUP_DESCRIPTOR = {
    "schema": "hepta.p1-paper-canary-root-cleanup-operation-descriptor.v1",
    "version": 1,
    "call_role": "cleanup-control",
    "tool_name": "host.finalize_external_p1",
    "operation": ROOT_CLEANUP_OPERATION,
    "effect": "CONTROL",
    "phase": "ROOT_CLEANUP",
    "socket_path": ROOT_FINALIZER_SOCKET,
    "request_schema": ROOT_CLEANUP_REQUEST_SCHEMA,
    "emergency_request_schema": ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA,
    "response_schema": ROOT_CLEANUP_RECEIPT_SCHEMA,
    "emergency_response_schema": ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA,
    "max_request_bytes": MAX_BYTES,
    "max_response_bytes": MAX_BYTES,
    "timeout_ms": ROOT_CLEANUP_TIMEOUT_MS,
    "paper_only": True,
    "live_authorized": False,
    "authority_granted": False,
}
ROOT_CLEANUP_DESCRIPTOR_SHA256 = canonical_sha256(ROOT_CLEANUP_DESCRIPTOR)


def _load_canonical(raw: bytes, label: str) -> dict[str, Any]:
    if not isinstance(raw, bytes) or len(raw) < 3 or len(raw) > MAX_BYTES:
        raise CanaryContractError(f"{label}_SIZE_INVALID")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant, parse_float=_reject_float)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CanaryContractError(f"{label}_JSON_INVALID") from error
    if not isinstance(value, dict):
        raise CanaryContractError(f"{label}_ROOT_INVALID")
    if raw != canonical_json(value):
        raise CanaryContractError(f"{label}_NOT_CANONICAL")
    return value


def _exact(value: Any, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise CanaryContractError(code)
    return value


def _text(
        value: Any, code: str, *, pattern: Optional[re.Pattern[str]] = None,
        maximum: int = 2048) -> str:
    if (
            not isinstance(value, str) or not value or "\0" in value or
            len(value.encode("utf-8", errors="strict")) > maximum or
            (pattern is not None and pattern.fullmatch(value) is None)):
        raise CanaryContractError(code)
    return value


def _identifier(value: Any, code: str) -> str:
    return _text(value, code, pattern=IDENTIFIER, maximum=128)


def _digest(value: Any, code: str, *, nonzero: bool = True) -> str:
    result = _text(value, code, pattern=DIGEST, maximum=71)
    if nonzero and result == "sha256:" + "0" * 64:
        raise CanaryContractError(code)
    return result


def _integer(
        value: Any, code: str, *, minimum: int = 0,
        maximum: int = 2**63 - 1) -> int:
    if (
            isinstance(value, bool) or not isinstance(value, int) or
            value < minimum or value > maximum):
        raise CanaryContractError(code)
    return value


def _boolean(value: Any, code: str) -> bool:
    if not isinstance(value, bool):
        raise CanaryContractError(code)
    return value


def _decimal(value: Any, code: str, *, positive: bool = False) -> str:
    result = _text(value, code, pattern=DECIMAL, maximum=64)
    if positive and all(character in "0." for character in result):
        raise CanaryContractError(code)
    return result


def _absolute_path(value: Any, code: str) -> str:
    result = _text(value, code, maximum=4096)
    path = PurePosixPath(result)
    if (
            not path.is_absolute() or result != path.as_posix() or
            any(part in {"", ".", ".."} for part in path.parts[1:])):
        raise CanaryContractError(code)
    return result


def _sealed(document: dict[str, Any], code: str) -> str:
    claimed = _digest(document.get("body_sha256"), code)
    body = dict(document)
    del body["body_sha256"]
    if canonical_sha256(body) != claimed:
        raise CanaryContractError(code)
    return claimed


def _validate_session_owner(value: Any) -> dict[str, Any]:
    owner = _exact(value, SESSION_OWNER_FIELDS, "HANDOFF_SESSION_OWNER_INVALID")
    _identifier(owner["token_name"], "HANDOFF_SESSION_OWNER_INVALID")
    if owner["token_path"] != "/run/hepta-agent-alpha/sessions/session.token":
        raise CanaryContractError("HANDOFF_SESSION_OWNER_INVALID")
    if owner["authority_path"] != (
            "/var/lib/hepta-local-ai-paper-agent/session-authority/"
            "session.token.authority.json"):
        raise CanaryContractError("HANDOFF_SESSION_OWNER_INVALID")
    _digest(owner["authority_file_sha256"], "HANDOFF_SESSION_OWNER_INVALID")
    _digest(owner["authority_body_sha256"], "HANDOFF_SESSION_OWNER_INVALID")
    _integer(owner["lease_generation"], "HANDOFF_SESSION_OWNER_INVALID", minimum=1)
    _identifier(owner["session_id"], "HANDOFF_SESSION_OWNER_INVALID")
    if owner["peer_uid"] != 2104 or owner["peer_gid"] != 2104:
        raise CanaryContractError("HANDOFF_SESSION_OWNER_INVALID")
    _digest(owner["token_sha256"], "HANDOFF_SESSION_OWNER_INVALID")
    if owner["revoke_bearer_path"] != (
            "/var/lib/hepta-local-ai-paper-agent/session-authority/"
            "session.token.revoke-token"):
        raise CanaryContractError("HANDOFF_SESSION_OWNER_INVALID")
    _digest(owner["revoke_bearer_sha256"], "HANDOFF_SESSION_OWNER_INVALID")
    if owner["revoke_bearer_sha256"] != owner["token_sha256"]:
        raise CanaryContractError("HANDOFF_SESSION_OWNER_BEARER_MISMATCH")
    if (
            not isinstance(owner["owner_account"], str) or
            re.fullmatch(r"DU[0-9]{1,16}", owner["owner_account"]) is None or
            owner["owner_execution_domain"] != "PAPER:alpha"):
        raise CanaryContractError("HANDOFF_SESSION_OWNER_SCOPE_INVALID")
    return owner


def _validate_intent(
        value: Any, handoff: dict[str, Any], *, require_fresh_at_ms: Optional[int]
) -> dict[str, Any]:
    intent = _exact(value, INTENT_FIELDS, "HANDOFF_INTENT_FIELDS_INVALID")
    if intent["schema"] != "hepta.trade-intent.v1" or intent["paper_only"] is not True:
        raise CanaryContractError("HANDOFF_INTENT_PAPER_BOUNDARY_INVALID")
    if (
            intent["strategy_id"] != handoff["strategy_id"] or
            intent["strategy_version"] != handoff["strategy_version"] or
            intent["strategy_sha256"] != handoff["strategy_sha256"]):
        raise CanaryContractError("HANDOFF_INTENT_STRATEGY_MISMATCH")
    _identifier(intent["intent_id"], "HANDOFF_INTENT_ID_INVALID")
    if (
            intent["instrument"] != "EUR.USD" or intent["symbol"] != "EUR" or
            intent["currency"] != "USD" or intent["sec_type"] != "CASH" or
            intent["exchange"] != "IDEALPRO"):
        raise CanaryContractError("HANDOFF_INTENT_INSTRUMENT_INVALID")
    if (
            intent["side"] not in {"BUY", "SELL"} or
            intent["quantity"] != 1 or intent["order_type"] != "LMT" or
            intent["tif"] != "DAY"):
        raise CanaryContractError("HANDOFF_INTENT_ORDER_INVALID")
    bid = _decimal(intent["observed_bid"], "HANDOFF_INTENT_QUOTE_INVALID", positive=True)
    ask = _decimal(intent["observed_ask"], "HANDOFF_INTENT_QUOTE_INVALID", positive=True)
    limit_price = _decimal(intent["limit_price"], "HANDOFF_INTENT_QUOTE_INVALID", positive=True)
    if (
            _decimal_order_key(bid) > _decimal_order_key(ask) or
            limit_price != (ask if intent["side"] == "BUY" else bid)):
        raise CanaryContractError("HANDOFF_INTENT_QUOTE_INVALID")
    if Decimal(limit_price) * Decimal(intent["quantity"]) > MAX_NOTIONAL:
        raise CanaryContractError("HANDOFF_INTENT_NOTIONAL_INVALID")
    observed = _integer(intent["observed_at_ms"], "HANDOFF_INTENT_TIME_INVALID")
    expires = _integer(intent["expires_at_ms"], "HANDOFF_INTENT_TIME_INVALID")
    if (
            expires <= observed or expires - observed > MAX_INTENT_HORIZON_MS or
            expires > handoff["expires_at_ms"]):
        raise CanaryContractError("HANDOFF_INTENT_TIME_INVALID")
    if require_fresh_at_ms is not None and (
            observed > require_fresh_at_ms + 1_000 or
            require_fresh_at_ms - observed > MAX_QUOTE_AGE_MS or
            not require_fresh_at_ms < expires):
        raise CanaryContractError("HANDOFF_INTENT_STALE")
    _integer(intent["max_holding_ms"], "HANDOFF_INTENT_HOLDING_INVALID",
             minimum=1, maximum=MAX_INTENT_HORIZON_MS)
    _decimal(intent["max_adverse_move"], "HANDOFF_INTENT_RISK_INVALID")
    _decimal(intent["expected_slippage"], "HANDOFF_INTENT_RISK_INVALID")
    for field in ("entry_thesis", "invalidation_condition", "exit_plan"):
        _text(intent[field], "HANDOFF_INTENT_REASONING_INVALID", maximum=2048)
    return intent


def _decimal_order_key(value: str) -> tuple[int, str]:
    whole, separator, fraction = value.partition(".")
    normalized = (fraction if separator else "").ljust(8, "0")
    return len(whole), whole + normalized


def validate_handoff(
        raw: bytes, *, now_ms: Optional[int] = None,
        require_fresh: bool = True) -> Handoff:
    document = _load_canonical(raw, "HANDOFF")
    _exact(document, HANDOFF_FIELDS, "HANDOFF_FIELDS_INVALID")
    if document["schema"] != HANDOFF_SCHEMA or document["version"] != VERSION:
        raise CanaryContractError("HANDOFF_IDENTITY_INVALID")
    issued = _integer(document["issued_at_ms"], "HANDOFF_TIME_INVALID")
    expires = _integer(document["expires_at_ms"], "HANDOFF_TIME_INVALID")
    if expires - issued != POLICY_WINDOW_MS:
        raise CanaryContractError("HANDOFF_TIME_INVALID")
    if require_fresh:
        if now_ms is None:
            raise CanaryContractError("HANDOFF_NOW_REQUIRED")
        _integer(now_ms, "HANDOFF_NOW_INVALID")
        if not issued <= now_ms < expires:
            raise CanaryContractError("HANDOFF_EXPIRED")
    _identifier(document["campaign_id"], "HANDOFF_CAMPAIGN_INVALID")
    _text(document["domain_id"], "HANDOFF_DOMAIN_INVALID", pattern=DOMAIN, maximum=32)
    for field in (
            "policy_sha256", "source_baseline_sha256",
            "p1_audit_receipt_sha256",
            "watch_handoff_receipt_file_sha256",
            "watch_handoff_receipt_body_sha256",
            "zero_exposure_attestation_sha256",
            "admission_finalization_receipt_sha256", "strategy_sha256",
            "decision_sha256", "intent_sha256", "tool_catalog_sha256",
            "tool_descriptor_set_sha256"):
        _digest(document[field], f"HANDOFF_{field.upper()}_INVALID")
    _identifier(document["strategy_id"], "HANDOFF_STRATEGY_INVALID")
    _identifier(document["strategy_version"], "HANDOFF_STRATEGY_INVALID")
    _identifier(document["decision_id"], "HANDOFF_DECISION_INVALID")
    _identifier(document["cycle_id"], "HANDOFF_CYCLE_INVALID")
    intent = _validate_intent(
        document["intent"], document,
        require_fresh_at_ms=now_ms if require_fresh else None)
    if canonical_sha256(intent) != document["intent_sha256"]:
        raise CanaryContractError("HANDOFF_INTENT_DIGEST_MISMATCH")
    calls_value = document["tool_calls"]
    if not isinstance(calls_value, list) or len(calls_value) != len(ROLE_PLAN):
        raise CanaryContractError("HANDOFF_TOOL_CALLS_INVALID")
    calls: dict[str, dict[str, Any]] = {}
    call_ids: set[str] = set()
    command_ids: set[str] = set()
    for index, item in enumerate(calls_value):
        call = _exact(item, PLANNED_CALL_FIELDS, "HANDOFF_TOOL_CALL_FIELDS_INVALID")
        role, tool_name, effect, phase = ROLE_PLAN[index]
        if (
                call["call_role"] != role or call["tool_name"] != tool_name or
                call["effect"] != effect or call["phase"] != phase):
            raise CanaryContractError("HANDOFF_TOOL_CALL_PLAN_INVALID")
        call_id = _identifier(call["tool_call_id"], "HANDOFF_TOOL_CALL_ID_INVALID")
        if call_id in call_ids:
            raise CanaryContractError("HANDOFF_TOOL_CALL_ID_DUPLICATE")
        call_ids.add(call_id)
        _text(call["tool_name"], "HANDOFF_TOOL_NAME_INVALID", pattern=TOOL_NAME,
              maximum=128)
        _digest(call["tool_descriptor_sha256"], "HANDOFF_TOOL_DESCRIPTOR_INVALID")
        command_id = call["command_id"]
        if effect == "READ_ONLY":
            if command_id is not None:
                raise CanaryContractError("HANDOFF_READ_ONLY_COMMAND_ID_FORBIDDEN")
        else:
            command_id = _identifier(
                command_id, "HANDOFF_COMMAND_ID_INVALID")
            if command_id != call_id:
                raise CanaryContractError("HANDOFF_COMMAND_CALL_ID_MISMATCH")
            if command_id in command_ids:
                raise CanaryContractError("HANDOFF_COMMAND_ID_DUPLICATE")
            command_ids.add(command_id)
        calls[role] = call
    root_cleanup = _exact(
        document["root_cleanup_call"], ROOT_CLEANUP_CALL_FIELDS,
        "HANDOFF_ROOT_CLEANUP_CALL_INVALID")
    expected_root_cleanup = {
        "call_role": "cleanup-control",
        "tool_name": "host.finalize_external_p1",
        "operation": ROOT_CLEANUP_OPERATION,
        "effect": "CONTROL",
        "phase": "ROOT_CLEANUP",
        "socket_path": ROOT_FINALIZER_SOCKET,
        "request_schema": ROOT_CLEANUP_REQUEST_SCHEMA,
        "emergency_request_schema": ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA,
        "response_schema": ROOT_CLEANUP_RECEIPT_SCHEMA,
        "emergency_response_schema": ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA,
        "tool_call_id": root_cleanup["tool_call_id"],
        "command_id": root_cleanup["command_id"],
        "tool_descriptor_sha256": ROOT_CLEANUP_DESCRIPTOR_SHA256,
    }
    root_call_id = _identifier(
        root_cleanup["tool_call_id"],
        "HANDOFF_ROOT_CLEANUP_CALL_ID_INVALID")
    root_command_id = _identifier(
        root_cleanup["command_id"],
        "HANDOFF_ROOT_CLEANUP_COMMAND_ID_INVALID")
    if (
            root_cleanup != expected_root_cleanup or
            root_call_id != root_command_id or root_call_id in call_ids or
            root_command_id in command_ids):
        raise CanaryContractError("HANDOFF_ROOT_CLEANUP_CALL_INVALID")
    call_ids.add(root_call_id)
    command_ids.add(root_command_id)
    projection = [
        {field: call[field] for field in TOOL_BINDING_FIELDS}
        for call in calls_value
    ]
    if canonical_sha256(projection) != document["tool_descriptor_set_sha256"]:
        raise CanaryContractError("HANDOFF_DESCRIPTOR_SET_DIGEST_MISMATCH")
    images_value = document["installed_images"]
    if (
            not isinstance(images_value, list) or
            len(images_value) != len(INSTALLED_IMAGE_PATHS)):
        raise CanaryContractError("HANDOFF_INSTALLED_IMAGES_INVALID")
    images: dict[str, dict[str, Any]] = {}
    for item, (expected_role, expected_path) in zip(
            images_value, INSTALLED_IMAGE_PATHS):
        image = _exact(
            item, INSTALLED_IMAGE_FIELDS,
            "HANDOFF_INSTALLED_IMAGE_FIELDS_INVALID")
        if image["role"] != expected_role or image["path"] != expected_path:
            raise CanaryContractError("HANDOFF_INSTALLED_IMAGE_PATH_INVALID")
        _digest(image["file_sha256"], "HANDOFF_INSTALLED_IMAGE_DIGEST_INVALID")
        if (
                _integer(image["mode"], "HANDOFF_INSTALLED_IMAGE_MODE_INVALID")
                != 0o755 or
                _integer(image["uid"], "HANDOFF_INSTALLED_IMAGE_UID_INVALID") < 0 or
                _integer(image["gid"], "HANDOFF_INSTALLED_IMAGE_GID_INVALID") < 0 or
                _integer(image["nlink"], "HANDOFF_INSTALLED_IMAGE_NLINK_INVALID")
                != 1):
            raise CanaryContractError("HANDOFF_INSTALLED_IMAGE_METADATA_INVALID")
        images[expected_role] = image
    if images["receipt-validator-v2"]["file_sha256"] != (
            "sha256:" + HISTORICAL_V2_RAW_SHA256):
        raise CanaryContractError("HANDOFF_V2_VALIDATOR_IMAGE_INVALID")
    if canonical_sha256(images_value) != document["installed_images_sha256"]:
        raise CanaryContractError("HANDOFF_INSTALLED_IMAGES_DIGEST_MISMATCH")
    profile = _exact(
        document["runtime_profile_reference"], RUNTIME_PROFILE_FIELDS,
        "HANDOFF_RUNTIME_PROFILE_INVALID")
    if (
            profile["path"] !=
                f"/etc/heptatrader/trust-domains/{document['domain_id']}.ib-paper.env" or
            _digest(profile["file_sha256"], "HANDOFF_RUNTIME_PROFILE_INVALID")
                != profile["file_sha256"] or
            _integer(profile["size"], "HANDOFF_RUNTIME_PROFILE_INVALID",
                     minimum=1, maximum=65536) < 1 or
            _integer(profile["mode"], "HANDOFF_RUNTIME_PROFILE_INVALID")
                != 0o644 or
            _integer(profile["uid"], "HANDOFF_RUNTIME_PROFILE_INVALID") != 0 or
            _integer(profile["gid"], "HANDOFF_RUNTIME_PROFILE_INVALID") != 0 or
            _integer(profile["nlink"], "HANDOFF_RUNTIME_PROFILE_INVALID") != 1):
        raise CanaryContractError("HANDOFF_RUNTIME_PROFILE_INVALID")
    if document["backend_transform_version"] != BACKEND_TRANSFORM_VERSION:
        raise CanaryContractError("HANDOFF_BACKEND_TRANSFORM_INVALID")
    _identifier(
        document["execution_service_epoch"],
        "HANDOFF_EXECUTION_SERVICE_EPOCH_INVALID")
    _integer(
        document["execution_service_fencing_generation"],
        "HANDOFF_EXECUTION_SERVICE_FENCE_INVALID", minimum=1)
    owner = _validate_session_owner(document["session_owner_reference"])
    if owner["token_path"] != (
            f"/run/hepta-agent-{document['domain_id']}/sessions/"
            f"{owner['token_name']}"):
        raise CanaryContractError("HANDOFF_SESSION_OWNER_TOKEN_PATH_INVALID")
    if owner["token_name"] != "session.token":
        raise CanaryContractError("HANDOFF_SESSION_OWNER_TOKEN_NAME_INVALID")
    if not (
            document["paper_only"] is True and
            document["live_authorized"] is False and
            document["direct_broker_access"] is False and
            document["authority_granted"] is False and
            document["one_order_only"] is True and
            document["end_flat_required"] is True):
        raise CanaryContractError("HANDOFF_AUTHORITY_BOUNDARY_INVALID")
    body_sha256 = _sealed(document, "HANDOFF_BODY_DIGEST_INVALID")
    return Handoff(
        raw=raw, document=document, file_sha256=sha256_bytes(raw),
        body_sha256=body_sha256, calls=calls, images=images)


def _journal_snapshot(backend: InjectedBackend) -> JournalSnapshot:
    value = backend.reopen_journal()
    if not isinstance(value, Mapping) or set(value) != {
            "path", "raw", "secure_reopen", "mode", "nlink"}:
        raise CanaryContractError("JOURNAL_SNAPSHOT_INVALID")
    path = _absolute_path(value["path"], "JOURNAL_PATH_INVALID")
    raw = value["raw"]
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise CanaryContractError("JOURNAL_SIZE_INVALID")
    secure = _boolean(value["secure_reopen"], "JOURNAL_REOPEN_INVALID")
    mode = _integer(value["mode"], "JOURNAL_METADATA_INVALID", maximum=0o7777)
    nlink = _integer(value["nlink"], "JOURNAL_METADATA_INVALID", minimum=1)
    if not secure or mode != 0o600 or nlink != 1:
        raise CanaryContractError("JOURNAL_METADATA_INVALID")
    return JournalSnapshot(path, raw, secure, mode, nlink)


def journal_path_for(handoff: Handoff) -> str:
    return (ARTIFACT_ROOT / handoff.document["campaign_id"] /
            handoff.document["cycle_id"] /
            "execution-journal.v1.jsonl").as_posix()


def handoff_path_for(handoff: Handoff) -> str:
    return (CONTROL_ROOT / handoff.document["campaign_id"] /
            handoff.document["cycle_id"] /
            "execution-handoff.v1.json").as_posix()


def pre_cleanup_evidence_path_for(handoff: Handoff) -> str:
    return (ARTIFACT_ROOT / handoff.document["campaign_id"] /
            handoff.document["cycle_id"] /
            "pre-cleanup-flat-evidence.v1.json").as_posix()


def pre_cleanup_response_bundle_path_for(handoff: Handoff) -> str:
    return (ARTIFACT_ROOT / handoff.document["campaign_id"] /
            handoff.document["cycle_id"] /
            "pre-cleanup-response-bundle.v1.json").as_posix()


def root_cleanup_request_path_for(handoff: Handoff) -> str:
    return (ARTIFACT_ROOT / handoff.document["campaign_id"] /
            handoff.document["cycle_id"] /
            "root-cleanup-request.v1.json").as_posix()


def root_cleanup_receipt_path_for(handoff: Handoff) -> str:
    return (CONTROL_ROOT / handoff.document["campaign_id"] /
            handoff.document["cycle_id"] /
            "root-cleanup-receipt.v4.json").as_posix()


def durable_owner_retirement_receipt_path_for(handoff: Handoff) -> str:
    return (CONTROL_ROOT / handoff.document["campaign_id"] /
            handoff.document["cycle_id"] /
            "durable-owner-retirement-receipt.v4.json").as_posix()


def emergency_cleanup_evidence_path_for(handoff: Handoff) -> str:
    return (ARTIFACT_ROOT / handoff.document["campaign_id"] /
            handoff.document["cycle_id"] /
            "root-emergency-cleanup-evidence.v1.json").as_posix()


def root_emergency_cleanup_request_path_for(handoff: Handoff) -> str:
    return (ARTIFACT_ROOT / handoff.document["campaign_id"] /
            handoff.document["cycle_id"] /
            "root-emergency-cleanup-request.v1.json").as_posix()


def root_emergency_cleanup_receipt_path_for(handoff: Handoff) -> str:
    return (CONTROL_ROOT / handoff.document["campaign_id"] /
            handoff.document["cycle_id"] /
            "root-emergency-cleanup-receipt.v1.json").as_posix()


def durable_recovery_owner_reference_path_for(handoff: Handoff) -> str:
    return (CONTROL_ROOT / handoff.document["campaign_id"] /
            handoff.document["cycle_id"] /
            "durable-recovery-owner-reference.v1.json").as_posix()


def recovery_record_path_for(handoff: Handoff) -> str:
    return (ARTIFACT_ROOT / handoff.document["campaign_id"] /
            handoff.document["cycle_id"] /
            "recovery-record.v1.json").as_posix()


def _parse_journal(raw: bytes, handoff: Handoff) -> list[dict[str, Any]]:
    if not raw or len(raw) > MAX_BYTES or not raw.endswith(b"\n"):
        raise CanaryContractError("JOURNAL_FRAME_INVALID")
    records: list[dict[str, Any]] = []
    for line in raw.splitlines(keepends=True):
        record = _load_canonical(line, "JOURNAL_RECORD")
        records.append(record)
    header = _exact(records[0], JOURNAL_HEADER_FIELDS, "JOURNAL_HEADER_INVALID")
    if (
            header["schema"] != JOURNAL_SCHEMA or header["version"] != VERSION or
            header["record_type"] != "HEADER" or header["sequence"] != 0 or
            header["handoff_file_sha256"] != handoff.file_sha256 or
            header["handoff_body_sha256"] != handoff.body_sha256 or
            header["session_owner_reference_sha256"] !=
                canonical_sha256(handoff.document["session_owner_reference"]) or
            header["authority_granted"] is not False):
        raise CanaryContractError("JOURNAL_HEADER_INVALID")
    _integer(header["created_at_ms"], "JOURNAL_HEADER_INVALID")
    pending: dict[str, str] = {}
    completed: set[str] = set()
    for sequence, record_value in enumerate(records[1:], 1):
        record = _exact(record_value, JOURNAL_CALL_FIELDS, "JOURNAL_CALL_INVALID")
        if (
                record["schema"] != JOURNAL_SCHEMA or record["version"] != VERSION or
                record["record_type"] != "CALL" or record["sequence"] != sequence):
            raise CanaryContractError("JOURNAL_SEQUENCE_INVALID")
        _integer(record["recorded_at_ms"], "JOURNAL_CALL_INVALID")
        role = record["call_role"]
        if role not in handoff.calls:
            raise CanaryContractError("JOURNAL_CALL_ROLE_INVALID")
        binding = handoff.calls[role]
        if (
                record["phase"] != binding["phase"] or
                record["tool_call_id"] != binding["tool_call_id"] or
                record["tool_name"] != binding["tool_name"] or
                record["command_id"] != binding["command_id"] or
                record["adapter_image_sha256"] != handoff.images[
                    "backend-adapter"]["file_sha256"] or
                record["adapter_transform_version"] !=
                    BACKEND_TRANSFORM_VERSION):
            raise CanaryContractError("JOURNAL_CALL_BINDING_MISMATCH")
        _digest(record["request_sha256"], "JOURNAL_REQUEST_DIGEST_INVALID")
        if record["event"] == "REQUEST":
            if (
                    role in pending or role in completed or
                    record["response_sha256"] is not None or
                    record["status"] != "PENDING" or
                    record["reason_code"] != "" or
                    record["service_epoch"] is not None or
                    record["fencing_generation"] is not None or
                    record["raw_request_sha256"] is not None or
                    record["raw_response_sha256"] is not None or
                    record["normalized_payload_sha256"] is not None):
                raise CanaryContractError("JOURNAL_REQUEST_INVALID")
            pending[role] = record["request_sha256"]
        elif record["event"] == "RESPONSE":
            if pending.get(role) != record["request_sha256"]:
                raise CanaryContractError("JOURNAL_RESPONSE_WITHOUT_REQUEST")
            _digest(record["response_sha256"], "JOURNAL_RESPONSE_DIGEST_INVALID")
            if record["status"] not in BACKEND_STATUSES:
                raise CanaryContractError("JOURNAL_RESPONSE_STATUS_INVALID")
            if record["status"] == "OK":
                if record["reason_code"] not in {"", "OK"}:
                    raise CanaryContractError("JOURNAL_RESPONSE_REASON_INVALID")
            else:
                _text(record["reason_code"], "JOURNAL_RESPONSE_REASON_INVALID",
                      pattern=REASON, maximum=96)
            _identifier(record["service_epoch"], "JOURNAL_RESPONSE_EPOCH_INVALID")
            _integer(record["fencing_generation"],
                     "JOURNAL_RESPONSE_FENCE_INVALID", minimum=1)
            for field in (
                    "raw_request_sha256", "raw_response_sha256",
                    "normalized_payload_sha256"):
                _digest(record[field], "JOURNAL_ADAPTER_EVIDENCE_INVALID")
            del pending[role]
            completed.add(role)
        else:
            raise CanaryContractError("JOURNAL_EVENT_INVALID")
    return records


def _journal_evidence(
        records: list[dict[str, Any]], handoff: Handoff) -> list[dict[str, Any]]:
    requests: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    for record in records[1:]:
        role = record["call_role"]
        if record["event"] == "REQUEST":
            requests[role] = record
            continue
        request = requests.get(role)
        if request is None:
            raise CanaryContractError("JOURNAL_RESPONSE_WITHOUT_REQUEST")
        binding = handoff.calls[role]
        evidence.append({
            "tool_call_id": binding["tool_call_id"],
            "tool_name": binding["tool_name"],
            "tool_descriptor_sha256": binding["tool_descriptor_sha256"],
            "effect": binding["effect"], "phase": binding["phase"],
            "request_sha256": record["request_sha256"],
            "response_sha256": record["response_sha256"],
            "status": record["status"], "reason_code": record["reason_code"],
        })
    return evidence


def _stable_image_bytes(
        path: Path, expected: dict[str, Any], *, exact_raw_sha256: Optional[str] = None
) -> bytes:
    try:
        before = os.lstat(path)
        if (
                stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                stat.S_IMODE(before.st_mode) & 0o022 or
                before.st_size < 1 or before.st_size > MAX_BYTES):
            raise CanaryContractError("RECEIPT_VALIDATOR_IMAGE_UNSAFE")
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            raw = bytearray()
            while len(raw) <= MAX_BYTES:
                chunk = os.read(
                    descriptor, min(65536, MAX_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CanaryContractError("RECEIPT_VALIDATOR_IMAGE_UNAVAILABLE") from error
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
        item.st_uid, item.st_gid, item.st_size, item.st_mtime_ns,
        item.st_ctime_ns)
    if (
            len(raw) > MAX_BYTES or identity(before) != identity(opened) or
            identity(opened) != identity(after)):
        raise CanaryContractError("RECEIPT_VALIDATOR_IMAGE_CHANGED")
    payload = bytes(raw)
    digest = hashlib.sha256(payload).hexdigest()
    if "sha256:" + digest != expected["file_sha256"]:
        raise CanaryContractError("RECEIPT_VALIDATOR_IMAGE_DIGEST_MISMATCH")
    if exact_raw_sha256 is not None and digest != exact_raw_sha256:
        raise CanaryContractError("RECEIPT_VALIDATOR_HISTORICAL_BLOB_MISMATCH")
    # Source-tree execution is an offline test/development seam.  Installed
    # execution additionally pins the producer-attested root image metadata.
    if str(path) == expected["path"] and (
            stat.S_IMODE(after.st_mode) != expected["mode"] or
            after.st_uid != expected["uid"] or after.st_gid != expected["gid"] or
            after.st_nlink != expected["nlink"]):
        raise CanaryContractError("RECEIPT_VALIDATOR_IMAGE_METADATA_MISMATCH")
    return payload


def _validate_running_executor_image(expected: dict[str, Any]) -> bytes:
    runtime_path = Path(__file__)
    if runtime_path == EXECUTOR_CREDENTIAL_IMAGE_PATH:
        installed = _stable_image_bytes(Path(expected["path"]), expected)
        credential = _stable_image_bytes(runtime_path, expected)
        metadata = os.lstat(runtime_path)
        if (
                metadata.st_uid != os.geteuid() or
                stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600} or
                metadata.st_nlink != 1 or credential != installed):
            raise CanaryContractError("EXECUTOR_CREDENTIAL_IMAGE_INVALID")
        return credential
    # Source-tree loading is retained solely as an offline test seam.
    return _stable_image_bytes(runtime_path.resolve(), expected)


def _load_contract_module(
        name: str, candidates: tuple[str, ...], expected: dict[str, Any], *,
        exact_raw_sha256: Optional[str] = None) -> ModuleType:
    directory = Path(__file__).resolve().parent
    for candidate in candidates:
        path = directory / candidate
        if not path.is_file():
            continue
        raw = _stable_image_bytes(
            path, expected, exact_raw_sha256=exact_raw_sha256)
        module = ModuleType(name)
        module.__file__ = str(path)
        try:
            exec(compile(raw, str(path), "exec"), module.__dict__)
        except Exception as error:
            raise CanaryContractError("RECEIPT_VALIDATOR_IMPORT_FAILED") from error
        return module
    raise CanaryContractError("RECEIPT_VALIDATOR_UNAVAILABLE")


def _receipt_modules(handoff: Handoff) -> tuple[ModuleType, ModuleType]:
    current = _load_contract_module(
        "_hepta_paper_receipt_contracts_v3_executor",
        ("hepta_paper_receipt_contracts.py", "hepta-paper-receipt-contracts"),
        handoff.images["receipt-validator-v3"])
    compatibility = _load_contract_module(
        "_hepta_paper_receipt_contracts_v2_compat_executor",
        ("hepta_paper_receipt_contracts_v2_compat.py",
         "hepta-paper-receipt-contracts-v2-compat"),
        handoff.images["receipt-validator-v2"],
        exact_raw_sha256=HISTORICAL_V2_RAW_SHA256)
    if (
            getattr(current, "VERSION", None) != 3 or
            getattr(current, "TOOL_POLICY_VERSION", None) != 2 or
            getattr(compatibility, "VERSION", None) != 2 or
            getattr(compatibility, "TOOL_POLICY_VERSION", None) != 1):
        raise CanaryContractError("RECEIPT_VALIDATOR_IDENTITY_INVALID")
    return current, compatibility


def _reason_list(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        _text(value, "REASON_CODE_INVALID", pattern=REASON, maximum=96)
        if value not in result:
            result.append(value)
    return result


class _Run:
    def __init__(self, handoff: Handoff, backend: InjectedBackend, now_ms: int) -> None:
        self.handoff = handoff
        self.backend = backend
        self.started_at_ms = now_ms
        self.sequence = 0
        self.evidence: list[dict[str, Any]] = []
        self.responses: dict[str, BackendResponse] = {}
        self.reasons: list[str] = []
        self.service_epoch: Optional[str] = handoff.document[
            "execution_service_epoch"]
        self.fencing_generation = handoff.document[
            "execution_service_fencing_generation"]
        self.last_completed_phase = "NONE"
        self.uncertain_phase: Optional[str] = None
        self.uncertain_tool_call_id: Optional[str] = None
        self.place_attempted = False
        self.close_attempted = False
        self.cycle_opened = False
        self.cycle_closed = False
        self.close_outcome: Optional[str] = None
        self.preview_sha256: Optional[str] = None
        self.order_id_sha256: Optional[str] = None
        self.owned_order_id_sha256: Optional[str] = None
        self.last_state = self._empty_state(authoritative=False, account_complete=False)
        self.pre_cleanup_state: Optional[dict[str, Any]] = None
        self.pre_cleanup_response_bundle: Optional[dict[str, Any]] = None
        self.pre_cleanup_response_bundle_raw: Optional[bytes] = None
        self.pre_cleanup_evidence: Optional[dict[str, Any]] = None
        self.pre_cleanup_evidence_raw: Optional[bytes] = None
        self.root_cleanup_request: Optional[dict[str, Any]] = None
        self.root_cleanup_request_raw: Optional[bytes] = None
        self.root_cleanup_receipt: Optional[dict[str, Any]] = None
        self.root_cleanup_receipt_raw: Optional[bytes] = None
        self.emergency_cleanup_evidence: Optional[dict[str, Any]] = None
        self.emergency_cleanup_evidence_raw: Optional[bytes] = None
        self.root_emergency_cleanup_request: Optional[dict[str, Any]] = None
        self.root_emergency_cleanup_request_raw: Optional[bytes] = None
        self.root_emergency_cleanup_receipt: Optional[dict[str, Any]] = None
        self.root_emergency_cleanup_receipt_raw: Optional[bytes] = None
        self.composite_snapshot: Optional[dict[str, Any]] = None
        self.composite_snapshot_raw: Optional[bytes] = None
        self.root_cleanup_attempted = False
        self.root_cleanup_complete = False
        self.recovery_used = False

    def now_ms(self) -> int:
        value = self.backend.now_ms()
        return _integer(value, "BACKEND_NOW_INVALID")

    def _empty_state(self, *, authoritative: bool, account_complete: bool) -> dict[str, Any]:
        epoch = self.service_epoch or "unobserved"
        state_seed = {
            "authoritative": authoritative, "account_complete": account_complete,
            "service_epoch": epoch, "fencing_generation": self.fencing_generation,
            "active_order_id_sha256s": [], "positions": [],
            "gross_absolute_position": 0, "authorized_connector_count": 0,
            "end_flat": True,
        }
        return {**state_seed, "snapshot_sha256": canonical_sha256(state_seed)}

    def append_header(self) -> None:
        header = {
            "schema": JOURNAL_SCHEMA, "version": VERSION,
            "record_type": "HEADER", "sequence": 0,
            "created_at_ms": self.started_at_ms,
            "handoff_file_sha256": self.handoff.file_sha256,
            "handoff_body_sha256": self.handoff.body_sha256,
            "session_owner_reference_sha256": canonical_sha256(
                self.handoff.document["session_owner_reference"]),
            "authority_granted": False,
        }
        self._append_record(header)

    def _append_record(self, record: dict[str, Any]) -> None:
        self.backend.append_journal(canonical_json(record))
        snapshot = _journal_snapshot(self.backend)
        if snapshot.path != journal_path_for(self.handoff):
            raise CanaryContractError("JOURNAL_PATH_MISMATCH")
        records = _parse_journal(snapshot.raw, self.handoff)
        if records[-1] != record:
            raise CanaryContractError("JOURNAL_APPEND_NOT_DURABLE")

    def _request(self, role: str, arguments: dict[str, Any]) -> tuple[bytes, str]:
        call = self.handoff.calls[role]
        document = {
            "schema": "hepta.p1-paper-canary-backend-request.v1",
            "version": 1, "tool_call_id": call["tool_call_id"],
            "tool_name": call["tool_name"],
            "command_id": call["command_id"],
            "campaign_id": self.handoff.document["campaign_id"],
            "domain_id": self.handoff.document["domain_id"],
            "policy_sha256": self.handoff.document["policy_sha256"],
            "strategy_sha256": self.handoff.document["strategy_sha256"],
            "decision_sha256": self.handoff.document["decision_sha256"],
            "cycle_id": self.handoff.document["cycle_id"],
            "intent_sha256": self.handoff.document["intent_sha256"],
            "tool_catalog_sha256": self.handoff.document["tool_catalog_sha256"],
            "tool_descriptor_sha256": call["tool_descriptor_sha256"],
            "session_owner_reference_sha256": canonical_sha256(
                self.handoff.document["session_owner_reference"]),
            "paper_only": True, "live_authorized": False,
            "direct_broker_access": False, "authority_granted": False,
            "arguments": arguments,
        }
        raw = canonical_json(document)
        return raw, sha256_bytes(raw)

    def _synthetic_uncertain(
            self, role: str, code: str, request_sha256: str) -> BackendResponse:
        call = self.handoff.calls[role]
        error_document = {
            "schema": "hepta.p1-paper-canary-backend-uncertainty.v1",
            "tool_call_id": call["tool_call_id"], "tool_name": call["tool_name"],
            "request_sha256": request_sha256, "reason_code": code,
        }
        raw = canonical_json(error_document)
        empty_payload_sha256 = canonical_sha256({})
        document = {
            "schema": BACKEND_RESPONSE_SCHEMA, "version": 1,
            "tool_call_id": call["tool_call_id"], "tool_name": call["tool_name"],
            "command_id": call["command_id"],
            "tool_catalog_sha256": self.handoff.document["tool_catalog_sha256"],
            "tool_descriptor_sha256": call["tool_descriptor_sha256"],
            "status": "UNCERTAIN", "reason_code": code,
            "service_epoch": self.service_epoch or "unobserved",
            "fencing_generation": self.fencing_generation,
            "adapter_image_sha256": self.handoff.images[
                "backend-adapter"]["file_sha256"],
            "adapter_transform_version": BACKEND_TRANSFORM_VERSION,
            "raw_request_sha256": request_sha256,
            "raw_response_sha256": sha256_bytes(raw),
            "normalized_payload_sha256": empty_payload_sha256,
            "payload": {},
        }
        normalized = canonical_json(document)
        return BackendResponse(
            document=document, raw=normalized, sha256=sha256_bytes(normalized))

    def _validate_backend_response(self, role: str, raw: bytes) -> BackendResponse:
        document = _load_canonical(raw, "BACKEND_RESPONSE")
        _exact(document, BACKEND_RESPONSE_FIELDS, "BACKEND_RESPONSE_FIELDS_INVALID")
        call = self.handoff.calls[role]
        if (
                document["schema"] != BACKEND_RESPONSE_SCHEMA or
                document["version"] != VERSION or
                document["tool_call_id"] != call["tool_call_id"] or
                document["tool_name"] != call["tool_name"] or
                document["command_id"] != call["command_id"] or
                document["tool_catalog_sha256"] !=
                    self.handoff.document["tool_catalog_sha256"] or
                document["tool_descriptor_sha256"] !=
                    call["tool_descriptor_sha256"] or
                document["adapter_image_sha256"] != self.handoff.images[
                    "backend-adapter"]["file_sha256"] or
                document["adapter_transform_version"] !=
                    BACKEND_TRANSFORM_VERSION):
            raise CanaryContractError("BACKEND_RESPONSE_PIN_MISMATCH")
        if document["status"] not in BACKEND_STATUSES:
            raise CanaryContractError("BACKEND_RESPONSE_STATUS_INVALID")
        if document["status"] == "OK":
            if document["reason_code"] not in {"", "OK"}:
                raise CanaryContractError("BACKEND_RESPONSE_REASON_INVALID")
        else:
            _text(document["reason_code"], "BACKEND_RESPONSE_REASON_INVALID",
                  pattern=REASON, maximum=96)
        _identifier(document["service_epoch"], "BACKEND_RESPONSE_EPOCH_INVALID")
        _integer(document["fencing_generation"],
                 "BACKEND_RESPONSE_FENCE_INVALID", minimum=1)
        for field in (
                "raw_request_sha256", "raw_response_sha256",
                "normalized_payload_sha256"):
            _digest(document[field], "BACKEND_RESPONSE_EVIDENCE_DIGEST_INVALID")
        if not isinstance(document["payload"], dict):
            raise CanaryContractError("BACKEND_RESPONSE_PAYLOAD_INVALID")
        if canonical_sha256(document["payload"]) != document[
                "normalized_payload_sha256"]:
            raise CanaryContractError("BACKEND_RESPONSE_NORMALIZATION_MISMATCH")
        return BackendResponse(document=document, raw=raw, sha256=sha256_bytes(raw))

    def call(self, role: str, arguments: dict[str, Any]) -> BackendResponse:
        call = self.handoff.calls[role]
        request_raw, request_sha256 = self._request(role, arguments)
        self.sequence += 1
        requested = {
            "schema": JOURNAL_SCHEMA, "version": VERSION,
            "record_type": "CALL", "sequence": self.sequence,
            "recorded_at_ms": self.now_ms(), "call_role": role,
            "phase": call["phase"], "event": "REQUEST",
            "tool_call_id": call["tool_call_id"], "tool_name": call["tool_name"],
            "command_id": call["command_id"],
            "request_sha256": request_sha256, "response_sha256": None,
            "status": "PENDING", "reason_code": "", "service_epoch": None,
            "fencing_generation": None,
            "adapter_image_sha256": self.handoff.images[
                "backend-adapter"]["file_sha256"],
            "adapter_transform_version": BACKEND_TRANSFORM_VERSION,
            "raw_request_sha256": None, "raw_response_sha256": None,
            "normalized_payload_sha256": None,
        }
        self._append_record(requested)
        if role == "place":
            self.place_attempted = True
        if role == "close":
            self.close_attempted = True
        try:
            response = self._validate_backend_response(
                role, self.backend.invoke(
                    call["tool_name"], call["tool_call_id"], request_raw))
        except Exception as error:
            code = error.code if isinstance(error, CanaryContractError) else \
                "BACKEND_CALL_UNCERTAIN"
            if REASON.fullmatch(code) is None:
                code = "BACKEND_CALL_UNCERTAIN"
            response = self._synthetic_uncertain(role, code, request_sha256)
        self.sequence += 1
        response_document = response.document
        responded = {
            "schema": JOURNAL_SCHEMA, "version": VERSION,
            "record_type": "CALL", "sequence": self.sequence,
            "recorded_at_ms": self.now_ms(), "call_role": role,
            "phase": call["phase"], "event": "RESPONSE",
            "tool_call_id": call["tool_call_id"], "tool_name": call["tool_name"],
            "command_id": call["command_id"],
            "request_sha256": request_sha256, "response_sha256": response.sha256,
            "status": response_document["status"],
            "reason_code": response_document["reason_code"],
            "service_epoch": response_document["service_epoch"],
            "fencing_generation": response_document["fencing_generation"],
            "adapter_image_sha256": response_document[
                "adapter_image_sha256"],
            "adapter_transform_version": response_document[
                "adapter_transform_version"],
            "raw_request_sha256": response_document["raw_request_sha256"],
            "raw_response_sha256": response_document["raw_response_sha256"],
            "normalized_payload_sha256": response_document[
                "normalized_payload_sha256"],
        }
        self._append_record(responded)
        evidence = {
            "tool_call_id": call["tool_call_id"], "tool_name": call["tool_name"],
            "tool_descriptor_sha256": call["tool_descriptor_sha256"],
            "effect": call["effect"], "phase": call["phase"],
            "request_sha256": request_sha256,
            "response_sha256": response.sha256,
            "status": response_document["status"],
            "reason_code": response_document["reason_code"],
        }
        self.evidence.append(evidence)
        self.responses[role] = response
        if response_document["status"] != "OK":
            self.fail(response_document["reason_code"], call)
        else:
            self.last_completed_phase = call["phase"]
        epoch = response_document["service_epoch"]
        fence = response_document["fencing_generation"]
        if epoch != self.service_epoch or fence != self.fencing_generation:
            self.fail("SERVICE_EPOCH_FENCE_DRIFT", call)
        return response

    def fail(self, reason: str, call: Optional[dict[str, Any]] = None) -> None:
        self.reasons = _reason_list(self.reasons + [reason])
        if call is not None and self.uncertain_phase is None:
            self.uncertain_phase = call["phase"]
            self.uncertain_tool_call_id = call["tool_call_id"]

    def ok(self, response: BackendResponse) -> bool:
        return response.document["status"] == "OK" and not self.reasons


def _validate_health(payload: Any) -> dict[str, Any]:
    value = _exact(payload, HEALTH_FIELDS, "HEALTH_PAYLOAD_INVALID")
    if not (
            value["execution_mode"] == "PAPER" and
            value["paper_account"] is True and value["connected"] is True and
            value["complete"] is True):
        raise CanaryContractError("HEALTH_BOUNDARY_INVALID")
    connectors = _integer(value["authorized_connector_count"],
                          "HEALTH_CONNECTOR_COUNT_INVALID", maximum=1)
    if connectors != 1:
        raise CanaryContractError("BROKER_SESSION_CONNECTOR_NOT_ONE")
    return value


def _validate_quote(payload: Any, intent: dict[str, Any], now_ms: int) -> dict[str, Any]:
    value = _exact(payload, QUOTE_FIELDS, "QUOTE_PAYLOAD_INVALID")
    if (
            value["instrument"] != "EUR.USD" or value["symbol"] != "EUR" or
            value["currency"] != "USD" or value["sec_type"] != "CASH" or
            value["exchange"] != "IDEALPRO" or value["authoritative"] is not True or
            value["complete"] is not True):
        raise CanaryContractError("QUOTE_CONTRACT_INVALID")
    bid = _decimal(value["bid"], "QUOTE_PRICE_INVALID", positive=True)
    ask = _decimal(value["ask"], "QUOTE_PRICE_INVALID", positive=True)
    observed = _integer(value["observed_at_ms"], "QUOTE_TIME_INVALID")
    if (
            bid != intent["observed_bid"] or ask != intent["observed_ask"] or
            observed != intent["observed_at_ms"] or observed > now_ms + 1_000 or
            now_ms - observed > MAX_QUOTE_AGE_MS):
        raise CanaryContractError("QUOTE_STALE_OR_MISMATCHED")
    return value


def _scope_bound(value: Mapping[str, Any], owner: Mapping[str, Any],
                 code: str) -> None:
    if (
            value.get("owner_account") != owner["owner_account"] or
            value.get("owner_execution_domain") !=
                owner["owner_execution_domain"]):
        raise CanaryContractError(code)


def _validate_account(
        payload: Any, *, require_zero: bool, owner: Mapping[str, Any]
) -> dict[str, Any]:
    value = _exact(payload, ACCOUNT_FIELDS, "ACCOUNT_PAYLOAD_INVALID")
    _digest(value["account_id_sha256"], "ACCOUNT_ID_INVALID")
    gross = _integer(value["gross_absolute_position"], "ACCOUNT_GROSS_INVALID",
                     maximum=1)
    _integer(value["fx_cash_generation"], "ACCOUNT_GENERATION_INVALID", minimum=1)
    _scope_bound(value, owner, "ACCOUNT_OWNER_SCOPE_INVALID")
    if not (
            value["account_kind"] == "PAPER" and value["authoritative"] is True and
            value["account_complete"] is True and (not require_zero or gross == 0)):
        raise CanaryContractError("ACCOUNT_BOUNDARY_INVALID")
    return value


def _validate_positions(
        payload: Any, *, require_empty: bool, owner: Mapping[str, Any]
) -> dict[str, Any]:
    value = _exact(payload, POSITIONS_FIELDS, "POSITIONS_PAYLOAD_INVALID")
    if value["authoritative"] is not True or value["complete"] is not True:
        raise CanaryContractError("POSITIONS_NOT_AUTHORITATIVE")
    _digest(value["snapshot_sha256"], "POSITIONS_SNAPSHOT_INVALID")
    _integer(value["position_generation"], "POSITIONS_GENERATION_INVALID", minimum=1)
    _integer(value["fx_cash_generation"], "POSITIONS_GENERATION_INVALID", minimum=1)
    _scope_bound(value, owner, "POSITIONS_OWNER_SCOPE_INVALID")
    positions = value["positions"]
    if not isinstance(positions, list) or len(positions) > 1:
        raise CanaryContractError("POSITIONS_SCOPE_INVALID")
    gross = 0
    for item in positions:
        position = _exact(item, POSITION_FIELDS, "POSITION_FIELDS_INVALID")
        if position["instrument"] != "EUR.USD":
            raise CanaryContractError("POSITION_INSTRUMENT_INVALID")
        quantity = _integer(position["quantity"], "POSITION_QUANTITY_INVALID",
                            minimum=-1, maximum=1)
        if quantity == 0:
            raise CanaryContractError("POSITION_QUANTITY_INVALID")
        gross += abs(quantity)
    if value["gross_absolute_position"] != gross:
        raise CanaryContractError("POSITION_GROSS_MISMATCH")
    if require_empty and positions:
        raise CanaryContractError("POSITION_NOT_EMPTY")
    return value


def _validate_orders(
        payload: Any, *, require_empty: bool, owner: Mapping[str, Any]
) -> dict[str, Any]:
    value = _exact(payload, ORDERS_FIELDS, "ORDERS_PAYLOAD_INVALID")
    if value["authoritative"] is not True or value["complete"] is not True:
        raise CanaryContractError("ORDERS_NOT_AUTHORITATIVE")
    _digest(value["snapshot_sha256"], "ORDERS_SNAPSHOT_INVALID")
    _integer(value["connection_epoch"], "ORDERS_GENERATION_INVALID", minimum=1)
    _integer(value["generation"], "ORDERS_GENERATION_INVALID", minimum=1)
    _scope_bound(value, owner, "ORDERS_OWNER_SCOPE_INVALID")
    orders = value["orders"]
    if not isinstance(orders, list) or len(orders) > 1:
        raise CanaryContractError("ORDERS_SCOPE_INVALID")
    for item in orders:
        order = _exact(item, ORDER_FIELDS, "ORDER_FIELDS_INVALID")
        _digest(order["order_id_sha256"], "ORDER_ID_INVALID")
        if (
                order["instrument"] != "EUR.USD" or
                not isinstance(order["owned"], bool) or
                not isinstance(order["active"], bool)):
            raise CanaryContractError("ORDER_SCOPE_INVALID")
    if require_empty and orders:
        raise CanaryContractError("ORDER_NOT_EMPTY")
    return value


def _validate_risk(payload: Any, *, owner: Mapping[str, Any]) -> dict[str, Any]:
    value = _exact(payload, RISK_FIELDS, "RISK_PAYLOAD_INVALID")
    _scope_bound(value, owner, "RISK_OWNER_SCOPE_INVALID")
    for field in (
            "connection_epoch", "orders_generation", "position_generation",
            "fx_cash_generation"):
        _integer(value[field], "RISK_GENERATION_INVALID", minimum=1)
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
            "order_types": ["LMT"], "tifs": ["DAY"], "complete": True}:
        raise CanaryContractError("RISK_BOUNDARY_INVALID")
    return value


def _safe_validate(run: _Run, call: dict[str, Any], function: Any, *args: Any,
                   **kwargs: Any) -> Optional[dict[str, Any]]:
    try:
        return function(*args, **kwargs)
    except CanaryContractError as error:
        run.fail(error.code, call)
        return None


def _preflight(run: _Run) -> Optional[str]:
    handoff = run.handoff.document
    owner = handoff["session_owner_reference"]
    payloads: dict[str, Any] = {}
    calls = (
        ("preflight-health", _validate_health, {}),
        ("preflight-quote", _validate_quote,
         {"intent": handoff["intent"], "now_ms": run.now_ms()}),
        ("preflight-account", _validate_account,
         {"require_zero": True, "owner": owner}),
        ("preflight-positions", _validate_positions,
         {"require_empty": True, "owner": owner}),
        ("preflight-orders", _validate_orders,
         {"require_empty": True, "owner": owner}),
        ("preflight-risk", _validate_risk, {"owner": owner}),
    )
    for role, validator, keyword in calls:
        response = run.call(role, {"instrument": "EUR.USD"})
        if response.document["status"] != "OK":
            return None
        value = _safe_validate(
            run, run.handoff.calls[role], validator,
            response.document["payload"], **keyword)
        if value is None:
            return None
        payloads[role] = value
        if run.reasons:
            return None
    account = payloads["preflight-account"]
    positions = payloads["preflight-positions"]
    orders = payloads["preflight-orders"]
    risk = payloads["preflight-risk"]
    if not (
            account["fx_cash_generation"] == positions["fx_cash_generation"] ==
                risk["fx_cash_generation"] and
            positions["position_generation"] == risk["position_generation"] and
            orders["connection_epoch"] == risk["connection_epoch"] and
            orders["generation"] == risk["orders_generation"]):
        run.fail("PREFLIGHT_CROSS_GENERATION_MISMATCH")
        return None
    campaign_response = run.call("preflight-campaign", {})
    if campaign_response.document["status"] != "OK":
        return None
    campaign = _safe_validate(
        run, run.handoff.calls["preflight-campaign"], _exact,
        campaign_response.document["payload"], CAMPAIGN_FIELDS,
        "CAMPAIGN_PAYLOAD_INVALID")
    if campaign is None:
        return None
    if campaign != {
            "state": "IDLE", "cycle_id": None, "remaining_cycles": 1,
            "authority_granted": False}:
        run.fail("CAMPAIGN_NOT_IDLE", run.handoff.calls["preflight-campaign"])
        return None
    payloads["preflight-campaign"] = campaign
    return canonical_sha256(payloads)


def _close(run: _Run, outcome: str) -> None:
    if outcome not in {
            "PREVIEW_REJECTED", "PLACE_REJECTED", "PLACE_ACCEPTED",
            "PLACE_UNCERTAIN", "OPERATOR_ABORT"}:
        raise CanaryContractError("CLOSE_OUTCOME_INVALID")
    run.close_outcome = outcome
    response = run.call("close", {
        "cycle_id": run.handoff.document["cycle_id"],
        "intent_sha256": run.handoff.document["intent_sha256"],
        "outcome": outcome,
    })
    if response.document["status"] != "OK":
        return
    value = _safe_validate(
        run, run.handoff.calls["close"], _exact, response.document["payload"],
        CLOSE_FIELDS, "CLOSE_PAYLOAD_INVALID")
    if value != {
            "closed": True, "cycle_id": run.handoff.document["cycle_id"],
            "intent_sha256": run.handoff.document["intent_sha256"],
            "outcome": outcome,
            "authority_granted": False}:
        run.fail("CLOSE_RESPONSE_INVALID", run.handoff.calls["close"])
        return
    run.cycle_closed = True


def _open_preview_place_close(run: _Run, preflight_sha256: str) -> None:
    handoff = run.handoff.document
    opened = run.call("open", {
        "cycle_id": handoff["cycle_id"], "intent": handoff["intent"],
        "intent_sha256": handoff["intent_sha256"],
        "preflight_sha256": preflight_sha256,
    })
    if opened.document["status"] == "OK":
        value = _safe_validate(
            run, run.handoff.calls["open"], _exact, opened.document["payload"],
            OPEN_FIELDS, "OPEN_PAYLOAD_INVALID")
        if value is not None and (
                value["opened"] is True and value["cycle_id"] == handoff["cycle_id"] and
                value["intent_sha256"] == handoff["intent_sha256"] and
                value["authority_granted"] is False and
                isinstance(value["deadline_at_ms"], int) and
                run.now_ms() < value["deadline_at_ms"] <= handoff["expires_at_ms"]):
            run.cycle_opened = True
        else:
            run.fail("OPEN_RESPONSE_INVALID", run.handoff.calls["open"])
    if not run.cycle_opened:
        return
    if run.reasons:
        _close(run, "OPERATOR_ABORT")
        return
    preview = run.call("preview-order", {
        "cycle_id": handoff["cycle_id"], "intent": handoff["intent"],
        "intent_sha256": handoff["intent_sha256"],
    })
    run.preview_sha256 = preview.sha256
    if preview.document["status"] == "OK":
        value = _safe_validate(
            run, run.handoff.calls["preview-order"], _exact,
            preview.document["payload"], PREVIEW_ORDER_FIELDS,
            "PREVIEW_ORDER_PAYLOAD_INVALID")
        if value is None or not (
                value["approved"] is True and value["cycle_id"] == handoff["cycle_id"] and
                value["intent_sha256"] == handoff["intent_sha256"] and
                value["order_request_sha256"] == canonical_sha256(handoff["intent"]) and
                value["authority_granted"] is False):
            run.fail("PREVIEW_ORDER_RESPONSE_INVALID", run.handoff.calls["preview-order"])
    if run.reasons:
        _close(run, "PREVIEW_REJECTED")
        return
    placed = run.call("place", {
        "cycle_id": handoff["cycle_id"], "intent": handoff["intent"],
        "intent_sha256": handoff["intent_sha256"],
        "preview_response_sha256": run.preview_sha256,
    })
    close_outcome = (
        "PLACE_ACCEPTED" if placed.document["status"] == "OK" else
        "PLACE_REJECTED" if placed.document["status"] == "REJECTED" else
        "PLACE_UNCERTAIN")
    if placed.document["status"] == "OK":
        value = _safe_validate(
            run, run.handoff.calls["place"], _exact, placed.document["payload"],
            PLACE_FIELDS, "PLACE_PAYLOAD_INVALID")
        if value is not None and (
                value["accepted"] is True and value["owned"] is True and
                value["cycle_id"] == handoff["cycle_id"] and
                value["intent_sha256"] == handoff["intent_sha256"] and
                value["authority_granted"] is False):
            try:
                run.order_id_sha256 = _digest(
                    value["order_id_sha256"], "PLACE_ORDER_ID_INVALID")
            except CanaryContractError as error:
                run.fail(error.code, run.handoff.calls["place"])
        else:
            run.fail("PLACE_RESPONSE_INVALID", run.handoff.calls["place"])
            close_outcome = "PLACE_UNCERTAIN"
    # The close call is adjacent to the single place attempt even when place
    # returned uncertainty.  It is never retried or assigned a replacement ID.
    _close(run, close_outcome)


def _reconcile_pair(run: _Run, prefix: str) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    orders_role = f"{prefix}-orders"
    positions_role = f"{prefix}-positions"
    orders_response = run.call(orders_role, {"instrument": "EUR.USD"})
    if orders_response.document["status"] != "OK":
        return None, None
    orders = _safe_validate(
        run, run.handoff.calls[orders_role], _validate_orders,
        orders_response.document["payload"], require_empty=False,
        owner=run.handoff.document["session_owner_reference"])
    if orders is None or run.reasons:
        return None, None
    positions_response = run.call(positions_role, {"instrument": "EUR.USD"})
    if positions_response.document["status"] != "OK":
        return orders, None
    positions = _safe_validate(
        run, run.handoff.calls[positions_role], _validate_positions,
        positions_response.document["payload"], require_empty=False,
        owner=run.handoff.document["session_owner_reference"])
    return orders, positions


def _state_from_snapshots(
        run: _Run, *, orders: dict[str, Any], positions: dict[str, Any],
        account: Optional[dict[str, Any]] = None,
        health: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    active = sorted(
        item["order_id_sha256"] for item in orders["orders"] if item["active"])
    normalized_positions = [dict(item) for item in positions["positions"]]
    gross = positions["gross_absolute_position"]
    connectors = (
        health["authorized_connector_count"] if health is not None else
        run.last_state["authorized_connector_count"])
    complete = (
        account["account_complete"] if account is not None else
        positions["complete"] and orders["complete"])
    authoritative = (
        positions["authoritative"] and orders["authoritative"] and
        (account is None or account["authoritative"]))
    snapshot = {
        "orders_snapshot_sha256": orders["snapshot_sha256"],
        "positions_snapshot_sha256": positions["snapshot_sha256"],
        "account": account, "health": health,
        "service_epoch": run.service_epoch or "unobserved",
        "fencing_generation": run.fencing_generation,
    }
    state = {
        "authoritative": authoritative, "account_complete": complete,
        "snapshot_sha256": canonical_sha256(snapshot),
        "service_epoch": run.service_epoch or "unobserved",
        "fencing_generation": run.fencing_generation,
        "active_order_id_sha256s": active, "positions": normalized_positions,
        "gross_absolute_position": gross,
        "authorized_connector_count": connectors,
        "end_flat": not active and not normalized_positions and gross == 0,
    }
    run.last_state = state
    return state


def _cancel(run: _Run, order_id_sha256: str) -> bool:
    response = run.call("cancel-order", {
        "order_id_sha256": order_id_sha256, "stable_cancel": True,
    })
    if response.document["status"] != "OK":
        return False
    value = _safe_validate(
        run, run.handoff.calls["cancel-order"], _exact,
        response.document["payload"], CANCEL_FIELDS, "CANCEL_PAYLOAD_INVALID")
    if value != {
            "cancelled": True, "order_id_sha256": order_id_sha256,
            "stable_cancel": True, "authority_granted": False}:
        run.fail("CANCEL_RESPONSE_INVALID", run.handoff.calls["cancel-order"])
        return False
    return True


def _flatten(run: _Run, position: dict[str, Any]) -> bool:
    side = "SELL" if position["quantity"] == 1 else "BUY"
    preview = run.call("preview-flatten", {
        "instrument": "EUR.USD", "position_quantity": position["quantity"],
        "side": side, "quantity": 1, "order_type": "LMT", "tif": "DAY",
        "reduce_only": True, "atomic": True, "no_retry": True,
    })
    if preview.document["status"] != "OK":
        return False
    value = _safe_validate(
        run, run.handoff.calls["preview-flatten"], _exact,
        preview.document["payload"], PREVIEW_FLATTEN_FIELDS,
        "PREVIEW_FLATTEN_PAYLOAD_INVALID")
    if value is None:
        return False
    now_ms = run.now_ms()
    bid = _decimal(
        value["observed_bid"], "PREVIEW_FLATTEN_QUOTE_INVALID", positive=True)
    ask = _decimal(
        value["observed_ask"], "PREVIEW_FLATTEN_QUOTE_INVALID", positive=True)
    limit_price = _decimal(
        value["limit_price"], "PREVIEW_FLATTEN_LIMIT_INVALID", positive=True)
    observed_at_ms = _integer(
        value["quote_observed_at_ms"], "PREVIEW_FLATTEN_TIME_INVALID")
    expires_at_ms = _integer(
        value["expires_at_ms"], "PREVIEW_FLATTEN_TIME_INVALID")
    if (
            value["approved"] is not True or
            value["instrument"] != "EUR.USD" or
            value["position_quantity"] != position["quantity"] or
            value["side"] != side or value["quantity"] != 1 or
            value["order_type"] != "LMT" or value["tif"] != "DAY" or
            _decimal_order_key(bid) > _decimal_order_key(ask) or
            limit_price != (bid if side == "SELL" else ask) or
            observed_at_ms > now_ms + 1_000 or
            now_ms - observed_at_ms > MAX_QUOTE_AGE_MS or
            not now_ms < expires_at_ms <= min(
                run.handoff.document["expires_at_ms"],
                now_ms + MAX_INTENT_HORIZON_MS) or
            value["reduce_only"] is not True or value["atomic"] is not True or
            value["authority_granted"] is not False):
        run.fail("PREVIEW_FLATTEN_RESPONSE_INVALID",
                 run.handoff.calls["preview-flatten"])
        return False
    flattened = run.call("flatten-position", {
        "instrument": "EUR.USD", "position_quantity": position["quantity"],
        "side": side, "quantity": 1, "order_type": "LMT", "tif": "DAY",
        "limit_price": limit_price,
        "quote_observed_at_ms": observed_at_ms,
        "expires_at_ms": expires_at_ms,
        "reduce_only": True, "atomic": True, "no_retry": True,
        "preview_response_sha256": preview.sha256,
    })
    if flattened.document["status"] != "OK":
        return False
    value = _safe_validate(
        run, run.handoff.calls["flatten-position"], _exact,
        flattened.document["payload"], FLATTEN_FIELDS,
        "FLATTEN_PAYLOAD_INVALID")
    expected = {
        "flattened": True, "instrument": "EUR.USD",
        "position_quantity": position["quantity"], "side": side,
        "quantity": 1, "order_type": "LMT", "tif": "DAY",
        "limit_price": limit_price,
        "quote_observed_at_ms": observed_at_ms,
        "expires_at_ms": expires_at_ms,
        "reduce_only": True, "atomic": True, "authority_granted": False,
    }
    if value != expected:
        run.fail("FLATTEN_RESPONSE_INVALID", run.handoff.calls["flatten-position"])
        return False
    return True


def _risk_reduce(run: _Run) -> tuple[bool, bool]:
    orders, positions = _reconcile_pair(run, "reconcile")
    if orders is None or positions is None or run.reasons:
        return False, False
    _state_from_snapshots(run, orders=orders, positions=positions)
    active = [item for item in orders["orders"] if item["active"]]
    if len(active) > 1 or any(not item["owned"] for item in active):
        run.fail("OWNED_ORDER_SCOPE_UNCERTAIN")
        return False, False
    if active:
        order = active[0]
        if (
                run.order_id_sha256 is None or
                order["order_id_sha256"] != run.order_id_sha256):
            run.fail("OWNED_ORDER_BINDING_MISMATCH")
            return False, False
        run.owned_order_id_sha256 = order["order_id_sha256"]
    exact_positions = positions["positions"]
    if len(exact_positions) > 1:
        run.fail("POSITION_SCOPE_UNCERTAIN")
        return False, False
    if not active and not exact_positions:
        run.fail("PLACE_OUTCOME_NOT_RECONCILABLE")
        return False, False
    cancelled = False
    flattened = False
    if active:
        cancelled = _cancel(run, active[0]["order_id_sha256"])
        if not cancelled or run.reasons:
            return cancelled, False
    if exact_positions:
        flattened = _flatten(run, exact_positions[0])
        if not flattened or run.reasons:
            return cancelled, flattened
    if cancelled and flattened:
        run.recovery_used = True
    return cancelled, flattened


def _historic_commands_settled(run: _Run) -> bool:
    try:
        snapshot = _journal_snapshot(run.backend)
        records = _parse_journal(snapshot.raw, run.handoff)
    except CanaryContractError as error:
        run.fail(error.code)
        return False
    requests: dict[str, dict[str, Any]] = {}
    responses: dict[str, dict[str, Any]] = {}
    for record in records[1:]:
        if record["event"] == "REQUEST":
            requests[record["call_role"]] = record
        else:
            responses[record["call_role"]] = record
    pending = set(requests) - set(responses)
    unsettled = {
        role for role, response in responses.items()
        if response["status"] in {"UNCERTAIN", "ERROR"}
    }
    close = responses.get("close")
    if pending or unsettled or close is None or close["status"] != "OK" or \
            not run.cycle_closed:
        run.fail("PRE_CLEANUP_MUTATION_FENCE_UNSETTLED")
        return False
    return True


def _pre_cleanup_reconcile(run: _Run) -> bool:
    # Campaign CLOSE is the mutation fence.  Every historic command must have
    # a terminal response before any zero proof is sampled.
    if not _historic_commands_settled(run):
        return False
    health_response = run.call("final-health", {})
    if health_response.document["status"] != "OK":
        return False
    health = _safe_validate(
        run, run.handoff.calls["final-health"], _validate_health,
        health_response.document["payload"])
    if health is None or run.reasons:
        return False
    orders_response = run.call("final-orders", {"instrument": "EUR.USD"})
    if orders_response.document["status"] != "OK":
        return False
    orders = _safe_validate(
        run, run.handoff.calls["final-orders"], _validate_orders,
        orders_response.document["payload"], require_empty=True,
        owner=run.handoff.document["session_owner_reference"])
    if orders is None or run.reasons:
        return False
    account_response = run.call("final-account", {})
    if account_response.document["status"] != "OK":
        return False
    account = _safe_validate(
        run, run.handoff.calls["final-account"], _validate_account,
        account_response.document["payload"], require_zero=True,
        owner=run.handoff.document["session_owner_reference"])
    if account is None or run.reasons:
        return False
    positions_response = run.call("final-positions", {"instrument": "EUR.USD"})
    if positions_response.document["status"] != "OK":
        return False
    positions = _safe_validate(
        run, run.handoff.calls["final-positions"], _validate_positions,
        positions_response.document["payload"], require_empty=True,
        owner=run.handoff.document["session_owner_reference"])
    if positions is None or run.reasons:
        return False
    if account["fx_cash_generation"] != positions["fx_cash_generation"]:
        run.fail("PRE_CLEANUP_CROSS_GENERATION_MISMATCH")
        return False
    state = _state_from_snapshots(
        run, orders=orders, positions=positions, account=account, health=health)
    risk_response = run.call("cleanup-risk", {})
    if risk_response.document["status"] != "OK":
        return False
    risk = _safe_validate(
        run, run.handoff.calls["cleanup-risk"], _validate_risk,
        risk_response.document["payload"],
        owner=run.handoff.document["session_owner_reference"])
    if risk is None or run.reasons:
        return False
    if not (
            risk["connection_epoch"] == orders["connection_epoch"] and
            risk["orders_generation"] == orders["generation"] and
            risk["position_generation"] == positions["position_generation"] and
            risk["fx_cash_generation"] == account["fx_cash_generation"] ==
                positions["fx_cash_generation"]):
        run.fail("PRE_CLEANUP_CROSS_GENERATION_MISMATCH")
        return False
    prior_orders_response = run.responses.get("reconcile-orders")
    prior_positions_response = run.responses.get("reconcile-positions")
    if prior_orders_response is not None and \
            orders["generation"] < prior_orders_response.document[
                "payload"]["generation"]:
        run.fail("PRE_CLEANUP_ORDER_GENERATION_REGRESSED")
        return False
    if prior_positions_response is not None and \
            positions["position_generation"] < prior_positions_response.document[
                "payload"]["position_generation"]:
        run.fail("PRE_CLEANUP_POSITION_GENERATION_REGRESSED")
        return False
    if not (
            state["authoritative"] and state["account_complete"] and
            state["active_order_id_sha256s"] == [] and state["positions"] == [] and
            state["gross_absolute_position"] == 0 and
            state["authorized_connector_count"] == 1 and state["end_flat"]):
        run.fail("PRE_CLEANUP_AUTHORITATIVE_FLAT_INCOMPLETE")
        return False
    run.pre_cleanup_state = dict(state)
    return True


def _sealed_body(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "body_sha256": canonical_sha256(body)}


def _pre_cleanup_documents(
        run: _Run, journal: JournalSnapshot,
) -> tuple[
        dict[str, Any], bytes, dict[str, Any], bytes, dict[str, Any], bytes]:
    if (
            run.pre_cleanup_state is None or run.close_outcome is None or
            not run.cycle_opened or not run.cycle_closed or
            not run.place_attempted or not run.close_attempted):
        raise CanaryContractError("PRE_CLEANUP_LIFECYCLE_INCOMPLETE")
    if journal.path != journal_path_for(run.handoff):
        raise CanaryContractError("PRE_CLEANUP_JOURNAL_PATH_MISMATCH")
    records = _parse_journal(journal.raw, run.handoff)
    observed_evidence = _journal_evidence(records, run.handoff)
    if observed_evidence != run.evidence:
        raise CanaryContractError("PRE_CLEANUP_TOOL_EVIDENCE_MISMATCH")
    handoff = run.handoff.document
    created_at_ms = run.now_ms()
    bundle_responses: list[dict[str, Any]] = []
    for role in PRE_CLEANUP_FINAL_ROLES:
        call = run.handoff.calls[role]
        response = run.responses.get(role)
        evidence_item = next(
            (item for item in observed_evidence
             if item["tool_call_id"] == call["tool_call_id"]),
            None)
        if (
                response is None or evidence_item is None or
                response.document["status"] != "OK" or
                response.sha256 != evidence_item["response_sha256"] or
                response.document["normalized_payload_sha256"] !=
                    canonical_sha256(response.document["payload"])):
            raise CanaryContractError("PRE_CLEANUP_RESPONSE_BUNDLE_INCOMPLETE")
        bundle_responses.append({
            "call_role": role,
            "tool_call_id": call["tool_call_id"],
            "tool_name": call["tool_name"],
            "tool_descriptor_sha256": call["tool_descriptor_sha256"],
            "effect": call["effect"],
            "phase": call["phase"],
            "request_sha256": evidence_item["request_sha256"],
            "response_sha256": response.sha256,
            "status": evidence_item["status"],
            "reason_code": evidence_item["reason_code"],
            "backend_response": response.document,
        })
    bundle_body = {
        "schema": PRE_CLEANUP_RESPONSE_BUNDLE_SCHEMA,
        "version": VERSION,
        "created_at_ms": created_at_ms,
        "campaign_id": handoff["campaign_id"],
        "domain_id": handoff["domain_id"],
        "cycle_id": handoff["cycle_id"],
        "handoff_file_sha256": run.handoff.file_sha256,
        "handoff_body_sha256": run.handoff.body_sha256,
        "journal_path": journal.path,
        "journal_sha256": sha256_bytes(journal.raw),
        "journal_last_sequence": len(records) - 1,
        "final_roles": list(PRE_CLEANUP_FINAL_ROLES),
        "responses": bundle_responses,
        "tool_evidence_sha256": canonical_sha256(observed_evidence),
        "claimed_broker_state": run.pre_cleanup_state,
        "claimed_broker_state_sha256": canonical_sha256(
            run.pre_cleanup_state),
        "authority_granted": False,
    }
    bundle = _sealed_body(bundle_body)
    bundle_raw = canonical_json(bundle)
    evidence_body = {
        "schema": PRE_CLEANUP_EVIDENCE_SCHEMA,
        "version": VERSION,
        "created_at_ms": created_at_ms,
        "campaign_id": handoff["campaign_id"],
        "domain_id": handoff["domain_id"],
        "cycle_id": handoff["cycle_id"],
        "handoff_path": handoff_path_for(run.handoff),
        "handoff_file_sha256": run.handoff.file_sha256,
        "handoff_body_sha256": run.handoff.body_sha256,
        "intent_sha256": handoff["intent_sha256"],
        "installed_images_sha256": handoff["installed_images_sha256"],
        "executor_image_sha256": run.handoff.images["executor"][
            "file_sha256"],
        "backend_adapter_image_sha256": run.handoff.images[
            "backend-adapter"]["file_sha256"],
        "backend_transform_version": BACKEND_TRANSFORM_VERSION,
        "session_owner_reference_sha256": canonical_sha256(
            handoff["session_owner_reference"]),
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": handoff[
            "execution_service_fencing_generation"],
        "journal_path": journal.path,
        "journal_sha256": sha256_bytes(journal.raw),
        "journal_size": len(journal.raw),
        "journal_last_sequence": len(records) - 1,
        "tool_evidence_sha256": canonical_sha256(observed_evidence),
        "response_bundle_path": pre_cleanup_response_bundle_path_for(
            run.handoff),
        "response_bundle_file_sha256": sha256_bytes(bundle_raw),
        "response_bundle_body_sha256": bundle["body_sha256"],
        "cycle_opened": True,
        "cycle_closed": True,
        "place_attempted": True,
        "close_attempted": True,
        "close_outcome": run.close_outcome,
        "broker_state": run.pre_cleanup_state,
        "broker_state_sha256": canonical_sha256(run.pre_cleanup_state),
        "authority_granted": False,
    }
    evidence = _sealed_body(evidence_body)
    evidence_raw = canonical_json(evidence)
    root_call = handoff["root_cleanup_call"]
    if created_at_ms >= handoff["expires_at_ms"]:
        raise CanaryContractError("ROOT_CLEANUP_REQUEST_EXPIRED")
    expires_at_ms = created_at_ms + ROOT_CLEANUP_TIMEOUT_MS
    request_body = {
        "schema": ROOT_CLEANUP_REQUEST_SCHEMA,
        "version": VERSION,
        "issued_at_ms": created_at_ms,
        "expires_at_ms": expires_at_ms,
        "campaign_id": handoff["campaign_id"],
        "domain_id": handoff["domain_id"],
        "cycle_id": handoff["cycle_id"],
        "cleanup_tool_call_id": root_call["tool_call_id"],
        "cleanup_command_id": root_call["command_id"],
        "tool_descriptor_sha256": root_call["tool_descriptor_sha256"],
        "handoff_file_sha256": run.handoff.file_sha256,
        "handoff_body_sha256": run.handoff.body_sha256,
        "session_owner_reference_sha256": canonical_sha256(
            handoff["session_owner_reference"]),
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": handoff[
            "execution_service_fencing_generation"],
        "pre_cleanup_evidence_path": pre_cleanup_evidence_path_for(
            run.handoff),
        "pre_cleanup_evidence_file_sha256": sha256_bytes(evidence_raw),
        "pre_cleanup_evidence_body_sha256": evidence["body_sha256"],
        "required_actions": list(ROOT_CLEANUP_ACTIONS),
        "paper_only": True,
        "live_authorized": False,
        "authority_granted": False,
    }
    request = _sealed_body(request_body)
    request_raw = canonical_json(request)
    run.pre_cleanup_response_bundle = bundle
    run.pre_cleanup_response_bundle_raw = bundle_raw
    run.pre_cleanup_evidence = evidence
    run.pre_cleanup_evidence_raw = evidence_raw
    run.root_cleanup_request = request
    run.root_cleanup_request_raw = request_raw
    return bundle, bundle_raw, evidence, evidence_raw, request, request_raw


def _validate_root_cleanup_receipt(
        raw: bytes, *, run: _Run, evidence_raw: bytes, request_raw: bytes,
        journal: JournalSnapshot,
) -> dict[str, Any]:
    receipt = _load_canonical(raw, "ROOT_CLEANUP_RECEIPT")
    _exact(
        receipt, ROOT_CLEANUP_RECEIPT_FIELDS,
        "ROOT_CLEANUP_RECEIPT_FIELDS_INVALID")
    if (
            receipt["schema"] != ROOT_CLEANUP_RECEIPT_SCHEMA or
            receipt["version"] != ROOT_CLEANUP_RECEIPT_VERSION or
            receipt["status"] != "ROOT_CLEANUP_COMPLETE_DENY_ALL" or
            receipt["completed_actions"] != list(ROOT_CLEANUP_ACTIONS) or
            receipt["paper_only"] is not True or
            receipt["live_authorized"] is not False or
            receipt["authority_granted"] is not False):
        raise CanaryContractError("ROOT_CLEANUP_RECEIPT_IDENTITY_INVALID")
    _sealed(receipt, "ROOT_CLEANUP_RECEIPT_BODY_INVALID")
    handoff = run.handoff.document
    request = run.root_cleanup_request
    evidence = run.pre_cleanup_evidence
    if request is None or evidence is None:
        raise CanaryContractError("ROOT_CLEANUP_CHECKPOINT_MISSING")
    expected = {
        "campaign_id": handoff["campaign_id"],
        "domain_id": handoff["domain_id"],
        "cycle_id": handoff["cycle_id"],
        "cleanup_tool_call_id": handoff["root_cleanup_call"]["tool_call_id"],
        "cleanup_command_id": handoff["root_cleanup_call"]["command_id"],
        "tool_descriptor_sha256": handoff["root_cleanup_call"][
            "tool_descriptor_sha256"],
        "execution_handoff_path": handoff_path_for(run.handoff),
        "execution_handoff_file_sha256": run.handoff.file_sha256,
        "execution_handoff_body_sha256": run.handoff.body_sha256,
        "watch_handoff_file_sha256": handoff[
            "watch_handoff_receipt_file_sha256"],
        "watch_handoff_body_sha256": handoff[
            "watch_handoff_receipt_body_sha256"],
        "intent_sha256": handoff["intent_sha256"],
        "installed_images_sha256": handoff["installed_images_sha256"],
        "executor_image_sha256": run.handoff.images["executor"][
            "file_sha256"],
        "backend_adapter_image_sha256": run.handoff.images[
            "backend-adapter"]["file_sha256"],
        "root_finalizer_image_sha256": run.handoff.images[
            "root-finalizer"]["file_sha256"],
        "backend_transform_version": BACKEND_TRANSFORM_VERSION,
        "session_owner_reference_sha256": canonical_sha256(
            handoff["session_owner_reference"]),
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": handoff[
            "execution_service_fencing_generation"],
        "journal_path": journal.path,
        "journal_sha256": sha256_bytes(journal.raw),
        "journal_size": len(journal.raw),
        "journal_last_sequence": evidence["journal_last_sequence"],
        "tool_evidence_sha256": evidence["tool_evidence_sha256"],
        "pre_cleanup_evidence_path": pre_cleanup_evidence_path_for(
            run.handoff),
        "pre_cleanup_evidence_file_sha256": sha256_bytes(evidence_raw),
        "pre_cleanup_evidence_body_sha256": evidence["body_sha256"],
        "root_cleanup_request_path": root_cleanup_request_path_for(
            run.handoff),
        "root_cleanup_request_file_sha256": sha256_bytes(request_raw),
        "root_cleanup_request_body_sha256": request["body_sha256"],
    }
    if any(receipt[field] != value for field, value in expected.items()):
        raise CanaryContractError("ROOT_CLEANUP_RECEIPT_BINDING_MISMATCH")
    completed_at_ms = _integer(
        receipt["completed_at_ms"], "ROOT_CLEANUP_RECEIPT_TIME_INVALID")
    if not request["issued_at_ms"] <= completed_at_ms <= request["expires_at_ms"]:
        raise CanaryContractError("ROOT_CLEANUP_RECEIPT_TIME_INVALID")
    for field in (
            "guardian_request_id", "local_control_transaction_id"):
        _identifier(receipt[field], "ROOT_CLEANUP_RECEIPT_ID_INVALID")
    for field in (
            "local_control_request_sha256",
            "guardian_active_receipt_file_sha256",
            "guardian_active_receipt_body_sha256", "identity_manifest_sha256",
            "broker_policy_sha256"):
        _digest(receipt[field], "ROOT_CLEANUP_RECEIPT_DIGEST_INVALID")
    if (
            receipt["broker_mutation_units"] != list(BROKER_MUTATION_UNITS) or
            receipt["broker_mutation_units_sha256"] != canonical_sha256(
                list(BROKER_MUTATION_UNITS))):
        raise CanaryContractError("ROOT_CLEANUP_MUTATION_UNITS_INVALID")
    for field in (
            "guardian_stopped", "execution_control_disabled",
            "kill_switch_engaged", "global_kill_switch_engaged",
            "broker_deny_all", "broker_mutation_units_inactive", "permit_absent",
            "guardian_runtime_absent"):
        if receipt[field] is not True:
            raise CanaryContractError("ROOT_CLEANUP_PROOF_INCOMPLETE")
    if (
            receipt["durable_owner_reference_sha256"] != canonical_sha256(
                handoff["session_owner_reference"]) or
            receipt["durable_owner_count"] != 0 or
            receipt["durable_owner_status"] != "RETIRED" or
            receipt["durable_owner_retirement_receipt_path"] !=
                durable_owner_retirement_receipt_path_for(run.handoff)):
        raise CanaryContractError("ROOT_CLEANUP_OWNER_RETIREMENT_INVALID")
    for field in (
            "durable_owner_retirement_receipt_file_sha256",
            "durable_owner_retirement_receipt_body_sha256"):
        _digest(receipt[field], "ROOT_CLEANUP_OWNER_RETIREMENT_INVALID")
    for field in (
            "runtime_session_count", "authorized_connector_count",
            "identity_count"):
        if _integer(
                receipt[field], "ROOT_CLEANUP_PROOF_INCOMPLETE",
                minimum=0) != 0:
            raise CanaryContractError("ROOT_CLEANUP_PROOF_INCOMPLETE")
    return receipt


def _root_cleanup(run: _Run) -> bool:
    checkpoint = _journal_snapshot(run.backend)
    try:
        bundle, bundle_raw, evidence, evidence_raw, request, request_raw = \
            _pre_cleanup_documents(run, checkpoint)
    except CanaryContractError as error:
        run.fail(error.code)
        return False
    try:
        run.backend.publish_checkpoint({
            "pre-cleanup-response-bundle.v1.json": bundle_raw,
            "pre-cleanup-flat-evidence.v1.json": evidence_raw,
            "root-cleanup-request.v1.json": request_raw,
        })
    except Exception:
        run.fail("ROOT_CLEANUP_CHECKPOINT_PUBLISH_FAILED")
        return False
    run.root_cleanup_attempted = True
    try:
        receipt_raw = run.backend.finalize_root_cleanup(request_raw)
    except Exception:
        run.fail("ROOT_CLEANUP_RESPONSE_UNCERTAIN")
        run.uncertain_phase = "ROOT_CLEANUP"
        run.uncertain_tool_call_id = run.handoff.document[
            "root_cleanup_call"]["tool_call_id"]
        return False
    try:
        receipt = _validate_root_cleanup_receipt(
            receipt_raw, run=run, evidence_raw=evidence_raw,
            request_raw=request_raw, journal=checkpoint)
    except CanaryContractError as error:
        run.fail(error.code)
        run.uncertain_phase = "ROOT_CLEANUP"
        run.uncertain_tool_call_id = run.handoff.document[
            "root_cleanup_call"]["tool_call_id"]
        return False
    run.root_cleanup_receipt = receipt
    run.root_cleanup_receipt_raw = receipt_raw
    run.root_cleanup_complete = True
    state = run.pre_cleanup_state
    if state is None:
        run.fail("PRE_CLEANUP_STATE_MISSING")
        return False
    composite_body = {
        "schema": COMPOSITE_SNAPSHOT_SCHEMA,
        "version": VERSION,
        "created_at_ms": receipt["completed_at_ms"],
        "campaign_id": run.handoff.document["campaign_id"],
        "domain_id": run.handoff.document["domain_id"],
        "cycle_id": run.handoff.document["cycle_id"],
        "pre_cleanup_broker_state_sha256": canonical_sha256(state),
        "pre_cleanup_evidence_file_sha256": sha256_bytes(evidence_raw),
        "pre_cleanup_evidence_body_sha256": evidence["body_sha256"],
        "root_cleanup_receipt_file_sha256": sha256_bytes(receipt_raw),
        "root_cleanup_receipt_body_sha256": receipt["body_sha256"],
        "authoritative": state["authoritative"],
        "account_complete": state["account_complete"],
        "active_order_id_sha256s": state["active_order_id_sha256s"],
        "positions": state["positions"],
        "gross_absolute_position": state["gross_absolute_position"],
        "authorized_connector_count": receipt["authorized_connector_count"],
        "end_flat": state["end_flat"],
        "service_epoch": state["service_epoch"],
        "fencing_generation": state["fencing_generation"],
        "authority_granted": False,
    }
    composite = _sealed_body(composite_body)
    composite_raw = canonical_json(composite)
    run.composite_snapshot = composite
    run.composite_snapshot_raw = composite_raw
    run.last_state = {
        "authoritative": state["authoritative"],
        "account_complete": state["account_complete"],
        "snapshot_sha256": sha256_bytes(composite_raw),
        "service_epoch": state["service_epoch"],
        "fencing_generation": state["fencing_generation"],
        "active_order_id_sha256s": state["active_order_id_sha256s"],
        "positions": state["positions"],
        "gross_absolute_position": state["gross_absolute_position"],
        "authorized_connector_count": receipt["authorized_connector_count"],
        "end_flat": state["end_flat"],
    }
    return True


def _emergency_cleanup_documents(
        run: _Run, journal: JournalSnapshot,
) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes]:
    if not run.reasons:
        raise CanaryContractError("ROOT_EMERGENCY_REASONS_MISSING")
    records = _parse_journal(journal.raw, run.handoff)
    evidence = _journal_evidence(records, run.handoff)
    if evidence != run.evidence:
        raise CanaryContractError("ROOT_EMERGENCY_TOOL_EVIDENCE_MISMATCH")
    reasons = _reason_list(run.reasons)
    handoff = run.handoff.document
    created_at_ms = run.now_ms()
    if run.uncertain_phase is not None and run.uncertain_tool_call_id is not None:
        uncertainty_kind = "TOOL_CALL"
        uncertain_phase = run.uncertain_phase
        uncertain_tool_call_id = run.uncertain_tool_call_id
        if (
                uncertain_phase not in {item[3] for item in ROLE_PLAN} or
                not any(
                    call["tool_call_id"] == uncertain_tool_call_id
                    for call in run.handoff.calls.values())):
            raise CanaryContractError("ROOT_EMERGENCY_UNCERTAINTY_INVALID")
    else:
        uncertainty_kind = "PRE_TOOL"
        uncertain_phase = "NOT_APPLICABLE"
        uncertain_tool_call_id = "NOT_APPLICABLE"
    evidence_body = {
        "schema": ROOT_EMERGENCY_EVIDENCE_SCHEMA,
        "version": VERSION,
        "created_at_ms": created_at_ms,
        "campaign_id": handoff["campaign_id"],
        "domain_id": handoff["domain_id"],
        "cycle_id": handoff["cycle_id"],
        "handoff_path": handoff_path_for(run.handoff),
        "handoff_file_sha256": run.handoff.file_sha256,
        "handoff_body_sha256": run.handoff.body_sha256,
        "intent_sha256": handoff["intent_sha256"],
        "installed_images_sha256": handoff["installed_images_sha256"],
        "executor_image_sha256": run.handoff.images["executor"][
            "file_sha256"],
        "backend_adapter_image_sha256": run.handoff.images[
            "backend-adapter"]["file_sha256"],
        "root_finalizer_image_sha256": run.handoff.images[
            "root-finalizer"]["file_sha256"],
        "backend_transform_version": BACKEND_TRANSFORM_VERSION,
        "session_owner_reference_sha256": canonical_sha256(
            handoff["session_owner_reference"]),
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": handoff[
            "execution_service_fencing_generation"],
        "journal_path": journal.path,
        "journal_sha256": sha256_bytes(journal.raw),
        "journal_size": len(journal.raw),
        "journal_last_sequence": len(records) - 1,
        "tool_evidence_sha256": canonical_sha256(evidence),
        "recovery_reason_codes": reasons,
        "last_known_state": run.last_state,
        "last_known_state_sha256": canonical_sha256(run.last_state),
        "cycle_opened": run.cycle_opened,
        "cycle_closed": run.cycle_closed,
        "place_attempted": run.place_attempted,
        "close_attempted": run.close_attempted,
        "close_outcome": run.close_outcome or "NOT_APPLICABLE",
        "uncertainty_kind": uncertainty_kind,
        "last_completed_phase": run.last_completed_phase,
        "uncertain_phase": uncertain_phase,
        "uncertain_tool_call_id": uncertain_tool_call_id,
        "broker_flat_proven": False,
        "authority_granted": False,
    }
    emergency_evidence = _sealed_body(evidence_body)
    emergency_evidence_raw = canonical_json(emergency_evidence)
    root_call = handoff["root_cleanup_call"]
    # The handoff must be fresh when recovery starts, but the root-owned
    # emergency cleanup gets its own bounded window once issued.  Clamping
    # this request to the mutation handoff can strand a late crash after the
    # handoff expires, before DENY_ALL and connector-zero are re-established.
    expires_at_ms = created_at_ms + ROOT_EMERGENCY_CLEANUP_TIMEOUT_MS
    if expires_at_ms <= created_at_ms:
        raise CanaryContractError("ROOT_EMERGENCY_REQUEST_EXPIRED")
    request_body = {
        "schema": ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA,
        "version": VERSION,
        "issued_at_ms": created_at_ms,
        "expires_at_ms": expires_at_ms,
        "campaign_id": handoff["campaign_id"],
        "domain_id": handoff["domain_id"],
        "cycle_id": handoff["cycle_id"],
        "cleanup_tool_call_id": root_call["tool_call_id"],
        "cleanup_command_id": root_call["command_id"],
        "tool_descriptor_sha256": root_call["tool_descriptor_sha256"],
        "handoff_file_sha256": run.handoff.file_sha256,
        "handoff_body_sha256": run.handoff.body_sha256,
        "session_owner_reference_sha256": canonical_sha256(
            handoff["session_owner_reference"]),
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": handoff[
            "execution_service_fencing_generation"],
        "emergency_evidence_path": emergency_cleanup_evidence_path_for(
            run.handoff),
        "emergency_evidence_file_sha256": sha256_bytes(
            emergency_evidence_raw),
        "emergency_evidence_body_sha256": emergency_evidence["body_sha256"],
        "recovery_reason_codes": reasons,
        "required_actions": list(ROOT_EMERGENCY_CLEANUP_ACTIONS),
        "broker_flat_proven": False,
        "paper_only": True,
        "live_authorized": False,
        "authority_granted": False,
    }
    request = _sealed_body(request_body)
    request_raw = canonical_json(request)
    run.emergency_cleanup_evidence = emergency_evidence
    run.emergency_cleanup_evidence_raw = emergency_evidence_raw
    run.root_emergency_cleanup_request = request
    run.root_emergency_cleanup_request_raw = request_raw
    return emergency_evidence, emergency_evidence_raw, request, request_raw


def _validate_root_emergency_cleanup_receipt(
        raw: bytes, *, run: _Run, evidence_raw: bytes, request_raw: bytes,
        journal: JournalSnapshot,
) -> dict[str, Any]:
    receipt = _load_canonical(raw, "ROOT_EMERGENCY_CLEANUP_RECEIPT")
    _exact(
        receipt, ROOT_EMERGENCY_CLEANUP_RECEIPT_FIELDS,
        "ROOT_EMERGENCY_RECEIPT_FIELDS_INVALID")
    if (
            receipt["schema"] != ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA or
            receipt["version"] != VERSION or
            receipt["status"] != "ROOT_EMERGENCY_CLEANUP_COMPLETE_DENY_ALL" or
            receipt["completed_actions"] !=
                list(ROOT_EMERGENCY_CLEANUP_ACTIONS) or
            receipt["paper_only"] is not True or
            receipt["live_authorized"] is not False or
            receipt["authority_granted"] is not False or
            receipt["broker_flat_proven"] is not False or
            receipt["recovery_required"] is not True or
            receipt["evidence_retained"] is not True):
        raise CanaryContractError("ROOT_EMERGENCY_RECEIPT_IDENTITY_INVALID")
    _sealed(receipt, "ROOT_EMERGENCY_RECEIPT_BODY_INVALID")
    handoff = run.handoff.document
    request = run.root_emergency_cleanup_request
    evidence = run.emergency_cleanup_evidence
    if request is None or evidence is None:
        raise CanaryContractError("ROOT_EMERGENCY_CHECKPOINT_MISSING")
    expected = {
        "campaign_id": handoff["campaign_id"],
        "domain_id": handoff["domain_id"],
        "cycle_id": handoff["cycle_id"],
        "cleanup_tool_call_id": handoff["root_cleanup_call"]["tool_call_id"],
        "cleanup_command_id": handoff["root_cleanup_call"]["command_id"],
        "tool_descriptor_sha256": handoff["root_cleanup_call"][
            "tool_descriptor_sha256"],
        "execution_handoff_path": handoff_path_for(run.handoff),
        "execution_handoff_file_sha256": run.handoff.file_sha256,
        "execution_handoff_body_sha256": run.handoff.body_sha256,
        "watch_handoff_file_sha256": handoff[
            "watch_handoff_receipt_file_sha256"],
        "watch_handoff_body_sha256": handoff[
            "watch_handoff_receipt_body_sha256"],
        "intent_sha256": handoff["intent_sha256"],
        "installed_images_sha256": handoff["installed_images_sha256"],
        "executor_image_sha256": run.handoff.images["executor"][
            "file_sha256"],
        "backend_adapter_image_sha256": run.handoff.images[
            "backend-adapter"]["file_sha256"],
        "root_finalizer_image_sha256": run.handoff.images[
            "root-finalizer"]["file_sha256"],
        "backend_transform_version": BACKEND_TRANSFORM_VERSION,
        "session_owner_reference_sha256": canonical_sha256(
            handoff["session_owner_reference"]),
        "execution_service_epoch": handoff["execution_service_epoch"],
        "execution_service_fencing_generation": handoff[
            "execution_service_fencing_generation"],
        "journal_path": journal.path,
        "journal_sha256": sha256_bytes(journal.raw),
        "journal_size": len(journal.raw),
        "journal_last_sequence": evidence["journal_last_sequence"],
        "tool_evidence_sha256": evidence["tool_evidence_sha256"],
        "emergency_evidence_path": emergency_cleanup_evidence_path_for(
            run.handoff),
        "emergency_evidence_file_sha256": sha256_bytes(evidence_raw),
        "emergency_evidence_body_sha256": evidence["body_sha256"],
        "root_emergency_cleanup_request_path":
            root_emergency_cleanup_request_path_for(run.handoff),
        "root_emergency_cleanup_request_file_sha256": sha256_bytes(request_raw),
        "root_emergency_cleanup_request_body_sha256": request["body_sha256"],
        "recovery_reason_codes": request["recovery_reason_codes"],
    }
    if any(receipt[field] != value for field, value in expected.items()):
        raise CanaryContractError("ROOT_EMERGENCY_RECEIPT_BINDING_MISMATCH")
    completed_at_ms = _integer(
        receipt["completed_at_ms"], "ROOT_EMERGENCY_RECEIPT_TIME_INVALID")
    if not request["issued_at_ms"] <= completed_at_ms <= request["expires_at_ms"]:
        raise CanaryContractError("ROOT_EMERGENCY_RECEIPT_TIME_INVALID")
    for field in ("guardian_request_id", "local_control_transaction_id"):
        _identifier(receipt[field], "ROOT_EMERGENCY_RECEIPT_ID_INVALID")
    for field in (
            "local_control_request_sha256",
            "guardian_active_receipt_file_sha256",
            "guardian_active_receipt_body_sha256", "identity_manifest_sha256",
            "broker_policy_sha256"):
        _digest(receipt[field], "ROOT_EMERGENCY_RECEIPT_DIGEST_INVALID")
    if (
            receipt["broker_mutation_units"] != list(BROKER_MUTATION_UNITS) or
            receipt["broker_mutation_units_sha256"] != canonical_sha256(
                list(BROKER_MUTATION_UNITS))):
        raise CanaryContractError("ROOT_EMERGENCY_MUTATION_UNITS_INVALID")
    for field in (
            "guardian_stopped", "execution_control_disabled",
            "kill_switch_engaged", "global_kill_switch_engaged",
            "broker_deny_all", "broker_mutation_units_inactive", "permit_absent",
            "guardian_runtime_absent"):
        if receipt[field] is not True:
            raise CanaryContractError("ROOT_EMERGENCY_PROOF_INCOMPLETE")
    for field in (
            "runtime_session_count", "authorized_connector_count",
            "identity_count"):
        if _integer(
                receipt[field], "ROOT_EMERGENCY_PROOF_INCOMPLETE",
                minimum=0) != 0:
            raise CanaryContractError("ROOT_EMERGENCY_PROOF_INCOMPLETE")
    if (
            receipt["durable_owner_reference_sha256"] != canonical_sha256(
                handoff["session_owner_reference"]) or
            receipt["durable_owner_count"] != 1 or
            receipt["durable_owner_status"] != "RECOVERY_ONLY" or
            receipt["durable_recovery_owner_reference_path"] !=
                durable_recovery_owner_reference_path_for(run.handoff)):
        raise CanaryContractError("ROOT_EMERGENCY_OWNER_RECOVERY_INVALID")
    for field in (
            "durable_recovery_owner_reference_file_sha256",
            "durable_recovery_owner_reference_body_sha256"):
        _digest(receipt[field], "ROOT_EMERGENCY_OWNER_RECOVERY_INVALID")
    return receipt


def _root_emergency_cleanup(run: _Run) -> bool:
    if run.root_cleanup_attempted:
        return False
    checkpoint = _journal_snapshot(run.backend)
    try:
        evidence, evidence_raw, request, request_raw = \
            _emergency_cleanup_documents(run, checkpoint)
        run.backend.publish_checkpoint({
            "root-emergency-cleanup-evidence.v1.json": evidence_raw,
            "root-emergency-cleanup-request.v1.json": request_raw,
        })
    except Exception:
        run.fail("ROOT_EMERGENCY_CHECKPOINT_PUBLISH_FAILED")
        return False
    run.root_cleanup_attempted = True
    try:
        receipt_raw = run.backend.finalize_root_cleanup(request_raw)
    except Exception:
        run.fail("ROOT_EMERGENCY_CLEANUP_RESPONSE_UNCERTAIN")
        return False
    try:
        receipt = _validate_root_emergency_cleanup_receipt(
            receipt_raw, run=run, evidence_raw=evidence_raw,
            request_raw=request_raw, journal=checkpoint)
    except CanaryContractError as error:
        run.fail(error.code)
        return False
    run.root_emergency_cleanup_receipt = receipt
    run.root_emergency_cleanup_receipt_raw = receipt_raw
    return True


def _actual_bindings(run: _Run, receipt_module: ModuleType) -> dict[str, Any]:
    handoff = run.handoff.document
    calls = [
        {field: evidence[field] for field in TOOL_BINDING_FIELDS}
        for evidence in run.evidence
    ]
    return {
        "campaign_id": handoff["campaign_id"], "domain_id": handoff["domain_id"],
        "policy_sha256": handoff["policy_sha256"],
        "strategy_id": handoff["strategy_id"],
        "strategy_version": handoff["strategy_version"],
        "strategy_sha256": handoff["strategy_sha256"],
        "decision_id": handoff["decision_id"],
        "decision_sha256": handoff["decision_sha256"],
        "cycle_id": handoff["cycle_id"],
        "intent_id": handoff["intent"]["intent_id"],
        "intent_sha256": handoff["intent_sha256"],
        "tool_catalog_sha256": handoff["tool_catalog_sha256"],
        "tool_descriptor_set_sha256": receipt_module.canonical_sha256(calls),
        "tool_calls": calls,
    }


def _receipt_artifacts(
        run: _Run, *, preflight_sha256: str, final_outcome: str,
        cleanup_complete: bool, reasons: list[str], journal: JournalSnapshot,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    current, _compatibility = _receipt_modules(run.handoff)
    bindings = _actual_bindings(run, current)
    bindings_document = current.make_bindings_document(bindings)
    external_artifacts: dict[str, bytes] = {}
    if cleanup_complete:
        if any(value is None for value in (
                run.pre_cleanup_response_bundle,
                run.pre_cleanup_response_bundle_raw,
                run.pre_cleanup_evidence, run.pre_cleanup_evidence_raw,
                run.root_cleanup_request, run.root_cleanup_request_raw,
                run.root_cleanup_receipt, run.root_cleanup_receipt_raw,
                run.composite_snapshot, run.composite_snapshot_raw)):
            raise CanaryContractError("ROOT_CLEANUP_ARTIFACT_SET_INCOMPLETE")
        pre = run.pre_cleanup_evidence
        pre_raw = run.pre_cleanup_evidence_raw
        bundle = run.pre_cleanup_response_bundle
        bundle_raw = run.pre_cleanup_response_bundle_raw
        request = run.root_cleanup_request
        request_raw = run.root_cleanup_request_raw
        root_receipt = run.root_cleanup_receipt
        root_receipt_raw = run.root_cleanup_receipt_raw
        composite_raw = run.composite_snapshot_raw
        assert isinstance(pre, dict) and isinstance(pre_raw, bytes)
        assert isinstance(bundle, dict) and isinstance(bundle_raw, bytes)
        assert isinstance(request, dict) and isinstance(request_raw, bytes)
        assert isinstance(root_receipt, dict) and isinstance(root_receipt_raw, bytes)
        assert isinstance(composite_raw, bytes)
        event_body = {
            "schema": EXTERNAL_EVENT_SUMMARY_SCHEMA,
            "version": VERSION,
            "created_at_ms": run.now_ms(),
            "campaign_id": run.handoff.document["campaign_id"],
            "domain_id": run.handoff.document["domain_id"],
            "cycle_id": run.handoff.document["cycle_id"],
            "cycle_opened": run.cycle_opened,
            "cycle_closed": run.cycle_closed,
            "place_attempted": run.place_attempted,
            "close_attempted": run.close_attempted,
            "close_outcome": run.close_outcome,
            "final_outcome": final_outcome,
            "reason_codes": reasons,
            "pre_cleanup_evidence_file_sha256": sha256_bytes(pre_raw),
            "pre_cleanup_evidence_body_sha256": pre["body_sha256"],
            "root_cleanup_request_file_sha256": sha256_bytes(request_raw),
            "root_cleanup_request_body_sha256": request["body_sha256"],
            "root_cleanup_receipt_file_sha256": sha256_bytes(root_receipt_raw),
            "root_cleanup_receipt_body_sha256": root_receipt["body_sha256"],
            "authority_granted": False,
        }
        event_summary = _sealed_body(event_body)
        event_summary_raw = canonical_json(event_summary)
        event_summary_sha256 = sha256_bytes(event_summary_raw)
        external_artifacts = {
            "pre-cleanup-response-bundle.v1.json": bundle_raw,
            "pre-cleanup-flat-evidence.v1.json": pre_raw,
            "root-cleanup-request.v1.json": request_raw,
            "root-cleanup-receipt.v4.json": root_receipt_raw,
            "external-p1-event-summary.v1.json": event_summary_raw,
            "composite-snapshot.v1.json": composite_raw,
        }
    else:
        event_summary_sha256 = canonical_sha256({
            "cycle_opened": run.cycle_opened,
            "cycle_closed": run.cycle_closed,
            "place_attempted": run.place_attempted,
            "close_attempted": run.close_attempted,
            "close_outcome": run.close_outcome,
            "root_cleanup_attempted": run.root_cleanup_attempted,
            "root_cleanup_complete": run.root_cleanup_complete,
            "final_outcome": final_outcome,
            "reason_codes": reasons,
        })
        if run.emergency_cleanup_evidence_raw is not None:
            external_artifacts[
                "root-emergency-cleanup-evidence.v1.json"] = \
                run.emergency_cleanup_evidence_raw
        if run.root_emergency_cleanup_request_raw is not None:
            external_artifacts[
                "root-emergency-cleanup-request.v1.json"] = \
                run.root_emergency_cleanup_request_raw
        if run.root_emergency_cleanup_receipt_raw is not None:
            external_artifacts[
                "root-emergency-cleanup-receipt.v1.json"] = \
                run.root_emergency_cleanup_receipt_raw
    payload = {
        "bindings": bindings,
        "bindings_sha256": bindings_document["bindings_sha256"],
        "started_at_ms": run.started_at_ms, "finished_at_ms": run.now_ms(),
        "paper_only": True, "live_authorized": False,
        "direct_broker_access": False, "execution_mode": "PAPER",
        "preflight_sha256": preflight_sha256,
        "preview_receipt_sha256": run.preview_sha256,
        "broker_order_id_sha256": run.order_id_sha256,
        "journal_sha256": sha256_bytes(journal.raw),
        "event_summary_sha256": event_summary_sha256,
        "mutation_attempted": any(
            item["effect"] == "MUTATION" for item in run.evidence),
        "tool_evidence": run.evidence,
        "final_authoritative_state": run.last_state,
        "cleanup_complete": cleanup_complete, "reason_codes": reasons,
        "final_outcome": final_outcome,
    }
    receipt = current.make_receipt(current.CYCLE_SCHEMA, payload)
    evidence = {
        "preflight_sha256": payload["preflight_sha256"],
        "preview_receipt_sha256": payload["preview_receipt_sha256"],
        "broker_order_id_sha256": payload["broker_order_id_sha256"],
        "journal_sha256": payload["journal_sha256"],
        "event_summary_sha256": payload["event_summary_sha256"],
        "tool_evidence_sha256": current.canonical_sha256(run.evidence),
        "final_authoritative_state_sha256": current.canonical_sha256(run.last_state),
        "final_snapshot_sha256": run.last_state["snapshot_sha256"],
        "final_service_epoch": run.last_state["service_epoch"],
        "final_fencing_generation": run.last_state["fencing_generation"],
    }
    evidence_binding = {
        "bindings_sha256": bindings_document["bindings_sha256"],
        "receipt_schema": current.CYCLE_SCHEMA,
        "payload_sha256": receipt["payload_sha256"], "evidence": evidence,
    }
    evidence_document = {
        "schema": current.EVIDENCE_BINDINGS_SCHEMA, "version": current.VERSION,
        "evidence_bindings": evidence_binding,
        "evidence_bindings_sha256": current.canonical_sha256(evidence_binding),
    }
    current.validate_receipt_document(
        receipt, bindings_document, evidence_document)
    artifacts = {
        **external_artifacts,
        "bindings-v3.json": canonical_json(bindings_document),
        "evidence-bindings-v3.json": canonical_json(evidence_document),
        "receipt-v3.json": canonical_json(receipt),
    }
    return artifacts, {
        "bindings": bindings_document, "evidence": evidence_document,
        "receipt": receipt,
    }


def _mechanical_v2(
        run: _Run, v3: dict[str, Any], artifacts: dict[str, bytes]
) -> dict[str, bytes]:
    current, compatibility = _receipt_modules(run.handoff)
    forbidden = any(
        item["tool_name"] == "execution.get_command_status" or
        item["status"] != "OK" for item in run.evidence)
    if forbidden or run.recovery_used or run.reasons:
        raise CanaryContractError("V2_COMPATIBILITY_NOT_ELIGIBLE")
    bindings_v2 = dict(v3["bindings"])
    bindings_v2["schema"] = compatibility.BINDINGS_SCHEMA
    bindings_v2["version"] = compatibility.VERSION
    receipt_v2 = dict(v3["receipt"])
    receipt_v2["schema"] = compatibility.CYCLE_SCHEMA
    receipt_v2["version"] = compatibility.VERSION
    evidence_v2 = dict(v3["evidence"])
    evidence_v2["schema"] = compatibility.EVIDENCE_BINDINGS_SCHEMA
    evidence_v2["version"] = compatibility.VERSION
    evidence_binding = dict(evidence_v2["evidence_bindings"])
    evidence_binding["receipt_schema"] = compatibility.CYCLE_SCHEMA
    evidence_v2["evidence_bindings"] = evidence_binding
    evidence_v2["evidence_bindings_sha256"] = compatibility.canonical_sha256(
        evidence_binding)
    compatibility.validate_receipt_document(receipt_v2, bindings_v2, evidence_v2)
    v2_artifacts = {
        "bindings-v2-compat.json": canonical_json(bindings_v2),
        "evidence-bindings-v2-compat.json": canonical_json(evidence_v2),
        "receipt-v2-compat.json": canonical_json(receipt_v2),
    }
    cross = _make_cross_binding(run.handoff, artifacts, v2_artifacts)
    v2_artifacts["receipt-v2-v3-cross-binding.json"] = canonical_json(cross)
    validate_cross_binding(
        v2_artifacts["receipt-v2-v3-cross-binding.json"],
        handoff_raw=run.handoff.raw, artifacts={**artifacts, **v2_artifacts})
    return v2_artifacts


def _make_cross_binding(
        handoff: Handoff, v3_artifacts: Mapping[str, bytes],
        v2_artifacts: Mapping[str, bytes]) -> dict[str, Any]:
    current, compatibility = _receipt_modules(handoff)
    v3_bindings = _load_canonical(v3_artifacts["bindings-v3.json"], "V3_BINDINGS")
    v3_evidence = _load_canonical(
        v3_artifacts["evidence-bindings-v3.json"], "V3_EVIDENCE")
    v3_receipt = _load_canonical(v3_artifacts["receipt-v3.json"], "V3_RECEIPT")
    v2_bindings = _load_canonical(
        v2_artifacts["bindings-v2-compat.json"], "V2_BINDINGS")
    v2_evidence = _load_canonical(
        v2_artifacts["evidence-bindings-v2-compat.json"], "V2_EVIDENCE")
    v2_receipt = _load_canonical(
        v2_artifacts["receipt-v2-compat.json"], "V2_RECEIPT")
    external_documents: dict[str, dict[str, Any]] = {}
    for name in (
            "pre-cleanup-response-bundle.v1.json",
            "pre-cleanup-flat-evidence.v1.json",
            "root-cleanup-request.v1.json", "root-cleanup-receipt.v4.json",
            "external-p1-event-summary.v1.json",
            "composite-snapshot.v1.json"):
        if name not in v3_artifacts:
            raise CanaryContractError("CROSS_EXTERNAL_ARTIFACT_MISSING")
        external_documents[name] = _load_canonical(
            v3_artifacts[name], "CROSS_EXTERNAL_ARTIFACT")
    external_p1 = {
        "response_bundle_file_sha256": sha256_bytes(
            v3_artifacts["pre-cleanup-response-bundle.v1.json"]),
        "response_bundle_body_sha256": external_documents[
            "pre-cleanup-response-bundle.v1.json"]["body_sha256"],
        "pre_cleanup_evidence_file_sha256": sha256_bytes(
            v3_artifacts["pre-cleanup-flat-evidence.v1.json"]),
        "pre_cleanup_evidence_body_sha256": external_documents[
            "pre-cleanup-flat-evidence.v1.json"]["body_sha256"],
        "root_cleanup_request_file_sha256": sha256_bytes(
            v3_artifacts["root-cleanup-request.v1.json"]),
        "root_cleanup_request_body_sha256": external_documents[
            "root-cleanup-request.v1.json"]["body_sha256"],
        "root_cleanup_receipt_file_sha256": sha256_bytes(
            v3_artifacts["root-cleanup-receipt.v4.json"]),
        "root_cleanup_receipt_body_sha256": external_documents[
            "root-cleanup-receipt.v4.json"]["body_sha256"],
        "event_summary_file_sha256": sha256_bytes(
            v3_artifacts["external-p1-event-summary.v1.json"]),
        "event_summary_body_sha256": external_documents[
            "external-p1-event-summary.v1.json"]["body_sha256"],
        "composite_snapshot_file_sha256": sha256_bytes(
            v3_artifacts["composite-snapshot.v1.json"]),
        "composite_snapshot_body_sha256": external_documents[
            "composite-snapshot.v1.json"]["body_sha256"],
    }
    body = {
        "schema": CROSS_BINDING_SCHEMA, "version": VERSION,
        "status": "STRICT_V2_V3_COMPATIBLE",
        "handoff_file_sha256": handoff.file_sha256,
        "handoff_body_sha256": handoff.body_sha256,
        "installed_images_sha256": handoff.document[
            "installed_images_sha256"],
        "runtime_profile_sha256": handoff.document[
            "runtime_profile_reference"]["file_sha256"],
        "backend_transform_version": BACKEND_TRANSFORM_VERSION,
        "planned_tool_descriptor_set_sha256":
            handoff.document["tool_descriptor_set_sha256"],
        "shared_bindings_sha256": v3_bindings["bindings_sha256"],
        "shared_payload_sha256": v3_receipt["payload_sha256"],
        "external_p1_evidence": external_p1,
        "v3": {
            "receipt_schema": current.CYCLE_SCHEMA,
            "receipt_file_sha256": sha256_bytes(v3_artifacts["receipt-v3.json"]),
            "receipt_payload_sha256": v3_receipt["payload_sha256"],
            "bindings_file_sha256": sha256_bytes(v3_artifacts["bindings-v3.json"]),
            "bindings_sha256": v3_bindings["bindings_sha256"],
            "evidence_bindings_file_sha256": sha256_bytes(
                v3_artifacts["evidence-bindings-v3.json"]),
            "evidence_bindings_sha256": v3_evidence["evidence_bindings_sha256"],
        },
        "v2": {
            "receipt_schema": compatibility.CYCLE_SCHEMA,
            "receipt_file_sha256": sha256_bytes(
                v2_artifacts["receipt-v2-compat.json"]),
            "receipt_payload_sha256": v2_receipt["payload_sha256"],
            "bindings_file_sha256": sha256_bytes(
                v2_artifacts["bindings-v2-compat.json"]),
            "bindings_sha256": v2_bindings["bindings_sha256"],
            "evidence_bindings_file_sha256": sha256_bytes(
                v2_artifacts["evidence-bindings-v2-compat.json"]),
            "evidence_bindings_sha256": v2_evidence["evidence_bindings_sha256"],
        },
        "mechanical_transform":
            "SCHEMA_AND_ENVELOPE_VERSION_ONLY_PAYLOAD_IDENTICAL",
        "v2_compatibility_only": True,
        "authorization_requirements": [
            "AUTHORITATIVE_V3_RECEIPT",
            "ROUND114_CROSS_BINDING_VALIDATION",
            "SEALED_ROOT_CLEANUP_RECEIPT",
        ],
        "authority_granted": False,
    }
    return {**body, "body_sha256": canonical_sha256(body)}


def validate_cross_binding(
        raw: bytes, *, handoff_raw: bytes,
        artifacts: Mapping[str, bytes]) -> dict[str, Any]:
    cross = _load_canonical(raw, "CROSS_BINDING")
    _exact(cross, CROSS_BINDING_FIELDS, "CROSS_BINDING_FIELDS_INVALID")
    if (
            cross["schema"] != CROSS_BINDING_SCHEMA or cross["version"] != VERSION or
            cross["status"] != "STRICT_V2_V3_COMPATIBLE" or
            cross["mechanical_transform"] !=
                "SCHEMA_AND_ENVELOPE_VERSION_ONLY_PAYLOAD_IDENTICAL" or
            cross["v2_compatibility_only"] is not True or
            cross["authorization_requirements"] != [
                "AUTHORITATIVE_V3_RECEIPT",
                "ROUND114_CROSS_BINDING_VALIDATION",
                "SEALED_ROOT_CLEANUP_RECEIPT"] or
            cross["authority_granted"] is not False):
        raise CanaryContractError("CROSS_BINDING_IDENTITY_INVALID")
    _sealed(cross, "CROSS_BINDING_BODY_INVALID")
    handoff = validate_handoff(handoff_raw, require_fresh=False)
    if (
            cross["handoff_file_sha256"] != handoff.file_sha256 or
            cross["handoff_body_sha256"] != handoff.body_sha256 or
            cross["installed_images_sha256"] !=
                handoff.document["installed_images_sha256"] or
            cross["runtime_profile_sha256"] != handoff.document[
                "runtime_profile_reference"]["file_sha256"] or
            cross["backend_transform_version"] !=
                BACKEND_TRANSFORM_VERSION or
            cross["planned_tool_descriptor_set_sha256"] !=
                handoff.document["tool_descriptor_set_sha256"]):
        raise CanaryContractError("CROSS_BINDING_HANDOFF_MISMATCH")
    current, compatibility = _receipt_modules(handoff)
    documents: dict[str, dict[str, Any]] = {}
    for name in (
            "bindings-v3.json", "evidence-bindings-v3.json", "receipt-v3.json",
            "bindings-v2-compat.json", "evidence-bindings-v2-compat.json",
            "receipt-v2-compat.json"):
        if name not in artifacts:
            raise CanaryContractError("CROSS_BINDING_ARTIFACT_MISSING")
        documents[name] = _load_canonical(artifacts[name], "CROSS_ARTIFACT")
    current.validate_receipt_document(
        documents["receipt-v3.json"], documents["bindings-v3.json"],
        documents["evidence-bindings-v3.json"])
    compatibility.validate_receipt_document(
        documents["receipt-v2-compat.json"],
        documents["bindings-v2-compat.json"],
        documents["evidence-bindings-v2-compat.json"])
    external_names = {
        "response_bundle": "pre-cleanup-response-bundle.v1.json",
        "pre_cleanup_evidence": "pre-cleanup-flat-evidence.v1.json",
        "root_cleanup_request": "root-cleanup-request.v1.json",
        "root_cleanup_receipt": "root-cleanup-receipt.v4.json",
        "event_summary": "external-p1-event-summary.v1.json",
        "composite_snapshot": "composite-snapshot.v1.json",
    }
    external = _exact(
        cross["external_p1_evidence"], EXTERNAL_CROSS_FIELDS,
        "CROSS_EXTERNAL_FIELDS_INVALID")
    external_documents: dict[str, dict[str, Any]] = {}
    for prefix, name in external_names.items():
        if name not in artifacts:
            raise CanaryContractError("CROSS_EXTERNAL_ARTIFACT_MISSING")
        document = _load_canonical(artifacts[name], "CROSS_EXTERNAL_ARTIFACT")
        _sealed(document, "CROSS_EXTERNAL_BODY_INVALID")
        if (
                external[f"{prefix}_file_sha256"] !=
                    sha256_bytes(artifacts[name]) or
                external[f"{prefix}_body_sha256"] != document["body_sha256"]):
            raise CanaryContractError("CROSS_EXTERNAL_ARTIFACT_MISMATCH")
        external_documents[prefix] = document
    pre = _exact(
        external_documents["pre_cleanup_evidence"],
        PRE_CLEANUP_EVIDENCE_FIELDS, "CROSS_PRE_CLEANUP_FIELDS_INVALID")
    bundle = _exact(
        external_documents["response_bundle"],
        PRE_CLEANUP_RESPONSE_BUNDLE_FIELDS,
        "CROSS_RESPONSE_BUNDLE_FIELDS_INVALID")
    request = _exact(
        external_documents["root_cleanup_request"],
        ROOT_CLEANUP_REQUEST_FIELDS, "CROSS_ROOT_REQUEST_FIELDS_INVALID")
    root_receipt = _exact(
        external_documents["root_cleanup_receipt"],
        ROOT_CLEANUP_RECEIPT_FIELDS, "CROSS_ROOT_RECEIPT_FIELDS_INVALID")
    event_summary = _exact(
        external_documents["event_summary"], EXTERNAL_EVENT_SUMMARY_FIELDS,
        "CROSS_EVENT_SUMMARY_FIELDS_INVALID")
    composite = _exact(
        external_documents["composite_snapshot"], COMPOSITE_SNAPSHOT_FIELDS,
        "CROSS_COMPOSITE_FIELDS_INVALID")
    if (
            bundle["schema"] != PRE_CLEANUP_RESPONSE_BUNDLE_SCHEMA or
            pre["schema"] != PRE_CLEANUP_EVIDENCE_SCHEMA or
            request["schema"] != ROOT_CLEANUP_REQUEST_SCHEMA or
            root_receipt["schema"] != ROOT_CLEANUP_RECEIPT_SCHEMA or
            root_receipt["version"] != ROOT_CLEANUP_RECEIPT_VERSION or
            root_receipt["status"] != "ROOT_CLEANUP_COMPLETE_DENY_ALL" or
            event_summary["schema"] != EXTERNAL_EVENT_SUMMARY_SCHEMA or
            composite["schema"] != COMPOSITE_SNAPSHOT_SCHEMA or
            any(item["version"] != VERSION for item in (
                bundle, pre, request, event_summary, composite)) or
            root_receipt["completed_actions"] != list(ROOT_CLEANUP_ACTIONS) or
            any(root_receipt[field] is not True for field in (
                "guardian_stopped", "execution_control_disabled",
                "kill_switch_engaged", "global_kill_switch_engaged",
                "broker_deny_all", "broker_mutation_units_inactive",
                "permit_absent",
                "guardian_runtime_absent")) or
            root_receipt["broker_mutation_units"] !=
                list(BROKER_MUTATION_UNITS) or
            root_receipt["broker_mutation_units_sha256"] != canonical_sha256(
                list(BROKER_MUTATION_UNITS)) or
            any(root_receipt[field] != 0 for field in (
                "runtime_session_count", "authorized_connector_count",
                "identity_count")) or
            root_receipt["durable_owner_count"] != 0 or
            root_receipt["durable_owner_reference_sha256"] != canonical_sha256(
                handoff.document["session_owner_reference"]) or
            root_receipt["durable_owner_status"] != "RETIRED" or
            root_receipt["durable_owner_retirement_receipt_path"] !=
                durable_owner_retirement_receipt_path_for(handoff) or
            pre["broker_state_sha256"] != canonical_sha256(
                pre["broker_state"]) or
            pre["response_bundle_file_sha256"] !=
                external["response_bundle_file_sha256"] or
            pre["response_bundle_body_sha256"] !=
                external["response_bundle_body_sha256"] or
            bundle["claimed_broker_state_sha256"] != canonical_sha256(
                bundle["claimed_broker_state"]) or
            bundle["claimed_broker_state"] != pre["broker_state"] or
            pre["broker_state"]["authorized_connector_count"] != 1 or
            pre["broker_state"]["end_flat"] is not True or
            request["pre_cleanup_evidence_file_sha256"] !=
                external["pre_cleanup_evidence_file_sha256"] or
            request["pre_cleanup_evidence_body_sha256"] !=
                external["pre_cleanup_evidence_body_sha256"] or
            root_receipt["root_cleanup_request_file_sha256"] !=
                external["root_cleanup_request_file_sha256"] or
            root_receipt["root_cleanup_request_body_sha256"] !=
                external["root_cleanup_request_body_sha256"] or
            event_summary["pre_cleanup_evidence_file_sha256"] !=
                external["pre_cleanup_evidence_file_sha256"] or
            event_summary["root_cleanup_receipt_file_sha256"] !=
                external["root_cleanup_receipt_file_sha256"] or
            composite["pre_cleanup_evidence_file_sha256"] !=
                external["pre_cleanup_evidence_file_sha256"] or
            composite["root_cleanup_receipt_file_sha256"] !=
                external["root_cleanup_receipt_file_sha256"] or
            composite["authorized_connector_count"] != 0 or
            composite["end_flat"] is not True or
            documents["receipt-v3.json"]["payload"][
                "event_summary_sha256"] !=
                    external["event_summary_file_sha256"] or
            documents["receipt-v3.json"]["payload"][
                "final_authoritative_state"]["snapshot_sha256"] !=
                    external["composite_snapshot_file_sha256"]):
        raise CanaryContractError("CROSS_EXTERNAL_CHAIN_INVALID")
    for field in (
            "durable_owner_retirement_receipt_file_sha256",
            "durable_owner_retirement_receipt_body_sha256"):
        _digest(root_receipt[field], "CROSS_EXTERNAL_CHAIN_INVALID")
    for version, suffix in (("v3", "v3"), ("v2", "v2-compat")):
        value = _exact(cross[version], CROSS_VERSION_FIELDS,
                       "CROSS_BINDING_VERSION_FIELDS_INVALID")
        receipt_name = f"receipt-{suffix}.json"
        bindings_name = f"bindings-{suffix}.json"
        evidence_name = f"evidence-bindings-{suffix}.json"
        receipt = documents[receipt_name]
        bindings = documents[bindings_name]
        evidence = documents[evidence_name]
        expected_schema = current.CYCLE_SCHEMA if version == "v3" else \
            compatibility.CYCLE_SCHEMA
        if value != {
                "receipt_schema": expected_schema,
                "receipt_file_sha256": sha256_bytes(artifacts[receipt_name]),
                "receipt_payload_sha256": receipt["payload_sha256"],
                "bindings_file_sha256": sha256_bytes(artifacts[bindings_name]),
                "bindings_sha256": bindings["bindings_sha256"],
                "evidence_bindings_file_sha256": sha256_bytes(
                    artifacts[evidence_name]),
                "evidence_bindings_sha256": evidence["evidence_bindings_sha256"]}:
            raise CanaryContractError("CROSS_BINDING_ARTIFACT_MISMATCH")
    if not (
            cross["shared_bindings_sha256"] ==
                cross["v2"]["bindings_sha256"] == cross["v3"]["bindings_sha256"] and
            cross["shared_payload_sha256"] ==
                cross["v2"]["receipt_payload_sha256"] ==
                cross["v3"]["receipt_payload_sha256"]):
        raise CanaryContractError("CROSS_BINDING_SHARED_DIGEST_MISMATCH")
    return cross


def _recovery_record(
        run: _Run, *, bindings_sha256: str, journal: JournalSnapshot,
        reasons: list[str]) -> dict[str, Any]:
    if run.root_emergency_cleanup_receipt is not None:
        cleanup_mode = "EMERGENCY"
        cleanup_evidence = run.emergency_cleanup_evidence
        cleanup_evidence_raw = run.emergency_cleanup_evidence_raw
        cleanup_request = run.root_emergency_cleanup_request
        cleanup_request_raw = run.root_emergency_cleanup_request_raw
        cleanup_receipt = run.root_emergency_cleanup_receipt
        cleanup_receipt_raw = run.root_emergency_cleanup_receipt_raw
        broker_flat_proven = False
    elif run.root_cleanup_receipt is not None:
        cleanup_mode = "NORMAL"
        cleanup_evidence = run.pre_cleanup_evidence
        cleanup_evidence_raw = run.pre_cleanup_evidence_raw
        cleanup_request = run.root_cleanup_request
        cleanup_request_raw = run.root_cleanup_request_raw
        cleanup_receipt = run.root_cleanup_receipt
        cleanup_receipt_raw = run.root_cleanup_receipt_raw
        broker_flat_proven = True
    else:
        cleanup_mode = (
            "ATTEMPTED_UNCERTAIN" if run.root_cleanup_attempted else "NONE")
        cleanup_evidence = (
            run.emergency_cleanup_evidence or run.pre_cleanup_evidence)
        cleanup_evidence_raw = (
            run.emergency_cleanup_evidence_raw or run.pre_cleanup_evidence_raw)
        cleanup_request = (
            run.root_emergency_cleanup_request or run.root_cleanup_request)
        cleanup_request_raw = (
            run.root_emergency_cleanup_request_raw or run.root_cleanup_request_raw)
        cleanup_receipt = None
        cleanup_receipt_raw = None
        broker_flat_proven = False
    body = {
        "schema": RECOVERY_SCHEMA, "version": VERSION,
        "status": "SEALED_RECOVERY_REQUIRED", "created_at_ms": run.now_ms(),
        "handoff_file_sha256": run.handoff.file_sha256,
        "handoff_body_sha256": run.handoff.body_sha256,
        "bindings_sha256": bindings_sha256,
        "installed_images_sha256": run.handoff.document[
            "installed_images_sha256"],
        "runtime_profile_sha256": run.handoff.document[
            "runtime_profile_reference"]["file_sha256"],
        "backend_transform_version": BACKEND_TRANSFORM_VERSION,
        "campaign_id": run.handoff.document["campaign_id"],
        "domain_id": run.handoff.document["domain_id"],
        "policy_sha256": run.handoff.document["policy_sha256"],
        "strategy_sha256": run.handoff.document["strategy_sha256"],
        "decision_sha256": run.handoff.document["decision_sha256"],
        "cycle_id": run.handoff.document["cycle_id"],
        "intent_sha256": run.handoff.document["intent_sha256"],
        "journal_path": journal.path,
        "journal_file_sha256": sha256_bytes(journal.raw),
        "tool_evidence_sha256": canonical_sha256(run.evidence),
        "last_authoritative_snapshot_sha256": run.last_state["snapshot_sha256"],
        "session_owner_reference": run.handoff.document["session_owner_reference"],
        "last_completed_phase": run.last_completed_phase,
        "uncertain_phase": run.uncertain_phase,
        "uncertain_tool_call_id": run.uncertain_tool_call_id,
        "place_attempted": run.place_attempted,
        "place_call_id": run.handoff.calls["place"]["tool_call_id"],
        "close_attempted": run.close_attempted,
        "owned_order_id_sha256": run.owned_order_id_sha256,
        "service_epoch": run.service_epoch or "unobserved",
        "fencing_generation": run.fencing_generation,
        "authoritative_state": run.last_state,
        "cleanup_complete": False, "recovery_required": True,
        "root_cleanup_call_id": run.handoff.document[
            "root_cleanup_call"]["tool_call_id"],
        "root_cleanup_command_id": run.handoff.document[
            "root_cleanup_call"]["command_id"],
        "root_cleanup_attempted": run.root_cleanup_attempted,
        "root_cleanup_mode": cleanup_mode,
        "root_deny_all_proven": cleanup_receipt is not None,
        "broker_flat_proven": broker_flat_proven,
        "root_cleanup_evidence_file_sha256": (
            sha256_bytes(cleanup_evidence_raw)
            if cleanup_evidence_raw is not None else None),
        "root_cleanup_evidence_body_sha256": (
            cleanup_evidence["body_sha256"]
            if cleanup_evidence is not None else None),
        "root_cleanup_request_file_sha256": (
            sha256_bytes(cleanup_request_raw)
            if cleanup_request_raw is not None else None),
        "root_cleanup_request_body_sha256": (
            cleanup_request["body_sha256"]
            if cleanup_request is not None else None),
        "root_cleanup_receipt_schema": (
            cleanup_receipt["schema"] if cleanup_receipt is not None else None),
        "root_cleanup_receipt_status": (
            cleanup_receipt["status"] if cleanup_receipt is not None else None),
        "root_cleanup_receipt_file_sha256": (
            sha256_bytes(cleanup_receipt_raw)
            if cleanup_receipt_raw is not None else None),
        "root_cleanup_receipt_body_sha256": (
            cleanup_receipt["body_sha256"]
            if cleanup_receipt is not None else None),
        "reason_codes": reasons, "authority_granted": False,
    }
    return {**body, "body_sha256": canonical_sha256(body)}


def validate_recovery_record(
        raw: bytes, *, handoff_raw: bytes,
        journal_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    record = _load_canonical(raw, "RECOVERY_RECORD")
    _exact(record, RECOVERY_FIELDS, "RECOVERY_FIELDS_INVALID")
    if (
            record["schema"] != RECOVERY_SCHEMA or record["version"] != VERSION or
            record["status"] != "SEALED_RECOVERY_REQUIRED" or
            record["cleanup_complete"] is not False or
            record["recovery_required"] is not True or
            record["authority_granted"] is not False):
        raise CanaryContractError("RECOVERY_IDENTITY_INVALID")
    _sealed(record, "RECOVERY_BODY_INVALID")
    handoff = validate_handoff(handoff_raw, require_fresh=False)
    snapshot_value = dict(journal_snapshot)
    class _SnapshotBackend:
        def reopen_journal(self) -> Mapping[str, Any]:
            return snapshot_value
    snapshot = _journal_snapshot(_SnapshotBackend())  # type: ignore[arg-type]
    if snapshot.path != journal_path_for(handoff):
        raise CanaryContractError("RECOVERY_JOURNAL_PATH_MISMATCH")
    records = _parse_journal(snapshot.raw, handoff)
    evidence = _journal_evidence(records, handoff)
    if (
            record["handoff_file_sha256"] != handoff.file_sha256 or
            record["handoff_body_sha256"] != handoff.body_sha256 or
            record["installed_images_sha256"] !=
                handoff.document["installed_images_sha256"] or
            record["runtime_profile_sha256"] != handoff.document[
                "runtime_profile_reference"]["file_sha256"] or
            record["backend_transform_version"] !=
                BACKEND_TRANSFORM_VERSION or
            record["campaign_id"] != handoff.document["campaign_id"] or
            record["domain_id"] != handoff.document["domain_id"] or
            record["policy_sha256"] != handoff.document["policy_sha256"] or
            record["strategy_sha256"] != handoff.document["strategy_sha256"] or
            record["decision_sha256"] != handoff.document["decision_sha256"] or
            record["cycle_id"] != handoff.document["cycle_id"] or
            record["intent_sha256"] != handoff.document["intent_sha256"] or
            record["journal_path"] != snapshot.path or
            record["journal_file_sha256"] != sha256_bytes(snapshot.raw) or
            record["tool_evidence_sha256"] != canonical_sha256(evidence) or
            record["session_owner_reference"] !=
                handoff.document["session_owner_reference"] or
            record["place_call_id"] != handoff.calls["place"]["tool_call_id"] or
            record["service_epoch"] !=
                handoff.document["execution_service_epoch"] or
            record["fencing_generation"] != handoff.document[
                "execution_service_fencing_generation"]):
        raise CanaryContractError("RECOVERY_BINDING_MISMATCH")
    _digest(record["bindings_sha256"], "RECOVERY_BINDINGS_INVALID")
    _digest(record["last_authoritative_snapshot_sha256"],
            "RECOVERY_SNAPSHOT_INVALID")
    _identifier(record["service_epoch"], "RECOVERY_EPOCH_INVALID")
    _integer(record["fencing_generation"], "RECOVERY_FENCE_INVALID", minimum=1)
    if record["last_completed_phase"] not in {"NONE", *{item[3] for item in ROLE_PLAN}}:
        raise CanaryContractError("RECOVERY_PHASE_INVALID")
    if record["uncertain_phase"] is not None and record["uncertain_phase"] not in {
            *{item[3] for item in ROLE_PLAN}, "ROOT_CLEANUP"}:
        raise CanaryContractError("RECOVERY_PHASE_INVALID")
    if record["uncertain_tool_call_id"] is not None:
        call_id = _identifier(
            record["uncertain_tool_call_id"], "RECOVERY_CALL_ID_INVALID")
        uncertain = next(
            (call for call in handoff.calls.values()
             if call["tool_call_id"] == call_id), None)
        root_call = handoff.document["root_cleanup_call"]
        if uncertain is None and call_id != root_call["tool_call_id"]:
            raise CanaryContractError("RECOVERY_CALL_ID_INVALID")
        if uncertain is None:
            if (
                    record["uncertain_phase"] != "ROOT_CLEANUP" or
                    root_call["command_id"] != call_id):
                raise CanaryContractError("RECOVERY_CALL_ID_INVALID")
        elif uncertain["effect"] == "READ_ONLY":
            if uncertain["command_id"] is not None:
                raise CanaryContractError("RECOVERY_READ_COMMAND_ID_INVALID")
        elif uncertain["command_id"] != call_id:
            raise CanaryContractError("RECOVERY_EFFECTFUL_COMMAND_ID_INVALID")
    for field in ("place_attempted", "close_attempted"):
        _boolean(record[field], "RECOVERY_ATTEMPT_FLAG_INVALID")
    if record["owned_order_id_sha256"] is not None:
        _digest(record["owned_order_id_sha256"], "RECOVERY_ORDER_ID_INVALID")
    _validate_final_state(record["authoritative_state"])
    if record["last_authoritative_snapshot_sha256"] != record[
            "authoritative_state"]["snapshot_sha256"]:
        raise CanaryContractError("RECOVERY_SNAPSHOT_INVALID")
    root_call = handoff.document["root_cleanup_call"]
    if (
            record["root_cleanup_call_id"] != root_call["tool_call_id"] or
            record["root_cleanup_command_id"] != root_call["command_id"]):
        raise CanaryContractError("RECOVERY_ROOT_CLEANUP_BINDING_MISMATCH")
    attempted = _boolean(
        record["root_cleanup_attempted"], "RECOVERY_ROOT_CLEANUP_INVALID")
    deny_proven = _boolean(
        record["root_deny_all_proven"], "RECOVERY_ROOT_CLEANUP_INVALID")
    broker_flat = _boolean(
        record["broker_flat_proven"], "RECOVERY_ROOT_CLEANUP_INVALID")
    mode = record["root_cleanup_mode"]
    if mode not in {"NONE", "ATTEMPTED_UNCERTAIN", "NORMAL", "EMERGENCY"}:
        raise CanaryContractError("RECOVERY_ROOT_CLEANUP_INVALID")
    for file_field, body_field in (
            ("root_cleanup_evidence_file_sha256",
             "root_cleanup_evidence_body_sha256"),
            ("root_cleanup_request_file_sha256",
             "root_cleanup_request_body_sha256"),
            ("root_cleanup_receipt_file_sha256",
             "root_cleanup_receipt_body_sha256")):
        file_value = record[file_field]
        body_value = record[body_field]
        if (file_value is None) != (body_value is None):
            raise CanaryContractError("RECOVERY_ROOT_CLEANUP_INVALID")
        if file_value is not None:
            _digest(file_value, "RECOVERY_ROOT_CLEANUP_INVALID")
            _digest(body_value, "RECOVERY_ROOT_CLEANUP_INVALID")
    receipt_schema = record["root_cleanup_receipt_schema"]
    receipt_status = record["root_cleanup_receipt_status"]
    receipt_file = record["root_cleanup_receipt_file_sha256"]
    receipt_body = record["root_cleanup_receipt_body_sha256"]
    if mode == "NORMAL":
        if not (
                attempted and deny_proven and broker_flat and
                receipt_schema == ROOT_CLEANUP_RECEIPT_SCHEMA and
                receipt_status == "ROOT_CLEANUP_COMPLETE_DENY_ALL" and
                receipt_file is not None and receipt_body is not None and
                record["root_cleanup_evidence_file_sha256"] is not None and
                record["root_cleanup_request_file_sha256"] is not None):
            raise CanaryContractError("RECOVERY_ROOT_CLEANUP_INVALID")
    elif mode == "EMERGENCY":
        if not (
                attempted and deny_proven and not broker_flat and
                receipt_schema == ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA and
                receipt_status ==
                    "ROOT_EMERGENCY_CLEANUP_COMPLETE_DENY_ALL" and
                receipt_file is not None and receipt_body is not None and
                record["root_cleanup_evidence_file_sha256"] is not None and
                record["root_cleanup_request_file_sha256"] is not None):
            raise CanaryContractError("RECOVERY_ROOT_CLEANUP_INVALID")
    elif mode == "ATTEMPTED_UNCERTAIN":
        if not attempted or deny_proven or broker_flat or any(
                value is not None for value in (
                    receipt_schema, receipt_status, receipt_file, receipt_body)):
            raise CanaryContractError("RECOVERY_ROOT_CLEANUP_INVALID")
    elif (
            attempted or deny_proven or broker_flat or
            any(value is not None for value in (
                receipt_schema, receipt_status, receipt_file, receipt_body))):
        raise CanaryContractError("RECOVERY_ROOT_CLEANUP_INVALID")
    reasons = record["reason_codes"]
    if not isinstance(reasons, list) or not reasons:
        raise CanaryContractError("RECOVERY_REASONS_INVALID")
    _reason_list(reasons)
    request_roles = {
        item["call_role"] for item in records[1:] if item["event"] == "REQUEST"}
    if record["place_attempted"] != ("place" in request_roles):
        raise CanaryContractError("RECOVERY_PLACE_ATTEMPT_MISMATCH")
    if record["close_attempted"] != ("close" in request_roles):
        raise CanaryContractError("RECOVERY_CLOSE_ATTEMPT_MISMATCH")
    return record


def _validate_final_state(value: Any) -> dict[str, Any]:
    state = _exact(value, FINAL_STATE_FIELDS, "FINAL_STATE_FIELDS_INVALID")
    _boolean(state["authoritative"], "FINAL_STATE_INVALID")
    _boolean(state["account_complete"], "FINAL_STATE_INVALID")
    _digest(state["snapshot_sha256"], "FINAL_STATE_INVALID")
    _identifier(state["service_epoch"], "FINAL_STATE_INVALID")
    _integer(state["fencing_generation"], "FINAL_STATE_INVALID", minimum=1)
    orders = state["active_order_id_sha256s"]
    if not isinstance(orders, list) or len(orders) > 1:
        raise CanaryContractError("FINAL_STATE_INVALID")
    for order in orders:
        _digest(order, "FINAL_STATE_INVALID")
    positions = state["positions"]
    if not isinstance(positions, list) or len(positions) > 1:
        raise CanaryContractError("FINAL_STATE_INVALID")
    gross = 0
    for item in positions:
        position = _exact(item, POSITION_FIELDS, "FINAL_STATE_INVALID")
        if position["instrument"] != "EUR.USD":
            raise CanaryContractError("FINAL_STATE_INVALID")
        quantity = _integer(position["quantity"], "FINAL_STATE_INVALID",
                            minimum=-1, maximum=1)
        if quantity == 0:
            raise CanaryContractError("FINAL_STATE_INVALID")
        gross += abs(quantity)
    if state["gross_absolute_position"] != gross:
        raise CanaryContractError("FINAL_STATE_INVALID")
    connectors = _integer(state["authorized_connector_count"],
                          "FINAL_STATE_INVALID", maximum=1)
    expected_flat = not orders and not positions and gross == 0
    if state["end_flat"] is not expected_flat or connectors < 0:
        raise CanaryContractError("FINAL_STATE_INVALID")
    return state


def _prior_journal_result(
        handoff: Handoff, backend: InjectedBackend, now_ms: int,
        snapshot: JournalSnapshot) -> ExecutionResult:
    records = _parse_journal(snapshot.raw, handoff)
    run = _Run(handoff, backend, now_ms)
    run.evidence = _journal_evidence(records, handoff)
    for record in records[1:]:
        if record["event"] != "RESPONSE":
            continue
        run.service_epoch = record["service_epoch"]
        run.fencing_generation = record["fencing_generation"]
        if record["status"] == "OK":
            run.last_completed_phase = record["phase"]
        else:
            run.fail(record["reason_code"], handoff.calls[record["call_role"]])
    request_roles = {
        item["call_role"] for item in records[1:] if item["event"] == "REQUEST"}
    run.place_attempted = "place" in request_roles
    run.close_attempted = "close" in request_roles
    run.fail("PRIOR_JOURNAL_REQUIRES_RECOVERY")
    _root_emergency_cleanup(run)
    reasons = _reason_list(run.reasons)
    current, _compatibility = _receipt_modules(run.handoff)
    bindings_document = current.make_bindings_document(
        _actual_bindings(run, current))
    recovery = _recovery_record(
        run, bindings_sha256=bindings_document["bindings_sha256"],
        journal=snapshot, reasons=reasons)
    artifacts = {
        "bindings-v3.json": canonical_json(bindings_document),
        "recovery-record-v1.json": canonical_json(recovery),
    }
    if run.emergency_cleanup_evidence_raw is not None:
        artifacts["root-emergency-cleanup-evidence.v1.json"] = \
            run.emergency_cleanup_evidence_raw
    if run.root_emergency_cleanup_request_raw is not None:
        artifacts["root-emergency-cleanup-request.v1.json"] = \
            run.root_emergency_cleanup_request_raw
    if run.root_emergency_cleanup_receipt_raw is not None:
        artifacts["root-emergency-cleanup-receipt.v1.json"] = \
            run.root_emergency_cleanup_receipt_raw
    validate_recovery_record(
        artifacts["recovery-record-v1.json"], handoff_raw=handoff.raw,
        journal_snapshot={
            "path": snapshot.path, "raw": snapshot.raw,
            "secure_reopen": True, "mode": snapshot.mode, "nlink": snapshot.nlink})
    backend.publish_artifacts(artifacts)
    return ExecutionResult("RECOVERY_REQUIRED", False, False, artifacts)


def execute(handoff_raw: bytes, backend: InjectedBackend) -> ExecutionResult:
    """Execute one immutable canary handoff with at-most-once effectful calls."""

    now_ms = _integer(backend.now_ms(), "BACKEND_NOW_INVALID")
    handoff = validate_handoff(handoff_raw, now_ms=now_ms)
    _validate_running_executor_image(handoff.images["executor"])
    initial_journal = _journal_snapshot(backend)
    if initial_journal.path != journal_path_for(handoff):
        raise CanaryContractError("JOURNAL_PATH_MISMATCH")
    if initial_journal.raw:
        return _prior_journal_result(handoff, backend, now_ms, initial_journal)
    run = _Run(handoff, backend, now_ms)
    run.append_header()
    preflight_sha256 = _preflight(run)
    if preflight_sha256 is not None and not run.reasons:
        _open_preview_place_close(run, preflight_sha256)
    if preflight_sha256 is None:
        preflight_sha256 = canonical_sha256({
            "handoff_file_sha256": handoff.file_sha256,
            "completed_preflight_calls": [
                item["tool_call_id"] for item in run.evidence],
        })
    cancelled = False
    flattened = False
    cleanup_complete = False
    if (
            run.order_id_sha256 is not None and run.cycle_closed and
            not run.reasons):
        cancelled, flattened = _risk_reduce(run)
        if not run.reasons:
            pre_cleanup_flat = _pre_cleanup_reconcile(run)
            if pre_cleanup_flat and not run.reasons:
                cleanup_complete = _root_cleanup(run)
    if cleanup_complete and not run.reasons:
        if cancelled and flattened:
            final_outcome = "RECOVERED"
            run.reasons = _reason_list(
                run.reasons + ["ACTIVE_ORDER_AND_POSITION_REDUCED"])
        elif cancelled:
            final_outcome = "CANCELLED_FLAT"
        elif flattened:
            final_outcome = "FILLED_AND_FLAT"
        else:
            run.fail("CANARY_ORDER_NOT_RISK_REDUCED")
            final_outcome = "RECOVERY_REQUIRED"
            cleanup_complete = False
    else:
        final_outcome = "RECOVERY_REQUIRED"
    if final_outcome == "RECOVERY_REQUIRED" and not run.reasons:
        run.fail("CANARY_RECOVERY_REQUIRED")
    if final_outcome == "RECOVERY_REQUIRED" and not run.root_cleanup_attempted:
        _root_emergency_cleanup(run)
    reasons = _reason_list(run.reasons)
    journal = _journal_snapshot(backend)
    _parse_journal(journal.raw, handoff)
    receipt_artifacts, documents = _receipt_artifacts(
        run, preflight_sha256=preflight_sha256, final_outcome=final_outcome,
        cleanup_complete=cleanup_complete, reasons=reasons, journal=journal)
    artifacts = dict(receipt_artifacts)
    v2_compatible = False
    if final_outcome in {"CANCELLED_FLAT", "FILLED_AND_FLAT"}:
        v2_artifacts = _mechanical_v2(run, documents, artifacts)
        artifacts.update(v2_artifacts)
        v2_compatible = True
    elif final_outcome == "RECOVERY_REQUIRED":
        recovery = _recovery_record(
            run, bindings_sha256=documents["bindings"]["bindings_sha256"],
            journal=journal, reasons=reasons)
        artifacts["recovery-record-v1.json"] = canonical_json(recovery)
        validate_recovery_record(
            artifacts["recovery-record-v1.json"], handoff_raw=handoff.raw,
            journal_snapshot={
                "path": journal.path, "raw": journal.raw,
                "secure_reopen": True, "mode": journal.mode,
                "nlink": journal.nlink})
    result_body = {
        "schema": RESULT_SCHEMA, "version": VERSION,
        "status": (
            "SUCCESS" if final_outcome in {
                "CANCELLED_FLAT", "FILLED_AND_FLAT", "RECOVERED"}
            else "RECOVERY_REQUIRED"),
        "handoff_file_sha256": handoff.file_sha256,
        "handoff_body_sha256": handoff.body_sha256,
        "installed_images_sha256": handoff.document[
            "installed_images_sha256"],
        "runtime_profile_sha256": handoff.document[
            "runtime_profile_reference"]["file_sha256"],
        "backend_transform_version": BACKEND_TRANSFORM_VERSION,
        "receipt_v3_file_sha256": sha256_bytes(artifacts["receipt-v3.json"]),
        "receipt_v2_compat_file_sha256": (
            sha256_bytes(artifacts["receipt-v2-compat.json"])
            if v2_compatible else None),
        "recovery_record_file_sha256": (
            sha256_bytes(artifacts["recovery-record-v1.json"])
            if "recovery-record-v1.json" in artifacts else None),
        "v2_compatible": v2_compatible, "cleanup_complete": cleanup_complete,
        "authority_granted": False,
    }
    result = {**result_body, "body_sha256": canonical_sha256(result_body)}
    artifacts["execution-result-v1.json"] = canonical_json(result)
    backend.publish_artifacts(artifacts)
    return ExecutionResult(
        result["status"], False, v2_compatible, artifacts)


def _credential_handoff() -> bytes:
    path = HANDOFF_CREDENTIAL_PATH
    try:
        before = os.lstat(path)
        if (
                stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != os.geteuid() or
                stat.S_IMODE(before.st_mode) not in {0o400, 0o600} or
                before.st_size < 1 or before.st_size > MAX_BYTES):
            raise CanaryContractError("HANDOFF_CREDENTIAL_METADATA_INVALID")
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            raw = bytearray()
            while len(raw) <= MAX_BYTES:
                chunk = os.read(
                    descriptor, min(65536, MAX_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CanaryContractError("HANDOFF_CREDENTIAL_UNAVAILABLE") from error
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
        item.st_uid, item.st_gid, item.st_size, item.st_mtime_ns,
        item.st_ctime_ns)
    if (
            len(raw) > MAX_BYTES or identity(before) != identity(opened) or
            identity(opened) != identity(after)):
        raise CanaryContractError("HANDOFF_CREDENTIAL_CHANGED")
    return bytes(raw)


def _fixed_backend(handoff: Handoff) -> InjectedBackend:
    expected = handoff.images["backend-adapter"]
    raw = _stable_image_bytes(Path(expected["path"]), expected)
    module = ModuleType("_hepta_p1_paper_canary_fixed_backend_adapter")
    module.__file__ = expected["path"]
    try:
        exec(compile(raw, expected["path"], "exec"), module.__dict__)
        factory = getattr(
            module, "create_hepta_p1_paper_canary_backend", None)
        if not callable(factory):
            raise CanaryContractError("BACKEND_FACTORY_MISSING")
        backend = factory(executor_module=sys.modules[__name__], handoff=handoff)
    except CanaryContractError:
        raise
    except Exception as error:
        raise CanaryContractError(
            "BACKEND_ADAPTER_INITIALIZATION_FAILED") from error
    return backend


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.parse_args(argv)
    try:
        raw = _credential_handoff()
        handoff = validate_handoff(
            raw, now_ms=time.time_ns() // 1_000_000)
        backend = _fixed_backend(handoff)
        result = execute(raw, backend)
    except CanaryContractError as error:
        print(f"hepta-p1-paper-canary-executor: FAIL {error.code}")
        return 2
    print(canonical_json({
        "authority_granted": False, "status": result.status,
        "v2_compatible": result.v2_compatible,
    }).decode("ascii"), end="")
    return 0 if result.status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
