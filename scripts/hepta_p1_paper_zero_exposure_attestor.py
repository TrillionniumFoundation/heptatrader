#!/usr/bin/env -S /usr/bin/python3.12 -I -S

"""Independently attest a producer-issued PAPER zero-exposure pair.

This fixed, root-only executable consumes only local immutable receipts and a
previously supplied offline signed account-evidence envelope. It independently
revalidates the producer chain and reviewed Ed25519 signature, then reacquires
the producer's host-authority lease through no-replace publication. It never
invokes systemd, opens a network socket, reads credentials, or grants authority.
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

INSTALLED_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-paper-zero-exposure-attestor")
SNAPSHOT_PRODUCER_EXECUTABLE = Path(
    "/usr/libexec/hepta-p1-paper-zero-exposure-snapshot-producer")
HANDOFF_EXECUTABLE = Path("/usr/libexec/hepta-p1-watch-to-paper-handoff")
BROKER_POLICY_HELPER = Path("/usr/libexec/hepta-broker-egress-policy")
BROKER_POLICY_UNIT = "hepta-broker-egress-policy.service"
SIGNATURE_VERIFIER = Path("/usr/bin/openssl")
VERIFICATION_KEY = Path(
    "/etc/heptatrader/p1-paper-account-evidence-ed25519.pub")
HOST_AUTHORITY_DIRECTORY = Path("/run/hepta/ib-paper-host-authority")
HOST_AUTHORITY_LEASE_PATH = HOST_AUTHORITY_DIRECTORY / "lease.lock"
HOST_AUTHORITY_OWNER_PATH = HOST_AUTHORITY_DIRECTORY / "owner.v1"
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
KILL_SWITCH_PATH = Path("/run/hepta/ib-paper-control-alpha/kill-switch")
GLOBAL_KILL_SWITCH_PATH = Path("/run/hepta/ib-paper-control/kill-switch")
PAPER_CONTROL_GID = 2121
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
    "/var/lib/heptatrader/p1-watch-profile-receipts/round114-generation22.json")
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

PRODUCTION_MODE = "PRODUCTION_ROOT_OFFLINE_SIGNED_ACCOUNT_ATTESTOR"
SNAPSHOT_PRODUCTION_MODE = "PRODUCTION_ROOT_OFFLINE_SIGNED_ACCOUNT_ADAPTER"
HANDOFF_PRODUCTION_MODE = "PRODUCTION_ROOT_SYSTEMD"
SIGNATURE_ALGORITHM = "ED25519"
REMOTE_OBSERVATION_AUTHORITY = "INDEPENDENT_REMOTE_READ_ONLY_ACCOUNT"
REMOTE_QUERY_EFFECT = "READ_ONLY"
BROKER_OBSERVATION_METHOD = (
    "FIXED_LOCAL_READ_ONLY_SYSTEMD_PROC_BROKER_POLICY")
BROKER_OBSERVER_ID = "hepta-p1-zero-exposure-local-boundary-v2"
ACCOUNT_OBSERVER_ID = "hepta-p1-zero-exposure-signed-adapter-v2"
PROTECTED_BROKER_PORTS = (4001, 4002, 7496, 7497)

OUTPUT_SCHEMA = "hepta.p1-paper-deny-all-zero-exposure-receipt.v1"
BROKER_SNAPSHOT_SCHEMA = "hepta.p1-paper-broker-deny-all-snapshot.v1"
ACCOUNT_SNAPSHOT_SCHEMA = (
    "hepta.p1-paper-authoritative-account-snapshot.v1")
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
INTENT_SCHEMA = "hepta.p1-paper-zero-exposure-production-intent.v1"
CHALLENGE_SCHEMA = "hepta.p1-paper-account-evidence-challenge.v1"
SIGNED_EVIDENCE_ENVELOPE_SCHEMA = (
    "hepta.remote-authoritative-account-evidence-envelope.v1")
SIGNED_EVIDENCE_PAYLOAD_SCHEMA = (
    "hepta.remote-authoritative-account-evidence.v1")
RESERVATION_SCHEMA = (
    "hepta.p1-paper-zero-exposure-host-authority-reservation.v1")
RESERVATION_LIFECYCLE = (
    "CHALLENGE_ISSUED_TO_PAPER_TESTING_ADMISSION_FINALIZATION")
RESERVATION_NEXT_CONSUMER = "PAPER_TESTING_ADMISSION_VERIFIER"
RESERVATION_FINALIZATION_SCHEMA = (
    "hepta.p1-paper-zero-exposure-reservation-finalization.v1")
RESERVATION_CURRENT_POINTER_SCHEMA = (
    "hepta.p1-paper-zero-exposure-finalization-current.v1")
TRANSPORT_CUTOFF_SCHEMA = "hepta.paper-transport-cutoff-receipt.v1"
TERMINAL_PROVIDER_TRUST_POLICY_SCHEMA = (
    "hepta.paper-terminal-account-provider-trust-policy.v1")
TERMINAL_CHALLENGE_SCHEMA = (
    "hepta.paper-terminal-account-evidence-challenge.v1")
TERMINAL_SIGNED_EVIDENCE_SCHEMA = (
    "hepta.paper-signed-terminal-account-evidence.v1")
TERMINAL_WITNESS_SCHEMA = "hepta.paper-post-cutoff-terminal-witness.v1"
EGRESS_BOUNDARY_RECEIPT_SCHEMA = "hepta.broker-egress-current-boundary.v1"
TERMINAL_PRODUCTION_MODE = "PRODUCTION_ROOT_POST_CUTOFF_SIGNED_ACCOUNT_WITNESS"
TERMINAL_PROOF_KIND = "POST_CUTOFF_SIGNED_ACCOUNT_WITNESS_V1"
TERMINAL_PROVIDER_CAPABILITY = (
    "ACCOUNT_WIDE_ATOMIC_OR_CAUSAL_POST_CUTOFF_READ_ONLY_V1")
TERMINAL_PROVIDER_ID = "reviewed-remote-account-authority-a"
# Deliberately impossible to satisfy until deployment provisions and reviews
# one exact remote provider trust policy; never synthesize a local substitute.
TERMINAL_PROVIDER_TRUST_POLICY_BODY_SHA256 = (
    "sha256:a77c1b3e779ef085a888815b6e1ac1b3facfc99e7b7494d694e8745e15173563")
TERMINAL_CUTOFF_STATUS = "TRANSPORT_CUTOFF_DURABLE"
TERMINAL_CHALLENGE_STATUS = "AWAITING_SIGNED_TERMINAL_ACCOUNT_EVIDENCE"
TERMINAL_SIGNED_EVIDENCE_STATUS = "COMPLETE"
TERMINAL_WITNESS_STATUS = "POST_CUTOFF_TERMINAL_FLAT_PROVEN"
RESERVATION_FINALIZATION_ORDER = (
    "CANDIDATE_COMMIT_THEN_TOMBSTONE_COMMIT_THEN_CURRENT_POINTER_COMMIT_"
    "THEN_OWNER_REMOVE_THEN_REOPEN")

MAXIMUM_JSON_BYTES = 4 * 1024 * 1024
MAXIMUM_OUTPUT_BYTES = 2 * 1024 * 1024
MAXIMUM_CLOCK_SKEW_MS = 5 * 1000
MAXIMUM_EVIDENCE_AGE_MS = 30 * 1000
MAXIMUM_OBSERVATION_SKEW_MS = 30 * 1000
MAXIMUM_CHALLENGE_LIFETIME_MS = 5 * 60 * 1000
MAXIMUM_OUTPUT_LIFETIME_MS = 60 * 1000

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
NONCE = re.compile(r"[0-9a-f]{64}")
RESERVATION_ID = re.compile(r"zero-exposure-[0-9a-f]{48}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW | CLOEXEC
READ_FLAGS = os.O_RDONLY | NOFOLLOW | CLOEXEC | NONBLOCK
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC
RENAME_NOREPLACE = 1
LIBC = ctypes.CDLL(None, use_errno=True)
CLI_RUN_TOKEN = object()
SAFE_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C", "LC_ALL": "C", "PYTHONNOUSERSITE": "1",
}

BOUNDARY_FIELDS = (
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized",
)
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
SIGNATURE_ATTESTATION_FIELDS = frozenset({
    *SIGNATURE_PROOF_FIELDS, "return_code", "stdout", "stderr",
    "stdout_sha256", "stderr_sha256",
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
    "source_baseline_sha256", "cycle_id", "recovery_id", "finalization_id",
    "boot_id", "service_pid", "service_start_ticks",
    "broker_socket_identity_sha256", "account_id_sha256", "owner_ids",
    "owner_set_sha256", "owner_set_canonical_hex", "owner_count",
    "execution_service_epoch", "execution_service_fencing_generation",
    "mutation_fence_generation", "known_mutation_command_set_sha256",
    "known_mutation_command_count", "known_correlation_set_sha256",
    "known_correlation_count", "egress_policy_generation",
    "egress_policy_sha256", "authorized_connectors", "authorized_uids",
    "broker_socket_count", "broker_process_count", "credential_exposure_count",
    "process_inventory_complete", "socket_inventory_complete",
    "credential_inventory_complete", "mutation_gate_closed",
    "reconnect_permitted", *BOUNDARY_FIELDS, "body_sha256",
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
    "source_baseline_sha256", "cycle_id", "recovery_id", "finalization_id",
    "nonce", "boot_id", "service_pid", "service_start_ticks",
    "broker_socket_identity_sha256", "account_id_sha256", "owner_ids",
    "owner_set_sha256", "owner_set_canonical_hex", "owner_count",
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
    "owner_count", "execution_service_epoch",
    "execution_service_fencing_generation", "mutation_fence_generation",
    "known_mutation_command_set_sha256", "known_mutation_command_count",
    "known_correlation_set_sha256", "known_correlation_count",
    "egress_policy_generation", "egress_policy_sha256", "provider_id",
    "provider_trust_policy_sha256", "provider_key_sha256",
    "provider_capability", "provider_request_sha256",
    "provider_response_sha256", "observation_authority", "query_effect",
    "query_epoch", "query_fencing_generation", "query_invocation_id",
    "provider_clock_id", "provider_boot_id",
    "query_started_after_challenge", "snapshot_consistency",
    "consistency_token_sha256", "consistency_cutoff_body_sha256",
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
    "expires_at_ms", "round", "domain", "campaign_id",
    "source_baseline_sha256", "cycle_id", "recovery_id", "finalization_id",
    "boot_id", "service_pid", "service_start_ticks",
    "broker_socket_identity_sha256", "account_id_sha256", "owner_ids",
    "owner_set_sha256", "owner_set_canonical_hex", "owner_count",
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
    "consistency_dominates_all_mutations", "active_orders_complete",
    "completed_orders_complete", "executions_complete", "positions_complete",
    "cash_fx_complete", "risk_complete", "active_order_count",
    "completed_order_count", "execution_count", "position_count",
    "cash_fx_exposure_count", "gross_absolute_position", "gross_fx_exposure",
    "gross_risk", "settled_mutation_command_count",
    "unknown_mutation_command_count", "unresolved_mutation_command_count",
    "first_host_observed_at_ms", "second_host_observed_at_ms",
    "host_policy_sha256", "host_authorized_connectors",
    "host_authorized_uids", "host_broker_socket_count",
    "host_broker_process_count", "host_credential_exposure_count",
    "host_process_inventory_complete", "host_socket_inventory_complete",
    "host_credential_inventory_complete", "host_paper_units_inactive",
    "host_kill_switch_engaged", "post_cutoff_boundary_verified",
    "egress_policy_generation_stable", "read_only_authority",
    "authoritative", "account_complete", "mutation_attempted",
    *BOUNDARY_FIELDS, "body_sha256",
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

# Exact current producer contracts, copied rather than runtime-imported.
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
    "reservation_lifecycle", "next_consumer", "boot_id", "request_nonce",
    "account_id_sha256", "producer", "production_mode",
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
    "reservation_reference", "candidate_reference",
    "zero_exposure_receipt_reference", "host_authority_lease",
    "recovery_observation", "owner_present_at_tombstone_commit",
    "owner_removal_required_after_commit", "finalization_order",
    "recovery_reason", *BOUNDARY_FIELDS, "body_sha256",
})
RESERVATION_CURRENT_POINTER_FIELDS = frozenset({
    "schema", "version", "status", "updated_at_ms", "round", "domain",
    "campaign_id", "source_baseline_sha256", "boot_id",
    "reservation_id", "reservation_generation",
    "predecessor_finalization_body_sha256",
    "finalization_tombstone_reference", "host_authority_lease",
    *BOUNDARY_FIELDS, "body_sha256",
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
OUTPUT_FIELDS = frozenset({
    "schema", "version", "status", "observed_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "producer", "production_mode", "snapshot_producer",
    "snapshot_production_mode", "intent_id", "operator_intent_reference",
    "watch_handoff_receipt", "challenge_reference",
    "host_authority_reservation", "reservation_id",
    "reservation_generation", "reservation_lifecycle",
    "reservation_predecessor_finalization_body_sha256",
    "reservation_prior_finalization_pointer_reference",
    "reservation_next_consumer", "reservation_continuity_verified",
    "reservation_finalization_tombstone_path",
    "reservation_finalization_current_pointer_path",
    "reservation_finalization_tombstone_absent",
    "reservation_finalization_schema", "reservation_finalization_order",
    "reservation_boot_id", "reservation_lease_device",
    "reservation_lease_inode",
    "signed_evidence_reference", "broker_boundary_reference",
    "authoritative_state_reference", "signature_verification",
    "request_nonce", "account_id_sha256", "provider_id",
    "provider_request_id_sha256", "provider_response_sha256",
    "observation_method", "broker_policy_helper", "broker_observer_id",
    "account_observer_id", "observation_authority", "query_effect",
    "query_epoch", "query_fencing_generation", "query_invocation_id",
    "read_only_authority", "authoritative", "account_complete",
    "snapshot_sha256", "observation_complete", "broker_deny_all",
    "policy_sha256", "authorized_connectors", "authorized_uids",
    "broker_socket_count", "broker_process_count",
    "credential_exposure_count", "order_count", "position_count",
    "gross_absolute_position", "end_flat", "paper_units_inactive",
    "kill_switch_engaged", "protected_broker_ports",
    "process_inventory_complete", "socket_inventory_complete",
    "credential_inventory_complete", "host_authority_lease",
    "host_authority_lease_reacquired", *BOUNDARY_FIELDS, "body_sha256",
})


class AttestationError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise AttestationError(reason)


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise AttestationError(
            "ZERO_EXPOSURE_CANONICALIZATION_FAILED") from error


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


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
    """Stable directory identity; child churn may change ``st_nlink``."""

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
    try:
        descriptor = os.open("/", DIRECTORY_FLAGS)
    except OSError as error:
        raise AttestationError(reason) from error
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
    except (OSError, AttestationError) as error:
        os.close(descriptor)
        if isinstance(error, AttestationError):
            raise
        raise AttestationError(reason) from error


def _trusted_parent(
    descriptor: int, *, expected_uid: int, expected_gid: int, reason: str,
) -> tuple[int, ...]:
    metadata = os.fstat(descriptor)
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
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
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
            final = os.stat(path.name, dir_fd=parent,
                            follow_symlinks=False)
            _require(
                0 < len(payload) <= maximum and
                _identity(opened) == _identity(after) == _identity(final) and
                parent_identity == _trusted_parent(
                    parent, expected_uid=expected_uid,
                    expected_gid=expected_gid, reason=reason), reason)
            return bytes(payload), opened, parent_identity
        finally:
            os.close(descriptor)
    except (OSError, AttestationError) as error:
        if isinstance(error, AttestationError):
            raise
        raise AttestationError(reason) from error
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
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            _require(
                _identity(opened) == _identity(after) == _identity(named) and
                parent_identity == _trusted_parent(
                    parent, expected_uid=expected_uid,
                    expected_gid=expected_gid, reason=reason), reason)
        finally:
            os.close(descriptor)
    except (OSError, AttestationError) as error:
        if isinstance(error, AttestationError):
            raise
        raise AttestationError(reason) from error
    finally:
        os.close(parent)
    try:
        text = payload.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise AttestationError(reason) from error
    _require(text.endswith("\n") and BOOT_ID.fullmatch(text[:-1]) is not None,
             reason)
    return text[:-1]


def strict_object(payload: bytes, reason: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AttestationError(reason)
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("ascii", errors="strict"),
            object_pairs_hook=unique,
            parse_float=lambda _value: (_ for _ in ()).throw(
                AttestationError(reason)),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                AttestationError(reason)))
    except AttestationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AttestationError(reason) from error
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
) -> dict[str, Any]:
    payload, metadata, _ = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=frozenset({mode}), maximum=max(size, 1))
    record = _profile_record(path, payload, metadata)
    _require(
        record["file_sha256"] == sha256 and record["bytes"] == size and
        record["mode"] == stat.S_IFREG | mode and
        record["uid"] == expected_uid and record["gid"] == expected_gid and
        record["nlink"] == 1, reason)
    return record


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
            opened.st_uid == expected_uid and opened.st_gid == expected_file_gid
            and stat.S_IMODE(opened.st_mode) == 0o440 and
            _identity(before) == _identity(opened) == _identity(after) ==
                _identity(final) and payload == b"engaged" and
            parent_identity == _trusted_parent(
                parent, expected_uid=expected_uid,
                expected_gid=expected_parent_gid, reason=reason), reason)
    except (OSError, AttestationError) as error:
        if isinstance(error, AttestationError):
            raise
        raise AttestationError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _validate_profile_record(
    value: Any, actual: Mapping[str, Any], reason: str, *, sealed: bool = False,
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
    _require(document.get("status") == status and
             document.get("round") == ROUND and
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
            raise AttestationError(reason)
        _require(parent_identity == _trusted_parent(
            parent, expected_uid=expected_uid, expected_gid=expected_gid,
            reason=reason), reason)
    except OSError as error:
        raise AttestationError(reason) from error
    finally:
        os.close(parent)


def _integer(value: Any, reason: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, reason)
    return value


def _digest(value: Any, reason: str, *, nonzero: bool = True) -> str:
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
        "ZERO_EXPOSURE_RESERVATION_ID_INVALID")
    return HOST_AUTHORITY_DIRECTORY / (
        "finalized." + reservation_id + ".v1.json")


def reservation_current_pointer_path() -> Path:
    return HOST_AUTHORITY_DIRECTORY / "finalization-current.v1.json"


def _false_boundary(document: Mapping[str, Any], reason: str) -> None:
    _require(all(document.get(field) is False for field in BOUNDARY_FIELDS),
             reason)


def _reference(value: Any, reason: str) -> dict[str, str]:
    _require(isinstance(value, dict) and set(value) == REFERENCE_FIELDS, reason)
    path = value.get("path")
    _require(type(path) is str, reason)
    return {
        "path": str(_canonical_path(Path(path), reason)),
        "file_sha256": _digest(value.get("file_sha256"), reason),
        "body_sha256": _digest(value.get("body_sha256"), reason),
    }


def _reservation_reference(value: Any, reason: str) -> dict[str, Any]:
    _require(isinstance(value, dict) and
             set(value) == RESERVATION_REFERENCE_FIELDS, reason)
    path = value.get("path")
    _require(type(path) is str and
             _canonical_path(Path(path), reason) ==
                HOST_AUTHORITY_OWNER_PATH, reason)
    result = dict(value)
    _digest(result.get("file_sha256"), reason)
    _digest(result.get("body_sha256"), reason)
    for field in (
        "device", "inode", "uid", "gid", "mode", "size", "mtime_ns",
        "ctime_ns",
    ):
        _integer(result.get(field), reason)
    _require(result["device"] > 0 and result["inode"] > 0 and
             result["mode"] == 0o600 and result["size"] > 0, reason)
    return result


def _signed_reference(value: Any, reason: str) -> dict[str, str]:
    _require(isinstance(value, dict) and
             set(value) == SIGNED_REFERENCE_FIELDS, reason)
    path = value.get("path")
    _require(type(path) is str, reason)
    return {
        "path": str(_canonical_path(Path(path), reason)),
        "file_sha256": _digest(value.get("file_sha256"), reason),
        "signed_payload_sha256": _digest(
            value.get("signed_payload_sha256"), reason),
    }


def _executable_reference(value: Any, reason: str) -> dict[str, str]:
    _require(isinstance(value, dict) and
             set(value) == EXECUTABLE_REFERENCE_FIELDS, reason)
    path = value.get("path")
    _require(type(path) is str, reason)
    return {
        "path": str(_canonical_path(Path(path), reason)),
        "file_sha256": _digest(value.get("file_sha256"), reason),
    }


def _boundary_findings(
    document: Mapping[str, Any], prefix: str,
) -> list[str]:
    findings: list[str] = []
    for field in BOUNDARY_FIELDS:
        _require(type(document.get(field)) is bool,
                 prefix + "_BOUNDARY_INVALID")
        if document[field]:
            findings.append(prefix + "_" + field.upper() + "_DANGEROUS")
    return findings


def _time_current(
    start: Any, expires: Any, now_ms: int, reason: str,
) -> tuple[int, int, bool]:
    started = _integer(start, reason)
    expiry = _integer(expires, reason)
    _require(started < expiry and started <= now_ms + MAXIMUM_CLOCK_SKEW_MS,
             reason)
    return started, expiry, now_ms < expiry


@dataclass(frozen=True)
class InputBinding:
    path: Path
    payload: bytes
    identity: tuple[int, ...]
    parent_identity: tuple[int, ...]
    document: dict[str, Any]
    sealed: bool

    @property
    def reference(self) -> dict[str, str]:
        _require(self.sealed, "ZERO_EXPOSURE_REFERENCE_INVALID")
        return {
            "path": str(self.path), "file_sha256": digest_bytes(self.payload),
            "body_sha256": self.document["body_sha256"],
        }

    def signed_reference(self, payload_sha256: str) -> dict[str, str]:
        _require(not self.sealed, "ZERO_EXPOSURE_REFERENCE_INVALID")
        return {
            "path": str(self.path), "file_sha256": digest_bytes(self.payload),
            "signed_payload_sha256": payload_sha256,
        }

    def reservation_reference(self) -> dict[str, Any]:
        _require(
            self.sealed and self.path == HOST_AUTHORITY_OWNER_PATH and
            self.document.get("schema") == RESERVATION_SCHEMA,
            "ZERO_EXPOSURE_RESERVATION_REFERENCE_INVALID")
        metadata = self.identity
        result = {
            "path": str(self.path),
            "file_sha256": digest_bytes(self.payload),
            "body_sha256": self.document["body_sha256"],
            "device": metadata[0], "inode": metadata[1],
            "uid": metadata[4], "gid": metadata[5],
            "mode": stat.S_IMODE(metadata[2]), "size": metadata[6],
            "mtime_ns": metadata[7], "ctime_ns": metadata[8],
        }
        _require(set(result) == RESERVATION_REFERENCE_FIELDS,
                 "ZERO_EXPOSURE_RESERVATION_REFERENCE_INVALID")
        return result

    def reopen(self, *, expected_uid: int, expected_gid: int) -> None:
        reason = "ZERO_EXPOSURE_INPUT_SECURE_REOPEN_MISMATCH"
        payload, metadata, parent = secure_read(
            self.path, reason, expected_uid=expected_uid,
            expected_gid=expected_gid)
        _require(
            payload == self.payload and _identity(metadata) == self.identity and
            parent == self.parent_identity and
            strict_object(payload, reason) == self.document, reason)


@dataclass(frozen=True)
class FileBinding:
    path: Path
    payload: bytes
    identity: tuple[int, ...]
    parent_identity: tuple[int, ...]
    expected_uid: int
    expected_gid: int
    modes: frozenset[int]
    maximum: int
    executing: bool = False

    @property
    def reference(self) -> dict[str, str]:
        return {"path": str(self.path), "file_sha256": digest_bytes(self.payload)}

    def reopen(self) -> None:
        reason = "ZERO_EXPOSURE_FIXED_FILE_DRIFT"
        if self.executing:
            lexical = Path(__file__).absolute()
            try:
                lexical_metadata = os.lstat(lexical)
                resolved = lexical.resolve(strict=True)
                installed = self.path.resolve(strict=True)
            except OSError as error:
                raise AttestationError(reason) from error
            _require(not stat.S_ISLNK(lexical_metadata.st_mode) and
                     lexical == self.path and resolved == installed == self.path
                     and os.path.samefile(resolved, installed), reason)
        payload, metadata, parent = secure_read(
            self.path, reason, expected_uid=self.expected_uid,
            expected_gid=self.expected_gid, modes=self.modes,
            maximum=self.maximum)
        _require(payload == self.payload and
                 _identity(metadata) == self.identity and
                 parent == self.parent_identity, reason)


def _bind_file(
    path: Path, reason: str, *, expected_uid: int, expected_gid: int,
    modes: frozenset[int], maximum: int = MAXIMUM_JSON_BYTES,
    executing: bool = False,
) -> FileBinding:
    path = _canonical_path(path, reason)
    if executing:
        lexical = Path(__file__).absolute()
        try:
            metadata = os.lstat(lexical)
            resolved = lexical.resolve(strict=True)
        except OSError as error:
            raise AttestationError(reason) from error
        _require(not stat.S_ISLNK(metadata.st_mode) and lexical == path and
                 resolved == path.resolve(strict=True) == path, reason)
    payload, metadata, parent = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid,
        modes=modes, maximum=maximum)
    binding = FileBinding(
        path, payload, _identity(metadata), parent, expected_uid, expected_gid,
        modes, maximum, executing)
    binding.reopen()
    return binding


def _bind_document(
    path: Path, fields: frozenset[str] | None, schema: str | None,
    reason: str, *, expected_uid: int, expected_gid: int,
) -> InputBinding:
    path = _canonical_path(path, reason)
    payload, metadata, parent = secure_read(
        path, reason, expected_uid=expected_uid, expected_gid=expected_gid)
    document = strict_object(payload, reason)
    _require(payload == canonical_bytes(document), reason)
    is_sealed = fields is not None
    if is_sealed:
        _require(schema is not None, reason)
        _sealed(
            document, fields, schema, reason,
            version=HANDOFF_VERSION if schema == HANDOFF_SCHEMA else VERSION)
    binding = InputBinding(
        path, payload, _identity(metadata), parent, document, is_sealed)
    binding.reopen(expected_uid=expected_uid, expected_gid=expected_gid)
    return binding


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
        return self.binding.signed_reference(self.payload_sha256)


@dataclass(frozen=True)
class HostAuthorityLease:
    directory_descriptor: int
    descriptor: int
    directory_identity: tuple[int, ...]
    lease_identity: tuple[int, ...]
    expected_uid: int
    expected_gid: int
    boot_id: str

    @property
    def reference(self) -> dict[str, Any]:
        directory = self.directory_identity
        lease = self.lease_identity
        return {
            "directory_path": str(HOST_AUTHORITY_DIRECTORY),
            "lease_path": str(HOST_AUTHORITY_LEASE_PATH),
            "owner_path": str(HOST_AUTHORITY_OWNER_PATH),
            "directory_device": directory[0],
            "directory_inode": directory[1],
            "directory_uid": directory[3], "directory_gid": directory[4],
            "directory_mode": stat.S_IMODE(directory[2]),
            "lease_device": lease[0], "lease_inode": lease[1],
            "lease_uid": lease[4], "lease_gid": lease[5],
            "lease_mode": stat.S_IMODE(lease[2]), "lease_size": lease[6],
            "held_exclusive": True,
            "boot_id": self.boot_id,
        }


def _write_memfd(name: str, payload: bytes) -> int:
    descriptor = -1
    try:
        descriptor = os.memfd_create(
            name, os.MFD_CLOEXEC | getattr(os, "MFD_ALLOW_SEALING", 0))
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            _require(count > 0, "ZERO_EXPOSURE_SIGNATURE_VERIFY_FAILED")
            offset += count
        os.lseek(descriptor, 0, os.SEEK_SET)
        if hasattr(fcntl, "F_ADD_SEALS"):
            seals = (fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK |
                     fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
            fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, seals)
        return descriptor
    except (OSError, AttestationError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        if isinstance(error, AttestationError):
            raise
        raise AttestationError(
            "ZERO_EXPOSURE_SIGNATURE_VERIFY_FAILED") from error


class ProductionContext:
    """Fixed installed image, trust files, verifier, and host lease."""

    def __init__(self, *, expected_uid: int = ROOT_UID,
                 expected_gid: int = ROOT_GID) -> None:
        _require(os.geteuid() == expected_uid and os.getegid() == expected_gid,
                 "ZERO_EXPOSURE_ROOT_REQUIRED")
        executable_modes = frozenset({0o500, 0o555, 0o700, 0o755})
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid
        self.attestor = _bind_file(
            INSTALLED_EXECUTABLE, "ZERO_EXPOSURE_EXECUTING_IMAGE_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid,
            modes=executable_modes, executing=True)
        self.snapshot_producer = _bind_file(
            SNAPSHOT_PRODUCER_EXECUTABLE,
            "ZERO_EXPOSURE_SNAPSHOT_PRODUCER_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid,
            modes=executable_modes)
        self.handoff_producer = _bind_file(
            HANDOFF_EXECUTABLE, "ZERO_EXPOSURE_HANDOFF_PRODUCER_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid,
            modes=executable_modes)
        self.broker_helper = _bind_file(
            BROKER_POLICY_HELPER, "ZERO_EXPOSURE_BROKER_HELPER_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid,
            modes=executable_modes)
        self.signature_verifier = _bind_file(
            SIGNATURE_VERIFIER, "ZERO_EXPOSURE_SIGNATURE_VERIFIER_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid,
            modes=executable_modes)
        self.verification_key = _bind_file(
            VERIFICATION_KEY, "ZERO_EXPOSURE_VERIFICATION_KEY_INVALID",
            expected_uid=expected_uid, expected_gid=expected_gid,
            modes=frozenset({0o400, 0o440, 0o444, 0o600, 0o640, 0o644}),
            maximum=64 * 1024)

    def reopen(self) -> None:
        self.attestor.reopen()
        self.snapshot_producer.reopen()
        self.handoff_producer.reopen()
        self.broker_helper.reopen()
        self.signature_verifier.reopen()
        self.verification_key.reopen()

    def verify_signature(self, evidence: SignedEvidence) -> dict[str, Any]:
        self.reopen()
        payload_fd = _write_memfd(
            "hepta-zero-attestor-payload", evidence.payload_bytes)
        signature_fd = _write_memfd(
            "hepta-zero-attestor-signature", evidence.signature)
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
                    pass_fds=(payload_fd, signature_fd), env=SAFE_ENVIRONMENT,
                    cwd="/", timeout=15)
            except (OSError, subprocess.SubprocessError) as error:
                raise AttestationError(
                    "ZERO_EXPOSURE_SIGNATURE_VERIFY_FAILED") from error
        finally:
            os.close(payload_fd)
            os.close(signature_fd)
        expected_stdout = b"Signature Verified Successfully\n"
        _require(result.returncode == 0 and result.stdout == expected_stdout and
                 result.stderr == b"",
                 "ZERO_EXPOSURE_SIGNATURE_VERIFY_FAILED")
        self.reopen()
        return {
            "algorithm": SIGNATURE_ALGORITHM,
            "public_key": self.verification_key.reference,
            "verifier": self.signature_verifier.reference,
            "signature_sha256": evidence.signature_sha256,
            "signed_payload_sha256": evidence.payload_sha256,
            "return_code": result.returncode,
            "stdout": result.stdout.decode("ascii"),
            "stderr": result.stderr.decode("ascii"),
            "stdout_sha256": digest_bytes(result.stdout),
            "stderr_sha256": digest_bytes(result.stderr),
        }

    def acquire_lease(self) -> HostAuthorityLease:
        return _acquire_host_authority_lease(
            expected_uid=self.expected_uid, expected_gid=self.expected_gid)

    def validate_lease(
        self, lease: HostAuthorityLease, reservation: InputBinding,
    ) -> dict[str, Any]:
        return _validate_live_lease(lease, reservation)

    def release_lease(
        self, lease: HostAuthorityLease, reservation: InputBinding,
    ) -> None:
        _release_host_authority_lease(lease, reservation)


def _validate_authority_directory(
    metadata: os.stat_result, expected_uid: int, expected_gid: int,
    reason: str,
) -> None:
    _require(stat.S_ISDIR(metadata.st_mode) and metadata.st_nlink >= 2 and
             metadata.st_uid == expected_uid and
             metadata.st_gid == expected_gid and
             stat.S_IMODE(metadata.st_mode) == 0o700, reason)


def _validate_authority_lock(
    metadata: os.stat_result, expected_uid: int, expected_gid: int,
    reason: str,
) -> None:
    _require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
             metadata.st_uid == expected_uid and
             metadata.st_gid == expected_gid and
             stat.S_IMODE(metadata.st_mode) == 0o600 and
             metadata.st_size == 0, reason)


def _reservation_tombstone_absent(
    directory: int, path: Path, reason: str,
) -> None:
    _require(path.parent == HOST_AUTHORITY_DIRECTORY, reason)
    try:
        os.stat(path.name, dir_fd=directory,
                follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise AttestationError(reason) from error
    raise AttestationError(reason)


def _acquire_host_authority_lease(
    *, expected_uid: int, expected_gid: int,
) -> HostAuthorityLease:
    reason = "ZERO_EXPOSURE_HOST_AUTHORITY_LEASE_INVALID"
    directory_path = _canonical_path(HOST_AUTHORITY_DIRECTORY, reason)
    lease_path = _canonical_path(HOST_AUTHORITY_LEASE_PATH, reason)
    owner_path = _canonical_path(HOST_AUTHORITY_OWNER_PATH, reason)
    _require(lease_path.parent == directory_path and
             owner_path.parent == directory_path, reason)
    directory_descriptor = -1
    descriptor = -1
    locked = False
    try:
        directory_descriptor = _open_directory(directory_path, reason)
        directory = os.fstat(directory_descriptor)
        _validate_authority_directory(
            directory, expected_uid, expected_gid, reason)
        before = os.stat(lease_path.name, dir_fd=directory_descriptor,
                         follow_symlinks=False)
        _validate_authority_lock(before, expected_uid, expected_gid, reason)
        descriptor = os.open(
            lease_path.name, READ_FLAGS, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        _validate_authority_lock(opened, expected_uid, expected_gid, reason)
        _require(_identity(before) == _identity(opened), reason)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise AttestationError(
                    "ZERO_EXPOSURE_HOST_AUTHORITY_LEASE_BUSY") from error
            raise
        lease = HostAuthorityLease(
            directory_descriptor, descriptor,
            _directory_identity(directory), _identity(opened),
            expected_uid, expected_gid,
            _read_boot_id(
                expected_uid=expected_uid, expected_gid=expected_gid,
                reason=reason))
        directory_descriptor = -1
        descriptor = -1
        locked = False
        return lease
    except AttestationError:
        raise
    except OSError as error:
        raise AttestationError(reason) from error
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


def _validate_live_lease(
    lease: HostAuthorityLease, reservation: InputBinding,
) -> dict[str, Any]:
    reason = "ZERO_EXPOSURE_HOST_AUTHORITY_LEASE_REBOUND"
    _require(type(lease) is HostAuthorityLease, reason)
    try:
        directory = os.fstat(lease.directory_descriptor)
        opened = os.fstat(lease.descriptor)
        named = os.stat(HOST_AUTHORITY_LEASE_PATH.name,
                        dir_fd=lease.directory_descriptor,
                        follow_symlinks=False)
        _validate_authority_directory(
            directory, lease.expected_uid, lease.expected_gid, reason)
        for metadata in (opened, named):
            _validate_authority_lock(
                metadata, lease.expected_uid, lease.expected_gid, reason)
        _require(_directory_identity(directory) == lease.directory_identity and
                 _identity(opened) == lease.lease_identity == _identity(named),
                 reason)
        _require(
            _read_boot_id(
                expected_uid=lease.expected_uid,
                expected_gid=lease.expected_gid, reason=reason) ==
                lease.boot_id,
            reason)
        rebound = _open_directory(HOST_AUTHORITY_DIRECTORY, reason)
        try:
            _require(_directory_identity(os.fstat(rebound)) ==
                     lease.directory_identity, reason)
        finally:
            os.close(rebound)
        _require(type(reservation) is InputBinding and
                 reservation.path == HOST_AUTHORITY_OWNER_PATH, reason)
        reservation.reopen(
            expected_uid=lease.expected_uid, expected_gid=lease.expected_gid)
        reservation_id = reservation.document.get("reservation_id")
        tombstone_path = reservation_tombstone_path(reservation_id)
        _require(
            reservation.document.get("finalization_tombstone_path") ==
                str(tombstone_path), reason)
        _reservation_tombstone_absent(
            lease.directory_descriptor, tombstone_path, reason)
        reference = lease.reference
        _require(set(reference) == HOST_AUTHORITY_LEASE_FIELDS, reason)
        return reference
    except AttestationError:
        raise
    except OSError as error:
        raise AttestationError(reason) from error


def _release_host_authority_lease(
    lease: HostAuthorityLease, reservation: InputBinding,
) -> None:
    failure: Exception | None = None
    try:
        _validate_live_lease(lease, reservation)
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
        raise AttestationError(
            "ZERO_EXPOSURE_HOST_AUTHORITY_LEASE_RELEASE_FAILED") from failure


def _validate_terminal_live_lease(
    lease: HostAuthorityLease, owner: InputBinding,
) -> None:
    reason = "TERMINAL_WITNESS_HOST_AUTHORITY_LEASE_REBOUND"
    _require(type(lease) is HostAuthorityLease and
             owner.path == HOST_AUTHORITY_OWNER_PATH and
             owner.document.get("schema") == TERMINAL_CHALLENGE_SCHEMA, reason)
    try:
        directory = os.fstat(lease.directory_descriptor)
        opened = os.fstat(lease.descriptor)
        named = os.stat(
            HOST_AUTHORITY_LEASE_PATH.name,
            dir_fd=lease.directory_descriptor, follow_symlinks=False)
        _validate_authority_directory(
            directory, lease.expected_uid, lease.expected_gid, reason)
        _validate_authority_lock(
            opened, lease.expected_uid, lease.expected_gid, reason)
        _validate_authority_lock(
            named, lease.expected_uid, lease.expected_gid, reason)
        _require(
            _directory_identity(directory) == lease.directory_identity and
            _identity(opened) == lease.lease_identity == _identity(named) and
            _read_boot_id(
                expected_uid=lease.expected_uid,
                expected_gid=lease.expected_gid, reason=reason) ==
                    lease.boot_id, reason)
        owner.reopen(
            expected_uid=lease.expected_uid, expected_gid=lease.expected_gid)
    except AttestationError:
        raise
    except OSError as error:
        raise AttestationError(reason) from error


def _release_terminal_live_lease(
    lease: HostAuthorityLease, owner: InputBinding,
) -> None:
    failure: Exception | None = None
    try:
        _validate_terminal_live_lease(lease, owner)
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
        raise AttestationError(
            "TERMINAL_WITNESS_HOST_AUTHORITY_LEASE_RELEASE_FAILED") from failure


def _validate_historical_lease(
    value: Any, *, expected_uid: int, expected_gid: int,
) -> dict[str, Any]:
    reason = "ZERO_EXPOSURE_HISTORICAL_LEASE_INVALID"
    _require(isinstance(value, dict) and
             set(value) == HOST_AUTHORITY_LEASE_FIELDS, reason)
    for field, expected in {
        "directory_path": HOST_AUTHORITY_DIRECTORY,
        "lease_path": HOST_AUTHORITY_LEASE_PATH,
        "owner_path": HOST_AUTHORITY_OWNER_PATH,
    }.items():
        path = value.get(field)
        _require(type(path) is str and
                 _canonical_path(Path(path), reason) == expected, reason)
    for field in (
        "directory_device", "directory_inode", "directory_uid",
        "directory_gid", "directory_mode", "lease_device", "lease_inode",
        "lease_uid", "lease_gid", "lease_mode", "lease_size",
    ):
        _integer(value.get(field), reason)
    _identifier(value.get("boot_id"), BOOT_ID, reason)
    _require(
        value["directory_device"] > 0 and value["directory_inode"] > 0 and
        value["lease_device"] > 0 and value["lease_inode"] > 0 and
        value["directory_uid"] == expected_uid and
        value["directory_gid"] == expected_gid and
        value["directory_mode"] == 0o700 and
        value["lease_uid"] == expected_uid and
        value["lease_gid"] == expected_gid and
        value["lease_mode"] == 0o600 and value["lease_size"] == 0 and
        value.get("held_exclusive") is True, reason)
    return dict(value)


def account_state_sha256(document: Mapping[str, Any]) -> str:
    return digest_bytes(canonical_bytes({
        "query_epoch": document.get("query_epoch"),
        "query_fencing_generation": document.get(
            "query_fencing_generation"),
        "query_invocation_id": document.get("query_invocation_id"),
        "active_order_id_sha256s": document.get(
            "active_order_id_sha256s"),
        "positions": document.get("positions"),
        "gross_absolute_position": document.get("gross_absolute_position"),
        "authorized_connector_count": document.get(
            "authorized_connector_count"),
        "end_flat": document.get("end_flat"),
    }))


def _validate_account_state(document: Mapping[str, Any], reason: str) -> None:
    _identifier(document.get("query_epoch"), IDENTIFIER, reason)
    _integer(document.get("query_fencing_generation"), reason, 1)
    _identifier(document.get("query_invocation_id"), IDENTIFIER, reason)
    orders = document.get("active_order_id_sha256s")
    _require(isinstance(orders, list) and len(orders) <= 128, reason)
    for order in orders:
        _digest(order, reason)
    _require(len(orders) == len(set(orders)), reason)
    positions = document.get("positions")
    _require(isinstance(positions, list) and len(positions) <= 128, reason)
    seen: set[str] = set()
    gross = 0
    for position in positions:
        _require(isinstance(position, dict) and
                 set(position) == POSITION_FIELDS, reason)
        instrument = _identifier(position.get("instrument"), IDENTIFIER, reason)
        _require(instrument not in seen, reason)
        seen.add(instrument)
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
    result = [_digest(item, reason) for item in value]
    _require(result == sorted(set(result)), reason)
    return result


def _validate_current_egress_boundary(
        expected_generation: int, expected_state_sha256: str) -> None:
    reason = "TERMINAL_WITNESS_CURRENT_BOUNDARY_INVALID"
    _integer(expected_generation, reason, 1)
    _digest(expected_state_sha256, reason)
    try:
        receipt_result = subprocess.run(
            ("/usr/bin/python3.12", "-I", "-S", str(BROKER_POLICY_HELPER),
             "--read-current-boundary"),
            check=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=SAFE_ENVIRONMENT, cwd="/", timeout=15)
        unit_result = subprocess.run(
            ("/usr/bin/systemctl", "show", "--no-pager",
             "--property=LoadState,ActiveState,SubState,Job,MainPID,ControlPID",
             BROKER_POLICY_UNIT),
            check=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=SAFE_ENVIRONMENT, cwd="/", timeout=15)
    except (OSError, subprocess.SubprocessError) as error:
        raise AttestationError(reason) from error
    _require(
        receipt_result.returncode == 0 and receipt_result.stderr == b"" and
        0 < len(receipt_result.stdout) <= 64 * 1024 and
        unit_result.returncode == 0 and unit_result.stderr == b"" and
        0 < len(unit_result.stdout) <= 4096, reason)
    receipt = strict_object(receipt_result.stdout, reason)
    _require(
        set(receipt) == EGRESS_BOUNDARY_RECEIPT_FIELDS and
        receipt_result.stdout == canonical_bytes(receipt) and
        receipt.get("schema") == EGRESS_BOUNDARY_RECEIPT_SCHEMA and
        receipt.get("version") == 1 and
        receipt.get("status") == "EXACT_DENY_ALL" and
        receipt.get("state") == "DENY_ALL" and
        receipt.get("protected_tcp_destination_ports") ==
            list(PROTECTED_BROKER_PORTS) and
        receipt.get("protected_port_count") == 4 and
        receipt.get("authorized_connector_count") == 0 and
        receipt.get("authorized_connectors") == [] and
        receipt.get("authorized_uids") == [] and
        receipt.get("paper_authorized") is False and
        receipt.get("live_authorized") is False and
        re.fullmatch(r"sha256:[0-9a-f]{64}", str(
            receipt.get("source_policy_sha256"))) is not None and
        receipt.get("generation") == expected_generation and
        receipt.get("state_sha256") == expected_state_sha256, reason)
    body = dict(receipt)
    claimed = body.pop("body_sha256", None)
    _require(claimed == digest_bytes(canonical_bytes(body)), reason)
    try:
        unit = dict(
            line.split("=", 1) for line in
            unit_result.stdout.decode("ascii", errors="strict").splitlines())
    except (UnicodeError, ValueError) as error:
        raise AttestationError(reason) from error
    _require(
        set(unit) == {"LoadState", "ActiveState", "SubState", "Job",
                      "MainPID", "ControlPID"} and
        unit["LoadState"] == "loaded" and unit["ActiveState"] == "active" and
        unit["SubState"] == "running" and unit["Job"] == "" and
        unit["ControlPID"] == "0" and unit["MainPID"].isdecimal() and
        int(unit["MainPID"]) == receipt.get("publisher_pid"), reason)


CURRENT_BOUNDARY_VALIDATOR = _validate_current_egress_boundary


def _terminal_owner_binding(document: Mapping[str, Any], reason: str) -> None:
    owners = document.get("owner_ids")
    _require(isinstance(owners, list) and 0 < len(owners) <= 128, reason)
    normalized = [_digest(item, reason) for item in owners]
    _require(normalized == sorted(set(normalized)) and
             _integer(document.get("owner_count"), reason, 1) == len(owners),
             reason)
    canonical_hex = document.get("owner_set_canonical_hex")
    _require(type(canonical_hex) is str and 0 < len(canonical_hex) <= 131072 and
             len(canonical_hex) % 2 == 0 and
             re.fullmatch(r"[0-9a-f]+", canonical_hex) is not None, reason)
    canonical = bytes.fromhex(canonical_hex)
    _require(
        _digest(document.get("owner_set_sha256"), reason) ==
            digest_bytes(canonical), reason)
    try:
        text = canonical.decode("ascii")
    except UnicodeError as error:
        raise AttestationError(reason) from error
    _require(
        canonical.endswith(b"\n") and "\r" not in text and
        "\x00" not in text, reason)
    lines = text[:-1].split("\n")
    _require(
        len(lines) == len(owners) and lines == sorted(set(lines)), reason)
    expected_account_sha256 = _digest(
        document.get("account_id_sha256"), reason)
    expected_domain = "PAPER:" + _identifier(
        document.get("domain"), IDENTIFIER, reason)
    tokens: list[str] = []
    for line in lines:
        fields = line.split("\t")
        _require(len(fields) == 4, reason)
        token = _digest(fields[0], reason)
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
            raise AttestationError(reason) from error
        _require(
            re.fullmatch(r"DU[0-9]{1,16}", account) is not None and
            owner_domain == expected_domain and
            digest_bytes(account.encode("ascii")) == expected_account_sha256,
            reason)
        tokens.append(token)
    _require(tokens == normalized, reason)


def _validate_terminal_policy(
    document: dict[str, Any], *, verification_key_sha256: str,
) -> None:
    reason = "TERMINAL_WITNESS_PROVIDER_TRUST_POLICY_INVALID"
    _sealed(document, TERMINAL_PROVIDER_TRUST_POLICY_FIELDS,
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
        document.get("mutation_attempted") is False, reason)
    _false_boundary(document, reason)


def _validate_terminal_cutoff(
    document: dict[str, Any], *, expected_source: str,
    expected_campaign: str, expected_cycle: str, expected_recovery: str,
    expected_finalization: str, expected_boot_id: str,
) -> None:
    reason = "TERMINAL_WITNESS_TRANSPORT_CUTOFF_INVALID"
    _sealed(document, TRANSPORT_CUTOFF_FIELDS, TRANSPORT_CUTOFF_SCHEMA, reason)
    for field, expected in {
        "source_baseline_sha256": expected_source,
        "campaign_id": expected_campaign, "cycle_id": expected_cycle,
        "recovery_id": expected_recovery,
        "finalization_id": expected_finalization, "boot_id": expected_boot_id,
    }.items():
        _require(document.get(field) == expected, reason)
    _require(
        document.get("status") == TERMINAL_CUTOFF_STATUS and
        document.get("round") == ROUND and document.get("domain") == DOMAIN_ID
        and _integer(document.get("service_pid"), reason, 1) > 0 and
        _integer(document.get("service_start_ticks"), reason, 1) > 0 and
        _integer(document.get("execution_service_fencing_generation"), reason,
                 1) > 0 and
        _integer(document.get("mutation_fence_generation"), reason, 1) > 0 and
        _integer(document.get("egress_policy_generation"), reason, 1) > 0 and
        document.get("authorized_connectors") == 0 and
        document.get("authorized_uids") == [] and
        document.get("broker_socket_count") == 0 and
        document.get("broker_process_count") == 0 and
        document.get("credential_exposure_count") == 0 and
        document.get("process_inventory_complete") is True and
        document.get("socket_inventory_complete") is True and
        document.get("credential_inventory_complete") is True and
        document.get("mutation_gate_closed") is True and
        document.get("reconnect_permitted") is False, reason)
    for field in (
        "source_baseline_sha256", "broker_socket_identity_sha256",
        "account_id_sha256", "known_mutation_command_set_sha256",
        "known_correlation_set_sha256", "egress_policy_sha256",
    ):
        _digest(document.get(field), reason)
    _terminal_owner_binding(document, reason)
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
        document.get("unresolved_mutation_command_count") == 0, reason)
    _false_boundary(document, reason)


def _parse_terminal_signed_evidence(binding: InputBinding) -> SignedEvidence:
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
        type(envelope.get("signature_base64")) is str, reason)
    try:
        signature = base64.b64decode(
            envelope["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeError, ValueError) as error:
        raise AttestationError(reason) from error
    _require(len(signature) == 64 and
             base64.b64encode(signature).decode("ascii") ==
                envelope["signature_base64"], reason)
    payload = dict(envelope["payload"])
    raw = canonical_bytes(payload)
    return SignedEvidence(
        binding, payload, raw, digest_bytes(raw), signature,
        digest_bytes(signature))


def _validate_terminal_challenge(
    document: dict[str, Any], *, cutoff: InputBinding,
    trust_policy: InputBinding, context: ProductionContext, now_ms: int,
    now_monotonic_ns: int, boot_id: str,
) -> None:
    reason = "TERMINAL_WITNESS_CHALLENGE_INVALID"
    _sealed(document, TERMINAL_CHALLENGE_FIELDS, TERMINAL_CHALLENGE_SCHEMA,
            reason)
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
        document.get("status") == TERMINAL_CHALLENGE_STATUS and
        all(document.get(field) == cutoff_document.get(field)
            for field in identity_fields) and document.get("boot_id") == boot_id
        and document.get("transport_cutoff_receipt") == cutoff.reference and
        document.get("cutoff_completed_at_ms") ==
            cutoff_document["completed_at_ms"] and
        document.get("cutoff_completed_monotonic_ns") ==
            cutoff_document["completed_monotonic_ns"] and
        document.get("producer") == context.snapshot_producer.reference and
        document.get("production_mode") == TERMINAL_PRODUCTION_MODE and
        document.get("provider_trust_policy") == trust_policy.reference and
        document.get("provider_id") == TERMINAL_PROVIDER_ID and
        document.get("provider_key_sha256") ==
            trust_policy.document["provider_key_sha256"] and
        document.get("provider_capability") == TERMINAL_PROVIDER_CAPABILITY and
        document.get("signature_algorithm") == SIGNATURE_ALGORITHM and
        document.get("signature_verifier") ==
            context.signature_verifier.reference and
        document.get("verification_key") == context.verification_key.reference
        and document.get("required_observation_authority") ==
            REMOTE_OBSERVATION_AUTHORITY and
        document.get("required_snapshot_consistency") ==
            ["ATOMIC_ACCOUNT", "CAUSAL_WATERMARK"], reason)
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


def _validate_terminal_signed_payload(
    evidence: SignedEvidence, *, cutoff: InputBinding, challenge: InputBinding,
    trust_policy: InputBinding, now_ms: int, now_monotonic_ns: int,
) -> None:
    reason = "TERMINAL_WITNESS_SIGNED_ACCOUNT_EVIDENCE_INVALID"
    payload = evidence.payload
    challenge_document = challenge.document
    cutoff_document = cutoff.document
    policy = trust_policy.document
    _require(
        payload.get("status") == TERMINAL_SIGNED_EVIDENCE_STATUS and
        payload.get("round") == ROUND and
        payload.get("nonce") == challenge_document["nonce"] and
        payload.get("challenge_body_sha256") ==
            challenge_document["body_sha256"] and
        payload.get("transport_cutoff_body_sha256") ==
            cutoff_document["body_sha256"] and
        payload.get("provider_id") == TERMINAL_PROVIDER_ID and
        payload.get("provider_trust_policy_sha256") ==
            policy["body_sha256"] and
        payload.get("provider_key_sha256") == policy["provider_key_sha256"] and
        payload.get("provider_capability") == TERMINAL_PROVIDER_CAPABILITY and
        payload.get("observation_authority") ==
            REMOTE_OBSERVATION_AUTHORITY and
        payload.get("query_effect") == REMOTE_QUERY_EFFECT, reason)
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
        _digest(payload.get(field), reason)
    for field in ("query_epoch", "query_invocation_id", "provider_clock_id"):
        _identifier(payload.get(field), IDENTIFIER, reason)
    _identifier(payload.get("provider_boot_id"), BOOT_ID, reason)
    _integer(payload.get("query_fencing_generation"), reason, 1)
    host_wall = (
        cutoff_document["completed_at_ms"], challenge_document["issued_at_ms"],
        now_ms)
    provider_wall = (
        _integer(payload.get("query_started_at_ms"), reason),
        _integer(payload.get("observed_at_ms"), reason),
        _integer(payload.get("query_completed_at_ms"), reason),
        _integer(payload.get("expires_at_ms"), reason))
    provider_monotonic = (
        _integer(payload.get("query_started_monotonic_ns"), reason),
        _integer(payload.get("observed_monotonic_ns"), reason),
        _integer(payload.get("query_completed_monotonic_ns"), reason))
    _require(
        tuple(sorted(host_wall)) == host_wall and
        tuple(sorted(provider_wall)) == provider_wall and
        tuple(sorted(provider_monotonic)) == provider_monotonic and
        cutoff_document["completed_monotonic_ns"] <=
            challenge_document["issued_monotonic_ns"] <= now_monotonic_ns and
        payload.get("query_started_after_challenge") is True and
        policy["challenge_bound_query_supported"] is True and
        payload["query_completed_at_ms"] - payload["query_started_at_ms"] <=
            MAXIMUM_EVIDENCE_AGE_MS and
        payload["expires_at_ms"] - payload["query_completed_at_ms"] <=
            MAXIMUM_CHALLENGE_LIFETIME_MS, reason)
    consistency = payload.get("snapshot_consistency")
    _require(
        consistency in {"ATOMIC_ACCOUNT", "CAUSAL_WATERMARK"} and
        (consistency != "ATOMIC_ACCOUNT" or
         policy["atomic_account_supported"] is True) and
        (consistency != "CAUSAL_WATERMARK" or
         policy["causal_watermark_supported"] is True) and
        payload.get("consistency_cutoff_body_sha256") ==
            cutoff_document["body_sha256"] and
        payload.get("consistency_known_mutation_command_set_sha256") ==
            cutoff_document["known_mutation_command_set_sha256"] and
        payload.get("consistency_known_correlation_set_sha256") ==
            cutoff_document["known_correlation_set_sha256"] and
        payload.get("consistency_dominates_cutoff") is True and
        payload.get("consistency_dominates_all_mutations") is True, reason)
    _validate_terminal_zero_account_state(payload, reason)


def _validate_terminal_witness(
    document: dict[str, Any], *, cutoff: InputBinding,
    challenge: InputBinding, trust_policy: InputBinding,
    evidence: SignedEvidence, provider_request: FileBinding,
    provider_response: FileBinding, context: ProductionContext,
) -> None:
    reason = "TERMINAL_WITNESS_OUTPUT_INVALID"
    _sealed(document, TERMINAL_WITNESS_FIELDS, TERMINAL_WITNESS_SCHEMA, reason)
    payload = evidence.payload
    identity_fields = (
        "round", "domain", "campaign_id", "source_baseline_sha256",
        "cycle_id", "recovery_id", "finalization_id", "boot_id",
        "service_pid", "service_start_ticks", "broker_socket_identity_sha256",
        "account_id_sha256", "owner_ids", "owner_set_sha256",
        "owner_set_canonical_hex", "owner_count", "execution_service_epoch",
        "execution_service_fencing_generation", "mutation_fence_generation",
        "known_mutation_command_set_sha256", "known_mutation_command_count",
        "known_correlation_set_sha256", "known_correlation_count",
        "egress_policy_generation", "egress_policy_sha256")
    _require(
        document.get("status") == TERMINAL_WITNESS_STATUS and
        document.get("terminal_proof_kind") == TERMINAL_PROOF_KIND and
        all(document.get(field) == challenge.document.get(field)
            for field in identity_fields) and
        document.get("transport_cutoff_receipt") == cutoff.reference and
        document.get("challenge_reference") == challenge.reference and
        document.get("signed_evidence_reference") == evidence.reference and
        document.get("provider_trust_policy") == trust_policy.reference and
        document.get("provider_request_reference") == provider_request.reference
        and document.get("provider_response_reference") ==
            provider_response.reference and
        payload.get("provider_request_sha256") ==
            provider_request.reference["file_sha256"] and
        payload.get("provider_response_sha256") ==
            provider_response.reference["file_sha256"], reason)
    _validate_signature_proof(
        document.get("signature_verification"), evidence, context, reason)
    pairs = (
        "provider_id", "provider_key_sha256", "provider_capability",
        "provider_request_sha256", "provider_response_sha256",
        "query_started_at_ms", "query_started_monotonic_ns", "observed_at_ms",
        "observed_monotonic_ns", "query_completed_at_ms",
        "query_completed_monotonic_ns", "provider_clock_id", "provider_boot_id",
        "query_started_after_challenge", "snapshot_consistency",
        "consistency_token_sha256",
        "consistency_dominates_cutoff", "consistency_dominates_all_mutations",
        "active_orders_complete", "completed_orders_complete",
        "executions_complete", "positions_complete", "cash_fx_complete",
        "risk_complete", "gross_absolute_position", "gross_fx_exposure",
        "gross_risk", "settled_mutation_command_count",
        "unknown_mutation_command_count", "unresolved_mutation_command_count",
        "read_only_authority", "authoritative", "account_complete",
        "mutation_attempted")
    _require(all(document.get(field) == payload.get(field) for field in pairs),
             reason)
    _require(
        document.get("nonce") == challenge.document["nonce"] and
        document.get("active_order_count") ==
            len(payload["active_order_id_sha256s"]) == 0 and
        document.get("completed_order_count") ==
            len(payload["completed_order_id_sha256s"]) and
        document.get("execution_count") == len(payload["execution_id_sha256s"])
        and document.get("position_count") == len(payload["positions"]) == 0 and
        document.get("cash_fx_exposure_count") ==
            len(payload["cash_fx_exposures"]) == 0 and
        document.get("post_cutoff_boundary_verified") is True and
        document.get("egress_policy_generation_stable") is True and
        document.get("host_policy_sha256") ==
            cutoff.document["egress_policy_sha256"] and
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
        cutoff.document["completed_at_ms"] <= challenge.document["issued_at_ms"]
            <= document.get("received_at_ms") <=
            document.get("first_host_observed_at_ms") <=
            document.get("second_host_observed_at_ms") <=
            document.get("verified_at_ms") < document.get("expires_at_ms") and
        cutoff.document["completed_monotonic_ns"] <=
            challenge.document["issued_monotonic_ns"] <=
            document.get("received_monotonic_ns") <=
            document.get("verified_monotonic_ns"), reason)
    _terminal_owner_binding(document, reason)
    _false_boundary(document, reason)


def _parse_signed_evidence(binding: InputBinding) -> SignedEvidence:
    reason = "ZERO_EXPOSURE_SIGNED_EVIDENCE_INVALID"
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
        type(envelope.get("signature_base64")) is str, reason)
    try:
        signature = base64.b64decode(
            envelope["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeError, ValueError) as error:
        raise AttestationError(reason) from error
    _require(len(signature) == 64 and
             base64.b64encode(signature).decode("ascii") ==
                envelope["signature_base64"], reason)
    payload = dict(envelope["payload"])
    payload_bytes = canonical_bytes(payload)
    return SignedEvidence(
        binding, payload, payload_bytes, digest_bytes(payload_bytes),
        signature, digest_bytes(signature))


def _validate_legacy_profile_record(
    value: Any, reason: str, *, path: Path, sha256: str, size: int, mode: int,
    expected_uid: int, expected_gid: int,
) -> None:
    fields = {
        "path", "sha256", "bytes", "device", "inode", "mode", "nlink",
        "uid", "gid", "mtime_ns", "ctime_ns",
    }
    _require(isinstance(value, dict) and set(value) == fields, reason)
    _require(
        value.get("path") == str(path) and value.get("sha256") == sha256 and
        value.get("bytes") == size and value.get("mode") == stat.S_IFREG | mode
        and value.get("uid") == expected_uid and
        value.get("gid") == expected_gid and value.get("nlink") == 1 and
        all(type(value.get(field)) is int and value[field] >= 0 for field in (
            "device", "inode", "mtime_ns", "ctime_ns")) and
        value["inode"] > 0, reason)


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
    target = _read_profile_file(
        PAPER_PROFILE_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid, mode=0o644,
        sha256=PAPER_PROFILE_DORMANT_SHA256,
        size=PAPER_PROFILE_DORMANT_BYTES)
    backup = _read_profile_file(
        PAPER_PROFILE_DORMANT_BACKUP_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_PROFILE_DORMANT_SHA256,
        size=PAPER_PROFILE_DORMANT_BYTES)
    retained = _read_profile_file(
        PAPER_PROFILE_FORWARD_RETAINED_PATH, reason,
        expected_uid=expected_uid, expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_PROFILE_DORMANT_SHA256,
        size=PAPER_PROFILE_DORMANT_BYTES)
    retired = _read_profile_file(
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
        } and all(preimage.get(field) is False for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access")), reason)
    for document in (transition, preimage):
        _validate_legacy_profile_record(
            document.get("backup"), reason,
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
    target = _read_profile_file(
        PAPER_RUNTIME_PROFILE_PATH, reason, expected_uid=expected_uid,
        expected_gid=expected_gid, mode=0o644,
        sha256=PAPER_RUNTIME_PROFILE_HARDENED_SHA256,
        size=PAPER_RUNTIME_PROFILE_HARDENED_BYTES)
    backup = _read_profile_file(
        PAPER_RUNTIME_PROFILE_BACKUP_PATH, reason,
        expected_uid=expected_uid, expected_gid=expected_gid, mode=0o600,
        sha256=PAPER_RUNTIME_PROFILE_LEGACY_SHA256,
        size=PAPER_RUNTIME_PROFILE_LEGACY_BYTES)
    retained = _read_profile_file(
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


def _validate_handoff_v2_host_binding(
    handoff: Mapping[str, Any], context: ProductionContext,
) -> None:
    reason = "ZERO_EXPOSURE_HANDOFF_INVALID"
    _require(
        handoff.get("version") == HANDOFF_VERSION and
        handoff.get("status") == "WATCH_RETIRED_HANDOFF_COMPLETE" and
        handoff.get("global_kill_switch_engaged") is True and
        handoff.get("identity_count") == 0 and
        handoff.get("identity_manifest_sha256") ==
            DISABLED_IDENTITY_MANIFEST_SHA256 and
        handoff.get("paper_profile_restored") is True and
        handoff.get("profile_candidate_absent") is True and
        handoff.get("paper_runtime_profile_hardened") is True and
        handoff.get("paper_runtime_profile_candidate_absent") is True,
        reason)
    _validate_profile_restoration(
        handoff.get("paper_profile_restoration"), reason,
        expected_uid=context.expected_uid, expected_gid=context.expected_gid)
    _validate_runtime_profile_hardening(
        handoff.get("paper_runtime_profile_hardening"), reason,
        expected_uid=context.expected_uid, expected_gid=context.expected_gid)
    identity_payload, _, _ = secure_read(
        IDENTITY_MANIFEST_PATH, reason, expected_uid=context.expected_uid,
        expected_gid=context.expected_gid, modes=frozenset({0o600}),
        maximum=64 * 1024)
    _require(digest_bytes(identity_payload) ==
             DISABLED_IDENTITY_MANIFEST_SHA256, reason)
    _read_kill_switch(
        KILL_SWITCH_PATH, reason, expected_uid=context.expected_uid,
        expected_file_gid=PAPER_CONTROL_GID,
        expected_parent_gid=PAPER_CONTROL_GID)
    _read_kill_switch(
        GLOBAL_KILL_SWITCH_PATH, reason, expected_uid=context.expected_uid,
        expected_file_gid=GLOBAL_PAPER_CONTROL_GID,
        expected_parent_gid=GLOBAL_PAPER_CONTROL_GID)


def _validate_signature_proof(
    value: Any, evidence: SignedEvidence, context: ProductionContext,
    reason: str,
) -> None:
    _require(isinstance(value, dict) and
             set(value) == SIGNATURE_PROOF_FIELDS, reason)
    _require(
        value.get("algorithm") == SIGNATURE_ALGORITHM and
        _executable_reference(value.get("public_key"), reason) ==
            context.verification_key.reference and
        _executable_reference(value.get("verifier"), reason) ==
            context.signature_verifier.reference and
        _digest(value.get("signature_sha256"), reason) ==
            evidence.signature_sha256 and
        _digest(value.get("signed_payload_sha256"), reason) ==
            evidence.payload_sha256, reason)


def _load_inputs(
    paths: Mapping[str, Path], *, expected_uid: int, expected_gid: int,
) -> dict[str, InputBinding]:
    definitions: dict[str, tuple[frozenset[str] | None, str | None, str]] = {
        "reservation": (
            RESERVATION_FIELDS, RESERVATION_SCHEMA,
            "ZERO_EXPOSURE_RESERVATION_INVALID"),
        "intent": (INTENT_FIELDS, INTENT_SCHEMA,
                   "ZERO_EXPOSURE_OPERATOR_INTENT_INVALID"),
        "handoff": (HANDOFF_FIELDS, HANDOFF_SCHEMA,
                    "ZERO_EXPOSURE_HANDOFF_INVALID"),
        "challenge": (CHALLENGE_FIELDS, CHALLENGE_SCHEMA,
                      "ZERO_EXPOSURE_CHALLENGE_INVALID"),
        "evidence": (None, None, "ZERO_EXPOSURE_SIGNED_EVIDENCE_INVALID"),
        "broker": (BROKER_SNAPSHOT_FIELDS, BROKER_SNAPSHOT_SCHEMA,
                   "ZERO_EXPOSURE_BROKER_SNAPSHOT_INVALID"),
        "account": (ACCOUNT_SNAPSHOT_FIELDS, ACCOUNT_SNAPSHOT_SCHEMA,
                    "ZERO_EXPOSURE_ACCOUNT_SNAPSHOT_INVALID"),
    }
    result = {
        name: _bind_document(
            paths[name], fields, schema, reason,
            expected_uid=expected_uid, expected_gid=expected_gid)
        for name, (fields, schema, reason) in definitions.items()
    }
    reason = "ZERO_EXPOSURE_RESERVATION_LINEAGE_INVALID"
    directory = _open_directory(HOST_AUTHORITY_DIRECTORY, reason)
    try:
        _trusted_parent(
            directory, expected_uid=expected_uid,
            expected_gid=expected_gid, reason=reason)
        names = os.listdir(directory)
    except OSError as error:
        raise AttestationError(reason) from error
    finally:
        os.close(directory)
    pointer_path = reservation_current_pointer_path()
    if pointer_path.name in names:
        result["lineage_pointer"] = _bind_document(
            pointer_path, RESERVATION_CURRENT_POINTER_FIELDS,
            RESERVATION_CURRENT_POINTER_SCHEMA, reason,
            expected_uid=expected_uid, expected_gid=expected_gid)
    prefix = "finalized.zero-exposure-"
    suffix = ".v1.json"
    tombstone_names = sorted(
        name for name in names
        if name.startswith(prefix) and name.endswith(suffix))
    for index, name in enumerate(tombstone_names, start=1):
        result[f"lineage_tombstone_{index}"] = _bind_document(
            HOST_AUTHORITY_DIRECTORY / name,
            RESERVATION_FINALIZATION_FIELDS, RESERVATION_FINALIZATION_SCHEMA,
            reason, expected_uid=expected_uid, expected_gid=expected_gid)
    return result


def _expected_pointer_document(
    tombstone: InputBinding,
) -> dict[str, Any]:
    document = tombstone.document
    boundary = {field: False for field in BOUNDARY_FIELDS}
    return seal({
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


def _expected_pointer_reference(
    tombstone: InputBinding,
) -> dict[str, str]:
    document = _expected_pointer_document(tombstone)
    payload = canonical_bytes(document)
    return {
        "path": str(reservation_current_pointer_path()),
        "file_sha256": digest_bytes(payload),
        "body_sha256": document["body_sha256"],
    }


def _validate_reservation_lineage(
    inputs: Mapping[str, InputBinding], reservation: Mapping[str, Any],
    lease_reference: Mapping[str, Any],
) -> None:
    reason = "ZERO_EXPOSURE_RESERVATION_LINEAGE_INVALID"
    tombstones = sorted(
        (binding for name, binding in inputs.items()
         if name.startswith("lineage_tombstone_")),
        key=lambda binding: binding.document["reservation_generation"])
    previous: InputBinding | None = None
    previous_pointer_reference: dict[str, str] | None = None
    for generation, tombstone in enumerate(tombstones, start=1):
        document = tombstone.document
        reservation_id = _identifier(
            document.get("reservation_id"), RESERVATION_ID, reason)
        predecessor = document.get(
            "predecessor_finalization_body_sha256")
        if predecessor is not None:
            _digest(predecessor, reason)
        _require(
            tombstone.path == reservation_tombstone_path(reservation_id) and
            document.get("status") in {
                "ABORTED", "ADMISSION_GO", "ADMISSION_NO_GO",
                "ADMISSION_HALT"} and
            document.get("round") == ROUND and
            document.get("domain") == DOMAIN_ID and
            document.get("reservation_generation") == generation and
            predecessor == (None if previous is None else
                            previous.document["body_sha256"]) and
            document.get("prior_finalization_pointer_reference") ==
                previous_pointer_reference and
            document.get("boot_id") == lease_reference.get("boot_id") and
            document.get("host_authority_lease") == dict(lease_reference) and
            document.get("owner_present_at_tombstone_commit") is True and
            document.get("owner_removal_required_after_commit") is True and
            document.get("finalization_order") ==
                RESERVATION_FINALIZATION_ORDER,
            reason)
        _identifier(document.get("campaign_id"), IDENTIFIER, reason)
        _digest(document.get("source_baseline_sha256"), reason)
        _reservation_reference(document.get("reservation_reference"), reason)
        _false_boundary(document, reason)
        if document["status"] == "ABORTED":
            _require(
                document.get("candidate_reference") is None and
                document.get("zero_exposure_receipt_reference") is None and
                document.get("recovery_reason") in {
                    "CHALLENGE_NOT_PUBLISHED", "RESERVATION_EXPIRED"} and
                isinstance(document.get("recovery_observation"), dict),
                reason)
        else:
            _reference(document.get("candidate_reference"), reason)
            _reference(
                document.get("zero_exposure_receipt_reference"), reason)
            _require(
                document.get("recovery_reason") is None and
                document.get("recovery_observation") is None,
                reason)
        previous = tombstone
        previous_pointer_reference = _expected_pointer_reference(tombstone)
    pointer = inputs.get("lineage_pointer")
    if previous is None:
        _require(pointer is None, reason)
    else:
        _require(pointer is not None and
                 pointer.path == reservation_current_pointer_path() and
                 pointer.document == _expected_pointer_document(previous) and
                 pointer.reference == previous_pointer_reference, reason)
    _require(
        reservation.get("reservation_generation") == len(tombstones) + 1 and
        reservation.get("predecessor_finalization_body_sha256") ==
            (None if previous is None else
             previous.document["body_sha256"]) and
        reservation.get("prior_finalization_pointer_reference") ==
            previous_pointer_reference,
        reason)


def _validate_chain(
    inputs: Mapping[str, InputBinding], evidence: SignedEvidence,
    context: ProductionContext, lease_reference: Mapping[str, Any],
    *, expected_source: str, expected_domain: str,
    expected_campaign: str, now_ms: int,
) -> tuple[dict[str, Any], list[str], list[str], list[int]]:
    intent = inputs["intent"].document
    handoff = inputs["handoff"].document
    reservation = inputs["reservation"].document
    challenge = inputs["challenge"].document
    broker = inputs["broker"].document
    account = inputs["account"].document
    payload = evidence.payload
    readiness: list[str] = []
    dangers: list[str] = []
    expiries: list[int] = []

    reservation_reason = "ZERO_EXPOSURE_RESERVATION_INVALID"
    reservation_reference = inputs["reservation"].reservation_reference()
    reservation_id = _identifier(
        reservation.get("reservation_id"), RESERVATION_ID,
        reservation_reason)
    _validate_reservation_lineage(inputs, reservation, lease_reference)
    _require(
        reservation.get("status") == "ACTIVE" and
        type(reservation.get("reservation_generation")) is int and
        reservation.get("reservation_generation") >= 1 and
        reservation.get("reservation_owner_kind") ==
            "ZERO_EXPOSURE_ADMISSION_EVIDENCE" and
        reservation.get("reservation_lifecycle") == RESERVATION_LIFECYCLE and
        reservation.get("next_consumer") == RESERVATION_NEXT_CONSUMER and
        reservation.get("boot_id") == lease_reference.get("boot_id") and
        reservation.get("producer") == context.snapshot_producer.reference and
        reservation.get("production_mode") == SNAPSHOT_PRODUCTION_MODE and
        reservation.get("operator_intent_reference") ==
            inputs["intent"].reference and
        reservation.get("watch_handoff_receipt") ==
            inputs["handoff"].reference and
        reservation.get("host_authority_lease") == dict(lease_reference) and
        reservation.get("finalization_tombstone_path") == str(
            reservation_tombstone_path(reservation_id)) and
        reservation.get("finalization_current_pointer_path") == str(
            reservation_current_pointer_path()) and
        reservation.get("finalization_tombstone_absent") is True,
        reservation_reason)
    _identifier(reservation.get("request_nonce"), NONCE, reservation_reason)
    _digest(reservation.get("account_id_sha256"), reservation_reason)
    reservation_paths = {
        "challenge_output_path": inputs["challenge"].path,
        "signed_account_evidence_path": inputs["evidence"].path,
        "broker_snapshot_output_path": inputs["broker"].path,
        "account_snapshot_output_path": inputs["account"].path,
    }
    for field, expected_path in reservation_paths.items():
        value = reservation.get(field)
        _require(type(value) is str and
                 _canonical_path(Path(value), reservation_reason) ==
                    expected_path, reservation_reason)

    reason = "ZERO_EXPOSURE_PRODUCER_BINDING_INVALID"
    _require(_executable_reference(intent.get("producer"), reason) ==
             context.snapshot_producer.reference, reason)
    _require(_executable_reference(intent.get("broker_policy_helper"), reason)
             == context.broker_helper.reference, reason)
    _require(_executable_reference(intent.get("signature_verifier"), reason)
             == context.signature_verifier.reference, reason)
    _require(_executable_reference(intent.get("verification_key"), reason) ==
             context.verification_key.reference, reason)
    _require(intent.get("production_mode") == SNAPSHOT_PRODUCTION_MODE and
             intent.get("status") == "APPROVED" and
             intent.get("allow_fixed_read_only_host_observation") is True and
             intent.get("allow_offline_signed_account_adaptation") is True,
             "ZERO_EXPOSURE_OPERATOR_INTENT_INVALID")
    _identifier(intent.get("intent_id"), IDENTIFIER,
                "ZERO_EXPOSURE_OPERATOR_INTENT_INVALID")
    _digest(intent.get("account_id_sha256"),
            "ZERO_EXPOSURE_OPERATOR_INTENT_INVALID")
    intent_paths = {
        "handoff": "watch_handoff_receipt_path",
        "challenge": "challenge_output_path",
        "evidence": "signed_account_evidence_path",
        "broker": "broker_snapshot_output_path",
        "account": "account_snapshot_output_path",
    }
    for name, field in intent_paths.items():
        value = intent.get(field)
        _require(type(value) is str and
                 _canonical_path(Path(value),
                                 "ZERO_EXPOSURE_OPERATOR_INTENT_INVALID") ==
                    inputs[name].path,
                 "ZERO_EXPOSURE_OPERATOR_INTENT_INVALID")

    chain_reason = "ZERO_EXPOSURE_CHAIN_REFERENCE_INVALID"
    _require(
        _reference(challenge.get("operator_intent_reference"), chain_reason) ==
            inputs["intent"].reference and
        _reference(challenge.get("watch_handoff_receipt"), chain_reason) ==
            inputs["handoff"].reference and
        _reservation_reference(
            challenge.get("host_authority_reservation"), chain_reason) ==
            reservation_reference, chain_reason)
    for document in (broker, account):
        _require(
            _executable_reference(document.get("producer"), reason) ==
                context.snapshot_producer.reference and
            document.get("production_mode") == SNAPSHOT_PRODUCTION_MODE and
            _reference(document.get("operator_intent_reference"), reason) ==
                inputs["intent"].reference and
            _reference(document.get("watch_handoff_receipt"), reason) ==
                inputs["handoff"].reference and
            _reference(document.get("challenge_reference"), reason) ==
                inputs["challenge"].reference and
            _reservation_reference(
                document.get("host_authority_reservation"), reason) ==
                reservation_reference, reason)
    _require(
        _signed_reference(account.get("signed_evidence_reference"), reason) ==
            evidence.reference and
        broker.get("signed_account_payload_sha256") ==
            evidence.payload_sha256, reason)
    _validate_signature_proof(
        account.get("signature_verification"), evidence, context,
        "ZERO_EXPOSURE_SIGNATURE_PROOF_INVALID")

    _require(
        _executable_reference(handoff.get("producer"),
                              "ZERO_EXPOSURE_HANDOFF_INVALID") ==
            context.handoff_producer.reference and
        handoff.get("production_mode") == HANDOFF_PRODUCTION_MODE,
        "ZERO_EXPOSURE_HANDOFF_INVALID")
    _validate_handoff_v2_host_binding(handoff, context)
    _reference(handoff.get("activation_receipt"),
               "ZERO_EXPOSURE_HANDOFF_INVALID")
    _reference(handoff.get("p1_audit_receipt"),
               "ZERO_EXPOSURE_HANDOFF_INVALID")
    _reference(handoff.get("freeze_bundle"),
               "ZERO_EXPOSURE_HANDOFF_INVALID")

    _require(
        challenge.get("status") == "AWAITING_SIGNED_RESPONSE" and
        challenge.get("producer") == context.snapshot_producer.reference and
        challenge.get("production_mode") == SNAPSHOT_PRODUCTION_MODE and
        challenge.get("signature_algorithm") == SIGNATURE_ALGORITHM and
        challenge.get("signature_verifier") ==
            context.signature_verifier.reference and
        challenge.get("verification_key") ==
            context.verification_key.reference and
        challenge.get("required_observation_authority") ==
            REMOTE_OBSERVATION_AUTHORITY and
        challenge.get("nonce") == reservation.get("request_nonce") and
        challenge.get("account_id_sha256") ==
            reservation.get("account_id_sha256") and
        challenge.get("issued_at_ms") == reservation.get("issued_at_ms") and
        challenge.get("expires_at_ms") == reservation.get("expires_at_ms"),
        "ZERO_EXPOSURE_CHALLENGE_INVALID")
    nonce = _identifier(
        challenge.get("nonce"), NONCE, "ZERO_EXPOSURE_CHALLENGE_INVALID")
    _require(
        payload.get("status") == "COMPLETE" and
        payload.get("nonce") == nonce and
        payload.get("challenge_body_sha256") ==
            challenge.get("body_sha256") and
        payload.get("account_id_sha256") ==
            challenge.get("account_id_sha256") ==
            intent.get("account_id_sha256") and
        payload.get("observation_authority") ==
            REMOTE_OBSERVATION_AUTHORITY and
        payload.get("query_effect") == REMOTE_QUERY_EFFECT and
        payload.get("read_only_authority") is True and
        payload.get("authoritative") is True and
        payload.get("account_complete") is True,
        "ZERO_EXPOSURE_SIGNED_EVIDENCE_INVALID")
    _identifier(payload.get("provider_id"), IDENTIFIER,
                "ZERO_EXPOSURE_SIGNED_EVIDENCE_INVALID")
    for field in (
        "account_id_sha256", "provider_request_id_sha256",
        "provider_response_sha256", "challenge_body_sha256",
    ):
        _digest(payload.get(field), "ZERO_EXPOSURE_SIGNED_EVIDENCE_INVALID")
    _validate_account_state(payload, "ZERO_EXPOSURE_SIGNED_EVIDENCE_INVALID")

    expected_lineage = (
        ROUND, expected_domain, expected_campaign, expected_source)
    for document in (
        intent, handoff, reservation, challenge, payload, broker, account,
    ):
        lineage = (document.get("round"), document.get("domain"),
                   document.get("campaign_id"),
                   document.get("source_baseline_sha256"))
        if lineage != expected_lineage:
            dangers.append("ZERO_EXPOSURE_LINEAGE_MISMATCH_DANGEROUS")

    account_payload_fields = (
        "provider_id", "account_id_sha256", "provider_request_id_sha256",
        "provider_response_sha256", "query_epoch",
        "query_fencing_generation", "query_invocation_id", "snapshot_sha256",
        "active_order_id_sha256s", "positions", "gross_absolute_position",
        "authorized_connector_count", "end_flat",
    )
    _require(all(account.get(field) == payload.get(field)
                 for field in account_payload_fields),
             "ZERO_EXPOSURE_ACCOUNT_PAYLOAD_BINDING_INVALID")
    _validate_account_state(account,
                            "ZERO_EXPOSURE_ACCOUNT_SNAPSHOT_INVALID")
    _require(
        broker.get("request_nonce") == account.get("request_nonce") == nonce and
        broker.get("account_id_sha256") == account.get("account_id_sha256") ==
            payload.get("account_id_sha256") and
        broker.get("broker_policy_helper") == context.broker_helper.reference,
        "ZERO_EXPOSURE_SNAPSHOT_PAIR_BINDING_INVALID")

    time_documents = (
        (intent, "issued_at_ms", "ZERO_EXPOSURE_OPERATOR_INTENT_STALE"),
        (handoff, "issued_at_ms", "ZERO_EXPOSURE_HANDOFF_STALE"),
        (reservation, "issued_at_ms", "ZERO_EXPOSURE_RESERVATION_STALE"),
        (challenge, "issued_at_ms", "ZERO_EXPOSURE_CHALLENGE_STALE"),
        (payload, "observed_at_ms", "ZERO_EXPOSURE_SIGNED_EVIDENCE_STALE"),
        (broker, "observed_at_ms", "ZERO_EXPOSURE_BROKER_SNAPSHOT_STALE"),
        (account, "observed_at_ms", "ZERO_EXPOSURE_ACCOUNT_SNAPSHOT_STALE"),
    )
    for document, start_field, stale_reason in time_documents:
        _, expiry, current = _time_current(
            document.get(start_field), document.get("expires_at_ms"), now_ms,
            "ZERO_EXPOSURE_TIME_INVALID")
        expiries.append(expiry)
        if not current:
            readiness.append(stale_reason)
    _require(challenge["expires_at_ms"] - challenge["issued_at_ms"] <=
             MAXIMUM_CHALLENGE_LIFETIME_MS,
             "ZERO_EXPOSURE_CHALLENGE_INVALID")
    _require(
        challenge["issued_at_ms"] <= payload["observed_at_ms"],
        "ZERO_EXPOSURE_SIGNED_EVIDENCE_PRECHALLENGE")
    if now_ms - payload["observed_at_ms"] > MAXIMUM_EVIDENCE_AGE_MS:
        readiness.append("ZERO_EXPOSURE_SIGNED_EVIDENCE_TOO_OLD")
    if abs(broker["observed_at_ms"] - account["observed_at_ms"]) > \
            MAXIMUM_OBSERVATION_SKEW_MS:
        readiness.append("ZERO_EXPOSURE_OBSERVATION_WINDOW_TOO_WIDE")

    for field in (
        "watch_units_inactive", "paper_units_inactive", "broker_deny_all",
        "kill_switch_engaged", "global_kill_switch_engaged",
        "paper_profile_restored", "profile_candidate_absent",
        "crash_recovery_verified", *BOUNDARY_FIELDS,
    ):
        _require(type(handoff.get(field)) is bool,
                 "ZERO_EXPOSURE_HANDOFF_INVALID")
    for field in (
        "watch_authority_count", "watch_socket_count", "watch_timer_count",
        "cleanup_residue_count", "identity_count",
    ):
        _integer(handoff.get(field), "ZERO_EXPOSURE_HANDOFF_INVALID")
    handoff_complete = (
        handoff.get("status") == "WATCH_RETIRED_HANDOFF_COMPLETE" and
        handoff["watch_units_inactive"] is True and
        handoff["watch_authority_count"] == 0 and
        handoff["watch_socket_count"] == 0 and
        handoff["watch_timer_count"] == 0 and
        handoff["paper_units_inactive"] is True and
        handoff["broker_deny_all"] is True and
        handoff["kill_switch_engaged"] is True and
        handoff["global_kill_switch_engaged"] is True and
        handoff["identity_count"] == 0 and
        handoff["identity_manifest_sha256"] ==
            DISABLED_IDENTITY_MANIFEST_SHA256 and
        handoff["paper_profile_restored"] is True and
        handoff["profile_candidate_absent"] is True and
        handoff["crash_recovery_verified"] is True and
        handoff["cleanup_residue_count"] == 0)
    if not handoff_complete:
        readiness.append("ZERO_EXPOSURE_HANDOFF_NOT_COMPLETE")

    _require(
        broker.get("status") in {"PASS", "NO_GO", "HALT"} and
        broker.get("observation_method") == BROKER_OBSERVATION_METHOD and
        broker.get("observer_id") == BROKER_OBSERVER_ID and
        broker.get("protected_broker_ports") == list(PROTECTED_BROKER_PORTS),
        "ZERO_EXPOSURE_BROKER_SNAPSHOT_INVALID")
    _digest(broker.get("policy_sha256"),
            "ZERO_EXPOSURE_BROKER_SNAPSHOT_INVALID")
    for field in (
        "observation_complete", "broker_deny_all", "paper_units_inactive",
        "kill_switch_engaged", "process_inventory_complete",
        "socket_inventory_complete", "credential_inventory_complete",
        *BOUNDARY_FIELDS,
    ):
        _require(type(broker.get(field)) is bool,
                 "ZERO_EXPOSURE_BROKER_SNAPSHOT_INVALID")
    for field in (
        "authorized_connectors", "broker_socket_count", "broker_process_count",
        "credential_exposure_count",
    ):
        _integer(broker.get(field),
                 "ZERO_EXPOSURE_BROKER_SNAPSHOT_INVALID")
    uids = broker.get("authorized_uids")
    _require(isinstance(uids, list) and len(uids) <= 1024 and
             all(type(uid) is int and uid >= 0 for uid in uids) and
             uids == sorted(set(uids)),
             "ZERO_EXPOSURE_BROKER_SNAPSHOT_INVALID")
    broker_exposure = (
        broker["authorized_connectors"] > 0 or bool(uids) or
        broker["broker_socket_count"] > 0 or
        broker["broker_process_count"] > 0 or
        broker["credential_exposure_count"] > 0)
    if broker_exposure or broker["status"] == "HALT":
        dangers.append("ZERO_EXPOSURE_BROKER_EXPOSURE_DANGEROUS")
    broker_ready = (
        broker["status"] == "PASS" and
        broker["observation_complete"] is True and
        broker["broker_deny_all"] is True and
        broker["paper_units_inactive"] is True and
        broker["kill_switch_engaged"] is True and
        broker["process_inventory_complete"] is True and
        broker["socket_inventory_complete"] is True and
        broker["credential_inventory_complete"] is True)
    if not broker_ready:
        readiness.append("ZERO_EXPOSURE_BROKER_NOT_READY")

    _require(
        account.get("status") in {"COMPLETE", "UNVERIFIED"} and
        account.get("observer_id") == ACCOUNT_OBSERVER_ID and
        account.get("observation_authority") ==
            REMOTE_OBSERVATION_AUTHORITY and
        account.get("query_effect") == REMOTE_QUERY_EFFECT,
        "ZERO_EXPOSURE_ACCOUNT_SNAPSHOT_INVALID")
    for field in (
        "read_only_authority", "authoritative", "account_complete",
        "end_flat", *BOUNDARY_FIELDS,
    ):
        _require(type(account.get(field)) is bool,
                 "ZERO_EXPOSURE_ACCOUNT_SNAPSHOT_INVALID")
    orders = account["active_order_id_sha256s"]
    positions = account["positions"]
    account_exposure = (
        bool(orders) or bool(positions) or
        account["gross_absolute_position"] > 0 or
        account["authorized_connector_count"] > 0)
    if account_exposure:
        dangers.append("ZERO_EXPOSURE_ACCOUNT_EXPOSURE_DANGEROUS")
    account_ready = (
        account["status"] == "COMPLETE" and
        account["read_only_authority"] is True and
        account["authoritative"] is True and
        account["account_complete"] is True)
    if not account_ready:
        readiness.append("ZERO_EXPOSURE_ACCOUNT_NOT_AUTHORITATIVE")

    for document, prefix in (
        (intent, "ZERO_EXPOSURE_INTENT"),
        (handoff, "ZERO_EXPOSURE_HANDOFF"),
        (challenge, "ZERO_EXPOSURE_CHALLENGE"),
        (reservation, "ZERO_EXPOSURE_RESERVATION"),
        (payload, "ZERO_EXPOSURE_SIGNED_EVIDENCE"),
        (broker, "ZERO_EXPOSURE_BROKER"),
        (account, "ZERO_EXPOSURE_ACCOUNT"),
    ):
        dangers.extend(_boundary_findings(document, prefix))

    historical_lease = _validate_historical_lease(
        broker.get("host_authority_lease"),
        expected_uid=context.expected_uid, expected_gid=context.expected_gid)
    _require(dict(lease_reference) == historical_lease,
             "ZERO_EXPOSURE_HOST_AUTHORITY_LEASE_IDENTITY_MISMATCH")
    facts = {
        "intent": intent, "handoff": handoff, "reservation": reservation,
        "challenge": challenge,
        "broker": broker, "account": account, "payload": payload,
    }
    return facts, sorted(set(readiness)), sorted(set(dangers)), expiries


def _validate_signature_attestation(value: Any, reason: str) -> None:
    _require(isinstance(value, dict) and
             set(value) == SIGNATURE_ATTESTATION_FIELDS, reason)
    _require(
        value.get("algorithm") == SIGNATURE_ALGORITHM and
        value.get("return_code") == 0 and
        value.get("stdout") == "Signature Verified Successfully\n" and
        value.get("stderr") == "" and
        value.get("stdout_sha256") ==
            digest_bytes(b"Signature Verified Successfully\n") and
        value.get("stderr_sha256") == digest_bytes(b""), reason)
    _executable_reference(value.get("public_key"), reason)
    _executable_reference(value.get("verifier"), reason)
    _digest(value.get("signature_sha256"), reason)
    _digest(value.get("signed_payload_sha256"), reason)


def validate_output(document: Any) -> dict[str, Any]:
    reason = "ZERO_EXPOSURE_OUTPUT_INVALID"
    _require(isinstance(document, dict), reason)
    _sealed(document, OUTPUT_FIELDS, OUTPUT_SCHEMA, reason)
    _require(
        document.get("round") == ROUND and document.get("domain") == DOMAIN_ID
        and document.get("status") in {"PASS", "NO_GO", "HALT"} and
        document.get("production_mode") == PRODUCTION_MODE and
        document.get("snapshot_production_mode") ==
            SNAPSHOT_PRODUCTION_MODE, reason)
    _identifier(document.get("campaign_id"), IDENTIFIER, reason)
    _identifier(document.get("intent_id"), IDENTIFIER, reason)
    _digest(document.get("source_baseline_sha256"), reason)
    producer = _executable_reference(document.get("producer"), reason)
    snapshot_producer = _executable_reference(
        document.get("snapshot_producer"), reason)
    broker_helper = _executable_reference(
        document.get("broker_policy_helper"), reason)
    _require(producer["path"] == str(INSTALLED_EXECUTABLE) and
             snapshot_producer["path"] ==
                str(SNAPSHOT_PRODUCER_EXECUTABLE) and
             broker_helper["path"] == str(BROKER_POLICY_HELPER), reason)
    for field in (
        "operator_intent_reference", "watch_handoff_receipt",
        "challenge_reference", "broker_boundary_reference",
        "authoritative_state_reference",
    ):
        _reference(document.get(field), reason)
    reservation_reference = _reservation_reference(
        document.get("host_authority_reservation"), reason)
    reservation_id = _identifier(
        document.get("reservation_id"), RESERVATION_ID, reason)
    generation = _integer(
        document.get("reservation_generation"), reason, minimum=1)
    predecessor = document.get(
        "reservation_predecessor_finalization_body_sha256")
    prior_pointer = document.get(
        "reservation_prior_finalization_pointer_reference")
    if generation == 1:
        _require(predecessor is None and prior_pointer is None, reason)
    else:
        _digest(predecessor, reason)
        _reference(prior_pointer, reason)
    _require(
        document.get("reservation_lifecycle") == RESERVATION_LIFECYCLE and
        document.get("reservation_next_consumer") ==
            RESERVATION_NEXT_CONSUMER and
        document.get("reservation_finalization_tombstone_path") == str(
            reservation_tombstone_path(reservation_id)) and
        document.get("reservation_finalization_current_pointer_path") == str(
            reservation_current_pointer_path()) and
        document.get("reservation_finalization_schema") ==
            RESERVATION_FINALIZATION_SCHEMA and
        document.get("reservation_finalization_order") ==
            RESERVATION_FINALIZATION_ORDER,
        reason)
    _identifier(document.get("reservation_boot_id"), BOOT_ID, reason)
    for field in ("reservation_lease_device", "reservation_lease_inode"):
        _require(_integer(document.get(field), reason) > 0, reason)
    _signed_reference(document.get("signed_evidence_reference"), reason)
    _validate_signature_attestation(
        document.get("signature_verification"), reason)
    _identifier(document.get("request_nonce"), NONCE, reason)
    _digest(document.get("account_id_sha256"), reason)
    _identifier(document.get("provider_id"), IDENTIFIER, reason)
    _digest(document.get("provider_request_id_sha256"), reason)
    _digest(document.get("provider_response_sha256"), reason)
    _identifier(document.get("query_epoch"), IDENTIFIER, reason)
    _integer(document.get("query_fencing_generation"), reason, 1)
    _identifier(document.get("query_invocation_id"), IDENTIFIER, reason)
    _digest(document.get("snapshot_sha256"), reason)
    _digest(document.get("policy_sha256"), reason)
    _require(
        document.get("observation_method") == BROKER_OBSERVATION_METHOD and
        document.get("broker_observer_id") == BROKER_OBSERVER_ID and
        document.get("account_observer_id") == ACCOUNT_OBSERVER_ID and
        document.get("observation_authority") ==
            REMOTE_OBSERVATION_AUTHORITY and
        document.get("query_effect") == REMOTE_QUERY_EFFECT and
        document.get("protected_broker_ports") == list(PROTECTED_BROKER_PORTS),
        reason)
    observed = _integer(document.get("observed_at_ms"), reason)
    expires = _integer(document.get("expires_at_ms"), reason)
    _require(observed < expires, reason)
    for field in (
        "authorized_connectors", "broker_socket_count", "broker_process_count",
        "credential_exposure_count", "order_count", "position_count",
        "gross_absolute_position",
    ):
        _integer(document.get(field), reason)
    uids = document.get("authorized_uids")
    _require(isinstance(uids, list) and
             all(type(uid) is int and uid >= 0 for uid in uids) and
             uids == sorted(set(uids)), reason)
    for field in (
        "read_only_authority", "authoritative", "account_complete",
        "observation_complete", "broker_deny_all", "end_flat",
        "paper_units_inactive", "kill_switch_engaged",
        "process_inventory_complete", "socket_inventory_complete",
        "credential_inventory_complete", "host_authority_lease_reacquired",
        "reservation_continuity_verified",
        "reservation_finalization_tombstone_absent",
        *BOUNDARY_FIELDS,
    ):
        _require(type(document.get(field)) is bool, reason)
    _require(all(document[field] is False for field in BOUNDARY_FIELDS), reason)
    lease = document.get("host_authority_lease")
    _require(isinstance(lease, dict), reason)
    _validate_historical_lease(
        lease, expected_uid=_integer(lease.get("directory_uid"), reason),
        expected_gid=_integer(lease.get("directory_gid"), reason))
    _require(
        reservation_reference["path"] == str(HOST_AUTHORITY_OWNER_PATH) and
        document["reservation_boot_id"] == lease["boot_id"] and
        document["reservation_lease_device"] == lease["lease_device"] and
        document["reservation_lease_inode"] == lease["lease_inode"], reason)
    if document["status"] == "PASS":
        _require(
            document["read_only_authority"] is True and
            document["authoritative"] is True and
            document["account_complete"] is True and
            document["observation_complete"] is True and
            document["broker_deny_all"] is True and
            document["authorized_connectors"] == 0 and uids == [] and
            document["broker_socket_count"] == 0 and
            document["broker_process_count"] == 0 and
            document["credential_exposure_count"] == 0 and
            document["order_count"] == 0 and
            document["position_count"] == 0 and
            document["gross_absolute_position"] == 0 and
            document["end_flat"] is True and
            document["paper_units_inactive"] is True and
            document["kill_switch_engaged"] is True and
            document["process_inventory_complete"] is True and
            document["socket_inventory_complete"] is True and
            document["credential_inventory_complete"] is True and
            document["host_authority_lease_reacquired"] is True and
            document["reservation_continuity_verified"] is True and
            document["reservation_finalization_tombstone_absent"] is True,
            reason)
    return document


def _assert_stable(
    inputs: Mapping[str, InputBinding], context: ProductionContext,
    lease: HostAuthorityLease,
) -> dict[str, Any]:
    for binding in inputs.values():
        binding.reopen(expected_uid=context.expected_uid,
                       expected_gid=context.expected_gid)
    context.reopen()
    return context.validate_lease(lease, inputs["reservation"])


def _build_attestation(
    *, inputs: Mapping[str, InputBinding], context: ProductionContext,
    lease: HostAuthorityLease, expected_source: str,
    expected_domain: str, expected_campaign: str, now_ms: int,
) -> dict[str, Any]:
    evidence = _parse_signed_evidence(inputs["evidence"])
    lease_reference = _assert_stable(inputs, context, lease)
    facts, readiness, dangers, expiries = _validate_chain(
        inputs, evidence, context, lease_reference,
        expected_source=expected_source, expected_domain=expected_domain,
        expected_campaign=expected_campaign, now_ms=now_ms)
    _assert_stable(inputs, context, lease)
    signature_attestation = context.verify_signature(evidence)
    _assert_stable(inputs, context, lease)
    broker = facts["broker"]
    account = facts["account"]
    intent = facts["intent"]
    reservation = facts["reservation"]
    status = "HALT" if dangers else "NO_GO" if readiness else "PASS"
    current_expiries = [expiry for expiry in expiries if expiry > now_ms]
    expires = min(now_ms + MAXIMUM_OUTPUT_LIFETIME_MS, *current_expiries) \
        if current_expiries else now_ms + 1
    body = {
        "schema": OUTPUT_SCHEMA, "version": VERSION, "status": status,
        "observed_at_ms": now_ms, "expires_at_ms": expires,
        "round": ROUND, "domain": expected_domain,
        "campaign_id": expected_campaign,
        "source_baseline_sha256": expected_source,
        "producer": context.attestor.reference,
        "production_mode": PRODUCTION_MODE,
        "snapshot_producer": context.snapshot_producer.reference,
        "snapshot_production_mode": SNAPSHOT_PRODUCTION_MODE,
        "intent_id": intent["intent_id"],
        "operator_intent_reference": inputs["intent"].reference,
        "watch_handoff_receipt": inputs["handoff"].reference,
        "challenge_reference": inputs["challenge"].reference,
        "host_authority_reservation":
            inputs["reservation"].reservation_reference(),
        "reservation_id": reservation["reservation_id"],
        "reservation_generation": reservation["reservation_generation"],
        "reservation_predecessor_finalization_body_sha256":
            reservation["predecessor_finalization_body_sha256"],
        "reservation_prior_finalization_pointer_reference":
            reservation["prior_finalization_pointer_reference"],
        "reservation_lifecycle": reservation["reservation_lifecycle"],
        "reservation_next_consumer": reservation["next_consumer"],
        "reservation_continuity_verified": True,
        "reservation_finalization_tombstone_path":
            reservation["finalization_tombstone_path"],
        "reservation_finalization_current_pointer_path":
            reservation["finalization_current_pointer_path"],
        "reservation_finalization_tombstone_absent": True,
        "reservation_finalization_schema": RESERVATION_FINALIZATION_SCHEMA,
        "reservation_finalization_order": RESERVATION_FINALIZATION_ORDER,
        "reservation_boot_id": reservation["boot_id"],
        "reservation_lease_device": lease_reference["lease_device"],
        "reservation_lease_inode": lease_reference["lease_inode"],
        "signed_evidence_reference": evidence.reference,
        "broker_boundary_reference": inputs["broker"].reference,
        "authoritative_state_reference": inputs["account"].reference,
        "signature_verification": signature_attestation,
        "request_nonce": broker["request_nonce"],
        "account_id_sha256": account["account_id_sha256"],
        "provider_id": account["provider_id"],
        "provider_request_id_sha256": account["provider_request_id_sha256"],
        "provider_response_sha256": account["provider_response_sha256"],
        "observation_method": broker["observation_method"],
        "broker_policy_helper": context.broker_helper.reference,
        "broker_observer_id": broker["observer_id"],
        "account_observer_id": account["observer_id"],
        "observation_authority": account["observation_authority"],
        "query_effect": account["query_effect"],
        "query_epoch": account["query_epoch"],
        "query_fencing_generation": account["query_fencing_generation"],
        "query_invocation_id": account["query_invocation_id"],
        "read_only_authority": account["read_only_authority"],
        "authoritative": account["authoritative"],
        "account_complete": account["account_complete"],
        "snapshot_sha256": account["snapshot_sha256"],
        "observation_complete": broker["observation_complete"],
        "broker_deny_all": broker["broker_deny_all"],
        "policy_sha256": broker["policy_sha256"],
        "authorized_connectors": max(
            broker["authorized_connectors"],
            account["authorized_connector_count"]),
        "authorized_uids": broker["authorized_uids"],
        "broker_socket_count": broker["broker_socket_count"],
        "broker_process_count": broker["broker_process_count"],
        "credential_exposure_count": broker["credential_exposure_count"],
        "order_count": len(account["active_order_id_sha256s"]),
        "position_count": len(account["positions"]),
        "gross_absolute_position": account["gross_absolute_position"],
        "end_flat": account["end_flat"],
        "paper_units_inactive": broker["paper_units_inactive"],
        "kill_switch_engaged": broker["kill_switch_engaged"],
        "protected_broker_ports": broker["protected_broker_ports"],
        "process_inventory_complete": broker["process_inventory_complete"],
        "socket_inventory_complete": broker["socket_inventory_complete"],
        "credential_inventory_complete":
            broker["credential_inventory_complete"],
        "host_authority_lease": lease_reference,
        "host_authority_lease_reacquired": True,
        "paper_authorized": False, "live_authorized": False,
        "mutation_authorized": False, "direct_broker_access": False,
        "order_submission_authorized": False,
    }
    receipt = seal(body)
    validate_output(receipt)
    return receipt


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
            raise AttestationError("ZERO_EXPOSURE_OUTPUT_ALREADY_EXISTS")
        raise AttestationError(reason) from OSError(number, os.strerror(number))


def _publish(
    receipt: dict[str, Any], output: Path, *,
    inputs: Mapping[str, InputBinding], context: ProductionContext,
    lease: HostAuthorityLease,
) -> str:
    reason = "ZERO_EXPOSURE_OUTPUT_PUBLISH_FAILED"
    output = _canonical_path(output, "ZERO_EXPOSURE_OUTPUT_PATH_INVALID")
    _require(output not in {binding.path for binding in inputs.values()},
             "ZERO_EXPOSURE_OUTPUT_ALIASES_INPUT")
    payload = canonical_bytes(receipt)
    _require(0 < len(payload) <= MAXIMUM_OUTPUT_BYTES, reason)
    validate_output(receipt)
    _assert_stable(inputs, context, lease)
    parent = _open_directory(output.parent, reason)
    parent_identity = _trusted_parent(
        parent, expected_uid=context.expected_uid,
        expected_gid=context.expected_gid, reason=reason)
    temporary = "." + output.name + ".zero-attestor-" + secrets.token_hex(16)
    descriptor = -1
    renamed = False
    try:
        try:
            os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AttestationError("ZERO_EXPOSURE_OUTPUT_ALREADY_EXISTS")
        descriptor = os.open(temporary, CREATE_FLAGS, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, context.expected_uid, context.expected_gid)
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            _require(count > 0, reason)
            offset += count
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        _require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
                 metadata.st_uid == context.expected_uid and
                 metadata.st_gid == context.expected_gid and
                 stat.S_IMODE(metadata.st_mode) == 0o600 and
                 metadata.st_size == len(payload), reason)
        os.fsync(parent)
        _require(parent_identity == _trusted_parent(
            parent, expected_uid=context.expected_uid,
            expected_gid=context.expected_gid, reason=reason), reason)
        _assert_stable(inputs, context, lease)
        _rename_noreplace(parent, temporary, output.name, reason)
        renamed = True
        os.fsync(parent)
        _require(parent_identity == _trusted_parent(
            parent, expected_uid=context.expected_uid,
            expected_gid=context.expected_gid, reason=reason), reason)
    except AttestationError:
        raise
    except OSError as error:
        raise AttestationError(reason) from error
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
        output, "ZERO_EXPOSURE_OUTPUT_POST_VERIFY_FAILED",
        expected_uid=context.expected_uid, expected_gid=context.expected_gid,
        maximum=MAXIMUM_OUTPUT_BYTES)
    _require(committed == payload and
             strict_object(committed,
                           "ZERO_EXPOSURE_OUTPUT_POST_VERIFY_FAILED") ==
                receipt,
             "ZERO_EXPOSURE_OUTPUT_POST_VERIFY_FAILED")
    validate_output(strict_object(
        committed, "ZERO_EXPOSURE_OUTPUT_POST_VERIFY_FAILED"))
    _assert_stable(inputs, context, lease)
    return digest_bytes(committed)


def attest_and_publish(
    *, operator_intent_path: Path, handoff_path: Path, challenge_path: Path,
    signed_evidence_path: Path, broker_snapshot_path: Path,
    account_snapshot_path: Path, expected_source: str,
    expected_domain: str, expected_campaign: str, output_path: Path,
    production_mode: str | None, expected_uid: int = ROOT_UID,
    expected_gid: int = ROOT_GID, now_ms: int | None = None,
    _run_token: object | None = None,
) -> dict[str, Any]:
    _require(_run_token is CLI_RUN_TOKEN, "ZERO_EXPOSURE_CLI_RUN_REQUIRED")
    _require(production_mode == PRODUCTION_MODE,
             "ZERO_EXPOSURE_EXPLICIT_PRODUCTION_INTENT_REQUIRED")
    expected_source = _digest(
        expected_source, "ZERO_EXPOSURE_EXPECTED_SOURCE_INVALID")
    expected_domain = _identifier(
        expected_domain, IDENTIFIER, "ZERO_EXPOSURE_EXPECTED_DOMAIN_INVALID")
    expected_campaign = _identifier(
        expected_campaign, IDENTIFIER,
        "ZERO_EXPOSURE_EXPECTED_CAMPAIGN_INVALID")
    _require(expected_domain == DOMAIN_ID,
             "ZERO_EXPOSURE_EXPECTED_DOMAIN_INVALID")
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    _require(type(now) is int and now >= 0, "ZERO_EXPOSURE_TIME_INVALID")
    paths = {
        "reservation": HOST_AUTHORITY_OWNER_PATH,
        "intent": _canonical_path(
            operator_intent_path, "ZERO_EXPOSURE_INPUT_PATH_INVALID"),
        "handoff": _canonical_path(
            handoff_path, "ZERO_EXPOSURE_INPUT_PATH_INVALID"),
        "challenge": _canonical_path(
            challenge_path, "ZERO_EXPOSURE_INPUT_PATH_INVALID"),
        "evidence": _canonical_path(
            signed_evidence_path, "ZERO_EXPOSURE_INPUT_PATH_INVALID"),
        "broker": _canonical_path(
            broker_snapshot_path, "ZERO_EXPOSURE_INPUT_PATH_INVALID"),
        "account": _canonical_path(
            account_snapshot_path, "ZERO_EXPOSURE_INPUT_PATH_INVALID"),
    }
    _require(len(set(paths.values())) == len(paths),
             "ZERO_EXPOSURE_INPUT_ALIAS")
    output = _canonical_path(output_path, "ZERO_EXPOSURE_OUTPUT_PATH_INVALID")
    context = ProductionContext(
        expected_uid=expected_uid, expected_gid=expected_gid)
    inputs = _load_inputs(
        paths, expected_uid=expected_uid, expected_gid=expected_gid)
    historical = _validate_historical_lease(
        inputs["broker"].document.get("host_authority_lease"),
        expected_uid=expected_uid, expected_gid=expected_gid)
    lease = context.acquire_lease()
    try:
        _require(
            context.validate_lease(lease, inputs["reservation"]) == historical,
                 "ZERO_EXPOSURE_HOST_AUTHORITY_LEASE_IDENTITY_MISMATCH")
        receipt = _build_attestation(
            inputs=inputs, context=context, lease=lease,
            expected_source=expected_source, expected_domain=expected_domain,
            expected_campaign=expected_campaign, now_ms=now)
        _publish(receipt, output, inputs=inputs, context=context, lease=lease)
        return receipt
    finally:
        context.release_lease(lease, inputs["reservation"])


def validate_terminal_witness_bundle(
    *, transport_cutoff_path: Path, provider_trust_policy_path: Path,
    challenge_path: Path, signed_evidence_path: Path,
    provider_request_path: Path, provider_response_path: Path,
    witness_path: Path, expected_source: str, expected_campaign: str,
    expected_cycle: str, expected_recovery: str, expected_finalization: str,
    expected_uid: int = ROOT_UID, expected_gid: int = ROOT_GID,
    now_ms: int | None = None, now_monotonic_ns: int | None = None,
) -> dict[str, Any]:
    expected_source = _digest(
        expected_source, "TERMINAL_WITNESS_EXPECTED_SOURCE_INVALID")
    for value in (expected_campaign, expected_cycle, expected_recovery,
                  expected_finalization):
        _identifier(value, IDENTIFIER, "TERMINAL_WITNESS_IDENTITY_INVALID")
    paths = {
        "owner": HOST_AUTHORITY_OWNER_PATH,
        "cutoff": _canonical_path(
            transport_cutoff_path, "TERMINAL_WITNESS_PATH_INVALID"),
        "trust": _canonical_path(
            provider_trust_policy_path, "TERMINAL_WITNESS_PATH_INVALID"),
        "challenge": _canonical_path(
            challenge_path, "TERMINAL_WITNESS_PATH_INVALID"),
        "evidence": _canonical_path(
            signed_evidence_path, "TERMINAL_WITNESS_PATH_INVALID"),
        "request": _canonical_path(
            provider_request_path, "TERMINAL_WITNESS_PATH_INVALID"),
        "response": _canonical_path(
            provider_response_path, "TERMINAL_WITNESS_PATH_INVALID"),
        "witness": _canonical_path(
            witness_path, "TERMINAL_WITNESS_PATH_INVALID"),
    }
    _require(len(set(paths.values())) == len(paths),
             "TERMINAL_WITNESS_PATH_ALIAS")
    context = ProductionContext(
        expected_uid=expected_uid, expected_gid=expected_gid)
    owner = _bind_document(
        paths["owner"], TERMINAL_CHALLENGE_FIELDS, TERMINAL_CHALLENGE_SCHEMA,
        "TERMINAL_WITNESS_OWNER_INVALID", expected_uid=expected_uid,
        expected_gid=expected_gid)
    cutoff = _bind_document(
        paths["cutoff"], TRANSPORT_CUTOFF_FIELDS, TRANSPORT_CUTOFF_SCHEMA,
        "TERMINAL_WITNESS_TRANSPORT_CUTOFF_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid)
    trust = _bind_document(
        paths["trust"], TERMINAL_PROVIDER_TRUST_POLICY_FIELDS,
        TERMINAL_PROVIDER_TRUST_POLICY_SCHEMA,
        "TERMINAL_WITNESS_PROVIDER_TRUST_POLICY_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid)
    challenge = _bind_document(
        paths["challenge"], TERMINAL_CHALLENGE_FIELDS,
        TERMINAL_CHALLENGE_SCHEMA, "TERMINAL_WITNESS_CHALLENGE_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid)
    evidence_binding = _bind_document(
        paths["evidence"], None, None,
        "TERMINAL_WITNESS_SIGNED_ACCOUNT_EVIDENCE_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid)
    evidence = _parse_terminal_signed_evidence(evidence_binding)
    file_modes = frozenset({0o400, 0o440, 0o600, 0o640})
    request = _bind_file(
        paths["request"], "TERMINAL_WITNESS_PROVIDER_REQUEST_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid,
        modes=file_modes, maximum=MAXIMUM_JSON_BYTES)
    response = _bind_file(
        paths["response"], "TERMINAL_WITNESS_PROVIDER_RESPONSE_INVALID",
        expected_uid=expected_uid, expected_gid=expected_gid,
        modes=file_modes, maximum=MAXIMUM_JSON_BYTES)
    witness = _bind_document(
        paths["witness"], TERMINAL_WITNESS_FIELDS, TERMINAL_WITNESS_SCHEMA,
        "TERMINAL_WITNESS_OUTPUT_INVALID", expected_uid=expected_uid,
        expected_gid=expected_gid)
    lease = context.acquire_lease()
    try:
        _validate_terminal_live_lease(lease, owner)
        _require(owner.document == challenge.document,
                 "TERMINAL_WITNESS_OWNER_INVALID")
        now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
        monotonic = time.monotonic_ns() if now_monotonic_ns is None else \
            now_monotonic_ns
        _integer(now, "TERMINAL_WITNESS_TIME_INVALID")
        _integer(monotonic, "TERMINAL_WITNESS_TIME_INVALID")
        _validate_terminal_cutoff(
            cutoff.document, expected_source=expected_source,
            expected_campaign=expected_campaign, expected_cycle=expected_cycle,
            expected_recovery=expected_recovery,
            expected_finalization=expected_finalization,
            expected_boot_id=lease.boot_id)
        _validate_terminal_policy(
            trust.document,
            verification_key_sha256=context.verification_key.reference[
                "file_sha256"])
        _validate_terminal_challenge(
            challenge.document, cutoff=cutoff, trust_policy=trust,
            context=context, now_ms=now, now_monotonic_ns=monotonic,
            boot_id=lease.boot_id)
        _validate_terminal_signed_payload(
            evidence, cutoff=cutoff, challenge=challenge,
            trust_policy=trust, now_ms=now, now_monotonic_ns=monotonic)
        _require(
            evidence.payload["provider_request_sha256"] ==
                request.reference["file_sha256"] and
            evidence.payload["provider_response_sha256"] ==
                response.reference["file_sha256"],
            "TERMINAL_WITNESS_PROVIDER_ARTIFACT_MISMATCH")
        context.verify_signature(evidence)
        _validate_terminal_witness(
            witness.document, cutoff=cutoff, challenge=challenge,
            trust_policy=trust, evidence=evidence, provider_request=request,
            provider_response=response, context=context)
        _require(
            witness.document["verified_at_ms"] <= now <
                witness.document["expires_at_ms"] and
            witness.document["verified_monotonic_ns"] <= monotonic,
            "TERMINAL_WITNESS_OUTPUT_STALE")
        CURRENT_BOUNDARY_VALIDATOR(
            witness.document["egress_policy_generation"],
            witness.document["egress_policy_sha256"])
        for binding in (owner, cutoff, trust, challenge, evidence_binding,
                        witness):
            binding.reopen(expected_uid=expected_uid, expected_gid=expected_gid)
        request.reopen()
        response.reopen()
        context.reopen()
        _validate_terminal_live_lease(lease, owner)
        CURRENT_BOUNDARY_VALIDATOR(
            witness.document["egress_policy_generation"],
            witness.document["egress_policy_sha256"])
        return witness.document
    finally:
        _release_terminal_live_lease(lease, owner)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--validate-terminal-witness", action="store_true")
    parser.add_argument("--operator-intent", type=Path)
    parser.add_argument("--watch-handoff-receipt", type=Path)
    parser.add_argument("--challenge", type=Path)
    parser.add_argument("--signed-account-evidence", type=Path)
    parser.add_argument("--broker-boundary-snapshot", type=Path)
    parser.add_argument("--authoritative-account-snapshot", type=Path,
                        required=False)
    parser.add_argument("--transport-cutoff-receipt", type=Path)
    parser.add_argument("--provider-trust-policy", type=Path)
    parser.add_argument("--provider-request", type=Path)
    parser.add_argument("--provider-response", type=Path)
    parser.add_argument("--terminal-witness", type=Path)
    parser.add_argument("--expected-source-baseline-sha256")
    parser.add_argument("--expected-domain", choices=(DOMAIN_ID,))
    parser.add_argument("--expected-campaign-id")
    parser.add_argument("--expected-cycle-id")
    parser.add_argument("--expected-recovery-id")
    parser.add_argument("--expected-finalization-id")
    parser.add_argument("--production-mode", choices=(PRODUCTION_MODE,))
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parsed = _parser().parse_args(argv)
    try:
        if parsed.validate_terminal_witness:
            _require(
                parsed.transport_cutoff_receipt is not None and
                parsed.provider_trust_policy is not None and
                parsed.challenge is not None and
                parsed.signed_account_evidence is not None and
                parsed.provider_request is not None and
                parsed.provider_response is not None and
                parsed.terminal_witness is not None and
                parsed.expected_source_baseline_sha256 is not None and
                parsed.expected_campaign_id is not None and
                parsed.expected_cycle_id is not None and
                parsed.expected_recovery_id is not None and
                parsed.expected_finalization_id is not None and
                parsed.output is None,
                "TERMINAL_WITNESS_CLI_ARGUMENT_INVALID")
            witness = validate_terminal_witness_bundle(
                transport_cutoff_path=parsed.transport_cutoff_receipt,
                provider_trust_policy_path=parsed.provider_trust_policy,
                challenge_path=parsed.challenge,
                signed_evidence_path=parsed.signed_account_evidence,
                provider_request_path=parsed.provider_request,
                provider_response_path=parsed.provider_response,
                witness_path=parsed.terminal_witness,
                expected_source=parsed.expected_source_baseline_sha256,
                expected_campaign=parsed.expected_campaign_id,
                expected_cycle=parsed.expected_cycle_id,
                expected_recovery=parsed.expected_recovery_id,
                expected_finalization=parsed.expected_finalization_id)
            print("STATUS=" + witness["status"])
            print("PAPER_AUTHORIZED=false")
            print("ORDER_SUBMISSION_AUTHORIZED=false")
            return 0
        _require(
            parsed.operator_intent is not None and
            parsed.watch_handoff_receipt is not None and
            parsed.challenge is not None and
            parsed.signed_account_evidence is not None and
            parsed.broker_boundary_snapshot is not None and
            parsed.authoritative_account_snapshot is not None and
            parsed.expected_source_baseline_sha256 is not None and
            parsed.expected_domain is not None and
            parsed.expected_campaign_id is not None and
            parsed.production_mode is not None and parsed.output is not None,
            "ZERO_EXPOSURE_CLI_ARGUMENT_INVALID")
        receipt = attest_and_publish(
            operator_intent_path=parsed.operator_intent,
            handoff_path=parsed.watch_handoff_receipt,
            challenge_path=parsed.challenge,
            signed_evidence_path=parsed.signed_account_evidence,
            broker_snapshot_path=parsed.broker_boundary_snapshot,
            account_snapshot_path=parsed.authoritative_account_snapshot,
            expected_source=parsed.expected_source_baseline_sha256,
            expected_domain=parsed.expected_domain,
            expected_campaign=parsed.expected_campaign_id,
            output_path=parsed.output, production_mode=parsed.production_mode,
            _run_token=CLI_RUN_TOKEN)
    except AttestationError as error:
        print("hepta_p1_paper_zero_exposure_attestor: FAIL " + error.reason,
              file=sys.stderr)
        return 4
    print("STATUS=" + receipt["status"])
    print("PAPER_AUTHORIZED=false")
    print("ORDER_SUBMISSION_AUTHORIZED=false")
    return {"PASS": 0, "NO_GO": 2, "HALT": 3}[receipt["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
