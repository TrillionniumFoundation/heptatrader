#!/usr/bin/env python3
"""Produce P1 PAPER zero-exposure inputs from offline signed evidence.

The target never opens a broker or account connection.  A root operator first
asks this fixed installed executable to issue a fresh challenge.  A reviewed
independent remote read-only authority returns canonical full-account evidence
signed by the pinned Ed25519 key.  This executable verifies that signature,
independently observes the target's local deny-all boundary twice, and then
publishes canonical no-replace broker and account snapshots.

Missing, unsigned, stale, incomplete, or mismatched account authority is an
error, never an empty account.  This program has no order or mutation method.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


VERSION = 1
ROUND = 114
DOMAIN_ID = "alpha"
ROOT_UID = 0
ROOT_GID = 0
PAPER_CONTROL_GID = 2121

INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-paper-zero-exposure-snapshot-producer")
BROKER_POLICY_HELPER = Path("/usr/libexec/hepta-broker-egress-policy")
SIGNATURE_VERIFIER = Path("/usr/bin/openssl")
VERIFICATION_KEY = Path(
    "/etc/heptatrader/p1-paper-account-evidence-ed25519.pub")
SYSTEMCTL = "/usr/bin/systemctl"
PYTHON = "/usr/bin/python3.12"
KILL_SWITCH_PATH = Path("/run/hepta/ib-paper-control-alpha/kill-switch")
GLOBAL_KILL_SWITCH_PATH = Path("/run/hepta/ib-paper-control/kill-switch")
GLOBAL_PAPER_CONTROL_GID = 2003
IDENTITY_MANIFEST_PATH = Path(
    "/etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json")
DISABLED_IDENTITY_MANIFEST_SHA256 = (
    "sha256:4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435")
PAPER_PROFILE_PATH = Path("/etc/heptatrader/trust-domains/alpha.env")
PAPER_PROFILE_DORMANT_BACKUP_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-backups/"
    "round114-dormant-paper-to-watch/alpha.env")
PAPER_PROFILE_FORWARD_RETAINED_PATH = PAPER_PROFILE_PATH.with_name(
    ".alpha.env.hepta-p1-round114-dormant-paper-to-watch.retained")
PAPER_PROFILE_FORWARD_PREIMAGE_PATH = (
    PAPER_PROFILE_DORMANT_BACKUP_PATH.with_name("preimage-evidence.json"))
PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/"
    "round114-dormant-paper-to-watch.json")
PAPER_PROFILE_DEPLOYMENT_RECEIPT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/"
    "round114-generation22.json")
PAPER_PROFILE_CANDIDATE_PATH = PAPER_PROFILE_PATH.with_name(
    ".alpha.env.hepta-p1-round114-watch-to-paper.candidate")
PAPER_PROFILE_RETIRED_WATCH_PATH = Path(
    "/var/lib/heptatrader/p1-watch-to-paper-handoff/round114/"
    "retired-watch-profile.env")
PAPER_PROFILE_DORMANT_SHA256 = (
    "sha256:e5866254918ebb23c39c3e3630b9281ab780ad82c2cdb8f63e68749b1f4e9012")
PAPER_PROFILE_DORMANT_BYTES = 878
PAPER_PROFILE_WATCH_SHA256 = (
    "sha256:ffcde4c46237ecacb3c32603f3aca0ba1a51c5b353b4fd2e5ab2f42ca1470e3f")
PAPER_PROFILE_WATCH_BYTES = 736
PAPER_RUNTIME_PROFILE_PATH = Path(
    "/etc/heptatrader/trust-domains/alpha.ib-paper.env")
PAPER_RUNTIME_PROFILE_CANDIDATE_PATH = PAPER_RUNTIME_PROFILE_PATH.with_name(
    ".alpha.ib-paper.env.hepta-p1-round114-runtime-harden.candidate")
PAPER_RUNTIME_PROFILE_BACKUP_PATH = Path(
    "/var/lib/heptatrader/p1-watch-to-paper-handoff/round114/"
    "legacy-paper-runtime-profile-backup.env")
PAPER_RUNTIME_PROFILE_RETAINED_PATH = Path(
    "/var/lib/heptatrader/p1-watch-to-paper-handoff/round114/"
    "retained-legacy-paper-runtime-profile.env")
PAPER_RUNTIME_PROFILE_HARDENED_SHA256 = (
    "sha256:99dd8ab1cd612989906a972abcaad0dd4234d908ea4ce295c0c01a9059604ee4")
PAPER_RUNTIME_PROFILE_HARDENED_BYTES = 767
PAPER_RUNTIME_PROFILE_LEGACY_SHA256 = (
    "sha256:2537f50ffe51f74e975f452e570d2c8ddaa82e1757955443014f5f28c9170f03")
PAPER_RUNTIME_PROFILE_LEGACY_BYTES = 776
PROC_ROOT = Path("/proc")
SYSTEMD_CREDENTIAL_ROOT = Path("/run/credentials")
HOST_AUTHORITY_DIRECTORY = Path("/run/hepta/ib-paper-host-authority")
HOST_AUTHORITY_LEASE_PATH = HOST_AUTHORITY_DIRECTORY / "lease.lock"
HOST_AUTHORITY_OWNER_PATH = HOST_AUTHORITY_DIRECTORY / "owner.v1"
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")

PRODUCTION_MODE = "PRODUCTION_ROOT_OFFLINE_SIGNED_ACCOUNT_ADAPTER"
BROKER_OBSERVATION_METHOD = (
    "FIXED_LOCAL_READ_ONLY_SYSTEMD_PROC_BROKER_POLICY")
REMOTE_OBSERVATION_AUTHORITY = "INDEPENDENT_REMOTE_READ_ONLY_ACCOUNT"
REMOTE_QUERY_EFFECT = "READ_ONLY"
SIGNATURE_ALGORITHM = "ED25519"
PROTECTED_BROKER_PORTS = (4001, 4002, 7496, 7497)

BROKER_SNAPSHOT_SCHEMA = "hepta.p1-paper-broker-deny-all-snapshot.v1"
ACCOUNT_SNAPSHOT_SCHEMA = (
    "hepta.p1-paper-authoritative-account-snapshot.v1")
INTENT_SCHEMA = "hepta.p1-paper-zero-exposure-production-intent.v1"
CHALLENGE_SCHEMA = "hepta.p1-paper-account-evidence-challenge.v1"
SIGNED_EVIDENCE_ENVELOPE_SCHEMA = (
    "hepta.remote-authoritative-account-evidence-envelope.v1")
SIGNED_EVIDENCE_PAYLOAD_SCHEMA = (
    "hepta.remote-authoritative-account-evidence.v1")
HANDOFF_SCHEMA = "hepta.p1-watch-to-paper-handoff-receipt.v2"
HANDOFF_VERSION = 2
PROFILE_RESTORATION_SCHEMA = (
    "hepta.p1-watch-to-paper-profile-restoration.v1")
PROFILE_RESTORATION_STATUS = "DORMANT_PAPER_PROFILE_RESTORED"
PAPER_RUNTIME_PROFILE_HARDENING_SCHEMA = (
    "hepta.p1-watch-to-paper-runtime-profile-hardening.v1")
PAPER_RUNTIME_PROFILE_HARDENING_STATUS = "PAPER_RUNTIME_PROFILE_HARDENED"
PROFILE_TRANSITION_SCHEMA = (
    "hepta.p1-watch-profile-dormant-paper-transition-receipt.v2")
PROFILE_TRANSITION_STATUS = (
    "OFFLINE_DORMANT_PAPER_TO_PASSIVE_WATCH_TRANSITIONED")
PROFILE_DEPLOYMENT_SCHEMA = "hepta.p1-watch-profile-deployment-receipt.v8"
PROFILE_DEPLOYMENT_STATUS = "OFFLINE_PASSIVE_WATCH_PROFILE_REATTESTED"
PROFILE_PREIMAGE_SCHEMA = (
    "hepta.p1-watch-profile-transition-preimage-evidence.v1")
PROFILE_PREIMAGE_STATUS = "DORMANT_PAPER_PREIMAGE_BOUND"
RESERVATION_SCHEMA = (
    "hepta.p1-paper-zero-exposure-host-authority-reservation.v1")
RESERVATION_LIFECYCLE = (
    "CHALLENGE_ISSUED_TO_PAPER_TESTING_ADMISSION_FINALIZATION")
RESERVATION_NEXT_CONSUMER = "PAPER_TESTING_ADMISSION_VERIFIER"
RESERVATION_FINALIZATION_SCHEMA = (
    "hepta.p1-paper-zero-exposure-reservation-finalization.v1")
RESERVATION_CURRENT_POINTER_SCHEMA = (
    "hepta.p1-paper-zero-exposure-finalization-current.v1")
PAPER_ADMISSION_CANDIDATE_SCHEMA = (
    "hepta.paper-testing-admission-candidate-receipt.v1")
ZERO_EXPOSURE_RECEIPT_SCHEMA = (
    "hepta.p1-paper-deny-all-zero-exposure-receipt.v1")
TRANSPORT_CUTOFF_SCHEMA = "hepta.paper-transport-cutoff-receipt.v1"
TERMINAL_PROVIDER_TRUST_POLICY_SCHEMA = (
    "hepta.paper-terminal-account-provider-trust-policy.v1")
TERMINAL_CHALLENGE_SCHEMA = (
    "hepta.paper-terminal-account-evidence-challenge.v1")
TERMINAL_CUTOFF_OWNER_SCHEMA = "hepta.paper-terminal-cutoff-owner.v1"
TERMINAL_SIGNED_EVIDENCE_SCHEMA = (
    "hepta.paper-signed-terminal-account-evidence.v1")
TERMINAL_WITNESS_SCHEMA = "hepta.paper-post-cutoff-terminal-witness.v1"
TERMINAL_PRODUCTION_MODE = "PRODUCTION_ROOT_POST_CUTOFF_SIGNED_ACCOUNT_WITNESS"
TERMINAL_PROOF_KIND = "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1"
TERMINAL_PROVIDER_CAPABILITY = (
    "ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1")
TERMINAL_PROVIDER_ID = "reviewed-remote-account-authority-a"
# Fail closed until deployment reviews a concrete provider trust policy and
# replaces this deliberately unprovisioned compile-time pin.
TERMINAL_PROVIDER_TRUST_POLICY_BODY_SHA256 = (
    "sha256:a77c1b3e779ef085a888815b6e1ac1b3facfc99e7b7494d694e8745e15173563")
TERMINAL_CUTOFF_STATUS = "TRANSPORT_CUTOFF_DURABLE"
TERMINAL_CHALLENGE_STATUS = "AWAITING_SIGNED_TERMINAL_ACCOUNT_EVIDENCE"
TERMINAL_SIGNED_EVIDENCE_STATUS = "COMPLETE"
TERMINAL_WITNESS_STATUS = "POST_CUTOFF_TERMINAL_FLAT_PROVEN"
EGRESS_BOUNDARY_RECEIPT_SCHEMA = "hepta.broker-egress-current-boundary.v1"
RESERVATION_FINALIZATION_ORDER = (
    "CANDIDATE_COMMIT_THEN_TOMBSTONE_COMMIT_THEN_CURRENT_POINTER_COMMIT_"
    "THEN_OWNER_REMOVE_THEN_REOPEN")

MAXIMUM_JSON_BYTES = 4 * 1024 * 1024
MAXIMUM_COMMAND_BYTES = 64 * 1024
MAXIMUM_CLOCK_SKEW_MS = 5 * 1000
MAXIMUM_EVIDENCE_AGE_MS = 30 * 1000
MAXIMUM_CHALLENGE_LIFETIME_MS = 5 * 60 * 1000
MAXIMUM_OUTPUT_LIFETIME_MS = 60 * 1000
MAXIMUM_INVENTORY_ATTEMPTS = 3

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
NONCE = re.compile(r"[0-9a-f]{64}")
RESERVATION_ID = re.compile(r"zero-exposure-[0-9a-f]{48}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PAPER_CREDENTIAL_DIRECTORY = re.compile(
    r"hepta-execution-ib-paper(?:@alpha)?\.service")
KNOWN_BROKER_PROCESS = re.compile(
    rb"(?:^|[/ _-])(?:tws|ibgateway|ib-gateway|"
    rb"hepta-ib-executiond)(?:$|[/ _-])", re.IGNORECASE)

NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW | CLOEXEC
READ_FLAGS = os.O_RDONLY | NOFOLLOW | CLOEXEC | NONBLOCK
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC
RENAME_NOREPLACE = 1
LIBC = ctypes.CDLL(None, use_errno=True)
CLI_RUN_TOKEN = object()
_ADMISSION_SESSION_SECRET = object()

SAFE_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C", "LC_ALL": "C", "PYTHONNOUSERSITE": "1",
}

BOUNDARY_FIELDS = (
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized",
)
PAPER_ADMISSION_CANDIDATE_FIELDS = frozenset({
    "schema", "version", "status", "evaluated_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "strategy_sha256", "input_bindings", "findings",
    "paper_test_admission_candidate", *BOUNDARY_FIELDS,
    "authorization_effect", "body_sha256",
})
PAPER_ADMISSION_INPUT_BINDING_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "schema", "version", "status",
})
REFERENCE_FIELDS = frozenset({"path", "file_sha256", "body_sha256"})
RESERVATION_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "device", "inode", "uid", "gid",
    "mode", "size", "mtime_ns", "ctime_ns",
})
SIGNED_REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "signed_payload_sha256"})
EXECUTABLE_REFERENCE_FIELDS = frozenset({"path", "file_sha256"})
SIGNATURE_PROOF_FIELDS = frozenset({
    "algorithm", "public_key", "verifier", "signature_sha256",
    "signed_payload_sha256",
})
HOST_AUTHORITY_LEASE_FIELDS = frozenset({
    "directory_path", "lease_path", "owner_path", "directory_device",
    "directory_inode", "directory_uid", "directory_gid", "directory_mode",
    "lease_device", "lease_inode", "lease_uid", "lease_gid", "lease_mode",
    "lease_size", "held_exclusive", "boot_id",
})
POSITION_FIELDS = frozenset({"instrument", "quantity"})

TRANSPORT_CUTOFF_FIELDS = frozenset({
    "schema", "version", "status", "completed_at_ms",
    "completed_monotonic_ns", "round", "domain", "campaign_id",
    "source_baseline_sha256",
    "cycle_id", "recovery_id", "finalization_id", "boot_id",
    "service_pid", "service_start_ticks", "broker_socket_identity_sha256",
    "account_id_sha256", "owner_ids", "owner_set_sha256",
    "owner_set_canonical_hex", "owner_count",
    "execution_service_epoch", "execution_service_fencing_generation",
    "mutation_fence_generation", "known_mutation_command_set_sha256",
    "known_mutation_command_count", "known_correlation_set_sha256",
    "known_correlation_count", "egress_policy_generation",
    "egress_policy_sha256", "authorized_connectors", "authorized_uids",
    "broker_socket_count", "broker_process_count",
    "credential_exposure_count", "process_inventory_complete",
    "socket_inventory_complete", "credential_inventory_complete",
    "mutation_gate_closed", "reconnect_permitted", *BOUNDARY_FIELDS,
    "body_sha256",
})
TERMINAL_CUTOFF_OWNER_FIELDS = frozenset({
    "schema", "version", "status", "boot_id", "campaign_id", "cycle_id",
    "recovery_id", "finalization_id", "terminalizing_latch_sha256",
    "transport_cutoff_file_sha256", "transport_cutoff_body_sha256",
    "transport_cutoff_document", "next_consumer", *BOUNDARY_FIELDS,
    "body_sha256",
})
TERMINAL_PROVIDER_TRUST_POLICY_FIELDS = frozenset({
    "schema", "version", "status", "provider_id", "provider_key_sha256",
    "provider_capability", "atomic_account_supported",
    "causal_watermark_supported", "challenge_bound_query_supported",
    "read_only_authority_required", "mutation_attempted", *BOUNDARY_FIELDS,
    "body_sha256",
})
TERMINAL_CHALLENGE_FIELDS = frozenset({
    "schema", "version", "status", "issued_at_ms", "issued_monotonic_ns",
    "expires_at_ms", "round", "domain", "campaign_id",
    "source_baseline_sha256", "cycle_id",
    "recovery_id", "finalization_id", "nonce", "boot_id", "service_pid",
    "service_start_ticks", "broker_socket_identity_sha256",
    "account_id_sha256", "owner_ids", "owner_set_sha256",
    "owner_set_canonical_hex", "owner_count",
    "execution_service_epoch", "execution_service_fencing_generation",
    "mutation_fence_generation", "known_mutation_command_set_sha256",
    "known_mutation_command_count", "known_correlation_set_sha256",
    "known_correlation_count", "egress_policy_generation",
    "egress_policy_sha256", "transport_cutoff_receipt",
    "cutoff_completed_at_ms", "cutoff_completed_monotonic_ns", "producer",
    "production_mode", "provider_trust_policy", "provider_id",
    "provider_key_sha256", "provider_capability", "signature_algorithm",
    "signature_verifier", "verification_key",
    "required_observation_authority", "required_snapshot_consistency",
    *BOUNDARY_FIELDS, "body_sha256",
})
TERMINAL_SIGNED_EVIDENCE_PAYLOAD_FIELDS = frozenset({
    "schema", "version", "status", "query_started_at_ms",
    "query_started_monotonic_ns", "observed_at_ms", "observed_monotonic_ns",
    "query_completed_at_ms", "query_completed_monotonic_ns", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256", "cycle_id",
    "recovery_id", "finalization_id", "nonce", "challenge_body_sha256",
    "transport_cutoff_body_sha256", "boot_id", "service_pid",
    "service_start_ticks", "broker_socket_identity_sha256",
    "account_id_sha256", "owner_set_sha256", "owner_set_canonical_hex",
    "owner_count",
    "execution_service_epoch", "execution_service_fencing_generation",
    "mutation_fence_generation", "known_mutation_command_set_sha256",
    "known_mutation_command_count", "known_correlation_set_sha256",
    "known_correlation_count", "egress_policy_generation",
    "egress_policy_sha256", "provider_id", "provider_trust_policy_sha256",
    "provider_key_sha256", "provider_capability",
    "provider_request_sha256", "provider_response_sha256",
    "observation_authority", "query_effect", "query_epoch",
    "query_fencing_generation", "query_invocation_id", "provider_clock_id",
    "provider_boot_id", "query_started_after_challenge",
    "snapshot_consistency", "consistency_token_sha256",
    "consistency_cutoff_body_sha256",
    "consistency_known_mutation_command_set_sha256",
    "consistency_known_correlation_set_sha256", "consistency_dominates_cutoff",
    "consistency_dominates_all_mutations", "active_orders_complete",
    "active_orders_end_marker_observed", "completed_orders_complete",
    "completed_orders_end_marker_observed", "executions_complete",
    "executions_end_marker_observed", "positions_complete",
    "positions_end_marker_observed", "cash_fx_complete",
    "cash_fx_end_marker_observed", "risk_complete",
    "risk_end_marker_observed", "active_order_id_sha256s",
    "completed_order_id_sha256s", "execution_id_sha256s", "positions",
    "cash_fx_exposures", "gross_absolute_position", "gross_fx_exposure",
    "gross_risk", "settled_mutation_command_count",
    "unknown_mutation_command_count", "unresolved_mutation_command_count",
    "read_only_authority", "authoritative", "account_complete",
    "mutation_attempted", *BOUNDARY_FIELDS,
})
TERMINAL_WITNESS_FIELDS = frozenset({
    "schema", "version", "status", "terminal_proof_kind", "received_at_ms",
    "received_monotonic_ns", "verified_at_ms", "verified_monotonic_ns",
    "expires_at_ms", "round", "domain",
    "campaign_id", "source_baseline_sha256", "cycle_id", "recovery_id",
    "finalization_id", "boot_id",
    "service_pid", "service_start_ticks", "broker_socket_identity_sha256",
    "account_id_sha256", "owner_ids", "owner_set_sha256",
    "owner_set_canonical_hex", "owner_count",
    "execution_service_epoch", "execution_service_fencing_generation",
    "mutation_fence_generation", "known_mutation_command_set_sha256",
    "known_mutation_command_count", "known_correlation_set_sha256",
    "known_correlation_count", "egress_policy_generation",
    "egress_policy_sha256", "transport_cutoff_receipt", "challenge_reference",
    "signed_evidence_reference", "provider_trust_policy",
    "provider_request_reference", "provider_response_reference",
    "signature_verification", "nonce", "provider_id", "provider_key_sha256",
    "provider_capability", "provider_request_sha256",
    "provider_response_sha256", "query_started_at_ms",
    "query_started_monotonic_ns", "observed_at_ms", "observed_monotonic_ns",
    "query_completed_at_ms", "query_completed_monotonic_ns",
    "provider_clock_id", "provider_boot_id",
    "query_started_after_challenge", "snapshot_consistency",
    "consistency_token_sha256", "consistency_dominates_cutoff",
    "consistency_dominates_all_mutations",
    "active_orders_complete", "completed_orders_complete",
    "executions_complete", "positions_complete", "cash_fx_complete",
    "risk_complete", "active_order_count", "completed_order_count",
    "execution_count", "position_count", "cash_fx_exposure_count",
    "gross_absolute_position", "gross_fx_exposure", "gross_risk",
    "settled_mutation_command_count", "unknown_mutation_command_count",
    "unresolved_mutation_command_count", "first_host_observed_at_ms",
    "second_host_observed_at_ms", "host_policy_sha256",
    "host_authorized_connectors", "host_authorized_uids",
    "host_broker_socket_count", "host_broker_process_count",
    "host_credential_exposure_count", "host_process_inventory_complete",
    "host_socket_inventory_complete", "host_credential_inventory_complete",
    "host_paper_units_inactive", "host_kill_switch_engaged",
    "post_cutoff_boundary_verified", "egress_policy_generation_stable",
    "read_only_authority", "authoritative", "account_complete",
    "mutation_attempted", *BOUNDARY_FIELDS, "body_sha256",
})
EGRESS_BOUNDARY_RECEIPT_FIELDS = frozenset({
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
UNIT_STATE_FIELDS = frozenset({
    "load_state", "active_state", "sub_state", "job", "unit_file_state",
    "main_pid", "control_pid",
})

HANDOFF_FIELDS = frozenset({
    "schema", "version", "status", "issued_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "producer", "production_mode", "activation_receipt", "p1_audit_receipt",
    "freeze_bundle",
    "watch_units_inactive", "watch_authority_count", "watch_socket_count",
    "watch_timer_count", "paper_units_inactive", "broker_deny_all",
    "kill_switch_engaged", "global_kill_switch_engaged", "identity_count",
    "identity_manifest_sha256", "paper_profile_restored",
    "paper_profile_restoration", "profile_candidate_absent",
    "paper_runtime_profile_hardened", "paper_runtime_profile_hardening",
    "paper_runtime_profile_candidate_absent",
    "crash_recovery_verified",
    "cleanup_residue_count", *BOUNDARY_FIELDS, "body_sha256",
})
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

INTENT_FIELDS = frozenset({
    "schema", "version", "status", "issued_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "intent_id", "account_id_sha256", "production_mode", "producer",
    "broker_policy_helper", "signature_verifier", "verification_key",
    "watch_handoff_receipt_path", "challenge_output_path",
    "signed_account_evidence_path", "broker_snapshot_output_path",
    "account_snapshot_output_path", "allow_fixed_read_only_host_observation",
    "allow_offline_signed_account_adaptation", *BOUNDARY_FIELDS,
    "body_sha256",
})

CHALLENGE_FIELDS = frozenset({
    "schema", "version", "status", "issued_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256", "nonce",
    "account_id_sha256", "producer", "production_mode",
    "operator_intent_reference", "watch_handoff_receipt",
    "host_authority_reservation",
    "signature_algorithm", "signature_verifier", "verification_key",
    "required_observation_authority", *BOUNDARY_FIELDS, "body_sha256",
})

RESERVATION_FIELDS = frozenset({
    "schema", "version", "status", "issued_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "reservation_id", "reservation_generation",
    "predecessor_finalization_body_sha256",
    "prior_finalization_pointer_reference", "reservation_owner_kind",
    "reservation_lifecycle", "next_consumer",
    "boot_id", "request_nonce", "account_id_sha256", "producer",
    "production_mode",
    "operator_intent_reference", "watch_handoff_receipt",
    "challenge_output_path", "signed_account_evidence_path",
    "broker_snapshot_output_path", "account_snapshot_output_path",
    "host_authority_lease", "finalization_tombstone_path",
    "finalization_current_pointer_path",
    "finalization_tombstone_absent", *BOUNDARY_FIELDS, "body_sha256",
})

RESERVATION_FINALIZATION_FIELDS = frozenset({
    "schema", "version", "status", "finalized_at_ms", "round", "domain",
    "campaign_id", "source_baseline_sha256", "reservation_id",
    "reservation_generation", "predecessor_finalization_body_sha256",
    "prior_finalization_pointer_reference", "boot_id",
    "reservation_reference",
    "candidate_reference", "zero_exposure_receipt_reference",
    "host_authority_lease", "recovery_observation",
    "owner_present_at_tombstone_commit",
    "owner_removal_required_after_commit",
    "finalization_order", "recovery_reason", *BOUNDARY_FIELDS,
    "body_sha256",
})
RESERVATION_CURRENT_POINTER_FIELDS = frozenset({
    "schema", "version", "status", "updated_at_ms", "round", "domain",
    "campaign_id", "source_baseline_sha256", "boot_id",
    "reservation_id", "reservation_generation",
    "predecessor_finalization_body_sha256",
    "finalization_tombstone_reference", "host_authority_lease",
    *BOUNDARY_FIELDS, "body_sha256",
})
RECOVERY_OBSERVATION_FIELDS = frozenset({
    "first_observed_at_ms", "second_observed_at_ms", "policy_sha256",
    "authorized_connectors", "authorized_uids", "broker_socket_count",
    "broker_process_count", "credential_exposure_count",
    "paper_units_inactive", "kill_switch_engaged",
    "process_inventory_complete", "socket_inventory_complete",
    "credential_inventory_complete",
})

SIGNED_EVIDENCE_ENVELOPE_FIELDS = frozenset({
    "schema", "version", "payload", "signature_base64",
})
SIGNED_EVIDENCE_PAYLOAD_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256", "nonce",
    "challenge_body_sha256", "account_id_sha256", "provider_id",
    "provider_request_id_sha256", "provider_response_sha256",
    "observation_authority", "query_effect", "query_epoch",
    "query_fencing_generation", "query_invocation_id",
    "read_only_authority", "authoritative", "account_complete",
    "snapshot_sha256", "active_order_id_sha256s", "positions",
    "gross_absolute_position", "authorized_connector_count", "end_flat",
    *BOUNDARY_FIELDS,
})

BROKER_SNAPSHOT_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "producer", "production_mode", "operator_intent_reference",
    "watch_handoff_receipt", "challenge_reference", "request_nonce",
    "host_authority_reservation",
    "account_id_sha256", "signed_account_payload_sha256",
    "observation_method", "broker_policy_helper", "observer_id",
    "observation_complete", "broker_deny_all", "policy_sha256",
    "authorized_connectors", "authorized_uids", "broker_socket_count",
    "broker_process_count", "credential_exposure_count",
    "paper_units_inactive", "kill_switch_engaged", "protected_broker_ports",
    "process_inventory_complete", "socket_inventory_complete",
    "credential_inventory_complete", "host_authority_lease",
    *BOUNDARY_FIELDS, "body_sha256",
})

ACCOUNT_SNAPSHOT_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "producer", "production_mode", "operator_intent_reference",
    "watch_handoff_receipt", "challenge_reference",
    "host_authority_reservation",
    "signed_evidence_reference", "signature_verification", "request_nonce",
    "provider_id", "account_id_sha256", "provider_request_id_sha256",
    "provider_response_sha256", "observer_id", "observation_authority",
    "query_effect", "query_epoch", "query_fencing_generation",
    "query_invocation_id", "read_only_authority", "authoritative",
    "account_complete", "snapshot_sha256", "active_order_id_sha256s",
    "positions", "gross_absolute_position", "authorized_connector_count",
    "end_flat", *BOUNDARY_FIELDS, "body_sha256",
})


class ProducerError(RuntimeError):
    """Stable fail-closed production error."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class InventoryRetry(RuntimeError):
    """A process changed during one inventory attempt."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ProducerError(reason)


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ProducerError("ZERO_SNAPSHOT_CANONICALIZATION_FAILED") from error


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _wall_clock_ms() -> int:
    """Return the non-injectable clock used at admission commit seams."""

    return time.time_ns() // 1_000_000


def seal(body: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(body)
    result["body_sha256"] = digest_bytes(canonical_bytes(result))
    return result


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_uid, metadata.st_gid,
    )


def _canonical_path(path: Path, reason: str) -> Path:
    _require(path.is_absolute(), reason)
    normalized = Path(os.path.normpath(os.fspath(path)))
    _require(normalized == path and path.name not in {"", ".", ".."}, reason)
    return normalized


def _open_directory(path: Path, reason: str) -> int:
    path = _canonical_path(path, reason)
    descriptor = -1
    try:
        descriptor = os.open("/", DIRECTORY_FLAGS)
        for component in path.parts[1:]:
            before = os.stat(component, dir_fd=descriptor,
                             follow_symlinks=False)
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            opened = os.fstat(child)
            _require(
                stat.S_ISDIR(opened.st_mode) and
                _directory_identity(before) == _directory_identity(opened),
                reason)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except (OSError, ProducerError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        if isinstance(error, ProducerError):
            raise
        raise ProducerError(reason) from error


def _trusted_parent(
    descriptor: int, *, expected_uid: int, expected_gid: int, reason: str,
) -> tuple[int, ...]:
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise ProducerError(reason) from error
    _require(
        stat.S_ISDIR(metadata.st_mode) and
        metadata.st_uid == expected_uid and metadata.st_gid == expected_gid and
        stat.S_IMODE(metadata.st_mode) & 0o022 == 0, reason)
    return _directory_identity(metadata)


def secure_read(
    path: Path, reason: str, *, expected_uid: int = ROOT_UID,
    expected_gid: int = ROOT_GID,
    modes: frozenset[int] = frozenset({0o600}),
    maximum: int = MAXIMUM_JSON_BYTES,
) -> tuple[bytes, os.stat_result, tuple[int, ...]]:
    path = _canonical_path(path, reason)
    parent = _open_directory(path.parent, reason)
    try:
        parent_identity = _trusted_parent(
            parent, expected_uid=expected_uid, expected_gid=expected_gid,
            reason=reason)
        try:
            before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        except OSError as error:
            raise ProducerError(reason) from error
        try:
            opened = os.fstat(descriptor)
            _require(
                stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
                opened.st_uid == expected_uid and
                opened.st_gid == expected_gid and
                stat.S_IMODE(opened.st_mode) in modes and
                0 < opened.st_size <= maximum and
                _identity(before) == _identity(opened), reason)
            payload = bytearray()
            while len(payload) <= maximum:
                chunk = os.read(
                    descriptor, min(65536, maximum + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
            final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            _require(
                0 < len(payload) <= maximum and
                _identity(opened) == _identity(after) == _identity(final) and
                parent_identity == _trusted_parent(
                    parent, expected_uid=expected_uid,
                    expected_gid=expected_gid, reason=reason), reason)
            return bytes(payload), opened, parent_identity
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ProducerError(reason) from error
    finally:
        os.close(parent)


def _read_boot_id(*, expected_uid: int, expected_gid: int,
                  reason: str) -> str:
    path = _canonical_path(BOOT_ID_PATH, reason)
    parent = _open_directory(path.parent, reason)
    try:
        parent_identity = _trusted_parent(
            parent, expected_uid=expected_uid, expected_gid=expected_gid,
            reason=reason)
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        try:
            opened = os.fstat(descriptor)
            _require(
                stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
                opened.st_uid == expected_uid and
                opened.st_gid == expected_gid and
                stat.S_IMODE(opened.st_mode) & 0o022 == 0 and
                _identity(before) == _identity(opened), reason)
            payload = os.read(descriptor, 65)
            after = os.fstat(descriptor)
            named = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
            _require(
                _identity(opened) == _identity(after) == _identity(named) and
                parent_identity == _trusted_parent(
                    parent, expected_uid=expected_uid,
                    expected_gid=expected_gid, reason=reason), reason)
        finally:
            os.close(descriptor)
    except (OSError, ProducerError) as error:
        if isinstance(error, ProducerError):
            raise
        raise ProducerError(reason) from error
    finally:
        os.close(parent)
    try:
        text = payload.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise ProducerError(reason) from error
    _require(text.endswith("\n") and BOOT_ID.fullmatch(text[:-1]) is not None,
             reason)
    return text[:-1]


def strict_object(payload: bytes, reason: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProducerError(reason)
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("ascii", errors="strict"),
            object_pairs_hook=unique,
            parse_float=lambda _value: (_ for _ in ()).throw(
                ProducerError(reason)),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ProducerError(reason)),
        )
    except ProducerError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ProducerError(reason) from error
    _require(isinstance(value, dict), reason)
    return value


def _sealed(
    document: dict[str, Any], fields: frozenset[str], schema: str, reason: str,
    *, version: int = VERSION,
) -> str:
    _require(set(document) == fields and document.get("schema") == schema and
             document.get("version") == version, reason)
    claimed = document.get("body_sha256")
    _require(type(claimed) is str and DIGEST.fullmatch(claimed) is not None,
             reason)
    body = dict(document)
    del body["body_sha256"]
    _require(claimed == digest_bytes(canonical_bytes(body)), reason)
    return claimed


def _profile_record(
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
    path: Path, reason: str, *, expected_uid: int, expected_gid: int,
    mode: int, sha256: str, size: int,
) -> tuple[bytes, os.stat_result, dict[str, Any]]:
    payload, metadata, _ = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({mode}), maximum=max(size, 1))
    record = _profile_record(path, payload, metadata)
    _require(
        record["file_sha256"] == sha256 and record["bytes"] == size and
        record["mode"] == stat.S_IFREG | mode and
        record["uid"] == expected_uid and record["gid"] == expected_gid and
        record["nlink"] == 1, reason)
    return payload, metadata, record


def _read_kill_switch(
    path: Path, reason: str, *, expected_uid: int, expected_file_gid: int,
    expected_parent_gid: int,
) -> None:
    path = _canonical_path(path, reason)
    parent = _open_directory(path.parent, reason)
    descriptor = -1
    try:
        parent_identity = _trusted_parent(
            parent, expected_uid=expected_uid,
            expected_gid=expected_parent_gid, reason=reason)
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        opened = os.fstat(descriptor)
        payload = os.read(descriptor, 8)
        after = os.fstat(descriptor)
        final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _require(
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
            opened.st_uid == expected_uid and
            opened.st_gid == expected_file_gid and
            stat.S_IMODE(opened.st_mode) == 0o440 and
            _identity(before) == _identity(opened) == _identity(after) ==
                _identity(final) and payload == b"engaged" and
            parent_identity == _trusted_parent(
                parent, expected_uid=expected_uid,
                expected_gid=expected_parent_gid, reason=reason), reason)
    except (OSError, ProducerError) as error:
        if isinstance(error, ProducerError):
            raise
        raise ProducerError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _validate_profile_record(
    value: Any, actual: Mapping[str, Any], reason: str,
    *, sealed: bool = False,
) -> None:
    fields = (PROFILE_SEALED_EVIDENCE_FIELDS if sealed else
              PROFILE_FILE_EVIDENCE_FIELDS)
    _require(isinstance(value, dict) and set(value) == fields, reason)
    for field in PROFILE_FILE_EVIDENCE_FIELDS:
        _require(value.get(field) == actual.get(field), reason)
    if sealed:
        _require(
            _digest(value.get("body_sha256"), reason, nonzero=True) ==
                actual.get("body_sha256"), reason)


def _read_sealed_profile_document(
    path: Path, evidence: Any, reason: str, *, expected_uid: int,
    expected_gid: int, fields: frozenset[str], schema: str, version: int,
    status: str,
) -> dict[str, Any]:
    payload, metadata, _ = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({0o600}))
    document = strict_object(payload, reason)
    _require(canonical_bytes(document) == payload, reason)
    _sealed(document, fields, schema, reason, version=version)
    _require(
        document.get("status") == status and document.get("round") == ROUND and
        document.get("domain") == DOMAIN_ID, reason)
    actual = {**_profile_record(path, payload, metadata),
              "body_sha256": document["body_sha256"]}
    _validate_profile_record(evidence, actual, reason, sealed=True)
    return document


def _require_profile_candidate_absent(
    path: Path, reason: str, *, expected_uid: int, expected_gid: int,
) -> None:
    parent = _open_directory(path.parent, reason)
    try:
        parent_identity = _trusted_parent(
            parent, expected_uid=expected_uid, expected_gid=expected_gid,
            reason=reason)
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ProducerError(reason)
        _require(parent_identity == _trusted_parent(
            parent, expected_uid=expected_uid, expected_gid=expected_gid,
            reason=reason), reason)
    except OSError as error:
        raise ProducerError(reason) from error
    finally:
        os.close(parent)


def _integer(value: Any, reason: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, reason)
    return value


def _digest(value: Any, reason: str, *, nonzero: bool = False) -> str:
    _require(type(value) is str and DIGEST.fullmatch(value) is not None, reason)
    if nonzero:
        _require(value != "sha256:" + "0" * 64, reason)
    return value


def _identifier(value: Any, pattern: re.Pattern[str], reason: str) -> str:
    _require(type(value) is str and pattern.fullmatch(value) is not None, reason)
    return value


def reservation_tombstone_path(reservation_id: str) -> Path:
    _identifier(
        reservation_id, RESERVATION_ID,
        "ZERO_SNAPSHOT_RESERVATION_ID_INVALID")
    return HOST_AUTHORITY_DIRECTORY / (
        "finalized." + reservation_id + ".v1.json")


def reservation_current_pointer_path() -> Path:
    return HOST_AUTHORITY_DIRECTORY / "finalization-current.v1.json"


def _false_boundary(document: Mapping[str, Any], reason: str) -> None:
    _require(all(document.get(field) is False for field in BOUNDARY_FIELDS),
             reason)


def _executable_reference(value: Any, reason: str) -> dict[str, str]:
    _require(isinstance(value, dict) and
             set(value) == EXECUTABLE_REFERENCE_FIELDS, reason)
    path = value.get("path")
    _require(type(path) is str, reason)
    return {
        "path": str(_canonical_path(Path(path), reason)),
        "file_sha256": _digest(value.get("file_sha256"), reason,
                               nonzero=True),
    }


def _validate_reservation_reference(
    value: Any, reason: str,
) -> dict[str, Any]:
    _require(isinstance(value, dict) and
             set(value) == RESERVATION_REFERENCE_FIELDS, reason)
    path = value.get("path")
    _require(type(path) is str and
             _canonical_path(Path(path), reason) ==
                HOST_AUTHORITY_OWNER_PATH, reason)
    result = dict(value)
    _digest(result.get("file_sha256"), reason, nonzero=True)
    _digest(result.get("body_sha256"), reason, nonzero=True)
    for field in (
        "device", "inode", "uid", "gid", "mode", "size", "mtime_ns",
        "ctime_ns",
    ):
        _integer(result.get(field), reason)
    _require(result["device"] > 0 and result["inode"] > 0 and
             result["mode"] == 0o600 and result["size"] > 0, reason)
    return result


def _validate_reference(value: Any, reason: str) -> dict[str, str]:
    _require(isinstance(value, dict) and set(value) == REFERENCE_FIELDS,
             reason)
    path = value.get("path")
    _require(type(path) is str, reason)
    result = {
        "path": str(_canonical_path(Path(path), reason)),
        "file_sha256": _digest(value.get("file_sha256"), reason,
                               nonzero=True),
        "body_sha256": _digest(value.get("body_sha256"), reason,
                               nonzero=True),
    }
    return result


def account_state_sha256(document: Mapping[str, Any]) -> str:
    return digest_bytes(canonical_bytes({
        "query_epoch": document.get("query_epoch"),
        "query_fencing_generation":
            document.get("query_fencing_generation"),
        "query_invocation_id": document.get("query_invocation_id"),
        "active_order_id_sha256s": document.get("active_order_id_sha256s"),
        "positions": document.get("positions"),
        "gross_absolute_position": document.get("gross_absolute_position"),
        "authorized_connector_count":
            document.get("authorized_connector_count"),
        "end_flat": document.get("end_flat"),
    }))


def _validate_account_state(document: Mapping[str, Any], reason: str) -> None:
    _identifier(document.get("query_epoch"), IDENTIFIER, reason)
    _integer(document.get("query_fencing_generation"), reason, 1)
    _identifier(document.get("query_invocation_id"), IDENTIFIER, reason)
    orders = document.get("active_order_id_sha256s")
    _require(isinstance(orders, list) and len(orders) <= 128, reason)
    for order in orders:
        _digest(order, reason, nonzero=True)
    _require(len(orders) == len(set(orders)), reason)
    positions = document.get("positions")
    _require(isinstance(positions, list) and len(positions) <= 128, reason)
    instruments: set[str] = set()
    gross = 0
    for position in positions:
        _require(isinstance(position, dict) and
                 set(position) == POSITION_FIELDS, reason)
        instrument = _identifier(
            position.get("instrument"), IDENTIFIER, reason)
        _require(instrument not in instruments, reason)
        instruments.add(instrument)
        quantity = position.get("quantity")
        _require(type(quantity) is int and -(2**63) < quantity < 2**63 and
                 quantity != 0, reason)
        gross += abs(quantity)
        _require(gross <= 2**63 - 1, reason)
    _require(_integer(document.get("gross_absolute_position"), reason) ==
             gross, reason)
    _require(_integer(document.get("authorized_connector_count"), reason) <=
             1024, reason)
    _require(type(document.get("end_flat")) is bool and
             document.get("end_flat") is
                (not orders and not positions and gross == 0), reason)
    _require(_digest(document.get("snapshot_sha256"), reason) ==
             account_state_sha256(document), reason)


def _terminal_digest_list(value: Any, reason: str) -> list[str]:
    _require(isinstance(value, list) and len(value) <= 4096, reason)
    result = [_digest(item, reason, nonzero=True) for item in value]
    _require(result == sorted(set(result)), reason)
    return result


def _terminal_owner_binding(document: Mapping[str, Any], reason: str) -> None:
    owners = document.get("owner_ids")
    _require(isinstance(owners, list) and 0 < len(owners) <= 128, reason)
    normalized = [_digest(item, reason, nonzero=True) for item in owners]
    _require(normalized == sorted(set(normalized)), reason)
    _require(_integer(document.get("owner_count"), reason, 1) == len(owners),
             reason)
    canonical_hex = document.get("owner_set_canonical_hex")
    _require(type(canonical_hex) is str and 0 < len(canonical_hex) <= 131072 and
             len(canonical_hex) % 2 == 0 and
             re.fullmatch(r"[0-9a-f]+", canonical_hex) is not None, reason)
    canonical = bytes.fromhex(canonical_hex)
    _require(_digest(document.get("owner_set_sha256"), reason, nonzero=True) ==
             digest_bytes(canonical), reason)
    try:
        text = canonical.decode("ascii")
    except UnicodeError as error:
        raise ProducerError(reason) from error
    _require(
        canonical.endswith(b"\n") and "\r" not in text and
        "\x00" not in text, reason)
    lines = text[:-1].split("\n")
    _require(
        len(lines) == len(owners) and lines == sorted(set(lines)), reason)
    expected_account_sha256 = _digest(
        document.get("account_id_sha256"), reason, nonzero=True)
    domain = _identifier(document.get("domain"), IDENTIFIER, reason)
    expected_domain = "PAPER:" + domain
    tokens: list[str] = []
    for line in lines:
        fields = line.split("\t")
        _require(len(fields) == 4, reason)
        token = _digest(fields[0], reason, nonzero=True)
        _require(
            re.fullmatch(r"[1-9][0-9]*", fields[1]) is not None and
            int(fields[1]) <= (1 << 64) - 1 and
            re.fullmatch(r"(?:[0-9a-f][0-9a-f])+", fields[2]) is not None and
            re.fullmatch(r"(?:[0-9a-f][0-9a-f])+", fields[3]) is not None,
            reason)
        try:
            account = bytes.fromhex(fields[2]).decode("utf-8")
            owner_domain = bytes.fromhex(fields[3]).decode("utf-8")
        except (UnicodeError, ValueError) as error:
            raise ProducerError(reason) from error
        _require(
            re.fullmatch(r"DU[0-9]{1,16}", account) is not None and
            owner_domain == expected_domain and
            digest_bytes(account.encode("ascii")) == expected_account_sha256,
            reason)
        tokens.append(token)
    _require(tokens == normalized, reason)


def validate_transport_cutoff(
    document: dict[str, Any], *, now_ms: int, now_monotonic_ns: int,
    expected_source: str, expected_campaign: str, expected_cycle: str,
    expected_recovery: str, expected_finalization: str,
    expected_boot_id: str,
) -> None:
    reason = "TERMINAL_WITNESS_TRANSPORT_CUTOFF_INVALID"
    _sealed(document, TRANSPORT_CUTOFF_FIELDS, TRANSPORT_CUTOFF_SCHEMA, reason)
    for field, expected in {
        "source_baseline_sha256": expected_source,
        "campaign_id": expected_campaign, "cycle_id": expected_cycle,
        "recovery_id": expected_recovery,
        "finalization_id": expected_finalization,
        "boot_id": expected_boot_id,
    }.items():
        _require(document.get(field) == expected, reason)
    _require(
        document.get("status") == TERMINAL_CUTOFF_STATUS and
        document.get("round") == ROUND and
        document.get("domain") == DOMAIN_ID and
        _integer(document.get("completed_at_ms"), reason) <= now_ms and
        _integer(document.get("completed_monotonic_ns"), reason) <=
            now_monotonic_ns and
        _integer(document.get("service_pid"), reason, 1) > 0 and
        _integer(document.get("service_start_ticks"), reason, 1) > 0 and
        _integer(document.get("execution_service_fencing_generation"),
                 reason, 1) > 0 and
        _integer(document.get("mutation_fence_generation"), reason, 1) > 0 and
        _integer(document.get("egress_policy_generation"), reason, 1) > 0,
        reason)
    _digest(document.get("source_baseline_sha256"), reason, nonzero=True)
    _digest(document.get("broker_socket_identity_sha256"), reason,
            nonzero=True)
    _digest(document.get("account_id_sha256"), reason, nonzero=True)
    _digest(document.get("known_mutation_command_set_sha256"), reason,
            nonzero=True)
    _digest(document.get("known_correlation_set_sha256"), reason,
            nonzero=True)
    _digest(document.get("egress_policy_sha256"), reason, nonzero=True)
    _identifier(document.get("execution_service_epoch"), IDENTIFIER, reason)
    for field in ("campaign_id", "cycle_id", "recovery_id",
                  "finalization_id"):
        _identifier(document.get(field), IDENTIFIER, reason)
    _identifier(document.get("boot_id"), BOOT_ID, reason)
    _terminal_owner_binding(document, reason)
    _require(
        _integer(document.get("known_mutation_command_count"), reason) <=
            4096 and
        _integer(document.get("known_correlation_count"), reason) <= 4096 and
        document.get("authorized_connectors") == 0 and
        document.get("authorized_uids") == [] and
        document.get("broker_socket_count") == 0 and
        document.get("broker_process_count") == 0 and
        document.get("credential_exposure_count") == 0 and
        document.get("process_inventory_complete") is True and
        document.get("socket_inventory_complete") is True and
        document.get("credential_inventory_complete") is True and
        document.get("mutation_gate_closed") is True and
        document.get("reconnect_permitted") is False,
        reason)
    _false_boundary(document, reason)


def validate_terminal_provider_trust_policy(
    document: dict[str, Any], *, verification_key_sha256: str,
) -> None:
    reason = "TERMINAL_WITNESS_PROVIDER_TRUST_POLICY_INVALID"
    _sealed(
        document, TERMINAL_PROVIDER_TRUST_POLICY_FIELDS,
        TERMINAL_PROVIDER_TRUST_POLICY_SCHEMA, reason)
    _require(
        document.get("status") == "ACTIVE" and
        document.get("body_sha256") ==
            TERMINAL_PROVIDER_TRUST_POLICY_BODY_SHA256 and
        document.get("provider_id") == TERMINAL_PROVIDER_ID and
        document.get("provider_key_sha256") == verification_key_sha256 and
        document.get("provider_capability") == TERMINAL_PROVIDER_CAPABILITY and
        document.get("atomic_account_supported") is True and
        document.get("causal_watermark_supported") is True and
        document.get("challenge_bound_query_supported") is True and
        document.get("read_only_authority_required") is True and
        document.get("mutation_attempted") is False,
        reason)
    _identifier(document.get("provider_id"), IDENTIFIER, reason)
    _digest(document.get("provider_key_sha256"), reason, nonzero=True)
    _false_boundary(document, reason)


def _validate_terminal_zero_account_state(
    document: Mapping[str, Any], reason: str,
) -> None:
    for field in (
        "active_orders_complete", "active_orders_end_marker_observed",
        "completed_orders_complete", "completed_orders_end_marker_observed",
        "executions_complete", "executions_end_marker_observed",
        "positions_complete", "positions_end_marker_observed",
        "cash_fx_complete", "cash_fx_end_marker_observed", "risk_complete",
        "risk_end_marker_observed", "read_only_authority", "authoritative",
        "account_complete",
    ):
        _require(document.get(field) is True, reason)
    _require(document.get("mutation_attempted") is False, reason)
    active = _terminal_digest_list(
        document.get("active_order_id_sha256s"), reason)
    _terminal_digest_list(document.get("completed_order_id_sha256s"), reason)
    _terminal_digest_list(document.get("execution_id_sha256s"), reason)
    _require(
        not active and document.get("positions") == [] and
        document.get("cash_fx_exposures") == [] and
        document.get("gross_absolute_position") == "0" and
        document.get("gross_fx_exposure") == "0" and
        document.get("gross_risk") == "0" and
        _integer(document.get("known_mutation_command_count"), reason) ==
            _integer(document.get("settled_mutation_command_count"), reason) and
        document.get("unknown_mutation_command_count") == 0 and
        document.get("unresolved_mutation_command_count") == 0,
        reason)
    _false_boundary(document, reason)


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

    def reopen(self, *, expected_uid: int, expected_gid: int,
               reason: str = "ZERO_SNAPSHOT_INPUT_DRIFT") -> None:
        payload, metadata, parent = secure_read(
            self.path, reason, expected_uid=expected_uid,
            expected_gid=expected_gid)
        _require(
            payload == self.payload and
            _identity(metadata) == self.metadata_identity and
            parent == self.parent_identity and
                 strict_object(payload, reason) == self.document, reason)


def reservation_reference(binding: InputBinding) -> dict[str, Any]:
    """Bind the durable owner marker including replay-resistant identity."""

    reason = "ZERO_SNAPSHOT_RESERVATION_REFERENCE_INVALID"
    _require(type(binding) is InputBinding and
             binding.path == HOST_AUTHORITY_OWNER_PATH and
             binding.document.get("schema") == RESERVATION_SCHEMA, reason)
    metadata = binding.metadata_identity
    reference = {
        "path": str(binding.path),
        "file_sha256": digest_bytes(binding.payload),
        "body_sha256": binding.document["body_sha256"],
        "device": metadata[0], "inode": metadata[1],
        "uid": metadata[4], "gid": metadata[5],
        "mode": stat.S_IMODE(metadata[2]), "size": metadata[6],
        "mtime_ns": metadata[7], "ctime_ns": metadata[8],
    }
    _require(set(reference) == RESERVATION_REFERENCE_FIELDS, reason)
    return reference


@dataclass(frozen=True)
class ExecutableBinding:
    path: Path
    payload: bytes
    metadata_identity: tuple[int, ...]
    parent_identity: tuple[int, ...]
    expected_uid: int
    expected_gid: int
    executing: bool = False

    @property
    def reference(self) -> dict[str, str]:
        return {"path": str(self.path), "file_sha256": digest_bytes(self.payload)}

    def reopen(self) -> None:
        reason = "ZERO_SNAPSHOT_EXECUTABLE_DRIFT"
        if self.executing:
            lexical = Path(__file__).absolute()
            try:
                lexical_metadata = os.lstat(lexical)
                resolved = lexical.resolve(strict=True)
                installed = self.path.resolve(strict=True)
                same = os.path.samefile(resolved, installed)
            except OSError as error:
                raise ProducerError(reason) from error
            _require(
                not stat.S_ISLNK(lexical_metadata.st_mode) and
                lexical == self.path and resolved == installed == self.path and
                same, reason)
        payload, metadata, parent = secure_read(
            self.path, reason, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid,
            modes=frozenset({0o500, 0o555, 0o700, 0o755}))
        _require(payload == self.payload and
                 _identity(metadata) == self.metadata_identity and
                 parent == self.parent_identity, reason)


@dataclass(frozen=True)
class TrustFileBinding:
    path: Path
    payload: bytes
    metadata_identity: tuple[int, ...]
    parent_identity: tuple[int, ...]
    expected_uid: int
    expected_gid: int

    @property
    def reference(self) -> dict[str, str]:
        return {"path": str(self.path), "file_sha256": digest_bytes(self.payload)}

    def reopen(self) -> None:
        payload, metadata, parent = secure_read(
            self.path, "ZERO_SNAPSHOT_VERIFICATION_KEY_DRIFT",
            expected_uid=self.expected_uid, expected_gid=self.expected_gid,
            modes=frozenset({0o400, 0o440, 0o444, 0o600, 0o640, 0o644}),
            maximum=64 * 1024)
        _require(payload == self.payload and
                 _identity(metadata) == self.metadata_identity and
                 parent == self.parent_identity,
                 "ZERO_SNAPSHOT_VERIFICATION_KEY_DRIFT")


@dataclass(frozen=True)
class TerminalProviderArtifactBinding:
    path: Path
    payload: bytes
    metadata_identity: tuple[int, ...]
    parent_identity: tuple[int, ...]
    expected_uid: int
    expected_gid: int

    @property
    def reference(self) -> dict[str, str]:
        return {"path": str(self.path), "file_sha256": digest_bytes(self.payload)}

    def reopen(self) -> None:
        payload, metadata, parent = secure_read(
            self.path, "TERMINAL_WITNESS_PROVIDER_ARTIFACT_DRIFT",
            expected_uid=self.expected_uid, expected_gid=self.expected_gid,
            modes=frozenset({0o400, 0o440, 0o600, 0o640}),
            maximum=MAXIMUM_JSON_BYTES)
        _require(
            payload == self.payload and
            _identity(metadata) == self.metadata_identity and
            parent == self.parent_identity,
            "TERMINAL_WITNESS_PROVIDER_ARTIFACT_DRIFT")


@dataclass(frozen=True)
class HostObservation:
    observed_at_ms: int
    policy_sha256: str
    authorized_connectors: int
    authorized_uids: tuple[int, ...]
    broker_socket_count: int
    broker_process_count: int
    credential_exposure_count: int
    paper_units_inactive: bool
    kill_switch_engaged: bool
    process_inventory_complete: bool
    socket_inventory_complete: bool
    credential_inventory_complete: bool
    # The existing production broker-boundary observer does not expose the
    # egress supervisor generation.  Terminal evidence must therefore reject
    # its observation until that independently sourced value is available;
    # legacy admission evidence remains compatible with ``None``.
    egress_policy_generation: int | None = None


@dataclass(frozen=True)
class HostAuthorityLease:
    """Anchored exclusive lease held for the complete production commit."""

    directory_path: Path
    lease_path: Path
    owner_path: Path
    directory_descriptor: int
    descriptor: int
    directory_identity: tuple[int, ...]
    lease_identity: tuple[int, ...]
    expected_uid: int
    expected_gid: int
    boot_id: str
    secret: object

    @property
    def reference(self) -> dict[str, Any]:
        directory = self.directory_identity
        lease = self.lease_identity
        return {
            "directory_path": str(self.directory_path),
            "lease_path": str(self.lease_path),
            "owner_path": str(self.owner_path),
            "directory_device": directory[0],
            "directory_inode": directory[1],
            "directory_uid": directory[3],
            "directory_gid": directory[4],
            "directory_mode": stat.S_IMODE(directory[2]),
            "lease_device": lease[0],
            "lease_inode": lease[1],
            "lease_uid": lease[4],
            "lease_gid": lease[5],
            "lease_mode": stat.S_IMODE(lease[2]),
            "lease_size": lease[6],
            "held_exclusive": True,
            "boot_id": self.boot_id,
        }


@dataclass(frozen=True)
class SignedEvidence:
    binding: InputBinding
    payload: dict[str, Any]
    payload_bytes: bytes
    payload_sha256: str
    signature: bytes
    signature_sha256: str

    @property
    def reference(self) -> dict[str, str]:
        return {
            "path": str(self.binding.path),
            "file_sha256": digest_bytes(self.binding.payload),
            "signed_payload_sha256": self.payload_sha256,
        }


@dataclass(frozen=True)
class SignatureCertification:
    payload_sha256: str
    signature_sha256: str
    secret: object


@dataclass(frozen=True)
class SnapshotPair:
    broker_snapshot: dict[str, Any]
    account_snapshot: dict[str, Any]
    inputs: tuple[InputBinding, ...]
    signed_evidence: SignedEvidence
    context: "ProductionContext"


@dataclass(frozen=True)
class FinalizationLineage:
    """The securely reopened global reservation lineage head."""

    prior_pointer: InputBinding | None
    prior_tombstone: InputBinding | None
    next_generation: int
    predecessor_finalization_body_sha256: str | None


def _bind_document(
    path: Path, fields: frozenset[str], schema: str, reason: str, *,
    expected_uid: int, expected_gid: int,
) -> InputBinding:
    payload, metadata, parent = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid)
    document = strict_object(payload, reason)
    _require(payload == canonical_bytes(document), reason)
    _sealed(
        document, fields, schema, reason,
        version=HANDOFF_VERSION if schema == HANDOFF_SCHEMA else VERSION)
    return InputBinding(path, payload, _identity(metadata), parent, document)


def _bind_unsealed_document(
    path: Path, reason: str, *, expected_uid: int, expected_gid: int,
) -> InputBinding:
    payload, metadata, parent = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid)
    document = strict_object(payload, reason)
    _require(payload == canonical_bytes(document), reason)
    return InputBinding(path, payload, _identity(metadata), parent, document)


def _bind_admission_artifact(
    path: Path, *, schema: str, reservation: InputBinding,
    expected_uid: int, expected_gid: int,
) -> InputBinding:
    reason = "ZERO_SNAPSHOT_ADMISSION_ARTIFACT_INVALID"
    binding = _bind_unsealed_document(
        _canonical_path(path, reason), reason, expected_uid=expected_uid,
        expected_gid=expected_gid)
    document = binding.document
    _require(
        document.get("schema") == schema and
        document.get("version") == VERSION and
        type(document.get("body_sha256")) is str,
        reason)
    claimed = _digest(document["body_sha256"], reason, nonzero=True)
    body = dict(document)
    del body["body_sha256"]
    _require(
        claimed == digest_bytes(canonical_bytes(body)) and
        document.get("round") == ROUND and
        document.get("domain") == DOMAIN_ID and
        document.get("campaign_id") ==
            reservation.document["campaign_id"] and
        document.get("source_baseline_sha256") ==
            reservation.document["source_baseline_sha256"] and
        all(field in document and document[field] is False
            for field in BOUNDARY_FIELDS),
        reason)
    return binding


def _validate_current_admission_candidate(
    document: Any, *, status: str, now_ms: int, reason: str,
    require_current: bool = True,
) -> None:
    """Parse the exact candidate schema and require a current generation."""

    _require(
        isinstance(document, dict) and
        set(document) == PAPER_ADMISSION_CANDIDATE_FIELDS and
        document.get("schema") == PAPER_ADMISSION_CANDIDATE_SCHEMA and
        document.get("version") == VERSION and
        document.get("status") == status and
        document.get("round") == ROUND and
        document.get("domain") == DOMAIN_ID and
        document.get("authorization_effect") ==
            "NONE_READ_ONLY_CANDIDATE_ONLY" and
        document.get("paper_test_admission_candidate") is (status == "GO") and
        all(document.get(field) is False for field in BOUNDARY_FIELDS),
        reason)
    evaluated = _integer(document.get("evaluated_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    _require(
        evaluated < expires and
        (not require_current or evaluated <= now_ms < expires),
        reason)
    source = _digest(document.get("source_baseline_sha256"), reason)
    strategy = _digest(document.get("strategy_sha256"), reason)
    findings = document.get("findings")
    bindings = document.get("input_bindings")
    _require(
        isinstance(findings, list) and
        findings == sorted(set(findings)) and
        all(type(item) is str and bool(item) for item in findings) and
        isinstance(bindings, dict) and bool(bindings) and
        all(type(name) is str and bool(name) for name in bindings),
        reason)
    for binding in bindings.values():
        _require(
            isinstance(binding, dict) and
            set(binding) == PAPER_ADMISSION_INPUT_BINDING_FIELDS and
            type(binding.get("path")) is str and
            (binding.get("schema") is None or
             type(binding.get("schema")) is str) and
            (binding.get("version") is None or
             type(binding.get("version")) is int) and
            (binding.get("status") is None or
             type(binding.get("status")) is str),
            reason)
        _canonical_path(Path(binding["path"]), reason)
        for field in ("file_sha256", "body_sha256"):
            value = binding.get(field)
            _require(value is None or
                     (type(value) is str and
                      DIGEST.fullmatch(value) is not None), reason)
    if status == "GO":
        _require(
            source != "sha256:" + "0" * 64 and
            strategy != "sha256:" + "0" * 64 and not findings and
            all(binding["file_sha256"] is not None and
                binding["body_sha256"] is not None
                for binding in bindings.values()),
            reason)


def _validate_active_candidate_zero_binding(
    candidate: InputBinding, zero_receipt: InputBinding, *, status: str,
    reservation: InputBinding, lease: HostAuthorityLease,
) -> None:
    """Bind the candidate's zero input to the exact active reservation.

    The admission candidate is durable before the finalization tombstone.  A
    status-only check here would allow a different, otherwise well-formed zero
    receipt to be swapped in at that crash seam.  Recompute the complete
    producer/consumer reference while the host-authority lease is still held.
    """

    reason = "ZERO_SNAPSHOT_ADMISSION_FINALIZATION_INVALID"
    candidate_document = candidate.document
    zero_document = zero_receipt.document
    active = reservation.document
    candidate_inputs = candidate_document.get("input_bindings")
    zero_input = candidate_inputs.get("zero_exposure_receipt") \
        if isinstance(candidate_inputs, dict) else None
    expected_zero_input = {
        "path": str(zero_receipt.path),
        "file_sha256": digest_bytes(zero_receipt.payload),
        "body_sha256": zero_document.get("body_sha256"),
        "schema": zero_document.get("schema"),
        "version": zero_document.get("version"),
        "status": zero_document.get("status"),
    }
    _require(
        candidate_document.get("status") == status and
        candidate_document.get("paper_test_admission_candidate") is
            (status == "GO") and
        zero_document.get("status") in {"PASS", "NO_GO", "HALT"} and
        (status != "GO" or zero_document.get("status") == "PASS") and
        zero_input == expected_zero_input and
        zero_document.get("host_authority_reservation") ==
            reservation_reference(reservation) and
        zero_document.get("reservation_id") == active.get("reservation_id") and
        zero_document.get("reservation_generation") ==
            active.get("reservation_generation") and
        zero_document.get("reservation_predecessor_finalization_body_sha256") ==
            active.get("predecessor_finalization_body_sha256") and
        zero_document.get("reservation_prior_finalization_pointer_reference") ==
            active.get("prior_finalization_pointer_reference") and
        zero_document.get("reservation_finalization_tombstone_path") ==
            active.get("finalization_tombstone_path") and
        zero_document.get("reservation_finalization_current_pointer_path") ==
            active.get("finalization_current_pointer_path") and
        zero_document.get("reservation_boot_id") == active.get("boot_id") and
        zero_document.get("host_authority_lease") == lease.reference ==
            active.get("host_authority_lease") and
        zero_document.get("reservation_lease_device") ==
            lease.reference["lease_device"] and
        zero_document.get("reservation_lease_inode") ==
            lease.reference["lease_inode"] and
        zero_document.get("reservation_continuity_verified") is True and
        zero_document.get("reservation_finalization_tombstone_absent") is True,
        reason)


def _load_finalization_lineage(
    lease: HostAuthorityLease, *, reservation: InputBinding | None = None,
    pending_reservation_id: str | None = None,
) -> FinalizationLineage:
    """Validate every durable tombstone and return the gap-free head.

    A single tombstone for the still-present owner may be ahead of the current
    pointer only while a crashed finalization is being resumed.
    """

    reason = "ZERO_SNAPSHOT_RESERVATION_LINEAGE_INVALID"
    if reservation is None:
        _validate_host_authority_lease(
            lease, allow_unvalidated_owner=pending_reservation_id is not None)
    else:
        _validate_host_authority_lease(lease, reservation=reservation)
    if pending_reservation_id is not None:
        _identifier(pending_reservation_id, RESERVATION_ID, reason)
    pointer_path = reservation_current_pointer_path()
    _require(pointer_path.parent == lease.directory_path, reason)
    pointer: InputBinding | None
    if _named_path_absent(
            pointer_path, expected_uid=lease.expected_uid,
            expected_gid=lease.expected_gid, reason=reason):
        pointer = None
    else:
        pointer = _bind_document(
            pointer_path, RESERVATION_CURRENT_POINTER_FIELDS,
            RESERVATION_CURRENT_POINTER_SCHEMA, reason,
            expected_uid=lease.expected_uid, expected_gid=lease.expected_gid)
    tombstones: list[InputBinding] = []
    try:
        names = os.listdir(lease.directory_descriptor)
    except OSError as error:
        raise ProducerError(reason) from error
    prefix = "finalized.zero-exposure-"
    suffix = ".v1.json"
    for name in names:
        if not (name.startswith(prefix) and name.endswith(suffix)):
            continue
        reservation_id = name[len("finalized."):-len(suffix)]
        _identifier(reservation_id, RESERVATION_ID, reason)
        tombstone = _bind_document(
            lease.directory_path / name, RESERVATION_FINALIZATION_FIELDS,
            RESERVATION_FINALIZATION_SCHEMA, reason,
            expected_uid=lease.expected_uid, expected_gid=lease.expected_gid)
        document = tombstone.document
        _require(
            document.get("reservation_id") == reservation_id and
            document.get("boot_id") == lease.boot_id and
            document.get("host_authority_lease") == lease.reference and
            document.get("round") == ROUND and
            document.get("domain") == DOMAIN_ID and
            document.get("status") in {
                "ABORTED", "ADMISSION_GO", "ADMISSION_NO_GO",
                "ADMISSION_HALT"} and
            document.get("finalization_order") ==
                RESERVATION_FINALIZATION_ORDER and
            document.get("owner_present_at_tombstone_commit") is True and
            document.get("owner_removal_required_after_commit") is True,
            reason)
        _false_boundary(document, reason)
        tombstones.append(tombstone)
    tombstones.sort(key=lambda item: item.document["reservation_generation"])
    pointer_generation = None if pointer is None else _integer(
        pointer.document.get("reservation_generation"), reason, minimum=1)
    previous_body: str | None = None
    previous_pointer_reference: dict[str, str] | None = None
    committed: list[InputBinding] = []
    pending: list[InputBinding] = []
    for expected_generation, tombstone in enumerate(tombstones, start=1):
        document = tombstone.document
        generation = _integer(
            document.get("reservation_generation"), reason, minimum=1)
        predecessor = document.get("predecessor_finalization_body_sha256")
        if predecessor is not None:
            _digest(predecessor, reason, nonzero=True)
        _require(
            generation == expected_generation and
            predecessor == previous_body and
            document.get("prior_finalization_pointer_reference") ==
                previous_pointer_reference,
            reason)
        _validate_reservation_reference(
            document.get("reservation_reference"), reason)
        if document["status"] == "ABORTED":
            _require(
                document.get("candidate_reference") is None and
                document.get("zero_exposure_receipt_reference") is None and
                document.get("recovery_reason") in {
                    "CHALLENGE_NOT_PUBLISHED", "RESERVATION_EXPIRED"} and
                isinstance(document.get("recovery_observation"), dict) and
                set(document["recovery_observation"]) ==
                    RECOVERY_OBSERVATION_FIELDS,
                reason)
        else:
            _validate_reference(document.get("candidate_reference"), reason)
            _validate_reference(
                document.get("zero_exposure_receipt_reference"), reason)
            _require(document.get("recovery_reason") is None and
                     document.get("recovery_observation") is None, reason)
        _false_boundary(document, reason)
        previous_body = document["body_sha256"]
        previous_pointer_document = _pointer_document_for_tombstone(tombstone)
        previous_pointer_reference = {
            "path": str(reservation_current_pointer_path()),
            "file_sha256": digest_bytes(canonical_bytes(
                previous_pointer_document)),
            "body_sha256": previous_pointer_document["body_sha256"],
        }
        if (pending_reservation_id is not None and
                document["reservation_id"] == pending_reservation_id and
                (pointer_generation is None or
                 generation > pointer_generation)):
            pending.append(tombstone)
        else:
            committed.append(tombstone)
    if pointer is None:
        _require(not committed and len(pending) <= 1, reason)
        next_generation = 1
        predecessor = None
        prior_tombstone = None
    else:
        pointer_document = pointer.document
        generation = _integer(
            pointer_document.get("reservation_generation"), reason,
            minimum=1)
        _require(
            pointer_document.get("status") == "CURRENT" and
            pointer_document.get("round") == ROUND and
            pointer_document.get("domain") == DOMAIN_ID and
            pointer_document.get("boot_id") == lease.boot_id and
            pointer_document.get("host_authority_lease") == lease.reference and
            len(committed) == generation and
            pointer_document.get("predecessor_finalization_body_sha256") ==
                (None if generation == 1 else
                 committed[generation - 2].document["body_sha256"]),
            reason)
        prior_tombstone = committed[-1]
        _require(
            pointer_document.get("reservation_id") ==
                prior_tombstone.document["reservation_id"] and
            _validate_reference(
                pointer_document.get("finalization_tombstone_reference"),
                reason) == prior_tombstone.reference,
            reason)
        _require(pointer.document ==
                 _pointer_document_for_tombstone(prior_tombstone), reason)
        _false_boundary(pointer_document, reason)
        next_generation = generation + 1
        predecessor = prior_tombstone.document["body_sha256"]
    _require(
        len(pending) <= 1 and
        (not pending or
         (pending[0].document["reservation_generation"] == next_generation and
          pending[0].document["predecessor_finalization_body_sha256"] ==
              predecessor)),
        reason)
    if pointer is not None:
        pointer.reopen(
            expected_uid=lease.expected_uid, expected_gid=lease.expected_gid,
            reason=reason)
    if prior_tombstone is not None:
        prior_tombstone.reopen(
            expected_uid=lease.expected_uid, expected_gid=lease.expected_gid,
            reason=reason)
    return FinalizationLineage(
        pointer, prior_tombstone, next_generation, predecessor)


def _bind_executable(
    path: Path, reason: str, *, expected_uid: int, expected_gid: int,
    executing: bool = False,
) -> ExecutableBinding:
    path = _canonical_path(path, reason)
    if executing:
        lexical = Path(__file__).absolute()
        try:
            lexical_metadata = os.lstat(lexical)
            resolved = lexical.resolve(strict=True)
            installed = path.resolve(strict=True)
            same = os.path.samefile(resolved, installed)
        except OSError as error:
            raise ProducerError(reason) from error
        _require(
            not stat.S_ISLNK(lexical_metadata.st_mode) and
            lexical == path and resolved == installed == path and same,
            reason)
    payload, metadata, parent = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({0o500, 0o555, 0o700, 0o755}))
    if executing:
        _require(_identity(os.lstat(Path(__file__).absolute())) ==
                 _identity(metadata), reason)
    binding = ExecutableBinding(
        path, payload, _identity(metadata), parent, expected_uid, expected_gid,
        executing)
    binding.reopen()
    return binding


def _bind_trust_file(
    path: Path, reason: str, *, expected_uid: int, expected_gid: int,
) -> TrustFileBinding:
    path = _canonical_path(path, reason)
    payload, metadata, parent = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({0o400, 0o440, 0o444, 0o600, 0o640, 0o644}),
        maximum=64 * 1024)
    binding = TrustFileBinding(
        path, payload, _identity(metadata), parent, expected_uid, expected_gid)
    binding.reopen()
    return binding


def _bind_terminal_provider_artifact(
    path: Path, reason: str, *, expected_uid: int, expected_gid: int,
) -> TerminalProviderArtifactBinding:
    path = _canonical_path(path, reason)
    payload, metadata, parent = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({0o400, 0o440, 0o600, 0o640}),
        maximum=MAXIMUM_JSON_BYTES)
    _require(bool(payload), reason)
    binding = TerminalProviderArtifactBinding(
        path, payload, _identity(metadata), parent, expected_uid, expected_gid)
    binding.reopen()
    return binding


def _validate_legacy_profile_record(
    value: Any, reason: str, *, path: Path, sha256: str, size: int, mode: int,
    body_sha256: str | None = None, expected_uid: int = ROOT_UID,
    expected_gid: int = ROOT_GID,
) -> None:
    fields = {
        "path", "sha256", "bytes", "device", "inode", "mode", "nlink",
        "uid", "gid", "mtime_ns", "ctime_ns",
    }
    if body_sha256 is not None:
        fields.add("body_sha256")
    _require(isinstance(value, dict) and set(value) == fields, reason)
    _require(
        value.get("path") == str(path) and value.get("sha256") == sha256 and
        value.get("bytes") == size and value.get("mode") == stat.S_IFREG | mode
        and value.get("uid") == expected_uid and
        value.get("gid") == expected_gid and
        value.get("nlink") == 1 and
        all(type(value.get(field)) is int and value[field] >= 0 for field in (
            "device", "inode", "mtime_ns", "ctime_ns")) and
        value["inode"] > 0 and
        (body_sha256 is None or value.get("body_sha256") == body_sha256),
        reason)


def _validate_profile_restoration(
    value: Any, reason: str, *, expected_uid: int, expected_gid: int,
) -> None:
    _require(isinstance(value, dict) and set(value) == PROFILE_RESTORATION_FIELDS,
             reason)
    _require(
        value.get("schema") == PROFILE_RESTORATION_SCHEMA and
        value.get("version") == 1 and
        value.get("status") == PROFILE_RESTORATION_STATUS and
        value.get("candidate_path") == str(PAPER_PROFILE_CANDIDATE_PATH) and
        value.get("retired_watch_path") ==
            str(PAPER_PROFILE_RETIRED_WATCH_PATH) and
        value.get("exchange_method") == "RENAME_EXCHANGE" and
        value.get("forward_only_after_exchange") is True,
        reason)
    _digest(value.get("restore_intent_record_sha256"), reason, nonzero=True)
    _digest(value.get("restore_exchange_record_sha256"), reason, nonzero=True)

    _, _, target = _read_profile_file(
        PAPER_PROFILE_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid, mode=0o644,
        sha256=PAPER_PROFILE_DORMANT_SHA256,
        size=PAPER_PROFILE_DORMANT_BYTES)
    _, _, backup = _read_profile_file(
        PAPER_PROFILE_DORMANT_BACKUP_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_PROFILE_DORMANT_SHA256,
        size=PAPER_PROFILE_DORMANT_BYTES)
    _, _, retained = _read_profile_file(
        PAPER_PROFILE_FORWARD_RETAINED_PATH, reason,
        expected_uid=expected_uid, expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_PROFILE_DORMANT_SHA256,
        size=PAPER_PROFILE_DORMANT_BYTES)
    _, _, retired = _read_profile_file(
        PAPER_PROFILE_RETIRED_WATCH_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_PROFILE_WATCH_SHA256, size=PAPER_PROFILE_WATCH_BYTES)
    _validate_profile_record(value.get("target"), target, reason)
    _validate_profile_record(value.get("dormant_backup"), backup, reason)
    _validate_profile_record(
        value.get("forward_retained_dormant"), retained, reason)
    _validate_profile_record(value.get("retired_watch"), retired, reason)

    transition = _read_sealed_profile_document(
        PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH,
        value.get("forward_transition_receipt"), reason,
        expected_uid=expected_uid, expected_gid=expected_gid,
        fields=PROFILE_TRANSITION_FIELDS, schema=PROFILE_TRANSITION_SCHEMA,
        version=2, status=PROFILE_TRANSITION_STATUS)
    deployment = _read_sealed_profile_document(
        PAPER_PROFILE_DEPLOYMENT_RECEIPT_PATH,
        value.get("profile_deployment_receipt"), reason,
        expected_uid=expected_uid, expected_gid=expected_gid,
        fields=PROFILE_DEPLOYMENT_FIELDS, schema=PROFILE_DEPLOYMENT_SCHEMA,
        version=8, status=PROFILE_DEPLOYMENT_STATUS)
    preimage = _read_sealed_profile_document(
        PAPER_PROFILE_FORWARD_PREIMAGE_PATH,
        value.get("forward_preimage_evidence"), reason,
        expected_uid=expected_uid, expected_gid=expected_gid,
        fields=PROFILE_PREIMAGE_FIELDS, schema=PROFILE_PREIMAGE_SCHEMA,
        version=1, status=PROFILE_PREIMAGE_STATUS)

    _require(
        transition.get("target_path") == str(PAPER_PROFILE_PATH) and
        transition.get("backup_path") ==
            str(PAPER_PROFILE_DORMANT_BACKUP_PATH) and
        transition.get("retained_target_path") ==
            str(PAPER_PROFILE_FORWARD_RETAINED_PATH) and
        transition.get("profile_content_changed") is True and
        transition.get("target_written") is True and
        transition.get("target_replaced") is True and
        all(transition.get(field) is False for field in (
            "services_started", "services_stopped", "services_restarted",
            "campaign_launched", "paper_authorized", "live_authorized",
            "mutation_attempted", "direct_broker_access")) and
        deployment.get("target_path") == str(PAPER_PROFILE_PATH) and
        deployment.get("dormant_paper_to_watch_transition_receipt") == {
            "path": str(PAPER_PROFILE_FORWARD_TRANSITION_RECEIPT_PATH),
            "sha256": value["forward_transition_receipt"]["file_sha256"],
            "body_sha256":
                value["forward_transition_receipt"]["body_sha256"],
            "bytes": value["forward_transition_receipt"]["bytes"],
            "device": value["forward_transition_receipt"]["device"],
            "inode": value["forward_transition_receipt"]["inode"],
            "mode": value["forward_transition_receipt"]["mode"],
            "nlink": value["forward_transition_receipt"]["nlink"],
            "uid": value["forward_transition_receipt"]["uid"],
            "gid": value["forward_transition_receipt"]["gid"],
            "mtime_ns": value["forward_transition_receipt"]["mtime_ns"],
            "ctime_ns": value["forward_transition_receipt"]["ctime_ns"],
        } and
        all(preimage.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")), reason)
    _validate_legacy_profile_record(
        transition.get("backup"), reason,
        path=PAPER_PROFILE_DORMANT_BACKUP_PATH,
        sha256=PAPER_PROFILE_DORMANT_SHA256,
        size=PAPER_PROFILE_DORMANT_BYTES, mode=0o600,
        expected_uid=expected_uid, expected_gid=expected_gid)
    _validate_legacy_profile_record(
        transition.get("retained_target"), reason,
        path=PAPER_PROFILE_FORWARD_RETAINED_PATH,
        sha256=PAPER_PROFILE_DORMANT_SHA256,
        size=PAPER_PROFILE_DORMANT_BYTES, mode=0o600,
        expected_uid=expected_uid, expected_gid=expected_gid)
    _validate_legacy_profile_record(
        preimage.get("backup"), reason,
        path=PAPER_PROFILE_DORMANT_BACKUP_PATH,
        sha256=PAPER_PROFILE_DORMANT_SHA256,
        size=PAPER_PROFILE_DORMANT_BYTES, mode=0o600,
        expected_uid=expected_uid, expected_gid=expected_gid)
    _require_profile_candidate_absent(
        PAPER_PROFILE_CANDIDATE_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid)


def _validate_runtime_profile_hardening(
    value: Any, reason: str, *, expected_uid: int, expected_gid: int,
) -> None:
    _require(
        isinstance(value, dict) and
        set(value) == PAPER_RUNTIME_PROFILE_HARDENING_FIELDS and
        value.get("schema") == PAPER_RUNTIME_PROFILE_HARDENING_SCHEMA and
        value.get("version") == 1 and
        value.get("status") == PAPER_RUNTIME_PROFILE_HARDENING_STATUS and
        value.get("candidate_path") ==
            str(PAPER_RUNTIME_PROFILE_CANDIDATE_PATH) and
        value.get("retained_legacy_path") ==
            str(PAPER_RUNTIME_PROFILE_RETAINED_PATH) and
        value.get("exchange_method") == "RENAME_EXCHANGE" and
        value.get("forward_only_after_exchange") is True,
        reason)
    _digest(
        value.get("harden_intent_record_sha256"), reason, nonzero=True)
    _digest(
        value.get("harden_exchange_record_sha256"), reason, nonzero=True)
    _, _, target = _read_profile_file(
        PAPER_RUNTIME_PROFILE_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid, mode=0o644,
        sha256=PAPER_RUNTIME_PROFILE_HARDENED_SHA256,
        size=PAPER_RUNTIME_PROFILE_HARDENED_BYTES)
    _, _, backup = _read_profile_file(
        PAPER_RUNTIME_PROFILE_BACKUP_PATH, reason,
        expected_uid=expected_uid, expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_RUNTIME_PROFILE_LEGACY_SHA256,
        size=PAPER_RUNTIME_PROFILE_LEGACY_BYTES)
    _, _, retained = _read_profile_file(
        PAPER_RUNTIME_PROFILE_RETAINED_PATH, reason,
        expected_uid=expected_uid, expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_RUNTIME_PROFILE_LEGACY_SHA256,
        size=PAPER_RUNTIME_PROFILE_LEGACY_BYTES)
    _validate_profile_record(value.get("target"), target, reason)
    _validate_profile_record(value.get("legacy_backup"), backup, reason)
    _validate_profile_record(value.get("retained_legacy"), retained, reason)
    _require_profile_candidate_absent(
        PAPER_RUNTIME_PROFILE_CANDIDATE_PATH, reason,
        expected_uid=expected_uid, expected_gid=expected_gid)


def validate_handoff(
    document: dict[str, Any], now_ms: int, *, expected_uid: int = ROOT_UID,
    expected_gid: int = ROOT_GID,
) -> None:
    reason = "ZERO_SNAPSHOT_HANDOFF_INVALID"
    _sealed(
        document, HANDOFF_FIELDS, HANDOFF_SCHEMA, reason,
        version=HANDOFF_VERSION)
    _require(document.get("version") == HANDOFF_VERSION and
             document.get("status") == "WATCH_RETIRED_HANDOFF_COMPLETE" and
             document.get("round") == ROUND and
             document.get("domain") == DOMAIN_ID, reason)
    _digest(document.get("source_baseline_sha256"), reason, nonzero=True)
    _identifier(document.get("campaign_id"), IDENTIFIER, reason)
    for field in ("activation_receipt", "p1_audit_receipt", "freeze_bundle"):
        _validate_reference(document.get(field), reason)
    issued = _integer(document.get("issued_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    _require(issued <= now_ms < expires, reason)
    for field in (
        "watch_units_inactive", "paper_units_inactive", "broker_deny_all",
        "kill_switch_engaged", "global_kill_switch_engaged",
        "paper_profile_restored", "profile_candidate_absent",
        "paper_runtime_profile_hardened",
        "paper_runtime_profile_candidate_absent",
        "crash_recovery_verified", *BOUNDARY_FIELDS,
    ):
        _require(type(document.get(field)) is bool, reason)
    for field in (
        "watch_authority_count", "watch_socket_count", "watch_timer_count",
        "cleanup_residue_count", "identity_count",
    ):
        _integer(document.get(field), reason)
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
    _validate_profile_restoration(
        document.get("paper_profile_restoration"), reason,
        expected_uid=expected_uid, expected_gid=expected_gid)
    _validate_runtime_profile_hardening(
        document.get("paper_runtime_profile_hardening"), reason,
        expected_uid=expected_uid, expected_gid=expected_gid)
    identity_payload, _, _ = secure_read(
        IDENTITY_MANIFEST_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid, modes=frozenset({0o600}), maximum=64 * 1024)
    _require(digest_bytes(identity_payload) ==
             DISABLED_IDENTITY_MANIFEST_SHA256, reason)
    _read_kill_switch(
        KILL_SWITCH_PATH, reason, expected_uid=expected_uid,
        expected_file_gid=PAPER_CONTROL_GID,
        expected_parent_gid=PAPER_CONTROL_GID)
    _read_kill_switch(
        GLOBAL_KILL_SWITCH_PATH, reason, expected_uid=expected_uid,
        expected_file_gid=GLOBAL_PAPER_CONTROL_GID,
        expected_parent_gid=GLOBAL_PAPER_CONTROL_GID)
    _false_boundary(document, reason)


def _intent_paths(document: Mapping[str, Any], reason: str) -> dict[str, Path]:
    fields = {
        "handoff": "watch_handoff_receipt_path",
        "challenge": "challenge_output_path",
        "signed_evidence": "signed_account_evidence_path",
        "broker_output": "broker_snapshot_output_path",
        "account_output": "account_snapshot_output_path",
    }
    result: dict[str, Path] = {}
    for name, field in fields.items():
        value = document.get(field)
        _require(type(value) is str, reason)
        result[name] = _canonical_path(Path(value), reason)
    _require(len(set(result.values())) == len(result), reason)
    return result


def validate_intent(
    document: dict[str, Any], now_ms: int, context: "ProductionContext",
    expected_source: str, expected_campaign: str,
) -> dict[str, Path]:
    reason = "ZERO_SNAPSHOT_OPERATOR_INTENT_INVALID"
    _sealed(document, INTENT_FIELDS, INTENT_SCHEMA, reason)
    _require(
        document.get("status") == "APPROVED" and
        document.get("round") == ROUND and
        document.get("domain") == DOMAIN_ID and
        document.get("campaign_id") == expected_campaign and
        document.get("source_baseline_sha256") == expected_source and
        document.get("production_mode") == PRODUCTION_MODE and
        document.get("allow_fixed_read_only_host_observation") is True and
        document.get("allow_offline_signed_account_adaptation") is True,
        reason)
    _identifier(document.get("intent_id"), IDENTIFIER, reason)
    _digest(document.get("account_id_sha256"), reason, nonzero=True)
    issued = _integer(document.get("issued_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    _require(issued <= now_ms < expires, reason)
    _require(
        _executable_reference(document.get("producer"), reason) ==
            context.producer.reference and
        _executable_reference(document.get("broker_policy_helper"), reason) ==
            context.broker_helper.reference and
        _executable_reference(document.get("signature_verifier"), reason) ==
            context.signature_verifier.reference and
        _executable_reference(document.get("verification_key"), reason) ==
            context.verification_key.reference,
        reason)
    _false_boundary(document, reason)
    return _intent_paths(document, reason)


def _require_reservation_tombstone_absent(
    lease: HostAuthorityLease, path: Path, reason: str,
) -> None:
    _require(path.parent == lease.directory_path, reason)
    try:
        os.stat(path.name,
                dir_fd=lease.directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise ProducerError(reason) from error
    raise ProducerError(reason)


def build_reservation(
    *, intent: InputBinding, handoff: InputBinding,
    context: "ProductionContext", lease: HostAuthorityLease,
    paths: Mapping[str, Path], now_ms: int, nonce: str,
    reservation_id: str, lineage: FinalizationLineage | None = None,
) -> dict[str, Any]:
    reason = "ZERO_SNAPSHOT_RESERVATION_INVALID"
    _identifier(nonce, NONCE, reason)
    tombstone_path = reservation_tombstone_path(reservation_id)
    _require_reservation_tombstone_absent(lease, tombstone_path, reason)
    context.validate_host_authority_lease(lease)
    if lineage is None:
        lineage = _load_finalization_lineage(lease)
    _require(type(lineage) is FinalizationLineage, reason)
    expires = min(
        now_ms + MAXIMUM_CHALLENGE_LIFETIME_MS,
        intent.document["expires_at_ms"], handoff.document["expires_at_ms"])
    _require(now_ms < expires, reason)
    boundary = {field: False for field in BOUNDARY_FIELDS}
    return seal({
        "schema": RESERVATION_SCHEMA, "version": VERSION,
        "status": "ACTIVE", "issued_at_ms": now_ms,
        "expires_at_ms": expires, "round": ROUND, "domain": DOMAIN_ID,
        "campaign_id": intent.document["campaign_id"],
        "source_baseline_sha256":
            intent.document["source_baseline_sha256"],
        "reservation_id": reservation_id,
        "reservation_generation": lineage.next_generation,
        "predecessor_finalization_body_sha256":
            lineage.predecessor_finalization_body_sha256,
        "prior_finalization_pointer_reference": None if
            lineage.prior_pointer is None else lineage.prior_pointer.reference,
        "reservation_owner_kind": "ZERO_EXPOSURE_ADMISSION_EVIDENCE",
        "reservation_lifecycle": RESERVATION_LIFECYCLE,
        "next_consumer": RESERVATION_NEXT_CONSUMER,
        "boot_id": lease.boot_id,
        "request_nonce": nonce,
        "account_id_sha256": intent.document["account_id_sha256"],
        "producer": context.producer.reference,
        "production_mode": PRODUCTION_MODE,
        "operator_intent_reference": intent.reference,
        "watch_handoff_receipt": handoff.reference,
        "challenge_output_path": str(paths["challenge"]),
        "signed_account_evidence_path": str(paths["signed_evidence"]),
        "broker_snapshot_output_path": str(paths["broker_output"]),
        "account_snapshot_output_path": str(paths["account_output"]),
        "host_authority_lease": lease.reference,
        "finalization_tombstone_path": str(tombstone_path),
        "finalization_current_pointer_path": str(
            reservation_current_pointer_path()),
        "finalization_tombstone_absent": True, **boundary,
    })


def validate_reservation(
    reservation: InputBinding, now_ms: int, *, intent: InputBinding,
    handoff: InputBinding, context: "ProductionContext",
    lease: HostAuthorityLease, paths: Mapping[str, Path],
) -> None:
    reason = "ZERO_SNAPSHOT_RESERVATION_INVALID"
    document = reservation.document
    _require(reservation.path == HOST_AUTHORITY_OWNER_PATH, reason)
    _sealed(document, RESERVATION_FIELDS, RESERVATION_SCHEMA, reason)
    lineage = _load_finalization_lineage(lease, reservation=reservation)
    _require(
        document.get("status") == "ACTIVE" and
        document.get("round") == ROUND and
        document.get("domain") == DOMAIN_ID and
        document.get("campaign_id") == intent.document["campaign_id"] and
        document.get("source_baseline_sha256") ==
            intent.document["source_baseline_sha256"] and
        document.get("reservation_generation") ==
            lineage.next_generation and
        document.get("predecessor_finalization_body_sha256") ==
            lineage.predecessor_finalization_body_sha256 and
        document.get("prior_finalization_pointer_reference") ==
            (None if lineage.prior_pointer is None else
             lineage.prior_pointer.reference) and
        document.get("reservation_owner_kind") ==
            "ZERO_EXPOSURE_ADMISSION_EVIDENCE" and
        document.get("reservation_lifecycle") == RESERVATION_LIFECYCLE and
        document.get("next_consumer") == RESERVATION_NEXT_CONSUMER and
        document.get("boot_id") == lease.boot_id and
        document.get("account_id_sha256") ==
            intent.document["account_id_sha256"] and
        document.get("producer") == context.producer.reference and
        document.get("production_mode") == PRODUCTION_MODE and
        document.get("operator_intent_reference") == intent.reference and
        document.get("watch_handoff_receipt") == handoff.reference and
        document.get("host_authority_lease") == lease.reference and
        document.get("finalization_tombstone_path") == str(
            reservation_tombstone_path(document.get("reservation_id"))) and
        document.get("finalization_current_pointer_path") == str(
            reservation_current_pointer_path()) and
        document.get("finalization_tombstone_absent") is True,
        reason)
    _identifier(document.get("reservation_id"), RESERVATION_ID, reason)
    _identifier(document.get("request_nonce"), NONCE, reason)
    issued = _integer(document.get("issued_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    _require(issued <= now_ms < expires and
             expires - issued <= MAXIMUM_CHALLENGE_LIFETIME_MS, reason)
    expected_paths = {
        "challenge_output_path": paths["challenge"],
        "signed_account_evidence_path": paths["signed_evidence"],
        "broker_snapshot_output_path": paths["broker_output"],
        "account_snapshot_output_path": paths["account_output"],
    }
    for field, expected in expected_paths.items():
        value = document.get(field)
        _require(type(value) is str and
                 _canonical_path(Path(value), reason) == expected, reason)
    _false_boundary(document, reason)
    _require_reservation_tombstone_absent(
        lease, reservation_tombstone_path(document["reservation_id"]), reason)
    reference = reservation_reference(reservation)
    _require(reference["uid"] == context.expected_uid and
             reference["gid"] == context.expected_gid and
             reference["mode"] == 0o600, reason)
    context.validate_host_authority_lease(lease, reservation)


def validate_reservation_for_recovery(
    reservation: InputBinding, now_ms: int, *, context: "ProductionContext",
    lease: HostAuthorityLease, expected_source: str,
    expected_campaign: str,
) -> None:
    """Validate an active marker without requiring still-current inputs."""

    reason = "ZERO_SNAPSHOT_RESERVATION_RECOVERY_INVALID"
    document = reservation.document
    _require(reservation.path == HOST_AUTHORITY_OWNER_PATH, reason)
    _sealed(document, RESERVATION_FIELDS, RESERVATION_SCHEMA, reason)
    reservation_id = _identifier(
        document.get("reservation_id"), RESERVATION_ID, reason)
    tombstone_path = reservation_tombstone_path(reservation_id)
    tombstone_absent = _named_path_absent(
        tombstone_path, expected_uid=lease.expected_uid,
        expected_gid=lease.expected_gid, reason=reason)
    lineage = _load_finalization_lineage(
        lease, reservation=reservation,
        pending_reservation_id=None if tombstone_absent else reservation_id)
    pointer_already_committed = (
        lineage.prior_tombstone is not None and
        lineage.prior_tombstone.document.get("reservation_id") ==
            reservation_id)
    expected_generation = (
        lineage.next_generation - 1 if pointer_already_committed else
        lineage.next_generation)
    expected_predecessor = (
        lineage.prior_tombstone.document.get(
            "predecessor_finalization_body_sha256") if
        pointer_already_committed else
        lineage.predecessor_finalization_body_sha256)
    expected_prior_pointer = (
        lineage.prior_tombstone.document.get(
            "prior_finalization_pointer_reference") if
        pointer_already_committed else
        (None if lineage.prior_pointer is None else
         lineage.prior_pointer.reference))
    _require(
        document.get("status") == "ACTIVE" and
        document.get("round") == ROUND and
        document.get("domain") == DOMAIN_ID and
        document.get("campaign_id") == expected_campaign and
        document.get("source_baseline_sha256") == expected_source and
        document.get("reservation_generation") == expected_generation and
        document.get("predecessor_finalization_body_sha256") ==
            expected_predecessor and
        document.get("prior_finalization_pointer_reference") ==
            expected_prior_pointer and
        document.get("reservation_owner_kind") ==
            "ZERO_EXPOSURE_ADMISSION_EVIDENCE" and
        document.get("reservation_lifecycle") == RESERVATION_LIFECYCLE and
        document.get("next_consumer") == RESERVATION_NEXT_CONSUMER and
        document.get("boot_id") == lease.boot_id and
        document.get("producer") == context.producer.reference and
        document.get("production_mode") == PRODUCTION_MODE and
        document.get("host_authority_lease") == lease.reference and
        document.get("finalization_tombstone_path") == str(tombstone_path) and
        document.get("finalization_current_pointer_path") == str(
            reservation_current_pointer_path()) and
        document.get("finalization_tombstone_absent") is True,
        reason)
    _digest(expected_source, reason, nonzero=True)
    _identifier(expected_campaign, IDENTIFIER, reason)
    _identifier(document.get("request_nonce"), NONCE, reason)
    _digest(document.get("account_id_sha256"), reason, nonzero=True)
    issued = _integer(document.get("issued_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    _require(issued <= now_ms and issued < expires, reason)
    for field in (
        "challenge_output_path", "signed_account_evidence_path",
        "broker_snapshot_output_path", "account_snapshot_output_path",
    ):
        value = document.get(field)
        _require(type(value) is str, reason)
        _canonical_path(Path(value), reason)
    _false_boundary(document, reason)
    context.validate_host_authority_lease(lease, reservation)


def _recovery_observation(
    first: HostObservation, second: HostObservation,
) -> dict[str, Any]:
    reason = "ZERO_SNAPSHOT_RESERVATION_RECOVERY_BOUNDARY_UNSAFE"
    first = validate_host_observation(first)
    second = validate_host_observation(second)
    _require(
        first.observed_at_ms <= second.observed_at_ms and
        _stable_boundary(first, second) and
        _observation_safe_zero(first) and _observation_safe_zero(second),
        reason)
    result = {
        "first_observed_at_ms": first.observed_at_ms,
        "second_observed_at_ms": second.observed_at_ms,
        "policy_sha256": second.policy_sha256,
        "authorized_connectors": 0, "authorized_uids": [],
        "broker_socket_count": 0, "broker_process_count": 0,
        "credential_exposure_count": 0, "paper_units_inactive": True,
        "kill_switch_engaged": True, "process_inventory_complete": True,
        "socket_inventory_complete": True,
        "credential_inventory_complete": True,
    }
    _require(set(result) == RECOVERY_OBSERVATION_FIELDS, reason)
    return result


def build_reservation_finalization(
    *, reservation: InputBinding, lease: HostAuthorityLease, now_ms: int,
    status: str, candidate_reference: Mapping[str, Any] | None,
    zero_exposure_receipt_reference: Mapping[str, Any] | None,
    recovery_reason: str | None,
    recovery_observation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build the commit-decision tombstone used by recovery or admission."""

    reason = "ZERO_SNAPSHOT_RESERVATION_FINALIZATION_INVALID"
    document = reservation.document
    _require(status in {
        "ABORTED", "ADMISSION_GO", "ADMISSION_NO_GO", "ADMISSION_HALT"},
        reason)
    if status == "ABORTED":
        _require(candidate_reference is None and
                 zero_exposure_receipt_reference is None and
                 recovery_reason in {
                     "CHALLENGE_NOT_PUBLISHED", "RESERVATION_EXPIRED"} and
                 isinstance(recovery_observation, Mapping) and
                 set(recovery_observation) == RECOVERY_OBSERVATION_FIELDS,
                 reason)
    else:
        _require(
            isinstance(candidate_reference, Mapping) and
            set(candidate_reference) == REFERENCE_FIELDS and
            isinstance(zero_exposure_receipt_reference, Mapping) and
            set(zero_exposure_receipt_reference) == REFERENCE_FIELDS and
            recovery_reason is None and recovery_observation is None,
            reason)
    boundary = {field: False for field in BOUNDARY_FIELDS}
    result = seal({
        "schema": RESERVATION_FINALIZATION_SCHEMA, "version": VERSION,
        "status": status, "finalized_at_ms": now_ms, "round": ROUND,
        "domain": DOMAIN_ID, "campaign_id": document["campaign_id"],
        "source_baseline_sha256": document["source_baseline_sha256"],
        "reservation_id": document["reservation_id"],
        "reservation_generation": document["reservation_generation"],
        "predecessor_finalization_body_sha256":
            document["predecessor_finalization_body_sha256"],
        "prior_finalization_pointer_reference":
            document["prior_finalization_pointer_reference"],
        "boot_id": document["boot_id"],
        "reservation_reference": reservation_reference(reservation),
        "candidate_reference": None if candidate_reference is None else
            dict(candidate_reference),
        "zero_exposure_receipt_reference": None if
            zero_exposure_receipt_reference is None else
            dict(zero_exposure_receipt_reference),
        "host_authority_lease": lease.reference,
        "recovery_observation": None if recovery_observation is None else
            dict(recovery_observation),
        "owner_present_at_tombstone_commit": True,
        "owner_removal_required_after_commit": True,
        "finalization_order": RESERVATION_FINALIZATION_ORDER,
        "recovery_reason": recovery_reason, **boundary,
    })
    _require(set(result) == RESERVATION_FINALIZATION_FIELDS, reason)
    return result


