#!/usr/bin/env python3

"""Root-owned bounded campaign gate for canonical IB PAPER one-shot cycles."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess
import sys
import time
from typing import Any, Callable, Optional


POLICY_SCHEMA = "hepta.ib-paper-campaign-policy.v5"
LEGACY_POLICY_V4_SCHEMA = "hepta.ib-paper-campaign-policy.v4"
LEGACY_POLICY_V3_SCHEMA = "hepta.ib-paper-campaign-policy.v3"
LEGACY_POLICY_V2_SCHEMA = "hepta.ib-paper-campaign-policy.v2"
LEGACY_POLICY_SCHEMA = "hepta.ib-paper-campaign-policy.v1"
ADMISSION_SCHEMA = "hepta.paper-testing-admission-candidate-receipt.v1"
FINALIZATION_SCHEMA = (
    "hepta.p1-paper-zero-exposure-reservation-finalization.v1")
FINALIZATION_POINTER_SCHEMA = (
    "hepta.p1-paper-zero-exposure-finalization-current.v1")
ZERO_EXPOSURE_SCHEMA = (
    "hepta.p1-paper-deny-all-zero-exposure-receipt.v1")
REQUEST_SCHEMA = "hepta.ib-paper-campaign-request.v1"
RESPONSE_SCHEMA = "hepta.ib-paper-campaign-response.v1"
LEGACY_STATE_SCHEMA = "hepta.ib-paper-campaign-state.v1"
STATE_SCHEMA = "hepta.ib-paper-campaign-state.v2"
RECEIPT_SCHEMA = "hepta.ib-paper-campaign-request-receipt.v1"
CONSUMPTION_SCHEMA = "hepta.ib-paper-campaign-consumption-state.v1"
LEGACY_TRADE_INTENT_SCHEMA = "hepta.trade-intent.v1"
TRADE_INTENT_SCHEMA = "hepta.trade-intent.v2"
TRUST_DOMAIN_SCHEMA = "hepta.agent-trust-domain-runtime.v1"
DEFAULT_POLICY_ROOT = Path("/etc/heptatrader/paper-campaigns")
DEFAULT_ADMISSION_ROOT = Path("/var/lib/hepta/paper-testing-admission")
HOST_AUTHORITY_DIRECTORY = Path("/run/hepta/ib-paper-host-authority")
HOST_AUTHORITY_LEASE_PATH = HOST_AUTHORITY_DIRECTORY / "lease.lock"
HOST_AUTHORITY_OWNER_PATH = HOST_AUTHORITY_DIRECTORY / "owner.v1"
HOST_AUTHORITY_CURRENT_POINTER_PATH = (
    HOST_AUTHORITY_DIRECTORY / "finalization-current.v1.json")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
DEFAULT_TRUST_DOMAIN_ROOT = Path("/etc/heptatrader/trust-domains")
DEFAULT_RUNTIME_ROOT = Path("/run/hepta/ib-paper-campaign")
DEFAULT_RECEIPT_ROOT = Path("/var/lib/hepta/ib-paper-campaign")
DEFAULT_AUTHORITY = Path("/usr/libexec/hepta-ib-paper-domain-authority")
LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH = Path(
    "/etc/heptatrader/local-ai-paper-deployment-v1.json")
LOCAL_PAPER_DEPLOYMENT_EVIDENCE_SCHEMA = (
    "hepta.local-ai-paper-deployment-evidence.v1")
LOCAL_PAPER_DEPLOYMENT_EVIDENCE_FIELDS = {
    "schema", "version", "source_freeze_commit", "source_freeze_tree",
    "source_manifest_sha256", "source_baseline_sha256",
    "install_transaction_id", "installed_at_ms", "generated_at_ms", "files",
    "certified_install_closure_file_sha256",
    "certified_install_closure_body_sha256",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "body_sha256",
}
LOCAL_PAPER_DEPLOYMENT_FILE_FIELDS = {"path", "sha256", "mode"}
CERTIFIED_INSTALL_CLOSURE_PATH = Path(
    "/etc/heptatrader/local-ai-paper-certified-install-closure-v1.json")
CERTIFIED_INSTALL_CLOSURE_SCHEMA = (
    "hepta.local-paper-certified-install-closure.v1")
CERTIFIED_INSTALL_CLOSURE_FIELDS = {
    "schema", "version", "source_freeze_commit", "source_freeze_tree",
    "source_manifest_sha256", "source_baseline_sha256",
    "install_transaction_id", "installed_at_ms", "files", "body_sha256",
}
LOCAL_PAPER_DEPLOYMENT_FILES = tuple(sorted((
    (Path("/usr/bin/hepta-campaignctl"), 0o755),
    (Path("/usr/bin/hepta-sessionctl"), 0o755),
    (Path("/usr/bin/heptactl"), 0o755),
    (Path("/usr/libexec/hepta-agent-mcp-launcher"), 0o755),
    (Path("/usr/libexec/hepta-agent-session-bootstrap"), 0o755),
    (Path("/usr/libexec/hepta_agent_trust_domain.py"), 0o755),
    (Path("/usr/libexec/hepta-broker-egress-policy"), 0o755),
    (Path("/usr/libexec/hepta-ib-executiond"), 0o755),
    (Path("/usr/libexec/hepta-ib-paper-campaign-operator"), 0o755),
    (Path("/usr/libexec/hepta-ib-paper-domain-authority"), 0o755),
    (Path("/usr/libexec/hepta-local-ai-paper-agent"), 0o755),
    (Path("/usr/libexec/hepta-local-paper-control"), 0o755),
    (Path("/usr/libexec/hepta-p1-paper-canary-finalizer"), 0o755),
    (Path("/usr/libexec/hepta-p1-paper-terminal-witness-verifier"), 0o755),
    (Path("/usr/libexec/hepta-paper-terminal-latch-committer"), 0o755),
    (Path("/usr/libexec/hepta-local-paper-repair"), 0o755),
    (Path("/usr/libexec/hepta-local-paper-safe-recover"), 0o755),
    (Path("/usr/libexec/hepta-local-paper-safe-recover-guard"), 0o755),
    (Path("/usr/libexec/hepta-local-paper-session-renew"), 0o755),
    (Path("/usr/libexec/hepta-local-paper-supervisor"), 0o755),
    (Path("/usr/libexec/hepta-mcp-server"), 0o755),
    (Path("/usr/libexec/hepta-prepare-paper-campaign"), 0o755),
    (Path("/usr/libexec/hepta-tool-gatewayd"), 0o755),
    (Path("/usr/lib/systemd/system/hepta-broker-egress-policy.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-events-ib-paper.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-events-ib-paper@.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-ib-paper.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-ib-paper.service.d/"
          "10-hepta-broker-egress-policy.conf"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-ib-paper.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-ib-paper@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-ib-paper@.service.d/"
          "10-hepta-broker-egress-policy.conf"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-execution-ib-paper@.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-ib-paper-campaign-operator@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-ib-paper-campaign-operator@.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-ib-paper-domain-preflight@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-ai-paper-agent.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-authority@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-p1-paper-canary-finalizer.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-p1-paper-canary-finalizer@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-fail-close@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-p1-paper-terminal-cutoff@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-p1-paper-terminal-witness-verifier@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-paper-terminal-latch-committer@.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-safe-recover.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-safe-recover.timer"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-session-renew.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-session-renew.timer"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-supervisor.service"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-local-paper-supervisor.timer"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-gateway.service"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-gateway.service.d/"
          "10-hepta-broker-egress-policy.conf"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-gateway@.service"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-gateway@.service.d/"
          "10-hepta-broker-egress-policy.conf"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-gateway.socket"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-gateway@.socket"), 0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-session-supervisor.socket"),
     0o644),
    (Path("/usr/lib/systemd/system/hepta-tool-session-supervisor@.socket"),
     0o644),
    (Path("/usr/lib/tmpfiles.d/heptatrader-agent-os.conf"), 0o644),
    (Path("/usr/lib/tmpfiles.d/heptatrader-ib-paper.conf"), 0o644),
    (Path("/usr/share/heptatrader/hepta-broker-network-policy-v1.json"),
     0o644),
    (Path("/usr/share/heptatrader/hepta-local-ai-paper-strategy-v2.json"),
     0o644),
    (Path("/usr/share/heptatrader/hepta-local-ai-paper-strategy-v3.json"),
     0o644),
    (Path("/usr/share/heptatrader/hepta-service-identities-v1.json"), 0o644),
), key=lambda item: str(item[0])))
LOCAL_PAPER_DEPLOYMENT_FILE_COUNT = 63
if len(LOCAL_PAPER_DEPLOYMENT_FILES) != LOCAL_PAPER_DEPLOYMENT_FILE_COUNT:
    raise RuntimeError(
        "local PAPER certified deployment closure must contain exactly "
        "63 files")
MAX_BYTES = 64 * 1024
MAX_DEPLOYED_FILE_BYTES = 256 * 1024 * 1024
MAX_CAMPAIGN_DURATION_MS = 4 * 60 * 60 * 1000
MAX_CAMPAIGN_CYCLES = 20
MAX_V5_CAMPAIGN_DURATION_MS = 24 * 60 * 60 * 1000
EXTERNAL_P1_CAMPAIGN_DURATION_MS = 5 * 60 * 1000
MAX_V5_CAMPAIGN_CYCLES = 720
MAX_LOCAL_CAMPAIGN_DURATION_MS = 30 * 24 * 60 * 60 * 1000
MAX_LOCAL_CAMPAIGN_CYCLES = 25_000
# IDEALPRO requires 25,000 units for a regular-size FX order. This remains a
# PAPER-only campaign ceiling; LIVE authorization is independently forbidden.
MAX_ORDER_QUANTITY = 25_000
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
    "broker_deny_all", "kill_switch_engaged", "global_kill_switch_engaged",
    "identity_count", "identity_manifest_sha256", "paper_profile_restored",
    "paper_profile_restoration", "profile_candidate_absent",
    "paper_runtime_profile_hardened", "paper_runtime_profile_hardening",
    "paper_runtime_profile_candidate_absent",
    "crash_recovery_verified", "cleanup_residue_count", "paper_authorized",
    "live_authorized", "mutation_authorized", "direct_broker_access",
    "order_submission_authorized", "body_sha256",
})
EXTERNAL_P1_PROFILE_RESTORATION_FIELDS = frozenset({
    "schema", "version", "status", "target", "dormant_backup",
    "forward_retained_dormant", "retired_watch",
    "forward_transition_receipt", "profile_deployment_receipt",
    "forward_preimage_evidence", "candidate_path", "retired_watch_path",
    "exchange_method", "forward_only_after_exchange",
    "restore_intent_record_sha256", "restore_exchange_record_sha256",
})
EXTERNAL_P1_PROFILE_EVIDENCE_FIELDS = frozenset({
    "path", "file_sha256", "bytes", "mode", "uid", "gid", "nlink",
    "device", "inode", "mtime_ns", "ctime_ns",
})
EXTERNAL_P1_PROFILE_SEALED_EVIDENCE_FIELDS = frozenset({
    *EXTERNAL_P1_PROFILE_EVIDENCE_FIELDS, "body_sha256",
})
EXTERNAL_P1_RUNTIME_HARDENING_FIELDS = frozenset({
    "schema", "version", "status", "target", "legacy_backup",
    "retained_legacy", "candidate_path", "retained_legacy_path",
    "exchange_method", "forward_only_after_exchange",
    "harden_intent_record_sha256", "harden_exchange_record_sha256",
})
EXTERNAL_P1_DISABLED_IDENTITY_SHA256 = (
    "sha256:4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435")
EXTERNAL_P1_DORMANT_PROFILE_PATH = Path(
    "/etc/heptatrader/trust-domains/alpha.env")
EXTERNAL_P1_DORMANT_PROFILE_SHA256 = (
    "sha256:e5866254918ebb23c39c3e3630b9281ab780ad82c2cdb8f63e68749b1f4e9012")
EXTERNAL_P1_DORMANT_PROFILE_BYTES = 878
EXTERNAL_P1_PAPER_PROFILE_PATH = Path(
    "/etc/heptatrader/trust-domains/alpha.ib-paper.env")
EXTERNAL_P1_PAPER_PROFILE_SHA256 = (
    "sha256:99dd8ab1cd612989906a972abcaad0dd4234d908ea4ce295c0c01a9059604ee4")
EXTERNAL_P1_PAPER_PROFILE_BYTES = 767
EXTERNAL_P1_PAPER_PROFILE_CANDIDATE_PATH = Path(
    "/etc/heptatrader/trust-domains/"
    ".alpha.ib-paper.env.hepta-p1-round114-runtime-harden.candidate")
EXTERNAL_P1_PAPER_PROFILE_BACKUP_PATH = Path(
    "/var/lib/heptatrader/p1-watch-to-paper-handoff/round114/"
    "legacy-paper-runtime-profile-backup.env")
EXTERNAL_P1_PAPER_PROFILE_RETAINED_PATH = Path(
    "/var/lib/heptatrader/p1-watch-to-paper-handoff/round114/"
    "retained-legacy-paper-runtime-profile.env")
EXTERNAL_P1_LEGACY_PAPER_PROFILE_SHA256 = (
    "sha256:2537f50ffe51f74e975f452e570d2c8ddaa82e1757955443014f5f28c9170f03")
EXTERNAL_P1_LEGACY_PAPER_PROFILE_BYTES = 776
EXTERNAL_P1_DORMANT_BACKUP_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-backups/"
    "round114-dormant-paper-to-watch/alpha.env")
EXTERNAL_P1_FORWARD_RETAINED_PATH = EXTERNAL_P1_DORMANT_PROFILE_PATH.with_name(
    ".alpha.env.hepta-p1-round114-dormant-paper-to-watch.retained")
EXTERNAL_P1_PREIMAGE_PATH = EXTERNAL_P1_DORMANT_BACKUP_PATH.with_name(
    "preimage-evidence.json")
EXTERNAL_P1_TRANSITION_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/"
    "round114-dormant-paper-to-watch.json")
EXTERNAL_P1_DEPLOYMENT_PATH = Path(
    "/var/lib/heptatrader/p1-watch-profile-receipts/round114-generation22.json")
EXTERNAL_P1_CANDIDATE_PATH = EXTERNAL_P1_DORMANT_PROFILE_PATH.with_name(
    ".alpha.env.hepta-p1-round114-watch-to-paper.candidate")
EXTERNAL_P1_RETIRED_WATCH_PATH = Path(
    "/var/lib/heptatrader/p1-watch-to-paper-handoff/round114/"
    "retired-watch-profile.env")
EXTERNAL_P1_WATCH_PROFILE_SHA256 = (
    "sha256:ffcde4c46237ecacb3c32603f3aca0ba1a51c5b353b4fd2e5ab2f42ca1470e3f")
EXTERNAL_P1_WATCH_PROFILE_BYTES = 736
MIN_OPERATOR_TTL_SECONDS = 5
MAX_OPERATOR_TTL_SECONDS = 20
MAX_OPERATOR_START_SKEW_MS = 5_000
MIN_INTENT_HORIZON_MS = 2_000
MAX_INTENT_HORIZON_MS = 60_000
MAX_HOLDING_MS = 60 * 60 * 1000
DOMAIN = re.compile(r"[a-z][a-z0-9-]{0,31}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}")
STRATEGY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
GIT_OBJECT = re.compile(r"[0-9a-f]{40}")
INSTALL_TRANSACTION = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:@+\-]{7,127}")
ZERO_DIGEST = "sha256:" + "0" * 64
ZERO_GIT_OBJECT = "0" * 40
SAFE_JSON_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json")
RESERVATION_ID = re.compile(r"zero-exposure-[0-9a-f]{48}")
FINALIZATION_TOMBSTONE_NAME = re.compile(
    r"finalized\.(zero-exposure-[0-9a-f]{48})\.v1\.json")
CONSUMPTION_NAME = re.compile(r"consumption\.[0-9a-f]{64}\.v1\.json")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
REASON = re.compile(r"[A-Z][A-Z0-9_]{2,95}")
SAFE_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "PYTHONDONTWRITEBYTECODE": "1",
}
POLICY_V1_FIELDS = {
    "schema", "version", "campaign_id", "domain_id", "enabled",
    "mutations_authorized", "paper_only", "live_authorized",
    "strategy_id", "strategy_version", "strategy_sha256",
    "valid_after_ms", "expires_at_ms", "allowed_instruments",
    "max_cycles", "max_quantity", "min_cycle_interval_ms",
    "operator_ttl_seconds", "max_intent_horizon_ms", "max_holding_ms",
    "max_active_orders", "order_type", "tif", "end_flat_required",
}
POLICY_V2_FIELDS = POLICY_V1_FIELDS | {
    "source_baseline_sha256", "admission_receipt_name",
    "admission_receipt_file_sha256", "admission_receipt_body_sha256",
}
POLICY_V3_FIELDS = POLICY_V2_FIELDS | {
    "admission_finalization_current_pointer_path",
    "admission_finalization_current_pointer_file_sha256",
    "admission_finalization_current_pointer_body_sha256",
    "admission_finalization_tombstone_path",
    "admission_finalization_tombstone_file_sha256",
    "admission_finalization_tombstone_body_sha256",
}
POLICY_V4_FIELDS = POLICY_V1_FIELDS | {
    "source_baseline_sha256", "admission_mode",
    "deployment_evidence_file_sha256",
    "deployment_evidence_body_sha256",
    "deployment_install_transaction_id",
}
POLICY_V5_FIELDS = POLICY_V3_FIELDS | {
    "admission_mode", "deployment_evidence_file_sha256",
    "deployment_evidence_body_sha256",
    "deployment_install_transaction_id",
    "p1_audit_receipt_path", "p1_audit_receipt_file_sha256",
    "p1_audit_receipt_body_sha256", "watch_handoff_receipt_path",
    "watch_handoff_receipt_file_sha256",
    "watch_handoff_receipt_body_sha256",
}
# v5 deliberately supports two non-overlapping exact policy contracts.  The
# external contract above consumes finalized P1/WATCH evidence.  The local
# contract retains the proven bounded MKT campaign surface while adding the
# same certified-install binding used by v5.  Field-set exactness keeps a
# local policy from smuggling any external-admission authority (and vice
# versa).
POLICY_V5_LOCAL_FIELDS = POLICY_V4_FIELDS
ADMISSION_FIELDS = {
    "schema", "version", "status", "evaluated_at_ms", "expires_at_ms",
    "round", "domain", "campaign_id", "source_baseline_sha256",
    "strategy_sha256",
    "input_bindings", "findings", "paper_test_admission_candidate",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized",
    "authorization_effect", "body_sha256",
}
ADMISSION_BINDING_FIELDS = {
    "path", "file_sha256", "body_sha256", "schema", "version", "status",
}
ADMISSION_INPUT_NAMES = {
    "source_baseline", "install_manifest", "install_receipt",
    "install_pointer", "profile_receipt", "activation_receipt",
    "p1_audit_receipt", "release_validation_receipt",
    "agent_os_rootful_gate_receipt", "dual_domain_gate_receipt",
    "rootful_gate_receipt", "p1_liveness_gate_receipt",
    "network_gate_receipt", "hard_network_gate_receipt",
    "native_gate_receipt", "watch_handoff_receipt",
    "zero_exposure_receipt",
}
BOUNDARY_FIELDS = (
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "order_submission_authorized",
)
REFERENCE_FIELDS = {"path", "file_sha256", "body_sha256"}
RESERVATION_REFERENCE_FIELDS = {
    "path", "file_sha256", "body_sha256", "device", "inode", "uid",
    "gid", "mode", "size", "mtime_ns", "ctime_ns",
}
HOST_AUTHORITY_LEASE_FIELDS = {
    "directory_path", "lease_path", "owner_path", "directory_device",
    "directory_inode", "directory_uid", "directory_gid", "directory_mode",
    "lease_device", "lease_inode", "lease_uid", "lease_gid", "lease_mode",
    "lease_size", "held_exclusive", "boot_id",
}
CONSUMPTION_FIELDS = {
    "schema", "version", "status", "consumed_at_ms",
    "monotonic_clock", "consumed_monotonic_ms",
    "monotonic_expires_at_ms", "domain_id",
    "campaign_id", "policy_file_sha256", "source_baseline_sha256",
    "strategy_id", "strategy_version", "strategy_sha256", "boot_id",
    "host_authority_lease", "p1_audit_receipt_reference",
    "p1_audit_receipt_snapshot", "watch_handoff_receipt_reference",
    "watch_handoff_receipt_snapshot", "admission_receipt_reference",
    "finalization_current_pointer_reference",
    "finalization_tombstone_reference", "zero_exposure_receipt_reference",
    "deployment_evidence_reference", "deployment_install_transaction_id",
    "authorization_effect", *BOUNDARY_FIELDS, "body_sha256",
}
FINALIZATION_FIELDS = {
    "schema", "version", "status", "finalized_at_ms", "round", "domain",
    "campaign_id", "source_baseline_sha256", "reservation_id",
    "reservation_generation", "predecessor_finalization_body_sha256",
    "prior_finalization_pointer_reference", "boot_id",
    "reservation_reference", "candidate_reference",
    "zero_exposure_receipt_reference", "host_authority_lease",
    "recovery_observation", "owner_present_at_tombstone_commit",
    "owner_removal_required_after_commit", "finalization_order",
    "recovery_reason", *BOUNDARY_FIELDS, "body_sha256",
}
FINALIZATION_POINTER_FIELDS = {
    "schema", "version", "status", "updated_at_ms", "round", "domain",
    "campaign_id", "source_baseline_sha256", "boot_id", "reservation_id",
    "reservation_generation", "predecessor_finalization_body_sha256",
    "finalization_tombstone_reference", "host_authority_lease",
    *BOUNDARY_FIELDS, "body_sha256",
}
ZERO_EXPOSURE_FIELDS = {
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
    "reservation_lease_inode", "signed_evidence_reference",
    "broker_boundary_reference", "authoritative_state_reference",
    "signature_verification", "request_nonce", "account_id_sha256",
    "provider_id", "provider_request_id_sha256",
    "provider_response_sha256", "observation_method",
    "broker_policy_helper", "broker_observer_id", "account_observer_id",
    "observation_authority", "query_effect", "query_epoch",
    "query_fencing_generation", "query_invocation_id",
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
}
FINALIZATION_ORDER = (
    "CANDIDATE_COMMIT_THEN_TOMBSTONE_COMMIT_THEN_CURRENT_POINTER_COMMIT_"
    "THEN_OWNER_REMOVE_THEN_REOPEN")
RESERVATION_LIFECYCLE = (
    "CHALLENGE_ISSUED_TO_PAPER_TESTING_ADMISSION_FINALIZATION")
RESERVATION_NEXT_CONSUMER = "PAPER_TESTING_ADMISSION_VERIFIER"
TRADE_INTENT_COMMON_FIELDS = {
    "schema", "paper_only", "strategy_id", "strategy_version",
    "strategy_sha256", "intent_id", "instrument", "symbol", "currency",
    "sec_type", "exchange", "side", "quantity", "order_type",
    "tif", "observed_bid", "observed_ask", "observed_at_ms",
    "expires_at_ms", "entry_thesis", "invalidation_condition",
    "max_holding_ms", "max_adverse_move", "expected_slippage", "exit_plan",
}
LEGACY_TRADE_INTENT_FIELDS = TRADE_INTENT_COMMON_FIELDS | {"limit_price"}
TRADE_INTENT_FIELDS = TRADE_INTENT_COMMON_FIELDS | {"reference_price"}
REQUEST_COMMON_FIELDS = {
    "schema", "version", "action", "request_id", "domain_id", "campaign_id",
}
REQUEST_FIELDS = {
    "status": REQUEST_COMMON_FIELDS,
    "open_cycle": REQUEST_COMMON_FIELDS | {
        "cycle_id", "intent", "intent_sha256", "preflight_sha256",
    },
    "close_cycle": REQUEST_COMMON_FIELDS | {
        "cycle_id", "intent_sha256", "outcome",
    },
    "halt": REQUEST_COMMON_FIELDS | {"reason_code"},
}
ACTIVE_CYCLE_FIELDS = {
    "cycle_id", "intent_sha256", "preflight_sha256", "opened_at_ms",
    "deadline_at_ms",
}
LEGACY_STATE_FIELDS = {
    "schema", "version", "domain_id", "campaign_id", "policy_sha256",
    "status", "created_at_ms", "expires_at_ms", "cycles_opened",
    "cycles_closed", "last_cycle_closed_at_ms", "active_cycle",
    "halt_reason", "last_outcome",
}
STATE_FIELDS = LEGACY_STATE_FIELDS | {
    "consumption_receipt_name",
    "consumption_receipt_file_sha256",
    "consumption_receipt_body_sha256", "consumption_receipt_identity",
}
TRUST_DOMAIN_FIELDS = {
    "schema", "version", "domain_id",
    "gateway_name", "gateway_uid", "gateway_group", "gateway_gid",
    "agent_name", "agent_uid", "agent_group", "agent_gid",
    "execution_name", "execution_uid", "execution_group", "execution_gid",
    "connect_group", "connect_group_gid", "socket_path", "token_directory",
    "supervisor_socket", "lease_credential_path", "gateway_state_directory",
    "execution_socket", "execution_event_socket",
    "execution_fence_credential_path", "execution_state_directory",
    "execution_gateway_uid", "execution_gateway_agent_id",
    "single_domain_compatibility", "paper_authorized", "live_authorized",
}
OUTCOMES = {
    "PREVIEW_REJECTED", "PLACE_REJECTED", "PLACE_ACCEPTED",
    "PLACE_UNCERTAIN", "OPERATOR_ABORT",
}


class CampaignError(RuntimeError):
    def __init__(
            self, code: str, *, recovery_required: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.recovery_required = recovery_required


def _boottime_ms() -> int:
    """Return suspend-aware, non-settable elapsed time for this Linux boot."""

    try:
        value = time.clock_gettime_ns(time.CLOCK_BOOTTIME) // 1_000_000
    except (AttributeError, OSError, OverflowError) as error:
        raise CampaignError(
            "CAMPAIGN_MONOTONIC_CLOCK_UNAVAILABLE",
            recovery_required=True) from error
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise CampaignError(
            "CAMPAIGN_MONOTONIC_CLOCK_INVALID", recovery_required=True)
    return value


@dataclass(frozen=True)
class CampaignPolicy:
    version: int
    campaign_id: str
    domain_id: str
    enabled: bool
    mutations_authorized: bool
    strategy_id: str
    strategy_version: str
    strategy_sha256: str
    valid_after_ms: int
    expires_at_ms: int
    allowed_instruments: tuple[str, ...]
    max_cycles: int
    max_quantity: int
    min_cycle_interval_ms: int
    operator_ttl_seconds: int
    max_intent_horizon_ms: int
    max_holding_ms: int
    order_type: str
    admission_mode: str
    source_baseline_sha256: str | None
    deployment_evidence_file_sha256: str | None
    deployment_evidence_body_sha256: str | None
    deployment_install_transaction_id: str | None
    admission_receipt_name: str | None
    admission_receipt_file_sha256: str | None
    admission_receipt_body_sha256: str | None
    finalization_current_pointer_path: Path | None
    finalization_current_pointer_file_sha256: str | None
    finalization_current_pointer_body_sha256: str | None
    finalization_tombstone_path: Path | None
    finalization_tombstone_file_sha256: str | None
    finalization_tombstone_body_sha256: str | None
    p1_audit_receipt_path: Path | None
    p1_audit_receipt_file_sha256: str | None
    p1_audit_receipt_body_sha256: str | None
    watch_handoff_receipt_path: Path | None
    watch_handoff_receipt_file_sha256: str | None
    watch_handoff_receipt_body_sha256: str | None


@dataclass(frozen=True)
class PolicySnapshot:
    path: Path | None
    payload: bytes
    identity: tuple[int, ...] | None
    policy: CampaignPolicy
    file_sha256: str

    def __iter__(self):
        """Keep two-value unpacking compatible with legacy callers."""

        yield self.policy
        yield self.file_sha256

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: int) -> CampaignPolicy | str:
        return (self.policy, self.file_sha256)[index]


@dataclass(frozen=True)
class LocalDeploymentSnapshot:
    payload: bytes
    document: dict[str, Any]
    evidence_identity: tuple[int, ...]
    certified_identity: tuple[int, ...]
    installed_identities: tuple[tuple[str, tuple[int, ...]], ...]
    file_sha256: str
    body_sha256: str
    source_baseline_sha256: str
    install_transaction_id: str


@dataclass(frozen=True)
class ConsumptionSnapshot:
    payload: bytes
    document: dict[str, Any]
    identity: tuple[int, ...]
    file_sha256: str
    body_sha256: str


@dataclass(frozen=True)
class PinnedReferenceSnapshot:
    path: Path
    payload: bytes
    document: dict[str, Any]
    identity: tuple[int, ...]
    anchor_identity: tuple[tuple[int, ...], ...]
    file_sha256: str
    body_sha256: str

    @property
    def reference(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "file_sha256": self.file_sha256,
            "body_sha256": self.body_sha256,
        }

    @property
    def consumption_binding(self) -> dict[str, Any]:
        return {
            "identity": list(self.identity),
            "anchor_identity": [
                list(identity) for identity in self.anchor_identity],
        }


@dataclass(frozen=True)
class AdmissionSnapshot:
    path: Path
    payload: bytes
    identity: tuple[int, ...]
    anchor_identity: tuple[tuple[int, ...], ...]
    file_sha256: str
    body_sha256: str
    evaluated_at_ms: int
    expires_at_ms: int
    document: dict[str, Any]

    @property
    def reference(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "file_sha256": self.file_sha256,
            "body_sha256": self.body_sha256,
        }


@dataclass(frozen=True)
class FinalizationArtifact:
    path: Path
    payload: bytes
    identity: tuple[int, ...]
    anchor_identity: tuple[tuple[int, ...], ...]
    file_sha256: str
    body_sha256: str
    document: dict[str, Any]

    @property
    def reference(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "file_sha256": self.file_sha256,
            "body_sha256": self.body_sha256,
        }


@dataclass(frozen=True)
class CampaignPaths:
    runtime_root: Path = DEFAULT_RUNTIME_ROOT
    receipt_root: Path = DEFAULT_RECEIPT_ROOT


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CampaignError("CAMPAIGN_INVALID_JSON") from error
    if not isinstance(value, dict):
        raise CampaignError(f"{label.upper()}_ROOT_INVALID")
    return value


def _canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, separators=(",", ":"),
            sort_keys=True, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError) as error:
        raise CampaignError("CAMPAIGN_NON_CANONICAL_JSON") from error


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _stable_read_with_identity(
        path: Path, *, installed: bool, expected_uid: int = 0,
        expected_gid: int = 0, expected_mode: int = 0o600,
) -> tuple[bytes, tuple[int, ...]]:
    before = os.lstat(path)
    if (
            stat.S_ISLNK(before.st_mode) or
            not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            before.st_size < 2 or before.st_size > MAX_BYTES or
            (installed and (
                before.st_uid != expected_uid or
                before.st_gid != expected_gid or
                stat.S_IMODE(before.st_mode) != expected_mode)) or
            (not installed and stat.S_IMODE(before.st_mode) & 0o002)):
        raise CampaignError("CAMPAIGN_SOURCE_METADATA_UNSAFE")
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0) |
        getattr(os, "O_NONBLOCK", 0))
    try:
        opened = os.fstat(descriptor)
        raw = bytearray()
        while len(raw) <= MAX_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if (
            len(raw) > MAX_BYTES or
            any(getattr(before, field) != getattr(opened, field) or
                getattr(opened, field) != getattr(after, field)
                for field in fields)):
        raise CampaignError("CAMPAIGN_SOURCE_CHANGED")
    return bytes(raw), tuple(int(getattr(after, field)) for field in fields)


def _stable_read(
        path: Path, *, installed: bool, expected_uid: int = 0,
        expected_gid: int = 0, expected_mode: int = 0o600,
) -> bytes:
    raw, _identity = _stable_read_with_identity(
        path, installed=installed, expected_uid=expected_uid,
        expected_gid=expected_gid, expected_mode=expected_mode)
    return raw


def _snapshot_local_deployed_file(
        path: Path, expected_sha256: str, expected_mode: int,
) -> tuple[int, ...]:
    failure = "CAMPAIGN_DEPLOYMENT_FILE_INVALID"
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != 0 or before.st_gid != 0 or
                stat.S_IMODE(before.st_mode) != expected_mode or
                before.st_size < 1 or
                before.st_size > MAX_DEPLOYED_FILE_BYTES):
            raise CampaignError(failure)
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise CampaignError(failure)
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise CampaignError(failure)
        after = os.fstat(descriptor)
        current = os.lstat(path)
        fields = (
            "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
            "st_uid", "st_gid", "st_mode", "st_nlink")
        identity = tuple(int(getattr(before, field)) for field in fields)
        if (identity != tuple(int(getattr(after, field)) for field in fields) or
                identity != tuple(
                    int(getattr(current, field)) for field in fields) or
                "sha256:" + digest.hexdigest() != expected_sha256):
            raise CampaignError(failure)
        return identity
    finally:
        os.close(descriptor)


def _load_certified_install_closure(
        expected_file_sha256: str, expected_body_sha256: str,
) -> tuple[bytes, dict[str, Any], tuple[int, ...]]:
    failure = "CAMPAIGN_CERTIFIED_INSTALL_CLOSURE_INVALID"
    raw, identity = _stable_read_with_identity(
        CERTIFIED_INSTALL_CLOSURE_PATH, installed=True,
        expected_uid=0, expected_gid=0, expected_mode=0o600)
    document = _strict_json(raw, "certified_install_closure")
    if (raw != _canonical_json(document) or
            _sha256(raw) != expected_file_sha256 or
            set(document) != CERTIFIED_INSTALL_CLOSURE_FIELDS or
            document.get("schema") != CERTIFIED_INSTALL_CLOSURE_SCHEMA or
            document.get("version") != 1):
        raise CampaignError(failure)
    for field in ("source_freeze_commit", "source_freeze_tree"):
        item = document.get(field)
        if (not isinstance(item, str) or GIT_OBJECT.fullmatch(item) is None or
                item == ZERO_GIT_OBJECT):
            raise CampaignError(failure)
    for field in (
            "source_manifest_sha256", "source_baseline_sha256",
            "body_sha256"):
        item = document.get(field)
        if (not isinstance(item, str) or DIGEST.fullmatch(item) is None or
                item == ZERO_DIGEST):
            raise CampaignError(failure)
    if document["body_sha256"] != expected_body_sha256:
        raise CampaignError(failure)
    transaction_id = document.get("install_transaction_id")
    installed_at_ms = document.get("installed_at_ms")
    if (not isinstance(transaction_id, str) or
            INSTALL_TRANSACTION.fullmatch(transaction_id) is None or
            type(installed_at_ms) is not int or installed_at_ms <= 0 or
            installed_at_ms > time.time_ns() // 1_000_000):
        raise CampaignError(failure)
    files = document.get("files")
    if (not isinstance(files, list) or
            len(files) != len(LOCAL_PAPER_DEPLOYMENT_FILES)):
        raise CampaignError(failure)
    for record, (path, mode) in zip(
            files, LOCAL_PAPER_DEPLOYMENT_FILES, strict=True):
        if (not isinstance(record, dict) or
                set(record) != LOCAL_PAPER_DEPLOYMENT_FILE_FIELDS or
                record.get("path") != str(path) or
                type(record.get("mode")) is not int or
                record.get("mode") != mode or
                not isinstance(record.get("sha256"), str) or
                DIGEST.fullmatch(str(record.get("sha256"))) is None or
                record.get("sha256") == ZERO_DIGEST):
            raise CampaignError(failure)
    body = dict(document)
    expected_body = body.pop("body_sha256")
    if _sha256(_canonical_json(body)) != expected_body:
        raise CampaignError(failure)
    return raw, document, identity


def load_local_paper_deployment(
        policy: CampaignPolicy,
) -> LocalDeploymentSnapshot:
    failure = "CAMPAIGN_DEPLOYMENT_EVIDENCE_INVALID"
    raw, evidence_identity = _stable_read_with_identity(
        LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH, installed=True,
        expected_uid=0, expected_gid=0, expected_mode=0o600)
    document = _strict_json(raw, "deployment_evidence")
    if raw != _canonical_json(document):
        raise CampaignError("CAMPAIGN_DEPLOYMENT_EVIDENCE_NON_CANONICAL")
    if (set(document) != LOCAL_PAPER_DEPLOYMENT_EVIDENCE_FIELDS or
            document.get("schema") !=
                LOCAL_PAPER_DEPLOYMENT_EVIDENCE_SCHEMA or
            document.get("version") != 1 or
            document.get("paper_authorized") is not False or
            document.get("live_authorized") is not False or
            document.get("mutation_authorized") is not False):
        raise CampaignError(failure)
    for field in ("source_freeze_commit", "source_freeze_tree"):
        item = document.get(field)
        if (not isinstance(item, str) or GIT_OBJECT.fullmatch(item) is None or
                item == ZERO_GIT_OBJECT):
            raise CampaignError(failure)
    for field in (
            "source_manifest_sha256", "source_baseline_sha256",
            "certified_install_closure_file_sha256",
            "certified_install_closure_body_sha256",
            "body_sha256"):
        item = document.get(field)
        if (not isinstance(item, str) or DIGEST.fullmatch(item) is None or
                item == ZERO_DIGEST):
            raise CampaignError(failure)
    transaction_id = document.get("install_transaction_id")
    if (not isinstance(transaction_id, str) or
            INSTALL_TRANSACTION.fullmatch(transaction_id) is None):
        raise CampaignError(failure)
    installed_at_ms = document.get("installed_at_ms")
    generated_at_ms = document.get("generated_at_ms")
    if (type(installed_at_ms) is not int or
            type(generated_at_ms) is not int or installed_at_ms < 0 or
            generated_at_ms < installed_at_ms or
            generated_at_ms > time.time_ns() // 1_000_000):
        raise CampaignError(failure)
    certified_raw, certified, certified_identity = (
        _load_certified_install_closure(
            str(document["certified_install_closure_file_sha256"]),
            str(document["certified_install_closure_body_sha256"])))
    del certified_raw
    for field in (
            "source_freeze_commit", "source_freeze_tree",
            "source_manifest_sha256", "source_baseline_sha256",
            "install_transaction_id", "installed_at_ms", "files"):
        if document.get(field) != certified.get(field):
            raise CampaignError(
                "CAMPAIGN_DEPLOYMENT_CERTIFICATION_MISMATCH")
    files = document.get("files")
    if (not isinstance(files, list) or
            len(files) != len(LOCAL_PAPER_DEPLOYMENT_FILES)):
        raise CampaignError(failure)
    identities: list[tuple[str, tuple[int, ...]]] = []
    for record, (path, mode) in zip(
            files, LOCAL_PAPER_DEPLOYMENT_FILES, strict=True):
        if (not isinstance(record, dict) or
                set(record) != LOCAL_PAPER_DEPLOYMENT_FILE_FIELDS or
                record.get("path") != str(path) or
                type(record.get("mode")) is not int or
                record.get("mode") != mode or
                not isinstance(record.get("sha256"), str) or
                DIGEST.fullmatch(str(record.get("sha256"))) is None or
                record.get("sha256") == ZERO_DIGEST):
            raise CampaignError(failure)
        identities.append((
            str(path), _snapshot_local_deployed_file(
                path, str(record["sha256"]), mode)))
    body = dict(document)
    expected_body = body.pop("body_sha256")
    if _sha256(_canonical_json(body)) != expected_body:
        raise CampaignError(failure)
    file_sha256 = _sha256(raw)
    if (policy.source_baseline_sha256 !=
            document["source_baseline_sha256"] or
            policy.deployment_evidence_file_sha256 != file_sha256 or
            policy.deployment_evidence_body_sha256 != expected_body or
            policy.deployment_install_transaction_id != transaction_id):
        raise CampaignError("CAMPAIGN_POLICY_DEPLOYMENT_MISMATCH")
    current = os.lstat(LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if evidence_identity != tuple(
            int(getattr(current, field)) for field in fields):
        raise CampaignError("CAMPAIGN_DEPLOYMENT_EVIDENCE_CHANGED")
    certified_current = os.lstat(CERTIFIED_INSTALL_CLOSURE_PATH)
    if certified_identity != tuple(
            int(getattr(certified_current, field)) for field in fields):
        raise CampaignError("CAMPAIGN_CERTIFIED_INSTALL_CLOSURE_CHANGED")
    return LocalDeploymentSnapshot(
        payload=raw, document=document, evidence_identity=evidence_identity,
        certified_identity=certified_identity,
        installed_identities=tuple(identities), file_sha256=file_sha256,
        body_sha256=str(expected_body),
        source_baseline_sha256=str(document["source_baseline_sha256"]),
        install_transaction_id=str(transaction_id))


def _integer(
        value: Any, code: str, *, minimum: int = 0,
        maximum: int = 2**63 - 1,
) -> int:
    if (
            isinstance(value, bool) or not isinstance(value, int) or
            value < minimum or value > maximum):
        raise CampaignError(code)
    return value


def _number(
        value: Any, code: str, *, positive: bool = False,
) -> float:
    if (
            isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(float(value)) or
            (positive and float(value) <= 0.0)):
        raise CampaignError(code)
    return float(value)


def _text(
        value: Any, code: str, *, pattern: Optional[re.Pattern[str]] = None,
        maximum: int = 1024,
) -> str:
    if (
            not isinstance(value, str) or not value or "\0" in value or
            len(value.encode("utf-8")) > maximum or
            (pattern is not None and pattern.fullmatch(value) is None)):
        raise CampaignError(code)
    return value


def _absolute_path(value: Any, code: str) -> Path:
    text = _text(value, code, maximum=4096)
    path = Path(text)
    if (
            not path.is_absolute() or text.startswith("//") or
            os.path.normpath(text) != text or path.name in {"", ".", ".."}):
        raise CampaignError(code)
    return path


def parse_policy(raw: bytes, expected_domain: str) -> CampaignPolicy:
    document = _strict_json(raw, "campaign_policy")
    legacy_v1 = (
        document.get("schema") == LEGACY_POLICY_SCHEMA and
        type(document.get("version")) is int and
        document.get("version") == 1 and
        set(document) == POLICY_V1_FIELDS)
    legacy_v2 = (
        document.get("schema") == LEGACY_POLICY_V2_SCHEMA and
        type(document.get("version")) is int and
        document.get("version") == 2 and
        set(document) == POLICY_V2_FIELDS)
    legacy_v3 = (
        document.get("schema") == LEGACY_POLICY_V3_SCHEMA and
        type(document.get("version")) is int and
        document.get("version") == 3 and
        set(document) == POLICY_V3_FIELDS)
    legacy_v4 = (
        document.get("schema") == LEGACY_POLICY_V4_SCHEMA and
        type(document.get("version")) is int and
        document.get("version") == 4 and
        set(document) == POLICY_V4_FIELDS)
    current_external = (
        document.get("schema") == POLICY_SCHEMA and
        type(document.get("version")) is int and
        document.get("version") == 5 and
        set(document) == POLICY_V5_FIELDS)
    current_local = (
        document.get("schema") == POLICY_SCHEMA and
        type(document.get("version")) is int and
        document.get("version") == 5 and
        set(document) == POLICY_V5_LOCAL_FIELDS)
    current = current_external or current_local
    if not any((legacy_v1, legacy_v2, legacy_v3, legacy_v4, current)):
        raise CampaignError("CAMPAIGN_POLICY_CONTRACT_INVALID")
    if (
            legacy_v2 or legacy_v3 or legacy_v4 or current
    ) and raw != _canonical_json(document):
        raise CampaignError("CAMPAIGN_POLICY_NON_CANONICAL")
    domain_id = _text(
        document["domain_id"], "CAMPAIGN_POLICY_DOMAIN_INVALID",
        pattern=DOMAIN, maximum=32)
    if domain_id != expected_domain:
        raise CampaignError("CAMPAIGN_POLICY_DOMAIN_MISMATCH")
    campaign_id = _text(
        document["campaign_id"], "CAMPAIGN_POLICY_ID_INVALID",
        pattern=IDENTIFIER, maximum=96)
    enabled = document["enabled"]
    mutations_authorized = document["mutations_authorized"]
    order_type = document["order_type"]
    expected_order_type = "MKT" if legacy_v4 or current_local else "LMT"
    if (
            not isinstance(enabled, bool) or
            not isinstance(mutations_authorized, bool) or
            document["paper_only"] is not True or
            document["live_authorized"] is not False or
            type(document["max_active_orders"]) is not int or
            document["max_active_orders"] != 1 or
            order_type != expected_order_type or document["tif"] != "DAY" or
            document["end_flat_required"] is not True):
        raise CampaignError("CAMPAIGN_POLICY_SAFETY_BOUNDARY_INVALID")
    if legacy_v1 and (enabled or mutations_authorized):
        raise CampaignError("CAMPAIGN_POLICY_V1_ACTIVE_FORBIDDEN")
    if legacy_v2 and (enabled or mutations_authorized):
        raise CampaignError("CAMPAIGN_POLICY_V2_ACTIVE_FORBIDDEN")
    if legacy_v4 and (enabled or mutations_authorized):
        raise CampaignError(
            "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED")
    if mutations_authorized and not enabled:
        raise CampaignError("CAMPAIGN_POLICY_DORMANT_AUTHORITY_FORBIDDEN")
    strategy_id = _text(
        document["strategy_id"], "CAMPAIGN_POLICY_STRATEGY_INVALID",
        pattern=STRATEGY, maximum=128)
    strategy_version = _text(
        document["strategy_version"], "CAMPAIGN_POLICY_STRATEGY_INVALID",
        pattern=STRATEGY, maximum=128)
    strategy_sha256 = _text(
        document["strategy_sha256"], "CAMPAIGN_POLICY_STRATEGY_DIGEST_INVALID",
        pattern=DIGEST, maximum=71)
    if enabled and strategy_sha256 == "sha256:" + "0" * 64:
        raise CampaignError("CAMPAIGN_POLICY_STRATEGY_DIGEST_INVALID")
    valid_after_ms = _integer(
        document["valid_after_ms"], "CAMPAIGN_POLICY_TIME_INVALID")
    expires_at_ms = _integer(
        document["expires_at_ms"], "CAMPAIGN_POLICY_TIME_INVALID")
    instruments = document["allowed_instruments"]
    if instruments != ["EUR.USD"]:
        raise CampaignError("CAMPAIGN_POLICY_INSTRUMENT_SCOPE_INVALID")
    max_cycles = _integer(
        document["max_cycles"], "CAMPAIGN_POLICY_CYCLE_LIMIT_INVALID",
        minimum=1, maximum=(
            MAX_LOCAL_CAMPAIGN_CYCLES if legacy_v4 else
            MAX_V5_CAMPAIGN_CYCLES if current else MAX_CAMPAIGN_CYCLES))
    max_quantity = _integer(
        document["max_quantity"], "CAMPAIGN_POLICY_QUANTITY_INVALID",
        minimum=1, maximum=MAX_ORDER_QUANTITY)
    if current_external and (max_cycles != 1 or max_quantity != 1):
        raise CampaignError("CAMPAIGN_POLICY_EXTERNAL_CANARY_INVALID")
    min_cycle_interval_ms = _integer(
        document["min_cycle_interval_ms"],
        "CAMPAIGN_POLICY_INTERVAL_INVALID",
        minimum=1_000, maximum=60 * 60 * 1000)
    operator_ttl_seconds = _integer(
        document["operator_ttl_seconds"], "CAMPAIGN_POLICY_TTL_INVALID",
        minimum=MIN_OPERATOR_TTL_SECONDS,
        maximum=MAX_OPERATOR_TTL_SECONDS)
    max_intent_horizon_ms = _integer(
        document["max_intent_horizon_ms"],
        "CAMPAIGN_POLICY_INTENT_HORIZON_INVALID",
        minimum=MIN_INTENT_HORIZON_MS, maximum=MAX_INTENT_HORIZON_MS)
    max_holding_ms = _integer(
        document["max_holding_ms"], "CAMPAIGN_POLICY_HOLDING_INVALID",
        minimum=0 if legacy_v4 or current else 1_000,
        maximum=MAX_HOLDING_MS)
    if (legacy_v4 or current) and 0 < max_holding_ms < 1_000:
        raise CampaignError("CAMPAIGN_POLICY_HOLDING_INVALID")
    maximum_duration_ms = (
        MAX_LOCAL_CAMPAIGN_DURATION_MS if legacy_v4 else
        MAX_V5_CAMPAIGN_DURATION_MS if current else
        MAX_CAMPAIGN_DURATION_MS)
    if enabled and (
            not mutations_authorized or valid_after_ms >= expires_at_ms or
            expires_at_ms - valid_after_ms > maximum_duration_ms):
        raise CampaignError("CAMPAIGN_POLICY_ACTIVE_WINDOW_INVALID")
    if current_external and (
            expires_at_ms - valid_after_ms !=
                EXTERNAL_P1_CAMPAIGN_DURATION_MS):
        raise CampaignError("CAMPAIGN_POLICY_EXTERNAL_CANARY_INVALID")
    admission_mode = "external-certified"
    if legacy_v4 or current:
        admission_mode = _text(
            document["admission_mode"],
            "CAMPAIGN_POLICY_ADMISSION_MODE_INVALID", maximum=32)
        expected_admission_mode = (
            "external-p1-finalized" if current_external else "local-only")
        if admission_mode != expected_admission_mode:
            raise CampaignError("CAMPAIGN_POLICY_ADMISSION_MODE_INVALID")
    source_baseline_sha256: str | None = None
    deployment_evidence_file_sha256: str | None = None
    deployment_evidence_body_sha256: str | None = None
    deployment_install_transaction_id: str | None = None
    admission_receipt_name: str | None = None
    admission_receipt_file_sha256: str | None = None
    admission_receipt_body_sha256: str | None = None
    finalization_current_pointer_path: Path | None = None
    finalization_current_pointer_file_sha256: str | None = None
    finalization_current_pointer_body_sha256: str | None = None
    finalization_tombstone_path: Path | None = None
    finalization_tombstone_file_sha256: str | None = None
    finalization_tombstone_body_sha256: str | None = None
    p1_audit_receipt_path: Path | None = None
    p1_audit_receipt_file_sha256: str | None = None
    p1_audit_receipt_body_sha256: str | None = None
    watch_handoff_receipt_path: Path | None = None
    watch_handoff_receipt_file_sha256: str | None = None
    watch_handoff_receipt_body_sha256: str | None = None
    if legacy_v2 or legacy_v3 or legacy_v4 or current:
        source_baseline_sha256 = _text(
            document["source_baseline_sha256"],
            "CAMPAIGN_POLICY_SOURCE_BASELINE_INVALID",
            pattern=DIGEST, maximum=71)
        if ((legacy_v4 or current) and
                source_baseline_sha256 == "sha256:" + "0" * 64):
            raise CampaignError("CAMPAIGN_POLICY_SOURCE_BASELINE_INVALID")
        if legacy_v4 or current:
            deployment_evidence_file_sha256 = _text(
                document["deployment_evidence_file_sha256"],
                "CAMPAIGN_POLICY_DEPLOYMENT_EVIDENCE_INVALID",
                pattern=DIGEST, maximum=71)
            deployment_evidence_body_sha256 = _text(
                document["deployment_evidence_body_sha256"],
                "CAMPAIGN_POLICY_DEPLOYMENT_EVIDENCE_INVALID",
                pattern=DIGEST, maximum=71)
            deployment_install_transaction_id = _text(
                document["deployment_install_transaction_id"],
                "CAMPAIGN_POLICY_DEPLOYMENT_EVIDENCE_INVALID",
                pattern=INSTALL_TRANSACTION, maximum=128)
            if (deployment_evidence_file_sha256 ==
                    "sha256:" + "0" * 64 or
                    deployment_evidence_body_sha256 ==
                    "sha256:" + "0" * 64):
                raise CampaignError(
                    "CAMPAIGN_POLICY_DEPLOYMENT_EVIDENCE_INVALID")
    if legacy_v2 or legacy_v3 or current_external:
        admission_receipt_name = _text(
            document["admission_receipt_name"],
            "CAMPAIGN_POLICY_ADMISSION_NAME_INVALID",
            pattern=SAFE_JSON_NAME, maximum=132)
        admission_receipt_file_sha256 = _text(
            document["admission_receipt_file_sha256"],
            "CAMPAIGN_POLICY_ADMISSION_FILE_DIGEST_INVALID",
            pattern=DIGEST, maximum=71)
        admission_receipt_body_sha256 = _text(
            document["admission_receipt_body_sha256"],
            "CAMPAIGN_POLICY_ADMISSION_BODY_DIGEST_INVALID",
            pattern=DIGEST, maximum=71)
        if any(value == "sha256:" + "0" * 64 for value in (
                source_baseline_sha256, admission_receipt_file_sha256,
                admission_receipt_body_sha256)):
            raise CampaignError("CAMPAIGN_POLICY_ADMISSION_PIN_INVALID")
    if legacy_v3 or current_external:
        finalization_current_pointer_path = _absolute_path(
            document["admission_finalization_current_pointer_path"],
            "CAMPAIGN_POLICY_FINALIZATION_POINTER_PATH_INVALID")
        finalization_tombstone_path = _absolute_path(
            document["admission_finalization_tombstone_path"],
            "CAMPAIGN_POLICY_FINALIZATION_TOMBSTONE_PATH_INVALID")
        finalization_current_pointer_file_sha256 = _text(
            document[
                "admission_finalization_current_pointer_file_sha256"],
            "CAMPAIGN_POLICY_FINALIZATION_POINTER_DIGEST_INVALID",
            pattern=DIGEST, maximum=71)
        finalization_current_pointer_body_sha256 = _text(
            document[
                "admission_finalization_current_pointer_body_sha256"],
            "CAMPAIGN_POLICY_FINALIZATION_POINTER_DIGEST_INVALID",
            pattern=DIGEST, maximum=71)
        finalization_tombstone_file_sha256 = _text(
            document["admission_finalization_tombstone_file_sha256"],
            "CAMPAIGN_POLICY_FINALIZATION_TOMBSTONE_DIGEST_INVALID",
            pattern=DIGEST, maximum=71)
        finalization_tombstone_body_sha256 = _text(
            document["admission_finalization_tombstone_body_sha256"],
            "CAMPAIGN_POLICY_FINALIZATION_TOMBSTONE_DIGEST_INVALID",
            pattern=DIGEST, maximum=71)
        if (
                finalization_current_pointer_path.name !=
                    "finalization-current.v1.json" or
                FINALIZATION_TOMBSTONE_NAME.fullmatch(
                    finalization_tombstone_path.name) is None or
                finalization_current_pointer_path.parent !=
                    finalization_tombstone_path.parent or
                any(value == "sha256:" + "0" * 64 for value in (
                    finalization_current_pointer_file_sha256,
                    finalization_current_pointer_body_sha256,
                    finalization_tombstone_file_sha256,
                    finalization_tombstone_body_sha256))):
            raise CampaignError("CAMPAIGN_POLICY_FINALIZATION_PIN_INVALID")
    if current_external:
        p1_audit_receipt_path = _absolute_path(
            document["p1_audit_receipt_path"],
            "CAMPAIGN_POLICY_P1_AUDIT_PATH_INVALID")
        watch_handoff_receipt_path = _absolute_path(
            document["watch_handoff_receipt_path"],
            "CAMPAIGN_POLICY_WATCH_HANDOFF_PATH_INVALID")
        p1_audit_receipt_file_sha256 = _text(
            document["p1_audit_receipt_file_sha256"],
            "CAMPAIGN_POLICY_P1_AUDIT_DIGEST_INVALID",
            pattern=DIGEST, maximum=71)
        p1_audit_receipt_body_sha256 = _text(
            document["p1_audit_receipt_body_sha256"],
            "CAMPAIGN_POLICY_P1_AUDIT_DIGEST_INVALID",
            pattern=DIGEST, maximum=71)
        watch_handoff_receipt_file_sha256 = _text(
            document["watch_handoff_receipt_file_sha256"],
            "CAMPAIGN_POLICY_WATCH_HANDOFF_DIGEST_INVALID",
            pattern=DIGEST, maximum=71)
        watch_handoff_receipt_body_sha256 = _text(
            document["watch_handoff_receipt_body_sha256"],
            "CAMPAIGN_POLICY_WATCH_HANDOFF_DIGEST_INVALID",
            pattern=DIGEST, maximum=71)
        if (
                p1_audit_receipt_path == watch_handoff_receipt_path or
                watch_handoff_receipt_path != EXTERNAL_P1_HANDOFF_PATH or
                any(value == ZERO_DIGEST for value in (
                    p1_audit_receipt_file_sha256,
                    p1_audit_receipt_body_sha256,
                    watch_handoff_receipt_file_sha256,
                    watch_handoff_receipt_body_sha256))):
            raise CampaignError("CAMPAIGN_POLICY_P1_HANDOFF_PIN_INVALID")
    return CampaignPolicy(
        version=(
            5 if current else 4 if legacy_v4 else 3 if legacy_v3 else
            2 if legacy_v2 else 1),
        campaign_id=campaign_id,
        domain_id=domain_id,
        enabled=enabled,
        mutations_authorized=mutations_authorized,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_sha256=strategy_sha256,
        valid_after_ms=valid_after_ms,
        expires_at_ms=expires_at_ms,
        allowed_instruments=tuple(instruments),
        max_cycles=max_cycles,
        max_quantity=max_quantity,
        min_cycle_interval_ms=min_cycle_interval_ms,
        operator_ttl_seconds=operator_ttl_seconds,
        max_intent_horizon_ms=max_intent_horizon_ms,
        max_holding_ms=max_holding_ms,
        order_type=order_type,
        admission_mode=admission_mode,
        source_baseline_sha256=source_baseline_sha256,
        deployment_evidence_file_sha256=
            deployment_evidence_file_sha256,
        deployment_evidence_body_sha256=
            deployment_evidence_body_sha256,
        deployment_install_transaction_id=
            deployment_install_transaction_id,
        admission_receipt_name=admission_receipt_name,
        admission_receipt_file_sha256=admission_receipt_file_sha256,
        admission_receipt_body_sha256=admission_receipt_body_sha256,
        finalization_current_pointer_path=
            finalization_current_pointer_path,
        finalization_current_pointer_file_sha256=
            finalization_current_pointer_file_sha256,
        finalization_current_pointer_body_sha256=
            finalization_current_pointer_body_sha256,
        finalization_tombstone_path=finalization_tombstone_path,
        finalization_tombstone_file_sha256=
            finalization_tombstone_file_sha256,
        finalization_tombstone_body_sha256=
            finalization_tombstone_body_sha256,
        p1_audit_receipt_path=p1_audit_receipt_path,
        p1_audit_receipt_file_sha256=p1_audit_receipt_file_sha256,
        p1_audit_receipt_body_sha256=p1_audit_receipt_body_sha256,
        watch_handoff_receipt_path=watch_handoff_receipt_path,
        watch_handoff_receipt_file_sha256=
            watch_handoff_receipt_file_sha256,
        watch_handoff_receipt_body_sha256=
            watch_handoff_receipt_body_sha256,
    )


def load_policy(
        path: Path, expected_domain: str, *,
        installed: bool = True,
) -> PolicySnapshot:
    raw, identity = _stable_read_with_identity(path, installed=installed)
    return PolicySnapshot(
        path=path, payload=raw, identity=identity,
        policy=parse_policy(raw, expected_domain), file_sha256=_sha256(raw))


_FILE_IDENTITY_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
    "st_size", "st_mtime_ns", "st_ctime_ns",
)
_DIRECTORY_IDENTITY_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_uid", "st_gid",
)


def _metadata_identity(
        metadata: os.stat_result, fields: tuple[str, ...],
) -> tuple[int, ...]:
    return tuple(int(getattr(metadata, field)) for field in fields)


def _open_anchored_directory(
        path: Path, *, expected_uid: int, expected_gid: int,
) -> tuple[list[int], list[tuple[int, str, tuple[int, ...]]]]:
    """Open every absolute-path component without following a symlink."""

    path_text = str(path)
    if (
            not path.is_absolute() or path == Path("/") or
            path_text.startswith("//") or
            os.path.normpath(path_text) != path_text):
        raise CampaignError("CAMPAIGN_ADMISSION_ROOT_UNSAFE")
    components = path.parts[1:]
    descriptors: list[int] = []
    links: list[tuple[int, str, tuple[int, ...]]] = []
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptors.append(os.open("/", flags))
        root_identity = _metadata_identity(
            os.fstat(descriptors[0]), _DIRECTORY_IDENTITY_FIELDS)
        links.append((-1, "/", root_identity))
        for component in components:
            parent_fd = descriptors[-1]
            named = os.stat(
                component, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(named.st_mode):
                raise CampaignError("CAMPAIGN_ADMISSION_ROOT_UNSAFE")
            identity = _metadata_identity(
                named, _DIRECTORY_IDENTITY_FIELDS)
            descriptor = os.open(component, flags, dir_fd=parent_fd)
            descriptors.append(descriptor)
            if identity != _metadata_identity(
                    os.fstat(descriptor), _DIRECTORY_IDENTITY_FIELDS):
                raise CampaignError("CAMPAIGN_ADMISSION_ROOT_CHANGED")
            links.append((parent_fd, component, identity))
        final = os.fstat(descriptors[-1])
        if (
                final.st_uid != expected_uid or
                final.st_gid != expected_gid or
                stat.S_IMODE(final.st_mode) & 0o022):
            raise CampaignError("CAMPAIGN_ADMISSION_ROOT_UNSAFE")
        return descriptors, links
    except CampaignError:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise
    except OSError as error:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise CampaignError("CAMPAIGN_ADMISSION_ROOT_UNSAFE") from error


def _assert_anchored_directory_unchanged(
        descriptors: list[int],
        links: list[tuple[int, str, tuple[int, ...]]],
) -> tuple[tuple[int, ...], ...]:
    identities: list[tuple[int, ...]] = []
    try:
        for index, (parent_fd, component, expected) in enumerate(links):
            opened = _metadata_identity(
                os.fstat(descriptors[index]), _DIRECTORY_IDENTITY_FIELDS)
            if parent_fd == -1:
                named = opened
            else:
                named = _metadata_identity(
                    os.stat(
                        component, dir_fd=parent_fd,
                        follow_symlinks=False),
                    _DIRECTORY_IDENTITY_FIELDS)
            if opened != expected or named != expected:
                raise CampaignError("CAMPAIGN_ADMISSION_ROOT_CHANGED")
            identities.append(expected)
    except OSError as error:
        raise CampaignError("CAMPAIGN_ADMISSION_ROOT_CHANGED") from error
    return tuple(identities)


def _read_anchored_artifact(
        path: Path, *, expected_uid: int, expected_gid: int,
        reason: str, expected_mode: int = 0o600,
        maximum_bytes: int = MAX_BYTES,
) -> tuple[bytes, tuple[int, ...], tuple[tuple[int, ...], ...]]:
    path = _absolute_path(str(path), reason)
    directory_fds, directory_links = _open_anchored_directory(
        path.parent, expected_uid=expected_uid, expected_gid=expected_gid)
    parent_fd = directory_fds[-1]
    descriptor: int | None = None
    try:
        try:
            named_before = os.stat(
                path.name, dir_fd=parent_fd, follow_symlinks=False)
            descriptor = os.open(
                path.name, os.O_RDONLY | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0) |
                getattr(os, "O_NONBLOCK", 0), dir_fd=parent_fd)
        except OSError as error:
            raise CampaignError(reason) from error
        opened = os.fstat(descriptor)
        if (
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_uid != expected_uid or
                opened.st_gid != expected_gid or
                stat.S_IMODE(opened.st_mode) != expected_mode or
                opened.st_size < 1 or opened.st_size > maximum_bytes or
                _metadata_identity(named_before, _FILE_IDENTITY_FIELDS) !=
                    _metadata_identity(opened, _FILE_IDENTITY_FIELDS)):
            raise CampaignError(reason)
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(
                descriptor,
                min(8192, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        named_after = os.stat(
            path.name, dir_fd=parent_fd, follow_symlinks=False)
        identity = _metadata_identity(opened, _FILE_IDENTITY_FIELDS)
        if (
                len(payload) > maximum_bytes or len(payload) != opened.st_size or
                identity != _metadata_identity(
                    after, _FILE_IDENTITY_FIELDS) or
                identity != _metadata_identity(
                    named_after, _FILE_IDENTITY_FIELDS)):
            raise CampaignError(reason)
        anchors = _assert_anchored_directory_unchanged(
            directory_fds, directory_links)
        return bytes(payload), identity, anchors
    except OSError as error:
        raise CampaignError(reason) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def _load_sealed_artifact(
        path: Path, *, fields: set[str], schema: str,
        expected_file_sha256: str | None = None,
        expected_body_sha256: str | None = None,
        expected_uid: int, expected_gid: int, reason: str,
) -> FinalizationArtifact:
    try:
        payload, identity, anchors = _read_anchored_artifact(
            path, expected_uid=expected_uid, expected_gid=expected_gid,
            reason=reason)
        document = _strict_json(payload, "finalization_artifact")
        if payload != _canonical_json(document) or set(document) != fields:
            raise CampaignError(reason)
        if document.get("schema") != schema or document.get("version") != 1:
            raise CampaignError(reason)
        claimed = document.get("body_sha256")
        if not isinstance(claimed, str) or DIGEST.fullmatch(claimed) is None:
            raise CampaignError(reason)
        body = dict(document)
        del body["body_sha256"]
        if _sha256(_canonical_json(body)) != claimed:
            raise CampaignError(reason)
        file_sha256 = _sha256(payload)
        if (
                expected_file_sha256 is not None and
                file_sha256 != expected_file_sha256):
            raise CampaignError(reason)
        if (
                expected_body_sha256 is not None and
                claimed != expected_body_sha256):
            raise CampaignError(reason)
        return FinalizationArtifact(
            path=path, payload=payload, identity=identity,
            anchor_identity=anchors, file_sha256=file_sha256,
            body_sha256=claimed, document=document)
    except CampaignError as error:
        if error.code == reason:
            raise
        raise CampaignError(reason) from error


def _load_pinned_reference_snapshot(
        path: Path, *, expected_file_sha256: str,
        expected_body_sha256: str, candidate_binding: dict[str, Any],
        expected_uid: int,
        expected_gid: int, reason: str,
) -> PinnedReferenceSnapshot:
    """Load one immutable policy-pinned JSON artifact through anchored FDs."""

    try:
        payload, identity, anchors = _read_anchored_artifact(
            path, expected_uid=expected_uid, expected_gid=expected_gid,
            reason=reason)
        document = _strict_json(payload, "pinned_reference")
        if payload != _canonical_json(document):
            raise CampaignError(reason)
        claimed_body = document.get("body_sha256")
        if (
                not isinstance(claimed_body, str) or
                DIGEST.fullmatch(claimed_body) is None or
                claimed_body == ZERO_DIGEST):
            raise CampaignError(reason)
        body = dict(document)
        del body["body_sha256"]
        if _sha256(_canonical_json(body)) != claimed_body:
            raise CampaignError(reason)
        file_sha256 = _sha256(payload)
        if (
                file_sha256 != expected_file_sha256 or
                claimed_body != expected_body_sha256):
            raise CampaignError(reason)
        if (
                not isinstance(candidate_binding, dict) or
                set(candidate_binding) != ADMISSION_BINDING_FIELDS or
                candidate_binding.get("path") != str(path) or
                candidate_binding.get("file_sha256") != file_sha256 or
                candidate_binding.get("body_sha256") != claimed_body or
                document.get("schema") != candidate_binding.get("schema") or
                document.get("version") != candidate_binding.get("version") or
                document.get("status") != candidate_binding.get("status")):
            raise CampaignError(reason)
        return PinnedReferenceSnapshot(
            path=path, payload=payload, document=document,
            identity=identity, anchor_identity=anchors,
            file_sha256=file_sha256, body_sha256=claimed_body)
    except (CampaignError, OSError) as error:
        if isinstance(error, CampaignError) and error.code == reason:
            raise
        raise CampaignError(reason) from error


def _external_p1_profile_evidence(
        path: Path, payload: bytes, identity: tuple[int, ...],
) -> dict[str, Any]:
    return {
        "path": str(path), "file_sha256": _sha256(payload),
        "bytes": len(payload), "mode": identity[2], "uid": identity[4],
        "gid": identity[5], "nlink": identity[3], "device": identity[0],
        "inode": identity[1], "mtime_ns": identity[7],
        "ctime_ns": identity[8],
    }


def _validate_external_p1_restoration_artifact(
        value: Any, *, path: Path, expected_uid: int, expected_gid: int,
        expected_sha256: str | None = None, expected_bytes: int | None = None,
        expected_mode: int = 0o600, sealed: bool = False,
) -> None:
    fields = (
        EXTERNAL_P1_PROFILE_SEALED_EVIDENCE_FIELDS if sealed else
        EXTERNAL_P1_PROFILE_EVIDENCE_FIELDS)
    reason = "CAMPAIGN_WATCH_HANDOFF_RESTORATION_INVALID"
    if (
            not isinstance(value, dict) or set(value) != fields or
            value.get("path") != str(path) or
            not isinstance(value.get("file_sha256"), str) or
            DIGEST.fullmatch(value["file_sha256"]) is None or
            value.get("file_sha256") == ZERO_DIGEST or
            type(value.get("bytes")) is not int or value["bytes"] < 1 or
            value.get("mode") != stat.S_IFREG | expected_mode or
            value.get("uid") != expected_uid or
            value.get("gid") != expected_gid or value.get("nlink") != 1 or
            any(type(value.get(field)) is not int or value[field] < 0
                for field in ("device", "inode", "mtime_ns", "ctime_ns")) or
            value.get("inode", 0) <= 0):
        raise CampaignError(reason)
    payload, identity, _anchors = _read_anchored_artifact(
        path, expected_uid=expected_uid, expected_gid=expected_gid,
        expected_mode=expected_mode,
        maximum_bytes=max(MAX_BYTES, int(value["bytes"])), reason=reason)
    evidence = _external_p1_profile_evidence(path, payload, identity)
    if evidence != {field: value[field] for field in EXTERNAL_P1_PROFILE_EVIDENCE_FIELDS}:
        raise CampaignError(reason)
    if (
            (expected_sha256 is not None and
             value["file_sha256"] != expected_sha256) or
            (expected_bytes is not None and value["bytes"] != expected_bytes)):
        raise CampaignError(reason)
    if sealed:
        try:
            document = _strict_json(payload, "external_p1_restoration")
        except CampaignError as error:
            raise CampaignError(reason) from error
        claimed = value.get("body_sha256")
        body = dict(document) if isinstance(document, dict) else {}
        document_claimed = body.pop("body_sha256", None)
        if (
                payload != _canonical_json(document) or
                not isinstance(claimed, str) or DIGEST.fullmatch(claimed) is None or
                claimed == ZERO_DIGEST or document_claimed != claimed or
                _sha256(_canonical_json(body)) != claimed):
            raise CampaignError(reason)


def _validate_external_p1_handoff_boundary(
        policy: CampaignPolicy, handoff: PinnedReferenceSnapshot, *,
        expected_uid: int, expected_gid: int,
) -> None:
    reason = "CAMPAIGN_WATCH_HANDOFF_RECEIPT_INVALID"
    document = handoff.document
    now_ms = time.time_ns() // 1_000_000
    if (
            policy.admission_mode != "external-p1-finalized" or
            handoff.path != EXTERNAL_P1_HANDOFF_PATH or
            set(document) != EXTERNAL_P1_HANDOFF_FIELDS or
            document.get("schema") != EXTERNAL_P1_HANDOFF_SCHEMA or
            document.get("version") != 2 or
            document.get("status") != EXTERNAL_P1_HANDOFF_STATUS or
            document.get("round") != 114 or document.get("domain") != "alpha" or
            document.get("campaign_id") != policy.campaign_id or
            document.get("source_baseline_sha256") !=
                policy.source_baseline_sha256 or
            document.get("production_mode") != "PRODUCTION_ROOT_SYSTEMD" or
            type(document.get("issued_at_ms")) is not int or
            type(document.get("expires_at_ms")) is not int or
            document["expires_at_ms"] - document["issued_at_ms"] != 300_000 or
            not document["issued_at_ms"] <= now_ms < document["expires_at_ms"] or
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
                EXTERNAL_P1_DISABLED_IDENTITY_SHA256 or
            document.get("paper_profile_restored") is not True or
            document.get("profile_candidate_absent") is not True or
            document.get("paper_runtime_profile_hardened") is not True or
            document.get("paper_runtime_profile_candidate_absent") is not True or
            document.get("crash_recovery_verified") is not True or
            document.get("cleanup_residue_count") != 0 or
            any(document.get(field) is not False for field in (
                "paper_authorized", "live_authorized", "mutation_authorized",
                "direct_broker_access", "order_submission_authorized"))):
        raise CampaignError(reason)
    if os.path.lexists(EXTERNAL_P1_CANDIDATE_PATH):
        raise CampaignError("CAMPAIGN_WATCH_HANDOFF_PROFILE_CANDIDATE_PRESENT")
    if os.path.lexists(EXTERNAL_P1_PAPER_PROFILE_CANDIDATE_PATH):
        raise CampaignError(
            "CAMPAIGN_EXTERNAL_P1_PAPER_PROFILE_CANDIDATE_PRESENT")
    restoration = document.get("paper_profile_restoration")
    if (
            not isinstance(restoration, dict) or
            set(restoration) != EXTERNAL_P1_PROFILE_RESTORATION_FIELDS or
            restoration.get("schema") !=
                "hepta.p1-watch-to-paper-profile-restoration.v1" or
            restoration.get("version") != 1 or
            restoration.get("status") != "DORMANT_PAPER_PROFILE_RESTORED" or
            restoration.get("candidate_path") != str(EXTERNAL_P1_CANDIDATE_PATH) or
            restoration.get("retired_watch_path") !=
                str(EXTERNAL_P1_RETIRED_WATCH_PATH) or
            restoration.get("exchange_method") != "RENAME_EXCHANGE" or
            restoration.get("forward_only_after_exchange") is not True or
            any(not isinstance(restoration.get(field), str) or
                DIGEST.fullmatch(restoration[field]) is None or
                restoration[field] == ZERO_DIGEST
                for field in ("restore_intent_record_sha256",
                              "restore_exchange_record_sha256"))):
        raise CampaignError("CAMPAIGN_WATCH_HANDOFF_RESTORATION_INVALID")
    for field, path, digest, size, mode, sealed in (
            ("target", EXTERNAL_P1_DORMANT_PROFILE_PATH,
             EXTERNAL_P1_DORMANT_PROFILE_SHA256,
             EXTERNAL_P1_DORMANT_PROFILE_BYTES, 0o644, False),
            ("dormant_backup", EXTERNAL_P1_DORMANT_BACKUP_PATH,
             EXTERNAL_P1_DORMANT_PROFILE_SHA256,
             EXTERNAL_P1_DORMANT_PROFILE_BYTES, 0o600, False),
            ("forward_retained_dormant", EXTERNAL_P1_FORWARD_RETAINED_PATH,
             EXTERNAL_P1_DORMANT_PROFILE_SHA256,
             EXTERNAL_P1_DORMANT_PROFILE_BYTES, 0o600, False),
            ("retired_watch", EXTERNAL_P1_RETIRED_WATCH_PATH,
             EXTERNAL_P1_WATCH_PROFILE_SHA256,
             EXTERNAL_P1_WATCH_PROFILE_BYTES, 0o600, False),
            ("forward_transition_receipt", EXTERNAL_P1_TRANSITION_PATH,
             None, None, 0o600, True),
            ("profile_deployment_receipt", EXTERNAL_P1_DEPLOYMENT_PATH,
             None, None, 0o600, True),
            ("forward_preimage_evidence", EXTERNAL_P1_PREIMAGE_PATH,
             None, None, 0o600, True)):
        _validate_external_p1_restoration_artifact(
            restoration.get(field), path=path,
            expected_uid=expected_uid, expected_gid=expected_gid,
            expected_sha256=digest, expected_bytes=size,
            expected_mode=mode, sealed=sealed)
    hardening = document.get("paper_runtime_profile_hardening")
    if (
            not isinstance(hardening, dict) or
            set(hardening) != EXTERNAL_P1_RUNTIME_HARDENING_FIELDS or
            hardening.get("schema") !=
                "hepta.p1-watch-to-paper-runtime-profile-hardening.v1" or
            hardening.get("version") != 1 or
            hardening.get("status") != "PAPER_RUNTIME_PROFILE_HARDENED" or
            hardening.get("candidate_path") !=
                str(EXTERNAL_P1_PAPER_PROFILE_CANDIDATE_PATH) or
            hardening.get("retained_legacy_path") !=
                str(EXTERNAL_P1_PAPER_PROFILE_RETAINED_PATH) or
            hardening.get("exchange_method") != "RENAME_EXCHANGE" or
            hardening.get("forward_only_after_exchange") is not True or
            any(not isinstance(hardening.get(field), str) or
                DIGEST.fullmatch(hardening[field]) is None or
                hardening[field] == ZERO_DIGEST
                for field in ("harden_intent_record_sha256",
                              "harden_exchange_record_sha256"))):
        raise CampaignError("CAMPAIGN_EXTERNAL_P1_PAPER_PROFILE_INVALID")
    for field, path, digest, size, mode in (
            ("target", EXTERNAL_P1_PAPER_PROFILE_PATH,
             EXTERNAL_P1_PAPER_PROFILE_SHA256,
             EXTERNAL_P1_PAPER_PROFILE_BYTES, 0o644),
            ("legacy_backup", EXTERNAL_P1_PAPER_PROFILE_BACKUP_PATH,
             EXTERNAL_P1_LEGACY_PAPER_PROFILE_SHA256,
             EXTERNAL_P1_LEGACY_PAPER_PROFILE_BYTES, 0o600),
            ("retained_legacy", EXTERNAL_P1_PAPER_PROFILE_RETAINED_PATH,
             EXTERNAL_P1_LEGACY_PAPER_PROFILE_SHA256,
             EXTERNAL_P1_LEGACY_PAPER_PROFILE_BYTES, 0o600)):
        _validate_external_p1_restoration_artifact(
            hardening.get(field), path=path,
            expected_uid=expected_uid, expected_gid=expected_gid,
            expected_sha256=digest, expected_bytes=size,
            expected_mode=mode, sealed=False)


def _load_policy_pinned_evidence(
        policy: CampaignPolicy, admission: AdmissionSnapshot, *,
        expected_uid: int,
        expected_gid: int,
) -> tuple[PinnedReferenceSnapshot, PinnedReferenceSnapshot]:
    if (
            policy.version != 5 or
            policy.p1_audit_receipt_path is None or
            policy.p1_audit_receipt_file_sha256 is None or
            policy.p1_audit_receipt_body_sha256 is None or
            policy.watch_handoff_receipt_path is None or
            policy.watch_handoff_receipt_file_sha256 is None or
        policy.watch_handoff_receipt_body_sha256 is None):
        raise CampaignError("CAMPAIGN_PINNED_EVIDENCE_POLICY_INVALID")
    bindings = admission.document.get("input_bindings")
    if not isinstance(bindings, dict):
        raise CampaignError("CAMPAIGN_PINNED_EVIDENCE_POLICY_INVALID")
    audit = _load_pinned_reference_snapshot(
        policy.p1_audit_receipt_path,
        expected_file_sha256=policy.p1_audit_receipt_file_sha256,
        expected_body_sha256=policy.p1_audit_receipt_body_sha256,
        candidate_binding=bindings.get("p1_audit_receipt"),
        expected_uid=expected_uid, expected_gid=expected_gid,
        reason="CAMPAIGN_P1_AUDIT_RECEIPT_INVALID")
    handoff = _load_pinned_reference_snapshot(
        policy.watch_handoff_receipt_path,
        expected_file_sha256=policy.watch_handoff_receipt_file_sha256,
        expected_body_sha256=policy.watch_handoff_receipt_body_sha256,
        candidate_binding=bindings.get("watch_handoff_receipt"),
        expected_uid=expected_uid, expected_gid=expected_gid,
        reason="CAMPAIGN_WATCH_HANDOFF_RECEIPT_INVALID")
    _validate_external_p1_handoff_boundary(
        policy, handoff, expected_uid=expected_uid, expected_gid=expected_gid)
    if audit.path == handoff.path or audit.identity == handoff.identity:
        raise CampaignError("CAMPAIGN_PINNED_EVIDENCE_COLLISION")
    return audit, handoff


def _reference(value: Any, reason: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != REFERENCE_FIELDS:
        raise CampaignError(reason)
    path = _absolute_path(value.get("path"), reason)
    for field in ("file_sha256", "body_sha256"):
        digest = value.get(field)
        if (
                not isinstance(digest, str) or
                DIGEST.fullmatch(digest) is None or
                digest == "sha256:" + "0" * 64):
            raise CampaignError(reason)
    return {**value, "path": str(path)}


def _reservation_reference(value: Any, reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != RESERVATION_REFERENCE_FIELDS:
        raise CampaignError(reason)
    _absolute_path(value.get("path"), reason)
    for field in ("file_sha256", "body_sha256"):
        digest = value.get(field)
        if (
                not isinstance(digest, str) or
                DIGEST.fullmatch(digest) is None or
                digest == "sha256:" + "0" * 64):
            raise CampaignError(reason)
    for field in (
            "device", "inode", "uid", "gid", "mode", "size", "mtime_ns",
            "ctime_ns"):
        _integer(value.get(field), reason)
    if value["inode"] <= 0 or value["size"] <= 0 or value["mode"] != 0o600:
        raise CampaignError(reason)
    return value


def _read_boot_id(
        path: Path, *, expected_uid: int, expected_gid: int,
) -> str:
    reason = "CAMPAIGN_ADMISSION_BOOT_ID_INVALID"
    path = _absolute_path(str(path), reason)
    directory_fds, directory_links = _open_anchored_directory(
        path.parent, expected_uid=expected_uid, expected_gid=expected_gid)
    descriptor: int | None = None
    try:
        parent_fd = directory_fds[-1]
        before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0) |
            getattr(os, "O_NONBLOCK", 0), dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        if (
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_uid != expected_uid or
                opened.st_gid != expected_gid or
                stat.S_IMODE(opened.st_mode) & 0o022 or
                _metadata_identity(before, _FILE_IDENTITY_FIELDS) !=
                    _metadata_identity(opened, _FILE_IDENTITY_FIELDS)):
            raise CampaignError(reason)
        payload = os.read(descriptor, 65)
        after = os.fstat(descriptor)
        named = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
                _metadata_identity(opened, _FILE_IDENTITY_FIELDS) !=
                    _metadata_identity(after, _FILE_IDENTITY_FIELDS) or
                _metadata_identity(opened, _FILE_IDENTITY_FIELDS) !=
                    _metadata_identity(named, _FILE_IDENTITY_FIELDS)):
            raise CampaignError(reason)
        _assert_anchored_directory_unchanged(directory_fds, directory_links)
        text = payload.decode("ascii", errors="strict")
        if not text.endswith("\n") or BOOT_ID.fullmatch(text[:-1]) is None:
            raise CampaignError(reason)
        return text[:-1]
    except (OSError, UnicodeError) as error:
        raise CampaignError(reason) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def validate_admission_receipt(
        raw: bytes, policy: CampaignPolicy, now_ms: int,
) -> tuple[str, int, int]:
    if policy.version not in {2, 3, 5}:
        raise CampaignError("CAMPAIGN_ADMISSION_POLICY_V2_REQUIRED")
    document = _strict_json(raw, "admission_receipt")
    if raw != _canonical_json(document):
        raise CampaignError("CAMPAIGN_ADMISSION_NON_CANONICAL")
    if set(document) != ADMISSION_FIELDS:
        raise CampaignError("CAMPAIGN_ADMISSION_CONTRACT_INVALID")
    claimed_body = document.get("body_sha256")
    if not isinstance(claimed_body, str) or DIGEST.fullmatch(claimed_body) is None:
        raise CampaignError("CAMPAIGN_ADMISSION_BODY_DIGEST_INVALID")
    body = dict(document)
    del body["body_sha256"]
    if _sha256(_canonical_json(body)) != claimed_body:
        raise CampaignError("CAMPAIGN_ADMISSION_BODY_DIGEST_INVALID")
    if claimed_body != policy.admission_receipt_body_sha256:
        raise CampaignError("CAMPAIGN_ADMISSION_BODY_PIN_MISMATCH")
    evaluated_at_ms = _integer(
        document.get("evaluated_at_ms"), "CAMPAIGN_ADMISSION_TIME_INVALID")
    expires_at_ms = _integer(
        document.get("expires_at_ms"), "CAMPAIGN_ADMISSION_TIME_INVALID")
    if not evaluated_at_ms <= now_ms < expires_at_ms:
        raise CampaignError("CAMPAIGN_ADMISSION_TIME_INVALID")
    if (
            policy.valid_after_ms < evaluated_at_ms or
            (policy.version != 5 and
             policy.expires_at_ms + policy.operator_ttl_seconds * 1000 >
             expires_at_ms) or
            now_ms + policy.operator_ttl_seconds * 1000 > expires_at_ms):
        raise CampaignError("CAMPAIGN_ADMISSION_WINDOW_INVALID")
    if (
            document.get("schema") != ADMISSION_SCHEMA or
            type(document.get("version")) is not int or
            document.get("version") != 1 or
            type(document.get("round")) is not int or
            document.get("round") != 114 or
            document.get("status") != "GO" or
            document.get("paper_test_admission_candidate") is not True or
            document.get("domain") != policy.domain_id or
            document.get("campaign_id") != policy.campaign_id or
            document.get("source_baseline_sha256") !=
                policy.source_baseline_sha256 or
            document.get("strategy_sha256") != policy.strategy_sha256 or
            document.get("authorization_effect") !=
                "NONE_READ_ONLY_CANDIDATE_ONLY" or
            document.get("findings") != [] or
            any(document.get(field) is not False for field in (
                "paper_authorized", "live_authorized", "mutation_authorized",
                "direct_broker_access", "order_submission_authorized"))):
        raise CampaignError("CAMPAIGN_ADMISSION_SEMANTIC_INVALID")
    bindings = document.get("input_bindings")
    if not isinstance(bindings, dict) or set(bindings) != ADMISSION_INPUT_NAMES:
        raise CampaignError("CAMPAIGN_ADMISSION_BINDINGS_INVALID")
    binding_paths: list[str] = []
    for binding in bindings.values():
        if (
                not isinstance(binding, dict) or
                set(binding) != ADMISSION_BINDING_FIELDS or
                not isinstance(binding.get("path"), str) or
                not binding["path"].startswith("/") or
                binding["path"].startswith("//") or
                "\0" in binding["path"] or
                len(binding["path"].encode("utf-8")) > 4096 or
                os.path.normpath(binding["path"]) != binding["path"] or
                not isinstance(binding.get("file_sha256"), str) or
                DIGEST.fullmatch(binding["file_sha256"]) is None or
                binding["file_sha256"] == "sha256:" + "0" * 64 or
                not isinstance(binding.get("body_sha256"), str) or
                DIGEST.fullmatch(binding["body_sha256"]) is None or
                binding["body_sha256"] == "sha256:" + "0" * 64 or
                not isinstance(binding.get("schema"), str) or
                not binding["schema"] or "\0" in binding["schema"] or
                len(binding["schema"].encode("utf-8")) > 256 or
                not (
                    binding.get("version") is None or
                    (type(binding["version"]) is int and
                     binding["version"] >= 0) or
                    (isinstance(binding["version"], str) and
                     bool(binding["version"]) and
                     "\0" not in binding["version"] and
                     len(binding["version"].encode("utf-8")) <= 128)) or
                not isinstance(binding.get("status"), str) or
                not binding["status"] or "\0" in binding["status"] or
                len(binding["status"].encode("utf-8")) > 256):
            raise CampaignError("CAMPAIGN_ADMISSION_BINDINGS_INVALID")
        binding_paths.append(binding["path"])
    if len(set(binding_paths)) != len(binding_paths):
        raise CampaignError("CAMPAIGN_ADMISSION_BINDINGS_INVALID")
    if policy.version == 5:
        direct_pins = {
            "p1_audit_receipt": {
                "path": str(policy.p1_audit_receipt_path),
                "file_sha256": policy.p1_audit_receipt_file_sha256,
                "body_sha256": policy.p1_audit_receipt_body_sha256,
            },
            "watch_handoff_receipt": {
                "path": str(policy.watch_handoff_receipt_path),
                "file_sha256": policy.watch_handoff_receipt_file_sha256,
                "body_sha256": policy.watch_handoff_receipt_body_sha256,
            },
        }
        for name, expected in direct_pins.items():
            binding = bindings[name]
            if any(binding.get(field) != value for field, value in
                   expected.items()):
                raise CampaignError(
                    "CAMPAIGN_ADMISSION_DIRECT_PIN_MISMATCH")
    return claimed_body, evaluated_at_ms, expires_at_ms


def load_admission_receipt(
        root: Path, policy: CampaignPolicy, now_ms: int, *,
        expected_uid: int = 0, expected_gid: int = 0,
) -> AdmissionSnapshot:
    name = policy.admission_receipt_name
    if (
            policy.version not in {2, 3, 5} or not isinstance(name, str) or
            SAFE_JSON_NAME.fullmatch(name) is None or
            "/" in name or name in {"", ".", ".."}):
        raise CampaignError("CAMPAIGN_ADMISSION_NAME_INVALID")
    root = Path(root)
    directory_fds, directory_links = _open_anchored_directory(
        root, expected_uid=expected_uid, expected_gid=expected_gid)
    root_fd = directory_fds[-1]
    descriptor: int | None = None
    try:
        try:
            named_before = os.stat(
                name, dir_fd=root_fd, follow_symlinks=False)
            descriptor = os.open(
                name, os.O_RDONLY | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0) |
                getattr(os, "O_NONBLOCK", 0), dir_fd=root_fd)
        except FileNotFoundError as error:
            raise CampaignError("CAMPAIGN_ADMISSION_MISSING") from error
        except OSError as error:
            raise CampaignError("CAMPAIGN_ADMISSION_METADATA_UNSAFE") from error
        opened = os.fstat(descriptor)
        if (
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_uid != expected_uid or opened.st_gid != expected_gid or
                stat.S_IMODE(opened.st_mode) != 0o600 or
                opened.st_size < 2 or opened.st_size > MAX_BYTES or
                _metadata_identity(named_before, _FILE_IDENTITY_FIELDS) !=
                _metadata_identity(opened, _FILE_IDENTITY_FIELDS)):
            raise CampaignError("CAMPAIGN_ADMISSION_METADATA_UNSAFE")
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
        named_after = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        identity = _metadata_identity(opened, _FILE_IDENTITY_FIELDS)
        if (
                total > MAX_BYTES or
                identity != _metadata_identity(after, _FILE_IDENTITY_FIELDS) or
                identity !=
                    _metadata_identity(named_after, _FILE_IDENTITY_FIELDS)):
            raise CampaignError("CAMPAIGN_ADMISSION_CHANGED")
        anchor_identity = _assert_anchored_directory_unchanged(
            directory_fds, directory_links)
        raw = b"".join(chunks)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)
    file_sha256 = _sha256(raw)
    if file_sha256 != policy.admission_receipt_file_sha256:
        raise CampaignError("CAMPAIGN_ADMISSION_FILE_PIN_MISMATCH")
    body_sha256, evaluated_at_ms, expires_at_ms = validate_admission_receipt(
        raw, policy, now_ms)
    return AdmissionSnapshot(
        path=root / name, payload=raw, identity=identity,
        anchor_identity=anchor_identity, file_sha256=file_sha256,
        body_sha256=body_sha256, evaluated_at_ms=evaluated_at_ms,
        expires_at_ms=expires_at_ms, document=_strict_json(
            raw, "admission_receipt"))


def _false_boundary(document: dict[str, Any], reason: str) -> None:
    if any(document.get(field) is not False for field in BOUNDARY_FIELDS):
        raise CampaignError(reason)


def _validate_finalized_admission_chain(
        *, policy: CampaignPolicy, admission: AdmissionSnapshot,
        pointer: FinalizationArtifact, tombstone: FinalizationArtifact,
        zero: FinalizationArtifact, lease_reference: dict[str, Any],
        boot_id: str, now_ms: int,
) -> None:
    reason = "CAMPAIGN_ADMISSION_FINALIZATION_INVALID"
    if policy.version not in {3, 5}:
        raise CampaignError("CAMPAIGN_ADMISSION_POLICY_V3_REQUIRED")
    pointer_document = pointer.document
    terminal = tombstone.document
    zero_document = zero.document
    reservation_id = pointer_document.get("reservation_id")
    if (
            not isinstance(reservation_id, str) or
            RESERVATION_ID.fullmatch(reservation_id) is None):
        raise CampaignError(reason)
    match = FINALIZATION_TOMBSTONE_NAME.fullmatch(tombstone.path.name)
    if match is None or match.group(1) != reservation_id:
        raise CampaignError(reason)
    generation = _integer(
        pointer_document.get("reservation_generation"), reason, minimum=1)
    predecessor = pointer_document.get(
        "predecessor_finalization_body_sha256")
    prior_pointer = terminal.get("prior_finalization_pointer_reference")
    if generation == 1:
        if predecessor is not None or prior_pointer is not None:
            raise CampaignError(reason)
    else:
        if (
                not isinstance(predecessor, str) or
                DIGEST.fullmatch(predecessor) is None or
                predecessor == "sha256:" + "0" * 64):
            raise CampaignError(reason)
        if _reference(prior_pointer, reason)["path"] != str(pointer.path):
            raise CampaignError(reason)
    finalized_at_ms = _integer(
        terminal.get("finalized_at_ms"), reason)
    updated_at_ms = _integer(
        pointer_document.get("updated_at_ms"), reason)
    if finalized_at_ms != updated_at_ms or finalized_at_ms > now_ms + 5_000:
        raise CampaignError(reason)
    reservation_reference = _reservation_reference(
        terminal.get("reservation_reference"), reason)
    _reference(terminal.get("candidate_reference"), reason)
    _reference(terminal.get("zero_exposure_receipt_reference"), reason)
    if (
            pointer.path != policy.finalization_current_pointer_path or
            tombstone.path != policy.finalization_tombstone_path or
            pointer_document.get("status") != "CURRENT" or
            terminal.get("status") != "ADMISSION_GO" or
            pointer_document.get("round") != 114 or
            terminal.get("round") != 114 or
            pointer_document.get("domain") != policy.domain_id or
            terminal.get("domain") != policy.domain_id or
            pointer_document.get("campaign_id") != policy.campaign_id or
            terminal.get("campaign_id") != policy.campaign_id or
            pointer_document.get("source_baseline_sha256") !=
                policy.source_baseline_sha256 or
            terminal.get("source_baseline_sha256") !=
                policy.source_baseline_sha256 or
            pointer_document.get("boot_id") != boot_id or
            terminal.get("boot_id") != boot_id or
            pointer_document.get("reservation_id") !=
                terminal.get("reservation_id") or
            terminal.get("reservation_generation") != generation or
            terminal.get("predecessor_finalization_body_sha256") !=
                predecessor or
            pointer_document.get("finalization_tombstone_reference") !=
                tombstone.reference or
            pointer_document.get("host_authority_lease") !=
                lease_reference or
            terminal.get("host_authority_lease") != lease_reference or
            reservation_reference.get("path") !=
                lease_reference["owner_path"] or
            terminal.get("candidate_reference") != admission.reference or
            terminal.get("zero_exposure_receipt_reference") !=
                zero.reference or
            terminal.get("recovery_observation") is not None or
            terminal.get("recovery_reason") is not None or
            terminal.get("owner_present_at_tombstone_commit") is not True or
            terminal.get("owner_removal_required_after_commit") is not True or
            terminal.get("finalization_order") != FINALIZATION_ORDER):
        raise CampaignError(reason)
    _false_boundary(pointer_document, reason)
    _false_boundary(terminal, reason)

    candidate_zero = admission.document.get("input_bindings", {}).get(
        "zero_exposure_receipt")
    expected_candidate_zero = {
        "path": str(zero.path),
        "file_sha256": zero.file_sha256,
        "body_sha256": zero.body_sha256,
        "schema": zero.document.get("schema"),
        "version": zero.document.get("version"),
        "status": zero.document.get("status"),
    }
    observed = _integer(zero_document.get("observed_at_ms"), reason)
    expires = _integer(zero_document.get("expires_at_ms"), reason)
    zero_generation = _integer(
        zero_document.get("reservation_generation"), reason, minimum=1)
    if (
            zero_document.get("status") != "PASS" or
            zero_document.get("round") != 114 or
            zero_document.get("domain") != policy.domain_id or
            zero_document.get("campaign_id") != policy.campaign_id or
            zero_document.get("source_baseline_sha256") !=
                policy.source_baseline_sha256 or
            observed > now_ms or not observed < expires or now_ms >= expires or
            candidate_zero != expected_candidate_zero or
            zero_document.get("host_authority_reservation") !=
                reservation_reference or
            zero_document.get("reservation_id") != reservation_id or
            zero_generation != generation or
            zero_document.get(
                "reservation_predecessor_finalization_body_sha256") !=
                predecessor or
            zero_document.get(
                "reservation_prior_finalization_pointer_reference") !=
                prior_pointer or
            zero_document.get("reservation_lifecycle") !=
                RESERVATION_LIFECYCLE or
            zero_document.get("reservation_next_consumer") !=
                RESERVATION_NEXT_CONSUMER or
            zero_document.get("reservation_finalization_tombstone_path") !=
                str(tombstone.path) or
            zero_document.get(
                "reservation_finalization_current_pointer_path") !=
                str(pointer.path) or
            zero_document.get("reservation_finalization_schema") !=
                FINALIZATION_SCHEMA or
            zero_document.get("reservation_finalization_order") !=
                FINALIZATION_ORDER or
            zero_document.get("reservation_boot_id") != boot_id or
            zero_document.get("reservation_lease_device") !=
                lease_reference["lease_device"] or
            zero_document.get("reservation_lease_inode") !=
                lease_reference["lease_inode"] or
            zero_document.get("host_authority_lease") != lease_reference or
            zero_document.get("reservation_continuity_verified") is not True or
            zero_document.get(
                "reservation_finalization_tombstone_absent") is not True or
            zero_document.get("host_authority_lease_reacquired") is not True or
            zero_document.get("read_only_authority") is not True or
            zero_document.get("authoritative") is not True or
            zero_document.get("account_complete") is not True or
            zero_document.get("observation_complete") is not True or
            zero_document.get("broker_deny_all") is not True or
            zero_document.get("authorized_connectors") != 0 or
            zero_document.get("authorized_uids") != [] or
            zero_document.get("broker_socket_count") != 0 or
            zero_document.get("broker_process_count") != 0 or
            zero_document.get("credential_exposure_count") != 0 or
            zero_document.get("order_count") != 0 or
            zero_document.get("position_count") != 0 or
            zero_document.get("gross_absolute_position") != 0 or
            zero_document.get("end_flat") is not True or
            zero_document.get("paper_units_inactive") is not True or
            zero_document.get("kill_switch_engaged") is not True or
            zero_document.get("process_inventory_complete") is not True or
            zero_document.get("socket_inventory_complete") is not True or
            zero_document.get("credential_inventory_complete") is not True):
        raise CampaignError(reason)
    _false_boundary(zero_document, reason)


def _load_finalized_admission_artifacts(
        admission_root: Path, policy: CampaignPolicy, now_ms: int, *,
        lease_reference: dict[str, Any], boot_id: str,
        expected_uid: int, expected_gid: int,
) -> tuple[
        AdmissionSnapshot, FinalizationArtifact,
        FinalizationArtifact, FinalizationArtifact]:
    reason = "CAMPAIGN_ADMISSION_FINALIZATION_INVALID"
    if (
            policy.version not in {3, 5} or
            policy.finalization_current_pointer_path is None or
            policy.finalization_tombstone_path is None):
        raise CampaignError("CAMPAIGN_ADMISSION_POLICY_V3_REQUIRED")
    pointer = _load_sealed_artifact(
        policy.finalization_current_pointer_path,
        fields=FINALIZATION_POINTER_FIELDS,
        schema=FINALIZATION_POINTER_SCHEMA,
        expected_file_sha256=
            policy.finalization_current_pointer_file_sha256,
        expected_body_sha256=
            policy.finalization_current_pointer_body_sha256,
        expected_uid=expected_uid, expected_gid=expected_gid, reason=reason)
    tombstone = _load_sealed_artifact(
        policy.finalization_tombstone_path,
        fields=FINALIZATION_FIELDS, schema=FINALIZATION_SCHEMA,
        expected_file_sha256=policy.finalization_tombstone_file_sha256,
        expected_body_sha256=policy.finalization_tombstone_body_sha256,
        expected_uid=expected_uid, expected_gid=expected_gid, reason=reason)
    admission = load_admission_receipt(
        admission_root, policy, now_ms, expected_uid=expected_uid,
        expected_gid=expected_gid)
    zero_reference = tombstone.document.get("zero_exposure_receipt_reference")
    zero_path = _absolute_path(
        zero_reference.get("path") if isinstance(zero_reference, dict) else
        None, reason)
    zero = _load_sealed_artifact(
        zero_path, fields=ZERO_EXPOSURE_FIELDS, schema=ZERO_EXPOSURE_SCHEMA,
        expected_file_sha256=(
            zero_reference.get("file_sha256")
            if isinstance(zero_reference, dict) else None),
        expected_body_sha256=(
            zero_reference.get("body_sha256")
            if isinstance(zero_reference, dict) else None),
        expected_uid=expected_uid, expected_gid=expected_gid, reason=reason)
    _validate_finalized_admission_chain(
        policy=policy, admission=admission, pointer=pointer,
        tombstone=tombstone, zero=zero, lease_reference=lease_reference,
        boot_id=boot_id, now_ms=now_ms)
    return admission, pointer, tombstone, zero


class FinalizedAdmissionSession:
    """Continuously locked view of one completed admission finalization."""

    def __init__(
            self, *, admission_root: Path, policy: CampaignPolicy,
            expected_uid: int, expected_gid: int,
            host_authority_root: Path, boot_id_path: Path,
            directory_fds: list[int], directory_links: list[
                tuple[int, str, tuple[int, ...]]],
            lease_descriptor: int, lease_identity: tuple[int, ...],
            lease_reference: dict[str, Any], boot_id: str,
            validation_time_ms: int,
            admission: AdmissionSnapshot, pointer: FinalizationArtifact,
            tombstone: FinalizationArtifact, zero: FinalizationArtifact,
    ) -> None:
        self.admission_root = admission_root
        self.policy = policy
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid
        self.host_authority_root = host_authority_root
        self.boot_id_path = boot_id_path
        self.directory_fds = directory_fds
        self.directory_links = directory_links
        self.lease_descriptor = lease_descriptor
        self.lease_identity = lease_identity
        self.lease_reference = lease_reference
        self.boot_id = boot_id
        self.validation_time_ms = validation_time_ms
        self.admission = admission
        self.pointer = pointer
        self.tombstone = tombstone
        self.zero = zero
        self.reopen_count = 0
        self.closed = False

    def __enter__(self) -> "FinalizedAdmissionSession":
        if self.closed:
            raise CampaignError("CAMPAIGN_ADMISSION_SESSION_CLOSED")
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def _assert_lease_and_owner(self) -> None:
        reason = "CAMPAIGN_ADMISSION_FINALIZATION_CHANGED"
        if self.closed:
            raise CampaignError(reason)
        root_fd = self.directory_fds[-1]
        try:
            fcntl.flock(
                self.lease_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            opened = os.fstat(self.lease_descriptor)
            named = os.stat(
                "lease.lock", dir_fd=root_fd, follow_symlinks=False)
            if (
                    _metadata_identity(opened, _FILE_IDENTITY_FIELDS) !=
                        self.lease_identity or
                    _metadata_identity(named, _FILE_IDENTITY_FIELDS) !=
                        self.lease_identity):
                raise CampaignError(reason)
            _assert_anchored_directory_unchanged(
                self.directory_fds, self.directory_links)
            try:
                os.stat("owner.v1", dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise CampaignError(reason)
            if _read_boot_id(
                    self.boot_id_path, expected_uid=self.expected_uid,
                    expected_gid=self.expected_gid) != self.boot_id:
                raise CampaignError(reason)
        except OSError as error:
            raise CampaignError(reason) from error

    def reopen(self, now_ms: int) -> AdmissionSnapshot:
        self._assert_lease_and_owner()
        validation_time_ms = (
            self.validation_time_ms if self.policy.version == 5 else now_ms)
        current = _load_finalized_admission_artifacts(
            self.admission_root, self.policy, validation_time_ms,
            lease_reference=self.lease_reference, boot_id=self.boot_id,
            expected_uid=self.expected_uid, expected_gid=self.expected_gid)
        if current != (
                self.admission, self.pointer, self.tombstone, self.zero):
            raise CampaignError("CAMPAIGN_ADMISSION_FINALIZATION_CHANGED")
        self._assert_lease_and_owner()
        self.reopen_count += 1
        return self.admission

    def close(self) -> None:
        if self.closed:
            return
        failure: OSError | None = None
        try:
            fcntl.flock(self.lease_descriptor, fcntl.LOCK_UN)
        except OSError as error:
            failure = error
        for descriptor in (
                self.lease_descriptor, *reversed(self.directory_fds)):
            try:
                os.close(descriptor)
            except OSError as error:
                if failure is None:
                    failure = error
        self.closed = True
        if failure is not None:
            raise CampaignError(
                "CAMPAIGN_ADMISSION_LEASE_RELEASE_FAILED") from failure


def open_finalized_admission_session(
        admission_root: Path, policy: CampaignPolicy, now_ms: int, *,
        expected_uid: int = 0, expected_gid: int = 0,
        host_authority_root: Path = HOST_AUTHORITY_DIRECTORY,
        boot_id_path: Path = BOOT_ID_PATH,
) -> FinalizedAdmissionSession:
    reason = "CAMPAIGN_ADMISSION_FINALIZATION_INVALID"
    if policy.version not in {3, 5}:
        raise CampaignError("CAMPAIGN_ADMISSION_POLICY_V3_REQUIRED")
    host_authority_root = _absolute_path(str(host_authority_root), reason)
    if (
            policy.finalization_current_pointer_path !=
                host_authority_root / "finalization-current.v1.json" or
            policy.finalization_tombstone_path is None or
            policy.finalization_tombstone_path.parent != host_authority_root):
        raise CampaignError(reason)
    directory_fds, directory_links = _open_anchored_directory(
        host_authority_root, expected_uid=expected_uid,
        expected_gid=expected_gid)
    lease_descriptor: int | None = None
    locked = False
    try:
        root_fd = directory_fds[-1]
        directory = os.fstat(root_fd)
        if (
                directory.st_uid != expected_uid or
                directory.st_gid != expected_gid or
                stat.S_IMODE(directory.st_mode) != 0o700):
            raise CampaignError(reason)
        before = os.stat(
            "lease.lock", dir_fd=root_fd, follow_symlinks=False)
        lease_descriptor = os.open(
            "lease.lock", os.O_RDONLY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0) |
            getattr(os, "O_NONBLOCK", 0), dir_fd=root_fd)
        opened = os.fstat(lease_descriptor)
        lease_identity = _metadata_identity(opened, _FILE_IDENTITY_FIELDS)
        if (
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_uid != expected_uid or
                opened.st_gid != expected_gid or
                stat.S_IMODE(opened.st_mode) != 0o600 or
                opened.st_size != 0 or
                _metadata_identity(before, _FILE_IDENTITY_FIELDS) !=
                    lease_identity):
            raise CampaignError(reason)
        try:
            fcntl.flock(
                lease_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise CampaignError(
                    "CAMPAIGN_ADMISSION_FINALIZATION_BUSY") from error
            raise CampaignError(reason) from error
        _assert_anchored_directory_unchanged(
            directory_fds, directory_links)
        try:
            os.stat("owner.v1", dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise CampaignError("CAMPAIGN_ADMISSION_FINALIZATION_INCOMPLETE")
        boot_id = _read_boot_id(
            boot_id_path, expected_uid=expected_uid,
            expected_gid=expected_gid)
        lease_reference = {
            "directory_path": str(host_authority_root),
            "lease_path": str(host_authority_root / "lease.lock"),
            "owner_path": str(host_authority_root / "owner.v1"),
            "directory_device": directory.st_dev,
            "directory_inode": directory.st_ino,
            "directory_uid": directory.st_uid,
            "directory_gid": directory.st_gid,
            "directory_mode": stat.S_IMODE(directory.st_mode),
            "lease_device": opened.st_dev,
            "lease_inode": opened.st_ino,
            "lease_uid": opened.st_uid,
            "lease_gid": opened.st_gid,
            "lease_mode": stat.S_IMODE(opened.st_mode),
            "lease_size": opened.st_size,
            "held_exclusive": True,
            "boot_id": boot_id,
        }
        if set(lease_reference) != HOST_AUTHORITY_LEASE_FIELDS:
            raise CampaignError(reason)
        artifacts = _load_finalized_admission_artifacts(
            admission_root, policy, now_ms,
            lease_reference=lease_reference, boot_id=boot_id,
            expected_uid=expected_uid, expected_gid=expected_gid)
        session = FinalizedAdmissionSession(
            admission_root=admission_root, policy=policy,
            expected_uid=expected_uid, expected_gid=expected_gid,
            host_authority_root=host_authority_root,
            boot_id_path=boot_id_path, directory_fds=directory_fds,
            directory_links=directory_links,
            lease_descriptor=lease_descriptor,
            lease_identity=lease_identity, lease_reference=lease_reference,
            boot_id=boot_id, validation_time_ms=now_ms,
            admission=artifacts[0], pointer=artifacts[1],
            tombstone=artifacts[2], zero=artifacts[3])
        return session
    except Exception:
        if lease_descriptor is not None:
            if locked:
                try:
                    fcntl.flock(lease_descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(lease_descriptor)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)
        raise


def validate_trade_intent(
        value: Any, policy: CampaignPolicy, now_ms: int,
) -> tuple[dict[str, Any], str]:
    market_intent = (
        policy.version == 4 or
        (policy.version == 5 and policy.admission_mode == "local-only"))
    expected_fields = (
        TRADE_INTENT_FIELDS if market_intent else
        LEGACY_TRADE_INTENT_FIELDS)
    expected_schema = (
        TRADE_INTENT_SCHEMA if market_intent else
        LEGACY_TRADE_INTENT_SCHEMA)
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise CampaignError("CAMPAIGN_INTENT_CONTRACT_INVALID")
    if value["schema"] != expected_schema or value["paper_only"] is not True:
        raise CampaignError("CAMPAIGN_INTENT_PAPER_BOUNDARY_INVALID")
    strategy_sha256 = _text(
        value["strategy_sha256"], "CAMPAIGN_INTENT_STRATEGY_DIGEST_INVALID",
        pattern=DIGEST, maximum=71)
    if (
            value["strategy_id"] != policy.strategy_id or
            value["strategy_version"] != policy.strategy_version or
            strategy_sha256 != policy.strategy_sha256):
        raise CampaignError("CAMPAIGN_INTENT_STRATEGY_MISMATCH")
    _text(
        value["intent_id"], "CAMPAIGN_INTENT_ID_INVALID",
        pattern=IDENTIFIER, maximum=96)
    if (
            value["instrument"] not in policy.allowed_instruments or
            value["instrument"] != "EUR.USD" or value["symbol"] != "EUR" or
            value["currency"] != "USD" or value["sec_type"] != "CASH" or
            value["exchange"] != "IDEALPRO"):
        raise CampaignError("CAMPAIGN_INTENT_INSTRUMENT_INVALID")
    if (
            value["side"] not in {"BUY", "SELL"} or
            value["order_type"] != policy.order_type or
            value["tif"] != "DAY"):
        raise CampaignError("CAMPAIGN_INTENT_ORDER_CONTRACT_INVALID")
    quantity = _integer(
        value["quantity"], "CAMPAIGN_INTENT_QUANTITY_INVALID",
        minimum=1, maximum=policy.max_quantity)
    del quantity
    observed_bid = _number(
        value["observed_bid"], "CAMPAIGN_INTENT_QUOTE_INVALID",
        positive=True)
    observed_ask = _number(
        value["observed_ask"], "CAMPAIGN_INTENT_QUOTE_INVALID",
        positive=True)
    if observed_bid > observed_ask:
        raise CampaignError("CAMPAIGN_INTENT_QUOTE_INVALID")
    if market_intent:
        reference_price = _number(
            value["reference_price"], "CAMPAIGN_INTENT_PRICE_INVALID",
            positive=True)
        expected_reference = (
            observed_ask if value["side"] == "BUY" else observed_bid)
        if reference_price != expected_reference:
            raise CampaignError("CAMPAIGN_INTENT_QUOTE_INVALID")
    else:
        limit_price = _number(
            value["limit_price"], "CAMPAIGN_INTENT_PRICE_INVALID",
            positive=True)
        if policy.version == 5:
            expected_limit = (
                observed_ask if value["side"] == "BUY" else observed_bid)
            if limit_price != expected_limit:
                raise CampaignError("CAMPAIGN_INTENT_QUOTE_INVALID")
    observed_at_ms = _integer(
        value["observed_at_ms"], "CAMPAIGN_INTENT_TIME_INVALID")
    expires_at_ms = _integer(
        value["expires_at_ms"], "CAMPAIGN_INTENT_TIME_INVALID")
    if (
            observed_at_ms > now_ms + 1_000 or
            now_ms - observed_at_ms > policy.max_intent_horizon_ms or
            expires_at_ms <= now_ms + 1_000 or
            expires_at_ms > now_ms + policy.max_intent_horizon_ms or
            expires_at_ms > policy.expires_at_ms):
        raise CampaignError("CAMPAIGN_INTENT_TIME_INVALID")
    if policy.version in {4, 5} and policy.max_holding_ms == 0:
        max_holding_ms = _integer(
            value["max_holding_ms"], "CAMPAIGN_INTENT_HOLDING_INVALID",
            minimum=0, maximum=0)
    else:
        max_holding_ms = _integer(
            value["max_holding_ms"], "CAMPAIGN_INTENT_HOLDING_INVALID",
            minimum=1_000, maximum=policy.max_holding_ms)
    del max_holding_ms
    if (
            _number(
                value["max_adverse_move"],
                "CAMPAIGN_INTENT_RISK_VALUE_INVALID") < 0.0 or
            _number(
                value["expected_slippage"],
                "CAMPAIGN_INTENT_RISK_VALUE_INVALID") < 0.0):
        raise CampaignError("CAMPAIGN_INTENT_RISK_VALUE_INVALID")
    for field in (
            "entry_thesis", "invalidation_condition", "exit_plan"):
        _text(
            value[field], "CAMPAIGN_INTENT_REASONING_INVALID",
            maximum=2048)
    canonical = _canonical_json(value)
    return value, _sha256(canonical)


def parse_request(raw: bytes) -> dict[str, Any]:
    request = _strict_json(raw, "campaign_request")
    action = request.get("action")
    if action not in REQUEST_FIELDS or set(request) != REQUEST_FIELDS[action]:
        raise CampaignError("CAMPAIGN_REQUEST_CONTRACT_INVALID")
    if (
            request.get("schema") != REQUEST_SCHEMA or
            request.get("version") != 1):
        raise CampaignError("CAMPAIGN_REQUEST_VERSION_INVALID")
    _text(
        request["request_id"], "CAMPAIGN_REQUEST_ID_INVALID",
        pattern=IDENTIFIER, maximum=96)
    _text(
        request["domain_id"], "CAMPAIGN_REQUEST_DOMAIN_INVALID",
        pattern=DOMAIN, maximum=32)
    _text(
        request["campaign_id"], "CAMPAIGN_REQUEST_CAMPAIGN_INVALID",
        pattern=IDENTIFIER, maximum=96)
    if action in {"open_cycle", "close_cycle"}:
        _text(
            request["cycle_id"], "CAMPAIGN_REQUEST_CYCLE_INVALID",
            pattern=IDENTIFIER, maximum=96)
        _text(
            request["intent_sha256"], "CAMPAIGN_REQUEST_DIGEST_INVALID",
            pattern=DIGEST, maximum=71)
    if action == "open_cycle":
        _text(
            request["preflight_sha256"],
            "CAMPAIGN_REQUEST_PREFLIGHT_DIGEST_INVALID",
            pattern=DIGEST, maximum=71)
    if action == "close_cycle" and request["outcome"] not in OUTCOMES:
        raise CampaignError("CAMPAIGN_REQUEST_OUTCOME_INVALID")
    if action == "halt":
        _text(
            request["reason_code"], "CAMPAIGN_REQUEST_REASON_INVALID",
            pattern=REASON, maximum=96)
    return request


def _secure_directory(
        path: Path, *, uid: int = 0, gid: int = 0,
) -> int:
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    metadata = os.lstat(path)
    if (
            stat.S_ISLNK(metadata.st_mode) or
            not stat.S_ISDIR(metadata.st_mode) or metadata.st_nlink != 2 or
            metadata.st_uid != uid or metadata.st_gid != gid or
            stat.S_IMODE(metadata.st_mode) != 0o700):
        raise CampaignError("CAMPAIGN_DIRECTORY_METADATA_UNSAFE")
    descriptor = os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    opened = os.fstat(descriptor)
    if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
        os.close(descriptor)
        raise CampaignError("CAMPAIGN_DIRECTORY_CHANGED")
    return descriptor


def _write_private_json(directory_fd: int, name: str, value: Any) -> None:
    payload = _canonical_json(value)
    temporary = f".{name}.{os.getpid()}.{time.time_ns()}"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600, dir_fd=directory_fd)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise CampaignError("CAMPAIGN_STATE_WRITE_INCOMPLETE")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    os.fsync(directory_fd)


def _read_private_json(
        directory_fd: int, name: str, *, uid: int = 0, gid: int = 0,
) -> dict[str, Any]:
    metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != uid or metadata.st_gid != gid or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_size < 2 or metadata.st_size > MAX_BYTES):
        raise CampaignError("CAMPAIGN_STATE_METADATA_UNSAFE")
    descriptor = os.open(
        name, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd)
    try:
        opened = os.fstat(descriptor)
        raw = bytearray()
        while len(raw) <= MAX_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns")
    if (
            len(raw) > MAX_BYTES or
            any(getattr(metadata, field) != getattr(opened, field) or
                getattr(opened, field) != getattr(after, field)
                for field in fields)):
        raise CampaignError("CAMPAIGN_STATE_CHANGED")
    return _strict_json(bytes(raw), "campaign_state")


def _consumption_receipt_name(policy: CampaignPolicy) -> str:
    binding = (
        policy.domain_id.encode("ascii") + b"\0" +
        policy.campaign_id.encode("ascii"))
    return "consumption." + hashlib.sha256(binding).hexdigest() + ".v1.json"


def _create_private_json_no_replace(
        directory_fd: int, name: str, value: Any,
) -> None:
    if CONSUMPTION_NAME.fullmatch(name) is None:
        raise CampaignError("CAMPAIGN_CONSUMPTION_NAME_INVALID")
    payload = _canonical_json(value)
    if len(payload) > MAX_BYTES:
        raise CampaignError("CAMPAIGN_CONSUMPTION_CONTRACT_INVALID")
    temporary = f".{name}.{os.getpid()}.{time.time_ns()}"
    descriptor: int | None = None
    linked = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=directory_fd)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise CampaignError("CAMPAIGN_CONSUMPTION_WRITE_INCOMPLETE")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        try:
            os.link(
                temporary, name, src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd, follow_symlinks=False)
        except FileExistsError as error:
            raise CampaignError(
                "CAMPAIGN_CONSUMPTION_ALREADY_EXISTS") from error
        linked = True
        os.fsync(directory_fd)
        os.unlink(temporary, dir_fd=directory_fd)
        linked = False
        os.fsync(directory_fd)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if not linked:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        raise


def _load_consumption_snapshot(
        directory_fd: int, name: str, *, uid: int, gid: int,
) -> ConsumptionSnapshot:
    if CONSUMPTION_NAME.fullmatch(name) is None:
        raise CampaignError("CAMPAIGN_CONSUMPTION_NAME_INVALID")
    metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != uid or metadata.st_gid != gid or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            metadata.st_size < 2 or metadata.st_size > MAX_BYTES):
        raise CampaignError("CAMPAIGN_CONSUMPTION_METADATA_UNSAFE")
    descriptor = os.open(
        name, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd)
    try:
        opened = os.fstat(descriptor)
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    finally:
        os.close(descriptor)
    identity = _metadata_identity(opened, _FILE_IDENTITY_FIELDS)
    if (
            total > MAX_BYTES or
            identity != _metadata_identity(
                metadata, _FILE_IDENTITY_FIELDS) or
            identity != _metadata_identity(after, _FILE_IDENTITY_FIELDS) or
            identity != _metadata_identity(named, _FILE_IDENTITY_FIELDS)):
        raise CampaignError("CAMPAIGN_CONSUMPTION_CHANGED")
    raw = b"".join(chunks)
    document = _strict_json(raw, "campaign_consumption")
    if raw != _canonical_json(document):
        raise CampaignError("CAMPAIGN_CONSUMPTION_NON_CANONICAL")
    if (
            set(document) != CONSUMPTION_FIELDS or
            document.get("schema") != CONSUMPTION_SCHEMA or
            document.get("version") != 1 or
            document.get("status") != "CONSUMED" or
            document.get("authorization_effect") != "NONE_STATE_ONLY" or
            any(document.get(field) is not False for field in
                BOUNDARY_FIELDS)):
        raise CampaignError("CAMPAIGN_CONSUMPTION_CONTRACT_INVALID")
    for field in (
            "p1_audit_receipt_snapshot",
            "watch_handoff_receipt_snapshot"):
        binding = document.get(field)
        pinned_identity = (
            binding.get("identity") if isinstance(binding, dict) else None)
        anchors = (
            binding.get("anchor_identity")
            if isinstance(binding, dict) else None)
        if (
                not isinstance(binding, dict) or
                set(binding) != {"identity", "anchor_identity"} or
                not isinstance(pinned_identity, list) or
                len(pinned_identity) != len(_FILE_IDENTITY_FIELDS) or
                any(type(value) is not int or value < 0
                    for value in pinned_identity) or
                not isinstance(anchors, list) or not anchors or
                any(
                    not isinstance(anchor, list) or
                    len(anchor) != len(_DIRECTORY_IDENTITY_FIELDS) or
                    any(type(value) is not int or value < 0
                        for value in anchor)
                    for anchor in anchors)):
            raise CampaignError("CAMPAIGN_CONSUMPTION_CONTRACT_INVALID")
    claimed_body = document.get("body_sha256")
    if (
            not isinstance(claimed_body, str) or
            DIGEST.fullmatch(claimed_body) is None or
            claimed_body == ZERO_DIGEST):
        raise CampaignError("CAMPAIGN_CONSUMPTION_BODY_INVALID")
    body = dict(document)
    del body["body_sha256"]
    if _sha256(_canonical_json(body)) != claimed_body:
        raise CampaignError("CAMPAIGN_CONSUMPTION_BODY_INVALID")
    return ConsumptionSnapshot(
        payload=raw, document=document, identity=identity,
        file_sha256=_sha256(raw), body_sha256=claimed_body)


def _new_state(
        policy: CampaignPolicy, policy_sha256: str, now_ms: int,
) -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "version": 2,
        "domain_id": policy.domain_id,
        "campaign_id": policy.campaign_id,
        "policy_sha256": policy_sha256,
        "status": "idle",
        "created_at_ms": now_ms,
        "expires_at_ms": policy.expires_at_ms,
        "cycles_opened": 0,
        "cycles_closed": 0,
        "last_cycle_closed_at_ms": None,
        "active_cycle": None,
        "halt_reason": None,
        "last_outcome": None,
        "consumption_receipt_name": None,
        "consumption_receipt_file_sha256": None,
        "consumption_receipt_body_sha256": None,
        "consumption_receipt_identity": None,
    }


def _validate_state_contract(
        state: dict[str, Any], *, expected_schema: str,
        expected_version: int, expected_fields: set[str],
        consumption_fields: bool,
) -> None:
    def valid_integer(value: Any) -> bool:
        return (
            not isinstance(value, bool) and isinstance(value, int) and
            0 <= value <= 2**63 - 1)

    last_closed = state.get("last_cycle_closed_at_ms")
    halt_reason = state.get("halt_reason")
    last_outcome = state.get("last_outcome")
    consumption_valid = True
    if consumption_fields:
        consumption_name = state.get("consumption_receipt_name")
        consumption_file = state.get("consumption_receipt_file_sha256")
        consumption_body = state.get("consumption_receipt_body_sha256")
        consumption_identity = state.get("consumption_receipt_identity")
        consumption_values = (
            consumption_name, consumption_file, consumption_body,
            consumption_identity)
        consumption_absent = all(
            value is None for value in consumption_values)
        consumption_bound = (
            isinstance(consumption_name, str) and
            CONSUMPTION_NAME.fullmatch(consumption_name) is not None and
            isinstance(consumption_file, str) and
            DIGEST.fullmatch(consumption_file) is not None and
            consumption_file != ZERO_DIGEST and
            isinstance(consumption_body, str) and
            DIGEST.fullmatch(consumption_body) is not None and
            consumption_body != ZERO_DIGEST and
            isinstance(consumption_identity, list) and
            len(consumption_identity) == len(_FILE_IDENTITY_FIELDS) and
            all(valid_integer(value) for value in consumption_identity))
        consumption_valid = consumption_absent or consumption_bound
    if (
            set(state) != expected_fields or
            state.get("schema") != expected_schema or
            state.get("version") != expected_version or
            DOMAIN.fullmatch(str(state.get("domain_id", ""))) is None or
            IDENTIFIER.fullmatch(str(state.get("campaign_id", ""))) is None or
            state.get("status") not in {
                "idle", "opening", "open", "closing", "halted"} or
            not valid_integer(state.get("created_at_ms")) or
            not valid_integer(state.get("expires_at_ms")) or
            not valid_integer(state.get("cycles_opened")) or
            not valid_integer(state.get("cycles_closed")) or
            state["cycles_opened"] < state["cycles_closed"] or
            DIGEST.fullmatch(str(state.get("policy_sha256", ""))) is None or
            (last_closed is not None and not valid_integer(last_closed)) or
            (state["cycles_closed"] == 0 and last_closed is not None) or
            (state["cycles_closed"] > 0 and last_closed is None) or
            (halt_reason is not None and (
                not isinstance(halt_reason, str) or
                REASON.fullmatch(halt_reason) is None)) or
            (state["status"] == "halted" and halt_reason is None) or
            (state["status"] == "idle" and halt_reason is not None) or
            (last_outcome is not None and last_outcome not in OUTCOMES) or
            (state["cycles_closed"] == 0 and last_outcome is not None) or
            (state["cycles_closed"] > 0 and last_outcome is None) or
            not consumption_valid):
        raise CampaignError(
            "CAMPAIGN_STATE_CONTRACT_INVALID", recovery_required=True)
    active = state.get("active_cycle")
    if active is not None and (
            not isinstance(active, dict) or set(active) != ACTIVE_CYCLE_FIELDS or
            IDENTIFIER.fullmatch(str(active.get("cycle_id", ""))) is None or
            DIGEST.fullmatch(str(active.get("intent_sha256", ""))) is None or
            DIGEST.fullmatch(str(active.get("preflight_sha256", ""))) is None or
            not valid_integer(active.get("opened_at_ms")) or
            not valid_integer(active.get("deadline_at_ms")) or
            active["deadline_at_ms"] <= active["opened_at_ms"] or
            active["deadline_at_ms"] > state["expires_at_ms"]):
        raise CampaignError(
            "CAMPAIGN_ACTIVE_CYCLE_INVALID", recovery_required=True)
    if state["status"] in {"opening", "open", "closing"} and active is None:
        raise CampaignError(
            "CAMPAIGN_ACTIVE_CYCLE_MISSING", recovery_required=True)
    if state["status"] in {"idle", "halted"} and active is not None:
        raise CampaignError(
            "CAMPAIGN_ACTIVE_CYCLE_UNEXPECTED", recovery_required=True)


def _validate_state(state: dict[str, Any]) -> None:
    _validate_state_contract(
        state, expected_schema=STATE_SCHEMA, expected_version=2,
        expected_fields=STATE_FIELDS, consumption_fields=True)


def _migrate_legacy_state(state: dict[str, Any]) -> dict[str, Any]:
    """Validate the old state before making a v2 document writable."""

    _validate_state_contract(
        state, expected_schema=LEGACY_STATE_SCHEMA, expected_version=1,
        expected_fields=LEGACY_STATE_FIELDS, consumption_fields=False)
    migrated = dict(state)
    migrated.update({
        "schema": STATE_SCHEMA,
        "version": 2,
        "consumption_receipt_name": None,
        "consumption_receipt_file_sha256": None,
        "consumption_receipt_body_sha256": None,
        "consumption_receipt_identity": None,
    })
    _validate_state(migrated)
    return migrated


def _policy_evidence_reference(
        path: Path | None, file_sha256: str | None,
        body_sha256: str | None,
) -> dict[str, str]:
    if (
            path is None or file_sha256 is None or body_sha256 is None):
        raise CampaignError("CAMPAIGN_CONSUMPTION_BINDING_INVALID")
    return {
        "path": str(path), "file_sha256": file_sha256,
        "body_sha256": body_sha256,
    }


def _consumption_document(
        policy_snapshot: PolicySnapshot,
        session: FinalizedAdmissionSession,
        deployment: LocalDeploymentSnapshot,
        p1_audit: PinnedReferenceSnapshot,
        watch_handoff: PinnedReferenceSnapshot,
        consumed_at_ms: int,
        consumed_monotonic_ms: int,
) -> dict[str, Any]:
    policy = policy_snapshot.policy
    monotonic_expires_at_ms = (
        consumed_monotonic_ms + policy.expires_at_ms - consumed_at_ms)
    if (
            type(consumed_at_ms) is not int or consumed_at_ms < 0 or
            type(consumed_monotonic_ms) is not int or
            consumed_monotonic_ms < 0 or
            not policy.valid_after_ms <= consumed_at_ms <
                policy.expires_at_ms or
            not consumed_monotonic_ms < monotonic_expires_at_ms <=
                2**63 - 1):
        raise CampaignError("CAMPAIGN_CONSUMPTION_TIME_INVALID")
    body: dict[str, Any] = {
        "schema": CONSUMPTION_SCHEMA,
        "version": 1,
        "status": "CONSUMED",
        "consumed_at_ms": consumed_at_ms,
        "monotonic_clock": "CLOCK_BOOTTIME",
        "consumed_monotonic_ms": consumed_monotonic_ms,
        "monotonic_expires_at_ms": monotonic_expires_at_ms,
        "domain_id": policy.domain_id,
        "campaign_id": policy.campaign_id,
        "policy_file_sha256": policy_snapshot.file_sha256,
        "source_baseline_sha256": policy.source_baseline_sha256,
        "strategy_id": policy.strategy_id,
        "strategy_version": policy.strategy_version,
        "strategy_sha256": policy.strategy_sha256,
        "boot_id": session.boot_id,
        "host_authority_lease": session.lease_reference,
        "p1_audit_receipt_reference": _policy_evidence_reference(
            policy.p1_audit_receipt_path,
            policy.p1_audit_receipt_file_sha256,
            policy.p1_audit_receipt_body_sha256),
        "p1_audit_receipt_snapshot": p1_audit.consumption_binding,
        "watch_handoff_receipt_reference": _policy_evidence_reference(
            policy.watch_handoff_receipt_path,
            policy.watch_handoff_receipt_file_sha256,
            policy.watch_handoff_receipt_body_sha256),
        "watch_handoff_receipt_snapshot":
            watch_handoff.consumption_binding,
        "admission_receipt_reference": session.admission.reference,
        "finalization_current_pointer_reference": session.pointer.reference,
        "finalization_tombstone_reference": session.tombstone.reference,
        "zero_exposure_receipt_reference": session.zero.reference,
        "deployment_evidence_reference": {
            "path": str(LOCAL_PAPER_DEPLOYMENT_EVIDENCE_PATH),
            "file_sha256": deployment.file_sha256,
            "body_sha256": deployment.body_sha256,
        },
        "deployment_install_transaction_id":
            deployment.install_transaction_id,
        "authorization_effect": "NONE_STATE_ONLY",
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
        "order_submission_authorized": False,
    }
    return {**body, "body_sha256": _sha256(_canonical_json(body))}


def _consumption_validation_time(
        snapshot: ConsumptionSnapshot, policy_snapshot: PolicySnapshot,
) -> tuple[int, int, int]:
    document = snapshot.document
    policy = policy_snapshot.policy
    consumed_at_ms = document.get("consumed_at_ms")
    consumed_monotonic_ms = document.get("consumed_monotonic_ms")
    monotonic_expires_at_ms = document.get("monotonic_expires_at_ms")
    if (
            type(consumed_at_ms) is not int or consumed_at_ms < 0 or
            type(consumed_monotonic_ms) is not int or
            consumed_monotonic_ms < 0 or
            type(monotonic_expires_at_ms) is not int or
            not consumed_monotonic_ms < monotonic_expires_at_ms <=
                2**63 - 1 or
            document.get("monotonic_clock") != "CLOCK_BOOTTIME" or
            not policy.valid_after_ms <= consumed_at_ms <
                policy.expires_at_ms or
            monotonic_expires_at_ms - consumed_monotonic_ms !=
                policy.expires_at_ms - consumed_at_ms or
            document.get("domain_id") != policy.domain_id or
            document.get("campaign_id") != policy.campaign_id or
            document.get("policy_file_sha256") !=
                policy_snapshot.file_sha256 or
            document.get("source_baseline_sha256") !=
                policy.source_baseline_sha256 or
            document.get("strategy_id") != policy.strategy_id or
            document.get("strategy_version") != policy.strategy_version or
            document.get("strategy_sha256") != policy.strategy_sha256 or
            document.get("p1_audit_receipt_reference") !=
                _policy_evidence_reference(
                    policy.p1_audit_receipt_path,
                    policy.p1_audit_receipt_file_sha256,
                    policy.p1_audit_receipt_body_sha256) or
            document.get("watch_handoff_receipt_reference") !=
                _policy_evidence_reference(
                    policy.watch_handoff_receipt_path,
                    policy.watch_handoff_receipt_file_sha256,
                    policy.watch_handoff_receipt_body_sha256) or
            not isinstance(document.get("boot_id"), str) or
            BOOT_ID.fullmatch(document["boot_id"]) is None or
            not isinstance(document.get("host_authority_lease"), dict) or
            set(document["host_authority_lease"]) !=
                HOST_AUTHORITY_LEASE_FIELDS):
        raise CampaignError("CAMPAIGN_CONSUMPTION_BINDING_INVALID")
    return (
        consumed_at_ms, consumed_monotonic_ms, monotonic_expires_at_ms)


def _validate_consumption_window(
        snapshot: ConsumptionSnapshot, policy_snapshot: PolicySnapshot, *,
        now_ms: int, now_monotonic_ms: int,
        require_operator_window: bool,
) -> int:
    consumed_at_ms, consumed_monotonic_ms, monotonic_expires_at_ms = (
        _consumption_validation_time(snapshot, policy_snapshot))
    policy = policy_snapshot.policy
    operator_window_ms = (
        policy.operator_ttl_seconds * 1000
        if require_operator_window else 0)
    if (
            type(now_ms) is not int or now_ms < 0 or
            type(now_monotonic_ms) is not int or now_monotonic_ms < 0 or
            now_monotonic_ms < consumed_monotonic_ms or
            now_monotonic_ms >= monotonic_expires_at_ms or
            not policy.valid_after_ms <= now_ms < policy.expires_at_ms or
            now_monotonic_ms + operator_window_ms >
                monotonic_expires_at_ms or
            now_ms + operator_window_ms > policy.expires_at_ms):
        raise CampaignError("CAMPAIGN_CONSUMPTION_TIME_INVALID")
    return consumed_at_ms


def _validate_consumption_snapshot(
        snapshot: ConsumptionSnapshot, policy_snapshot: PolicySnapshot,
        session: FinalizedAdmissionSession,
        deployment: LocalDeploymentSnapshot,
        p1_audit: PinnedReferenceSnapshot,
        watch_handoff: PinnedReferenceSnapshot,
        *, now_ms: int, now_monotonic_ms: int,
        require_operator_window: bool,
) -> None:
    consumed_at_ms, consumed_monotonic_ms, _monotonic_expires_at_ms = (
        _consumption_validation_time(snapshot, policy_snapshot))
    expected = _consumption_document(
        policy_snapshot, session, deployment, p1_audit, watch_handoff,
        consumed_at_ms, consumed_monotonic_ms)
    if snapshot.document != expected:
        raise CampaignError("CAMPAIGN_CONSUMPTION_BINDING_INVALID")
    _validate_consumption_window(
        snapshot, policy_snapshot, now_ms=now_ms,
        now_monotonic_ms=now_monotonic_ms,
        require_operator_window=require_operator_window)


def _consumption_pinned_evidence_matches(
        snapshot: ConsumptionSnapshot,
        p1_audit: PinnedReferenceSnapshot,
        watch_handoff: PinnedReferenceSnapshot,
) -> bool:
    return (
        snapshot.document.get("p1_audit_receipt_reference") ==
            p1_audit.reference and
        snapshot.document.get("p1_audit_receipt_snapshot") ==
            p1_audit.consumption_binding and
        snapshot.document.get("watch_handoff_receipt_reference") ==
            watch_handoff.reference and
        snapshot.document.get("watch_handoff_receipt_snapshot") ==
            watch_handoff.consumption_binding)


class OneShotOperator:
    def __init__(
            self, executable: Path = DEFAULT_AUTHORITY,
            runner: Callable[..., subprocess.CompletedProcess[bytes]] =
            subprocess.run) -> None:
        self._executable = executable
        self._runner = runner

    def _invoke(
            self, action: str, domain_id: str, cycle_id: str,
            intent_sha256: str, ttl_seconds: Optional[int] = None,
    ) -> dict[str, Any]:
        command = [
            str(self._executable), action, "--domain", domain_id,
            "--cycle-id", cycle_id, "--intent-sha256", intent_sha256,
        ]
        expected_status = "engaged"
        if action == "--operator-disarm":
            if ttl_seconds is None:
                raise CampaignError("CAMPAIGN_OPERATOR_TTL_MISSING")
            command.extend(("--operator-ttl-sec", str(ttl_seconds)))
            expected_status = "disarmed"
        try:
            completed = self._runner(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=SAFE_ENVIRONMENT, cwd="/",
                close_fds=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise CampaignError(
                "CAMPAIGN_OPERATOR_TRANSPORT_FAILED",
                recovery_required=True) from error
        if (
                completed.returncode != 0 or completed.stderr or
                len(completed.stdout) > MAX_BYTES):
            print(
                "hepta_ib_paper_campaign_operator: authority failure "
                f"rc={completed.returncode} "
                f"stderr={completed.stderr.decode('utf-8', errors='replace')[:512]!r}",
                file=sys.stderr, flush=True)
            raise CampaignError(
                "CAMPAIGN_OPERATOR_REJECTED", recovery_required=True)
        response = _strict_json(completed.stdout, "operator_response")
        if (
                response.get("status") != expected_status or
                response.get("domain") != domain_id or
                response.get("cycle_id") != cycle_id or
                response.get("intent_sha256") != intent_sha256 or
                isinstance(response.get("deadline_at_ms"), bool) or
                not isinstance(response.get("deadline_at_ms"), int)):
            raise CampaignError(
                "CAMPAIGN_OPERATOR_RESPONSE_INVALID",
                recovery_required=True)
        return response

    def disarm(
            self, domain_id: str, cycle_id: str, intent_sha256: str,
            ttl_seconds: int,
    ) -> dict[str, Any]:
        return self._invoke(
            "--operator-disarm", domain_id, cycle_id, intent_sha256,
            ttl_seconds)

    def reengage(
            self, domain_id: str, cycle_id: str, intent_sha256: str,
    ) -> dict[str, Any]:
        return self._invoke(
            "--operator-reengage", domain_id, cycle_id, intent_sha256)


class CampaignController:
    def __init__(
            self, policy_provider: Callable[
                [], PolicySnapshot | tuple[CampaignPolicy, str]],
            operator: OneShotOperator, paths: CampaignPaths = CampaignPaths(),
            now_ms: Callable[[], int] =
            lambda: time.time_ns() // 1_000_000,
            root_uid: int = 0, root_gid: int = 0,
            admission_provider: Optional[
                Callable[[CampaignPolicy, int],
                         FinalizedAdmissionSession]] = None,
            deployment_provider: Optional[
                Callable[[CampaignPolicy], LocalDeploymentSnapshot]] = None,
            now_monotonic_ms: Callable[[], int] = _boottime_ms,
    ) -> None:
        self._policy_provider = policy_provider
        self._operator = operator
        self._paths = paths
        self._now_ms = now_ms
        self._now_monotonic_ms = now_monotonic_ms
        self._root_uid = root_uid
        self._root_gid = root_gid
        self._admission_provider = admission_provider
        self._deployment_provider = (
            deployment_provider or load_local_paper_deployment)

    def _read_monotonic_ms(self) -> int:
        try:
            value = self._now_monotonic_ms()
        except CampaignError:
            raise
        except (OSError, OverflowError) as error:
            raise CampaignError(
                "CAMPAIGN_MONOTONIC_CLOCK_UNAVAILABLE",
                recovery_required=True) from error
        if type(value) is not int or not 0 <= value <= 2**63 - 1:
            raise CampaignError(
                "CAMPAIGN_MONOTONIC_CLOCK_INVALID",
                recovery_required=True)
        return value

    def _read_policy_snapshot(self) -> PolicySnapshot:
        supplied = self._policy_provider()
        if isinstance(supplied, PolicySnapshot):
            return supplied
        if (
                not isinstance(supplied, tuple) or len(supplied) != 2 or
                not isinstance(supplied[0], CampaignPolicy) or
                not isinstance(supplied[1], str) or
                DIGEST.fullmatch(supplied[1]) is None):
            raise CampaignError("CAMPAIGN_POLICY_PROVIDER_INVALID")
        return PolicySnapshot(
            path=None, payload=b"", identity=None, policy=supplied[0],
            file_sha256=supplied[1])

    def _response(
            self, request: dict[str, Any], state: dict[str, Any], *,
            status: str = "ok", reason_code: str = "",
    ) -> dict[str, Any]:
        active = state.get("active_cycle")
        return {
            "schema": RESPONSE_SCHEMA,
            "version": 1,
            "status": status,
            "action": request["action"],
            "request_id": request["request_id"],
            "domain_id": request["domain_id"],
            "campaign_id": request["campaign_id"],
            "reason_code": reason_code,
            "detail": "",
            "state": {
                "status": state["status"],
                "policy_sha256": state["policy_sha256"],
                "expires_at_ms": state["expires_at_ms"],
                "cycles_opened": state["cycles_opened"],
                "cycles_closed": state["cycles_closed"],
                "active_cycle_id": (
                    active["cycle_id"] if isinstance(active, dict) else None),
                "active_deadline_at_ms": (
                    active["deadline_at_ms"]
                    if isinstance(active, dict) else None),
                "halt_reason": state["halt_reason"],
                "last_outcome": state["last_outcome"],
            },
        }

    def _request_receipt_name(self, request_id: str) -> str:
        return hashlib.sha256(request_id.encode("ascii")).hexdigest() + ".json"

    def _load_state(
            self, runtime_fd: int, policy: CampaignPolicy,
            policy_sha256: str, now_ms: int,
    ) -> dict[str, Any]:
        name = f"{policy.domain_id}.json"
        try:
            state = _read_private_json(
                runtime_fd, name, uid=self._root_uid, gid=self._root_gid)
        except FileNotFoundError:
            return _new_state(policy, policy_sha256, now_ms)
        legacy = (
            state.get("schema") == LEGACY_STATE_SCHEMA and
            state.get("version") == 1)
        if legacy:
            state = _migrate_legacy_state(state)
        else:
            _validate_state(state)
        if state["domain_id"] != policy.domain_id:
            raise CampaignError(
                "CAMPAIGN_STATE_DOMAIN_MISMATCH",
                recovery_required=True)
        if legacy and state["status"] in {"opening", "open", "closing"}:
            # The old schema has no v5 consumption binding.  Restore the
            # kill-switch using only its strictly validated active binding,
            # persist the v2 HALT, and never treat upgrade as an open cycle.
            try:
                self._recover_active(
                    state, "CAMPAIGN_STATE_SCHEMA_UPGRADE")
            finally:
                self._write_state(runtime_fd, state)
            return state
        if state["campaign_id"] != policy.campaign_id:
            if state["status"] not in {"idle", "halted"}:
                try:
                    self._recover_active(
                        state, "CAMPAIGN_POLICY_DRIFT")
                finally:
                    self._write_state(runtime_fd, state)
                return state
            return _new_state(policy, policy_sha256, now_ms)
        if legacy:
            # Idle/halted v1 state carries no outstanding mutation authority;
            # persist its explicit, lossless v2 representation before use.
            self._write_state(runtime_fd, state)
        return state

    def _write_state(self, runtime_fd: int, state: dict[str, Any]) -> None:
        _validate_state(state)
        _write_private_json(
            runtime_fd, f"{state['domain_id']}.json", state)

    def _recover_active(
            self, state: dict[str, Any], reason_code: str,
    ) -> None:
        active = state.get("active_cycle")
        if not isinstance(active, dict):
            state["status"] = "halted"
            state["halt_reason"] = reason_code
            return
        try:
            self._operator.reengage(
                state["domain_id"], active["cycle_id"],
                active["intent_sha256"])
        except CampaignError as error:
            state["status"] = "closing"
            state["halt_reason"] = reason_code
            raise CampaignError(
                error.code, recovery_required=True) from error
        state["status"] = "halted"
        state["active_cycle"] = None
        state["halt_reason"] = reason_code

    def _prepare_state(
            self, state: dict[str, Any], policy: CampaignPolicy,
            policy_sha256: str, now_ms: int,
    ) -> None:
        if state["policy_sha256"] != policy_sha256:
            if state["status"] in {"opening", "open", "closing"}:
                self._recover_active(state, "CAMPAIGN_POLICY_DRIFT")
            else:
                state["status"] = "halted"
                state["halt_reason"] = "CAMPAIGN_POLICY_DRIFT"
            return
        active = state.get("active_cycle")
        deadline = (
            active.get("deadline_at_ms")
            if isinstance(active, dict) else None)
        policy_invalid = (
            not policy.enabled or not policy.mutations_authorized or
            now_ms < policy.valid_after_ms or now_ms >= policy.expires_at_ms)
        incomplete = state["status"] in {"opening", "closing"}
        expired_open = (
            state["status"] == "open" and
            isinstance(deadline, int) and now_ms >= deadline)
        if (
                state["status"] in {"opening", "open", "closing"} and
                (policy_invalid or incomplete or expired_open)):
            reason = (
                "CAMPAIGN_OPERATOR_WINDOW_EXPIRED"
                if expired_open else "CAMPAIGN_POLICY_INACTIVE")
            self._recover_active(state, reason)

    def _ensure_open_allowed(
            self, state: dict[str, Any], policy: CampaignPolicy,
            now_ms: int,
    ) -> None:
        if policy.version == 4:
            raise CampaignError(
                "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED")
        if policy.version not in {3, 5}:
            raise CampaignError("CAMPAIGN_POLICY_VERSION_UNSUPPORTED")
        if not policy.enabled or not policy.mutations_authorized:
            raise CampaignError("CAMPAIGN_POLICY_DISABLED")
        if now_ms < policy.valid_after_ms or now_ms >= policy.expires_at_ms:
            raise CampaignError("CAMPAIGN_POLICY_INACTIVE")
        if (
                now_ms + policy.operator_ttl_seconds * 1000 >
                policy.expires_at_ms):
            raise CampaignError("CAMPAIGN_OPERATOR_WINDOW_EXCEEDS_POLICY")
        if state["status"] != "idle":
            raise CampaignError("CAMPAIGN_NOT_IDLE")
        if state["cycles_opened"] >= policy.max_cycles:
            raise CampaignError("CAMPAIGN_CYCLE_LIMIT_REACHED")
        last_closed = state["last_cycle_closed_at_ms"]
        if (
                isinstance(last_closed, int) and
                now_ms - last_closed < policy.min_cycle_interval_ms):
            raise CampaignError("CAMPAIGN_CYCLE_COOLDOWN")

    def _halt_consumption(
            self, state: dict[str, Any], runtime_fd: int, reason: str,
    ) -> None:
        state["status"] = "halted"
        state["active_cycle"] = None
        state["halt_reason"] = reason
        self._write_state(runtime_fd, state)

    def _reengage_and_halt_pinned_evidence(
            self, request: dict[str, Any], state: dict[str, Any],
            policy: CampaignPolicy, runtime_fd: int, reason: str,
            intent_sha256: str,
    ) -> None:
        try:
            self._operator.reengage(
                policy.domain_id, request["cycle_id"], intent_sha256)
        except CampaignError as error:
            self._halt_consumption(state, runtime_fd, reason)
            raise CampaignError(
                error.code, recovery_required=True) from error
        self._halt_consumption(state, runtime_fd, reason)

    def _open_local_cycle(
            self, request: dict[str, Any], state: dict[str, Any],
            policy_snapshot: PolicySnapshot, now_ms: int,
            runtime_fd: int,
    ) -> None:
        """Open one v5 local MKT cycle without external P1 authority.

        This is the proven local campaign transaction: validate the exact
        v2 MKT intent, persist ``opening``, briefly disarm the one-shot gate,
        then re-open both the policy and certified deployment evidence before
        committing ``open``.  Any post-disarm drift is re-engaged and halted.
        """

        policy = policy_snapshot.policy
        if policy.version != 5 or policy.admission_mode != "local-only":
            raise CampaignError("CAMPAIGN_POLICY_VERSION_UNSUPPORTED")
        deployment = self._deployment_provider(policy)
        if not isinstance(deployment, LocalDeploymentSnapshot):
            raise CampaignError("CAMPAIGN_DEPLOYMENT_PROVIDER_INVALID")
        _intent, computed_digest = validate_trade_intent(
            request["intent"], policy, now_ms)
        if computed_digest != request["intent_sha256"]:
            raise CampaignError("CAMPAIGN_INTENT_DIGEST_MISMATCH")
        active = {
            "cycle_id": request["cycle_id"],
            "intent_sha256": computed_digest,
            "preflight_sha256": request["preflight_sha256"],
            "opened_at_ms": now_ms,
            "deadline_at_ms": (
                now_ms + policy.operator_ttl_seconds * 1000),
        }
        state["status"] = "opening"
        state["active_cycle"] = active
        self._write_state(runtime_fd, state)
        try:
            operator_state = self._operator.disarm(
                policy.domain_id, request["cycle_id"], computed_digest,
                policy.operator_ttl_seconds)
        except CampaignError as error:
            state["status"] = "closing"
            state["halt_reason"] = "CAMPAIGN_OPERATOR_OPEN_FAILED"
            self._write_state(runtime_fd, state)
            raise CampaignError(
                error.code, recovery_required=True) from error
        confirmed_at_ms = self._now_ms()
        invalid_reason = ""
        try:
            confirmed_policy_snapshot = self._read_policy_snapshot()
            if confirmed_policy_snapshot != policy_snapshot:
                invalid_reason = "CAMPAIGN_POLICY_CHANGED_DURING_OPEN"
        except (CampaignError, OSError):
            invalid_reason = "CAMPAIGN_POLICY_CHANGED_DURING_OPEN"
        try:
            confirmed_deployment = self._deployment_provider(policy)
            if (
                    not isinstance(
                        confirmed_deployment, LocalDeploymentSnapshot) or
                    confirmed_deployment != deployment):
                invalid_reason = invalid_reason or (
                    "CAMPAIGN_DEPLOYMENT_CHANGED_DURING_OPEN")
        except (CampaignError, OSError):
            invalid_reason = invalid_reason or (
                "CAMPAIGN_DEPLOYMENT_CHANGED_DURING_OPEN")
        try:
            _confirmed_intent, confirmed_digest = validate_trade_intent(
                request["intent"], policy, confirmed_at_ms)
            if confirmed_digest != computed_digest:
                invalid_reason = invalid_reason or (
                    "CAMPAIGN_INTENT_DIGEST_MISMATCH")
        except CampaignError as error:
            invalid_reason = invalid_reason or error.code
        deadline_at_ms = operator_state["deadline_at_ms"]
        if (
                isinstance(deadline_at_ms, bool) or
                not isinstance(deadline_at_ms, int) or
                confirmed_at_ms >= policy.expires_at_ms or
                deadline_at_ms <= confirmed_at_ms + 1_000 or
                deadline_at_ms > policy.expires_at_ms or
                deadline_at_ms >
                now_ms + policy.operator_ttl_seconds * 1000 +
                MAX_OPERATOR_START_SKEW_MS):
            invalid_reason = invalid_reason or (
                "CAMPAIGN_OPERATOR_DEADLINE_INVALID")
        if invalid_reason:
            state["status"] = "closing"
            state["halt_reason"] = invalid_reason
            self._write_state(runtime_fd, state)
            try:
                self._operator.reengage(
                    policy.domain_id, active["cycle_id"],
                    active["intent_sha256"])
            except CampaignError as error:
                self._write_state(runtime_fd, state)
                raise CampaignError(
                    error.code, recovery_required=True) from error
            state["status"] = "halted"
            state["active_cycle"] = None
            self._write_state(runtime_fd, state)
            raise CampaignError(invalid_reason)
        active["deadline_at_ms"] = deadline_at_ms
        state["status"] = "open"
        state["cycles_opened"] += 1
        self._write_state(runtime_fd, state)

    def _open_cycle(
            self, request: dict[str, Any], state: dict[str, Any],
            policy_snapshot: PolicySnapshot, now_ms: int,
            runtime_fd: int, receipt_fd: int,
    ) -> None:
        policy = policy_snapshot.policy
        self._ensure_open_allowed(state, policy, now_ms)
        if policy.version == 5 and policy.admission_mode == "local-only":
            self._open_local_cycle(
                request, state, policy_snapshot, now_ms, runtime_fd)
            return
        if self._admission_provider is None:
            raise CampaignError("CAMPAIGN_ADMISSION_PROVIDER_MISSING")
        consumption_name: str | None = None
        consumption: ConsumptionSnapshot | None = None
        validation_time_ms = now_ms
        if policy.version == 5:
            consumption_name = _consumption_receipt_name(policy)
            try:
                consumption = _load_consumption_snapshot(
                    receipt_fd, consumption_name, uid=self._root_uid,
                    gid=self._root_gid)
            except FileNotFoundError:
                if (
                        state["cycles_opened"] > 0 or
                        state["consumption_receipt_name"] is not None):
                    reason = "CAMPAIGN_CONSUMPTION_MISSING"
                    self._halt_consumption(state, runtime_fd, reason)
                    raise CampaignError(reason)
            except (CampaignError, OSError) as error:
                reason = "CAMPAIGN_CONSUMPTION_CHANGED"
                self._halt_consumption(state, runtime_fd, reason)
                raise CampaignError(reason) from error
            if consumption is not None:
                try:
                    validation_time_ms, _anchor_ms, _expires_ms = (
                        _consumption_validation_time(
                            consumption, policy_snapshot))
                    _validate_consumption_window(
                        consumption, policy_snapshot, now_ms=now_ms,
                        now_monotonic_ms=self._read_monotonic_ms(),
                        require_operator_window=True)
                except CampaignError as error:
                    reason = (
                        "CAMPAIGN_CONSUMPTION_TIME_INVALID"
                        if error.code in {
                            "CAMPAIGN_CONSUMPTION_TIME_INVALID",
                            "CAMPAIGN_MONOTONIC_CLOCK_INVALID",
                            "CAMPAIGN_MONOTONIC_CLOCK_UNAVAILABLE",
                        } else
                        "CAMPAIGN_CONSUMPTION_BINDING_INVALID")
                    self._halt_consumption(state, runtime_fd, reason)
                    raise CampaignError(reason) from error
                state_binding = (
                    state["consumption_receipt_name"],
                    state["consumption_receipt_file_sha256"],
                    state["consumption_receipt_body_sha256"],
                    state["consumption_receipt_identity"],
                )
                expected_binding = (
                    consumption_name, consumption.file_sha256,
                    consumption.body_sha256, list(consumption.identity),
                )
                if (
                        state_binding[0] is not None and
                        state_binding != expected_binding):
                    reason = "CAMPAIGN_CONSUMPTION_CHANGED"
                    self._halt_consumption(state, runtime_fd, reason)
                    raise CampaignError(reason)
                if state_binding[0] is None and state["cycles_opened"] > 0:
                    reason = "CAMPAIGN_CONSUMPTION_BINDING_INVALID"
                    self._halt_consumption(state, runtime_fd, reason)
                    raise CampaignError(reason)
        try:
            session = self._admission_provider(policy, validation_time_ms)
        except (CampaignError, OSError) as error:
            if policy.version == 5 and consumption is not None:
                reason = "CAMPAIGN_CONSUMPTION_FINALIZATION_CHANGED"
                self._halt_consumption(state, runtime_fd, reason)
                raise CampaignError(reason) from error
            raise
        if not isinstance(session, FinalizedAdmissionSession):
            raise CampaignError("CAMPAIGN_ADMISSION_PROVIDER_INVALID")
        session.__enter__()
        try:
            admission = session.admission
            _intent, computed_digest = validate_trade_intent(
                request["intent"], policy, now_ms)
            if computed_digest != request["intent_sha256"]:
                raise CampaignError("CAMPAIGN_INTENT_DIGEST_MISMATCH")
            opening_at_ms = now_ms
            opening_monotonic_ms: int | None = None
            deployment: LocalDeploymentSnapshot | None = None
            p1_audit: PinnedReferenceSnapshot | None = None
            watch_handoff: PinnedReferenceSnapshot | None = None
            if policy.version == 5:
                deployment = self._deployment_provider(policy)
                if not isinstance(deployment, LocalDeploymentSnapshot):
                    raise CampaignError(
                        "CAMPAIGN_DEPLOYMENT_PROVIDER_INVALID")
                try:
                    p1_audit, watch_handoff = _load_policy_pinned_evidence(
                        policy, admission, expected_uid=self._root_uid,
                        expected_gid=self._root_gid)
                except (CampaignError, OSError) as error:
                    reason = (
                        "CAMPAIGN_PINNED_EVIDENCE_CHANGED"
                        if consumption is not None else
                        "CAMPAIGN_PINNED_EVIDENCE_INVALID")
                    self._reengage_and_halt_pinned_evidence(
                        request, state, policy, runtime_fd, reason,
                        computed_digest)
                    raise CampaignError(reason) from error
            if policy.version == 5:
                assert deployment is not None
                assert consumption_name is not None
                assert p1_audit is not None
                assert watch_handoff is not None
                opening_at_ms = self._now_ms()
                opening_monotonic_ms = self._read_monotonic_ms()
                self._ensure_open_allowed(state, policy, opening_at_ms)
                _fresh_intent, fresh_digest = validate_trade_intent(
                    request["intent"], policy, opening_at_ms)
                if fresh_digest != computed_digest:
                    raise CampaignError("CAMPAIGN_INTENT_DIGEST_MISMATCH")
                if consumption is None:
                    document = _consumption_document(
                        policy_snapshot, session, deployment, p1_audit,
                        watch_handoff, opening_at_ms,
                        opening_monotonic_ms)
                    try:
                        _create_private_json_no_replace(
                            receipt_fd, consumption_name, document)
                        consumption = _load_consumption_snapshot(
                            receipt_fd, consumption_name, uid=self._root_uid,
                            gid=self._root_gid)
                    except (CampaignError, OSError) as error:
                        reason = "CAMPAIGN_CONSUMPTION_COMMIT_FAILED"
                        self._halt_consumption(state, runtime_fd, reason)
                        raise CampaignError(reason) from error
                if not _consumption_pinned_evidence_matches(
                        consumption, p1_audit, watch_handoff):
                    reason = "CAMPAIGN_PINNED_EVIDENCE_CHANGED"
                    self._reengage_and_halt_pinned_evidence(
                        request, state, policy, runtime_fd, reason,
                        computed_digest)
                    raise CampaignError(reason)
                try:
                    _validate_consumption_snapshot(
                        consumption, policy_snapshot, session, deployment,
                        p1_audit, watch_handoff, now_ms=opening_at_ms,
                        now_monotonic_ms=opening_monotonic_ms,
                        require_operator_window=True)
                except CampaignError as error:
                    reason = (
                        "CAMPAIGN_CONSUMPTION_TIME_INVALID"
                        if error.code == "CAMPAIGN_CONSUMPTION_TIME_INVALID"
                        else "CAMPAIGN_CONSUMPTION_BINDING_INVALID")
                    self._halt_consumption(state, runtime_fd, reason)
                    raise CampaignError(reason) from error
                state["consumption_receipt_name"] = consumption_name
                state["consumption_receipt_file_sha256"] = (
                    consumption.file_sha256)
                state["consumption_receipt_body_sha256"] = (
                    consumption.body_sha256)
                state["consumption_receipt_identity"] = list(
                    consumption.identity)
                self._write_state(runtime_fd, state)
            active = {
                "cycle_id": request["cycle_id"],
                "intent_sha256": computed_digest,
                "preflight_sha256": request["preflight_sha256"],
                "opened_at_ms": opening_at_ms,
                "deadline_at_ms": (
                    opening_at_ms + policy.operator_ttl_seconds * 1000),
            }
            state["status"] = "opening"
            state["active_cycle"] = active
            self._write_state(runtime_fd, state)
            disarm_started_at_ms = opening_at_ms
            if policy.version == 5:
                assert consumption is not None
                try:
                    disarm_started_at_ms = self._now_ms()
                    pre_disarm_monotonic_ms = self._read_monotonic_ms()
                    _validate_consumption_window(
                        consumption, policy_snapshot,
                        now_ms=disarm_started_at_ms,
                        now_monotonic_ms=pre_disarm_monotonic_ms,
                        require_operator_window=True)
                    _pre_disarm_intent, pre_disarm_digest = (
                        validate_trade_intent(
                            request["intent"], policy,
                            disarm_started_at_ms))
                    if pre_disarm_digest != computed_digest:
                        raise CampaignError(
                            "CAMPAIGN_INTENT_DIGEST_MISMATCH")
                except CampaignError as error:
                    reason = (
                        "CAMPAIGN_CONSUMPTION_TIME_INVALID"
                        if error.code in {
                            "CAMPAIGN_CONSUMPTION_TIME_INVALID",
                            "CAMPAIGN_MONOTONIC_CLOCK_INVALID",
                            "CAMPAIGN_MONOTONIC_CLOCK_UNAVAILABLE",
                        } else error.code)
                    self._halt_consumption(state, runtime_fd, reason)
                    raise CampaignError(reason) from error
            try:
                operator_state = self._operator.disarm(
                    policy.domain_id, request["cycle_id"], computed_digest,
                    policy.operator_ttl_seconds)
            except CampaignError as error:
                state["status"] = "closing"
                state["halt_reason"] = "CAMPAIGN_OPERATOR_OPEN_FAILED"
                self._write_state(runtime_fd, state)
                raise CampaignError(
                    error.code, recovery_required=True) from error
            invalid_reason = ""
            confirmed_at_ms = self._now_ms()
            confirmed_monotonic_ms: int | None = None
            if policy.version == 5:
                try:
                    confirmed_monotonic_ms = self._read_monotonic_ms()
                except CampaignError:
                    invalid_reason = (
                        "CAMPAIGN_CONSUMPTION_TIME_INVALID_DURING_OPEN")
            try:
                confirmed_policy_snapshot = self._read_policy_snapshot()
                if confirmed_policy_snapshot != policy_snapshot:
                    invalid_reason = "CAMPAIGN_POLICY_CHANGED_DURING_OPEN"
            except (CampaignError, OSError):
                invalid_reason = "CAMPAIGN_POLICY_CHANGED_DURING_OPEN"
            try:
                confirmed_admission = session.reopen(confirmed_at_ms)
                if confirmed_admission != admission:
                    invalid_reason = invalid_reason or (
                        "CAMPAIGN_ADMISSION_CHANGED_DURING_OPEN")
            except (CampaignError, OSError) as error:
                admission_expired = (
                    isinstance(error, CampaignError) and
                    error.code in {
                        "CAMPAIGN_ADMISSION_TIME_INVALID",
                        "CAMPAIGN_ADMISSION_WINDOW_INVALID"})
                invalid_reason = invalid_reason or (
                    "CAMPAIGN_ADMISSION_EXPIRED_DURING_OPEN"
                    if admission_expired else
                    "CAMPAIGN_ADMISSION_CHANGED_DURING_OPEN")
            if policy.version == 5:
                confirmed_deployment: LocalDeploymentSnapshot | None = None
                confirmed_p1_audit: PinnedReferenceSnapshot | None = None
                confirmed_watch_handoff: PinnedReferenceSnapshot | None = None
                try:
                    confirmed_deployment = self._deployment_provider(policy)
                    if (
                            not isinstance(
                                confirmed_deployment,
                                LocalDeploymentSnapshot) or
                            confirmed_deployment != deployment):
                        invalid_reason = invalid_reason or (
                            "CAMPAIGN_DEPLOYMENT_CHANGED_DURING_OPEN")
                except (CampaignError, OSError):
                    invalid_reason = invalid_reason or (
                        "CAMPAIGN_DEPLOYMENT_CHANGED_DURING_OPEN")
                try:
                    confirmed_p1_audit, confirmed_watch_handoff = (
                        _load_policy_pinned_evidence(
                            policy, admission, expected_uid=self._root_uid,
                            expected_gid=self._root_gid))
                    if (
                            confirmed_p1_audit != p1_audit or
                            confirmed_watch_handoff != watch_handoff):
                        invalid_reason = invalid_reason or (
                            "CAMPAIGN_PINNED_EVIDENCE_CHANGED_DURING_OPEN")
                except (CampaignError, OSError):
                    invalid_reason = invalid_reason or (
                        "CAMPAIGN_PINNED_EVIDENCE_CHANGED_DURING_OPEN")
                try:
                    confirmed_consumption = _load_consumption_snapshot(
                        receipt_fd, consumption_name, uid=self._root_uid,
                        gid=self._root_gid)
                    if (
                            confirmed_deployment is None or
                            confirmed_p1_audit is None or
                            confirmed_watch_handoff is None or
                            confirmed_consumption != consumption):
                        invalid_reason = invalid_reason or (
                            "CAMPAIGN_CONSUMPTION_CHANGED_DURING_OPEN")
                    elif confirmed_monotonic_ms is not None:
                        _validate_consumption_snapshot(
                            confirmed_consumption, policy_snapshot, session,
                            confirmed_deployment, confirmed_p1_audit,
                            confirmed_watch_handoff,
                            now_ms=confirmed_at_ms,
                            now_monotonic_ms=confirmed_monotonic_ms,
                            require_operator_window=False)
                except (CampaignError, OSError) as error:
                    time_invalid = (
                        isinstance(error, CampaignError) and
                        error.code == "CAMPAIGN_CONSUMPTION_TIME_INVALID")
                    invalid_reason = invalid_reason or (
                        "CAMPAIGN_CONSUMPTION_TIME_INVALID_DURING_OPEN"
                        if time_invalid else
                        "CAMPAIGN_CONSUMPTION_CHANGED_DURING_OPEN")
                try:
                    final_at_ms = self._now_ms()
                    final_monotonic_ms = self._read_monotonic_ms()
                    _validate_consumption_window(
                        consumption, policy_snapshot, now_ms=final_at_ms,
                        now_monotonic_ms=final_monotonic_ms,
                        require_operator_window=False)
                    confirmed_at_ms = final_at_ms
                except CampaignError:
                    invalid_reason = invalid_reason or (
                        "CAMPAIGN_CONSUMPTION_TIME_INVALID_DURING_OPEN")
            deadline_at_ms = operator_state["deadline_at_ms"]
            if (
                    isinstance(deadline_at_ms, bool) or
                    not isinstance(deadline_at_ms, int) or
                    confirmed_at_ms >= policy.expires_at_ms or
                    deadline_at_ms <= confirmed_at_ms + 1_000 or
                    deadline_at_ms > policy.expires_at_ms or
                    (policy.version != 5 and
                     deadline_at_ms > admission.expires_at_ms) or
                    deadline_at_ms >
                    disarm_started_at_ms +
                    policy.operator_ttl_seconds * 1000 +
                    MAX_OPERATOR_START_SKEW_MS):
                invalid_reason = invalid_reason or (
                    "CAMPAIGN_OPERATOR_DEADLINE_INVALID")
            if not invalid_reason:
                try:
                    session.close()
                except CampaignError:
                    invalid_reason = "CAMPAIGN_ADMISSION_CHANGED_DURING_OPEN"
            if invalid_reason:
                state["status"] = "closing"
                state["halt_reason"] = invalid_reason
                self._write_state(runtime_fd, state)
                try:
                    self._operator.reengage(
                        policy.domain_id, active["cycle_id"],
                        active["intent_sha256"])
                except CampaignError as error:
                    self._write_state(runtime_fd, state)
                    raise CampaignError(
                        error.code, recovery_required=True) from error
                state["status"] = "halted"
                state["active_cycle"] = None
                self._write_state(runtime_fd, state)
                raise CampaignError(invalid_reason)
            active["deadline_at_ms"] = deadline_at_ms
            state["status"] = "open"
            state["cycles_opened"] += 1
            self._write_state(runtime_fd, state)
        finally:
            session.close()

    def _close_cycle(
            self, request: dict[str, Any], state: dict[str, Any],
            policy: CampaignPolicy, now_ms: int, runtime_fd: int,
    ) -> None:
        active = state.get("active_cycle")
        if (
                state["status"] not in {"open", "closing"} or
                not isinstance(active, dict) or
                active["cycle_id"] != request["cycle_id"] or
                active["intent_sha256"] != request["intent_sha256"]):
            raise CampaignError("CAMPAIGN_ACTIVE_BINDING_MISMATCH")
        state["status"] = "closing"
        self._write_state(runtime_fd, state)
        try:
            self._operator.reengage(
                policy.domain_id, active["cycle_id"],
                active["intent_sha256"])
        except CampaignError as error:
            state["halt_reason"] = "CAMPAIGN_OPERATOR_CLOSE_FAILED"
            self._write_state(runtime_fd, state)
            raise CampaignError(
                error.code, recovery_required=True) from error
        state["cycles_closed"] += 1
        state["last_cycle_closed_at_ms"] = now_ms
        state["last_outcome"] = request["outcome"]
        state["active_cycle"] = None
        if state["cycles_closed"] >= policy.max_cycles:
            state["status"] = "halted"
            state["halt_reason"] = "CAMPAIGN_COMPLETE"
        else:
            state["status"] = "idle"
            state["halt_reason"] = None
        self._write_state(runtime_fd, state)

    def _halt(
            self, request: dict[str, Any], state: dict[str, Any],
            runtime_fd: int,
    ) -> None:
        if state["status"] in {"opening", "open", "closing"}:
            self._recover_active(state, request["reason_code"])
        else:
            state["status"] = "halted"
            state["halt_reason"] = request["reason_code"]
        self._write_state(runtime_fd, state)

    def process(self, request: dict[str, Any]) -> dict[str, Any]:
        # Reject the quarantined v4 shape before even entering the persistent
        # runtime/receipt namespace.  This preflight is deliberately repeated
        # under the campaign lock below for normal v3 processing: the first
        # read is only a zero-write authority fence, not the operational
        # snapshot.
        preflight_policy_snapshot = self._read_policy_snapshot()
        if preflight_policy_snapshot.policy.version == 4:
            raise CampaignError(
                "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED")
        runtime_fd = _secure_directory(
            self._paths.runtime_root, uid=self._root_uid,
            gid=self._root_gid)
        receipt_fd = _secure_directory(
            self._paths.receipt_root, uid=self._root_uid,
            gid=self._root_gid)
        lock_fd = os.open(
            "campaign.lock",
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=runtime_fd)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            policy_snapshot = self._read_policy_snapshot()
            policy = policy_snapshot.policy
            if policy.version == 4:
                raise CampaignError(
                    "CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED")
            policy_sha256 = policy_snapshot.file_sha256
            request_raw = _canonical_json(request)
            request_sha256 = _sha256(request_raw)
            receipt_name = self._request_receipt_name(request["request_id"])
            try:
                receipt = _read_private_json(
                    receipt_fd, receipt_name, uid=self._root_uid,
                    gid=self._root_gid)
            except FileNotFoundError:
                receipt = None
            if receipt is not None:
                if (
                        receipt.get("schema") != RECEIPT_SCHEMA or
                        receipt.get("request_sha256") != request_sha256 or
                        not isinstance(receipt.get("response"), dict)):
                    raise CampaignError("CAMPAIGN_REQUEST_ID_REUSE")
                return receipt["response"]
            now_ms = self._now_ms()
            state = self._load_state(
                runtime_fd, policy, policy_sha256, now_ms)
            try:
                self._prepare_state(
                    state, policy, policy_sha256, now_ms)
                self._write_state(runtime_fd, state)
                if (
                        request["domain_id"] != policy.domain_id or
                        request["campaign_id"] != policy.campaign_id):
                    raise CampaignError(
                        "CAMPAIGN_REQUEST_POLICY_MISMATCH")
                if request["action"] == "open_cycle":
                    self._open_cycle(
                        request, state, policy_snapshot, now_ms,
                        runtime_fd, receipt_fd)
                elif request["action"] == "close_cycle":
                    self._close_cycle(
                        request, state, policy, now_ms, runtime_fd)
                elif request["action"] == "halt":
                    self._halt(request, state, runtime_fd)
                response = self._response(request, state)
            except CampaignError as error:
                try:
                    self._write_state(runtime_fd, state)
                except CampaignError:
                    pass
                status = (
                    "recovery_required"
                    if error.recovery_required else "rejected")
                response = self._response(
                    request, state, status=status,
                    reason_code=error.code)
            _write_private_json(receipt_fd, receipt_name, {
                "schema": RECEIPT_SCHEMA,
                "version": 1,
                "request_id": request["request_id"],
                "request_sha256": request_sha256,
                "recorded_at_ms": now_ms,
                "response": response,
            })
            return response
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            os.close(receipt_fd)
            os.close(runtime_fd)


def load_agent_identity(path: Path, domain_id: str) -> tuple[int, int]:
    document = _strict_json(
        _stable_read(path, installed=True), "trust_domain")
    if (
            set(document) != TRUST_DOMAIN_FIELDS or
            document.get("schema") != TRUST_DOMAIN_SCHEMA or
            document.get("version") != 1 or
            document.get("domain_id") != domain_id or
            document.get("agent_name") != f"hepta-agent-{domain_id}" or
            document.get("agent_group") != f"hepta-agent-{domain_id}" or
            document.get("single_domain_compatibility") is not False or
            document.get("execution_gateway_agent_id") != domain_id or
            document.get("execution_gateway_uid") !=
            document.get("gateway_uid") or
            document.get("paper_authorized") is not False or
            document.get("live_authorized") is not False):
        raise CampaignError("CAMPAIGN_TRUST_DOMAIN_INVALID")
    uid = _integer(
        document.get("agent_uid"), "CAMPAIGN_AGENT_UID_INVALID",
        minimum=1, maximum=2**32 - 1)
    gid = _integer(
        document.get("agent_gid"), "CAMPAIGN_AGENT_GID_INVALID",
        minimum=1, maximum=2**32 - 1)
    return uid, gid


def _socket_activation_listener() -> socket.socket:
    try:
        listen_pid = int(os.environ.get("LISTEN_PID", "0"))
        listen_fds = int(os.environ.get("LISTEN_FDS", "0"))
    except ValueError as error:
        raise CampaignError("CAMPAIGN_SOCKET_ACTIVATION_INVALID") from error
    if (
            listen_pid != os.getpid() or listen_fds != 1 or
            os.environ.get("LISTEN_FDNAMES", "") != "hepta-paper-campaign"):
        raise CampaignError("CAMPAIGN_SOCKET_ACTIVATION_INVALID")
    listener = socket.fromfd(3, socket.AF_UNIX, socket.SOCK_STREAM)
    if listener.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
        listener.close()
        raise CampaignError("CAMPAIGN_SOCKET_TYPE_INVALID")
    return listener


def _read_request(channel: socket.socket) -> bytes:
    raw = bytearray()
    while len(raw) <= MAX_BYTES:
        chunk = channel.recv(min(8192, MAX_BYTES + 1 - len(raw)))
        if not chunk:
            break
        raw.extend(chunk)
        if b"\n" in chunk:
            break
    if len(raw) > MAX_BYTES or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise CampaignError("CAMPAIGN_REQUEST_FRAME_INVALID")
    return bytes(raw)


def serve_once(
        domain_id: str, controller: CampaignController,
        expected_agent_uid: int,
) -> None:
    listener = _socket_activation_listener()
    try:
        channel, _address = listener.accept()
    finally:
        listener.close()
    try:
        credentials = channel.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _peer_pid, peer_uid, _peer_gid = struct.unpack("3i", credentials)
        if peer_uid != expected_agent_uid:
            raise CampaignError("CAMPAIGN_PEER_IDENTITY_REJECTED")
        request = parse_request(_read_request(channel))
        if request["domain_id"] != domain_id:
            raise CampaignError("CAMPAIGN_REQUEST_DOMAIN_MISMATCH")
        try:
            response = controller.process(request)
        except CampaignError as error:
            response = {
                "schema": RESPONSE_SCHEMA,
                "version": 1,
                "status": (
                    "recovery_required"
                    if error.recovery_required else "rejected"),
                "action": request["action"],
                "request_id": request["request_id"],
                "domain_id": request["domain_id"],
                "campaign_id": request["campaign_id"],
                "reason_code": error.code,
                "detail": "",
                "state": None,
            }
        channel.sendall(_canonical_json(response))
        channel.shutdown(socket.SHUT_WR)
    finally:
        channel.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve-once", action="store_true", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--policy-root", type=Path, default=DEFAULT_POLICY_ROOT)
    parser.add_argument(
        "--admission-root", type=Path, default=DEFAULT_ADMISSION_ROOT)
    parser.add_argument(
        "--trust-domain-root", type=Path,
        default=DEFAULT_TRUST_DOMAIN_ROOT)
    arguments = parser.parse_args(argv)
    try:
        if DOMAIN.fullmatch(arguments.domain) is None:
            raise CampaignError("CAMPAIGN_DOMAIN_INVALID")
        if os.geteuid() != 0 or os.getegid() != 0:
            raise CampaignError("CAMPAIGN_OPERATOR_REQUIRES_ROOT")
        policy_path = arguments.policy_root / f"{arguments.domain}.json"
        trust_path = arguments.trust_domain_root / f"{arguments.domain}.json"
        agent_uid, _agent_gid = load_agent_identity(
            trust_path, arguments.domain)
        controller = CampaignController(
            lambda: load_policy(policy_path, arguments.domain),
            OneShotOperator(),
            admission_provider=lambda policy, now_ms:
                open_finalized_admission_session(
                    arguments.admission_root, policy, now_ms))
        serve_once(arguments.domain, controller, agent_uid)
        return 0
    except (CampaignError, OSError) as error:
        code = (
            error.code if isinstance(error, CampaignError)
            else "CAMPAIGN_OPERATOR_IO_FAILED")
        print(
            f"hepta_ib_paper_campaign_operator: FAIL {code}",
            file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