def _pointer_document_for_tombstone(
    tombstone: InputBinding,
) -> dict[str, Any]:
    document = tombstone.document
    boundary = {field: False for field in BOUNDARY_FIELDS}
    result = seal({
        "schema": RESERVATION_CURRENT_POINTER_SCHEMA, "version": VERSION,
        "status": "CURRENT",
        "updated_at_ms": document["finalized_at_ms"], "round": ROUND,
        "domain": DOMAIN_ID, "campaign_id": document["campaign_id"],
        "source_baseline_sha256": document["source_baseline_sha256"],
        "boot_id": document["boot_id"],
        "reservation_id": document["reservation_id"],
        "reservation_generation": document["reservation_generation"],
        "predecessor_finalization_body_sha256":
            document["predecessor_finalization_body_sha256"],
        "finalization_tombstone_reference": tombstone.reference,
        "host_authority_lease": document["host_authority_lease"],
        **boundary,
    })
    _require(set(result) == RESERVATION_CURRENT_POINTER_FIELDS,
             "ZERO_SNAPSHOT_RESERVATION_POINTER_INVALID")
    return result


def validate_reservation_finalization(
    tombstone: InputBinding, *, reservation: InputBinding,
    lease: HostAuthorityLease,
) -> None:
    reason = "ZERO_SNAPSHOT_RESERVATION_FINALIZATION_INVALID"
    document = tombstone.document
    active = reservation.document
    _sealed(
        document, RESERVATION_FINALIZATION_FIELDS,
        RESERVATION_FINALIZATION_SCHEMA, reason)
    _require(
        tombstone.path == reservation_tombstone_path(
            active["reservation_id"]) and
        document.get("status") in {
            "ABORTED", "ADMISSION_GO", "ADMISSION_NO_GO",
            "ADMISSION_HALT"} and
        document.get("round") == ROUND and
        document.get("domain") == DOMAIN_ID and
        document.get("campaign_id") == active["campaign_id"] and
        document.get("source_baseline_sha256") ==
            active["source_baseline_sha256"] and
        document.get("reservation_id") == active["reservation_id"] and
        document.get("reservation_generation") ==
            active["reservation_generation"] and
        document.get("predecessor_finalization_body_sha256") ==
            active["predecessor_finalization_body_sha256"] and
        document.get("prior_finalization_pointer_reference") ==
            active["prior_finalization_pointer_reference"] and
        document.get("boot_id") == active["boot_id"] == lease.boot_id and
        document.get("reservation_reference") ==
            reservation_reference(reservation) and
        document.get("host_authority_lease") == lease.reference and
        document.get("owner_present_at_tombstone_commit") is True and
        document.get("owner_removal_required_after_commit") is True and
        document.get("finalization_order") == RESERVATION_FINALIZATION_ORDER,
        reason)
    if document["status"] == "ABORTED":
        _require(
            document.get("candidate_reference") is None and
            document.get("zero_exposure_receipt_reference") is None and
            document.get("recovery_reason") in {
                "CHALLENGE_NOT_PUBLISHED", "RESERVATION_EXPIRED"} and
            isinstance(document.get("recovery_observation"), dict) and
            set(document["recovery_observation"]) ==
                RECOVERY_OBSERVATION_FIELDS,
            reason)
    else:
        _validate_reference(document.get("candidate_reference"), reason)
        _validate_reference(
            document.get("zero_exposure_receipt_reference"), reason)
        _require(document.get("recovery_reason") is None and
                 document.get("recovery_observation") is None, reason)
    _false_boundary(document, reason)
    tombstone.reopen(
        expected_uid=lease.expected_uid, expected_gid=lease.expected_gid,
        reason=reason)


def build_finalization_current_pointer(
    *, reservation: InputBinding, tombstone: InputBinding,
    lease: HostAuthorityLease, now_ms: int,
) -> dict[str, Any]:
    reason = "ZERO_SNAPSHOT_RESERVATION_POINTER_INVALID"
    validate_reservation_finalization(
        tombstone, reservation=reservation, lease=lease)
    _require(type(now_ms) is int and
             now_ms >= tombstone.document["finalized_at_ms"], reason)
    result = _pointer_document_for_tombstone(tombstone)
    return result


def validate_challenge(
    document: dict[str, Any], now_ms: int, *, intent: InputBinding,
    handoff: InputBinding, reservation: InputBinding,
    context: "ProductionContext",
) -> None:
    reason = "ZERO_SNAPSHOT_CHALLENGE_INVALID"
    _sealed(document, CHALLENGE_FIELDS, CHALLENGE_SCHEMA, reason)
    intent_document = intent.document
    _require(
        document.get("status") == "AWAITING_SIGNED_RESPONSE" and
        document.get("round") == ROUND and
        document.get("domain") == DOMAIN_ID and
        document.get("campaign_id") == intent_document["campaign_id"] and
        document.get("source_baseline_sha256") ==
            intent_document["source_baseline_sha256"] and
        document.get("account_id_sha256") ==
            intent_document["account_id_sha256"] and
        document.get("producer") == context.producer.reference and
        document.get("production_mode") == PRODUCTION_MODE and
        document.get("operator_intent_reference") == intent.reference and
        document.get("watch_handoff_receipt") == handoff.reference and
        document.get("host_authority_reservation") ==
            reservation_reference(reservation) and
        document.get("signature_algorithm") == SIGNATURE_ALGORITHM and
        document.get("signature_verifier") ==
            context.signature_verifier.reference and
        document.get("verification_key") ==
            context.verification_key.reference and
        document.get("required_observation_authority") ==
            REMOTE_OBSERVATION_AUTHORITY,
        reason)
    _identifier(document.get("nonce"), NONCE, reason)
    issued = _integer(document.get("issued_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    _require(issued <= now_ms < expires and
             expires - issued <= MAXIMUM_CHALLENGE_LIFETIME_MS, reason)
    _false_boundary(document, reason)


def parse_signed_evidence(binding: InputBinding) -> SignedEvidence:
    reason = "ZERO_SNAPSHOT_SIGNED_ACCOUNT_EVIDENCE_INVALID"
    envelope = binding.document
    _require(
        set(envelope) == SIGNED_EVIDENCE_ENVELOPE_FIELDS and
        envelope.get("schema") == SIGNED_EVIDENCE_ENVELOPE_SCHEMA and
        envelope.get("version") == VERSION and
        isinstance(envelope.get("payload"), dict) and
        set(envelope["payload"]) == SIGNED_EVIDENCE_PAYLOAD_FIELDS and
        envelope["payload"].get("schema") ==
            SIGNED_EVIDENCE_PAYLOAD_SCHEMA and
        envelope["payload"].get("version") == VERSION and
        type(envelope.get("signature_base64")) is str,
        reason)
    try:
        signature = base64.b64decode(
            envelope["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeError, ValueError) as error:
        raise ProducerError(reason) from error
    _require(len(signature) == 64 and
             base64.b64encode(signature).decode("ascii") ==
                envelope["signature_base64"], reason)
    payload = dict(envelope["payload"])
    payload_bytes = canonical_bytes(payload)
    return SignedEvidence(
        binding, payload, payload_bytes, digest_bytes(payload_bytes),
        signature, digest_bytes(signature))


def validate_signed_payload(
    evidence: SignedEvidence, now_ms: int, challenge: InputBinding,
) -> None:
    reason = "ZERO_SNAPSHOT_SIGNED_ACCOUNT_EVIDENCE_INVALID"
    payload = evidence.payload
    challenge_document = challenge.document
    _require(
        payload.get("status") == "COMPLETE" and
        payload.get("round") == ROUND and
        payload.get("domain") == challenge_document["domain"] and
        payload.get("campaign_id") == challenge_document["campaign_id"] and
        payload.get("source_baseline_sha256") ==
            challenge_document["source_baseline_sha256"] and
        payload.get("nonce") == challenge_document["nonce"] and
        payload.get("challenge_body_sha256") ==
            challenge_document["body_sha256"] and
        payload.get("account_id_sha256") ==
            challenge_document["account_id_sha256"] and
        payload.get("observation_authority") ==
            REMOTE_OBSERVATION_AUTHORITY and
        payload.get("query_effect") == REMOTE_QUERY_EFFECT and
        payload.get("read_only_authority") is True and
        payload.get("authoritative") is True and
        payload.get("account_complete") is True,
        reason)
    _identifier(payload.get("provider_id"), IDENTIFIER, reason)
    for field in (
        "account_id_sha256", "provider_request_id_sha256",
        "provider_response_sha256", "challenge_body_sha256",
    ):
        _digest(payload.get(field), reason, nonzero=True)
    observed = _integer(payload.get("observed_at_ms"), reason)
    expires = _integer(payload.get("expires_at_ms"), reason)
    _require(
        challenge_document["issued_at_ms"] <= observed <=
            now_ms + MAXIMUM_CLOCK_SKEW_MS and
        now_ms - observed <= MAXIMUM_EVIDENCE_AGE_MS and
        observed < expires and now_ms < expires,
        reason)
    _validate_account_state(payload, reason)
    _false_boundary(payload, reason)


def parse_terminal_signed_evidence(binding: InputBinding) -> SignedEvidence:
    reason = "TERMINAL_WITNESS_SIGNED_ACCOUNT_EVIDENCE_INVALID"
    envelope = binding.document
    _require(
        set(envelope) == SIGNED_EVIDENCE_ENVELOPE_FIELDS and
        envelope.get("schema") == SIGNED_EVIDENCE_ENVELOPE_SCHEMA and
        envelope.get("version") == VERSION and
        isinstance(envelope.get("payload"), dict) and
        set(envelope["payload"]) == TERMINAL_SIGNED_EVIDENCE_PAYLOAD_FIELDS and
        envelope["payload"].get("schema") ==
            TERMINAL_SIGNED_EVIDENCE_SCHEMA and
        envelope["payload"].get("version") == VERSION and
        type(envelope.get("signature_base64")) is str,
        reason)
    try:
        signature = base64.b64decode(
            envelope["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeError, ValueError) as error:
        raise ProducerError(reason) from error
    _require(
        len(signature) == 64 and
        base64.b64encode(signature).decode("ascii") ==
            envelope["signature_base64"], reason)
    payload = dict(envelope["payload"])
    payload_bytes = canonical_bytes(payload)
    return SignedEvidence(
        binding, payload, payload_bytes, digest_bytes(payload_bytes),
        signature, digest_bytes(signature))


def validate_terminal_signed_payload(
    evidence: SignedEvidence, *, now_ms: int, now_monotonic_ns: int,
    challenge: InputBinding, cutoff: InputBinding, trust_policy: InputBinding,
) -> None:
    reason = "TERMINAL_WITNESS_SIGNED_ACCOUNT_EVIDENCE_INVALID"
    payload = evidence.payload
    challenge_document = challenge.document
    cutoff_document = cutoff.document
    policy_document = trust_policy.document
    _require(
        payload.get("status") == TERMINAL_SIGNED_EVIDENCE_STATUS and
        payload.get("round") == ROUND and
        payload.get("nonce") == challenge_document["nonce"] and
        payload.get("challenge_body_sha256") ==
            challenge_document["body_sha256"] and
        payload.get("transport_cutoff_body_sha256") ==
            cutoff_document["body_sha256"] and
        payload.get("provider_id") == policy_document["provider_id"] and
        payload.get("provider_trust_policy_sha256") ==
            trust_policy.document["body_sha256"] and
        payload.get("provider_key_sha256") ==
            policy_document["provider_key_sha256"] and
        payload.get("provider_capability") == TERMINAL_PROVIDER_CAPABILITY and
        payload.get("observation_authority") ==
            REMOTE_OBSERVATION_AUTHORITY and
        payload.get("query_effect") == REMOTE_QUERY_EFFECT,
        reason)
    identity_fields = (
        "domain", "campaign_id", "source_baseline_sha256", "cycle_id",
        "recovery_id", "finalization_id", "boot_id", "service_pid",
        "service_start_ticks", "broker_socket_identity_sha256",
        "account_id_sha256", "owner_set_sha256", "owner_set_canonical_hex",
        "owner_count", "execution_service_epoch",
        "execution_service_fencing_generation", "mutation_fence_generation",
        "known_mutation_command_set_sha256", "known_mutation_command_count",
        "known_correlation_set_sha256", "known_correlation_count",
        "egress_policy_generation", "egress_policy_sha256",
    )
    _require(all(payload.get(field) == challenge_document.get(field)
                 for field in identity_fields), reason)
    for field in (
        "source_baseline_sha256", "broker_socket_identity_sha256",
        "account_id_sha256", "owner_set_sha256",
        "known_mutation_command_set_sha256", "known_correlation_set_sha256",
        "egress_policy_sha256", "provider_trust_policy_sha256",
        "provider_key_sha256", "provider_request_sha256",
        "provider_response_sha256", "consistency_token_sha256",
        "consistency_cutoff_body_sha256",
        "consistency_known_mutation_command_set_sha256",
        "consistency_known_correlation_set_sha256",
    ):
        _digest(payload.get(field), reason, nonzero=True)
    for field in ("query_epoch", "query_invocation_id", "provider_clock_id"):
        _identifier(payload.get(field), IDENTIFIER, reason)
    _identifier(payload.get("provider_boot_id"), BOOT_ID, reason)
    _integer(payload.get("query_fencing_generation"), reason, 1)
    host_timeline = (
        cutoff_document["completed_at_ms"],
        challenge_document["issued_at_ms"], now_ms,
    )
    provider_wall = (
        _integer(payload.get("query_started_at_ms"), reason),
        _integer(payload.get("observed_at_ms"), reason),
        _integer(payload.get("query_completed_at_ms"), reason),
        _integer(payload.get("expires_at_ms"), reason),
    )
    provider_monotonic = (
        _integer(payload.get("query_started_monotonic_ns"), reason),
        _integer(payload.get("observed_monotonic_ns"), reason),
        _integer(payload.get("query_completed_monotonic_ns"), reason),
    )
    _require(
        tuple(sorted(host_timeline)) == host_timeline and
        tuple(sorted(provider_wall)) == provider_wall and
        tuple(sorted(provider_monotonic)) == provider_monotonic and
        cutoff_document["completed_monotonic_ns"] <=
            challenge_document["issued_monotonic_ns"] <= now_monotonic_ns and
        payload.get("query_started_after_challenge") is True and
        policy_document["challenge_bound_query_supported"] is True and
        payload["query_completed_at_ms"] - payload["query_started_at_ms"] <=
            MAXIMUM_EVIDENCE_AGE_MS and
        payload["expires_at_ms"] - payload["query_completed_at_ms"] <=
            MAXIMUM_CHALLENGE_LIFETIME_MS, reason)
    consistency = payload.get("snapshot_consistency")
    _require(
        consistency in {"ATOMIC_ACCOUNT", "CAUSAL_WATERMARK"} and
        (consistency != "ATOMIC_ACCOUNT" or
         policy_document["atomic_account_supported"] is True) and
        (consistency != "CAUSAL_WATERMARK" or
         policy_document["causal_watermark_supported"] is True) and
        payload.get("consistency_cutoff_body_sha256") ==
            cutoff_document["body_sha256"] and
        payload.get("consistency_known_mutation_command_set_sha256") ==
            cutoff_document["known_mutation_command_set_sha256"] and
        payload.get("consistency_known_correlation_set_sha256") ==
            cutoff_document["known_correlation_set_sha256"] and
        payload.get("consistency_dominates_cutoff") is True and
        payload.get("consistency_dominates_all_mutations") is True,
        reason)
    _validate_terminal_zero_account_state(payload, reason)


def _write_memfd(name: str, payload: bytes) -> int:
    try:
        descriptor = os.memfd_create(
            name, os.MFD_CLOEXEC | getattr(os, "MFD_ALLOW_SEALING", 0))
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            _require(count > 0, "ZERO_SNAPSHOT_SIGNATURE_VERIFY_FAILED")
            offset += count
        os.lseek(descriptor, 0, os.SEEK_SET)
        if hasattr(fcntl, "F_ADD_SEALS"):
            seals = (fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK |
                     fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
            fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, seals)
        return descriptor
    except (OSError, ProducerError) as error:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        if isinstance(error, ProducerError):
            raise
        raise ProducerError("ZERO_SNAPSHOT_SIGNATURE_VERIFY_FAILED") from error


def _validate_host_authority_directory(
    metadata: os.stat_result, *, expected_uid: int, expected_gid: int,
    reason: str,
) -> None:
    _require(
        stat.S_ISDIR(metadata.st_mode) and metadata.st_nlink >= 2 and
        metadata.st_uid == expected_uid and metadata.st_gid == expected_gid and
        stat.S_IMODE(metadata.st_mode) == 0o700,
        reason)


def _validate_host_authority_lock(
    metadata: os.stat_result, *, expected_uid: int, expected_gid: int,
    reason: str,
) -> None:
    _require(
        stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
        metadata.st_uid == expected_uid and metadata.st_gid == expected_gid and
        stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_size == 0,
        reason)


def _require_host_authority_owner_absent(
    directory_descriptor: int, owner_name: str, reason: str,
) -> None:
    try:
        os.stat(owner_name, dir_fd=directory_descriptor,
                follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise ProducerError(reason) from error
    raise ProducerError(reason)


def _validate_host_authority_lease(
    lease: HostAuthorityLease, *, reservation: InputBinding | None = None,
    allow_unvalidated_owner: bool = False,
) -> None:
    reason = "ZERO_SNAPSHOT_HOST_AUTHORITY_LEASE_REBOUND"
    _require(type(lease) is HostAuthorityLease, reason)
    directory_path = _canonical_path(HOST_AUTHORITY_DIRECTORY, reason)
    lease_path = _canonical_path(HOST_AUTHORITY_LEASE_PATH, reason)
    owner_path = _canonical_path(HOST_AUTHORITY_OWNER_PATH, reason)
    _require(
        lease.directory_path == directory_path and
        lease.lease_path == lease_path and lease.owner_path == owner_path and
        lease_path.parent == directory_path and
        owner_path.parent == directory_path,
        reason)
    try:
        directory = os.fstat(lease.directory_descriptor)
        opened = os.fstat(lease.descriptor)
        named = os.stat(
            lease_path.name, dir_fd=lease.directory_descriptor,
            follow_symlinks=False)
        _validate_host_authority_directory(
            directory, expected_uid=lease.expected_uid,
            expected_gid=lease.expected_gid, reason=reason)
        for metadata in (opened, named):
            _validate_host_authority_lock(
                metadata, expected_uid=lease.expected_uid,
                expected_gid=lease.expected_gid, reason=reason)
        _require(
            _directory_identity(directory) == lease.directory_identity and
            _identity(opened) == lease.lease_identity == _identity(named),
            reason)
        _require(
            _read_boot_id(
                expected_uid=lease.expected_uid,
                expected_gid=lease.expected_gid, reason=reason) ==
                lease.boot_id,
            reason)
        rebound = _open_directory(directory_path, reason)
        try:
            _require(
                _directory_identity(os.fstat(rebound)) ==
                    lease.directory_identity,
                reason)
        finally:
            os.close(rebound)
        if reservation is not None:
            _require(reservation.path == owner_path, reason)
            reservation.reopen(
                expected_uid=lease.expected_uid,
                expected_gid=lease.expected_gid, reason=reason)
        elif not allow_unvalidated_owner:
            _require_host_authority_owner_absent(
                lease.directory_descriptor, owner_path.name, reason)
        _require(set(lease.reference) == HOST_AUTHORITY_LEASE_FIELDS, reason)
    except ProducerError:
        raise
    except OSError as error:
        raise ProducerError(reason) from error


def _acquire_host_authority_lease(
    context: "ProductionContext", *, allow_reservation_owner: bool = False,
) -> HostAuthorityLease:
    reason = "ZERO_SNAPSHOT_HOST_AUTHORITY_LEASE_INVALID"
    directory_path = _canonical_path(HOST_AUTHORITY_DIRECTORY, reason)
    lease_path = _canonical_path(HOST_AUTHORITY_LEASE_PATH, reason)
    owner_path = _canonical_path(HOST_AUTHORITY_OWNER_PATH, reason)
    _require(
        lease_path.parent == directory_path and
        owner_path.parent == directory_path,
        reason)
    directory_descriptor = -1
    descriptor = -1
    locked = False
    try:
        directory_descriptor = _open_directory(directory_path, reason)
        directory = os.fstat(directory_descriptor)
        _validate_host_authority_directory(
            directory, expected_uid=context.expected_uid,
            expected_gid=context.expected_gid, reason=reason)
        before = os.stat(
            lease_path.name, dir_fd=directory_descriptor,
            follow_symlinks=False)
        _validate_host_authority_lock(
            before, expected_uid=context.expected_uid,
            expected_gid=context.expected_gid, reason=reason)
        descriptor = os.open(
            lease_path.name, READ_FLAGS, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        _validate_host_authority_lock(
            opened, expected_uid=context.expected_uid,
            expected_gid=context.expected_gid, reason=reason)
        _require(_identity(before) == _identity(opened), reason)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise ProducerError(
                    "ZERO_SNAPSHOT_HOST_AUTHORITY_LEASE_BUSY") from error
            raise ProducerError(reason) from error
        final_opened = os.fstat(descriptor)
        final_named = os.stat(
            lease_path.name, dir_fd=directory_descriptor,
            follow_symlinks=False)
        final_directory = os.fstat(directory_descriptor)
        _validate_host_authority_directory(
            final_directory, expected_uid=context.expected_uid,
            expected_gid=context.expected_gid, reason=reason)
        for metadata in (final_opened, final_named):
            _validate_host_authority_lock(
                metadata, expected_uid=context.expected_uid,
                expected_gid=context.expected_gid, reason=reason)
        _require(
            _directory_identity(directory) ==
                _directory_identity(final_directory) and
            _identity(opened) == _identity(final_opened) ==
                _identity(final_named),
            reason)
        if not allow_reservation_owner:
            _require_host_authority_owner_absent(
                directory_descriptor, owner_path.name, reason)
        lease = HostAuthorityLease(
            directory_path, lease_path, owner_path, directory_descriptor,
            descriptor, _directory_identity(directory), _identity(opened),
            context.expected_uid, context.expected_gid,
            _read_boot_id(
                expected_uid=context.expected_uid,
                expected_gid=context.expected_gid,
                reason=reason),
            context._lease_certification_secret)
        directory_descriptor = -1
        descriptor = -1
        locked = False
        _validate_host_authority_lease(
            lease, allow_unvalidated_owner=allow_reservation_owner)
        return lease
    except ProducerError:
        raise
    except OSError as error:
        raise ProducerError(reason) from error
    finally:
        if descriptor >= 0:
            if locked:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _release_host_authority_lease(
    lease: HostAuthorityLease, *, reservation: InputBinding | None = None,
) -> None:
    failure: Exception | None = None
    try:
        _validate_host_authority_lease(lease, reservation=reservation)
    except Exception as error:
        failure = error
    try:
        fcntl.flock(lease.descriptor, fcntl.LOCK_UN)
    except Exception as error:
        if failure is None:
            failure = error
    for descriptor in (lease.descriptor, lease.directory_descriptor):
        try:
            os.close(descriptor)
        except Exception as error:
            if failure is None:
                failure = error
    if failure is not None:
        raise ProducerError(
            "ZERO_SNAPSHOT_HOST_AUTHORITY_LEASE_RELEASE_FAILED") from failure


def _close_host_authority_lease_fail_closed(lease: HostAuthorityLease) -> None:
    """Close descriptors without altering a possibly unbound owner marker."""

    failure: Exception | None = None
    try:
        fcntl.flock(lease.descriptor, fcntl.LOCK_UN)
    except Exception as error:
        failure = error
    for descriptor in (lease.descriptor, lease.directory_descriptor):
        try:
            os.close(descriptor)
        except Exception as error:
            if failure is None:
                failure = error
    if failure is not None:
        raise ProducerError(
            "ZERO_SNAPSHOT_HOST_AUTHORITY_LEASE_RELEASE_FAILED") from failure


def _validate_finalized_reservation_state(
    lease: HostAuthorityLease, tombstone: InputBinding,
    pointer: InputBinding,
) -> None:
    reason = "ZERO_SNAPSHOT_RESERVATION_FINALIZATION_REBOUND"
    try:
        directory = os.fstat(lease.directory_descriptor)
        opened = os.fstat(lease.descriptor)
        named = os.stat(
            lease.lease_path.name, dir_fd=lease.directory_descriptor,
            follow_symlinks=False)
        _validate_host_authority_directory(
            directory, expected_uid=lease.expected_uid,
            expected_gid=lease.expected_gid, reason=reason)
        for metadata in (opened, named):
            _validate_host_authority_lock(
                metadata, expected_uid=lease.expected_uid,
                expected_gid=lease.expected_gid, reason=reason)
        _require(
            _directory_identity(directory) == lease.directory_identity and
            _identity(opened) == lease.lease_identity == _identity(named) and
            _read_boot_id(
                expected_uid=lease.expected_uid,
                expected_gid=lease.expected_gid, reason=reason) ==
                lease.boot_id,
            reason)
        _require_host_authority_owner_absent(
            lease.directory_descriptor, lease.owner_path.name, reason)
        tombstone.reopen(
            expected_uid=lease.expected_uid, expected_gid=lease.expected_gid,
            reason=reason)
        _require(
            tombstone.document.get("schema") ==
                RESERVATION_FINALIZATION_SCHEMA and
            tombstone.document.get("boot_id") == lease.boot_id and
            tombstone.document.get("host_authority_lease") == lease.reference,
            reason)
        _require(
            pointer.path == reservation_current_pointer_path() and
            pointer.document.get("schema") ==
                RESERVATION_CURRENT_POINTER_SCHEMA and
            pointer.document.get("boot_id") == lease.boot_id and
            pointer.document.get("host_authority_lease") == lease.reference and
            pointer.document.get("finalization_tombstone_reference") ==
                tombstone.reference and
            pointer.document.get("reservation_generation") ==
                tombstone.document.get("reservation_generation"),
            reason)
        pointer.reopen(
            expected_uid=lease.expected_uid,
            expected_gid=lease.expected_gid, reason=reason)
    except ProducerError:
        raise
    except OSError as error:
        raise ProducerError(reason) from error


def _remove_reservation_after_finalization(
    lease: HostAuthorityLease, reservation: InputBinding,
    tombstone: InputBinding, pointer: InputBinding,
) -> None:
    reason = "ZERO_SNAPSHOT_RESERVATION_FINALIZATION_REMOVE_FAILED"
    _validate_host_authority_lease(lease, reservation=reservation)
    tombstone.reopen(
        expected_uid=lease.expected_uid, expected_gid=lease.expected_gid,
        reason=reason)
    _validate_current_pointer_for_finalization(
        pointer, reservation=reservation, tombstone=tombstone, lease=lease)
    try:
        named = os.stat(
            lease.owner_path.name, dir_fd=lease.directory_descriptor,
            follow_symlinks=False)
        _require(_identity(named) == reservation.metadata_identity, reason)
        os.unlink(
            lease.owner_path.name, dir_fd=lease.directory_descriptor)
        os.fsync(lease.directory_descriptor)
    except (OSError, ProducerError) as error:
        if isinstance(error, ProducerError):
            raise
        raise ProducerError(reason) from error
    _validate_finalized_reservation_state(lease, tombstone, pointer)


def _release_finalized_host_authority_lease(
    lease: HostAuthorityLease, tombstone: InputBinding,
    pointer: InputBinding,
) -> None:
    failure: Exception | None = None
    try:
        _validate_finalized_reservation_state(lease, tombstone, pointer)
    except Exception as error:
        failure = error
    try:
        fcntl.flock(lease.descriptor, fcntl.LOCK_UN)
    except Exception as error:
        if failure is None:
            failure = error
    for descriptor in (lease.descriptor, lease.directory_descriptor):
        try:
            os.close(descriptor)
        except Exception as error:
            if failure is None:
                failure = error
    if failure is not None:
        raise ProducerError(
            "ZERO_SNAPSHOT_HOST_AUTHORITY_LEASE_RELEASE_FAILED") from failure


class ProductionContext:
    """Root-only fixed executable/key bindings and signature verifier."""

    __slots__ = (
        "expected_uid", "expected_gid", "producer", "broker_helper",
        "signature_verifier", "verification_key", "_certification_secret",
        "_lease_certification_secret")

    def __init__(self, *, expected_uid: int = ROOT_UID,
                 expected_gid: int = ROOT_GID) -> None:
        _require(os.geteuid() == expected_uid and os.getegid() == expected_gid,
                 "ZERO_SNAPSHOT_ROOT_REQUIRED")
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid
        self.producer = _bind_executable(
            INSTALLED_EXECUTABLE, "ZERO_SNAPSHOT_EXECUTING_IMAGE_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid,
            executing=True)
        self.broker_helper = _bind_executable(
            BROKER_POLICY_HELPER, "ZERO_SNAPSHOT_BROKER_HELPER_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid)
        self.signature_verifier = _bind_executable(
            SIGNATURE_VERIFIER, "ZERO_SNAPSHOT_SIGNATURE_VERIFIER_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid)
        self.verification_key = _bind_trust_file(
            VERIFICATION_KEY, "ZERO_SNAPSHOT_VERIFICATION_KEY_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid)
        self._certification_secret = object()
        self._lease_certification_secret = object()

    def reopen(self) -> None:
        self.producer.reopen()
        self.broker_helper.reopen()
        self.signature_verifier.reopen()
        self.verification_key.reopen()

    def verify_signature(
        self, evidence: SignedEvidence,
    ) -> SignatureCertification:
        self.reopen()
        payload_fd = _write_memfd(
            "hepta-account-evidence", evidence.payload_bytes)
        signature_fd = _write_memfd(
            "hepta-account-signature", evidence.signature)
        arguments = (
            str(SIGNATURE_VERIFIER), "pkeyutl", "-verify", "-pubin",
            "-inkey", str(VERIFICATION_KEY), "-rawin", "-in",
            f"/proc/self/fd/{payload_fd}", "-sigfile",
            f"/proc/self/fd/{signature_fd}")
        try:
            try:
                result = subprocess.run(
                    arguments, check=False, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    pass_fds=(payload_fd, signature_fd),
                    env=SAFE_ENVIRONMENT, cwd="/", timeout=15)
            except (OSError, subprocess.SubprocessError) as error:
                raise ProducerError(
                    "ZERO_SNAPSHOT_SIGNATURE_VERIFY_FAILED") from error
        finally:
            os.close(payload_fd)
            os.close(signature_fd)
        _require(
            result.returncode == 0 and result.stderr == b"" and
            result.stdout == b"Signature Verified Successfully\n",
            "ZERO_SNAPSHOT_SIGNATURE_VERIFY_FAILED")
        self.reopen()
        return SignatureCertification(
            evidence.payload_sha256, evidence.signature_sha256,
            self._certification_secret)

    def certifies(
        self, evidence: SignedEvidence,
        certification: SignatureCertification | None,
    ) -> bool:
        return (
            type(certification) is SignatureCertification and
            certification.secret is self._certification_secret and
            certification.payload_sha256 == evidence.payload_sha256 and
            certification.signature_sha256 == evidence.signature_sha256)

    def acquire_host_authority_lease(
        self, *, allow_reservation_owner: bool = False,
    ) -> HostAuthorityLease:
        return _acquire_host_authority_lease(
            self, allow_reservation_owner=allow_reservation_owner)

    def validate_host_authority_lease(
        self, lease: HostAuthorityLease,
        reservation: InputBinding | None = None,
    ) -> dict[str, Any]:
        _require(
            type(lease) is HostAuthorityLease and
            lease.secret is self._lease_certification_secret,
            "ZERO_SNAPSHOT_HOST_AUTHORITY_LEASE_INVALID")
        _validate_host_authority_lease(lease, reservation=reservation)
        return lease.reference

    def release_host_authority_lease(
        self, lease: HostAuthorityLease,
        reservation: InputBinding | None = None,
    ) -> None:
        _require(
            type(lease) is HostAuthorityLease and
            lease.secret is self._lease_certification_secret,
            "ZERO_SNAPSHOT_HOST_AUTHORITY_LEASE_INVALID")
        _release_host_authority_lease(lease, reservation=reservation)


class AdmissionReservationSession:
    """Continuous owner/lease session used by the admission verifier.

    The caller must open this session before reading any admission input, keep
    it open while evaluating and publishing the candidate, and call
    :meth:`finalize` with that already-durable candidate.  Exiting without a
    successful finalization releases only the flock and deliberately retains
    the owner marker.
    """

    __slots__ = ("context", "lease", "reservation", "opened_at_ms",
                 "_closed", "_secret")

    def __init__(
        self, context: ProductionContext, lease: HostAuthorityLease,
        reservation: InputBinding, opened_at_ms: int, *, _secret: object,
    ) -> None:
        _require(_secret is _ADMISSION_SESSION_SECRET,
                 "ZERO_SNAPSHOT_ADMISSION_SESSION_INVALID")
        self.context = context
        self.lease = lease
        self.reservation = reservation
        self.opened_at_ms = opened_at_ms
        self._closed = False
        self._secret = _secret

    def __enter__(self) -> "AdmissionReservationSession":
        self.reopen()
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        if not self._closed:
            self.close_fail_closed()

    def reopen(self) -> None:
        _require(not self._closed and
                 self._secret is _ADMISSION_SESSION_SECRET,
                 "ZERO_SNAPSHOT_ADMISSION_SESSION_INVALID")
        self.context.reopen()
        self.context.validate_host_authority_lease(
            self.lease, self.reservation)
        self.reservation.reopen(
            expected_uid=self.context.expected_uid,
            expected_gid=self.context.expected_gid,
            reason="ZERO_SNAPSHOT_ADMISSION_SESSION_REBOUND")

    def close_fail_closed(self) -> None:
        _require(not self._closed,
                 "ZERO_SNAPSHOT_ADMISSION_SESSION_INVALID")
        try:
            self.context.release_host_authority_lease(
                self.lease, self.reservation)
        finally:
            self._closed = True

    def finalize(
        self, *, candidate_path: Path, zero_exposure_receipt_path: Path,
        expected_candidate_reference: Mapping[str, Any],
        expected_zero_exposure_receipt_reference: Mapping[str, Any],
        status: str, now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Commit candidate, tombstone, pointer, owner removal, and reopens."""

        reason = "ZERO_SNAPSHOT_ADMISSION_FINALIZATION_INVALID"
        _require(status in {"GO", "NO_GO", "HALT"}, reason)
        _require(type(now_ms) is int or now_ms is None, reason)
        now = _wall_clock_ms()
        _require(type(now) is int and now >= self.opened_at_ms, reason)
        candidate: InputBinding | None = None
        zero_receipt: InputBinding | None = None
        tombstone: InputBinding | None = None
        pointer: InputBinding | None = None
        owner_removed = False
        try:
            self.reopen()
            candidate_path = _canonical_path(candidate_path, reason)
            zero_exposure_receipt_path = _canonical_path(
                zero_exposure_receipt_path, reason)
            expected_candidate = _validate_reference(
                expected_candidate_reference, reason)
            expected_zero_receipt = _validate_reference(
                expected_zero_exposure_receipt_reference, reason)
            _require(
                expected_candidate["path"] == str(candidate_path) and
                expected_zero_receipt["path"] ==
                    str(zero_exposure_receipt_path),
                reason)
            _require(
                len({candidate_path, zero_exposure_receipt_path,
                     self.reservation.path,
                     reservation_tombstone_path(
                         self.reservation.document["reservation_id"]),
                     reservation_current_pointer_path()}) == 5,
                reason)
            candidate = _bind_admission_artifact(
                candidate_path, schema=PAPER_ADMISSION_CANDIDATE_SCHEMA,
                reservation=self.reservation,
                expected_uid=self.context.expected_uid,
                expected_gid=self.context.expected_gid)
            zero_receipt = _bind_admission_artifact(
                zero_exposure_receipt_path,
                schema=ZERO_EXPOSURE_RECEIPT_SCHEMA,
                reservation=self.reservation,
                expected_uid=self.context.expected_uid,
                expected_gid=self.context.expected_gid)
            _require(
                candidate.reference == expected_candidate and
                zero_receipt.reference == expected_zero_receipt,
                reason)
            _validate_current_admission_candidate(
                candidate.document, status=status, now_ms=now,
                reason=reason)
            _require(
                status != "GO" or
                now < self.reservation.document["expires_at_ms"],
                reason)
            _validate_active_candidate_zero_binding(
                candidate, zero_receipt, status=status,
                reservation=self.reservation, lease=self.lease)
            candidate.reopen(
                expected_uid=self.context.expected_uid,
                expected_gid=self.context.expected_gid, reason=reason)
            zero_receipt.reopen(
                expected_uid=self.context.expected_uid,
                expected_gid=self.context.expected_gid, reason=reason)
            self.reopen()
            tombstone_path = reservation_tombstone_path(
                self.reservation.document["reservation_id"])
            commit_now = _wall_clock_ms()
            _require(commit_now >= now and commit_now >= self.opened_at_ms,
                     reason)
            _validate_current_admission_candidate(
                candidate.document, status=status, now_ms=commit_now,
                reason=reason)
            _require(
                status != "GO" or
                commit_now < self.reservation.document["expires_at_ms"],
                reason)
            if _named_path_absent(
                    tombstone_path, expected_uid=self.context.expected_uid,
                    expected_gid=self.context.expected_gid, reason=reason):
                tombstone_document = build_reservation_finalization(
                    reservation=self.reservation, lease=self.lease,
                    now_ms=commit_now, status="ADMISSION_" + status,
                    candidate_reference=candidate.reference,
                    zero_exposure_receipt_reference=zero_receipt.reference,
                    recovery_reason=None, recovery_observation=None)
                _publish_one(
                    tombstone_path, tombstone_document,
                    expected_uid=self.context.expected_uid,
                    expected_gid=self.context.expected_gid)
            tombstone = _bind_document(
                tombstone_path, RESERVATION_FINALIZATION_FIELDS,
                RESERVATION_FINALIZATION_SCHEMA, reason,
                expected_uid=self.context.expected_uid,
                expected_gid=self.context.expected_gid)
            _require(
                tombstone.document.get("status") == "ADMISSION_" + status and
                tombstone.document.get("candidate_reference") ==
                    candidate.reference and
                tombstone.document.get("zero_exposure_receipt_reference") ==
                    zero_receipt.reference,
                reason)
            validate_reservation_finalization(
                tombstone, reservation=self.reservation, lease=self.lease)
            candidate.reopen(
                expected_uid=self.context.expected_uid,
                expected_gid=self.context.expected_gid, reason=reason)
            zero_receipt.reopen(
                expected_uid=self.context.expected_uid,
                expected_gid=self.context.expected_gid, reason=reason)
            pointer = _commit_finalization_pointer(
                reservation=self.reservation, tombstone=tombstone,
                lease=self.lease, now_ms=commit_now)
            candidate.reopen(
                expected_uid=self.context.expected_uid,
                expected_gid=self.context.expected_gid, reason=reason)
            zero_receipt.reopen(
                expected_uid=self.context.expected_uid,
                expected_gid=self.context.expected_gid, reason=reason)
            _remove_reservation_after_finalization(
                self.lease, self.reservation, tombstone, pointer)
            owner_removed = True
            candidate.reopen(
                expected_uid=self.context.expected_uid,
                expected_gid=self.context.expected_gid, reason=reason)
            zero_receipt.reopen(
                expected_uid=self.context.expected_uid,
                expected_gid=self.context.expected_gid, reason=reason)
            _validate_finalized_reservation_state(
                self.lease, tombstone, pointer)
            self.context.reopen()
            _release_finalized_host_authority_lease(
                self.lease, tombstone, pointer)
            self._closed = True
            return tombstone.document
        except Exception:
            if not self._closed:
                _close_host_authority_lease_fail_closed(self.lease)
                self._closed = True
            raise
        finally:
            # These names intentionally keep their bindings alive through the
            # final secure reopens above; no cleanup may remove durable files.
            _ = (candidate, zero_receipt, tombstone, pointer, owner_removed)


def _bind_finalized_admission_artifact(
    path: Path, *, schema: str, tombstone: InputBinding,
    expected_uid: int, expected_gid: int,
) -> InputBinding:
    reason = "ZERO_SNAPSHOT_FINALIZED_ADMISSION_ARTIFACT_INVALID"
    binding = _bind_unsealed_document(
        _canonical_path(path, reason), reason, expected_uid=expected_uid,
        expected_gid=expected_gid)
    document = binding.document
    terminal = tombstone.document
    _require(
        document.get("schema") == schema and
        document.get("version") == VERSION and
        type(document.get("body_sha256")) is str,
        reason)
    claimed = _digest(document["body_sha256"], reason, nonzero=True)
    body = dict(document)
    del body["body_sha256"]
    _require(
        claimed == digest_bytes(canonical_bytes(body)) and
        document.get("round") == ROUND and
        document.get("domain") == DOMAIN_ID and
        document.get("campaign_id") == terminal["campaign_id"] and
        document.get("source_baseline_sha256") ==
            terminal["source_baseline_sha256"] and
        all(field in document and document[field] is False
            for field in BOUNDARY_FIELDS),
        reason)
    return binding


class FinalizedAdmissionReservationSession:
    """Idempotent owner-absent view of one committed admission terminal."""

    __slots__ = (
        "context", "lease", "tombstone", "pointer", "candidate",
        "zero_receipt", "opened_at_ms", "finalized", "_closed", "_secret")

    def __init__(
        self, context: ProductionContext, lease: HostAuthorityLease,
        tombstone: InputBinding, pointer: InputBinding,
        candidate: InputBinding, zero_receipt: InputBinding,
        opened_at_ms: int, *, _secret: object,
    ) -> None:
        _require(_secret is _ADMISSION_SESSION_SECRET,
                 "ZERO_SNAPSHOT_ADMISSION_SESSION_INVALID")
        self.context = context
        self.lease = lease
        self.tombstone = tombstone
        self.pointer = pointer
        self.candidate = candidate
        self.zero_receipt = zero_receipt
        self.opened_at_ms = opened_at_ms
        self.finalized = True
        self._closed = False
        self._secret = _secret
        self.reopen()

    def __enter__(self) -> "FinalizedAdmissionReservationSession":
        self.reopen()
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        if not self._closed:
            self.close_fail_closed()

    def _validate_terminal_bindings(self) -> None:
        reason = "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID"
        terminal = self.tombstone.document
        candidate = self.candidate.document
        zero = self.zero_receipt.document
        status = terminal["status"].removeprefix("ADMISSION_")
        _validate_current_admission_candidate(
            candidate, status=status, now_ms=_wall_clock_ms(), reason=reason,
            require_current=status == "GO")
        candidate_inputs = candidate.get("input_bindings")
        zero_input = candidate_inputs.get("zero_exposure_receipt") \
            if isinstance(candidate_inputs, dict) else None
        _require(
            terminal.get("status") in {
                "ADMISSION_GO", "ADMISSION_NO_GO", "ADMISSION_HALT"} and
            terminal.get("candidate_reference") == self.candidate.reference and
            terminal.get("zero_exposure_receipt_reference") ==
                self.zero_receipt.reference and
            terminal.get("recovery_reason") is None and
            terminal.get("recovery_observation") is None and
            candidate.get("status") == status and
            candidate.get("paper_test_admission_candidate") is (status == "GO")
            and zero.get("status") in {"PASS", "NO_GO", "HALT"} and
            (status != "GO" or zero.get("status") == "PASS") and
            isinstance(zero_input, dict) and
            zero_input.get("path") == str(self.zero_receipt.path) and
            zero_input.get("file_sha256") ==
                digest_bytes(self.zero_receipt.payload) and
            zero_input.get("body_sha256") ==
                self.zero_receipt.document["body_sha256"] and
            zero.get("host_authority_reservation") ==
                terminal.get("reservation_reference") and
            zero.get("reservation_id") == terminal.get("reservation_id") and
            zero.get("reservation_generation") ==
                terminal.get("reservation_generation") and
            zero.get("reservation_predecessor_finalization_body_sha256") ==
                terminal.get("predecessor_finalization_body_sha256") and
            zero.get("reservation_prior_finalization_pointer_reference") ==
                terminal.get("prior_finalization_pointer_reference") and
            zero.get("reservation_finalization_tombstone_path") ==
                str(self.tombstone.path) and
            zero.get("reservation_finalization_current_pointer_path") ==
                str(self.pointer.path) and
            zero.get("reservation_boot_id") == terminal.get("boot_id") and
            zero.get("host_authority_lease") == self.lease.reference ==
                terminal.get("host_authority_lease") and
            zero.get("reservation_lease_device") ==
                self.lease.reference["lease_device"] and
            zero.get("reservation_lease_inode") ==
                self.lease.reference["lease_inode"] and
            zero.get("reservation_continuity_verified") is True and
            zero.get("reservation_finalization_tombstone_absent") is True,
            reason)

    def reopen(self) -> None:
        _require(not self._closed and
                 self._secret is _ADMISSION_SESSION_SECRET,
                 "ZERO_SNAPSHOT_ADMISSION_SESSION_INVALID")
        self.context.reopen()
        lineage = _load_finalization_lineage(self.lease)
        _require(
            lineage.prior_pointer is not None and
            lineage.prior_tombstone is not None and
            lineage.prior_pointer.reference == self.pointer.reference and
            lineage.prior_tombstone.reference == self.tombstone.reference,
            "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID")
        _validate_finalized_reservation_state(
            self.lease, self.tombstone, self.pointer)
        for binding in (self.candidate, self.zero_receipt):
            binding.reopen(
                expected_uid=self.context.expected_uid,
                expected_gid=self.context.expected_gid,
                reason="ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_REBOUND")
        self._validate_terminal_bindings()

    def close_fail_closed(self) -> None:
        _require(not self._closed,
                 "ZERO_SNAPSHOT_ADMISSION_SESSION_INVALID")
        try:
            _close_host_authority_lease_fail_closed(self.lease)
        finally:
            self._closed = True

    def finalize(
        self, *, candidate_path: Path, zero_exposure_receipt_path: Path,
        expected_candidate_reference: Mapping[str, Any],
        expected_zero_exposure_receipt_reference: Mapping[str, Any],
        status: str, now_ms: int | None = None,
    ) -> dict[str, Any]:
        reason = "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID"
        _require(status in {"GO", "NO_GO", "HALT"}, reason)
        _require(type(now_ms) is int or now_ms is None, reason)
        try:
            self.reopen()
            expected_candidate = _validate_reference(
                expected_candidate_reference, reason)
            expected_zero_receipt = _validate_reference(
                expected_zero_exposure_receipt_reference, reason)
            _require(
                _canonical_path(candidate_path, reason) == self.candidate.path and
                _canonical_path(zero_exposure_receipt_path, reason) ==
                    self.zero_receipt.path and
                self.candidate.reference == expected_candidate and
                self.zero_receipt.reference == expected_zero_receipt and
                self.tombstone.document["status"] == "ADMISSION_" + status,
                reason)
            _release_finalized_host_authority_lease(
                self.lease, self.tombstone, self.pointer)
            self._closed = True
            return self.tombstone.document
        except Exception:
            if not self._closed:
                _close_host_authority_lease_fail_closed(self.lease)
                self._closed = True
            raise


def open_admission_reservation_session(
    *, expected_source: str, expected_campaign: str,
    candidate_path: Path | None = None,
    zero_exposure_receipt_path: Path | None = None,
    production_mode: str | None, expected_uid: int = ROOT_UID,
    expected_gid: int = ROOT_GID, now_ms: int | None = None,
    _run_token: object | None = None,
) -> AdmissionReservationSession:
    """Acquire the existing owner lease before admission reads any input."""

    _require(_run_token is CLI_RUN_TOKEN,
             "ZERO_SNAPSHOT_CLI_RUN_REQUIRED")
    _require(production_mode == PRODUCTION_MODE,
             "ZERO_SNAPSHOT_EXPLICIT_PRODUCTION_INTENT_REQUIRED")
    _digest(expected_source, "ZERO_SNAPSHOT_EXPECTED_SOURCE_INVALID",
            nonzero=True)
    _identifier(expected_campaign, IDENTIFIER,
                "ZERO_SNAPSHOT_EXPECTED_CAMPAIGN_INVALID")
    _require(type(now_ms) is int or now_ms is None,
             "ZERO_SNAPSHOT_TIME_INVALID")
    now = _wall_clock_ms()
    _require(type(now) is int and now >= 0, "ZERO_SNAPSHOT_TIME_INVALID")
    context = ProductionContext(
        expected_uid=expected_uid, expected_gid=expected_gid)
    lease = context.acquire_host_authority_lease(
        allow_reservation_owner=True)
    try:
        owner_absent = _named_path_absent(
            HOST_AUTHORITY_OWNER_PATH, expected_uid=expected_uid,
            expected_gid=expected_gid,
            reason="ZERO_SNAPSHOT_ADMISSION_SESSION_INVALID")
        if owner_absent:
            _require(
                candidate_path is not None and
                zero_exposure_receipt_path is not None,
                "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID")
            lineage = _load_finalization_lineage(lease)
            _require(
                lineage.prior_pointer is not None and
                lineage.prior_tombstone is not None,
                "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID")
            tombstone = lineage.prior_tombstone
            pointer = lineage.prior_pointer
            _require(
                tombstone.document.get("source_baseline_sha256") ==
                    expected_source and
                tombstone.document.get("campaign_id") == expected_campaign and
                tombstone.document.get("status") in {
                    "ADMISSION_GO", "ADMISSION_NO_GO", "ADMISSION_HALT"},
                "ZERO_SNAPSHOT_FINALIZED_ADMISSION_SESSION_INVALID")
            candidate = _bind_finalized_admission_artifact(
                candidate_path, schema=PAPER_ADMISSION_CANDIDATE_SCHEMA,
                tombstone=tombstone, expected_uid=expected_uid,
                expected_gid=expected_gid)
            zero_receipt = _bind_finalized_admission_artifact(
                zero_exposure_receipt_path,
                schema=ZERO_EXPOSURE_RECEIPT_SCHEMA, tombstone=tombstone,
                expected_uid=expected_uid, expected_gid=expected_gid)
            return FinalizedAdmissionReservationSession(
                context, lease, tombstone, pointer, candidate, zero_receipt,
                now, _secret=_ADMISSION_SESSION_SECRET)
        reservation = _bind_document(
            HOST_AUTHORITY_OWNER_PATH, RESERVATION_FIELDS,
            RESERVATION_SCHEMA, "ZERO_SNAPSHOT_ADMISSION_SESSION_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid)
        validate_reservation_for_recovery(
            reservation, now, context=context, lease=lease,
            expected_source=expected_source,
            expected_campaign=expected_campaign)
        context.validate_host_authority_lease(lease, reservation)
        return AdmissionReservationSession(
            context, lease, reservation, now,
            _secret=_ADMISSION_SESSION_SECRET)
    except Exception:
        _close_host_authority_lease_fail_closed(lease)
        raise


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    netns_device: int
    netns_inode: int

    @property
    def netns(self) -> tuple[int, int]:
        return self.netns_device, self.netns_inode


class ProductionReadOnlyObserver:
    """Fixed local observer with no authority or mutation operation."""

    __slots__ = ("context",)

    def __init__(self, context: ProductionContext) -> None:
        _require(type(context) is ProductionContext,
                 "ZERO_SNAPSHOT_PRODUCTION_CONTEXT_REQUIRED")
        self.context = context

    @staticmethod
    def _show(unit: str) -> dict[str, Any]:
        names = (
            "LoadState", "ActiveState", "SubState", "Job", "UnitFileState",
            "MainPID", "ControlPID")
        arguments = (
            SYSTEMCTL, "show", "--no-pager", "--property=" + ",".join(names),
            unit)
        try:
            result = subprocess.run(
                arguments, check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=SAFE_ENVIRONMENT, cwd="/", timeout=15)
        except (OSError, subprocess.SubprocessError) as error:
            raise ProducerError("ZERO_SNAPSHOT_SYSTEMD_QUERY_FAILED") from error
        _require(result.returncode == 0 and result.stderr == b"" and
                 0 < len(result.stdout) <= MAXIMUM_COMMAND_BYTES,
                 "ZERO_SNAPSHOT_SYSTEMD_QUERY_FAILED")
        fields: dict[str, str] = {}
        try:
            for line in result.stdout.decode(
                    "utf-8", errors="strict").splitlines():
                key, value = line.split("=", 1)
                _require(key in names and key not in fields,
                         "ZERO_SNAPSHOT_SYSTEMD_QUERY_FAILED")
                fields[key] = value
            _require(set(fields) == set(names),
                     "ZERO_SNAPSHOT_SYSTEMD_QUERY_FAILED")
            main_pid = int(fields["MainPID"], 10)
            control_pid = int(fields["ControlPID"], 10)
        except (UnicodeError, ValueError) as error:
            raise ProducerError(
                "ZERO_SNAPSHOT_SYSTEMD_QUERY_FAILED") from error
        _require(main_pid >= 0 and control_pid >= 0,
                 "ZERO_SNAPSHOT_SYSTEMD_QUERY_FAILED")
        return {
            "load_state": fields["LoadState"],
            "active_state": fields["ActiveState"],
            "sub_state": fields["SubState"], "job": fields["Job"],
            "unit_file_state": fields["UnitFileState"],
            "main_pid": main_pid, "control_pid": control_pid,
        }

    @staticmethod
    def _broker_policy() -> tuple[str, int, tuple[int, ...], int]:
        try:
            result = subprocess.run(
                (PYTHON, "-I", "-S", str(BROKER_POLICY_HELPER),
                 "--check-deny-all"),
                check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=SAFE_ENVIRONMENT, cwd="/", timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            raise ProducerError(
                "ZERO_SNAPSHOT_BROKER_POLICY_QUERY_FAILED") from error
        _require(result.returncode == 0 and result.stderr == b"" and
                 len(result.stdout) <= 4096,
                 "ZERO_SNAPSHOT_BROKER_POLICY_QUERY_FAILED")
        match = re.fullmatch(
            rb"hepta_broker_egress_policy: PASS policy_sha256="
            rb"(?P<sha>[0-9a-f]{64}) authorized_connectors=0 "
            rb"authorized_uids= protected_ports=4\n", result.stdout)
        _require(match is not None,
                 "ZERO_SNAPSHOT_BROKER_POLICY_QUERY_FAILED")
        assert match is not None
        source_sha256 = "sha256:" + match.group("sha").decode("ascii")
        try:
            receipt_result = subprocess.run(
                (PYTHON, "-I", "-S", str(BROKER_POLICY_HELPER),
                 "--read-current-boundary"),
                check=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=SAFE_ENVIRONMENT, cwd="/", timeout=15)
        except (OSError, subprocess.SubprocessError) as error:
            raise ProducerError(
                "ZERO_SNAPSHOT_BROKER_BOUNDARY_RECEIPT_FAILED") from error
        _require(
            receipt_result.returncode == 0 and receipt_result.stderr == b"" and
            0 < len(receipt_result.stdout) <= 64 * 1024,
            "ZERO_SNAPSHOT_BROKER_BOUNDARY_RECEIPT_FAILED")
        receipt = strict_object(
            receipt_result.stdout,
            "ZERO_SNAPSHOT_BROKER_BOUNDARY_RECEIPT_FAILED")
        _require(
            set(receipt) == EGRESS_BOUNDARY_RECEIPT_FIELDS and
            receipt_result.stdout == canonical_bytes(receipt) and
            receipt.get("schema") == EGRESS_BOUNDARY_RECEIPT_SCHEMA and
            receipt.get("version") == 1 and
            receipt.get("status") == "EXACT_DENY_ALL" and
            receipt.get("state") == "DENY_ALL" and
            receipt.get("family") == "inet" and
            receipt.get("table") == "hepta_broker_egress_v1" and
            receipt.get("chain") == "output" and
            receipt.get("guard_chain") == "ib_guard" and
            receipt.get("protected_tcp_destination_ports") ==
                list(PROTECTED_BROKER_PORTS) and
            receipt.get("protected_port_count") == 4 and
            receipt.get("authorized_connector_count") == 0 and
            receipt.get("authorized_connectors") == [] and
            receipt.get("authorized_uids") == [] and
            receipt.get("paper_authorized") is False and
            receipt.get("live_authorized") is False and
            receipt.get("source_policy_sha256") == source_sha256 and
            type(receipt.get("generation")) is int and
            receipt["generation"] > 0 and
            _digest(receipt.get("state_sha256"),
                    "ZERO_SNAPSHOT_BROKER_BOUNDARY_RECEIPT_FAILED",
                    nonzero=True) == receipt["state_sha256"],
            "ZERO_SNAPSHOT_BROKER_BOUNDARY_RECEIPT_FAILED")
        body = dict(receipt)
        claimed = body.pop("body_sha256", None)
        _require(
            claimed == digest_bytes(canonical_bytes(body)),
            "ZERO_SNAPSHOT_BROKER_BOUNDARY_RECEIPT_FAILED")
        return receipt["state_sha256"], 0, tuple(), receipt["generation"]

    @staticmethod
    def _kill_switch() -> bool:
        for path, gid in (
                (KILL_SWITCH_PATH, PAPER_CONTROL_GID),
                (GLOBAL_KILL_SWITCH_PATH, GLOBAL_PAPER_CONTROL_GID)):
            payload, _, _ = secure_read(
                path, "ZERO_SNAPSHOT_KILL_SWITCH_QUERY_FAILED",
                expected_uid=ROOT_UID, expected_gid=gid,
                modes=frozenset({0o440}), maximum=8)
            _require(payload == b"engaged",
                     "ZERO_SNAPSHOT_KILL_SWITCH_QUERY_FAILED")
        return True

    @staticmethod
    def _bounded_proc_read(path: Path, maximum: int) -> bytes:
        try:
            descriptor = os.open(path, os.O_RDONLY | NOFOLLOW | CLOEXEC |
                                 NONBLOCK)
            try:
                payload = bytearray()
                while len(payload) <= maximum:
                    chunk = os.read(
                        descriptor, min(65536, maximum + 1 - len(payload)))
                    if not chunk:
                        break
                    payload.extend(chunk)
                _require(len(payload) <= maximum,
                         "ZERO_SNAPSHOT_PROCESS_INVENTORY_FAILED")
                return bytes(payload)
            finally:
                os.close(descriptor)
        except FileNotFoundError:
            raise
        except OSError as error:
            raise ProducerError(
                "ZERO_SNAPSHOT_PROCESS_INVENTORY_FAILED") from error

    @classmethod
    def _process_identity(cls, pid: int) -> ProcessIdentity:
        root = PROC_ROOT / str(pid)
        try:
            raw = cls._bounded_proc_read(root / "stat", 64 * 1024)
            namespace = os.stat(root / "ns" / "net")
            text = raw.decode("ascii", errors="strict")
            end = text.rfind(") ")
            _require(end > 0, "ZERO_SNAPSHOT_PROCESS_INVENTORY_FAILED")
            fields = text[end + 2:].split()
            # The suffix starts at proc stat field 3; starttime is field 22.
            start_ticks = int(fields[19], 10)
        except FileNotFoundError as error:
            raise InventoryRetry() from error
        except (OSError, UnicodeError, ValueError, IndexError) as error:
            raise ProducerError(
                "ZERO_SNAPSHOT_PROCESS_INVENTORY_FAILED") from error
        _require(start_ticks > 0,
                 "ZERO_SNAPSHOT_PROCESS_INVENTORY_FAILED")
        return ProcessIdentity(
            pid, start_ticks, namespace.st_dev, namespace.st_ino)

    @classmethod
    def _socket_inventory_for_namespace(
        cls, identity: ProcessIdentity,
    ) -> tuple[set[tuple[Any, ...]], set[str]]:
        records: set[tuple[Any, ...]] = set()
        inodes: set[str] = set()
        for name in ("tcp", "tcp6"):
            path = PROC_ROOT / str(identity.pid) / "net" / name
            try:
                payload = cls._bounded_proc_read(path, 8 * 1024 * 1024)
                lines = payload.decode("ascii", errors="strict").splitlines()
            except FileNotFoundError as error:
                raise InventoryRetry() from error
            except UnicodeError as error:
                raise ProducerError(
                    "ZERO_SNAPSHOT_SOCKET_INVENTORY_FAILED") from error
            _require(bool(lines) and lines[0].split()[:2] ==
                     ["sl", "local_address"],
                     "ZERO_SNAPSHOT_SOCKET_INVENTORY_FAILED")
            for line in lines[1:]:
                fields = line.split()
                _require(len(fields) >= 10,
                         "ZERO_SNAPSHOT_SOCKET_INVENTORY_FAILED")
                try:
                    local_port = int(fields[1].rsplit(":", 1)[1], 16)
                    remote_port = int(fields[2].rsplit(":", 1)[1], 16)
                    inode = int(fields[9], 10)
                except (IndexError, ValueError) as error:
                    raise ProducerError(
                        "ZERO_SNAPSHOT_SOCKET_INVENTORY_FAILED") from error
                if local_port not in PROTECTED_BROKER_PORTS and \
                        remote_port not in PROTECTED_BROKER_PORTS:
                    continue
                record = (
                    identity.netns, name, fields[1], fields[2], fields[3],
                    fields[9])
                _require(record not in records,
                         "ZERO_SNAPSHOT_SOCKET_INVENTORY_FAILED")
                records.add(record)
                if inode > 0:
                    inodes.add(str(inode))
        _require(cls._process_identity(identity.pid) == identity,
                 "ZERO_SNAPSHOT_PROCESS_INVENTORY_FAILED")
        return records, inodes

    @classmethod
    def _inventory_once(cls) -> tuple[int, int]:
        try:
            initial_names = sorted(
                name for name in os.listdir(PROC_ROOT) if name.isdigit())
        except OSError as error:
            raise ProducerError(
                "ZERO_SNAPSHOT_PROCESS_INVENTORY_FAILED") from error
        identities: dict[int, ProcessIdentity] = {}
        for name in initial_names:
            try:
                identity = cls._process_identity(int(name, 10))
            except InventoryRetry:
                continue
            identities[identity.pid] = identity
        representatives: dict[tuple[int, int], ProcessIdentity] = {}
        for identity in identities.values():
            representatives.setdefault(identity.netns, identity)
        namespace_inodes: dict[tuple[int, int], set[str]] = {}
        socket_records: set[tuple[Any, ...]] = set()
        for namespace, identity in representatives.items():
            records, inodes = cls._socket_inventory_for_namespace(identity)
            socket_records.update(records)
            namespace_inodes[namespace] = inodes

        broker_processes: set[tuple[int, int]] = set()
        for pid, identity in identities.items():
            process = PROC_ROOT / str(pid)
            try:
                cmdline = cls._bounded_proc_read(
                    process / "cmdline", 128 * 1024)
                try:
                    executable = os.readlink(process / "exe").encode(
                        "utf-8", errors="strict")
                except FileNotFoundError:
                    executable = b""
                descriptors = os.listdir(process / "fd")
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError) as error:
                if not process.exists():
                    continue
                raise ProducerError(
                    "ZERO_SNAPSHOT_PROCESS_INVENTORY_FAILED") from error
            owns_protected = False
            protected = namespace_inodes.get(identity.netns, set())
            for descriptor_name in descriptors:
                try:
                    target = os.readlink(process / "fd" / descriptor_name)
                except FileNotFoundError:
                    continue
                except OSError as error:
                    if not process.exists():
                        break
                    raise ProducerError(
                        "ZERO_SNAPSHOT_PROCESS_INVENTORY_FAILED") from error
                match = re.fullmatch(r"socket:\[(?P<inode>[0-9]+)\]", target)
                if match is not None and match.group("inode") in protected:
                    owns_protected = True
            try:
                stable = cls._process_identity(pid)
            except InventoryRetry:
                continue
            if stable != identity:
                raise InventoryRetry()
            process_identity = executable + b" " + cmdline.replace(b"\0", b" ")
            if owns_protected or KNOWN_BROKER_PROCESS.search(process_identity):
                broker_processes.add((pid, identity.start_ticks))
        try:
            final_names = {
                name for name in os.listdir(PROC_ROOT) if name.isdigit()}
        except OSError as error:
            raise ProducerError(
                "ZERO_SNAPSHOT_PROCESS_INVENTORY_FAILED") from error
        if final_names - set(initial_names):
            raise InventoryRetry()
        return len(broker_processes), len(socket_records)

    @classmethod
    def _process_and_socket_inventory(cls) -> tuple[int, int]:
        for _attempt in range(MAXIMUM_INVENTORY_ATTEMPTS):
            try:
                return cls._inventory_once()
            except InventoryRetry:
                continue
        raise ProducerError("ZERO_SNAPSHOT_PROCESS_INVENTORY_UNSTABLE")

    @staticmethod
    def _credential_inventory() -> int:
        try:
            names = sorted(os.listdir(SYSTEMD_CREDENTIAL_ROOT))
        except FileNotFoundError:
            return 0
        except OSError as error:
            raise ProducerError(
                "ZERO_SNAPSHOT_CREDENTIAL_INVENTORY_FAILED") from error
        count = 0
        for name in names:
            if PAPER_CREDENTIAL_DIRECTORY.fullmatch(name) is None:
                continue
            path = SYSTEMD_CREDENTIAL_ROOT / name
            try:
                metadata = os.lstat(path)
                _require(stat.S_ISDIR(metadata.st_mode) and
                         not stat.S_ISLNK(metadata.st_mode),
                         "ZERO_SNAPSHOT_CREDENTIAL_INVENTORY_FAILED")
                descriptor = os.open(path, DIRECTORY_FLAGS)
                try:
                    entries = os.listdir(descriptor)
                finally:
                    os.close(descriptor)
            except (OSError, ProducerError) as error:
                if isinstance(error, ProducerError):
                    raise
                raise ProducerError(
                    "ZERO_SNAPSHOT_CREDENTIAL_INVENTORY_FAILED") from error
            count += len(entries)
            _require(count <= 1024,
                     "ZERO_SNAPSHOT_CREDENTIAL_INVENTORY_FAILED")
        return count

    def observe(self, *, now_ms: int | None = None) -> HostObservation:
        self.context.reopen()
        states = {unit: self._show(unit) for unit in PAPER_UNITS}
        for value in states.values():
            _require(set(value) == UNIT_STATE_FIELDS,
                     "ZERO_SNAPSHOT_SYSTEMD_QUERY_FAILED")
        policy, connectors, uids, generation = self._broker_policy()
        kill_switch = self._kill_switch()
        processes, sockets = self._process_and_socket_inventory()
        credentials = self._credential_inventory()
        inactive = all(
            value["load_state"] == "loaded" and
            value["active_state"] == "inactive" and
            value["sub_state"] == "dead" and not value["job"] and
            value["main_pid"] == 0 and value["control_pid"] == 0 and
            value["unit_file_state"] in PAPER_INERT_UNIT_FILE_STATES[
                ".socket" if unit.endswith(".socket") else ".service"]
            for unit, value in states.items())
        observed = time.time_ns() // 1_000_000 if now_ms is None else now_ms
        _require(type(observed) is int and observed >= 0,
                 "ZERO_SNAPSHOT_TIME_INVALID")
        self.context.reopen()
        return HostObservation(
            observed, policy, connectors, uids, sockets, processes,
            credentials, inactive, kill_switch, True, True, True,
            generation)


def validate_host_observation(value: Any) -> HostObservation:
    reason = "ZERO_SNAPSHOT_HOST_OBSERVATION_INVALID"
    _require(type(value) is HostObservation, reason)
    _integer(value.observed_at_ms, reason)
    _digest(value.policy_sha256, reason, nonzero=True)
    for field in (
        value.authorized_connectors, value.broker_socket_count,
        value.broker_process_count, value.credential_exposure_count,
    ):
        _integer(field, reason)
    if value.egress_policy_generation is not None:
        _integer(value.egress_policy_generation, reason, minimum=1)
    _require(
        isinstance(value.authorized_uids, tuple) and
        value.authorized_uids == tuple(sorted(set(value.authorized_uids))) and
        all(type(uid) is int and uid >= 0 for uid in value.authorized_uids),
        reason)
    for field in (
        value.paper_units_inactive, value.kill_switch_engaged,
        value.process_inventory_complete, value.socket_inventory_complete,
        value.credential_inventory_complete,
    ):
        _require(type(field) is bool, reason)
    return value


def _observation_complete(value: HostObservation) -> bool:
    return (
        value.process_inventory_complete and value.socket_inventory_complete and
        value.credential_inventory_complete)


def _observation_safe_zero(value: HostObservation) -> bool:
    return (
        _observation_complete(value) and
        value.authorized_connectors == 0 and not value.authorized_uids and
        value.broker_socket_count == 0 and
        value.broker_process_count == 0 and
        value.credential_exposure_count == 0 and
        value.paper_units_inactive and value.kill_switch_engaged)


def _stable_boundary(
    first: HostObservation, second: HostObservation,
) -> bool:
    return (
        first.policy_sha256 == second.policy_sha256 and
        first.egress_policy_generation == second.egress_policy_generation and
        first.authorized_connectors == second.authorized_connectors and
        first.authorized_uids == second.authorized_uids and
        first.paper_units_inactive == second.paper_units_inactive and
        first.kill_switch_engaged == second.kill_switch_engaged and
        _observation_complete(first) and _observation_complete(second))


def _load_intent_and_handoff(
    *, operator_intent_path: Path, handoff_path: Path,
    context: ProductionContext, expected_source: str,
    expected_campaign: str, now_ms: int,
) -> tuple[InputBinding, InputBinding, dict[str, Path]]:
    _digest(expected_source, "ZERO_SNAPSHOT_EXPECTED_SOURCE_INVALID",
            nonzero=True)
    _identifier(expected_campaign, IDENTIFIER,
                "ZERO_SNAPSHOT_EXPECTED_CAMPAIGN_INVALID")
    intent = _bind_document(
        _canonical_path(operator_intent_path, "ZERO_SNAPSHOT_PATH_INVALID"),
        INTENT_FIELDS, INTENT_SCHEMA,
        "ZERO_SNAPSHOT_OPERATOR_INTENT_INVALID",
        expected_uid=context.expected_uid, expected_gid=context.expected_gid)
    paths = validate_intent(
        intent.document, now_ms, context, expected_source, expected_campaign)
    normalized_handoff = _canonical_path(
        handoff_path, "ZERO_SNAPSHOT_PATH_INVALID")
    _require(normalized_handoff == paths["handoff"],
             "ZERO_SNAPSHOT_OPERATOR_INTENT_INVALID")
    handoff = _bind_document(
        normalized_handoff, HANDOFF_FIELDS, HANDOFF_SCHEMA,
        "ZERO_SNAPSHOT_HANDOFF_INVALID", expected_uid=context.expected_uid,
        expected_gid=context.expected_gid)
    validate_handoff(
        handoff.document, now_ms, expected_uid=context.expected_uid,
        expected_gid=context.expected_gid)
    _require(
        handoff.document["source_baseline_sha256"] == expected_source and
        handoff.document["campaign_id"] == expected_campaign,
        "ZERO_SNAPSHOT_LINEAGE_MISMATCH")
    return intent, handoff, paths


def build_challenge(
    *, intent: InputBinding, handoff: InputBinding,
    reservation: InputBinding, context: ProductionContext,
    now_ms: int, nonce: str,
) -> dict[str, Any]:
    reason = "ZERO_SNAPSHOT_CHALLENGE_INVALID"
    _identifier(nonce, NONCE, reason)
    expires = min(
        now_ms + MAXIMUM_CHALLENGE_LIFETIME_MS,
        intent.document["expires_at_ms"], handoff.document["expires_at_ms"])
    _require(now_ms < expires, reason)
    boundary = {field: False for field in BOUNDARY_FIELDS}
    challenge = seal({
        "schema": CHALLENGE_SCHEMA, "version": VERSION,
        "status": "AWAITING_SIGNED_RESPONSE", "issued_at_ms": now_ms,
        "expires_at_ms": expires, "round": ROUND, "domain": DOMAIN_ID,
        "campaign_id": intent.document["campaign_id"],
        "source_baseline_sha256":
            intent.document["source_baseline_sha256"],
        "nonce": nonce,
        "account_id_sha256": intent.document["account_id_sha256"],
        "producer": context.producer.reference,
        "production_mode": PRODUCTION_MODE,
        "operator_intent_reference": intent.reference,
        "watch_handoff_receipt": handoff.reference,
        "host_authority_reservation": reservation_reference(reservation),
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signature_verifier": context.signature_verifier.reference,
        "verification_key": context.verification_key.reference,
        "required_observation_authority": REMOTE_OBSERVATION_AUTHORITY,
        **boundary,
    })
    validate_challenge(
        challenge, now_ms, intent=intent, handoff=handoff,
        reservation=reservation, context=context)
    return challenge


def validate_terminal_challenge(
    document: dict[str, Any], *, now_ms: int, now_monotonic_ns: int,
    cutoff: InputBinding, trust_policy: InputBinding,
    context: ProductionContext, boot_id: str,
) -> None:
    reason = "TERMINAL_WITNESS_CHALLENGE_INVALID"
    _sealed(document, TERMINAL_CHALLENGE_FIELDS, TERMINAL_CHALLENGE_SCHEMA,
            reason)
    cutoff_document = cutoff.document
    policy_document = trust_policy.document
    identity_fields = (
        "round", "domain", "campaign_id", "source_baseline_sha256",
        "cycle_id", "recovery_id", "finalization_id", "boot_id",
        "service_pid", "service_start_ticks", "broker_socket_identity_sha256",
        "account_id_sha256", "owner_ids", "owner_set_sha256",
        "owner_set_canonical_hex", "owner_count", "execution_service_epoch",
        "execution_service_fencing_generation", "mutation_fence_generation",
        "known_mutation_command_set_sha256", "known_mutation_command_count",
        "known_correlation_set_sha256", "known_correlation_count",
        "egress_policy_generation", "egress_policy_sha256",
    )
    _require(
        document.get("status") == TERMINAL_CHALLENGE_STATUS and
        all(document.get(field) == cutoff_document.get(field)
            for field in identity_fields) and
        document.get("boot_id") == boot_id and
        document.get("transport_cutoff_receipt") == cutoff.reference and
        document.get("cutoff_completed_at_ms") ==
            cutoff_document["completed_at_ms"] and
        document.get("cutoff_completed_monotonic_ns") ==
            cutoff_document["completed_monotonic_ns"] and
        document.get("producer") == context.producer.reference and
        document.get("production_mode") == TERMINAL_PRODUCTION_MODE and
        document.get("provider_trust_policy") == trust_policy.reference and
        document.get("provider_id") == policy_document["provider_id"] and
        document.get("provider_key_sha256") ==
            policy_document["provider_key_sha256"] and
        document.get("provider_capability") == TERMINAL_PROVIDER_CAPABILITY and
        document.get("signature_algorithm") == SIGNATURE_ALGORITHM and
        document.get("signature_verifier") ==
            context.signature_verifier.reference and
        document.get("verification_key") ==
            context.verification_key.reference and
        document.get("required_observation_authority") ==
            REMOTE_OBSERVATION_AUTHORITY and
        document.get("required_snapshot_consistency") ==
            ["ATOMIC_ACCOUNT", "CAUSAL_WATERMARK"], reason)
    _identifier(document.get("nonce"), NONCE, reason)
    issued = _integer(document.get("issued_at_ms"), reason)
    issued_monotonic = _integer(document.get("issued_monotonic_ns"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    _require(
        cutoff_document["completed_at_ms"] <= issued <= now_ms < expires and
        cutoff_document["completed_monotonic_ns"] <= issued_monotonic <=
            now_monotonic_ns and
        expires - issued <= MAXIMUM_CHALLENGE_LIFETIME_MS, reason)
    _terminal_owner_binding(document, reason)
    _false_boundary(document, reason)


def build_terminal_challenge(
    *, cutoff: InputBinding, trust_policy: InputBinding,
    context: ProductionContext, lease: HostAuthorityLease, now_ms: int,
    now_monotonic_ns: int, nonce: str,
) -> dict[str, Any]:
    reason = "TERMINAL_WITNESS_CHALLENGE_INVALID"
    _identifier(nonce, NONCE, reason)
    validate_terminal_provider_trust_policy(
        trust_policy.document,
        verification_key_sha256=context.verification_key.reference[
            "file_sha256"])
    expires = now_ms + MAXIMUM_CHALLENGE_LIFETIME_MS
    cutoff_document = cutoff.document
    copied = {
        field: cutoff_document[field] for field in (
            "round", "domain", "campaign_id", "source_baseline_sha256",
            "cycle_id", "recovery_id", "finalization_id", "boot_id",
            "service_pid", "service_start_ticks",
            "broker_socket_identity_sha256", "account_id_sha256", "owner_ids",
            "owner_set_sha256", "owner_set_canonical_hex", "owner_count",
            "execution_service_epoch", "execution_service_fencing_generation",
            "mutation_fence_generation", "known_mutation_command_set_sha256",
            "known_mutation_command_count", "known_correlation_set_sha256",
            "known_correlation_count", "egress_policy_generation",
            "egress_policy_sha256")
    }
    boundary = {field: False for field in BOUNDARY_FIELDS}
    challenge = seal({
        "schema": TERMINAL_CHALLENGE_SCHEMA, "version": VERSION,
        "status": TERMINAL_CHALLENGE_STATUS, "issued_at_ms": now_ms,
        "issued_monotonic_ns": now_monotonic_ns, "expires_at_ms": expires,
        **copied, "nonce": nonce, "transport_cutoff_receipt": cutoff.reference,
        "cutoff_completed_at_ms": cutoff_document["completed_at_ms"],
        "cutoff_completed_monotonic_ns":
            cutoff_document["completed_monotonic_ns"],
        "producer": context.producer.reference,
        "production_mode": TERMINAL_PRODUCTION_MODE,
        "provider_trust_policy": trust_policy.reference,
        "provider_id": trust_policy.document["provider_id"],
        "provider_key_sha256": trust_policy.document["provider_key_sha256"],
        "provider_capability": TERMINAL_PROVIDER_CAPABILITY,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signature_verifier": context.signature_verifier.reference,
        "verification_key": context.verification_key.reference,
        "required_observation_authority": REMOTE_OBSERVATION_AUTHORITY,
        "required_snapshot_consistency":
            ["ATOMIC_ACCOUNT", "CAUSAL_WATERMARK"], **boundary,
    })
    validate_terminal_challenge(
        challenge, now_ms=now_ms, now_monotonic_ns=now_monotonic_ns,
        cutoff=cutoff, trust_policy=trust_policy, context=context,
        boot_id=lease.boot_id)
    return challenge


def _signature_proof(
    evidence: SignedEvidence, context: ProductionContext,
) -> dict[str, Any]:
    return {
        "algorithm": SIGNATURE_ALGORITHM,
        "public_key": context.verification_key.reference,
        "verifier": context.signature_verifier.reference,
        "signature_sha256": evidence.signature_sha256,
        "signed_payload_sha256": evidence.payload_sha256,
    }


def validate_terminal_witness(
    document: dict[str, Any], *, cutoff: InputBinding,
    challenge: InputBinding, trust_policy: InputBinding,
    evidence: SignedEvidence, provider_request: TerminalProviderArtifactBinding,
    provider_response: TerminalProviderArtifactBinding,
    context: ProductionContext,
) -> None:
    reason = "TERMINAL_WITNESS_OUTPUT_INVALID"
    _sealed(document, TERMINAL_WITNESS_FIELDS, TERMINAL_WITNESS_SCHEMA, reason)
    payload = evidence.payload
    challenge_document = challenge.document
    cutoff_document = cutoff.document
    identity_fields = (
        "round", "domain", "campaign_id", "source_baseline_sha256",
        "cycle_id", "recovery_id", "finalization_id", "boot_id",
        "service_pid", "service_start_ticks", "broker_socket_identity_sha256",
        "account_id_sha256", "owner_ids", "owner_set_sha256",
        "owner_set_canonical_hex", "owner_count", "execution_service_epoch",
        "execution_service_fencing_generation", "mutation_fence_generation",
        "known_mutation_command_set_sha256", "known_mutation_command_count",
        "known_correlation_set_sha256", "known_correlation_count",
        "egress_policy_generation", "egress_policy_sha256",
    )
    _require(
        document.get("status") == TERMINAL_WITNESS_STATUS and
        document.get("terminal_proof_kind") == TERMINAL_PROOF_KIND and
        all(document.get(field) == challenge_document.get(field)
            for field in identity_fields) and
        document.get("transport_cutoff_receipt") == cutoff.reference and
        document.get("challenge_reference") == challenge.reference and
        document.get("signed_evidence_reference") == evidence.reference and
        document.get("provider_trust_policy") == trust_policy.reference and
        document.get("provider_request_reference") ==
            provider_request.reference and
        document.get("provider_response_reference") ==
            provider_response.reference and
        payload.get("provider_request_sha256") ==
            provider_request.reference["file_sha256"] and
        payload.get("provider_response_sha256") ==
            provider_response.reference["file_sha256"] and
        document.get("signature_verification") ==
            _signature_proof(evidence, context) and
        document.get("nonce") == challenge_document["nonce"], reason)
    evidence_pairs = (
        "provider_id", "provider_key_sha256", "provider_capability",
        "provider_request_sha256", "provider_response_sha256",
        "query_started_at_ms", "query_started_monotonic_ns", "observed_at_ms",
        "observed_monotonic_ns", "query_completed_at_ms",
        "query_completed_monotonic_ns", "provider_clock_id",
        "provider_boot_id", "query_started_after_challenge",
        "snapshot_consistency",
        "consistency_token_sha256", "consistency_dominates_cutoff",
        "consistency_dominates_all_mutations", "active_orders_complete",
        "completed_orders_complete", "executions_complete",
        "positions_complete", "cash_fx_complete", "risk_complete",
        "gross_absolute_position", "gross_fx_exposure", "gross_risk",
        "settled_mutation_command_count", "unknown_mutation_command_count",
        "unresolved_mutation_command_count", "read_only_authority",
        "authoritative", "account_complete", "mutation_attempted",
    )
    _require(all(document.get(field) == payload.get(field)
                 for field in evidence_pairs), reason)
    _require(
        document.get("active_order_count") ==
            len(payload["active_order_id_sha256s"]) == 0 and
        document.get("completed_order_count") ==
            len(payload["completed_order_id_sha256s"]) and
        document.get("execution_count") ==
            len(payload["execution_id_sha256s"]) and
        document.get("position_count") == len(payload["positions"]) == 0 and
        document.get("cash_fx_exposure_count") ==
            len(payload["cash_fx_exposures"]) == 0 and
        document.get("post_cutoff_boundary_verified") is True and
        document.get("egress_policy_generation_stable") is True and
        document.get("host_policy_sha256") ==
            cutoff_document["egress_policy_sha256"] and
        document.get("host_authorized_connectors") == 0 and
        document.get("host_authorized_uids") == [] and
        document.get("host_broker_socket_count") == 0 and
        document.get("host_broker_process_count") == 0 and
        document.get("host_credential_exposure_count") == 0 and
        document.get("host_process_inventory_complete") is True and
        document.get("host_socket_inventory_complete") is True and
        document.get("host_credential_inventory_complete") is True and
        document.get("host_paper_units_inactive") is True and
        document.get("host_kill_switch_engaged") is True and
        cutoff_document["completed_at_ms"] <=
            challenge_document["issued_at_ms"] <=
            document.get("received_at_ms") <=
            document.get("first_host_observed_at_ms") <=
            document.get("second_host_observed_at_ms") <=
            document.get("verified_at_ms") < document.get("expires_at_ms"),
        reason)
    _require(
        cutoff_document["completed_monotonic_ns"] <=
            challenge_document["issued_monotonic_ns"] <=
            document.get("received_monotonic_ns") <=
            document.get("verified_monotonic_ns"), reason)
    _terminal_owner_binding(document, reason)
    _false_boundary(document, reason)


def assemble_terminal_witness(
    *, cutoff: InputBinding, challenge: InputBinding,
    trust_policy: InputBinding, evidence: SignedEvidence,
    provider_request: TerminalProviderArtifactBinding,
    provider_response: TerminalProviderArtifactBinding,
    context: ProductionContext, certification: SignatureCertification,
    first_observation: HostObservation, second_observation: HostObservation,
    received_at_ms: int, received_monotonic_ns: int, verified_at_ms: int,
    verified_monotonic_ns: int,
) -> dict[str, Any]:
    reason = "TERMINAL_WITNESS_OUTPUT_INVALID"
    _require(context.certifies(evidence, certification), reason)
    payload = evidence.payload
    first = validate_host_observation(first_observation)
    second = validate_host_observation(second_observation)
    _require(
        challenge.document["issued_at_ms"] <= received_at_ms <=
            first.observed_at_ms <=
            second.observed_at_ms <= verified_at_ms and
        cutoff.document["completed_monotonic_ns"] <=
            challenge.document["issued_monotonic_ns"] <=
            received_monotonic_ns <= verified_monotonic_ns and
        _stable_boundary(first, second) and
        _observation_safe_zero(first) and _observation_safe_zero(second) and
        first.egress_policy_generation ==
            cutoff.document["egress_policy_generation"] and
        second.egress_policy_generation ==
            cutoff.document["egress_policy_generation"] and
        first.policy_sha256 == second.policy_sha256 ==
            cutoff.document["egress_policy_sha256"], reason)
    expires = min(
        challenge.document["expires_at_ms"],
        verified_at_ms + MAXIMUM_OUTPUT_LIFETIME_MS)
    _require(verified_at_ms < expires, reason)
    copied = {
        field: challenge.document[field] for field in (
            "round", "domain", "campaign_id", "source_baseline_sha256",
            "cycle_id", "recovery_id", "finalization_id", "boot_id",
            "service_pid", "service_start_ticks",
            "broker_socket_identity_sha256", "account_id_sha256", "owner_ids",
            "owner_set_sha256", "owner_set_canonical_hex", "owner_count",
            "execution_service_epoch", "execution_service_fencing_generation",
            "mutation_fence_generation", "known_mutation_command_set_sha256",
            "known_mutation_command_count", "known_correlation_set_sha256",
            "known_correlation_count", "egress_policy_generation",
            "egress_policy_sha256")
    }
    from_payload = {
        field: payload[field] for field in (
            "provider_id", "provider_key_sha256", "provider_capability",
            "provider_request_sha256", "provider_response_sha256",
            "query_started_at_ms", "query_started_monotonic_ns",
            "observed_at_ms", "observed_monotonic_ns", "query_completed_at_ms",
            "query_completed_monotonic_ns", "provider_clock_id",
            "provider_boot_id", "query_started_after_challenge",
            "snapshot_consistency",
            "consistency_token_sha256", "consistency_dominates_cutoff",
            "consistency_dominates_all_mutations", "active_orders_complete",
            "completed_orders_complete", "executions_complete",
            "positions_complete", "cash_fx_complete", "risk_complete",
            "gross_absolute_position", "gross_fx_exposure", "gross_risk",
            "settled_mutation_command_count", "unknown_mutation_command_count",
            "unresolved_mutation_command_count", "read_only_authority",
            "authoritative", "account_complete", "mutation_attempted")
    }
    boundary = {field: False for field in BOUNDARY_FIELDS}
    witness = seal({
        "schema": TERMINAL_WITNESS_SCHEMA, "version": VERSION,
        "status": TERMINAL_WITNESS_STATUS,
        "terminal_proof_kind": TERMINAL_PROOF_KIND,
        "received_at_ms": received_at_ms,
        "received_monotonic_ns": received_monotonic_ns,
        "verified_at_ms": verified_at_ms,
        "verified_monotonic_ns": verified_monotonic_ns,
        "expires_at_ms": expires, **copied,
        "transport_cutoff_receipt": cutoff.reference,
        "challenge_reference": challenge.reference,
        "signed_evidence_reference": evidence.reference,
        "provider_trust_policy": trust_policy.reference,
        "provider_request_reference": provider_request.reference,
        "provider_response_reference": provider_response.reference,
        "signature_verification": _signature_proof(evidence, context),
        "nonce": challenge.document["nonce"], **from_payload,
        "active_order_count": len(payload["active_order_id_sha256s"]),
        "completed_order_count": len(payload["completed_order_id_sha256s"]),
        "execution_count": len(payload["execution_id_sha256s"]),
        "position_count": len(payload["positions"]),
        "cash_fx_exposure_count": len(payload["cash_fx_exposures"]),
        "first_host_observed_at_ms": first.observed_at_ms,
        "second_host_observed_at_ms": second.observed_at_ms,
        "host_policy_sha256": second.policy_sha256,
        "host_authorized_connectors": second.authorized_connectors,
        "host_authorized_uids": list(second.authorized_uids),
        "host_broker_socket_count": second.broker_socket_count,
        "host_broker_process_count": second.broker_process_count,
        "host_credential_exposure_count": second.credential_exposure_count,
        "host_process_inventory_complete": second.process_inventory_complete,
        "host_socket_inventory_complete": second.socket_inventory_complete,
        "host_credential_inventory_complete":
            second.credential_inventory_complete,
        "host_paper_units_inactive": second.paper_units_inactive,
        "host_kill_switch_engaged": second.kill_switch_engaged,
        "post_cutoff_boundary_verified": True,
        "egress_policy_generation_stable": True, **boundary,
    })
    validate_terminal_witness(
        witness, cutoff=cutoff, challenge=challenge,
        trust_policy=trust_policy, evidence=evidence,
        provider_request=provider_request, provider_response=provider_response,
        context=context)
    return witness


def assemble_snapshots(
    *, intent: InputBinding, handoff: InputBinding, challenge: InputBinding,
    reservation: InputBinding,
    evidence: SignedEvidence, context: ProductionContext,
    host_authority_lease: HostAuthorityLease,
    first_observation: HostObservation, second_observation: HostObservation,
    now_ms: int, certification: SignatureCertification | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _require(
        type(context) is ProductionContext and type(intent) is InputBinding and
        type(handoff) is InputBinding and type(challenge) is InputBinding and
        type(reservation) is InputBinding and
        type(evidence) is SignedEvidence,
        "ZERO_SNAPSHOT_PRODUCTION_CONTEXT_REQUIRED")
    context.reopen()
    validate_handoff(
        handoff.document, now_ms, expected_uid=context.expected_uid,
        expected_gid=context.expected_gid)
    paths = validate_intent(
        intent.document, now_ms, context,
        intent.document.get("source_baseline_sha256"),
        intent.document.get("campaign_id"))
    _require(
        paths["handoff"] == handoff.path and
        paths["challenge"] == challenge.path and
        paths["signed_evidence"] == evidence.binding.path,
        "ZERO_SNAPSHOT_OPERATOR_INTENT_INVALID")
    validate_reservation(
        reservation, now_ms, intent=intent, handoff=handoff, context=context,
        lease=host_authority_lease, paths=paths)
    validate_challenge(
        challenge.document, now_ms, intent=intent, handoff=handoff,
        reservation=reservation, context=context)
    _require(
        reservation.document["request_nonce"] == challenge.document["nonce"],
        "ZERO_SNAPSHOT_RESERVATION_CHALLENGE_MISMATCH")
    validate_signed_payload(evidence, now_ms, challenge)
    first = validate_host_observation(first_observation)
    second = validate_host_observation(second_observation)
    lease_reference = context.validate_host_authority_lease(
        host_authority_lease, reservation)
    _require(first.observed_at_ms <= second.observed_at_ms <= now_ms,
             "ZERO_SNAPSHOT_OBSERVATION_ORDER_INVALID")
    payload = evidence.payload
    expires = min(
        now_ms + MAXIMUM_OUTPUT_LIFETIME_MS,
        intent.document["expires_at_ms"], handoff.document["expires_at_ms"],
        challenge.document["expires_at_ms"], payload["expires_at_ms"])
    _require(now_ms < expires, "ZERO_SNAPSHOT_OUTPUT_WINDOW_INVALID")
    stable = _stable_boundary(first, second)
    exposure = (
        max(first.authorized_connectors, second.authorized_connectors) > 0 or
        bool(set(first.authorized_uids) | set(second.authorized_uids)) or
        max(first.broker_socket_count, second.broker_socket_count) > 0 or
        max(first.broker_process_count, second.broker_process_count) > 0 or
        max(first.credential_exposure_count,
            second.credential_exposure_count) > 0)
    safe_twice = (
        stable and _observation_safe_zero(first) and
        _observation_safe_zero(second))
    certified = context.certifies(evidence, certification)
    broker_status = "HALT" if exposure else \
        "PASS" if safe_twice and certified else "NO_GO"
    boundary = {field: False for field in BOUNDARY_FIELDS}
    common = {
        "version": VERSION, "expires_at_ms": expires, "round": ROUND,
        "domain": DOMAIN_ID, "campaign_id": intent.document["campaign_id"],
        "source_baseline_sha256":
            intent.document["source_baseline_sha256"],
        "producer": context.producer.reference, "production_mode":
            PRODUCTION_MODE, "operator_intent_reference": intent.reference,
        "watch_handoff_receipt": handoff.reference,
        "challenge_reference": challenge.reference,
        "host_authority_reservation": reservation_reference(reservation),
    }
    broker = seal({
        "schema": BROKER_SNAPSHOT_SCHEMA, "status": broker_status,
        "observed_at_ms": second.observed_at_ms, **common,
        "request_nonce": challenge.document["nonce"],
        "account_id_sha256": challenge.document["account_id_sha256"],
        "signed_account_payload_sha256": evidence.payload_sha256,
        "observation_method": BROKER_OBSERVATION_METHOD,
        "broker_policy_helper": context.broker_helper.reference,
        "observer_id": "hepta-p1-zero-exposure-local-boundary-v2",
        "observation_complete": stable,
        "broker_deny_all":
            first.authorized_connectors == second.authorized_connectors == 0 and
            not first.authorized_uids and not second.authorized_uids,
        "policy_sha256": second.policy_sha256,
        "authorized_connectors": max(
            first.authorized_connectors, second.authorized_connectors),
        "authorized_uids": sorted(
            set(first.authorized_uids) | set(second.authorized_uids)),
        "broker_socket_count": max(
            first.broker_socket_count, second.broker_socket_count),
        "broker_process_count": max(
            first.broker_process_count, second.broker_process_count),
        "credential_exposure_count": max(
            first.credential_exposure_count,
            second.credential_exposure_count),
        "paper_units_inactive":
            first.paper_units_inactive and second.paper_units_inactive,
        "kill_switch_engaged":
            first.kill_switch_engaged and second.kill_switch_engaged,
        "protected_broker_ports": list(PROTECTED_BROKER_PORTS),
        "process_inventory_complete":
            first.process_inventory_complete and
            second.process_inventory_complete,
        "socket_inventory_complete":
            first.socket_inventory_complete and second.socket_inventory_complete,
        "credential_inventory_complete":
            first.credential_inventory_complete and
            second.credential_inventory_complete,
        "host_authority_lease": lease_reference,
        **boundary,
    })
    account = seal({
        "schema": ACCOUNT_SNAPSHOT_SCHEMA,
        "status": "COMPLETE" if certified else "UNVERIFIED",
        "observed_at_ms": payload["observed_at_ms"], **common,
        "signed_evidence_reference": evidence.reference,
        "signature_verification": _signature_proof(evidence, context),
        "request_nonce": challenge.document["nonce"],
        "provider_id": payload["provider_id"],
        "account_id_sha256": payload["account_id_sha256"],
        "provider_request_id_sha256": payload["provider_request_id_sha256"],
        "provider_response_sha256": payload["provider_response_sha256"],
        "observer_id": "hepta-p1-zero-exposure-signed-adapter-v2",
        "observation_authority": REMOTE_OBSERVATION_AUTHORITY,
        "query_effect": REMOTE_QUERY_EFFECT,
        "query_epoch": payload["query_epoch"],
        "query_fencing_generation": payload["query_fencing_generation"],
        "query_invocation_id": payload["query_invocation_id"],
        "read_only_authority": certified, "authoritative": certified,
        "account_complete": certified, "snapshot_sha256":
            payload["snapshot_sha256"], "active_order_id_sha256s":
            list(payload["active_order_id_sha256s"]),
        "positions": [dict(item) for item in payload["positions"]],
        "gross_absolute_position": payload["gross_absolute_position"],
        "authorized_connector_count": payload["authorized_connector_count"],
        "end_flat": payload["end_flat"], **boundary,
    })
    _require(set(broker) == BROKER_SNAPSHOT_FIELDS and
             set(account) == ACCOUNT_SNAPSHOT_FIELDS,
             "ZERO_SNAPSHOT_OUTPUT_INVALID")
    _validate_account_state(account, "ZERO_SNAPSHOT_OUTPUT_INVALID")
    return broker, account


def _rename_noreplace(
    parent: int, source: str, destination: str, reason: str,
) -> None:
    function = getattr(LIBC, "renameat2", None)
    _require(function is not None, reason)
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
            raise ProducerError("ZERO_SNAPSHOT_OUTPUT_ALREADY_EXISTS")
        raise ProducerError(reason) from OSError(number, os.strerror(number))


def _publish_one(
    path: Path, document: dict[str, Any], *, expected_uid: int,
    expected_gid: int,
) -> None:
    reason = "ZERO_SNAPSHOT_OUTPUT_PUBLISH_FAILED"
    path = _canonical_path(path, reason)
    payload = canonical_bytes(document)
    _require(0 < len(payload) <= MAXIMUM_JSON_BYTES, reason)
    parent = _open_directory(path.parent, reason)
    parent_identity = _trusted_parent(
        parent, expected_uid=expected_uid, expected_gid=expected_gid,
        reason=reason)
    temporary = "." + path.name + ".zero-snapshot-" + secrets.token_hex(16)
    descriptor = -1
    renamed = False
    try:
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ProducerError("ZERO_SNAPSHOT_OUTPUT_ALREADY_EXISTS")
        descriptor = os.open(temporary, CREATE_FLAGS, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, expected_uid, expected_gid)
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            _require(count > 0, reason)
            offset += count
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == expected_uid and
            metadata.st_gid == expected_gid and
            stat.S_IMODE(metadata.st_mode) == 0o600 and
            metadata.st_size == len(payload), reason)
        os.fsync(parent)
        _require(parent_identity == _trusted_parent(
            parent, expected_uid=expected_uid, expected_gid=expected_gid,
            reason=reason), reason)
        _rename_noreplace(parent, temporary, path.name, reason)
        renamed = True
        os.fsync(parent)
    except OSError as error:
        raise ProducerError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not renamed:
            try:
                os.unlink(temporary, dir_fd=parent)
                os.fsync(parent)
            except OSError:
                pass
        os.close(parent)
    committed, _, _ = secure_read(
        path, "ZERO_SNAPSHOT_OUTPUT_POST_VERIFY_FAILED",
        expected_uid=expected_uid, expected_gid=expected_gid)
    _require(committed == payload and
             strict_object(
                 committed, "ZERO_SNAPSHOT_OUTPUT_POST_VERIFY_FAILED") ==
                document, "ZERO_SNAPSHOT_OUTPUT_POST_VERIFY_FAILED")


def _replace_one(
    path: Path, document: dict[str, Any], expected: InputBinding, *,
    expected_uid: int, expected_gid: int,
) -> None:
    """Durably replace only the exact securely bound current pointer."""

    reason = "ZERO_SNAPSHOT_POINTER_REPLACE_FAILED"
    path = _canonical_path(path, reason)
    _require(expected.path == path, reason)
    expected.reopen(
        expected_uid=expected_uid, expected_gid=expected_gid, reason=reason)
    payload = canonical_bytes(document)
    _require(0 < len(payload) <= MAXIMUM_JSON_BYTES, reason)
    parent = _open_directory(path.parent, reason)
    parent_identity = _trusted_parent(
        parent, expected_uid=expected_uid, expected_gid=expected_gid,
        reason=reason)
    temporary = "." + path.name + ".zero-pointer-" + secrets.token_hex(16)
    descriptor = -1
    renamed = False
    try:
        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _require(_identity(named) == expected.metadata_identity, reason)
        descriptor = os.open(temporary, CREATE_FLAGS, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, expected_uid, expected_gid)
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            _require(count > 0, reason)
            offset += count
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == expected_uid and
            metadata.st_gid == expected_gid and
            stat.S_IMODE(metadata.st_mode) == 0o600 and
            metadata.st_size == len(payload), reason)
        os.fsync(parent)
        _require(
            parent_identity == _trusted_parent(
                parent, expected_uid=expected_uid,
                expected_gid=expected_gid, reason=reason) and
            _identity(os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)) ==
                expected.metadata_identity,
            reason)
        os.replace(
            temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        renamed = True
        os.fsync(parent)
    except (OSError, ProducerError) as error:
        if isinstance(error, ProducerError):
            raise
        raise ProducerError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not renamed:
            try:
                os.unlink(temporary, dir_fd=parent)
                os.fsync(parent)
            except OSError:
                pass
        os.close(parent)
    committed, _, _ = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid)
    _require(
        committed == payload and strict_object(committed, reason) == document,
        reason)


def _reopen_published_output(
    path: Path, document: dict[str, Any], *, expected_uid: int,
    expected_gid: int,
) -> None:
    reason = "ZERO_SNAPSHOT_OUTPUT_POST_VERIFY_FAILED"
    payload, _, _ = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid)
    _require(
        payload == canonical_bytes(document) and
        strict_object(payload, reason) == document,
        reason)


def _validate_current_pointer_for_finalization(
    pointer: InputBinding, *, reservation: InputBinding,
    tombstone: InputBinding, lease: HostAuthorityLease,
) -> None:
    reason = "ZERO_SNAPSHOT_RESERVATION_POINTER_INVALID"
    document = pointer.document
    active = reservation.document
    _sealed(
        document, RESERVATION_CURRENT_POINTER_FIELDS,
        RESERVATION_CURRENT_POINTER_SCHEMA, reason)
    _require(
        pointer.path == reservation_current_pointer_path() and
        document.get("status") == "CURRENT" and
        document.get("round") == ROUND and
        document.get("domain") == DOMAIN_ID and
        document.get("campaign_id") == active["campaign_id"] and
        document.get("source_baseline_sha256") ==
            active["source_baseline_sha256"] and
        document.get("boot_id") == active["boot_id"] == lease.boot_id and
        document.get("reservation_id") == active["reservation_id"] and
        document.get("reservation_generation") ==
            active["reservation_generation"] and
        document.get("predecessor_finalization_body_sha256") ==
            active["predecessor_finalization_body_sha256"] and
        document.get("finalization_tombstone_reference") ==
            tombstone.reference and
        document.get("host_authority_lease") == lease.reference,
        reason)
    _false_boundary(document, reason)
    pointer.reopen(
        expected_uid=lease.expected_uid, expected_gid=lease.expected_gid,
        reason=reason)


def _commit_finalization_pointer(
    *, reservation: InputBinding, tombstone: InputBinding,
    lease: HostAuthorityLease, now_ms: int,
) -> InputBinding:
    """Advance the current pointer once, or resume its durable commit."""

    reason = "ZERO_SNAPSHOT_RESERVATION_POINTER_INVALID"
    context_path = reservation_current_pointer_path()
    active = reservation.document
    prior_reference = active["prior_finalization_pointer_reference"]
    existing: InputBinding | None
    if _named_path_absent(
            context_path, expected_uid=lease.expected_uid,
            expected_gid=lease.expected_gid, reason=reason):
        existing = None
    else:
        existing = _bind_document(
            context_path, RESERVATION_CURRENT_POINTER_FIELDS,
            RESERVATION_CURRENT_POINTER_SCHEMA, reason,
            expected_uid=lease.expected_uid, expected_gid=lease.expected_gid)
    if (existing is not None and
            existing.document.get("reservation_generation") ==
                active["reservation_generation"]):
        _validate_current_pointer_for_finalization(
            existing, reservation=reservation, tombstone=tombstone,
            lease=lease)
        return existing
    document = build_finalization_current_pointer(
        reservation=reservation, tombstone=tombstone, lease=lease,
        now_ms=now_ms)
    if prior_reference is None:
        _require(existing is None, reason)
        _publish_one(
            context_path, document, expected_uid=lease.expected_uid,
            expected_gid=lease.expected_gid)
    else:
        _require(existing is not None and
                 existing.reference == prior_reference, reason)
        _replace_one(
            context_path, document, existing,
            expected_uid=lease.expected_uid,
            expected_gid=lease.expected_gid)
    pointer = _bind_document(
        context_path, RESERVATION_CURRENT_POINTER_FIELDS,
        RESERVATION_CURRENT_POINTER_SCHEMA, reason,
        expected_uid=lease.expected_uid, expected_gid=lease.expected_gid)
    _validate_current_pointer_for_finalization(
        pointer, reservation=reservation, tombstone=tombstone, lease=lease)
    return pointer


def _reopen_inputs(
    inputs: Sequence[InputBinding], context: ProductionContext,
) -> None:
    for binding in inputs:
        binding.reopen(
            expected_uid=context.expected_uid,
            expected_gid=context.expected_gid)
    context.reopen()


def _named_path_absent(
    path: Path, *, expected_uid: int, expected_gid: int, reason: str,
) -> bool:
    path = _canonical_path(path, reason)
    parent = _open_directory(path.parent, reason)
    try:
        parent_identity = _trusted_parent(
            parent, expected_uid=expected_uid, expected_gid=expected_gid,
            reason=reason)
        try:
            metadata = os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return True
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == expected_uid and metadata.st_gid == expected_gid
            and stat.S_IMODE(metadata.st_mode) == 0o600 and
            parent_identity == _trusted_parent(
                parent, expected_uid=expected_uid,
                expected_gid=expected_gid, reason=reason), reason)
        return False
    except (OSError, ProducerError) as error:
        if isinstance(error, ProducerError):
            raise
        raise ProducerError(reason) from error
    finally:
        os.close(parent)


def issue_challenge_and_publish(
    *, operator_intent_path: Path, handoff_path: Path,
    challenge_output_path: Path, expected_source: str,
    expected_campaign: str, production_mode: str | None,
    expected_uid: int = ROOT_UID, expected_gid: int = ROOT_GID,
    now_ms: int | None = None, _run_token: object | None = None,
) -> dict[str, Any]:
    _require(_run_token is CLI_RUN_TOKEN,
             "ZERO_SNAPSHOT_CLI_RUN_REQUIRED")
    _require(production_mode == PRODUCTION_MODE,
             "ZERO_SNAPSHOT_EXPLICIT_PRODUCTION_INTENT_REQUIRED")
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    _require(type(now) is int and now >= 0, "ZERO_SNAPSHOT_TIME_INVALID")
    context = ProductionContext(
        expected_uid=expected_uid, expected_gid=expected_gid)
    intent, handoff, paths = _load_intent_and_handoff(
        operator_intent_path=operator_intent_path, handoff_path=handoff_path,
        context=context, expected_source=expected_source,
        expected_campaign=expected_campaign, now_ms=now)
    challenge_output = _canonical_path(
        challenge_output_path, "ZERO_SNAPSHOT_PATH_INVALID")
    _require(challenge_output == paths["challenge"],
             "ZERO_SNAPSHOT_OPERATOR_INTENT_INVALID")
    lease = context.acquire_host_authority_lease()
    reservation: InputBinding | None = None
    try:
        _reopen_inputs((intent, handoff), context)
        context.validate_host_authority_lease(lease)
        nonce = secrets.token_hex(32)
        reservation_document = build_reservation(
            intent=intent, handoff=handoff, context=context, lease=lease,
            paths=paths, now_ms=now, nonce=nonce,
            reservation_id="zero-exposure-" + secrets.token_hex(24))
        # Commit the global authority owner before a challenge can escape and
        # cause a remote account query.  Any crash from here is fail-closed:
        # the durable no-replace marker blocks a second owner or replay.
        _publish_one(
            HOST_AUTHORITY_OWNER_PATH, reservation_document,
            expected_uid=expected_uid, expected_gid=expected_gid)
        reservation = _bind_document(
            HOST_AUTHORITY_OWNER_PATH, RESERVATION_FIELDS, RESERVATION_SCHEMA,
            "ZERO_SNAPSHOT_RESERVATION_INVALID", expected_uid=expected_uid,
            expected_gid=expected_gid)
        validate_reservation(
            reservation, now, intent=intent, handoff=handoff, context=context,
            lease=lease, paths=paths)
        challenge = build_challenge(
            intent=intent, handoff=handoff, reservation=reservation,
            context=context, now_ms=now, nonce=nonce)
        _reopen_inputs((intent, handoff, reservation), context)
        context.validate_host_authority_lease(lease, reservation)
        _publish_one(
            challenge_output, challenge, expected_uid=expected_uid,
            expected_gid=expected_gid)
        _reopen_published_output(
            HOST_AUTHORITY_OWNER_PATH, reservation_document,
            expected_uid=expected_uid, expected_gid=expected_gid)
        _reopen_published_output(
            challenge_output, challenge, expected_uid=expected_uid,
            expected_gid=expected_gid)
        validate_reservation(
            reservation, now, intent=intent, handoff=handoff, context=context,
            lease=lease, paths=paths)
        context.reopen()
        return challenge
    finally:
        if reservation is None or sys.exc_info()[0] is not None:
            _close_host_authority_lease_fail_closed(lease)
        else:
            context.release_host_authority_lease(lease, reservation)


def issue_terminal_challenge_and_publish(
    *, transport_cutoff_path: Path, provider_trust_policy_path: Path,
    challenge_output_path: Path, expected_source: str,
    expected_campaign: str, expected_cycle: str, expected_recovery: str,
    expected_finalization: str, production_mode: str | None,
    expected_uid: int = ROOT_UID, expected_gid: int = ROOT_GID,
    now_ms: int | None = None, now_monotonic_ns: int | None = None,
    _run_token: object | None = None,
) -> dict[str, Any]:
    _require(_run_token is CLI_RUN_TOKEN,
             "TERMINAL_WITNESS_CLI_RUN_REQUIRED")
    _require(production_mode == TERMINAL_PRODUCTION_MODE,
             "TERMINAL_WITNESS_EXPLICIT_PRODUCTION_INTENT_REQUIRED")
    expected_source = _digest(
        expected_source, "TERMINAL_WITNESS_EXPECTED_SOURCE_INVALID",
        nonzero=True)
    for value in (expected_campaign, expected_cycle, expected_recovery,
                  expected_finalization):
        _identifier(value, IDENTIFIER, "TERMINAL_WITNESS_IDENTITY_INVALID")
    now = _wall_clock_ms() if now_ms is None else now_ms
    monotonic = time.monotonic_ns() if now_monotonic_ns is None else \
        now_monotonic_ns
    _integer(now, "TERMINAL_WITNESS_TIME_INVALID")
    _integer(monotonic, "TERMINAL_WITNESS_TIME_INVALID")
    context = ProductionContext(
        expected_uid=expected_uid, expected_gid=expected_gid)
    cutoff = _bind_document(
        _canonical_path(transport_cutoff_path, "TERMINAL_WITNESS_PATH_INVALID"),
        TRANSPORT_CUTOFF_FIELDS, TRANSPORT_CUTOFF_SCHEMA,
        "TERMINAL_WITNESS_TRANSPORT_CUTOFF_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid)
    trust_policy = _bind_document(
        _canonical_path(
            provider_trust_policy_path, "TERMINAL_WITNESS_PATH_INVALID"),
        TERMINAL_PROVIDER_TRUST_POLICY_FIELDS,
        TERMINAL_PROVIDER_TRUST_POLICY_SCHEMA,
        "TERMINAL_WITNESS_PROVIDER_TRUST_POLICY_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid)
    challenge_output = _canonical_path(
        challenge_output_path, "TERMINAL_WITNESS_PATH_INVALID")
    _require(
        len({cutoff.path, trust_policy.path, challenge_output,
             HOST_AUTHORITY_OWNER_PATH}) == 4,
        "TERMINAL_WITNESS_PATH_ALIAS")
    lease = context.acquire_host_authority_lease(
        allow_reservation_owner=True)
    owner: InputBinding | None = None
    try:
        validate_transport_cutoff(
            cutoff.document, now_ms=now, now_monotonic_ns=monotonic,
            expected_source=expected_source,
            expected_campaign=expected_campaign, expected_cycle=expected_cycle,
            expected_recovery=expected_recovery,
            expected_finalization=expected_finalization,
            expected_boot_id=lease.boot_id)
        validate_terminal_provider_trust_policy(
            trust_policy.document,
            verification_key_sha256=context.verification_key.reference[
                "file_sha256"])
        cutoff_owner: InputBinding | None = None
        try:
            os.stat(
                HOST_AUTHORITY_OWNER_PATH.name,
                dir_fd=lease.directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            # A transport cutoff without its exact owner marker is an
            # incomplete/crashed handoff.  Never reconstruct authority from
            # the cutoff artifact alone: doing so would reopen an owner-absent
            # window and erase the no-gap lineage established by the root
            # cutoff recorder.
            raise ProducerError("TERMINAL_WITNESS_CUTOFF_OWNER_REQUIRED")
        except OSError as error:
            raise ProducerError("TERMINAL_WITNESS_OWNER_INVALID") from error
        else:
            # The cutoff recorder establishes this exact fail-closed owner
            # before it publishes the cutoff.  Replace it atomically with the
            # challenge while the same host lease remains held; there is never
            # an owner-absent authorization window.
            try:
                cutoff_owner = _bind_document(
                    HOST_AUTHORITY_OWNER_PATH, TERMINAL_CUTOFF_OWNER_FIELDS,
                    TERMINAL_CUTOFF_OWNER_SCHEMA,
                    "TERMINAL_WITNESS_CUTOFF_OWNER_INVALID",
                    expected_uid=expected_uid, expected_gid=expected_gid)
            except ProducerError:
                cutoff_owner = None
            if cutoff_owner is not None:
                marker = cutoff_owner.document
                _require(
                    marker.get("status") ==
                        "CUTOFF_HELD_FOR_TERMINAL_CHALLENGE" and
                    marker.get("boot_id") == lease.boot_id ==
                        cutoff.document["boot_id"] and
                    marker.get("campaign_id") == expected_campaign and
                    marker.get("cycle_id") == expected_cycle and
                    marker.get("recovery_id") == expected_recovery and
                    marker.get("finalization_id") == expected_finalization and
                    marker.get("transport_cutoff_document") ==
                        cutoff.document and
                    marker.get("transport_cutoff_file_sha256") ==
                        cutoff.reference["file_sha256"] and
                    marker.get("transport_cutoff_body_sha256") ==
                        cutoff.reference["body_sha256"] and
                    marker.get("next_consumer") ==
                        "TERMINAL_ACCOUNT_CHALLENGE",
                    "TERMINAL_WITNESS_CUTOFF_OWNER_INVALID")
                _false_boundary(
                    marker, "TERMINAL_WITNESS_CUTOFF_OWNER_INVALID")
                challenge = build_terminal_challenge(
                    cutoff=cutoff, trust_policy=trust_policy,
                    context=context, lease=lease, now_ms=now,
                    now_monotonic_ns=monotonic,
                    nonce=secrets.token_hex(32))
                _replace_one(
                    HOST_AUTHORITY_OWNER_PATH, challenge, cutoff_owner,
                    expected_uid=expected_uid, expected_gid=expected_gid)
        owner = _bind_document(
            HOST_AUTHORITY_OWNER_PATH, TERMINAL_CHALLENGE_FIELDS,
            TERMINAL_CHALLENGE_SCHEMA, "TERMINAL_WITNESS_OWNER_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid)
        validate_terminal_challenge(
            owner.document, now_ms=now, now_monotonic_ns=monotonic,
            cutoff=cutoff, trust_policy=trust_policy, context=context,
            boot_id=lease.boot_id)
        try:
            published = _bind_document(
                challenge_output, TERMINAL_CHALLENGE_FIELDS,
                TERMINAL_CHALLENGE_SCHEMA, "TERMINAL_WITNESS_CHALLENGE_INVALID",
                expected_uid=expected_uid, expected_gid=expected_gid)
            _require(
                published.document == owner.document,
                "TERMINAL_WITNESS_CHALLENGE_INVALID")
        except ProducerError as error:
            if challenge_output.exists():
                raise
            _publish_one(
                challenge_output, owner.document,
                expected_uid=expected_uid, expected_gid=expected_gid)
            published = _bind_document(
                challenge_output, TERMINAL_CHALLENGE_FIELDS,
                TERMINAL_CHALLENGE_SCHEMA, "TERMINAL_WITNESS_CHALLENGE_INVALID",
                expected_uid=expected_uid, expected_gid=expected_gid)
        _require(published.document == owner.document,
                 "TERMINAL_WITNESS_CHALLENGE_INVALID")
        for binding in (cutoff, trust_policy, owner, published):
            binding.reopen(
                expected_uid=expected_uid, expected_gid=expected_gid,
                reason="TERMINAL_WITNESS_INPUT_DRIFT")
        context.validate_host_authority_lease(lease, owner)
        return published.document
    finally:
        if owner is None or sys.exc_info()[0] is not None:
            _close_host_authority_lease_fail_closed(lease)
        else:
            context.release_host_authority_lease(lease, owner)


def consume_terminal_response_and_publish(
    *, transport_cutoff_path: Path, provider_trust_policy_path: Path,
    challenge_path: Path, signed_evidence_path: Path,
    provider_request_path: Path, provider_response_path: Path,
    witness_output_path: Path, expected_source: str,
    expected_campaign: str, expected_cycle: str, expected_recovery: str,
    expected_finalization: str, production_mode: str | None,
    expected_uid: int = ROOT_UID, expected_gid: int = ROOT_GID,
    _run_token: object | None = None,
) -> dict[str, Any]:
    _require(_run_token is CLI_RUN_TOKEN,
             "TERMINAL_WITNESS_CLI_RUN_REQUIRED")
    _require(production_mode == TERMINAL_PRODUCTION_MODE,
             "TERMINAL_WITNESS_EXPLICIT_PRODUCTION_INTENT_REQUIRED")
    expected_source = _digest(
        expected_source, "TERMINAL_WITNESS_EXPECTED_SOURCE_INVALID",
        nonzero=True)
    for value in (expected_campaign, expected_cycle, expected_recovery,
                  expected_finalization):
        _identifier(value, IDENTIFIER, "TERMINAL_WITNESS_IDENTITY_INVALID")
    context = ProductionContext(
        expected_uid=expected_uid, expected_gid=expected_gid)
    cutoff = _bind_document(
        transport_cutoff_path, TRANSPORT_CUTOFF_FIELDS, TRANSPORT_CUTOFF_SCHEMA,
        "TERMINAL_WITNESS_TRANSPORT_CUTOFF_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid)
    trust_policy = _bind_document(
        provider_trust_policy_path, TERMINAL_PROVIDER_TRUST_POLICY_FIELDS,
        TERMINAL_PROVIDER_TRUST_POLICY_SCHEMA,
        "TERMINAL_WITNESS_PROVIDER_TRUST_POLICY_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid)
    challenge = _bind_document(
        challenge_path, TERMINAL_CHALLENGE_FIELDS, TERMINAL_CHALLENGE_SCHEMA,
        "TERMINAL_WITNESS_CHALLENGE_INVALID", expected_uid=expected_uid,
        expected_gid=expected_gid)
    evidence_binding = _bind_unsealed_document(
        signed_evidence_path,
        "TERMINAL_WITNESS_SIGNED_ACCOUNT_EVIDENCE_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid)
    evidence = parse_terminal_signed_evidence(evidence_binding)
    provider_request = _bind_terminal_provider_artifact(
        provider_request_path, "TERMINAL_WITNESS_PROVIDER_REQUEST_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid)
    provider_response = _bind_terminal_provider_artifact(
        provider_response_path, "TERMINAL_WITNESS_PROVIDER_RESPONSE_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid)
    output = _canonical_path(
        witness_output_path, "TERMINAL_WITNESS_PATH_INVALID")
    input_paths = {
        cutoff.path, trust_policy.path, challenge.path, evidence_binding.path,
        provider_request.path, provider_response.path, HOST_AUTHORITY_OWNER_PATH,
    }
    _require(len(input_paths) == 7 and output not in input_paths,
             "TERMINAL_WITNESS_PATH_ALIAS")
    lease = context.acquire_host_authority_lease(
        allow_reservation_owner=True)
    owner: InputBinding | None = None
    try:
        owner = _bind_document(
            HOST_AUTHORITY_OWNER_PATH, TERMINAL_CHALLENGE_FIELDS,
            TERMINAL_CHALLENGE_SCHEMA, "TERMINAL_WITNESS_OWNER_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid)
        _require(owner.document == challenge.document,
                 "TERMINAL_WITNESS_OWNER_INVALID")
        received = _wall_clock_ms()
        received_monotonic = time.monotonic_ns()
        now = received
        monotonic = received_monotonic
        validate_transport_cutoff(
            cutoff.document, now_ms=now, now_monotonic_ns=monotonic,
            expected_source=expected_source,
            expected_campaign=expected_campaign, expected_cycle=expected_cycle,
            expected_recovery=expected_recovery,
            expected_finalization=expected_finalization,
            expected_boot_id=lease.boot_id)
        validate_terminal_provider_trust_policy(
            trust_policy.document,
            verification_key_sha256=context.verification_key.reference[
                "file_sha256"])
        validate_terminal_challenge(
            challenge.document, now_ms=now, now_monotonic_ns=monotonic,
            cutoff=cutoff, trust_policy=trust_policy, context=context,
            boot_id=lease.boot_id)
        validate_terminal_signed_payload(
            evidence, now_ms=now, now_monotonic_ns=monotonic,
            challenge=challenge, cutoff=cutoff, trust_policy=trust_policy)
        _require(
            evidence.payload["provider_request_sha256"] ==
                provider_request.reference["file_sha256"] and
            evidence.payload["provider_response_sha256"] ==
                provider_response.reference["file_sha256"],
            "TERMINAL_WITNESS_PROVIDER_ARTIFACT_MISMATCH")
        certification = context.verify_signature(evidence)
        existing: InputBinding | None = None
        try:
            os.lstat(output)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ProducerError("TERMINAL_WITNESS_OUTPUT_INVALID") from error
        else:
            existing = _bind_document(
                output, TERMINAL_WITNESS_FIELDS, TERMINAL_WITNESS_SCHEMA,
                "TERMINAL_WITNESS_OUTPUT_INVALID", expected_uid=expected_uid,
                expected_gid=expected_gid)
            validate_terminal_witness(
                existing.document, cutoff=cutoff, challenge=challenge,
                trust_policy=trust_policy, evidence=evidence,
                provider_request=provider_request,
                provider_response=provider_response, context=context)
            _require(
                existing.document["verified_at_ms"] <= now <
                    existing.document["expires_at_ms"] and
                existing.document["verified_monotonic_ns"] <= monotonic,
                "TERMINAL_WITNESS_OUTPUT_STALE")
        inputs = (cutoff, trust_policy, owner, challenge, evidence_binding)
        for binding in inputs:
            binding.reopen(
                expected_uid=expected_uid, expected_gid=expected_gid,
                reason="TERMINAL_WITNESS_INPUT_DRIFT")
        provider_request.reopen()
        provider_response.reopen()
        context.validate_host_authority_lease(lease, owner)
        observer = ProductionReadOnlyObserver(context)
        first = observer.observe()
        middle = _wall_clock_ms()
        middle_monotonic = time.monotonic_ns()
        validate_terminal_signed_payload(
            evidence, now_ms=middle, now_monotonic_ns=middle_monotonic,
            challenge=challenge, cutoff=cutoff, trust_policy=trust_policy)
        provider_request.reopen()
        provider_response.reopen()
        second = observer.observe()
        verified = _wall_clock_ms()
        verified_monotonic = time.monotonic_ns()
        validate_terminal_signed_payload(
            evidence, now_ms=verified,
            now_monotonic_ns=verified_monotonic, challenge=challenge,
            cutoff=cutoff, trust_policy=trust_policy)
        if existing is not None:
            first = validate_host_observation(first)
            second = validate_host_observation(second)
            _require(
                challenge.document["issued_at_ms"] <=
                    first.observed_at_ms <= second.observed_at_ms <= verified and
                _stable_boundary(first, second) and
                _observation_safe_zero(first) and
                _observation_safe_zero(second) and
                first.egress_policy_generation ==
                    cutoff.document["egress_policy_generation"] and
                second.egress_policy_generation ==
                    cutoff.document["egress_policy_generation"] and
                first.policy_sha256 == second.policy_sha256 ==
                    cutoff.document["egress_policy_sha256"],
                "TERMINAL_WITNESS_REPLAY_BOUNDARY_INVALID")
            for binding in inputs:
                binding.reopen(
                    expected_uid=expected_uid, expected_gid=expected_gid,
                    reason="TERMINAL_WITNESS_INPUT_DRIFT")
            provider_request.reopen()
            provider_response.reopen()
            existing.reopen(
                expected_uid=expected_uid, expected_gid=expected_gid,
                reason="TERMINAL_WITNESS_OUTPUT_DRIFT")
            context.validate_host_authority_lease(lease, owner)
            return existing.document
        witness = assemble_terminal_witness(
            cutoff=cutoff, challenge=challenge, trust_policy=trust_policy,
            evidence=evidence, provider_request=provider_request,
            provider_response=provider_response, context=context,
            certification=certification, first_observation=first,
            second_observation=second, received_at_ms=received,
            received_monotonic_ns=received_monotonic,
            verified_at_ms=verified,
            verified_monotonic_ns=verified_monotonic)
        for binding in inputs:
            binding.reopen(
                expected_uid=expected_uid, expected_gid=expected_gid,
                reason="TERMINAL_WITNESS_INPUT_DRIFT")
        provider_request.reopen()
        provider_response.reopen()
        context.validate_host_authority_lease(lease, owner)
        _publish_one(
            output, witness, expected_uid=expected_uid,
            expected_gid=expected_gid)
        committed = _bind_document(
            output, TERMINAL_WITNESS_FIELDS, TERMINAL_WITNESS_SCHEMA,
            "TERMINAL_WITNESS_OUTPUT_INVALID", expected_uid=expected_uid,
            expected_gid=expected_gid)
        validate_terminal_witness(
            committed.document, cutoff=cutoff, challenge=challenge,
            trust_policy=trust_policy, evidence=evidence,
            provider_request=provider_request,
            provider_response=provider_response, context=context)
        context.validate_host_authority_lease(lease, owner)
        return committed.document
    finally:
        if owner is None or sys.exc_info()[0] is not None:
            _close_host_authority_lease_fail_closed(lease)
        else:
            context.release_host_authority_lease(lease, owner)


def consume_response_and_publish(
    *, operator_intent_path: Path, handoff_path: Path, challenge_path: Path,
    signed_evidence_path: Path, broker_output_path: Path,
    account_output_path: Path, expected_source: str,
    expected_campaign: str, production_mode: str | None,
    expected_uid: int = ROOT_UID, expected_gid: int = ROOT_GID,
    _run_token: object | None = None,
) -> SnapshotPair:
    _require(_run_token is CLI_RUN_TOKEN,
             "ZERO_SNAPSHOT_CLI_RUN_REQUIRED")
    _require(production_mode == PRODUCTION_MODE,
             "ZERO_SNAPSHOT_EXPLICIT_PRODUCTION_INTENT_REQUIRED")
    started = time.time_ns() // 1_000_000
    context = ProductionContext(
        expected_uid=expected_uid, expected_gid=expected_gid)
    intent, handoff, paths = _load_intent_and_handoff(
        operator_intent_path=operator_intent_path, handoff_path=handoff_path,
        context=context, expected_source=expected_source,
        expected_campaign=expected_campaign, now_ms=started)
    supplied = {
        "challenge": _canonical_path(
            challenge_path, "ZERO_SNAPSHOT_PATH_INVALID"),
        "signed_evidence": _canonical_path(
            signed_evidence_path, "ZERO_SNAPSHOT_PATH_INVALID"),
        "broker_output": _canonical_path(
            broker_output_path, "ZERO_SNAPSHOT_PATH_INVALID"),
        "account_output": _canonical_path(
            account_output_path, "ZERO_SNAPSHOT_PATH_INVALID"),
    }
    _require(all(supplied[name] == paths[name] for name in supplied),
             "ZERO_SNAPSHOT_OPERATOR_INTENT_INVALID")
    challenge = _bind_document(
        supplied["challenge"], CHALLENGE_FIELDS, CHALLENGE_SCHEMA,
        "ZERO_SNAPSHOT_CHALLENGE_INVALID", expected_uid=expected_uid,
        expected_gid=expected_gid)
    lease = context.acquire_host_authority_lease(
        allow_reservation_owner=True)
    reservation: InputBinding | None = None
    try:
        reservation = _bind_document(
            HOST_AUTHORITY_OWNER_PATH, RESERVATION_FIELDS, RESERVATION_SCHEMA,
            "ZERO_SNAPSHOT_RESERVATION_INVALID", expected_uid=expected_uid,
            expected_gid=expected_gid)
        validate_reservation(
            reservation, started, intent=intent, handoff=handoff,
            context=context, lease=lease, paths=paths)
        validate_challenge(
            challenge.document, started, intent=intent, handoff=handoff,
            reservation=reservation, context=context)
        _require(
            reservation.document["request_nonce"] ==
                challenge.document["nonce"],
            "ZERO_SNAPSHOT_RESERVATION_CHALLENGE_MISMATCH")
        context.validate_host_authority_lease(lease, reservation)
        evidence_binding = _bind_unsealed_document(
            supplied["signed_evidence"],
            "ZERO_SNAPSHOT_SIGNED_ACCOUNT_EVIDENCE_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid)
        evidence = parse_signed_evidence(evidence_binding)
        validate_signed_payload(evidence, started, challenge)
        certification = context.verify_signature(evidence)
        inputs = (intent, handoff, reservation, challenge, evidence_binding)
        _reopen_inputs(inputs, context)
        context.validate_host_authority_lease(lease, reservation)
        observer = ProductionReadOnlyObserver(context)
        first = observer.observe()
        context.validate_host_authority_lease(lease, reservation)
        _reopen_inputs(inputs, context)
        middle = time.time_ns() // 1_000_000
        validate_challenge(
            challenge.document, middle, intent=intent, handoff=handoff,
            reservation=reservation, context=context)
        validate_signed_payload(evidence, middle, challenge)
        certification = context.verify_signature(evidence)
        context.validate_host_authority_lease(lease, reservation)
        # The second all-netns inventory is deliberately the final external
        # observation before receipt assembly and no-replace publication.  The
        # host authority lease prevents a conforming PAPER activation from
        # invalidating that observation before the pair is durably committed.
        second = observer.observe()
        context.validate_host_authority_lease(lease, reservation)
        finished = time.time_ns() // 1_000_000
        validate_challenge(
            challenge.document, finished, intent=intent, handoff=handoff,
            reservation=reservation, context=context)
        validate_signed_payload(evidence, finished, challenge)
        broker, account = assemble_snapshots(
            intent=intent, handoff=handoff, challenge=challenge,
            reservation=reservation, evidence=evidence, context=context,
            host_authority_lease=lease, first_observation=first,
            second_observation=second, now_ms=finished,
            certification=certification)
        pair = SnapshotPair(broker, account, inputs, evidence, context)
        _reopen_inputs(inputs, context)
        context.validate_host_authority_lease(lease, reservation)
        # Account first.  A crash before the broker commit leaves an incomplete
        # pair that the zero-exposure attestor rejects; neither file is replaced.
        _publish_one(
            supplied["account_output"], account, expected_uid=expected_uid,
            expected_gid=expected_gid)
        context.validate_host_authority_lease(lease, reservation)
        _reopen_inputs(inputs, context)
        _publish_one(
            supplied["broker_output"], broker, expected_uid=expected_uid,
            expected_gid=expected_gid)
        context.validate_host_authority_lease(lease, reservation)
        _reopen_inputs(inputs, context)
        _reopen_published_output(
            supplied["account_output"], account, expected_uid=expected_uid,
            expected_gid=expected_gid)
        _reopen_published_output(
            supplied["broker_output"], broker, expected_uid=expected_uid,
            expected_gid=expected_gid)
        context.reopen()
        context.validate_host_authority_lease(lease, reservation)
        return pair
    finally:
        if reservation is None or sys.exc_info()[0] is not None:
            _close_host_authority_lease_fail_closed(lease)
        else:
            context.release_host_authority_lease(lease, reservation)


def recover_reservation_and_publish(
    *, expected_source: str, expected_campaign: str,
    production_mode: str | None, expected_uid: int = ROOT_UID,
    expected_gid: int = ROOT_GID, now_ms: int | None = None,
    _run_token: object | None = None,
) -> dict[str, Any]:
    """Abort only a never-published or expired reservation, fail-closed."""

    _require(_run_token is CLI_RUN_TOKEN,
             "ZERO_SNAPSHOT_CLI_RUN_REQUIRED")
    _require(production_mode == PRODUCTION_MODE,
             "ZERO_SNAPSHOT_EXPLICIT_PRODUCTION_INTENT_REQUIRED")
    _digest(expected_source, "ZERO_SNAPSHOT_EXPECTED_SOURCE_INVALID",
            nonzero=True)
    _identifier(expected_campaign, IDENTIFIER,
                "ZERO_SNAPSHOT_EXPECTED_CAMPAIGN_INVALID")
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    _require(type(now) is int and now >= 0, "ZERO_SNAPSHOT_TIME_INVALID")
    context = ProductionContext(
        expected_uid=expected_uid, expected_gid=expected_gid)
    lease = context.acquire_host_authority_lease(
        allow_reservation_owner=True)
    reservation: InputBinding | None = None
    tombstone: InputBinding | None = None
    pointer: InputBinding | None = None
    owner_removed = False
    try:
        reservation = _bind_document(
            HOST_AUTHORITY_OWNER_PATH, RESERVATION_FIELDS, RESERVATION_SCHEMA,
            "ZERO_SNAPSHOT_RESERVATION_RECOVERY_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid)
        validate_reservation_for_recovery(
            reservation, now, context=context, lease=lease,
            expected_source=expected_source,
            expected_campaign=expected_campaign)
        challenge_path = _canonical_path(
            Path(reservation.document["challenge_output_path"]),
            "ZERO_SNAPSHOT_RESERVATION_RECOVERY_INVALID")
        challenge_absent = _named_path_absent(
            challenge_path, expected_uid=expected_uid,
            expected_gid=expected_gid,
            reason="ZERO_SNAPSHOT_RESERVATION_RECOVERY_INVALID")
        if challenge_absent:
            recovery_reason = "CHALLENGE_NOT_PUBLISHED"
        else:
            _require(
                now >= reservation.document["expires_at_ms"],
                "ZERO_SNAPSHOT_RESERVATION_RECOVERY_NOT_PERMITTED")
            challenge = _bind_document(
                challenge_path, CHALLENGE_FIELDS, CHALLENGE_SCHEMA,
                "ZERO_SNAPSHOT_RESERVATION_RECOVERY_INVALID",
                expected_uid=expected_uid, expected_gid=expected_gid)
            _require(
                challenge.document.get("host_authority_reservation") ==
                    reservation_reference(reservation) and
                challenge.document.get("nonce") ==
                    reservation.document["request_nonce"] and
                challenge.document.get("campaign_id") == expected_campaign and
                challenge.document.get("source_baseline_sha256") ==
                    expected_source,
                "ZERO_SNAPSHOT_RESERVATION_RECOVERY_INVALID")
            recovery_reason = "RESERVATION_EXPIRED"
        tombstone_path = reservation_tombstone_path(
            reservation.document["reservation_id"])
        if _named_path_absent(
                tombstone_path, expected_uid=expected_uid,
                expected_gid=expected_gid,
                reason="ZERO_SNAPSHOT_RESERVATION_RECOVERY_INVALID"):
            context.validate_host_authority_lease(lease, reservation)
            observer = ProductionReadOnlyObserver(context)
            first = observer.observe()
            context.validate_host_authority_lease(lease, reservation)
            second = observer.observe()
            context.validate_host_authority_lease(lease, reservation)
            observation = _recovery_observation(first, second)
            tombstone_document = build_reservation_finalization(
                reservation=reservation, lease=lease, now_ms=now,
                status="ABORTED", candidate_reference=None,
                zero_exposure_receipt_reference=None,
                recovery_reason=recovery_reason,
                recovery_observation=observation)
            _publish_one(
                tombstone_path, tombstone_document, expected_uid=expected_uid,
                expected_gid=expected_gid)
            tombstone = _bind_document(
                tombstone_path, RESERVATION_FINALIZATION_FIELDS,
                RESERVATION_FINALIZATION_SCHEMA,
                "ZERO_SNAPSHOT_RESERVATION_FINALIZATION_INVALID",
                expected_uid=expected_uid, expected_gid=expected_gid)
        else:
            tombstone = _bind_document(
                tombstone_path, RESERVATION_FINALIZATION_FIELDS,
                RESERVATION_FINALIZATION_SCHEMA,
                "ZERO_SNAPSHOT_RESERVATION_FINALIZATION_INVALID",
                expected_uid=expected_uid, expected_gid=expected_gid)
            _require(
                tombstone.document.get("status") == "ABORTED" and
                tombstone.document.get("recovery_reason") == recovery_reason,
                "ZERO_SNAPSHOT_RESERVATION_RECOVERY_INVALID")
            tombstone_document = tombstone.document
        validate_reservation_finalization(
            tombstone, reservation=reservation, lease=lease)
        context.validate_host_authority_lease(lease, reservation)
        pointer = _commit_finalization_pointer(
            reservation=reservation, tombstone=tombstone, lease=lease,
            now_ms=now)
        _remove_reservation_after_finalization(
            lease, reservation, tombstone, pointer)
        owner_removed = True
        _reopen_published_output(
            tombstone_path, tombstone_document, expected_uid=expected_uid,
            expected_gid=expected_gid)
        _validate_finalized_reservation_state(lease, tombstone, pointer)
        return tombstone_document
    finally:
        if (owner_removed and tombstone is not None and
                pointer is not None and sys.exc_info()[0] is None):
            _release_finalized_host_authority_lease(
                lease, tombstone, pointer)
        else:
            _close_host_authority_lease_fail_closed(lease)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--issue-challenge", action="store_true")
    operation.add_argument("--consume-response", action="store_true")
    operation.add_argument("--recover-reservation", action="store_true")
    operation.add_argument("--issue-terminal-challenge", action="store_true")
    operation.add_argument("--consume-terminal-response", action="store_true")
    parser.add_argument("--operator-intent", type=Path)
    parser.add_argument("--watch-handoff-receipt", type=Path)
    parser.add_argument("--challenge", type=Path)
    parser.add_argument("--signed-account-evidence", type=Path)
    parser.add_argument("--broker-output", type=Path)
    parser.add_argument("--account-output", type=Path)
    parser.add_argument("--transport-cutoff-receipt", type=Path)
    parser.add_argument("--provider-trust-policy", type=Path)
    parser.add_argument("--provider-request", type=Path)
    parser.add_argument("--provider-response", type=Path)
    parser.add_argument("--terminal-witness-output", type=Path)
    parser.add_argument("--expected-cycle-id")
    parser.add_argument("--expected-recovery-id")
    parser.add_argument("--expected-finalization-id")
    parser.add_argument("--expected-source-baseline-sha256", required=True)
    parser.add_argument("--expected-campaign-id", required=True)
    parser.add_argument(
        "--production-mode", choices=(PRODUCTION_MODE, TERMINAL_PRODUCTION_MODE),
        required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parsed = _parser().parse_args(argv)
    try:
        if parsed.issue_terminal_challenge:
            _require(
                parsed.transport_cutoff_receipt is not None and
                parsed.provider_trust_policy is not None and
                parsed.challenge is not None and
                parsed.expected_cycle_id is not None and
                parsed.expected_recovery_id is not None and
                parsed.expected_finalization_id is not None and
                parsed.signed_account_evidence is None and
                parsed.provider_request is None and
                parsed.provider_response is None and
                parsed.terminal_witness_output is None,
                "TERMINAL_WITNESS_CLI_ARGUMENT_INVALID")
            challenge = issue_terminal_challenge_and_publish(
                transport_cutoff_path=parsed.transport_cutoff_receipt,
                provider_trust_policy_path=parsed.provider_trust_policy,
                challenge_output_path=parsed.challenge,
                expected_source=parsed.expected_source_baseline_sha256,
                expected_campaign=parsed.expected_campaign_id,
                expected_cycle=parsed.expected_cycle_id,
                expected_recovery=parsed.expected_recovery_id,
                expected_finalization=parsed.expected_finalization_id,
                production_mode=parsed.production_mode,
                _run_token=CLI_RUN_TOKEN)
            print("STATUS=" + challenge["status"])
            print("PAPER_AUTHORIZED=false")
            print("ORDER_SUBMISSION_AUTHORIZED=false")
            return 0
        if parsed.consume_terminal_response:
            _require(
                parsed.transport_cutoff_receipt is not None and
                parsed.provider_trust_policy is not None and
                parsed.challenge is not None and
                parsed.signed_account_evidence is not None and
                parsed.provider_request is not None and
                parsed.provider_response is not None and
                parsed.terminal_witness_output is not None and
                parsed.expected_cycle_id is not None and
                parsed.expected_recovery_id is not None and
                parsed.expected_finalization_id is not None,
                "TERMINAL_WITNESS_CLI_ARGUMENT_INVALID")
            witness = consume_terminal_response_and_publish(
                transport_cutoff_path=parsed.transport_cutoff_receipt,
                provider_trust_policy_path=parsed.provider_trust_policy,
                challenge_path=parsed.challenge,
                signed_evidence_path=parsed.signed_account_evidence,
                provider_request_path=parsed.provider_request,
                provider_response_path=parsed.provider_response,
                witness_output_path=parsed.terminal_witness_output,
                expected_source=parsed.expected_source_baseline_sha256,
                expected_campaign=parsed.expected_campaign_id,
                expected_cycle=parsed.expected_cycle_id,
                expected_recovery=parsed.expected_recovery_id,
                expected_finalization=parsed.expected_finalization_id,
                production_mode=parsed.production_mode,
                _run_token=CLI_RUN_TOKEN)
            print("STATUS=" + witness["status"])
            print("PAPER_AUTHORIZED=false")
            print("ORDER_SUBMISSION_AUTHORIZED=false")
            return 0
        if parsed.recover_reservation:
            _require(
                parsed.operator_intent is None and
                parsed.watch_handoff_receipt is None and
                parsed.challenge is None and
                parsed.signed_account_evidence is None and
                parsed.broker_output is None and
                parsed.account_output is None,
                "ZERO_SNAPSHOT_CLI_ARGUMENT_INVALID")
            tombstone = recover_reservation_and_publish(
                expected_source=parsed.expected_source_baseline_sha256,
                expected_campaign=parsed.expected_campaign_id,
                production_mode=parsed.production_mode,
                _run_token=CLI_RUN_TOKEN)
            print("RESERVATION_STATUS=" + tombstone["status"])
            print("PAPER_AUTHORIZED=false")
            print("ORDER_SUBMISSION_AUTHORIZED=false")
            return 0
        if parsed.issue_challenge:
            _require(
                parsed.operator_intent is not None and
                parsed.watch_handoff_receipt is not None and
                parsed.challenge is not None and
                parsed.signed_account_evidence is None and
                parsed.broker_output is None and parsed.account_output is None,
                "ZERO_SNAPSHOT_CLI_ARGUMENT_INVALID")
            challenge = issue_challenge_and_publish(
                operator_intent_path=parsed.operator_intent,
                handoff_path=parsed.watch_handoff_receipt,
                challenge_output_path=parsed.challenge,
                expected_source=parsed.expected_source_baseline_sha256,
                expected_campaign=parsed.expected_campaign_id,
                production_mode=parsed.production_mode,
                _run_token=CLI_RUN_TOKEN)
            print("STATUS=" + challenge["status"])
            print("PAPER_AUTHORIZED=false")
            print("ORDER_SUBMISSION_AUTHORIZED=false")
            return 0
        _require(
            parsed.operator_intent is not None and
            parsed.watch_handoff_receipt is not None and
            parsed.challenge is not None and
            parsed.signed_account_evidence is not None and
            parsed.broker_output is not None and
            parsed.account_output is not None,
            "ZERO_SNAPSHOT_CLI_ARGUMENT_INVALID")
        pair = consume_response_and_publish(
            operator_intent_path=parsed.operator_intent,
            handoff_path=parsed.watch_handoff_receipt,
            challenge_path=parsed.challenge,
            signed_evidence_path=parsed.signed_account_evidence,
            broker_output_path=parsed.broker_output,
            account_output_path=parsed.account_output,
            expected_source=parsed.expected_source_baseline_sha256,
            expected_campaign=parsed.expected_campaign_id,
            production_mode=parsed.production_mode,
            _run_token=CLI_RUN_TOKEN)
    except ProducerError as error:
        print(
            "hepta_p1_paper_zero_exposure_snapshot_producer: FAIL " +
            error.reason, file=sys.stderr)
        return 4
    print("BROKER_STATUS=" + pair.broker_snapshot["status"])
    print("ACCOUNT_STATUS=" + pair.account_snapshot["status"])
    print("PAPER_AUTHORIZED=false")
    print("ORDER_SUBMISSION_AUTHORIZED=false")
    return {"PASS": 0, "NO_GO": 2, "HALT": 3}[
        pair.broker_snapshot["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
